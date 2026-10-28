"""基于 Claude Agent SDK 的引擎。

每轮对话起一个 Claude Code 子进程，用 resume 接上历史。给它的能力：
- 平台工具（进程内 MCP，前缀 mcp__analysis__）
- 内置的 Read / Write / Edit / Glob / Grep（路径受 guard 约束）和 Skill
- 没有 Bash、没有联网工具、不读任何本机配置（setting_sources=[]）
"""

from __future__ import annotations

import logging
import os
import warnings
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    HookMatcher,
    PermissionResultDeny,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolAnnotations,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    project_key_for_directory,
    tool,
)

from data_analyze.config import SKILLS_PLUGIN_DIR, Settings
from data_analyze.engine.base import (
    AgentEngine,
    EventSink,
    ResumeMissingError,
    TurnOutcome,
    TurnRequest,
    friendly_error,
)
from data_analyze.engine.guard import GUARDED_MATCHER, check_path
from data_analyze.engine.prompts import skill_catalog
from data_analyze.tools.base import ToolSpec, call_tool

logger = logging.getLogger(__name__)
# 有意为之：白名单内的工具直接放行，can_use_tool 只兜底拒绝白名单外的
warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)

MCP_SERVER = "analysis"
PLUGIN_NAME = "analyst"
MCP_PREFIX = f"mcp__{MCP_SERVER}__"
BUILTIN_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Skill"]
#: 工具结果回显到页面的上限
TOOL_OUTPUT_UI_LIMIT = 6_000
SDK_MESSAGE_BUFFER_BYTES = 32 * 1024 * 1024
_RESUME_MISSING = ("No conversation found", "no conversation found")


def _mcp_tool(spec: ToolSpec):  # noqa: ANN202
    annotations = ToolAnnotations(maxResultSizeChars=spec.max_result_chars) if spec.max_result_chars else None

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        output = await call_tool(spec, args)
        return {"content": [{"type": "text", "text": output.text}], "is_error": output.is_error}

    return tool(spec.name, spec.description, spec.input_schema, annotations=annotations)(handler)


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return ""


def transcript_dir(settings: Settings, workspace: Path) -> Path:
    """Claude Code 按工作目录存放对话记录：<配置目录>/projects/<目录键>/<session>.jsonl"""
    return settings.claude_config_dir / "projects" / project_key_for_directory(workspace.resolve())


def display_name(name: str) -> str:
    return name.removeprefix(MCP_PREFIX)


class ClaudeAgentEngine(AgentEngine):
    kind = "claude"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: ClaudeSDKClient | None = None
        self._interrupted = False
        #: 本轮被 ends_turn 工具主动结束：CLI 可能把这当成异常收尾，不算出错
        self._ended_by_tool = False

    def build_options(self, turn: TurnRequest) -> ClaudeAgentOptions:
        workspace = turn.workspace.resolve()
        server = create_sdk_mcp_server(MCP_SERVER, "1.0.0", [_mcp_tool(s) for s in turn.tools])
        readable = [SKILLS_PLUGIN_DIR.resolve()]

        async def path_guard(input_data: dict[str, Any], _tool_use_id: str | None, _ctx: Any) -> dict[str, Any]:
            reason = check_path(
                str(input_data.get("tool_name", "")), input_data.get("tool_input") or {}, workspace, readable
            )
            if reason is None:
                return {}
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }

        async def end_turn(_input: dict[str, Any], _tool_use_id: str | None, _ctx: Any) -> dict[str, Any]:
            # 例如 ask_user：问题已经交给用户，这一轮到此为止
            self._ended_by_tool = True
            return {"continue_": False, "stopReason": "等待用户回答"}

        ending = "|".join(f"{MCP_PREFIX}{s.name}" for s in turn.tools if s.ends_turn)
        hooks: dict[str, list[HookMatcher]] = {
            "PreToolUse": [HookMatcher(matcher=GUARDED_MATCHER, hooks=[path_guard])]
        }
        if ending:
            hooks["PostToolUse"] = [HookMatcher(matcher=ending, hooks=[end_turn])]

        async def deny_others(tool_name: str, _input: dict[str, Any], _ctx: Any) -> PermissionResultDeny:
            return PermissionResultDeny(message=f"工具 {tool_name} 在本平台不可用")

        channel = turn.channel
        env = {
            "ANTHROPIC_BASE_URL": channel.base_url,
            "ANTHROPIC_AUTH_TOKEN": channel.api_key,
            "ANTHROPIC_API_KEY": channel.api_key,
            "ANTHROPIC_MODEL": channel.model,
            # 兼容网关上往往只有一个模型：后台小任务也走它，避免请求不存在的模型
            "ANTHROPIC_DEFAULT_OPUS_MODEL": channel.model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": channel.model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": channel.model,
            "CLAUDE_CODE_SUBAGENT_MODEL": channel.model,
            "CLAUDE_CONFIG_DIR": str(self._settings.claude_config_dir),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
            "CLAUDE_AGENT_SDK_CLIENT_APP": "data-analyze/0.1",
        }
        # SDK 会把本进程的环境变量整体传给子进程：平台自己的密钥不能跟过去
        env |= {key: "" for key in os.environ if key.startswith("DA_")}

        return ClaudeAgentOptions(
            model=channel.model,
            cwd=str(workspace),
            system_prompt=turn.system_prompt,
            tools=BUILTIN_TOOLS,
            allowed_tools=[*BUILTIN_TOOLS, f"mcp__{MCP_SERVER}"],
            mcp_servers={MCP_SERVER: server},
            strict_mcp_config=True,
            setting_sources=[],
            plugins=[{"type": "local", "path": str(SKILLS_PLUGIN_DIR.resolve())}],
            # 只开放平台的分析技能；CLI 自带的编码类技能不给
            skills=[f"{PLUGIN_NAME}:{name}" for name, _ in skill_catalog()],
            can_use_tool=deny_others,
            hooks=hooks,
            max_turns=self._settings.agent_max_turns,
            resume=turn.resume_session_id,
            include_partial_messages=True,
            env=env,
            max_buffer_size=SDK_MESSAGE_BUFFER_BYTES,
            stderr=lambda line: logger.debug("claude-cli: %s", line),
        )

    async def run(self, turn: TurnRequest, sink: EventSink) -> TurnOutcome:
        outcome = TurnOutcome(status="completed", session_id=turn.resume_session_id)
        # 推过文本增量的 API 消息；它们对应的完整 AssistantMessage 不再重复推文本
        streamed_ids: set[str] = set()
        current_id: str | None = None
        try:
            async with ClaudeSDKClient(self.build_options(turn)) as client:
                self._client = client
                await client.query(turn.prompt)
                async for message in client.receive_response():
                    if isinstance(message, StreamEvent):
                        if message.parent_tool_use_id:
                            continue
                        event = message.event
                        if event.get("type") == "message_start":
                            current_id = event.get("message", {}).get("id")
                        elif event.get("type") == "content_block_start" and (
                            event.get("content_block", {}).get("type") == "text"
                        ):
                            sink.emit({"type": "text_start"})
                        elif event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                if current_id:
                                    streamed_ids.add(current_id)
                                sink.emit({"type": "text", "delta": delta["text"]})
                    elif isinstance(message, AssistantMessage):
                        if message.parent_tool_use_id:
                            continue
                        if message.error:
                            outcome.error = f"模型调用出错：{message.error}"
                        for block in message.content:
                            if isinstance(block, TextBlock) and block.text:
                                # 增量已经推过的不重复推；网关不支持流式时这里补上整段
                                if (message.message_id or current_id) not in streamed_ids:
                                    sink.emit({"type": "text_start"})
                                    sink.emit({"type": "text", "delta": block.text})
                            elif isinstance(block, ToolUseBlock):
                                sink.emit(
                                    {"type": "tool_call", "id": block.id, "name": display_name(block.name),
                                     "input": block.input}
                                )
                    elif isinstance(message, UserMessage):
                        if message.parent_tool_use_id or not isinstance(message.content, list):
                            continue
                        for block in message.content:
                            if isinstance(block, ToolResultBlock):
                                text = _tool_result_text(block.content)
                                sink.emit(
                                    {"type": "tool_result", "id": block.tool_use_id, "is_error": bool(block.is_error),
                                     "output": text[:TOOL_OUTPUT_UI_LIMIT]}
                                )
                        sink.flush()
                    elif isinstance(message, SystemMessage) and message.subtype == "init":
                        outcome.session_id = message.data.get("session_id") or outcome.session_id
                        logger.info(
                            "claude session %s tools=%s skills=%s",
                            outcome.session_id, message.data.get("tools"), message.data.get("skills"),
                        )
                    elif isinstance(message, ResultMessage):
                        outcome.session_id = message.session_id or outcome.session_id
                        outcome.usage = _usage(message)
                        if message.is_error and not self._interrupted and not self._ended_by_tool:
                            outcome.status = "error"
                            detail = message.result or "；".join(message.errors or []) or message.subtype
                            outcome.error = friendly_error(detail, message.api_error_status)
        except ClaudeSDKError as exc:
            if not self._interrupted:
                logger.exception("claude engine failed")
                outcome.status = "error"
                outcome.error = f"Agent 运行失败：{exc}"
        finally:
            self._client = None
            sink.flush()
        if self._interrupted:
            outcome.status = "interrupted"
        elif outcome.error and outcome.status == "completed":
            outcome.status = "error"
        if (
            outcome.status == "error" and turn.resume_session_id and outcome.error
            and any(mark in outcome.error for mark in _RESUME_MISSING)
        ):
            raise ResumeMissingError(outcome.error)
        return outcome

    async def interrupt(self) -> None:
        self._interrupted = True
        if self._client is not None:
            try:
                await self._client.interrupt()
            except ClaudeSDKError:
                logger.warning("interrupt failed", exc_info=True)


def _usage(message: ResultMessage) -> dict[str, Any]:
    """整理成与引擎无关的扁平结构；输入 token 含缓存命中和缓存写入的部分。"""
    raw = message.usage or {}
    cache_read = int(raw.get("cache_read_input_tokens") or 0)
    input_tokens = int(raw.get("input_tokens") or 0) + cache_read + int(raw.get("cache_creation_input_tokens") or 0)
    return {
        "duration_ms": message.duration_ms,
        "num_turns": message.num_turns,
        "input_tokens": input_tokens,
        "output_tokens": int(raw.get("output_tokens") or 0),
        "cache_read_tokens": cache_read,
        "cost_usd": message.total_cost_usd,
    }
