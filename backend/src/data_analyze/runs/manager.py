"""对话运行管理。

一轮运行在后台任务里跑，与 HTTP 连接解耦：页面断开、刷新后可以按序号重放事件接着看；
运行结束时把助手消息落库。同一会话同时只有一轮，全局并发受信号量限制（每轮一个引擎子进程）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from data_analyze.config import Settings
from data_analyze.datasources.catalog import SchemaCatalog
from data_analyze.datasources.federation import Federation
from data_analyze.datasources.registry import DriverRegistry
from data_analyze.datasources.sources import SourceHandle, connection_config, resolve_sources
from data_analyze.engine.base import (
    AgentEngine,
    ChannelConfig,
    EngineUnavailableError,
    EventSink,
    ResumeMissingError,
    TurnOutcome,
    TurnRequest,
    create_engine,
)
from data_analyze.engine.prompts import RelationBrief, SourceBrief, build_system_prompt
from data_analyze.engine.workspace import next_query_index, prepare_workspace
from data_analyze.runs.blocks import apply_event, finalize
from data_analyze.security import SecretBox
from data_analyze.store.models import Conversation, Message, ModelChannel, Relation, new_id, utcnow
from data_analyze.tools import build_tools
from data_analyze.tools.artifacts import ArtifactSink
from data_analyze.tools.base import ToolContext

logger = logging.getLogger(__name__)

#: 运行结束后保留事件多久，供晚到或重连的页面重放
RETAIN_SECONDS = 300
INTERRUPT_GRACE_SECONDS = 10


class RunConflictError(Exception):
    pass


@dataclass
class Run:
    id: str
    conversation_id: str
    message_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    done: bool = False
    engine: AgentEngine | None = None
    task: asyncio.Task[None] | None = None
    stop_requested: bool = False
    #: 每来一个事件就 set 一次并换新的，等待者各自持有旧的那个
    _signal: asyncio.Event = field(default_factory=asyncio.Event)

    def emit(self, event: dict[str, Any]) -> None:
        event = {**event, "seq": len(self.events)}
        self.events.append(event)
        apply_event(self.blocks, event)
        self._wake()

    def finish(self) -> None:
        self.done = True
        self._wake()

    def _wake(self) -> None:
        signal, self._signal = self._signal, asyncio.Event()
        signal.set()

    def has_content(self) -> bool:
        return any(b["kind"] != "text" or b["text"].strip() for b in self.blocks)

    async def subscribe(self, after: int = -1) -> AsyncIterator[dict[str, Any]]:
        index = after + 1
        while True:
            signal = self._signal
            while index < len(self.events):
                yield self.events[index]
                index += 1
            if self.done:
                return
            await signal.wait()


class RunManager:
    def __init__(
        self,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        box: SecretBox,
        drivers: DriverRegistry,
        catalog: SchemaCatalog,
        federation: Federation | None = None,
    ) -> None:
        self._settings = settings
        self._sessions = sessions
        self._box = box
        self._drivers = drivers
        self._catalog = catalog
        self._federation = federation
        self._runs: dict[str, Run] = {}
        self._slots = asyncio.Semaphore(settings.max_concurrent_runs)

    def active(self, conversation_id: str) -> Run | None:
        return self._runs.get(conversation_id)

    async def start(self, conversation_id: str, prompt: str) -> Run:
        current = self._runs.get(conversation_id)
        if current is not None and not current.done:
            raise RunConflictError("上一条消息还在处理中")
        async with self._sessions() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None:
                raise LookupError("会话不存在")
            session.add(Message(conversation_id=conversation_id, role="user", blocks=[{"kind": "text", "text": prompt}]))
            if conversation.title == "新对话":
                conversation.title = prompt.strip().splitlines()[0][:40] or "新对话"
            conversation.updated_at = utcnow()
            await session.commit()
        run = Run(id=new_id(), conversation_id=conversation_id, message_id=new_id())
        self._runs[conversation_id] = run
        run.emit({"type": "run_started", "run_id": run.id, "message_id": run.message_id})
        run.task = asyncio.create_task(self._execute(run, prompt))
        return run

    async def stop(self, conversation_id: str) -> bool:
        run = self._runs.get(conversation_id)
        if run is None or run.done:
            return False
        run.stop_requested = True
        if run.engine is not None:
            await run.engine.interrupt()
        asyncio.get_running_loop().call_later(INTERRUPT_GRACE_SECONDS, self._force_cancel, run)
        return True

    def _force_cancel(self, run: Run) -> None:
        if not run.done and run.task is not None:
            logger.warning("run %s did not stop in time, cancelling", run.id)
            run.task.cancel()

    async def shutdown(self) -> None:
        for run in list(self._runs.values()):
            if run.task is not None and not run.done:
                run.task.cancel()

    async def _execute(self, run: Run, prompt: str) -> None:
        outcome = TurnOutcome(status="error", error="未知错误")
        try:
            if self._slots.locked():
                run.emit({"type": "status", "message": "当前分析任务较多，正在排队…"})
            async with self._slots:
                if run.stop_requested:
                    outcome = TurnOutcome(status="interrupted")
                else:
                    outcome = await self._run_turn(run, prompt)
        except asyncio.CancelledError:
            outcome = TurnOutcome(status="interrupted")
        except (EngineUnavailableError, LookupError, ValueError) as exc:
            outcome = TurnOutcome(status="error", error=str(exc))
        except Exception as exc:
            logger.exception("run %s failed", run.id)
            outcome = TurnOutcome(status="error", error=f"运行失败：{type(exc).__name__}: {exc}")
        finally:
            if outcome.status == "error" and outcome.error:
                run.emit({"type": "error", "message": outcome.error})
            await self._persist(run, outcome)
            run.emit({"type": "done", "status": outcome.status, "message_id": run.message_id, "usage": outcome.usage})
            run.finish()
            asyncio.get_running_loop().call_later(RETAIN_SECONDS, self._forget, run)

    def _forget(self, run: Run) -> None:
        if self._runs.get(run.conversation_id) is run:
            del self._runs[run.conversation_id]

    async def _run_turn(self, run: Run, prompt: str) -> TurnOutcome:
        async with self._sessions() as session:
            conversation = await session.get(Conversation, run.conversation_id)
            if conversation is None:
                raise LookupError("会话不存在")
            channel = await session.get(ModelChannel, conversation.channel_id) if conversation.channel_id else None
            datasources, domain = await resolve_sources(session, conversation)
            ids = [d.id for d in datasources]
            relations = (
                await session.scalars(
                    select(Relation).where(
                        Relation.left_datasource_id.in_(ids), Relation.right_datasource_id.in_(ids),
                        # 待审核的只给提出它的会话看；管理员采纳后所有会话可见
                        (Relation.status == "confirmed") | (Relation.conversation_id == run.conversation_id),
                    )
                )
            ).all() if ids else []
        if channel is None:
            raise LookupError("会话没有可用的模型渠道，请先在「设置」中添加模型渠道")

        sources: dict[str, SourceHandle] = {}
        for ds in datasources:
            driver = await self._drivers.get(ds.id, connection_config(ds, self._box))
            sources[ds.alias or ds.id] = SourceHandle(
                id=ds.id, alias=ds.alias or ds.id, name=ds.name, kind=ds.kind, database=ds.database,
                description=ds.description, driver=driver,
            )
        alias_of = {s.id: s.alias for s in sources.values()}
        engine = create_engine(channel.protocol, self._settings)
        system_prompt = build_system_prompt(
            [SourceBrief(s.alias, s.name, s.kind, s.database, s.description, s.connection_key) for s in sources.values()],
            (domain.name, domain.description) if domain else None,
            [
                RelationBrief(
                    left=f"{alias_of[r.left_datasource_id]}.{r.left_table}.{r.left_column}",
                    right=f"{alias_of[r.right_datasource_id]}.{r.right_table}.{r.right_column}",
                    cardinality=r.cardinality, transform=r.transform, description=r.description,
                    confirmed=r.status == "confirmed",
                )
                for r in relations
            ],
            federation=bool(self._federation and self._federation.enabled),
            engine=engine.kind,
        )
        workspace = prepare_workspace(self._settings.workspaces, run.conversation_id)
        run.engine = engine
        sink = EventSink(run.emit)
        ctx = ToolContext(
            conversation_id=run.conversation_id,
            workspace=workspace,
            settings=self._settings,
            sources=sources,
            catalog=self._catalog,
            federation=self._federation,
            datasources=list(datasources),
            sessions=self._sessions,
            artifacts=ArtifactSink(self._sessions, run.conversation_id, self._settings.artifacts_dir),
            emit=sink.defer,
            counters={"sql": next_query_index(workspace)},
        )
        request = TurnRequest(
            conversation_id=run.conversation_id,
            prompt=prompt,
            workspace=workspace,
            channel=ChannelConfig(
                protocol=channel.protocol, base_url=channel.base_url,
                api_key=self._box.decrypt(channel.api_key_enc), model=channel.model,
            ),
            system_prompt=system_prompt,
            tools=[*build_tools(ctx), *engine.extra_tools(ctx)],
            resume_session_id=conversation.engine_session_id,
        )
        try:
            return await engine.run(request, sink)
        except ResumeMissingError as exc:
            if run.has_content():
                return TurnOutcome(status="error", error=str(exc))
            # 会话记录丢了（比如数据卷被清）：不带历史重新开始，而不是让这个会话永远不可用
            logger.warning("resume %s missing, starting fresh", request.resume_session_id)
            engine = create_engine(channel.protocol, self._settings)
            run.engine = engine
            return await engine.run(TurnRequest(**{**request.__dict__, "resume_session_id": None}), sink)

    async def _persist(self, run: Run, outcome: TurnOutcome) -> None:
        try:
            async with self._sessions() as session:
                session.add(
                    Message(
                        id=run.message_id, conversation_id=run.conversation_id, role="assistant",
                        blocks=finalize(run.blocks), status=outcome.status, usage=outcome.usage or None,
                    )
                )
                conversation = await session.get(Conversation, run.conversation_id)
                if conversation is not None:
                    if outcome.session_id:
                        conversation.engine_session_id = outcome.session_id
                    conversation.updated_at = utcnow()
                await session.commit()
        except Exception:
            logger.exception("persist run %s failed", run.id)
