"""启动时的数据迁移（建表、补列之后执行，可重复执行）。"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from data_analyze.store.models import Conversation, ConversationDataSource, DataSource

ALIAS_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def make_alias(base: str, taken: set[str]) -> str:
    """从库名等文字生成合法且不重复的标识。"""
    slug = re.sub(r"[^a-z0-9_]+", "_", base.lower()).strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"ds_{slug}".strip("_")
    slug = slug[:28]
    alias, n = slug, 2
    while alias in taken:
        alias = f"{slug}_{n}"
        n += 1
    return alias


async def run_migrations(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session:
        # 1. 旧数据源没有标识：按库名生成
        sources = (await session.scalars(select(DataSource).order_by(DataSource.created_at))).all()
        taken = {s.alias for s in sources if s.alias}
        for source in sources:
            if not source.alias:
                source.alias = make_alias(source.database or source.name, taken)
                taken.add(source.alias)
        # 2. 单数据源会话 → 多数据源关联表（历史消息不动）
        linked = set((await session.scalars(select(ConversationDataSource.conversation_id))).all())
        legacy = (
            await session.scalars(
                select(Conversation).where(Conversation.datasource_id.is_not(None))
            )
        ).all()
        for conversation in legacy:
            if conversation.id not in linked:
                session.add(
                    ConversationDataSource(conversation_id=conversation.id, datasource_id=conversation.datasource_id)
                )
            conversation.datasource_id = None
        await session.commit()
