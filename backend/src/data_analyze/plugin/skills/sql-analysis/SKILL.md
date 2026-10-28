---
name: sql-analysis
description: 编写分析 SQL 时使用（MySQL / PostgreSQL / ClickHouse）：时间分桶、同比环比、占比、排名、去重计数等常用写法，三种方言的差异和性能注意事项。
---

# 分析 SQL 写法

下面的例子以 MySQL 为主，**方言差异见文末**——写 SQL 前先确认目标数据源是哪种数据库。

## 基本原则

- 只能 SELECT / WITH。一次一条语句，不要分号拼接。
- **在 SQL 里聚合**，不要拉明细到 Python 再算（结果超过上限会被截断）。
- 大表一定带时间范围过滤，并尽量让过滤落在有索引的字段上（`describe_table` 能看到索引）。
- 不要对索引字段套函数做过滤：写 `order_time >= '2025-01-01' AND order_time < '2026-01-01'`，不写 `YEAR(order_time) = 2025`。
- 列别名用中文或清晰的英文都可以，但要能直接当图表字段名用。
- `purpose` 参数写成用户能看懂的一句话，它是表格标题。

## 时间分桶

```sql
-- 按月
SELECT DATE_FORMAT(order_time, '%Y-%m') AS 月份, SUM(pay_amount) AS GMV
FROM orders WHERE order_time >= '2025-01-01' AND order_time < '2026-01-01'
GROUP BY 月份 ORDER BY 月份;

-- 按周（周一为一周开始）
SELECT DATE_SUB(DATE(order_time), INTERVAL WEEKDAY(order_time) DAY) AS 周开始, COUNT(*) AS 订单数 ...

-- 按季度
SELECT CONCAT(YEAR(order_time), '-Q', QUARTER(order_time)) AS 季度 ...
```

## 同比 / 环比（窗口函数，MySQL 8）

```sql
WITH m AS (
  SELECT DATE_FORMAT(order_time, '%Y-%m') AS ym, SUM(pay_amount) AS gmv
  FROM orders WHERE order_time >= '2024-01-01' GROUP BY ym
)
SELECT ym, gmv,
       gmv / LAG(gmv) OVER (ORDER BY ym) - 1        AS 环比,
       gmv / LAG(gmv, 12) OVER (ORDER BY ym) - 1    AS 同比
FROM m ORDER BY ym;
```
注意：LAG(12) 要求中间月份连续，有缺月时改用自连接 `ON cur.ym = DATE_FORMAT(DATE_ADD(STR_TO_DATE(CONCAT(prev.ym,'-01'),'%Y-%m-%d'), INTERVAL 12 MONTH),'%Y-%m')`。

## 占比与排名

```sql
SELECT r.region AS 大区, SUM(o.pay_amount) AS GMV,
       SUM(o.pay_amount) / SUM(SUM(o.pay_amount)) OVER () AS 占比,
       RANK() OVER (ORDER BY SUM(o.pay_amount) DESC) AS 排名
FROM orders o JOIN regions r ON r.id = o.region_id
GROUP BY r.region;

-- 每组 Top N
SELECT * FROM (
  SELECT c.name AS 品类, p.name AS 商品, SUM(i.amount) AS 销售额,
         ROW_NUMBER() OVER (PARTITION BY c.name ORDER BY SUM(i.amount) DESC) AS rn
  FROM order_items i JOIN products p ON p.id = i.product_id JOIN categories c ON c.id = p.category_id
  GROUP BY c.name, p.name
) t WHERE rn <= 3;
```

## 常见坑

- 一对多 JOIN 后再 SUM 主表金额会**重复计算**（订单 JOIN 明细后 SUM(orders.pay_amount)）。先在子查询里聚合再 JOIN。
- 去重计数用 `COUNT(DISTINCT customer_id)`；比率先求分子分母再相除，不要对比率求平均。
- 整数相除得到小数是正常的；需要百分比展示时在结论里换算，不必在 SQL 里乘 100。
- `GROUP BY` 里使用 SELECT 中的别名在 MySQL 中是允许的。
- 字符串取值要和 `describe_table` 看到的完全一致，包括中英文和全半角。
- 查询报错时读错误信息修正，不要原样重试。

## 方言差异速查

| 写法 | MySQL | PostgreSQL | ClickHouse |
|---|---|---|---|
| 表名 | `orders`，同连接其他库 `wms.shipments` | `schema.表`，如 `public.members` | `user_events`，同连接其他库 `db.表` |
| 按月 | `DATE_FORMAT(t, '%Y-%m')` | `to_char(t, 'YYYY-MM')` | `formatDateTime(t, '%Y-%m')` 或 `toStartOfMonth(t)` |
| 按天 | `DATE(t)` | `t::date` | `toDate(t)` |
| 时间差（小时） | `TIMESTAMPDIFF(HOUR, a, b)` | `EXTRACT(EPOCH FROM b - a) / 3600` | `dateDiff('hour', a, b)` |
| 去重计数 | `COUNT(DISTINCT x)` | `COUNT(DISTINCT x)` | `uniqExact(x)`（精确）或 `uniq(x)`（近似，快） |
| 条件计数 | `SUM(cond)` | `COUNT(*) FILTER (WHERE cond)` | `countIf(cond)` |
| 中文别名 | 可以直接写 | 加双引号 `"订单数"` | 加双引号 `"订单数"`（平台会自动补引号，但自己写上更稳妥） |
| 整数相除 | 得小数 | **得整数**，先 `::numeric` | 得小数（用 `intDiv` 才是整除） |

ClickHouse 额外注意：
- 大表务必在 WHERE 里带分区/排序键（如 `event_date`），否则全表扫描
- `GROUP BY` 后结果默认无序，需要顺序时显式 `ORDER BY`
- `Nullable` 列参与聚合时用 `ifNull()` 处理
