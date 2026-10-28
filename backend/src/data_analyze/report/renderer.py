"""把结构化的报告内容渲染成单文件 HTML（ECharts、主题、数据全部内联，离线可看）。

报告不让 AI 自由写 HTML：版式固定，AI 只决定内容。这样风格稳定，也不会混进脚本。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt

from data_analyze.report.theme import FONT, chart_themes
from data_analyze.tools.chart_tool import polish_option

_HERE = Path(__file__).parent
_env = Environment(
    loader=FileSystemLoader(_HERE / "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
# html=False：原始 HTML 一律转义
_md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table").enable("strikethrough")

_ARROWS = {"good": "▲", "bad": "▼", "neutral": "–"}


@lru_cache
def _echarts_js() -> str:
    # 内联脚本里出现 </script 会提前闭合标签
    return (_HERE / "static" / "echarts.min.js").read_text(encoding="utf-8").replace("</script", "<\\/script")


def _script_json(data: Any) -> str:
    """嵌进 <script type=application/json> 的 JSON：转义 `<`，防止数据里的 </script> 截断标签。"""
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def markdown_html(text: str) -> str:
    return _md.render(text or "")


@dataclass
class ReportDocument:
    title: str
    datasource: str
    subtitle: str = ""
    summary: str = ""
    #: 已解析好的段落（见 report_tool.py）
    sections: list[dict[str, Any]] = field(default_factory=list)


def render_report(doc: ReportDocument, now: datetime | None = None) -> str:
    charts: dict[str, Any] = {}
    sections = []
    for i, section in enumerate(doc.sections):
        kind = section["type"]
        if kind == "markdown":
            sections.append({"type": kind, "title": section.get("title", ""), "html": markdown_html(section["content"])})
        elif kind == "kpis":
            items = []
            for item in section["items"]:
                tone = item.get("tone") or "neutral"
                items.append({**item, "tone": tone, "arrow": _ARROWS.get(tone, "")})
            sections.append({"type": kind, "items": items})
        elif kind == "chart":
            key = f"c{i}"
            charts[key] = polish_option(section["option"])
            sections.append({**section, "key": key})
        elif kind == "table":
            sections.append(section)
    return _env.get_template("report.html.j2").render(
        title=doc.title,
        subtitle=doc.subtitle,
        datasource=doc.datasource,
        generated_at=(now or datetime.now()).strftime("%Y-%m-%d %H:%M"),
        summary_html=markdown_html(doc.summary) if doc.summary.strip() else "",
        sections=sections,
        font=FONT,
        echarts_js=_echarts_js(),
        charts_json=_script_json(charts),
        themes_json=_script_json(chart_themes()),
    )
