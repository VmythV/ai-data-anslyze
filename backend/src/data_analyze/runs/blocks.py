"""UI 事件 → 消息块。前端 `frontend/src/lib/blocks.ts` 用同一套规则实时渲染，这里用来落库。

事件（引擎与工具发出）：
  text_start | text{delta} | tool_call{id,name,input} | tool_result{id,is_error,output}
  table{title,sql,columns,rows,total_rows,truncated,file} | chart{artifact_id,title,option} | kpis{title,items}
  report{artifact_id,title} | question{id,questions} | relation{left,right,match_rate,status}
  error{message} | status{message}
  以及运行级别的 run_started / done（不进消息块）
块：
  text{text} | tool{id,name,input,status: running|done|error|interrupted,output}
  table{...} | chart{...} | report{...} | question{id,questions} | error{message}
"""

from __future__ import annotations

from typing import Any

_PAYLOAD_KINDS = {"table", "chart", "kpis", "report", "question", "relation", "error"}


def apply_event(blocks: list[dict[str, Any]], event: dict[str, Any]) -> None:
    kind = event.get("type")
    if kind == "text_start":
        if not blocks or blocks[-1]["kind"] != "text" or blocks[-1]["text"]:
            blocks.append({"kind": "text", "text": ""})
    elif kind == "text":
        if not blocks or blocks[-1]["kind"] != "text":
            blocks.append({"kind": "text", "text": ""})
        blocks[-1]["text"] += event.get("delta", "")
    elif kind == "tool_call":
        blocks.append(
            {"kind": "tool", "id": event["id"], "name": event["name"], "input": event.get("input") or {},
             "status": "running", "output": ""}
        )
    elif kind == "tool_result":
        for block in reversed(blocks):
            if block["kind"] == "tool" and block["id"] == event["id"]:
                block["status"] = "error" if event.get("is_error") else "done"
                block["output"] = event.get("output", "")
                break
    elif kind in _PAYLOAD_KINDS:
        blocks.append({"kind": kind, **{k: v for k, v in event.items() if k != "type"}})


def finalize(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for block in blocks:
        if block["kind"] == "text" and not block["text"].strip():
            continue
        if block["kind"] == "tool" and block["status"] == "running":
            block = {**block, "status": "interrupted"}
        out.append(block)
    return out
