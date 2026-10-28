"""端到端冒烟：建渠道、数据源、会话，发一条消息，打印事件流。
用法：uv run python tests/e2e_flow.py <base_url> <api_key> <model> "<问题>"
E2E_PROTOCOL=openai 时建 OpenAI 协议渠道（走 Codex 引擎），base_url 含 /v1
"""
import json
import os
import sys

import httpx

API = "http://127.0.0.1:8110/api"
base_url, key, model = sys.argv[1], sys.argv[2], sys.argv[3]
question = sys.argv[4] if len(sys.argv) > 4 else "2025 年哪个大区卖得最好？画个图并出一份报告"

with httpx.Client(base_url=API, timeout=600, trust_env=False) as c:
    ch = c.post("/channels", json={"name": "test", "protocol": os.environ.get("E2E_PROTOCOL", "anthropic"), "base_url": base_url,
                                   "api_key": key, "model": model}).json()
    print("channel test:", c.post(f"/channels/{ch['id']}/test").json())
    ds_body = {"name": "电商 mock", "alias": "shop", "kind": "mysql", "host": "127.0.0.1", "port": 13306,
               "username": "analyst", "password": "analyst_ro_2026", "database": "shop"}
    print("datasource test:", c.post("/datasources/test", json=ds_body).json())
    existing = {d["alias"]: d for d in c.get("/datasources").json()}
    ds = existing.get("shop") or c.post("/datasources", json=ds_body).json()
    conv = c.post("/conversations", json={"channel_id": ch["id"], "datasource_ids": [ds["id"]]}).json()
    print("conversation", conv["id"])
    print(c.post(f"/conversations/{conv['id']}/messages", json={"content": question}).json())
    text = ""
    with c.stream("GET", f"/conversations/{conv['id']}/events") as r:
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:])
            t = ev["type"]
            if t == "text":
                text += ev["delta"]
                continue
            if text:
                print("  TEXT:", text.strip()[:300])
                text = ""
            if t == "tool_call":
                print("  CALL:", ev["name"], json.dumps(ev["input"], ensure_ascii=False)[:160])
            elif t == "tool_result":
                print("  RESULT:", "ERR" if ev["is_error"] else "ok", ev["output"][:160].replace("\n", " / "))
            elif t in ("table", "chart", "report"):
                print("  BLOCK:", t, ev.get("title"), ev.get("artifact_id", ""), len(ev.get("rows", [])))
            elif t != "text_start":
                print("  EVENT:", json.dumps(ev, ensure_ascii=False)[:300])
    detail = c.get(f"/conversations/{conv['id']}").json()
    print("persisted:", [(m["role"], m["status"], [b["kind"] for b in m["blocks"]]) for m in detail["messages"]])
