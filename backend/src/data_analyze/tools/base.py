"""与引擎无关的工具定义。

Claude 引擎把它们包成进程内 MCP 工具；Codex 引擎经 engine/mcp_gateway.py 以 HTTP MCP 提供。
工具除了给模型的文字结果，还可以通过 `emit` 往对话里推送 UI 块（表格、图表、报告）。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from data_analyze.config import Settings
    from data_analyze.datasources.catalog import SchemaCatalog
    from data_analyze.datasources.federation import Federation
    from data_analyze.datasources.sources import SourceHandle
    from data_analyze.store.models import DataSource
    from data_analyze.tools.artifacts import ArtifactSink

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolOutput:
    text: str
    is_error: bool = False


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[ToolOutput]]
    #: 结果较大的只读工具放宽 Claude Code 的内联上限
    max_result_chars: int | None = None
    #: 调用后结束本轮（等待用户），不再让模型继续
    ends_turn: bool = False


@dataclass
class ToolContext:
    conversation_id: str
    workspace: Path
    settings: Settings
    #: 本会话可用的数据源，键是标识（alias）
    sources: dict[str, SourceHandle]
    catalog: SchemaCatalog
    artifacts: ArtifactSink
    #: 推送 UI 事件（dict，见 runs/blocks.py）
    emit: Callable[[dict[str, Any]], None]
    #: 本轮 SQL 结果文件计数，用来生成 q001、q002…
    counters: dict[str, int] = field(default_factory=dict)
    #: 联邦查询引擎；未启用为 None
    federation: Federation | None = None
    #: 本会话已选数据源的完整配置（联邦查询同步 catalog 用），与 sources 一一对应
    datasources: list[DataSource] = field(default_factory=list)
    #: 平台库会话工厂（提交关联建议等需要写库的工具用）
    sessions: async_sessionmaker[AsyncSession] | None = None

    def next_index(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def source(self, alias: Any, language: str | None = None) -> SourceHandle:
        """按标识取数据源；只有一个可用数据源时可以省略。"""
        if not self.sources:
            raise ToolInputError("当前会话没有选择数据源，请提示用户在输入框上方选择数据源或数据域")
        candidates = [s for s in self.sources.values() if language is None or s.driver.language == language]
        if not alias:
            if len(candidates) == 1:
                return candidates[0]
            raise ToolInputError(f"有多个数据源，请用 datasource 参数指定：{', '.join(s.alias for s in candidates)}")
        handle = self.sources.get(str(alias))
        if handle is None:
            raise ToolInputError(f"没有标识为「{alias}」的数据源，可用：{', '.join(self.sources)}")
        if language is not None and handle.driver.language != language:
            other = "run_mongo" if handle.driver.language == "mongo" else "run_sql"
            raise ToolInputError(f"数据源 {alias}（{handle.kind}）不能用这个工具查询，请改用 {other}")
        return handle


class ToolInputError(ValueError):
    """参数不合规：原因直接回给模型，让它自己改。"""


async def call_tool(spec: ToolSpec, args: dict[str, Any] | None) -> ToolOutput:
    """执行工具。参数错误、工具内部故障都作为错误结果回给模型，让它换个办法，而不是整轮崩掉。"""
    try:
        return await spec.handler(args or {})
    except ToolInputError as exc:
        return ToolOutput(f"参数错误：{exc}", is_error=True)
    except Exception as exc:
        logger.exception("tool %s failed", spec.name)
        return ToolOutput(f"工具内部错误：{type(exc).__name__}: {exc}", is_error=True)


def resolve_in_workspace(workspace: Path, relative: str) -> Path:
    """模型给的文件路径一律相对工作区，越界拒绝。"""
    candidate = (workspace / relative).resolve()
    root = workspace.resolve()
    if candidate != root and root not in candidate.parents:
        raise ToolInputError(f"路径必须位于工作区内：{relative}")
    return candidate
