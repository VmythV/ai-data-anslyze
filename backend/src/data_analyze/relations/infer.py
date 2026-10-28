"""推断跨表 / 跨源关联：从表结构缓存里找候选，再抽样核对取值匹配率。

候选来源（可信度从高到低）：
1. 字段注释里写了「对应 / 关联 xx.yy(.zz)」
2. 不同表里同名的业务键（order_no、shop_customer_id…，排除 id、name、status 这类通用名）
3. 命名约定：customer_id → customers.id（会去掉 shop_ 这类数据源前缀）

核对：从引用方抽 500 个不同取值，看有多少在被引用方出现过（按文本比较，兼容数字/字符串混用）。
结果一律存为「待审核」，管理员采纳后才对所有会话生效。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from data_analyze.datasources.base import DataSourceError
from data_analyze.datasources.catalog import SchemaCatalog
from data_analyze.datasources.registry import DriverRegistry
from data_analyze.datasources.sources import connection_config
from data_analyze.security import SecretBox
from data_analyze.store.models import DataSource, Relation, SchemaEntry

logger = logging.getLogger(__name__)

SAMPLE_SIZE = 500
MIN_MATCH_RATE = 0.5
MAX_CANDIDATES = 40
#: 太通用、同名也不说明有关联的字段
GENERIC_NAMES = {
    "id", "name", "title", "type", "status", "state", "code", "remark", "comment", "description", "amount", "price",
    "quantity", "count", "created_at", "updated_at", "deleted_at", "create_time", "update_time", "date", "month",
    "year", "day", "time", "channel", "city", "province", "region", "source", "level", "tier", "category", "tags",
    "version", "sort", "enabled", "is_deleted", "phone_tail", "_id", "reason", "unit_price", "discount",
}
#: 注释里形如 shop.orders.order_no / customers.id 的引用；只有真能对上某个表字段的才算数
_REFERENCE = re.compile(r"(?<![\w.])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*){1,2})(?![\w.])")


@dataclass(frozen=True)
class Column:
    datasource_id: str
    alias: str
    database: str
    table: str
    name: str
    type: str
    comment: str

    @property
    def short_table(self) -> str:
        """PG 的 public.members → members"""
        return self.table.rsplit(".", 1)[-1]

    @property
    def leaf(self) -> str:
        """Mongo 嵌套字段 customer.shop_customer_id → shop_customer_id"""
        return self.name.rsplit(".", 1)[-1]

    def label(self) -> str:
        return f"{self.alias}.{self.table}.{self.name}"


@dataclass
class Candidate:
    #: 引用方（多的一侧）
    left: Column
    #: 被引用方
    right: Column
    score: float
    reason: str
    match_rate: float | None = None
    sampled: int = 0
    error: str = ""
    extra: list[str] = field(default_factory=list)

    @property
    def key(self) -> frozenset[tuple[str, str, str]]:
        return frozenset({(self.left.datasource_id, self.left.table, self.left.name),
                          (self.right.datasource_id, self.right.table, self.right.name)})


def _type_class(data_type: str) -> str:
    t = data_type.lower()
    if any(k in t for k in ("int", "decimal", "numeric", "double", "float", "real", "number")):
        return "number"
    if any(k in t for k in ("char", "text", "string", "uuid", "objectid", "enum")):
        return "text"
    if any(k in t for k in ("date", "time")):
        return "time"
    return t


def _compatible(a: Column, b: Column) -> bool:
    ta, tb = _type_class(a.type.split("|")[0]), _type_class(b.type.split("|")[0])
    return ta == tb and ta in ("number", "text")


def _singular(word: str) -> str:
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("ses"):
        return word[:-2]
    return word[:-1] if word.endswith("s") else word


class _Groups:
    """并查集：已经能经由已知关系互相到达的字段归为一组。"""

    def __init__(self) -> None:
        self._parent: dict[tuple[str, str, str], tuple[str, str, str]] = {}

    def find(self, x: tuple[str, str, str]) -> tuple[str, str, str]:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: tuple[str, str, str], b: tuple[str, str, str]) -> None:
        self._parent[self.find(a)] = self.find(b)

    def connected(self, a: tuple[str, str, str], b: tuple[str, str, str]) -> bool:
        return self.find(a) == self.find(b)


def generate_candidates(
    columns: list[Column], existing: set[frozenset[tuple[str, str, str]]], cross_source_only: bool = True
) -> list[Candidate]:
    usable = [c for c in columns if "[]" not in c.name]
    by_table: dict[tuple[str, str], list[Column]] = {}
    for c in usable:
        by_table.setdefault((c.datasource_id, c.table), []).append(c)
    aliases = {c.alias for c in usable}
    found: dict[frozenset, Candidate] = {}

    def add(left: Column, right: Column, score: float, reason: str) -> None:
        if (left.datasource_id, left.table) == (right.datasource_id, right.table) or not _compatible(left, right):
            return
        candidate = Candidate(left, right, score, reason)
        if candidate.key in existing:
            return
        current = found.get(candidate.key)
        if current is None or current.score < score:
            found[candidate.key] = candidate

    # 1. 注释里点名的字段
    for c in usable:
        for ref in _REFERENCE.findall(c.comment):
            parts = ref.split(".")
            column_name, table_name = parts[-1], parts[-2]
            scope = parts[0] if len(parts) == 3 else None
            for target in usable:
                if target.name != column_name and target.leaf != column_name:
                    continue
                if target.short_table != table_name and target.table != table_name:
                    continue
                if scope and scope not in (target.alias, target.database, target.table.split(".")[0]):
                    continue
                add(c, target, 3, f"字段注释提到 {ref}")

    # 2. 同名业务键
    by_leaf: dict[str, list[Column]] = {}
    for c in usable:
        if c.leaf.lower() not in GENERIC_NAMES and len(c.leaf) >= 5:
            by_leaf.setdefault(c.leaf.lower(), []).append(c)
    for same in by_leaf.values():
        for i, a in enumerate(same):
            for b in same[i + 1:]:
                add(a, b, 2, f"同名字段 {a.leaf}")

    # 3. xxx_id → xxxs.id
    for c in usable:
        leaf = c.leaf.lower()
        if not leaf.endswith("_id") or leaf in GENERIC_NAMES:
            continue
        entity = leaf[:-3]
        for prefix in aliases:
            entity = entity.removeprefix(f"{prefix}_")
        for (_, _table), cols in by_table.items():
            target_table = cols[0].short_table.lower()
            if _singular(target_table) != entity and target_table != entity:
                continue
            for target in cols:
                if target.leaf.lower() in ("id", "_id") or target.leaf.lower() == f"{entity}_id":
                    add(c, target, 2.5, f"命名约定 {c.leaf} → {target.short_table}.{target.leaf}")

    # 已知关系与注释/命名约定得出的候选连成组；两端已经同组的同名候选只是「兄弟字段」（都指向同一个父字段），去掉
    groups = _Groups()
    for key in existing:
        a, b = tuple(key) if len(key) == 2 else (next(iter(key)),) * 2
        groups.union(a, b)
    ordered = sorted(found.values(), key=lambda x: -x.score)
    kept = []
    for c in ordered:
        a = (c.left.datasource_id, c.left.table, c.left.name)
        b = (c.right.datasource_id, c.right.table, c.right.name)
        if groups.connected(a, b):
            continue
        internal = c.left.datasource_id == c.right.datasource_id
        # 同一个库内部的关联：注释和外键本来就能告诉 AI，默认不进审核队列；但高可信的仍参与分组
        if internal and cross_source_only:
            if c.score >= 2.5:
                groups.union(a, b)
            continue
        groups.union(a, b)
        kept.append(c)
    return kept[:MAX_CANDIDATES]


class RelationInference:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], box: SecretBox, drivers: DriverRegistry,
        catalog: SchemaCatalog,
    ) -> None:
        self._sessions = sessions
        self._box = box
        self._drivers = drivers
        self._catalog = catalog

    async def _columns(self, datasource_ids: list[str] | None) -> tuple[list[Column], dict[str, DataSource]]:
        async with self._sessions() as session:
            query = select(DataSource)
            if datasource_ids:
                query = query.where(DataSource.id.in_(datasource_ids))
            sources = {d.id: d for d in (await session.scalars(query)).all()}
        await self._catalog.ensure(list(sources))
        async with self._sessions() as session:
            entries = (
                await session.scalars(
                    select(SchemaEntry).where(
                        SchemaEntry.datasource_id.in_(list(sources)), SchemaEntry.column_name.is_not(None)
                    )
                )
            ).all()
        columns = [
            Column(e.datasource_id, sources[e.datasource_id].alias or "", sources[e.datasource_id].database,
                   e.table_name, e.column_name or "", e.data_type, e.comment)
            for e in entries
        ]
        return columns, sources

    async def existing_keys(self) -> set[frozenset[tuple[str, str, str]]]:
        async with self._sessions() as session:
            rows = (await session.scalars(select(Relation))).all()
        return {
            frozenset({(r.left_datasource_id, r.left_table, r.left_column),
                       (r.right_datasource_id, r.right_table, r.right_column)})
            for r in rows
        }

    async def match_rate(
        self, sources: dict[str, DataSource], left: Column, right: Column
    ) -> tuple[float, int]:
        """（匹配率，抽样数）：左侧抽样取值在右侧出现的比例。"""
        left_driver = await self._drivers.get(left.datasource_id, connection_config(sources[left.datasource_id], self._box))
        right_driver = await self._drivers.get(
            right.datasource_id, connection_config(sources[right.datasource_id], self._box)
        )
        values = await left_driver.sample_values(left.table, left.name, SAMPLE_SIZE)
        values = [v for v in values if v is not None and str(v) != ""]
        if not values:
            return 0.0, 0
        matched = await right_driver.count_matches(right.table, right.name, values)
        return matched / len(values), len(values)

    async def infer(self, datasource_ids: list[str] | None = None) -> list[Candidate]:
        """生成并核对候选；匹配率达标的存为待审核关联。返回全部核对过的候选（含未达标的）。"""
        columns, sources = await self._columns(datasource_ids)
        candidates = generate_candidates(columns, await self.existing_keys())
        semaphore = asyncio.Semaphore(4)

        async def check(c: Candidate) -> None:
            async with semaphore:
                try:
                    c.match_rate, c.sampled = await asyncio.wait_for(self.match_rate(sources, c.left, c.right), 30)
                except (DataSourceError, TimeoutError) as exc:
                    c.error = str(exc) or "核对超时"

        await asyncio.gather(*(check(c) for c in candidates))
        accepted = [c for c in candidates if c.match_rate is not None and c.match_rate >= MIN_MATCH_RATE]
        async with self._sessions() as session:
            for c in accepted:
                session.add(
                    Relation(
                        left_datasource_id=c.left.datasource_id, left_table=c.left.table, left_column=c.left.name,
                        right_datasource_id=c.right.datasource_id, right_table=c.right.table,
                        right_column=c.right.name, cardinality="n:1",
                        description=f"AI 推断：{c.reason}；抽样 {c.sampled} 个取值，匹配 {c.match_rate:.0%}",
                        source="ai", status="pending", match_rate=round(c.match_rate, 4),
                    )
                )
            await session.commit()
        logger.info("relation inference: %d candidates, %d accepted", len(candidates), len(accepted))
        return candidates
