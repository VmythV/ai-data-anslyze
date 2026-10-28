"""在沙箱容器里执行 Python 分析脚本。

沙箱不能联网、不挂共享卷：每次把本会话工作区打包传过去，跑完把新增/修改的文件解回工作区。
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime
from typing import Any

import httpx

from data_analyze.tools.base import ToolContext, ToolInputError, ToolOutput, ToolSpec
from data_analyze.tools.bundle import BundleTooLargeError, pack_workspace, unpack_into_workspace

_STDOUT_LIMIT = 8_000
_STDERR_LIMIT = 4_000
#: 传给沙箱的工作区上限 / 接收产出文件的上限
MAX_BUNDLE_BYTES = 200 * 1024 * 1024
MAX_RESULT_BYTES = 128 * 1024 * 1024


def python_tools(ctx: ToolContext) -> list[ToolSpec]:
    async def run_python(args: dict[str, Any]) -> ToolOutput:
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ToolInputError("code 不能为空")
        timeout = ctx.settings.python_timeout_seconds
        try:
            bundle = await asyncio.to_thread(pack_workspace, ctx.workspace, MAX_BUNDLE_BYTES)
        except BundleTooLargeError as exc:
            return ToolOutput(str(exc), is_error=True)
        # 脚本在本地工作区留档，方便追溯
        script = ctx.workspace / "scripts" / f"run_{datetime.now():%Y%m%d_%H%M%S_%f}.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(code, encoding="utf-8")
        try:
            # 沙箱是内网服务，不走本机代理
            async with httpx.AsyncClient(timeout=timeout + 60, trust_env=False) as client:
                resp = await client.post(
                    f"{ctx.settings.sandbox_url.rstrip('/')}/run",
                    json={"code": code, "timeout": timeout, "files_tar": base64.b64encode(bundle).decode()},
                )
        except httpx.HTTPError as exc:
            return ToolOutput(f"Python 沙箱不可用：{exc!r}。请改用 SQL 完成分析。", is_error=True)
        if resp.status_code != 200:
            return ToolOutput(f"Python 沙箱返回错误 {resp.status_code}：{resp.text[:500]}", is_error=True)
        result = resp.json()
        written: list[str] = []
        if result.get("files_tar"):
            try:
                written = await asyncio.to_thread(
                    unpack_into_workspace, base64.b64decode(result["files_tar"]), ctx.workspace, MAX_RESULT_BYTES
                )
            except BundleTooLargeError as exc:
                return ToolOutput(f"{exc}，请只保存需要的结果", is_error=True)
        parts = [f"退出码 {result['exit_code']}，用时 {result['duration_ms']} ms"]
        if result.get("timed_out"):
            parts.append(f"脚本超时（>{timeout} 秒）被终止")
        if result.get("stdout"):
            parts.append("stdout:\n" + _tail(result["stdout"], _STDOUT_LIMIT))
        if result.get("stderr"):
            parts.append("stderr:\n" + _tail(result["stderr"], _STDERR_LIMIT))
        if written:
            parts.append("新增或修改的文件：" + "、".join(written[:30]))
        return ToolOutput("\n".join(parts), is_error=result["exit_code"] != 0)

    return [
        ToolSpec(
            name="run_python",
            description=(
                "在隔离沙箱中执行 Python 3.12 脚本，工作目录是本会话工作区（run_sql 的结果在 data/ 下）。"
                "已安装 pandas、numpy、scipy、statsmodels、pyarrow、duckdb；不能联网，不能连数据库。"
                "用于 SQL 不方便完成的计算：同比环比、占比、相关性、预测、异常检测、数据整形等。"
                "用 print 输出你需要看的结论性数字（不要打印大表）；要画图的数据写成 output/*.csv，"
                "再交给 render_chart 的 data_file。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "完整的 Python 脚本"},
                    "purpose": {"type": "string", "description": "一句话说明这段脚本做什么，展示给用户"},
                },
                "required": ["code", "purpose"],
                "additionalProperties": False,
            },
            handler=run_python,
            max_result_chars=20_000,
        )
    ]


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"…（前面省略 {len(text) - limit} 字符）\n" + text[-limit:]
