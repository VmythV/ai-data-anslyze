---
name: cross-source
description: 问题需要用到两个及以上数据源（或同一数据源里没有外键的表）时使用：怎么找到关联字段、选哪种关联方式（同连接 JOIN / 结果级关联 / 联邦查询）、怎样核对匹配率并如实报告、何时提交新发现的关联。
---

# 跨数据源分析

## 1. 先弄清「用什么关联」

依次参考（越靠前越可信）：
1. 系统提示里的「已知的跨表 / 跨源关联」—— 管理员配置的结构化关系，标了【未确认】的只能当线索
2. 数据源和数据域的「管理员说明」
3. 字段注释（如「对应 shop.orders.order_no」）
4. 以上都没有：用 search_schema 找同名/同义字段，再抽样核对取值能不能对上。
   推断出来的关联在结论里注明「按 X 字段推断关联，匹配率 Y%」；拿不准、且结论依赖它时用 ask_user 让用户确认

注意取值转换：两边的键格式可能不同（如会员号 `M00012345` 对应客户 ID `12345`），类型也可能不同（字符串 vs 整数）。

## 2. 选关联方式

| 情况 | 做法 |
|---|---|
| 两个数据源在系统提示里列为「同一连接」 | **A. 一条 SQL 直接 JOIN**：在其中一个数据源上 run_sql，用「库名.表名」引用另一个库。最快最准，优先用 |
| 不同引擎 / 不同实例，两边都能先聚合到关联键、各自几万行以内 | **B. 结果级关联**：两边各自查询，再在 run_python 里用 DuckDB 关联 parquet 文件 |
| 不同引擎，需要在**明细**上关联、或某一边没法先聚合（会超过 5 万行） | **C. 联邦查询**（系统提示里出现 run_federated_sql 时才可用）：一条 Trino SQL 跨数据源 JOIN，过滤条件会下推到各个库 |

联邦查询（C）的写法：
- 表名一律写全「数据源标识.schema.表名」：MySQL / ClickHouse / MongoDB 的 schema 是库名，PostgreSQL 是 schema 名
  - `shop.shop.orders`、`crm.public.members`、`events.events.user_events`、`service.service.tickets`
- Trino 方言：日期字面量 `DATE '2025-01-01'`、时间差 `date_diff('hour', a, b)`、字符串只能用单引号、中文别名用双引号
- MongoDB 嵌套字段：`t.customer.shop_customer_id`；数组字段用 `UNNEST`
- 先在各库上把条件写进 WHERE（如分区字段 `event_date`），让过滤下推，避免把整张大表拉过来
- 两边键的类型不同（整数 vs 字符串）时显式 `CAST`
- 比单库查询慢得多：能用 A、B 解决的不要用 C

```sql
SELECT m.tier AS 会员等级, COUNT(DISTINCT e.user_id) AS 支付用户数
FROM events.events.user_events e
JOIN crm.public.members m ON m.shop_customer_id = e.user_id
WHERE e.event_type = 'pay_success' AND e.event_date >= DATE '2026-01-01'
GROUP BY m.tier
```

结果级关联的要点：
- **先过滤、先聚合到关联键**再拉数据，不要拉整张明细表：单次结果上限 5 万行，超过会被截断，关联结果就错了
  - 例：要「各会员等级的人均消费」→ shop 按 customer_id 聚合消费（几千行）+ crm 取 shop_customer_id、tier（一万多行）→ 在 DuckDB 里关联
- 用 save_as 给结果文件起好认的名字，文件名形如 `data/q003_<数据源>_<save_as>.parquet`，run_python 里用 glob 找
- 两边时间范围要对齐：各数据源覆盖的时间段可能不同，先查 MIN/MAX 再定分析区间
- 时区、单位（元/分）、枚举叫法（「金卡会员」vs「金卡」）不一致时，在 SQL 或 Python 里先统一

```python
import duckdb, glob
spend = glob.glob('data/q*_shop_spend.parquet')[0]
tier = glob.glob('data/q*_crm_tier.parquet')[0]
print(duckdb.sql(f"""
  SELECT t.tier, COUNT(*) AS 客户数, ROUND(AVG(s.spend), 1) AS 人均消费
  FROM '{spend}' s JOIN '{tier}' t ON t.shop_customer_id = s.customer_id
  GROUP BY 1 ORDER BY 3 DESC"""))
```

## 3. 一定要核对匹配率

关联之后，计算并报告：
- 左表有多少键在右表里找不到（如「15% 的下单客户不是会员」）
- 右表有多少键在左表里找不到（如「150 张发货单对应的订单不存在」）
- 一对多关联是否把金额重复计算了（先在多的一侧聚合再关联）

```sql
-- 同一连接时可以直接在 SQL 里算
SELECT COUNT(*) AS 订单数, SUM(s.order_no IS NULL) AS 无发货记录
FROM orders o LEFT JOIN wms.shipments s ON s.order_no = o.order_no
WHERE o.status <> '已取消'
```

匹配率明显偏低（比如低于 90%）时，先排查是不是关联字段或取值转换用错了，再下结论；确认是数据本身的缺口就在结论里如实说明。

## 4. 发现了新的关联

系统提示里没列出、但你验证过（匹配率高、确实是「键对键」）的跨数据源关联，用 propose_relation 提交给管理员审核，
以后的会话就能直接用。平台会自己抽样核对匹配率；两边取值需要转换时把规则写进 transform。

## 5. MongoDB 参与关联时

run_mongo 的结果会拍平：嵌套字段变成 `customer.shop_customer_id` 这样的列名（DuckDB 里要用双引号括起来）。先在管道里 `$project` 出需要的字段并起简单的名字，后面关联更省事。
