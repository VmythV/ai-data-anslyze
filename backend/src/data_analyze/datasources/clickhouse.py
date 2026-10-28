"""ClickHouse 驱动（clickhouse-connect，HTTP 接口）。

每次查询带上 `readonly=2`（只读、但允许设置执行参数）与 `max_execution_time`。
账号本身已是只读（readonly=1）时不允许再改设置，就去掉设置重试一次，只读由账号保证。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import clickhouse_connect
from clickhouse_connect.driver.exceptions import ClickHouseError

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

_LOW_CARDINALITY = ("LowCardinality", "Enum", "Bool")
_STRING_TYPES = ("String", "FixedString")
_TOP_VALUES_LIMIT = 20
_TOP_VALUES_SCAN = 20_000
_SAMPLE_ROWS = 5
#: 账号是 readonly=1 时改设置会报这个错误码
_READONLY_ERROR = "Code: 164"


def _quote(identifier: str) -> str:
    return "`" + identifier.replace("`", "\\`") + "`"


class ClickHouseDriver(Driver):
    kind = "clickhouse"
    dialect = "clickhouse"

    def __init__(self, config: ConnectionConfig) -> None:
        super().__init__(config)
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._settings_allowed = True

    def connection_key(self) -> str:
        c = self.config
        return f"clickhouse://{c.username}@{c.host}:{c.port}"

    async def _get_client(self) -> Any:
        async with self._lock:
            if self._client is None:
                c = self.config
                try:
                    self._client = await clickhouse_connect.get_async_client(
                        host=c.host, port=c.port, username=c.username, password=c.password, database=c.database,
                        secure=bool(c.options.get("secure")), connect_timeout=10,
                    )
                except (ClickHouseError, OSError) as exc:
                    raise DataSourceError(f"连接 ClickHouse 失败：{_describe(exc)}") from exc
            return self._client

    async def _fetch(
        self, sql: str, parameters: dict[str, Any] | None = None, timeout_seconds: int = 30
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        client = await self._get_client()
        settings = {"readonly": 2, "max_execution_time": timeout_seconds} if self._settings_allowed else None
        try:
            result = await asyncio.wait_for(client.query(sql, parameters=parameters, settings=settings),
                                            timeout_seconds + 5)
        except ClickHouseError as exc:
            if self._settings_allowed and _READONLY_ERROR in str(exc):
                self._settings_allowed = False
                return await self._fetch(sql, parameters, timeout_seconds)
            if "TIMEOUT_EXCEEDED" in str(exc):
                raise DataSourceError(f"查询超时（>{timeout_seconds} 秒），请缩小范围或加过滤条件") from exc
            raise DataSourceError(_describe(exc)) from exc
        except TimeoutError as exc:
            raise DataSourceError(f"查询超时（>{timeout_seconds} 秒），请缩小范围或加过滤条件") from exc
        return list(result.column_names), [tuple(r) for r in result.result_rows]

    async def test(self) -> str:
        _, rows = await self._fetch("SELECT version(), currentDatabase()")
        return f"ClickHouse {rows[0][0]}，当前库 {rows[0][1]}"

    async def list_tables(self) -> list[TableInfo]:
        _, rows = await self._fetch(
            "SELECT name, comment, total_rows FROM system.tables WHERE database = currentDatabase() "
            "AND NOT is_temporary ORDER BY name"
        )
        return [TableInfo(name=r[0], comment=r[1] or "", row_estimate=r[2]) for r in rows]

    async def catalog_columns(self) -> list[CatalogColumn]:
        _, rows = await self._fetch(
            "SELECT table, name, type, comment FROM system.columns WHERE database = currentDatabase() "
            "ORDER BY table, position"
        )
        return [CatalogColumn(table=r[0], name=r[1], type=r[2], comment=r[3] or "") for r in rows]

    async def describe_table(self, table: str) -> TableDetail:
        _, info = await self._fetch(
            "SELECT name, comment, total_rows, engine, sorting_key, partition_key FROM system.tables "
            "WHERE database = currentDatabase() AND name = {t:String}",
            {"t": table},
        )
        if not info:
            raise DataSourceError(f"表 {table} 不存在")
        _, cols = await self._fetch(
            "SELECT name, type, comment, is_in_primary_key FROM system.columns "
            "WHERE database = currentDatabase() AND table = {t:String} ORDER BY position",
            {"t": table},
        )
        names, sample = await self._fetch(f"SELECT * FROM {_quote(table)} LIMIT {_SAMPLE_ROWS}")
        columns = []
        for name, col_type, comment, in_pk in cols:
            top: list[tuple[Any, int]] = []
            if any(t in col_type for t in _LOW_CARDINALITY) or col_type.removeprefix("Nullable(") in _STRING_TYPES:
                top = await self._top_values(table, name)
            columns.append(
                ColumnInfo(name=name, type=col_type, nullable=col_type.startswith("Nullable"),
                           key="PRI" if in_pk else "", comment=comment or "", top_values=top)
            )
        name, comment, total, engine, sorting, partition = info[0]
        return TableDetail(
            table=TableInfo(name=name, comment=comment or "", row_estimate=total),
            columns=columns,
            sample_rows=[dict(zip(names, r, strict=True)) for r in sample],
            indexes=[f"引擎 {engine}", f"排序键 ({sorting})"] + ([f"分区键 {partition}"] if partition else []),
            note="ClickHouse 按排序键和分区键过滤最快；大表查询务必带上 event_date 之类的分区字段条件。",
        )

    async def _top_values(self, table: str, column: str) -> list[tuple[Any, int]]:
        col = _quote(column)
        _, rows = await self._fetch(
            f"SELECT {col}, count() AS n FROM (SELECT {col} FROM {_quote(table)} LIMIT {_TOP_VALUES_SCAN}) "
            f"GROUP BY {col} ORDER BY n DESC LIMIT {_TOP_VALUES_LIMIT + 1}"
        )
        return [] if len(rows) > _TOP_VALUES_LIMIT else [(r[0], r[1]) for r in rows]

    async def sample_values(self, table: str, column: str, limit: int) -> list[Any]:
        col = _quote(column)
        # 大表只扫前 100 万行，够抽样用
        _, rows = await self._fetch(
            f"SELECT DISTINCT {col} FROM (SELECT {col} FROM {_quote(table)} WHERE {col} IS NOT NULL LIMIT 1000000) "
            f"LIMIT {int(limit)}"
        )
        return [r[0] for r in rows]

    async def count_matches(self, table: str, column: str, values: list[Any]) -> int:
        if not values:
            return 0
        col = _quote(column)
        _, rows = await self._fetch(
            f"SELECT uniqExact(toString({col})) FROM {_quote(table)} WHERE toString({col}) IN {{v:Array(String)}}",
            {"v": [str(v) for v in values]},
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
        if self._client is not None:
            try:
                await self._client.close()
            finally:
                self._client = None


def _describe(exc: BaseException) -> str:
    text = str(exc).strip()
    # 服务端错误信息很长（含堆栈），只留第一段
    return "ClickHouse 错误：" + text.split("\n", 1)[0][:500] if text else type(exc).__name__
