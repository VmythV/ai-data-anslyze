---
name: python-analysis
description: 需要用 Python（pandas 等）做 SQL 不方便的计算时使用：沙箱约束、读取查询结果、常见分析套路和输出规范。
---

# Python 分析（run_python）

## 沙箱约束

- 工作目录是本会话工作区。`run_sql` 的结果在 `data/qNNN*.parquet`。
- 可用库：pandas、numpy、scipy、statsmodels、pyarrow、duckdb。**不能联网，不能连数据库**，也不能 pip install。
- 每次调用是独立进程，变量不保留；需要复用的中间结果写成文件（`output/xxx.parquet` 或 `.csv`）。
- 单次运行有超时；不要做大规模循环，尽量向量化。

## 什么时候用

- SQL 写起来很别扭的计算：多步变换、透视、滚动窗口、分布统计、相关性、回归、预测、异常检测。
- 合并多次查询的结果。
- 为 render_chart 准备整形好的数据（宽表 / 长表）。

简单聚合直接用 SQL，不要为了用 Python 而用 Python。

## 写法

```python
import pandas as pd

df = pd.read_parquet("data/q003_monthly_gmv.parquet")
df["环比"] = df["GMV"].pct_change()

# 只打印需要看的结论性数字，不要打印整张大表
print(df.tail(6).to_string(index=False))
print("最大环比增幅：", df.loc[df["环比"].idxmax(), ["月份", "环比"]].to_dict())

# 要画图的数据写到 output/，再给 render_chart 的 data_file
df.to_csv("output/monthly_gmv_mom.csv", index=False)
```

也可以用 duckdb 直接对 parquet 写 SQL：

```python
import duckdb
print(duckdb.sql("SELECT 品类, SUM(销售额) FROM 'data/q004.parquet' GROUP BY 1 ORDER BY 2 DESC").df())
```

## 常用套路

- 透视：`df.pivot_table(index="月份", columns="渠道", values="GMV", aggfunc="sum").reset_index()`
- 宽表转长表（适合 dataset + encode 画多系列）：`df.melt(id_vars="月份", var_name="渠道", value_name="GMV")`
- 滚动平均：`df["7日均值"] = df["订单数"].rolling(7).mean()`
- 异常检测：用 IQR 或 z-score 标记，打印异常点而不是全量。
- 趋势预测：`statsmodels.tsa.holtwinters.ExponentialSmoothing`，在结论里说明这是简单模型的估计。

## 输出规范

- 浮点数保留合理位数（`round(2)`），金额可换算成万元。
- 写出给图表用的 CSV 时，列名就是最终展示的字段名（中文可以）。
- 脚本报错时读 stderr 修正，不要原样重跑。
