"""基于 OpenAI Codex Python SDK 的引擎（OpenAI 协议渠道，走 Responses API）。

每轮起一个 `codex app-server` 子进程，用 thread_resume 接上历史。给它的能力：
- 平台工具：经 engine/mcp_gateway.py 以 HTTP MCP 提供，外加工作区文件工具和 load_skill
- 关掉 Codex 自带的 shell、补丁、联网搜索、子代理等全部内置工具，审批请求一律拒绝
- 每个会话独立的 CODEX_HOME，不读本机 ~/.codex，也不加载工作区里的 AGENTS.md
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from openai_codex import (
    ApprovalMode,
    AsyncCodex,
    AsyncThread,
    AsyncTurnHandle,
    CodexConfig,
    CodexError,
    JsonRpcError,
    Sandbox,
)

from data_analyze.config import Settings
from data_analyze.engine.base import (
    AgentEngine,
    EventSink,
    ResumeMissingError,
    TurnOutcome,
    TurnRequest,
    friendly_error,
)
from data_analyze.engine.mcp_gateway import GATEWAY, MOUNT_PATH, SERVER_NAME
from data_analyze.tools.base import ToolContext, ToolSpec
from data_analyze.tools.file_tools import file_tools

logger = logging.getLogger(__name__)

PROVIDER_ID = "da"
API_KEY_ENV = "DA_CODEX_CHANNEL_KEY"
MCP_TOKEN_ENV = "DA_CODEX_MCP_TOKEN"
#: 工具结果回显到页面的上限
TOOL_OUTPUT_UI_LIMIT = 6_000
#: 单个工具结果给模型的上限（约等于平台工具里最大的 max_result_chars）
TOOL_OUTPUT_TOKENS = 25_000
#: Codex 自带、本平台不开放的能力
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "shell_snapshot", "view_image", "multi_agent", "goals", "apps", "plugins",
    "image_generation", "tool_suggest", "hooks", "memories", "browser_use", "computer_use",
)
_RESUME_MISSING = re.compile(r"no rollout|not found|no such thread|unknown thread", re.I)
_HTTP_STATUS = re.compile(r'"httpStatusCode":\s*(\d+)')
_ERROR_STATUS = {"unauthorized": 401, "rateLimitExceeded": 429, "usageLimitExceeded": 429, "badRequest": 400}


def codex_home(settings: Settings, conversation_id: str) -> Path:
    return settings.codex_homes_dir / conversation_id


def _decline(method: str, _params: dict[str, Any] | None) -> dict[str, Any]:
    """app-server 发来的审批 / 提问请求一律拒绝（内置工具都关了，正常不会走到这里）。"""
    logger.warning("codex server request declined: %s", method)
    if method.endswith("/requestApproval"):
        return {"decision": "decline"}
    if "elicitation" in method:
        return {"action": "decline"}
    return {}


def _toml(value: Any) -> str:
    # TOML 的基本字符串、布尔、整数与 JSON 写法兼容
    return json.dumps(value, ensure_ascii=False)


class CodexAgentEngine(AgentEngine):
    kind = "codex"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._turn: AsyncTurnHandle | None = None
        self._interrupted = False
        #: 本轮被 ends_turn 工具主动结束：Codex 记为 interrupted，不算出错也不算用户中断
        self._ended_by_tool = False
        #: 因工具调用次数超限被停下
        self._over_limit = False

    def extra_tools(self, ctx: ToolContext) -> list[ToolSpec]:
        return file_tools(ctx)

    def build_config(self, turn: TurnRequest, mcp_token: str) -> tuple[CodexConfig, dict[str, Any]]:
        settings = self._settings
        channel = turn.channel
        tool_timeout = max(
            settings.python_timeout_seconds, settings.federated_timeout_seconds, settings.sql_timeout_seconds
        ) + 60
        values: dict[str, Any] = {
            "model_provider": PROVIDER_ID,
            "model": channel.model,
            f"model_providers.{PROVIDER_ID}.name": "data-analyze",
            f"model_providers.{PROVIDER_ID}.base_url": channel.base_url,
            f"model_providers.{PROVIDER_ID}.env_key": API_KEY_ENV,
            f"model_providers.{PROVIDER_ID}.wire_api": "responses",
            f"mcp_servers.{SERVER_NAME}.url": f"{settings.internal_base_url}{MOUNT_PATH}/",
            f"mcp_servers.{SERVER_NAME}.bearer_token_env_var": MCP_TOKEN_ENV,
            f"mcp_servers.{SERVER_NAME}.required": True,
            f"mcp_servers.{SERVER_NAME}.startup_timeout_sec": 20,
            f"mcp_servers.{SERVER_NAME}.tool_timeout_sec": tool_timeout,
            f"mcp_servers.{SERVER_NAME}.default_tools_approval_mode": "approve",
            "web_search": "disabled",
            "skills.include_instructions": False,
            "tools.experimental_request_user_input.enabled": False,
            "tools.update_plan.enabled": False,
            "project_doc_max_bytes": 0,
            "include_permissions_instructions": False,
            "include_apps_instructions": False,
            "check_for_update_on_startup": False,
            "tool_output_token_limit": TOOL_OUTPUT_TOKENS,
            **{f"features.{name}": False for name in DISABLED_FEATURES},
        }
        home = codex_home(settings, turn.conversation_id)
        home.mkdir(parents=True, exist_ok=True)
        loopback = "127.0.0.1,localhost,::1"
        env = {
            # 子进程继承本进程环境：平台自己的密钥不能跟过去
            **{key: "" for key in os.environ if key.startswith("DA_")},
            "CODEX_HOME": str(home),
            API_KEY_ENV: channel.api_key,
            MCP_TOKEN_ENV: mcp_token,
            # 回调 MCP 网关不能走代理
            "NO_PROXY": ",".join(filter(None, [os.environ.get("NO_PROXY", ""), loopback])),
            "no_proxy": ",".join(filter(None, [os.environ.get("no_proxy", ""), loopback])),
        }
        config = CodexConfig(
            config_overrides=tuple(f"{key}={_toml(value)}" for key, value in values.items()),
            cwd=str(turn.workspace.resolve()),
            env=env,
            client_name="data_analyze",
            client_title="data-analyze",
        )
        thread_options = {
            "cwd": str(turn.workspace.resolve()),
            "base_instructions": turn.system_prompt,
            "model": channel.model,
            "model_provider": PROVIDER_ID,
            "sandbox": Sandbox.read_only,
            "approval_mode": ApprovalMode.deny_all,
        }
        return config, thread_options

    async def run(self, turn: TurnRequest, sink: EventSink) -> TurnOutcome:
        outcome = TurnOutcome(status="completed", session_id=turn.resume_session_id)
        ending = {spec.name for spec in turn.tools if spec.ends_turn}
        try:
            async with GATEWAY.serve(turn.tools) as token:
                config, thread_options = self.build_config(turn, token)
                async with AsyncCodex(config) as codex:
                    # SDK 默认批准一切审批请求，换成一律拒绝
                    codex._client._sync._approval_handler = _decline
                    thread = await self._open_thread(codex, turn, thread_options)
                    outcome.session_id = thread.id
                    handle = await thread.turn(turn.prompt)
                    self._turn = handle
                    if self._interrupted:
                        await handle.interrupt()
                    await self._consume(handle, sink, outcome, ending)
        except ResumeMissingError:
            raise
        except CodexError as exc:
            if not self._interrupted:
                logger.exception("codex engine failed")
                outcome.status = "error"
                outcome.error = f"Agent 运行失败：{exc}"
        finally:
            self._turn = None
            sink.flush()
        if self._interrupted:
            outcome.status = "interrupted"
        elif outcome.error and outcome.status == "completed":
            outcome.status = "error"
        return outcome

    async def _open_thread(self, codex: AsyncCodex, turn: TurnRequest, options: dict[str, Any]) -> AsyncThread:
        if not turn.resume_session_id:
            return await codex.thread_start(**options)
        try:
            return await codex.thread_resume(turn.resume_session_id, **options)
        except JsonRpcError as exc:
            if _RESUME_MISSING.search(str(exc)):
                raise ResumeMissingError(str(exc)) from exc
            raise

    async def _consume(
        self, handle: AsyncTurnHandle, sink: EventSink, outcome: TurnOutcome, ending: set[str]
    ) -> None:
        streamed: set[str] = set()
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "reasoning_tokens": 0}
        tool_calls = 0
        async for event in handle.stream():
            method, payload = event.method, event.payload
            if method == "item/agentMessage/delta":
                if payload.delta:
                    if payload.item_id not in streamed:
                        streamed.add(payload.item_id)
                        sink.emit({"type": "text_start"})
                    sink.emit({"type": "text", "delta": payload.delta})
            elif method == "item/started":
                item = payload.item.root
                if item.type == "mcpToolCall":
                    sink.emit({"type": "tool_call", "id": item.id, "name": item.tool, "input": item.arguments or {}})
                elif item.type not in ("userMessage", "agentMessage", "reasoning"):
                    logger.warning("unexpected codex item %s", item.type)
            elif method == "item/completed":
                item = payload.item.root
                if item.type == "agentMessage" and item.id not in streamed and item.text:
                    # 网关不支持流式时这里补上整段
                    sink.emit({"type": "text_start"})
                    sink.emit({"type": "text", "delta": item.text})
                elif item.type == "mcpToolCall":
                    text, is_error = _tool_result(item)
                    sink.emit(
                        {"type": "tool_result", "id": item.id, "is_error": is_error,
                         "output": text[:TOOL_OUTPUT_UI_LIMIT]}
                    )
                    sink.flush()
                    tool_calls += 1
                    if item.tool in ending and not is_error:
                        # 例如 ask_user：问题已经交给用户，这一轮到此为止
                        self._ended_by_tool = True
                        await self._stop(handle)
                    elif tool_calls >= self._settings.agent_max_turns:
                        self._over_limit = True
                        await self._stop(handle)
            elif method == "thread/tokenUsage/updated":
                last = payload.token_usage.last
                usage["input_tokens"] += last.input_tokens
                usage["output_tokens"] += last.output_tokens
                usage["cache_read_tokens"] += last.cached_input_tokens
                usage["reasoning_tokens"] += last.reasoning_output_tokens
            elif method == "error":
                if not payload.will_retry:
                    outcome.error = _turn_error(payload.error)
                else:
                    logger.info("codex retrying: %s", payload.error.message)
            elif method == "turn/completed":
                result = payload.turn
                outcome.usage = {**usage, "duration_ms": result.duration_ms, "num_turns": tool_calls + 1,
                                 "cost_usd": None}
                status = result.status.value
                if status == "failed" and not self._interrupted:
                    outcome.status = "error"
                    outcome.error = _turn_error(result.error) if result.error else (outcome.error or "模型调用失败")
                elif status == "interrupted" and self._over_limit:
                    outcome.status = "error"
                    outcome.error = f"已达到单轮最多 {self._settings.agent_max_turns} 次工具调用的上限，请缩小问题范围后继续"

    async def _stop(self, handle: AsyncTurnHandle) -> None:
        try:
            await handle.interrupt()
        except CodexError:
            logger.warning("interrupt failed", exc_info=True)

    async def interrupt(self) -> None:
        self._interrupted = True
        if self._turn is not None:
            await self._stop(self._turn)


def _tool_result(item: Any) -> tuple[str, bool]:
    if item.error is not None:
        return item.error.message, True
    content = item.result.content if item.result is not None else []
    text = "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return text, item.status.value == "failed"


def _turn_error(error: Any) -> str:
    info = error.codex_error_info.model_dump_json(by_alias=True) if error.codex_error_info else ""
    match = _HTTP_STATUS.search(info)
    status = int(match.group(1)) if match else _ERROR_STATUS.get(info.strip('"'))
    detail = error.message + (f"（{error.additional_details}）" if error.additional_details else "")
    return friendly_error(detail, status)
