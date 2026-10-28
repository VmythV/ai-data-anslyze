---
name: mongo-query
description: 查询 MongoDB 数据源（run_mongo）前使用：聚合管道写法、日期与嵌套字段、数组展开、常见统计套路。
---

# MongoDB 查询（run_mongo）

只能用聚合管道（pipeline），平台会在末尾自动加 `$limit` 限制行数。不能用 `$out`、`$merge`、`$where`、`$function`。

## 先看结构
describe_table 给出的字段结构是**抽样推断**的：`customer.shop_customer_id` 是嵌套字段，`messages[].role` 是对象数组里的字段。
字段可能在部分文档里缺失（看「抽样出现率」），统计时注意缺失值。

## 日期
JSON 里没有日期类型，用扩展 JSON：
```json
{"$match": {"created_at": {"$gte": {"$date": "2025-01-01T00:00:00+08:00"}, "$lt": {"$date": "2026-01-01T00:00:00+08:00"}}}}
```
按月分组：`{"$dateToString": {"format": "%Y-%m", "date": "$created_at", "timezone": "+08:00"}}`

## 常用套路
```json
[
  {"$match": {"status": "已解决"}},
  {"$group": {"_id": {"月份": {"$dateToString": {"format": "%Y-%m", "date": "$created_at", "timezone": "+08:00"}},
                      "类别": "$category"},
              "工单数": {"$sum": 1}, "平均满意度": {"$avg": "$satisfaction"}}},
  {"$project": {"_id": 0, "月份": "$_id.月份", "类别": "$_id.类别", "工单数": 1, "平均满意度": {"$round": ["$平均满意度", 2]}}},
  {"$sort": {"月份": 1}}
]
```
- 数组字段计数：`{"$size": {"$ifNull": ["$messages", []]}}`
- 展开数组：`{"$unwind": "$tags"}` 后再 `$group`
- 处理时长：`{"$dateDiff": {"startDate": "$created_at", "endDate": "$resolved_at", "unit": "hour"}}`
- 同库集合关联：`{"$lookup": {"from": "agents", "localField": "agent_id", "foreignField": "_id", "as": "agent"}}`，再 `{"$unwind": "$agent"}`

## 输出
- 用 `$project` 把 `_id` 里的分组键展开成普通列、起中文名，结果表格更好读，也方便画图和关联
- 不要拉原始文档（尤其带长文本、数组的），先聚合
