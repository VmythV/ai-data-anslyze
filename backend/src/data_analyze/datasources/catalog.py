"""表结构缓存：把各数据源的表与字段存到平台库，供 search_schema 和关联配置使用。

数据源一多，AI 不可能逐个看表；先按关键词搜到候选表，再 describe_table 看细节。
搜索目前是简单的子串匹配打分，规模再大时可以在 search() 这一层换成全文索引或向量检索。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from data_analyze.datasources.base import DataSourceError
from data_analyze.datasources.registry import DriverRegistry
from data_analyze.datasources.sources import connection_config
from data_analyze.security import SecretBox
from data_analyze.store.models import DataSource, SchemaEntry, utcnow

logger = logging.getLogger(__name__)

#: 搜索返回的表数上限、每张表展示的命中字段数上限
SEARCH_LIMIT = 20
COLUMNS_PER_TABLE = 8


@dataclass
class TableHit:
    datasource_id: str
    table: str
    comment: str
    row_estimate: int | None
    score: float = 0
    columns: list[tuple[str, str, str]] = field(default_factory=list)


class SchemaCatalog:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], box: SecretBox, drivers: DriverRegistry) -> None:
        self._sessions = sessions
        self._box = box
        self._drivers = drivers
        self._locks: dict[str, asyncio.Lock] = {}

    async def refresh(self, datasource_id: str) -> tuple[int, int]:
        """重新拉取表结构，返回（表数，字段数）。失败时记录错误并抛出 DataSourceError。"""
        lock = self._locks.setdefault(datasource_id, asyncio.Lock())
        async with lock:
            async with self._sessions() as session:
                ds = await session.get(DataSource, datasource_id)
                if ds is None:
                    raise DataSourceError("数据源不存在")
                driver = await self._drivers.get(ds.id, connection_config(ds, self._box))
            try:
                tables = await driver.list_tables()
                columns = await driver.catalog_columns()
            except DataSourceError as exc:
                async with self._sessions() as session:
                    ds = await session.get(DataSource, datasource_id)
                    if ds is not None:
                        ds.schema_error = str(exc)
                        await session.commit()
                raise
            async with self._sessions() as session:
                await session.execute(delete(SchemaEntry).where(SchemaEntry.datasource_id == datasource_id))
                session.add_all(
                    SchemaEntry(datasource_id=datasource_id, table_name=t.name, column_name=None,
                                comment=t.comment, row_estimate=t.row_estimate)
                    for t in tables
                )
                session.add_all(
                    SchemaEntry(datasource_id=datasource_id, table_name=c.table, column_name=c.name,
                                data_type=c.type[:128], comment=c.comment)
                    for c in columns
                )
                ds = await session.get(DataSource, datasource_id)
                if ds is not None:
                    ds.schema_refreshed_at = utcnow()
                    ds.schema_error = None
                await session.commit()
            return len(tables), len(columns)

    async def refresh_quietly(self, datasource_id: str) -> None:
        try:
            await self.refresh(datasource_id)
        except DataSourceError as exc:
            logger.warning("schema refresh %s failed: %s", datasource_id, exc)

    async def ensure(self, datasource_ids: list[str]) -> None:
        """从没刷新过的数据源先刷新一次（失败不抛出，搜索结果里自然缺这个库）。"""
        async with self._sessions() as session:
            rows = await session.execute(
                select(DataSource.id).where(DataSource.id.in_(datasource_ids), DataSource.schema_refreshed_at.is_(None))
            )
            missing = list(rows.scalars())
        await asyncio.gather(*(self.refresh_quietly(i) for i in missing))

    async def search(self, datasource_ids: list[str], keywords: list[str]) -> list[TableHit]:
        words = [w.strip().lower() for w in keywords if w and w.strip()][:10]
        if not words or not datasource_ids:
            return []
        await self.ensure(datasource_ids)
        conditions = []
        for word in words:
            pattern = f"%{word}%"
            conditions += [SchemaEntry.table_name.ilike(pattern), SchemaEntry.column_name.ilike(pattern),
                           SchemaEntry.comment.ilike(pattern)]
        async with self._sessions() as session:
            entries = (
                await session.scalars(
                    select(SchemaEntry).where(SchemaEntry.datasource_id.in_(datasource_ids), or_(*conditions))
                )
            ).all()
            hit_tables = {(e.datasource_id, e.table_name) for e in entries}
            # 命中字段的表也要带上表说明
            table_rows = (
                await session.scalars(
                    select(SchemaEntry).where(
                        SchemaEntry.datasource_id.in_(datasource_ids), SchemaEntry.column_name.is_(None)
                    )
                )
            ).all()
        hits: dict[tuple[str, str], TableHit] = {
            (t.datasource_id, t.table_name): TableHit(t.datasource_id, t.table_name, t.comment, t.row_estimate)
            for t in table_rows if (t.datasource_id, t.table_name) in hit_tables
        }
        for e in entries:
            hit = hits.setdefault((e.datasource_id, e.table_name), TableHit(e.datasource_id, e.table_name, "", None))
            name = (e.column_name or e.table_name).lower()
            comment = e.comment.lower()
            for word in words:
                if e.column_name is None:
                    hit.score += 3 * (word in name) + 1.5 * (word in comment)
                else:
                    gained = 2 * (word in name) + 1 * (word in comment)
                    hit.score += gained
            if e.column_name is not None and len(hit.columns) < COLUMNS_PER_TABLE:
                hit.columns.append((e.column_name, e.data_type, e.comment))
        return sorted(hits.values(), key=lambda h: -h.score)[:SEARCH_LIMIT]

    async def tables(self, datasource_id: str) -> dict[str, dict]:
        """{表名: {comment, row_estimate, columns: [(name, type, comment)]}}，关联配置页面用。"""
        await self.ensure([datasource_id])
        async with self._sessions() as session:
            entries = (
                await session.scalars(
                    select(SchemaEntry).where(SchemaEntry.datasource_id == datasource_id).order_by(SchemaEntry.id)
                )
            ).all()
        out: dict[str, dict] = {}
        for e in entries:
            table = out.setdefault(e.table_name, {"comment": "", "row_estimate": None, "columns": []})
            if e.column_name is None:
                table["comment"], table["row_estimate"] = e.comment, e.row_estimate
            else:
                table["columns"].append((e.column_name, e.data_type, e.comment))
        return out
