from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import Connection, event, inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from data_analyze.store.models import Base


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url)
        # SQLite 默认不校验外键，ON DELETE 也就不生效
        event.listen(self.engine.sync_engine, "connect", _sqlite_pragmas)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_add_missing_columns)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()


def _add_missing_columns(conn: Connection) -> None:
    """轻量迁移：模型里新增的可空列，在已有的表上补出来。改类型、删列这类变更不在这里处理。"""
    inspector = inspect(conn)
    for table in Base.metadata.sorted_tables:
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            if not column.nullable:
                raise RuntimeError(f"{table.name}.{column.name} 是新增的非空列，需要手工迁移")
            ddl = column.type.compile(dialect=conn.dialect)
            conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl}'))


def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
