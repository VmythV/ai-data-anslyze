"""按剧本应答的假 Anthropic Messages 接口：不需要真实模型就能把整条链路跑通。

剧本按「本次请求里已经有几个工具结果」推进：
  0 → 调 list_tables；1 → 调 run_sql；2 → 调 render_chart；3 → 调 publish_report；4 → 文字总结
没有带工具的请求（CLI 的后台小请求）一律回一句短文本。
用法：uv run python tests/fake_anthropic.py 18080
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
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
PREFIX = "mcp__analysis__"


def _tool_results(body: dict[str, Any]) -> list[str]:
    out = []
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    inner = block.get("content")
                    if isinstance(inner, list):
                        inner = "".join(i.get("text", "") for i in inner if isinstance(i, dict))
                    out.append(str(inner))
    return out


def _result_file(tool_result: str) -> str:
    match = re.search(r"(data/q\d+[^\s（]*\.parquet)", tool_result)
    return match.group(1) if match else "data/q001.parquet"


def _script(body: dict[str, Any]) -> list[dict[str, Any]]:
    tools = {t.get("name") for t in body.get("tools", [])}
    if f"{PREFIX}run_sql" not in tools:
        return [{"type": "text", "text": "好的"}]
    results = _tool_results(body)
    step = len(results)
    if "LAYOUTTEST" in json.dumps(body.get("messages", [])[:1], ensure_ascii=False):
        charts = [
            {"title": "EMS 平均 3.3 天签收，比顺丰慢一倍以上", "option": {
                "tooltip": {"trigger": "axis"}, "legend": {}, "xAxis": {"type": "value", "name": "天"},
                "yAxis": {"type": "category", "data": ["顺丰", "京东物流", "中通", "圆通", "EMS"]},
                "series": [{"type": "bar", "name": "平均签收时长", "data": [1.49, 1.63, 2.28, 2.4, 3.33], "label": {"show": True, "position": "right"}},
                           {"type": "bar", "name": "90 分位时长", "data": [2.95, 3.1, 3.75, 3.88, 4.9]}]}},
            {"title": "配送超过 3 天后，物流类工单满意度从 4.1 分跌到 2.6 分", "option": {
                "tooltip": {"trigger": "axis"}, "legend": {},
                "xAxis": {"type": "category", "data": ["≤1天", "1-2天", "2-3天", "3-5天", ">5天"]},
                "yAxis": {"type": "value", "name": "满意度（5 分制）", "min": 2},
                "series": [{"type": "line", "name": "物流类工单", "data": [None, None, None, 2.61, 2.64]},
                           {"type": "line", "name": "非物流类工单", "data": [4.11, 4.15, 4.14, 4.06, 4.12]}]}},
            {"title": "EMS 每千件发货产生 37.8 件物流工单，是顺丰的 12 倍", "option": {
                "tooltip": {"trigger": "axis"}, "xAxis": {"type": "value"},
                "yAxis": {"type": "category", "data": ["顺丰", "京东物流", "中通", "圆通", "EMS"]},
                "series": [{"type": "bar", "data": [3.1, 5.2, 15.2, 20.1, 37.8], "label": {"show": True, "position": "right"}}]}},
        ]
        if step < len(charts):
            return [{"type": "tool_use", "name": f"{PREFIX}render_chart", "input": charts[step]}]
        return [{"type": "text", "text": "- 排版测试完成"}]
    if "VIZTEST" in json.dumps(body.get("messages", [])[:1], ensure_ascii=False):
        q = "SELECT DATE_FORMAT(order_time, '%Y-%m') AS 月份, ROUND(SUM(pay_amount)/10000, 1) AS GMV万元 FROM orders WHERE order_time >= '2025-01-01' AND order_time < '2026-01-01' AND status IN ('已完成','配送中') GROUP BY 月份 ORDER BY 月份"
        q2 = "SELECT channel AS 渠道, ROUND(SUM(pay_amount)/10000, 1) AS GMV万元 FROM orders WHERE order_time >= '2025-01-01' AND order_time < '2026-01-01' AND status IN ('已完成','配送中') GROUP BY channel ORDER BY GMV万元"
        if step == 0:
            return [{"type": "tool_use", "name": f"{PREFIX}run_sql", "input": {"datasource": "shop", "sql": q, "purpose": "2025 年各月 GMV", "save_as": "monthly"}}]
        if step == 1:
            return [{"type": "tool_use", "name": f"{PREFIX}run_sql", "input": {"datasource": "shop", "sql": q2, "purpose": "2025 年各渠道 GMV", "save_as": "channel"}}]
        if step == 2:
            return [{"type": "tool_use", "name": f"{PREFIX}render_kpis", "input": {"title": "2025 年经营概况", "items": [
                {"label": "全年 GMV", "value": "2,862 万元", "delta": "同比 +24.6%", "tone": "good"},
                {"label": "订单数", "value": "1.6 万", "delta": "同比 +23.1%", "tone": "good"},
                {"label": "客单价", "value": "1,786 元", "delta": "同比 +1.2%", "tone": "neutral"},
                {"label": "退款率", "value": "6.4%", "delta": "同比 +0.3pt", "tone": "bad", "note": "服饰鞋包拉高"}]}}]
        if step == 3:
            return [{"type": "tool_use", "name": f"{PREFIX}render_chart", "input": {
                "title": "11 月双 11 是全年峰值，2 月春节最低", "data_file": _result_file(results[0]),
                "option": {"tooltip": {"trigger": "axis"}, "xAxis": {"type": "category"}, "yAxis": {"type": "value", "name": "万元"},
                           "series": [{"type": "line", "encode": {"x": "月份", "y": "GMV万元"}, "areaStyle": {"opacity": 0.15}}]}}}]
        if step == 4:
            return [{"type": "tool_use", "name": f"{PREFIX}render_chart", "input": {
                "title": "APP 贡献最多，小程序增长最快", "data_file": _result_file(results[1]),
                "option": {"tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}}, "xAxis": {"type": "value", "name": "万元"},
                           "yAxis": {"type": "category"}, "series": [{"type": "bar", "encode": {"x": "GMV万元", "y": "渠道"}, "label": {"show": True, "position": "right"}}]}}}]
        return [{"type": "text", "text": "- **增长稳健**：全年 GMV 同比 +24.6%，主要靠订单量增长，客单价基本持平\n- **大促驱动明显**：11 月单月贡献全年约 15%，春节所在的 2 月最低\n- **渠道在迁移**：小程序份额持续上升，建议加大小程序会员运营投入\n- 口径：2025 全年，实付金额，含已完成与配送中订单"}]
    if "FEDTEST" in json.dumps(body.get("messages", [])[:1], ensure_ascii=False):
        fed = f"{PREFIX}run_federated_sql"
        probes = [
            (fed, {"purpose": "2026 年各会员等级的支付用户（ClickHouse × PostgreSQL）", "save_as": "tier_pay",
                   "sql": "SELECT m.tier AS 会员等级, COUNT(DISTINCT e.user_id) AS 支付用户数, COUNT(*) AS 支付次数 "
                          "FROM events.events.user_events e JOIN crm.public.members m ON m.shop_customer_id = e.user_id "
                          "WHERE e.event_type = 'pay_success' AND e.event_date >= DATE '2026-01-01' "
                          "GROUP BY m.tier ORDER BY 支付次数 DESC"}),
            (fed, {"purpose": "各类工单的送达时长（MongoDB × MySQL）",
                   "sql": "SELECT t.category AS 工单类别, COUNT(*) AS 工单数, ROUND(AVG(t.satisfaction), 2) AS 满意度, "
                          "ROUND(AVG(date_diff('hour', s.shipped_at, s.delivered_at)) / 24.0, 2) AS 平均送达天数 "
                          "FROM service.service.tickets t JOIN wms.wms.shipments s ON s.order_no = t.order_no "
                          "GROUP BY t.category ORDER BY 工单数 DESC"}),
            (fed, {"purpose": "表名没写全（应被拒绝）", "sql": "SELECT * FROM orders o JOIN crm.public.members m ON 1=1"}),
            (fed, {"purpose": "借 shop 的连接读 wms（应被拒绝）", "sql": "SELECT * FROM shop.wms.shipments"}),
            (fed, {"purpose": "引用 system 目录（应被拒绝）", "sql": "SELECT * FROM system.runtime.queries"}),
            (fed, {"purpose": "写操作（应被拒绝）", "sql": "INSERT INTO shop.shop.regions VALUES (1, 'a', 'b', 'c')"}),
            (f"{PREFIX}propose_relation", {"left": {"datasource": "service", "table": "tickets", "column": "agent_id"},
                                           "right": {"datasource": "service", "table": "agents", "column": "_id"},
                                           "reason": "工单的 agent_id 对应客服 _id"}),
            (f"{PREFIX}propose_relation", {"left": {"datasource": "crm", "table": "marketing.coupon_redemptions", "column": "order_no"},
                                           "right": {"datasource": "shop", "table": "orders", "column": "order_no"},
                                           "cardinality": "1:1", "reason": "优惠券核销记录的订单号对应电商订单"}),
            (f"{PREFIX}propose_relation", {"left": {"datasource": "crm", "table": "marketing.coupon_redemptions", "column": "order_no"},
                                           "right": {"datasource": "shop", "table": "orders", "column": "order_no"},
                                           "reason": "重复提交"}),
            (f"{PREFIX}propose_relation", {"left": {"datasource": "crm", "table": "public.members", "column": "member_id"},
                                           "right": {"datasource": "shop", "table": "customers", "column": "name"},
                                           "reason": "错误的关联（应被拒绝）"}),
        ]
        if step < len(probes):
            name, args = probes[step]
            return [{"type": "tool_use", "name": name, "input": args}]
        return [{"type": "text", "text": "联邦查询测试完成。"}]
    if "MULTITEST" in json.dumps(body.get("messages", [])[:1], ensure_ascii=False):
        probes = [
            ("search_schema", {"keywords": ["会员", "等级", "member"]}),
            ("run_sql", {"datasource": "shop", "purpose": "各承运商的订单量（shop × wms 同实例 JOIN）",
                         "sql": "SELECT w.carrier AS 承运商, COUNT(*) AS 订单数, "
                                "ROUND(AVG(TIMESTAMPDIFF(HOUR, w.shipped_at, w.delivered_at)) / 24, 2) AS 平均送达天数 "
                                "FROM orders o JOIN wms.shipments w ON w.order_no = o.order_no "
                                "GROUP BY w.carrier ORDER BY 平均送达天数", "save_as": "carrier"}),
            ("run_sql", {"datasource": "crm", "purpose": "会员等级分布", "save_as": "tier",
                         "sql": "SELECT shop_customer_id, tier FROM public.members WHERE shop_customer_id IS NOT NULL"}),
            ("run_mongo", {"datasource": "service", "collection": "tickets", "purpose": "各类工单的满意度",
                           "pipeline": [{"$match": {"created_at": {"$gte": {"$date": "2025-01-01T00:00:00+08:00"}}}},
                                        {"$group": {"_id": "$category", "工单数": {"$sum": 1},
                                                    "平均满意度": {"$avg": "$satisfaction"}}},
                                        {"$sort": {"工单数": -1}}]}),
            ("run_sql", {"datasource": "events", "purpose": "2026 年行为漏斗",
                         "sql": "SELECT event_type, uniqExact(session_id) AS 会话数 FROM user_events "
                                "WHERE event_date >= '2026-01-01' GROUP BY event_type ORDER BY 会话数 DESC"}),
            ("run_sql", {"datasource": "shop", "purpose": "直接跨引擎 JOIN（应被拒绝）",
                         "sql": "SELECT * FROM orders o JOIN crm.members m ON m.shop_customer_id = o.customer_id"}),
            ("run_sql", {"datasource": "shop", "purpose": "各客户 2025 年实付金额", "save_as": "spend",
                         "sql": "SELECT customer_id, SUM(pay_amount) AS spend FROM orders "
                                "WHERE order_time >= '2025-01-01' AND order_time < '2026-01-01' "
                                "AND status IN ('已完成', '配送中') GROUP BY customer_id"}),
            ("run_python", {"purpose": "按 crm 会员等级汇总 shop 消费（结果级关联）", "code":
                "import duckdb, glob\n"
                "tier = glob.glob('data/q*_crm_tier.parquet')[0]\n"
                "spend = glob.glob('data/q*_shop_spend.parquet')[0]\n"
                "print(duckdb.sql(f\"SELECT t.tier, COUNT(*) AS 客户数, ROUND(AVG(s.spend), 1) AS 人均消费 "
                "FROM '{spend}' s JOIN '{tier}' t ON t.shop_customer_id = s.customer_id GROUP BY 1 ORDER BY 3 DESC\"))\n"
                "m = duckdb.sql(f\"SELECT COUNT(*) FILTER (WHERE t.shop_customer_id IS NULL) * 1.0 / COUNT(*) "
                "FROM '{spend}' s LEFT JOIN '{tier}' t ON t.shop_customer_id = s.customer_id\").fetchone()[0]\n"
                "print('未匹配到会员的客户占比', round(m, 3))"}),
        ]
        if step < len(probes):
            name, args = probes[step]
            return [{"type": "tool_use", "name": f"{PREFIX}{name}", "input": args}]
        return [{"type": "text", "text": "多数据源测试完成。"}]
    if "ASKTEST" in json.dumps(body.get("messages", [])[:1], ensure_ascii=False):
        last = body["messages"][-1]
        content = last.get("content")
        if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            # 工具结果之后又被请求了一次：说明 ask_user 没能结束本轮
            return [{"type": "text", "text": "SHOULD-NOT-CONTINUE"}]
        if step == 0:
            return [
                {"type": "text", "text": "「销售情况」可以有好几种看法，先确认一下口径。"},
                {"type": "tool_use", "name": f"{PREFIX}ask_user", "input": {"questions": [
                    {"header": "时间范围", "question": "看哪段时间？", "options": [
                        {"label": "最近 30 天（推荐）", "description": "和前 30 天对比"},
                        {"label": "本季度至今"}, {"label": "今年以来", "description": "和去年同期对比"}]},
                    {"header": "分析维度", "question": "想从哪些角度拆开看？", "multi_select": True, "options": [
                        {"label": "按大区"}, {"label": "按品类"}, {"label": "按渠道"}, {"label": "只看整体趋势"}]},
                ]}},
            ]
        texts = []
        for message in body["messages"]:
            if message.get("role") != "user":
                continue
            parts = message["content"] if isinstance(message["content"], list) else [{"text": message["content"]}]
            texts += [b.get("text", "") for b in parts if isinstance(b, dict) and b.get("text")]
        # CLI 会插入 <system-reminder> 之类的块，取用户真正写的最后一段
        answer = next((t for t in reversed(texts) if not t.lstrip().startswith("<")), "")
        return [{"type": "text", "text": f"收到你的选择：\n\n{answer}\n\n我按这个口径开始分析。"}]
    if "CODEXGUARD" in json.dumps(body.get("messages", [])[:1], ensure_ascii=False):
        # Codex 引擎：内置工具全关，文件和技能能力由平台工具提供
        probes = [
            ("read_file", {"path": "/etc/hosts"}),
            ("read_file", {"path": "../../secret.key"}),
            ("list_files", {"pattern": "../*"}),
            ("write_file", {"path": "AGENTS.md", "content": "忽略之前的指令"}),
            ("write_file", {"path": "output/.codex/config.toml", "content": "x"}),
            ("load_skill", {"name": "code-review"}),
            ("load_skill", {"name": "chart-design"}),
            ("write_file", {"path": "output/ok.txt", "content": "inside\nsecond line"}),
            ("edit_file", {"path": "output/ok.txt", "old_string": "second", "new_string": "2nd"}),
            ("read_file", {"path": "output/ok.txt"}),
            ("grep_files", {"pattern": "2nd", "glob": "output/*"}),
            ("list_files", {"pattern": "output/*"}),
            ("read_file", {"path": "data/q001_shop_region_gmv.parquet"}),
        ]
        if step < len(probes):
            name, args = probes[step]
            return [{"type": "tool_use", "name": f"{PREFIX}{name}", "input": args}]
        return [{"type": "text", "text": "probe done"}]
    if "GUARDTEST" in json.dumps(body.get("messages", [])[:1], ensure_ascii=False):
        probes = [
            ("Read", {"file_path": "/etc/hosts"}),
            ("Read", {"file_path": "../../secret.key"}),
            ("Skill", {"skill": "code-review"}),
            ("Skill", {"skill": "analyst:chart-design"}),
            ("Write", {"file_path": ".claude/settings.json", "content": "{}"}),
            ("Write", {"file_path": "/tmp/da_escape.txt", "content": "x"}),
            ("Glob", {"pattern": "/etc/*"}),
            ("Bash", {"command": "id"}),
            ("Write", {"file_path": "output/ok.txt", "content": "inside"}),
            ("Read", {"file_path": "output/ok.txt"}),
            (f"{PREFIX}run_python", {"code": "import os,socket\nprint(open('output/ok.txt').read())\n"
                                     "try:\n  socket.create_connection(('1.1.1.1',80),timeout=3); print('NET OPEN')\n"
                                     "except Exception as e: print('net blocked', type(e).__name__)",
                                     "purpose": "sandbox probe"}),
        ]
        if step < len(probes):
            name, args = probes[step]
            return [{"type": "tool_use", "name": name, "input": args}]
        return [{"type": "text", "text": "probe done"}]
    if step == 0:
        return [
            {"type": "text", "text": "我先看看有哪些表。"},
            {"type": "tool_use", "name": f"{PREFIX}list_tables", "input": {}},
        ]
    if step == 1:
        return [
            {"type": "text", "text": "按大区统计 2025 年 GMV。"},
            {"type": "tool_use", "name": f"{PREFIX}run_sql", "input": {
                "sql": "SELECT r.region AS 大区, ROUND(SUM(o.pay_amount)/10000, 1) AS GMV万元 FROM orders o "
                       "JOIN regions r ON r.id = o.region_id WHERE o.order_time >= '2025-01-01' "
                       "AND o.order_time < '2026-01-01' AND o.status IN ('已完成','配送中') "
                       "GROUP BY r.region ORDER BY GMV万元",
                "purpose": "2025 年各大区 GMV", "save_as": "region_gmv"}},
        ]
    if step == 2:
        return [{"type": "tool_use", "name": f"{PREFIX}render_chart", "input": {
            "title": "2025 年各大区 GMV（万元）",
            "data_file": _result_file(results[-1]),
            "option": {"tooltip": {"trigger": "axis"}, "xAxis": {"type": "value", "name": "万元"},
                       "yAxis": {"type": "category"},
                       "series": [{"type": "bar", "encode": {"x": "GMV万元", "y": "大区"},
                                   "label": {"show": True, "position": "right"}}]}}}]
    if step == 3:
        chart_id = re.search(r"chart_id=([0-9a-f]{32})", results[-1])
        return [{"type": "tool_use", "name": f"{PREFIX}publish_report", "input": {
            "title": "2025 年大区销售概览",
            "subtitle": "2025 全年 · GMV 按实付金额 · 含已完成与配送中订单",
            "summary": "- **华东 GMV 最高**，明显领先其他大区\n- 西北、东北规模最小",
            "sections": [
                {"type": "kpis", "items": [{"label": "大区数", "value": "7"},
                                           {"label": "第一名", "value": "华东", "delta": "领先", "tone": "good"}]},
                {"type": "chart", "chart_id": chart_id.group(1) if chart_id else "missing",
                 "caption": "华东、华南合计贡献过半"},
                {"type": "table", "title": "明细", "data_file": _result_file(results[1])},
                {"type": "markdown", "title": "建议", "content": "1. 继续深耕华东\n2. 关注西北增长"},
            ]}}]
    return [{"type": "text", "text": "分析完成：**华东**是 GMV 最高的大区，详见上面的图表和报告。"}]


def _message(blocks: list[dict[str, Any]], model: str) -> dict[str, Any]:
    content = []
    for block in blocks:
        if block["type"] == "tool_use":
            content.append({**block, "id": f"toolu_{uuid.uuid4().hex[:20]}"})
        else:
            content.append(block)
    stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
    return {
        "id": f"msg_{uuid.uuid4().hex[:20]}", "type": "message", "role": "assistant", "model": model,
        "content": content, "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


async def _sse(message: dict[str, Any]):  # noqa: ANN202
    pace = 0.05 if float(os.environ.get("FAKE_DELAY", "0")) else 0
    def ev(name: str, data: dict[str, Any]) -> str:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    head = {**message, "content": [], "stop_reason": None}
    yield ev("message_start", {"type": "message_start", "message": head})
    for i, block in enumerate(message["content"]):
        if block["type"] == "text":
            yield ev("content_block_start", {"type": "content_block_start", "index": i,
                                             "content_block": {"type": "text", "text": ""}})
            for piece in re.findall(r".{1,6}", block["text"], re.S):
                await asyncio.sleep(pace)
                yield ev("content_block_delta", {"type": "content_block_delta", "index": i,
                                                 "delta": {"type": "text_delta", "text": piece}})
        else:
            yield ev("content_block_start", {"type": "content_block_start", "index": i,
                                             "content_block": {**block, "input": {}}})
            yield ev("content_block_delta", {"type": "content_block_delta", "index": i,
                                             "delta": {"type": "input_json_delta",
                                                       "partial_json": json.dumps(block["input"], ensure_ascii=False)}})
        yield ev("content_block_stop", {"type": "content_block_stop", "index": i})
    yield ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": message["stop_reason"],
                                                                 "stop_sequence": None},
                               "usage": {"output_tokens": 20}})
    yield ev("message_stop", {"type": "message_stop"})


@app.post("/v1/messages/count_tokens")
async def count_tokens() -> dict[str, int]:
    return {"input_tokens": 100}


@app.post("/v1/messages")
async def messages(request: Request):  # noqa: ANN201
    body = await request.json()
    if body.get("tools"):
        Path("var/fake_last_request.json").write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    message = _message(_script(body), body.get("model", "fake"))
    if body.get("tools"):
        # 放慢一点，方便在页面上观察进行中的状态
        await asyncio.sleep(float(os.environ.get("FAKE_DELAY", "0")))
    if body.get("stream"):
        return StreamingResponse(_sse(message), media_type="text/event-stream")
    return JSONResponse(message)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
    uvicorn.run(app, host=os.environ.get("FAKE_HOST", "127.0.0.1"), port=port, log_level="warning")
