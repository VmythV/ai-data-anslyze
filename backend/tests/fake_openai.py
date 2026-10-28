"""按剧本应答的假 OpenAI Responses 接口：不需要真实模型就能把 Codex 引擎的整条链路跑通。

剧本复用 fake_anthropic.py：把 Responses 请求转成 Anthropic 形状交给它，再把回答转回 Responses 的 SSE。
用法：uv run python tests/fake_openai.py 18081
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

import uvicorn
from fake_anthropic import _script
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()


def _tool_names(body: dict[str, Any]) -> dict[str, tuple[str | None, str]]:
    """{剧本里的 mcp__analysis__xxx: (命名空间, 工具名)}；Codex 把 MCP 工具放在 mcp__analysis 命名空间下"""
    names: dict[str, tuple[str | None, str]] = {}
    for tool in body.get("tools", []):
        if tool.get("type") == "function":
            names[tool["name"]] = (None, tool["name"])
        elif tool.get("type") == "namespace":
            for inner in tool.get("tools", []):
                names[f"{tool['name']}__{inner['name']}"] = (tool["name"], inner["name"])
    return names


def _output_text(output: Any) -> str:
    if isinstance(output, list):
        return "".join(str(part.get("text", "")) for part in output if isinstance(part, dict))
    return str(output)


def _as_anthropic(body: dict[str, Any], names: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    items = body.get("input", [])
    if isinstance(items, str):
        items = [{"type": "message", "role": "user", "content": [{"text": items}]}]
    for item in items:
        kind = item.get("type", "message")
        if kind == "message" and item.get("role") == "user":
            # Codex 注入的 <environment_context> 之类不算用户消息：剧本按第一条用户消息选场景
            texts = [c.get("text", "") for c in item.get("content", [])
                     if isinstance(c, dict) and not c.get("text", "").lstrip().startswith("<")]
            if not texts:
                continue
            messages.append({"role": "user", "content": [{"type": "text", "text": t} for t in texts]})
        elif kind == "function_call_output":
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": item.get("call_id"), "content": _output_text(item.get("output"))}
            ]})
        elif kind == "function_call":
            messages.append({"role": "assistant", "content": [{"type": "tool_use", "name": item.get("name")}]})
    return {"tools": [{"name": n} for n in names], "messages": messages}


def _sse(event: dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _stream(blocks: list[dict[str, Any]], names: dict[str, Any]):  # noqa: ANN202
    pace = 0.05 if float(os.environ.get("FAKE_DELAY", "0")) else 0
    rid = f"resp_{uuid.uuid4().hex}"
    yield _sse({"type": "response.created", "response": {"id": rid}})
    for index, block in enumerate(blocks):
        if block["type"] == "text":
            mid = f"msg_{uuid.uuid4().hex}"
            item = {"type": "message", "id": mid, "role": "assistant", "content": []}
            yield _sse({"type": "response.output_item.added", "output_index": index, "item": item})
            for piece in re.findall(r".{1,6}", block["text"], re.S):
                await asyncio.sleep(pace)
                yield _sse({"type": "response.output_text.delta", "item_id": mid, "output_index": index,
                            "content_index": 0, "delta": piece})
            done = {**item, "content": [{"type": "output_text", "text": block["text"]}]}
            yield _sse({"type": "response.output_item.done", "output_index": index, "item": done})
        else:
            namespace, name = names.get(block["name"], (None, block["name"]))
            item = {"type": "function_call", "id": f"fc_{uuid.uuid4().hex}", "call_id": f"call_{uuid.uuid4().hex[:24]}",
                    "name": name, "arguments": json.dumps(block["input"], ensure_ascii=False)}
            if namespace:
                item["namespace"] = namespace
            yield _sse({"type": "response.output_item.added", "output_index": index, "item": item})
            yield _sse({"type": "response.output_item.done", "output_index": index, "item": item})
    yield _sse({"type": "response.completed", "response": {"id": rid, "usage": {
        "input_tokens": 100, "input_tokens_details": {"cached_tokens": 40}, "output_tokens": 20,
        "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 120}}})


@app.post("/v1/responses")
async def responses(request: Request):  # noqa: ANN201
    body = await request.json()
    if request.headers.get("authorization") == "Bearer sk-bad":
        return JSONResponse({"error": {"message": "Incorrect API key provided", "type": "invalid_request_error",
                                       "code": "invalid_api_key"}}, status_code=401)
    names = _tool_names(body)
    if body.get("tools"):
        Path("var").mkdir(exist_ok=True)
        Path("var/fake_openai_last_request.json").write_text(
            json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        await asyncio.sleep(float(os.environ.get("FAKE_DELAY", "0")))
    blocks = _script(_as_anthropic(body, names))
    if not body.get("stream"):
        text = "".join(b.get("text", "") for b in blocks if b["type"] == "text")
        return JSONResponse({"id": f"resp_{uuid.uuid4().hex}", "status": "completed", "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]})
    return StreamingResponse(_stream(blocks, names), media_type="text/event-stream")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18081
    uvicorn.run(app, host=os.environ.get("FAKE_HOST", "127.0.0.1"), port=port, log_level="warning")
