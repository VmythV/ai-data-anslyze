"""会话、消息与运行事件流。

发消息：POST /conversations/{id}/messages 启动一轮运行；
看过程：GET /conversations/{id}/events?after=<seq>（SSE），断线后按 seq 续上。
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from data_analyze.api.deps import Services, db_session, services
from data_analyze.api.schemas import (
    ConversationDetail,
    ConversationIn,
    ConversationOut,
    ConversationPatch,
    MessageIn,
    MessageOut,
)
from data_analyze.datasources.sources import conversation_datasource_ids
from data_analyze.runs.manager import RunConflictError
from data_analyze.store.models import (
    Conversation,
    ConversationDataSource,
    DataDomain,
    DataSource,
    Message,
    ModelChannel,
)
from data_analyze.tools.chart_tool import polish_option

router = APIRouter(prefix="/api/conversations")
Session = Annotated[AsyncSession, Depends(db_session)]
Svc = Annotated[Services, Depends(services)]


def _out(c: Conversation, datasource_ids: list[str]) -> ConversationOut:
    return ConversationOut(
        id=c.id, title=c.title, channel_id=c.channel_id, datasource_ids=datasource_ids, domain_id=c.domain_id,
        created_at=c.created_at, updated_at=c.updated_at,
    )


async def _check_refs(
    session: AsyncSession, channel_id: str | None, datasource_ids: list[str] | None, domain_id: str | None
) -> None:
    if channel_id and await session.get(ModelChannel, channel_id) is None:
        raise HTTPException(400, "模型渠道不存在")
    if datasource_ids:
        found = set((await session.scalars(select(DataSource.id).where(DataSource.id.in_(datasource_ids)))).all())
        if found != set(datasource_ids):
            raise HTTPException(400, "部分数据源不存在")
    if domain_id and await session.get(DataDomain, domain_id) is None:
        raise HTTPException(400, "数据域不存在")


async def _set_datasources(session: AsyncSession, conversation_id: str, ids: list[str]) -> None:
    await session.execute(delete(ConversationDataSource).where(ConversationDataSource.conversation_id == conversation_id))
    session.add_all(
        ConversationDataSource(conversation_id=conversation_id, datasource_id=i) for i in dict.fromkeys(ids)
    )


def _polish_blocks(blocks: list[dict]) -> list[dict]:
    """历史图表也按当前的排版规则展示（不改库里存的原样）。"""
    return [
        {**b, "option": polish_option(b["option"])} if b.get("kind") == "chart" and isinstance(b.get("option"), dict)
        else b
        for b in blocks
    ]


@router.get("")
async def list_conversations(session: Session) -> list[ConversationOut]:
    rows = (await session.scalars(select(Conversation).order_by(Conversation.updated_at.desc()).limit(200))).all()
    links = (
        await session.execute(
            select(ConversationDataSource.conversation_id, ConversationDataSource.datasource_id).where(
                ConversationDataSource.conversation_id.in_([c.id for c in rows])
            )
        )
    ).all()
    by_conversation: dict[str, list[str]] = {}
    for conversation_id, datasource_id in links:
        by_conversation.setdefault(conversation_id, []).append(datasource_id)
    return [_out(c, by_conversation.get(c.id, [])) for c in rows]


@router.post("")
async def create_conversation(body: ConversationIn, session: Session) -> ConversationOut:
    channel_id = body.channel_id
    if channel_id is None:
        channel_id = (
            await session.scalars(select(ModelChannel.id).order_by(ModelChannel.is_default.desc()).limit(1))
        ).first()
    await _check_refs(session, channel_id, body.datasource_ids, body.domain_id)
    conversation = Conversation(channel_id=channel_id, domain_id=body.domain_id)
    session.add(conversation)
    await session.flush()
    await _set_datasources(session, conversation.id, body.datasource_ids)
    await session.commit()
    return _out(conversation, body.datasource_ids)


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str, session: Session, svc: Svc) -> ConversationDetail:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(404, "会话不存在")
    messages = (
        await session.scalars(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
        )
    ).all()
    run = svc.runs.active(conversation_id)
    datasource_ids = await conversation_datasource_ids(session, conversation_id)
    return ConversationDetail(
        **_out(conversation, datasource_ids).model_dump(),
        messages=[
            MessageOut(
                id=m.id, role=m.role, blocks=_polish_blocks(m.blocks), status=m.status, usage=m.usage,
                created_at=m.created_at,
            )
            for m in messages
        ],
        running=run is not None and not run.done,
    )


@router.patch("/{conversation_id}")
async def patch_conversation(conversation_id: str, body: ConversationPatch, session: Session) -> ConversationOut:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(404, "会话不存在")
    fields = body.model_dump(exclude_unset=True)
    await _check_refs(session, fields.get("channel_id"), fields.get("datasource_ids"), fields.get("domain_id"))
    if "channel_id" in fields and fields["channel_id"] != conversation.channel_id:
        # 换了模型渠道，旧的引擎会话接不上（协议状态不通用），历史消息仍保留在页面上
        conversation.engine_session_id = None
    datasource_ids = fields.pop("datasource_ids", None)
    if datasource_ids is not None:
        await _set_datasources(session, conversation_id, datasource_ids)
    for key, value in fields.items():
        setattr(conversation, key, value)
    await session.commit()
    return _out(conversation, await conversation_datasource_ids(session, conversation_id))


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str, session: Session, svc: Svc) -> dict[str, bool]:
    await svc.runs.stop(conversation_id)
    conversation = await session.get(Conversation, conversation_id)
    if conversation is not None:
        await session.delete(conversation)
        await session.commit()
    # 工作区、报告文件、引擎的对话记录
    await svc.housekeeper.remove_conversation_files(conversation_id)
    return {"ok": True}


@router.post("/{conversation_id}/messages")
async def send_message(conversation_id: str, body: MessageIn, svc: Svc) -> dict[str, str]:
    try:
        run = await svc.runs.start(conversation_id, body.content)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RunConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": run.id, "message_id": run.message_id}


@router.post("/{conversation_id}/stop")
async def stop_run(conversation_id: str, svc: Svc) -> dict[str, bool]:
    return {"stopped": await svc.runs.stop(conversation_id)}


@router.get("/{conversation_id}/events")
async def run_events(
    conversation_id: str,
    svc: Svc,
    after: Annotated[int, Query()] = -1,
    last_event_id: Annotated[str | None, Header()] = None,
) -> EventSourceResponse:
    run = svc.runs.active(conversation_id)
    if run is None:
        raise HTTPException(404, "当前没有进行中的分析")
    # 浏览器 EventSource 断线重连时自动带上最后收到的事件序号
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))

    async def stream():  # noqa: ANN202
        async for event in run.subscribe(after):
            yield {"id": str(event["seq"]), "data": json.dumps(event, ensure_ascii=False)}

    # ping 保活：中间的代理不会因为长时间无数据而断开
    return EventSourceResponse(stream(), ping=15, headers={"X-Accel-Buffering": "no"})
