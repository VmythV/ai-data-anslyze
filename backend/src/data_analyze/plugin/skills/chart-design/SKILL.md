---
name: chart-design
description: 调用 render_chart 画图前使用：根据数据选择图表类型、ECharts option 写法范例、简约好看的设计规范与常见错误。
---

# 图表设计（render_chart）

平台会自动套用统一主题（配色、字体、网格、提示框），**不要设置 color、backgroundColor、textStyle、title**。
你只需要选对图表类型、把数据映射对、补上必要的交互。

## 1. 选可视化手段：能用图就不用字

| 要表达的 | 用什么 |
|---|---|
| 1~6 个关键数字（总量、同比、占比、转化率） | **render_kpis 指标卡**，不要写在句子里 |
| 趋势、对比、构成、分布、关系 | **render_chart 图表** |
| 多个角度（如「按月趋势」+「按渠道构成」） | 多张图，每张只讲一件事（通常 1~3 张，页面会两列排布成小看板） |
| 超过 15 个类别且每个都重要 | 画 Top 10 的条形图，其余合并为「其他」；需要明细时用户能在查询结果表格里看 |
| 纯文字事实（「有没有这张表」） | 才只用文字 |

不要用 Markdown 表格罗列数据——用户已经能看到查询结果表格，回答里要的是图。

## 2. 按任务选类型

| 任务 | 图表 |
|---|---|
| 随时间的趋势 | 折线 `line`；单系列可加面积 `areaStyle: {opacity: 0.15}` |
| 类别之间比大小 | 柱状 `bar`，**按数值排序**；类别名长或多于 8 个用**横向条形**（yAxis 为 category） |
| 构成 / 占比 | 堆叠柱（`stack: "total"`）；类别 ≤ 5 时才用饼/环图 |
| 构成随时间变化 | 堆叠柱或堆叠面积 |
| 两个指标的关系 | 散点 `scatter` |
| 两个维度交叉的强度 | 热力图 `heatmap` + `visualMap` |
| 转化漏斗 | `funnel` |
| 正负偏差（同比增减） | 柱状，正负值自然分在 0 轴两侧 |

## 3. 硬规则

- **不用双 Y 轴**。两个量纲不同的指标 → 画两张图，或换算成同一量纲（如都转成增长率）。
- 系列数 ≤ 8；超过时只保留 Top 7，其余合并为「其他」。
- 2 个及以上系列必须有 `legend: {}`（平台会把它放到图表底部，不要自己设置位置）；单系列不要 legend。
- 坐标轴写单位：`yAxis: {name: "万元"}`；金额太大时先换算成万元/亿元再画。
- tooltip：折线/柱状用 `{trigger: "axis"}`，饼/散点用 `{trigger: "item"}`。
- 不要给每个点都加数值标签；条形图类别少（≤ 12）时可以 `label: {show: true, position: "right"}`。
- 折线点很多时保持默认不显示 symbol。
- 时间轴按时间顺序排列，不要按数值排序。
- 不支持 JS 函数；formatter 只能用字符串模板，如 `"{b}: {c} 万元"`。

## 4. 范例

**推荐：data_file + dataset + encode**（数据不进 option，最省事也不易出错）

```json
{
  "title": "2025 年各月 GMV（万元）",
  "data_file": "output/monthly_gmv.csv",
  "option": {
    "tooltip": {"trigger": "axis"},
    "xAxis": {"type": "category"},
    "yAxis": {"type": "value", "name": "万元"},
    "series": [{"type": "line", "encode": {"x": "月份", "y": "GMV万元"}, "areaStyle": {"opacity": 0.15}}]
  }
}
```

多系列（宽表，每列一个系列）：

```json
{
  "data_file": "output/channel_monthly.csv",
  "option": {
    "tooltip": {"trigger": "axis"}, "legend": {},
    "xAxis": {"type": "category"}, "yAxis": {"type": "value", "name": "订单数"},
    "series": [
      {"type": "bar", "stack": "total", "encode": {"x": "月份", "y": "APP"}, "name": "APP"},
      {"type": "bar", "stack": "total", "encode": {"x": "月份", "y": "小程序"}, "name": "小程序"},
      {"type": "bar", "stack": "total", "encode": {"x": "月份", "y": "PC官网"}, "name": "PC官网"}
    ]
  }
}
```

横向条形排行（数据按数值**升序**写入，最大的会显示在最上面）：

```json
{
  "option": {
    "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
    "grid": {"left": 8, "right": 48},
    "xAxis": {"type": "value", "name": "万元"},
    "yAxis": {"type": "category", "data": ["西北", "东北", "西南", "华中", "华北", "华南", "华东"]},
    "series": [{"type": "bar", "data": [120, 135, 230, 260, 310, 520, 980], "label": {"show": true, "position": "right"}}]
  }
}
```

环形图（类别 ≤ 5）：

```json
{"option": {"tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"}, "legend": {"bottom": 0},
  "series": [{"type": "pie", "radius": ["45%", "70%"], "data": [{"name": "微信支付", "value": 48}, {"name": "支付宝", "value": 40}]}]}}
```

热力图：

```json
{"data_file": "output/weekday_hour.csv",
 "option": {"tooltip": {"position": "top"},
  "xAxis": {"type": "category"}, "yAxis": {"type": "category"},
  "visualMap": {"min": 0, "max": 500, "calculable": true, "orient": "horizontal", "left": "center", "bottom": 0},
  "grid": {"bottom": 60},
  "series": [{"type": "heatmap", "encode": {"x": "小时", "y": "星期", "value": "订单数"}}]}}
```

## 5. 画完之后

- 文字只写 2~4 条短要点（每条一行、带关键数字），点出图里最重要的信息（峰值、拐点、差距）和建议；不写长段落，不要只说「如图所示」，也不要把图里的数字再念一遍。
- 图的标题直接写结论，**控制在 20 个字左右**（「EMS 平均 3.3 天，最慢」比「各承运商送达天数」好；太长的结论放到文字要点里）。多张图会两列排布，标题过长会被截断。
- 一次回答里图不在多，2~3 张讲清楚就够。
