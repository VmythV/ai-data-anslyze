"""进程内 MCP 网关：把一轮对话的平台工具以 Streamable HTTP MCP 暴露给 Codex 子进程。

工具 handler 仍在本进程的事件循环里执行（能直接用 ToolContext、往 EventSink 推 UI 块）。
每轮生成一个随机令牌，Codex 用 Bearer 头带上；令牌只在这一轮运行期间有效，对应这一轮的工具集。
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import mcp.types as mcp_types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.types import Receive, Scope, Send

from data_analyze.tools.base import ToolSpec, call_tool

MOUNT_PATH = "/internal/mcp"
SERVER_NAME = "analysis"
#: 工具参数里可能有整段报告 / 脚本
MAX_REQUEST_BYTES = 8 * 1024 * 1024


def _server(tools: list[ToolSpec]) -> Server[Any]:
    by_name = {spec.name: spec for spec in tools}
    listing = mcp_types.ListToolsResult(
        tools=[
            mcp_types.Tool(name=spec.name, description=spec.description, input_schema=spec.input_schema)
            for spec in tools
        ]
    )

    async def list_tools(_ctx: Any, _params: Any) -> mcp_types.ListToolsResult:
        return listing

    async def call(_ctx: Any, params: mcp_types.CallToolRequestParams) -> mcp_types.CallToolResult:
        spec = by_name.get(params.name)
        if spec is None:
            text, is_error = f"工具 {params.name} 在本平台不可用", True
        else:
            output = await call_tool(spec, params.arguments)
            text, is_error = output.text, output.is_error
        return mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text=text)], is_error=is_error)

    return Server(SERVER_NAME, version="1.0.0", on_list_tools=list_tools, on_call_tool=call)


class McpGateway:
    """挂在 FastAPI 上的 ASGI 应用；按请求里的令牌分发到对应那一轮的 MCP 服务。"""

    def __init__(self) -> None:
        self._routes: dict[str, StreamableHTTPSessionManager] = {}

    @asynccontextmanager
    async def serve(self, tools: list[ToolSpec]) -> AsyncIterator[str]:
        """在 with 块内提供这组工具，产出访问令牌。"""
        manager = StreamableHTTPSessionManager(
            _server(tools), json_response=True, stateless=True, max_request_body_size=MAX_REQUEST_BYTES
        )
        token = secrets.token_urlsafe(32)
        try:
            async with manager.run():
                self._routes[token] = manager
                try:
                    yield token
                finally:
                    del self._routes[token]
        except BaseExceptionGroup as group:
            # 任务组会把 with 块里的异常包一层；拆开原样抛出，调用方才能按类型处理
            if len(group.exceptions) == 1:
                inner = group.exceptions[0]
                raise inner from inner.__cause__
            raise

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")
        token = auth.removeprefix("Bearer ").strip()
        manager = self._routes.get(token) if token else None
        if manager is None:
            await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await manager.handle_request(scope, receive, send)


#: 全进程共用一个网关：app 挂载它，Codex 引擎往里注册每轮的工具
GATEWAY = McpGateway()
