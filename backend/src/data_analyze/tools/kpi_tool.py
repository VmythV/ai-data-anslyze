"""指标卡：把关键数字以大号数字 + 涨跌的卡片展示给用户，代替在回答里用文字罗列数字。"""

from __future__ import annotations

from typing import Any

from data_analyze.tools.base import ToolContext, ToolInputError, ToolOutput, ToolSpec

MAX_ITEMS = 6
_TONES = {"good", "bad", "neutral"}


def kpi_tools(ctx: ToolContext) -> list[ToolSpec]:
    async def render_kpis(args: dict[str, Any]) -> ToolOutput:
        items = args.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
            raise ToolInputError(f"items 需要 1~{MAX_ITEMS} 个指标")
        cleaned = []
        for item in items:
            if not isinstance(item, dict) or not str(item.get("label") or "").strip() or item.get("value") in (None, ""):
                raise ToolInputError("每个指标都需要 label 和 value")
            cleaned.append({
                "label": str(item["label"]).strip()[:24],
                "value": str(item["value"]).strip()[:24],
                "delta": str(item.get("delta") or "").strip()[:32],
                "tone": item.get("tone") if item.get("tone") in _TONES else "neutral",
                "note": str(item.get("note") or "").strip()[:60],
            })
        title = str(args.get("title") or "").strip()[:40]
        ctx.emit({"type": "kpis", "title": title, "items": cleaned})
        return ToolOutput(f"已展示 {len(cleaned)} 个指标卡。回答里不用再重复这些数字。")

    return [
        ToolSpec(
            name="render_kpis",
            description=(
                "把 1~6 个关键数字以指标卡展示给用户（大号数字 + 变化 + 一句备注），比在文字里罗列数字直观得多。"
                "适合：总量、同比/环比、占比、转化率、匹配率等结论性数字。"
                "value 写成易读的形式（「3,120 万元」「18.2%」「4.78 天」）；delta 写变化（「同比 +24.8%」）；"
                "tone 表示好坏：good / bad / neutral（增长不一定是好事，如退款率上升是 bad）。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "可选，这组指标的标题，如「2025 年经营概况」"},
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_ITEMS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "指标名，如「GMV」"},
                                "value": {"type": "string", "description": "数值，带单位"},
                                "delta": {"type": "string", "description": "可选，变化，如「同比 +24.8%」"},
                                "tone": {"type": "string", "enum": ["good", "bad", "neutral"]},
                                "note": {"type": "string", "description": "可选，一句话补充，如「双 11 贡献 15%」"},
                            },
                            "required": ["label", "value"],
                        },
                    },
                },
                "required": ["items"],
                "additionalProperties": False,
            },
            handler=render_kpis,
        )
    ]
