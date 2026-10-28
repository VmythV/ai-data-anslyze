"""会话可用的数据源：数据域里的数据源 ∪ 手动勾选的数据源。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from data_analyze.datasources.base import ConnectionConfig, Driver
from data_analyze.security import SecretBox
from data_analyze.store.models import Conversation, ConversationDataSource, DataDomain, DataSource, DomainDataSource


@dataclass(frozen=True)
class SourceHandle:
    """一轮对话里可用的一个数据源。"""

    id: str
    alias: str
    name: str
    kind: str
    database: str
    description: str
    driver: Driver

    @property
    def connection_key(self) -> str | None:
        return self.driver.connection_key()

    @property
    def language(self) -> str:
        return self.driver.language


def connection_config(ds: DataSource, box: SecretBox) -> ConnectionConfig:
    return ConnectionConfig(
        kind=ds.kind, host=ds.host, port=ds.port, username=ds.username,
        password=box.decrypt(ds.password_enc), database=ds.database, options=dict(ds.options or {}),
    )


async def conversation_datasource_ids(session: AsyncSession, conversation_id: str) -> list[str]:
    """手动勾选的数据源（不含数据域展开的）。"""
    rows = await session.scalars(
        select(ConversationDataSource.datasource_id).where(ConversationDataSource.conversation_id == conversation_id)
    )
    return list(rows.all())


async def domain_datasource_ids(session: AsyncSession, domain_id: str) -> list[str]:
    rows = await session.scalars(select(DomainDataSource.datasource_id).where(DomainDataSource.domain_id == domain_id))
    return list(rows.all())


async def resolve_sources(
    session: AsyncSession, conversation: Conversation
) -> tuple[list[DataSource], DataDomain | None]:
    """会话最终可用的数据源（按创建时间排序、去重）与选中的数据域。"""
    ids = set(await conversation_datasource_ids(session, conversation.id))
    domain = await session.get(DataDomain, conversation.domain_id) if conversation.domain_id else None
    if domain is not None:
        ids |= set(await domain_datasource_ids(session, domain.id))
    if not ids:
        return [], domain
    sources = (
        await session.scalars(select(DataSource).where(DataSource.id.in_(ids)).order_by(DataSource.created_at))
    ).all()
    return list(sources), domain
