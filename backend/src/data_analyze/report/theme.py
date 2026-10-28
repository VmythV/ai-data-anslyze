"""ECharts 主题：聊天里的图表卡片和 HTML 报告共用这一份（前端经 /api/chart-theme 取）。

配色来自经过色觉障碍校验的分类色板，亮色、暗色各一套；类别色按固定顺序分配。
"""

from __future__ import annotations

from typing import Any

FONT = 'system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif'

_MODES = {
    "light": {
        "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
        "text": "#0b0b0b",
        "text2": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "surface": "#fcfcfb",
        "tooltip": "#ffffff",
        "border": "rgba(11,11,11,0.10)",
    },
    "dark": {
        "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
        "text": "#f0efec",
        "text2": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "surface": "#1a1a19",
        "tooltip": "#242423",
        "border": "rgba(255,255,255,0.10)",
    },
}


def _axis(c: dict[str, Any], category: bool) -> dict[str, Any]:
    return {
        "axisLine": {"show": category, "lineStyle": {"color": c["axis"]}},
        "axisTick": {"show": False},
        "axisLabel": {"color": c["muted"], "fontSize": 11},
        "splitLine": {"show": not category, "lineStyle": {"color": c["grid"], "type": "solid"}},
        "nameTextStyle": {"color": c["muted"], "fontSize": 11},
    }


def echarts_theme(mode: str) -> dict[str, Any]:
    c = _MODES[mode]
    return {
        "color": c["series"],
        "backgroundColor": "transparent",
        "textStyle": {"fontFamily": FONT, "color": c["text2"]},
        "title": {"textStyle": {"color": c["text"], "fontWeight": 600}, "subtextStyle": {"color": c["muted"]}},
        "legend": {"textStyle": {"color": c["text2"]}, "icon": "roundRect", "itemWidth": 12, "itemHeight": 8},
        "tooltip": {
            "backgroundColor": c["tooltip"],
            "borderColor": c["border"],
            "borderWidth": 1,
            "textStyle": {"color": c["text"], "fontSize": 12},
            "extraCssText": "box-shadow:0 4px 16px rgba(0,0,0,.12);border-radius:8px;",
        },
        "grid": {"left": 8, "right": 16, "top": 40, "bottom": 8, "containLabel": True},
        "categoryAxis": _axis(c, True),
        "valueAxis": _axis(c, False),
        "timeAxis": _axis(c, True),
        "logAxis": _axis(c, False),
        "line": {"symbol": "circle", "symbolSize": 6, "showSymbol": False, "lineStyle": {"width": 2}, "smooth": False},
        "bar": {"itemStyle": {"borderRadius": [4, 4, 0, 0]}, "barMaxWidth": 28},
        "pie": {"itemStyle": {"borderColor": c["surface"], "borderWidth": 2}},
        "scatter": {"symbolSize": 8, "itemStyle": {"borderColor": c["surface"], "borderWidth": 1}},
        "visualMap": {"inRange": {"color": ["#cde2fb", "#6da7ec", "#2a78d6", "#184f95"]}, "textStyle": {"color": c["muted"]}},
    }


def chart_themes() -> dict[str, Any]:
    return {mode: echarts_theme(mode) for mode in _MODES}
