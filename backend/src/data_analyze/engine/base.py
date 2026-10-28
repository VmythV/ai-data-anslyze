"""Agent 引擎接口。

一轮对话 = 用户一条消息 → 引擎自主多轮调用工具 → 最终回答。引擎把过程投影成统一的 UI 事件
（runs/blocks.py），所以换引擎（Claude Agent SDK ⇄ Codex SDK）前端和存储都不用动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from data_analyze.tools.base import ToolSpec

if TYPE_CHECKING:
    from data_analyze.tools.base import ToolContext


@dataclass(frozen=True)
class ChannelConfig:
    protocol: str
    base_url: str
    api_key: str = field(repr=False)
    model: str


@dataclass(frozen=True)
class TurnRequest:
    conversation_id: str
    prompt: str
    workspace: Path
    channel: ChannelConfig
    system_prompt: str
    tools: list[ToolSpec]
    #: 引擎侧会话 ID：续聊时带上，首轮为空
    resume_session_id: str | None = None


@dataclass
class TurnOutcome:
    #: completed | interrupted | error
    status: str
    session_id: str | None = None
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class EventSink:
    """引擎事件直接发出；工具产出的 UI 块先暂存，等对应的工具结果投影后再发，保证顺序。"""

    def __init__(self, emit: Callable[[dict[str, Any]], None]) -> None:
        self._emit = emit
        self._deferred: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self._emit(event)

    def defer(self, event: dict[str, Any]) -> None:
        self._deferred.append(event)

    def flush(self) -> None:
        events, self._deferred = self._deferred, []
        for event in events:
            self._emit(event)


class AgentEngine(ABC):
    #: 提示词按它介绍引擎特有的工具（claude | codex）
    kind: str = ""

    def extra_tools(self, ctx: ToolContext) -> list[ToolSpec]:
        """引擎自己缺、需要平台补上的工具。"""
        return []

    @abstractmethod
    async def run(self, turn: TurnRequest, sink: EventSink) -> TurnOutcome: ...

    @abstractmethod
    async def interrupt(self) -> None:
        """请求尽快停下；run() 随后以 interrupted 结束。"""


class EngineUnavailableError(Exception):
    pass


class ResumeMissingError(Exception):
    """续聊用的引擎会话记录找不到了（比如数据卷被清）。"""


def friendly_error(detail: str, status: int | None) -> str:
    if status in (401, 403):
        return f"模型渠道鉴权失败（HTTP {status}），请检查 API Key。{detail}"
    if status == 404:
        return f"模型或接口地址不存在（HTTP 404），请检查 Base URL 和模型名。{detail}"
    if status == 429:
        return f"模型渠道限流（HTTP 429），请稍后重试。{detail}"
    if status:
        return f"模型调用失败（HTTP {status}）：{detail}"
    return f"Agent 运行失败：{detail}"


def engine_state_dirs(settings: Any, conversation_id: str, workspace: Path) -> list[Path]:
    """引擎为某个会话在本机留下的状态（对话记录等），删除会话时一并清掉。"""
    from data_analyze.engine.claude import transcript_dir
    from data_analyze.engine.codex import codex_home

    return [transcript_dir(settings, workspace), codex_home(settings, conversation_id)]


def create_engine(protocol: str, settings: Any) -> AgentEngine:
    if protocol == "anthropic":
        from data_analyze.engine.claude import ClaudeAgentEngine

        return ClaudeAgentEngine(settings)
    if protocol == "openai":
        from data_analyze.engine.codex import CodexAgentEngine

        return CodexAgentEngine(settings)
    raise EngineUnavailableError(f"「{protocol}」协议的模型渠道暂未接入 Agent 引擎")
