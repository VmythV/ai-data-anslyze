"""联邦查询（Trino）：每个数据源同步成一个 catalog（名字 = 数据源标识），AI 用一条 SQL 跨引擎关联。

- 平台以 `platform` 用户管理 catalog，以 `agent` 用户执行 AI 的查询（Trino 侧规则只读、不能看 system 目录）
- catalog 只在 Trino 内存里：Trino 重启后，查询报「catalog 不存在」时自动重新同步一次
- MongoDB：先把抽样推断的结构写进 _schema 集合，否则 Trino 只看一个文档推断结构、会漏列
- 本地开发时数据源配的是 127.0.0.1，Trino 在容器里访问不到：用 trino_host_aliases 换成 host.docker.internal
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any
from urllib.parse import quote, urlsplit

import trino
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from trino.exceptions import TrinoExternalError, TrinoQueryError, TrinoUserError

from data_analyze.config import Settings
from data_analyze.datasources.base import DataSourceError, QueryResult
from data_analyze.datasources.mongodb import MongoDriver
from data_analyze.datasources.registry import DriverRegistry
from data_analyze.datasources.sources import connection_config
from data_analyze.security import SecretBox
from data_analyze.store.models import DataSource

logger = logging.getLogger(__name__)

ADMIN_USER = "platform"
AGENT_USER = "agent"
#: Trino 自带的目录名，数据源标识不能占用
RESERVED_CATALOGS = {"system", "information_schema", "jmx", "memory", "tpch", "tpcds"}
_MISSING_CATALOG = ("CATALOG_NOT_FOUND", "does not exist", "not found")


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Federation:
    def __init__(
        self, settings: Settings, sessions: async_sessionmaker[AsyncSession], box: SecretBox, drivers: DriverRegistry
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._box = box
        self._drivers = drivers
        url = urlsplit(settings.trino_url) if settings.trino_url else None
        self._host = url.hostname if url else None
        self._port = url.port or (443 if url and url.scheme == "https" else 8080) if url else None
        self._scheme = url.scheme if url else "http"
        self._aliases = dict(
            pair.split("=", 1) for pair in settings.trino_host_aliases.split(",") if "=" in pair
        )
        #: 标识 → 已同步配置的指纹
        self._synced: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return self._host is not None

    # ---------- Trino 调用 ----------

    def _run_sync(
        self, user: str, sql: str, max_rows: int | None, timeout_seconds: int
    ) -> tuple[list[str], list[tuple[Any, ...]], bool]:
        conn = trino.dbapi.connect(
            host=self._host, port=self._port, user=user, http_scheme=self._scheme,
            session_properties={"query_max_execution_time": f"{timeout_seconds}s"},
            request_timeout=timeout_seconds + 10,
        )
        try:
            cur = conn.cursor()
            cur.execute(sql)
            if max_rows is None:
                rows = cur.fetchall()
                truncated = False
            else:
                rows = cur.fetchmany(max_rows + 1)
                truncated = len(rows) > max_rows
                if truncated:
                    cur.cancel()
            columns = [d[0] for d in cur.description or ()]
            return columns, [tuple(r) for r in rows[: max_rows or None]], truncated
        finally:
            conn.close()

    async def _run(self, user: str, sql: str, max_rows: int | None = None, timeout_seconds: int = 60):  # noqa: ANN202
        try:
            return await asyncio.to_thread(self._run_sync, user, sql, max_rows, timeout_seconds)
        except TrinoQueryError as exc:
            raise DataSourceError(_describe(exc)) from exc
        except (OSError, trino.exceptions.TrinoConnectionError, trino.exceptions.HttpError) as exc:
            raise DataSourceError(f"联邦查询引擎不可用：{exc}") from exc

    # ---------- catalog 同步 ----------

    def _host_for_trino(self, host: str) -> str:
        return self._aliases.get(host, host)

    def catalog_properties(self, ds: DataSource, password: str) -> tuple[str, dict[str, str]]:
        host = self._host_for_trino(ds.host)
        options = ds.options or {}
        if ds.kind == "mysql":
            return "mysql", {"connection-url": f"jdbc:mysql://{host}:{ds.port}", "connection-user": ds.username,
                             "connection-password": password}
        if ds.kind == "postgresql":
            ssl = f"?sslmode={options['sslmode']}" if options.get("sslmode") else ""
            return "postgresql", {"connection-url": f"jdbc:postgresql://{host}:{ds.port}/{ds.database}{ssl}",
                                  "connection-user": ds.username, "connection-password": password}
        if ds.kind == "clickhouse":
            ssl = "?ssl=true" if options.get("secure") else ""
            # ClickHouse 的 String 默认映射成 varbinary，和字符串比较会报类型不匹配
            return "clickhouse", {"connection-url": f"jdbc:clickhouse://{host}:{ds.port}/{ssl}",
                                  "connection-user": ds.username, "connection-password": password,
                                  "clickhouse.map-string-as-varchar": "true"}
        if ds.kind == "mongodb":
            auth = f"{quote(ds.username, safe='')}:{quote(password, safe='')}@" if ds.username else ""
            params = [f"authSource={quote(options.get('auth_source') or ds.database)}"] if ds.username else []
            if options.get("replica_set"):
                params.append(f"replicaSet={quote(str(options['replica_set']))}")
            if options.get("tls"):
                params.append("tls=true")
            query = f"/?{'&'.join(params)}" if params else "/"
            return "mongodb", {"mongodb.connection-url": f"mongodb://{auth}{host}:{ds.port}{query}"}
        raise DataSourceError(f"{ds.kind} 暂不支持联邦查询")

    def _fingerprint(self, ds: DataSource) -> str:
        raw = repr((ds.alias, ds.kind, ds.host, ds.port, ds.username, ds.password_enc, ds.database,
                    sorted((ds.options or {}).items())))
        return hashlib.sha256(raw.encode()).hexdigest()

    async def sync(self, ds: DataSource, force: bool = False) -> None:
        if not self.enabled or not ds.alias:
            return
        fingerprint = self._fingerprint(ds)
        async with self._lock:
            if not force and self._synced.get(ds.alias) == fingerprint:
                return
            password = self._box.decrypt(ds.password_enc)
            connector, props = self.catalog_properties(ds, password)
            if ds.kind == "mongodb":
                driver = await self._drivers.get(ds.id, connection_config(ds, self._box))
                assert isinstance(driver, MongoDriver)
                try:
                    await driver.write_trino_schema()
                except DataSourceError as exc:
                    logger.warning("write _schema for %s failed (federated columns may be incomplete): %s", ds.alias, exc)
            with_clause = ", ".join(f'"{k}" = {_literal(v)}' for k, v in props.items())
            await self._run(ADMIN_USER, f'DROP CATALOG IF EXISTS "{ds.alias}"')
            await self._run(ADMIN_USER, f'CREATE CATALOG "{ds.alias}" USING {connector} WITH ({with_clause})')
            self._synced[ds.alias] = fingerprint
            logger.info("trino catalog %s synced (%s)", ds.alias, connector)

    async def drop(self, alias: str) -> None:
        if not self.enabled or not alias:
            return
        async with self._lock:
            self._synced.pop(alias, None)
            try:
                await self._run(ADMIN_USER, f'DROP CATALOG IF EXISTS "{alias}"')
            except DataSourceError as exc:
                logger.warning("drop catalog %s failed: %s", alias, exc)

    async def sync_all(self) -> None:
        async with self._sessions() as session:
            sources = (await session.scalars(select(DataSource))).all()
        errors = []
        for ds in sources:
            try:
                await self.sync(ds)
            except DataSourceError as exc:
                errors.append(f"{ds.alias}：{exc}")
        self.last_error = "；".join(errors) or None

    async def keep_synced(self) -> None:
        """启动时 Trino 可能还没起来：重试到同步成功，之后定期检查（Trino 重启后 catalog 会丢）。"""
        delay = 5
        while True:
            try:
                await self._run(ADMIN_USER, "SELECT 1")
                if self._synced:
                    _, rows, _ = await self._run(ADMIN_USER, "SHOW CATALOGS")
                    present = {r[0] for r in rows}
                    if any(alias not in present for alias in self._synced):
                        self._synced.clear()  # Trino 重启过
                await self.sync_all()
                delay = 300
            except DataSourceError as exc:
                self.last_error = str(exc)
                delay = min(delay * 2, 60)
            await asyncio.sleep(delay)

    async def status(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "catalogs": [], "error": None}
        try:
            _, rows, _ = await self._run(ADMIN_USER, "SHOW CATALOGS", timeout_seconds=10)
            catalogs = sorted(r[0] for r in rows if r[0] not in RESERVED_CATALOGS)
            return {"enabled": True, "catalogs": catalogs, "error": self.last_error}
        except DataSourceError as exc:
            return {"enabled": True, "catalogs": [], "error": str(exc)}

    # ---------- 查询 ----------

    async def query(self, sources: list[DataSource], sql: str, max_rows: int, timeout_seconds: int) -> QueryResult:
        for ds in sources:
            await self.sync(ds)
        started = time.monotonic()
        try:
            columns, rows, truncated = await self._run(AGENT_USER, sql, max_rows, timeout_seconds)
        except DataSourceError as exc:
            if not any(mark in str(exc) for mark in _MISSING_CATALOG):
                raise
            # Trino 重启过、catalog 丢了：重新同步后再试一次
            for ds in sources:
                await self.sync(ds, force=True)
            columns, rows, truncated = await self._run(AGENT_USER, sql, max_rows, timeout_seconds)
        return QueryResult(columns=columns, rows=rows, truncated=truncated,
                           elapsed_ms=int((time.monotonic() - started) * 1000))


def _describe(exc: TrinoQueryError) -> str:
    kind = "查询错误" if isinstance(exc, TrinoUserError) else "数据源错误" if isinstance(exc, TrinoExternalError) else "执行错误"
    return f"Trino {kind}（{exc.error_name}）：{exc.message[:600]}"
