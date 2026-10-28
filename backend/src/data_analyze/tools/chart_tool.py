"""图表工具：校验 ECharts option，存为产物，推给页面渲染。"""

from __future__ import annotations

import json
from typing import Any

from data_analyze.tools.base import (
    ToolContext,
    ToolInputError,
    ToolOutput,
    ToolSpec,
    resolve_in_workspace,
)
from data_analyze.tools.tabular import read_table

CHART_MAX_ROWS = 5_000
OPTION_MAX_CHARS = 600_000
SERIES_TYPES = {
    "line", "bar", "pie", "scatter", "effectScatter", "radar", "tree", "treemap", "sunburst", "boxplot",
    "candlestick", "heatmap", "map", "parallel", "lines", "graph", "sankey", "funnel", "gauge",
    "pictorialBar", "themeRiver",
}


#: 折线点数不超过这个数时显示数据点，否则只有两三个点的线看起来像一截断线
SHOW_SYMBOL_MAX_POINTS = 24


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if isinstance(value, dict) else []


def _point_count(option: dict[str, Any], series: dict[str, Any]) -> int | None:
    if isinstance(series.get("data"), list):
        return len(series["data"])
    dataset = option.get("dataset")
    source = dataset.get("source") if isinstance(dataset, dict) else None
    return len(source) - 1 if isinstance(source, list) and source else None


def polish_option(option: dict[str, Any]) -> dict[str, Any]:
    """统一排版细节（只补没写的配置，可重复执行）。新画的图、历史图表、报告都会过一遍。

    - 图例放到底部：放在顶部会和纵轴名称挤在一起
    - 纵轴名称从轴线处往右排：居中时长名称会被左边缘截掉；没有纵轴名称时收回顶部留白
    - 点少的折线显示数据点
    - 条形图右侧有数值标签时，网格右边留出位置
    """
    option = dict(option)
    series_list = [s for s in _as_list(option.get("series")) if isinstance(s, dict)]
    grids = _as_list(option.get("grid"))
    grid = dict(grids[0]) if grids else {}

    legend = option.get("legend")
    if isinstance(legend, dict) and legend.get("show") is not False and not any(
        k in legend for k in ("top", "bottom", "left", "right")
    ):
        option["legend"] = {**legend, "bottom": 0, "type": legend.get("type", "scroll")}
        grid.setdefault("bottom", 36)

    y_axes = _as_list(option.get("yAxis"))
    # 主题给顶部留了纵轴名称的位置；没有纵轴名称（如横向条形图）时收回，免得标题和图之间空一大块
    if not any(isinstance(a, dict) and a.get("name") for a in y_axes):
        grid.setdefault("top", 12)
    if y_axes:
        polished = []
        for axis in y_axes:
            axis = dict(axis)
            if axis.get("name") and axis.get("position") != "right":
                style = dict(axis.get("nameTextStyle") or {})
                style.setdefault("align", "left")
                axis["nameTextStyle"] = style
            polished.append(axis)
        option["yAxis"] = polished if isinstance(option.get("yAxis"), list) else polished[0]

    new_series = []
    for item in _as_list(option.get("series")):
        if isinstance(item, dict) and item.get("type") == "line" and "showSymbol" not in item:
            count = _point_count(option, item)
            if count is not None and count <= SHOW_SYMBOL_MAX_POINTS:
                item = {**item, "showSymbol": True}
        new_series.append(item)
    if new_series:
        option["series"] = new_series if isinstance(option.get("series"), list) else new_series[0]

    if any(
        isinstance(s.get("label"), dict) and s["label"].get("show") and s["label"].get("position") == "right"
        for s in series_list
    ):
        grid.setdefault("right", 56)

    if grid and not isinstance(option.get("grid"), list):
        option["grid"] = grid
    return option


def prepare_option(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    option = args.get("option")
    if isinstance(option, str):
        try:
            option = json.loads(option)
        except json.JSONDecodeError as exc:
            raise ToolInputError(f"option 不是合法 JSON：{exc}") from exc
    if not isinstance(option, dict):
        raise ToolInputError("option 必须是 ECharts option 对象")
    option = dict(option)
    # 标题由卡片头部展示，option 里再放一份会重复
    option.pop("title", None)

    data_file = args.get("data_file")
    if data_file:
        path = resolve_in_workspace(ctx.workspace, str(data_file))
        columns, rows, total = read_table(path, CHART_MAX_ROWS)
        if total > CHART_MAX_ROWS:
            raise ToolInputError(
                f"{data_file} 有 {total} 行，超过图表上限 {CHART_MAX_ROWS} 行。请先聚合或抽样再画图。"
            )
        dataset = option.get("dataset")
        if isinstance(dataset, list):
            raise ToolInputError("使用 data_file 时 dataset 只能是单个对象（会被填入 source）")
        option["dataset"] = {**(dataset or {}), "source": [columns, *rows]}

    series = option.get("series")
    series_list = series if isinstance(series, list) else [series] if isinstance(series, dict) else []
    if not series_list:
        raise ToolInputError("option 缺少 series")
    for item in series_list:
        if not isinstance(item, dict) or item.get("type") not in SERIES_TYPES:
            raise ToolInputError(f"series.type 不合法：{item.get('type') if isinstance(item, dict) else item}")
    if "dataset" not in option and not any("data" in s for s in series_list):
        raise ToolInputError("没有数据：请提供 data_file，或在 series.data / dataset.source 中写入数据")

    option = polish_option(option)

    text = json.dumps(option, ensure_ascii=False)
    if len(text) > OPTION_MAX_CHARS:
        raise ToolInputError("option 过大，请先聚合数据")
    return option


def chart_tools(ctx: ToolContext) -> list[ToolSpec]:
    async def render_chart(args: dict[str, Any]) -> ToolOutput:
        title = str(args.get("title") or "").strip()
        if not title:
            raise ToolInputError("title 不能为空")
        option = prepare_option(ctx, args)
        chart_id = await ctx.artifacts.save_chart(title, option)
        ctx.emit({"type": "chart", "artifact_id": chart_id, "title": title, "option": option})
        return ToolOutput(
            f"图表「{title}」已展示给用户，chart_id={chart_id}。需要时可在 publish_report 的 chart 段落里引用它。"
        )

    return [
        ToolSpec(
            name="render_chart",
            description=(
                "把一张 ECharts 5 图表展示给用户（会自动套用统一主题和配色，不要自己设置颜色、背景和字体）。"
                "推荐写法：先用 run_sql / run_python 准备好画图数据，传 data_file，option 里用 dataset + encode 映射字段；"
                "数据量很小时也可以直接写 series.data。不要在 option 里写 title（用参数 title）。"
                "不支持 JS 函数，formatter 只能用字符串模板。调用前先加载 chart-design skill 了解规范。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "图表标题，直接写结论，控制在 20 个字左右，如「EMS 平均 3.3 天，最慢」",
                    },
                    "option": {"type": "object", "description": "ECharts option（不含 title）"},
                    "data_file": {
                        "type": "string",
                        "description": "可选，工作区内的数据文件（parquet/csv/json），首行列名会作为 dataset.source 的表头",
                    },
                },
                "required": ["title", "option"],
                "additionalProperties": False,
            },
            handler=render_chart,
        )
    ]
