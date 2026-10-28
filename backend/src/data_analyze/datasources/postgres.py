"""PostgreSQL 驱动（asyncpg）。

表名一律带 schema：`public.members`、`marketing.coupons`。连接级默认只读事务 + 语句超时，
查询本身也包在只读事务里。PG 不能跨 database 查询，同一个库里的各 schema 可以随意关联。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import asyncpg

from data_analyze.datasources.base import (
    CatalogColumn,
    ColumnInfo,
    ConnectionConfig,
    DataSourceError,
    Driver,
    QueryResult,
    TableDetail,
    TableInfo,
)

_LOW_CARDINALITY_TYPES = ("character varying", "character", "text", "boolean", "USER-DEFINED")
_TOP_VALUES_LIMIT = 20
_TOP_VALUES_SCAN = 20_000
_SAMPLE_ROWS = 5
_SYSTEM_SCHEMAS = "('pg_catalog', 'information_schema')"


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def split_name(table: str) -> tuple[str, str]:
    schema, _, name = table.rpartition(".")
    return (schema or "public"), name


class PostgresDriver(Driver):
    kind = "postgresql"
    dialect = "postgres"

    def __init__(self, config: ConnectionConfig) -> None:
        super().__init__(config)
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    def connection_key(self) -> str:
        c = self.config
        return f"postgresql://{c.username}@{c.host}:{c.port}/{c.database}"

    async def _get_pool(self) -> asyncpg.Pool:
        async with self._lock:
            if self._pool is None:
                c = self.config
                try:
                    self._pool = await asyncpg.create_pool(
                        host=c.host, port=c.port, user=c.username, password=c.password, database=c.database,
                        ssl=c.options.get("sslmode") or None, min_size=0, max_size=5, timeout=10,
                        server_settings={"default_transaction_read_only": "on", "application_name": "data-analyze"},
                    )
                except (asyncpg.PostgresError, OSError, TimeoutError) as exc:
                    raise DataSourceError(f"连接 PostgreSQL 失败：{_describe(exc)}") from exc
            return self._pool

    async def _fetch(self, sql: str, *args: Any, timeout_seconds: int = 30) -> tuple[list[str], list[tuple[Any, ...]]]:
        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn, conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_seconds) * 1000}")
                stmt = await conn.prepare(sql)
                records = await asyncio.wait_for(stmt.fetch(*args), timeout_seconds + 5)
                columns = [a.name for a in stmt.get_attributes()]
                return columns, [tuple(r) for r in records]
        except (TimeoutError, asyncpg.QueryCanceledError) as exc:
            raise DataSourceError(f"查询超时（>{timeout_seconds} 秒），请缩小范围或加过滤条件") from exc
        except asyncpg.PostgresError as exc:
            raise DataSourceError(_describe(exc)) from exc

    async def test(self) -> str:
        _, rows = await self._fetch("SELECT version(), current_database()")
        version, database = rows[0]
        return f"{version.split(' on ')[0]}，当前库 {database}"

    async def list_tables(self) -> list[TableInfo]:
        _, rows = await self._fetch(
            "SELECT n.nspname, c.relname, obj_description(c.oid, 'pg_class'), c.reltuples::bigint "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE c.relkind IN ('r', 'v', 'm', 'p') AND n.nspname NOT IN {_SYSTEM_SCHEMAS} "
            "AND n.nspname NOT LIKE 'pg_toast%' AND has_table_privilege(c.oid, 'SELECT') ORDER BY 1, 2"
        )
        return [
            TableInfo(name=f"{s}.{t}", comment=c or "", row_estimate=None if n is None or n < 0 else n)
            for s, t, c, n in rows
        ]

    async def catalog_columns(self) -> list[CatalogColumn]:
        _, rows = await self._fetch(
            "SELECT n.nspname || '.' || c.relname, a.attname, format_type(a.atttypid, a.atttypmod), "
            "col_description(c.oid, a.attnum) FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r', 'v', 'm', 'p') "
            f"AND n.nspname NOT IN {_SYSTEM_SCHEMAS} AND n.nspname NOT LIKE 'pg_toast%' "
            "AND has_table_privilege(c.oid, 'SELECT') ORDER BY 1, a.attnum"
        )
        return [CatalogColumn(table=r[0], name=r[1], type=r[2], comment=r[3] or "") for r in rows]

    async def describe_table(self, table: str) -> TableDetail:
        schema, name = split_name(table)
        _, info = await self._fetch(
            "SELECT obj_description(c.oid, 'pg_class'), c.reltuples::bigint FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = $1 AND c.relname = $2",
            schema, name,
        )
        if not info:
            raise DataSourceError(f"表 {schema}.{name} 不存在（PostgreSQL 表名请写成 schema.表名）")
        _, cols = await self._fetch(
            "SELECT c.column_name, c.data_type, format_type(a.atttypid, a.atttypmod), c.is_nullable, "
            "col_description(a.attrelid, a.attnum), "
            "EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = a.attrelid AND i.indisprimary "
            "AND a.attnum = ANY(i.indkey)) "
            "FROM information_schema.columns c "
            "JOIN pg_attribute a ON a.attrelid = (quote_ident($1) || '.' || quote_ident($2))::regclass "
            "AND a.attname = c.column_name "
            "WHERE c.table_schema = $1 AND c.table_name = $2 ORDER BY c.ordinal_position",
            schema, name,
        )
        _, idx = await self._fetch(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = $1 AND tablename = $2", schema, name
        )
        qualified = f"{_quote(schema)}.{_quote(name)}"
        names, sample = await self._fetch(f"SELECT * FROM {qualified} LIMIT {_SAMPLE_ROWS}")
        columns = []
        for col, data_type, full_type, nullable, comment, is_pk in cols:
            top: list[tuple[Any, int]] = []
            if data_type in _LOW_CARDINALITY_TYPES and not is_pk:
                top = await self._top_values(qualified, col)
            columns.append(
                ColumnInfo(name=col, type=full_type, nullable=nullable == "YES", key="PRI" if is_pk else "",
                           comment=comment or "", top_values=top)
            )
        comment, estimate = info[0]
        return TableDetail(
            table=TableInfo(name=f"{schema}.{name}", comment=comment or "",
                            row_estimate=None if estimate is None or estimate < 0 else estimate),
            columns=columns,
            sample_rows=[dict(zip(names, r, strict=True)) for r in sample],
            indexes=[i[0].split(" USING ", 1)[-1] for i in idx],
        )

    async def _top_values(self, qualified: str, column: str) -> list[tuple[Any, int]]:
        col = _quote(column)
        _, rows = await self._fetch(
            f"SELECT {col}, COUNT(*) AS n FROM (SELECT {col} FROM {qualified} LIMIT {_TOP_VALUES_SCAN}) t "
            f"GROUP BY {col} ORDER BY n DESC LIMIT {_TOP_VALUES_LIMIT + 1}"
        )
        return [] if len(rows) > _TOP_VALUES_LIMIT else [(r[0], r[1]) for r in rows]

    async def sample_values(self, table: str, column: str, limit: int) -> list[Any]:
        schema, name = split_name(table)
        col = _quote(column)
        _, rows = await self._fetch(
            f"SELECT DISTINCT {col} FROM {_quote(schema)}.{_quote(name)} WHERE {col} IS NOT NULL LIMIT {int(limit)}"
        )
        return [r[0] for r in rows]

    async def count_matches(self, table: str, column: str, values: list[Any]) -> int:
        if not values:
            return 0
        schema, name = split_name(table)
        col = _quote(column)
        _, rows = await self._fetch(
            f"SELECT COUNT(DISTINCT {col}::text) FROM {_quote(schema)}.{_quote(name)} WHERE {col}::text = ANY($1::text[])",
            [str(v) for v in values],
        )
        return int(rows[0][0])

    async def query(self, sql: str, max_rows: int, timeout_seconds: int) -> QueryResult:
        started = time.monotonic()
        columns, rows = await self._fetch(sql, timeout_seconds=timeout_seconds)
        return QueryResult(
            columns=columns, rows=rows[:max_rows], truncated=len(rows) > max_rows,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def _describe(exc: BaseException) -> str:
    if isinstance(exc, asyncpg.PostgresError):
        return f"PostgreSQL 错误 {getattr(exc, 'sqlstate', '')}：{exc}"
    return str(exc) or type(exc).__name__
