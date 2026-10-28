"""报告工具：把分析结论整理成一份单文件 HTML 报告。"""

from __future__ import annotations

from typing import Any

from data_analyze.report.renderer import ReportDocument, render_report
from data_analyze.tools.base import (
    ToolContext,
    ToolInputError,
    ToolOutput,
    ToolSpec,
    resolve_in_workspace,
)
from data_analyze.tools.tabular import read_table, to_jsonable

TABLE_MAX_ROWS = 200
_TONES = {"good", "bad", "neutral"}

_SECTION_SCHEMA = {
    "type": "object",
    "description": (
        "段落，按 type 区分："
        "markdown {type, title?, content}；"
        "kpis {type, items: [{label, value, delta?, tone?: good|bad|neutral, note?}]}；"
        "chart {type, chart_id, caption?, height?}（chart_id 来自 render_chart）；"
        "table {type, title?, data_file? | (columns + rows), max_rows?}"
    ),
    "properties": {
        "type": {"type": "string", "enum": ["markdown", "kpis", "chart", "table"]},
        "title": {"type": "string"},
        "content": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "value": {"type": "string"},
                    "delta": {"type": "string"},
                    "tone": {"type": "string", "enum": ["good", "bad", "neutral"]},
                    "note": {"type": "string"},
                },
                "required": ["label", "value"],
            },
        },
        "chart_id": {"type": "string"},
        "caption": {"type": "string"},
        "height": {"type": "integer"},
        "data_file": {"type": "string"},
        "columns": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": {"type": "array"}},
        "max_rows": {"type": "integer"},
    },
    "required": ["type"],
}


def report_tools(ctx: ToolContext) -> list[ToolSpec]:
    async def resolve_section(raw: dict[str, Any]) -> dict[str, Any]:
        kind = raw.get("type")
        if kind == "markdown":
            content = raw.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ToolInputError("markdown 段落缺少 content")
            return {"type": kind, "title": str(raw.get("title") or ""), "content": content}
        if kind == "kpis":
            items = raw.get("items")
            if not isinstance(items, list) or not items:
                raise ToolInputError("kpis 段落缺少 items")
            out = []
            for item in items[:8]:
                if not isinstance(item, dict) or not item.get("label") or item.get("value") in (None, ""):
                    raise ToolInputError("kpis 的每一项都需要 label 和 value")
                tone = item.get("tone") if item.get("tone") in _TONES else "neutral"
                out.append({k: str(item.get(k) or "") for k in ("label", "value", "delta", "note")} | {"tone": tone})
            return {"type": kind, "items": out}
        if kind == "chart":
            chart = await ctx.artifacts.get_chart(str(raw.get("chart_id") or ""))
            if chart is None:
                raise ToolInputError(f"找不到图表 {raw.get('chart_id')}：请使用本会话中 render_chart 返回的 chart_id")
            height = raw.get("height") if isinstance(raw.get("height"), int) else 360
            return {
                "type": kind,
                "title": chart.title,
                "caption": str(raw.get("caption") or ""),
                "height": min(max(height, 220), 720),
                "option": chart.spec["option"],
            }
        if kind == "table":
            limit = min(int(raw.get("max_rows") or 50), TABLE_MAX_ROWS)
            if raw.get("data_file"):
                columns, rows, total = read_table(resolve_in_workspace(ctx.workspace, str(raw["data_file"])), limit)
            else:
                columns, rows = raw.get("columns"), raw.get("rows")
                if not isinstance(columns, list) or not isinstance(rows, list):
                    raise ToolInputError("table 段落需要 data_file，或同时提供 columns 和 rows")
                total = len(rows)
                rows = [[to_jsonable(v) for v in r] for r in rows[:limit] if isinstance(r, list)]
            numeric = [all(isinstance(r[i], (int, float)) or r[i] is None for r in rows) if rows else False
                       for i in range(len(columns))]
            return {
                "type": kind,
                "title": str(raw.get("title") or ""),
                "columns": [str(c) for c in columns],
                "rows": [[_fmt(v) for v in r] for r in rows],
                "numeric": numeric,
                "total": total,
                "more": total > len(rows),
            }
        raise ToolInputError(f"未知的段落类型：{kind}")

    async def publish_report(args: dict[str, Any]) -> ToolOutput:
        title = str(args.get("title") or "").strip()
        if not title:
            raise ToolInputError("title 不能为空")
        raw_sections = args.get("sections") or []
        if not isinstance(raw_sections, list):
            raise ToolInputError("sections 必须是数组")
        sections = [await resolve_section(s) for s in raw_sections if isinstance(s, dict)]
        doc = ReportDocument(
            title=title,
            subtitle=str(args.get("subtitle") or ""),
            summary=str(args.get("summary") or ""),
            datasource="、".join(s.name for s in ctx.sources.values()) or "—",
            sections=sections,
        )
        report_id = await ctx.artifacts.save_report(title, render_report(doc))
        ctx.emit({"type": "report", "artifact_id": report_id, "title": title})
        return ToolOutput(f"报告「{title}」已生成并展示给用户（report_id={report_id}），用户可以预览和下载。")

    return [
        ToolSpec(
            name="publish_report",
            description=(
                "把分析成果整理成一份简洁美观的 HTML 报告（版式固定，你只提供内容）。"
                "适合在一个完整的分析结束、或用户要求出报告时调用。调用前先加载 report-writing skill。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "subtitle": {"type": "string", "description": "可选，分析范围，如「2024-01 至 2026-10，全渠道」"},
                    "summary": {"type": "string", "description": "核心结论（Markdown），3~5 条，每条带关键数字"},
                    "sections": {"type": "array", "items": _SECTION_SCHEMA},
                },
                "required": ["title", "summary", "sections"],
                "additionalProperties": False,
            },
            handler=publish_report,
        )
    ]


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:,.2f}" if abs(value) < 1e15 else str(value)
    # 整数不加千分位：年份、ID 加了反而难读
    return str(value)
