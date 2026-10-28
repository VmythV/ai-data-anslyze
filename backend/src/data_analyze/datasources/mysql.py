"""MySQL 驱动：直接用 aiomysql 连接池。

不经 SQLAlchemy 的 text()：它会把字符串字面量里的 `:00` 当成绑定参数。
查询不带参数执行，`%` 原样传给服务端。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import aiomysql
import pymysql

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

_LOW_CARDINALITY_TYPES = ("char", "varchar", "enum", "tinyint", "set")
_TOP_VALUES_LIMIT = 20
_TOP_VALUES_SCAN = 20_000
_SAMPLE_ROWS = 5


def _quote(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


class MySqlDriver(Driver):
    kind = "mysql"
    dialect = "mysql"

    def __init__(self, config: ConnectionConfig) -> None:
        super().__init__(config)
        self._config = config
        self._pool: aiomysql.Pool | None = None
        self._lock = asyncio.Lock()

    def connection_key(self) -> str:
        # 同一实例、同一账号：一条 SQL 里可以用「库名.表名」引用别的库
        return f"mysql://{self._config.username}@{self._config.host}:{self._config.port}"

    async def _get_pool(self) -> aiomysql.Pool:
        async with self._lock:
            if self._pool is None:
                try:
                    self._pool = await aiomysql.create_pool(
                        host=self._config.host,
                        port=self._config.port,
                        user=self._config.username,
                        password=self._config.password,
                        db=self._config.database,
                        charset="utf8mb4",
                        autocommit=True,
                        minsize=0,
                        maxsize=5,
                        pool_recycle=600,
                        connect_timeout=10,
                    )
                except (pymysql.MySQLError, OSError) as exc:
                    raise DataSourceError(f"连接 MySQL 失败：{_describe(exc)}") from exc
            return self._pool

    async def _fetch(
        self, sql: str, args: tuple[Any, ...] | None = None, timeout_seconds: int = 30, prelude: tuple[str, ...] = ()
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn, conn.cursor() as cur:
                # 服务端超时只管 SELECT；客户端再兜一层，防止网络卡住
                await cur.execute(f"SET SESSION MAX_EXECUTION_TIME={timeout_seconds * 1000}")
                await cur.execute("SET SESSION TRANSACTION READ ONLY")
                # 会话级设置必须和查询在同一个连接上执行
                for statement in prelude:
                    await cur.execute(statement)
                await asyncio.wait_for(cur.execute(sql, args), timeout_seconds + 5)
                columns = [d[0] for d in cur.description or ()]
                rows = list(await cur.fetchall())
                return columns, rows
        except TimeoutError as exc:
            raise DataSourceError(f"查询超时（>{timeout_seconds} 秒），请缩小范围或加过滤条件") from exc
        except pymysql.MySQLError as exc:
            raise DataSourceError(_describe(exc)) from exc

    async def test(self) -> str:
        _, rows = await self._fetch("SELECT VERSION(), DATABASE()")
        version, database = rows[0]
        return f"MySQL {version}，当前库 {database}"

    async def list_tables(self) -> list[TableInfo]:
        _, rows = await self._fetch(
            "SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME",
            # 默认缓存一天的统计信息，新导入的表行数会显示为 0
            prelude=("SET SESSION information_schema_stats_expiry = 0",),
        )
        return [TableInfo(name=r[0], comment=r[1] or "", row_estimate=r[2]) for r in rows]

    async def catalog_columns(self) -> list[CatalogColumn]:
        _, rows = await self._fetch(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        return [CatalogColumn(table=r[0], name=r[1], type=r[2], comment=r[3] or "") for r in rows]

    async def describe_table(self, table: str) -> TableDetail:
        _, info = await self._fetch(
            "SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
        if not info:
            raise DataSourceError(f"表 {table} 不存在")
        _, cols = await self._fetch(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_COMMENT, DATA_TYPE "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION",
            (table,),
        )
        _, idx = await self._fetch(
            "SELECT INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) "
            "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "GROUP BY INDEX_NAME",
            (table,),
        )
        names, sample = await self._fetch(f"SELECT * FROM {_quote(table)} LIMIT {_SAMPLE_ROWS}")
        columns = []
        for name, col_type, nullable, key, comment, data_type in cols:
            top: list[tuple[Any, int]] = []
            if data_type in _LOW_CARDINALITY_TYPES and key != "PRI":
                top = await self._top_values(table, name)
            columns.append(
                ColumnInfo(
                    name=name, type=col_type, nullable=nullable == "YES", key=key or "",
                    comment=comment or "", top_values=top,
                )
            )
        row = info[0]
        return TableDetail(
            table=TableInfo(name=row[0], comment=row[1] or "", row_estimate=row[2]),
            columns=columns,
            sample_rows=[dict(zip(names, r, strict=True)) for r in sample],
            indexes=[f"{n}({c})" for n, c in idx],
        )

    async def _top_values(self, table: str, column: str) -> list[tuple[Any, int]]:
        col = _quote(column)
        _, rows = await self._fetch(
            f"SELECT {col}, COUNT(*) AS n FROM (SELECT {col} FROM {_quote(table)} "
            f"LIMIT {_TOP_VALUES_SCAN}) AS t GROUP BY {col} ORDER BY n DESC LIMIT {_TOP_VALUES_LIMIT + 1}"
        )
        # 取值超过上限说明不是枚举型字段，列出来没有意义
        return [] if len(rows) > _TOP_VALUES_LIMIT else [(r[0], r[1]) for r in rows]

    async def sample_values(self, table: str, column: str, limit: int) -> list[Any]:
        col = _quote(column)
        _, rows = await self._fetch(
            f"SELECT DISTINCT {col} FROM {_quote(table)} WHERE {col} IS NOT NULL LIMIT {int(limit)}"
        )
        return [r[0] for r in rows]

    async def count_matches(self, table: str, column: str, values: list[Any]) -> int:
        if not values:
            return 0
        col = _quote(column)
        marks = ", ".join(["%s"] * len(values))
        _, rows = await self._fetch(
            f"SELECT COUNT(DISTINCT CAST({col} AS CHAR)) FROM {_quote(table)} WHERE {col} IN ({marks})",
            tuple(str(v) for v in values),
        )
        return int(rows[0][0])

    async def query(self, sql: str, max_rows: int, timeout_seconds: int) -> QueryResult:
        started = time.monotonic()
        columns, rows = await self._fetch(sql, None, timeout_seconds)
        truncated = len(rows) > max_rows
        return QueryResult(
            columns=columns,
            rows=rows[:max_rows],
            truncated=truncated,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None


def _describe(exc: BaseException) -> str:
    if isinstance(exc, pymysql.MySQLError) and len(exc.args) >= 2:
        return f"MySQL 错误 {exc.args[0]}：{exc.args[1]}"
    return str(exc) or type(exc).__name__
