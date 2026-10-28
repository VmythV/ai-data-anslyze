"""生成多数据源 mock 数据（只依赖标准库，固定随机种子，重复执行结果一致）。

用法：python deploy/mock/generate.py
- shop（MySQL）：电商订单，本文件
- wms（MySQL，与 shop 同实例）、crm（PostgreSQL）、service（MongoDB）：generate_more.py
- events（ClickHouse）：数据量大，由 clickhouse/init 里的 SQL 在库内生成

shop 库里刻意埋的规律（便于验证 AI 的分析能力）：
- 整体逐年增长约 25%；春节所在月份走低，6 月（618）与 11 月（双 11）走高
- 华东、华南贡献最多；西北、东北偏少；数码家电客单价最高
- 小程序渠道 2025 年起快速增长；退款率在服饰品类明显偏高
"""

from __future__ import annotations

import gzip
import random
from datetime import date, datetime, timedelta
from pathlib import Path

SEED = 20261027
START = date(2024, 1, 1)
END = date(2026, 10, 26)
BASE_DAILY_ORDERS = 45
HERE = Path(__file__).parent
OUT = HERE / "mysql" / "init" / "02-data.sql.gz"

REGIONS = {
    "华东": (["上海", "江苏", "浙江", "安徽", "福建", "山东"], 1.6),
    "华南": (["广东", "广西", "海南"], 1.4),
    "华北": (["北京", "天津", "河北", "山西", "内蒙古"], 1.1),
    "华中": (["湖北", "湖南", "河南", "江西"], 0.9),
    "西南": (["四川", "重庆", "云南", "贵州", "西藏"], 0.8),
    "西北": (["陕西", "甘肃", "青海", "宁夏", "新疆"], 0.45),
    "东北": (["辽宁", "吉林", "黑龙江"], 0.5),
}
CITIES = {
    "上海": ["上海"], "江苏": ["南京", "苏州", "无锡"], "浙江": ["杭州", "宁波", "温州"],
    "安徽": ["合肥", "芜湖"], "福建": ["福州", "厦门"], "山东": ["济南", "青岛"],
    "广东": ["广州", "深圳", "东莞", "佛山"], "广西": ["南宁", "桂林"], "海南": ["海口", "三亚"],
    "北京": ["北京"], "天津": ["天津"], "河北": ["石家庄", "保定"], "山西": ["太原"],
    "内蒙古": ["呼和浩特"], "湖北": ["武汉", "宜昌"], "湖南": ["长沙", "株洲"],
    "河南": ["郑州", "洛阳"], "江西": ["南昌"], "四川": ["成都", "绵阳"], "重庆": ["重庆"],
    "云南": ["昆明"], "贵州": ["贵阳"], "西藏": ["拉萨"], "陕西": ["西安"], "甘肃": ["兰州"],
    "青海": ["西宁"], "宁夏": ["银川"], "新疆": ["乌鲁木齐"], "辽宁": ["沈阳", "大连"],
    "吉林": ["长春"], "黑龙江": ["哈尔滨"],
}

# 一级品类：(二级品类, 价格区间, 成本率, 退款倾向, 品牌)
CATEGORIES = {
    "数码家电": (["手机", "笔记本电脑", "耳机", "智能手表", "空气净化器"], (299, 8999), 0.78, 0.03,
             ["星耀", "极光", "云帆", "Novo"]),
    "服饰鞋包": (["男装", "女装", "运动鞋", "箱包"], (59, 899), 0.45, 0.12,
             ["素线", "城野", "TIDE", "北纬"]),
    "美妆个护": (["护肤", "彩妆", "洗护"], (39, 699), 0.40, 0.05, ["花研", "LUMI", "青禾"]),
    "食品生鲜": (["零食", "水果", "乳品", "粮油"], (9, 199), 0.70, 0.04, ["田园记", "鲜到家", "麦香"]),
    "家居日用": (["厨具", "床品", "收纳", "清洁"], (19, 1299), 0.55, 0.06, ["木间", "居然好", "Homi"]),
    "母婴玩具": (["奶粉", "纸尿裤", "玩具"], (49, 599), 0.62, 0.04, ["贝贝熊", "小橡树"]),
}
CATEGORY_WEIGHT = {"数码家电": 0.8, "服饰鞋包": 1.3, "美妆个护": 1.0, "食品生鲜": 1.5,
                   "家居日用": 0.9, "母婴玩具": 0.6}

LEVELS = [("普通会员", 0.6), ("银卡会员", 0.25), ("金卡会员", 0.11), ("钻石会员", 0.04)]
REG_CHANNELS = [("APP", 0.4), ("小程序", 0.3), ("PC官网", 0.2), ("线下门店", 0.1)]
PAY_METHODS = [("微信支付", 0.48), ("支付宝", 0.40), ("银行卡", 0.08), ("货到付款", 0.04)]
REFUND_REASONS = ["尺码不合适", "质量问题", "与描述不符", "不想要了", "物流太慢", "发错货"]
SURNAMES = "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾萧田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈"
GIVEN = "伟芳娜秀英敏静丽强磊军洋勇艳杰娟涛明超秀兰霞平刚桂英华建国志红玉梅晨宇浩然子涵欣怡梓萱一诺"
SPRING_FESTIVAL = {2024: date(2024, 2, 10), 2025: date(2025, 1, 29), 2026: date(2026, 2, 17)}


def pick(rng: random.Random, weighted: list[tuple[str, float]]) -> str:
    return rng.choices([k for k, _ in weighted], [w for _, w in weighted])[0]


def sql_value(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return f"'{value}'"
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def inserts(table: str, columns: list[str], rows: list[tuple], batch: int = 1000) -> str:
    out = []
    for i in range(0, len(rows), batch):
        values = ",\n".join("(" + ",".join(sql_value(v) for v in row) + ")" for row in rows[i : i + batch])
        out.append(f"INSERT INTO {table} ({','.join(columns)}) VALUES\n{values};\n")
    return "".join(out)


def day_factor(day: date) -> float:
    years = (day - START).days / 365
    growth = 1.25**years
    month = {1: 0.95, 2: 0.85, 3: 0.95, 4: 1.0, 5: 1.05, 6: 1.35, 7: 0.95, 8: 0.95,
             9: 1.0, 10: 1.05, 11: 1.5, 12: 1.15}[day.month]
    festival = SPRING_FESTIVAL.get(day.year)
    if festival and -7 <= (day - festival).days <= 7:
        month *= 0.6
    if (day.month, day.day) in {(6, 18), (11, 11)}:
        month *= 3.0
    weekend = 1.15 if day.weekday() >= 5 else 1.0
    return growth * month * weekend


def build_shop() -> dict:
    """生成 shop 库（写出 MySQL 初始化脚本），并返回数据供其他库引用。随机序列与旧版一致，数据不变。"""
    rng = random.Random(SEED)
    sql: list[str] = ["SET NAMES utf8mb4;\nSET foreign_key_checks = 0;\nUSE shop;\n"]

    regions, region_weight = [], []
    for region, (provinces, weight) in REGIONS.items():
        for province in provinces:
            for city in CITIES[province]:
                regions.append((len(regions) + 1, region, province, city))
                region_weight.append(weight)
    sql.append(inserts("regions", ["id", "region", "province", "city"], regions))

    categories, products = [], []
    for top, (subs, (low, high), cost_rate, _, brands) in CATEGORIES.items():
        top_id = len(categories) + 1
        categories.append((top_id, top, None, 1))
        for sub in subs:
            sub_id = len(categories) + 1
            categories.append((sub_id, sub, top_id, 2))
            for n in range(rng.randint(6, 12)):
                price = round(rng.uniform(low, high) / 10) * 10 - 1
                price = max(price, low)
                brand = rng.choice(brands)
                launch = START - timedelta(days=rng.randint(0, 400)) + timedelta(days=rng.randint(0, 900))
                products.append((len(products) + 1, f"{brand} {sub} {chr(65 + n)}{rng.randint(1, 9)}",
                                 sub_id, brand, price, round(price * cost_rate * rng.uniform(0.9, 1.1), 2),
                                 min(launch, END), "在售" if rng.random() > 0.08 else "下架", top))
    sql.append(inserts("categories", ["id", "name", "parent_id", "level"], categories))
    sql.append(inserts("products", ["id", "name", "category_id", "brand", "unit_price", "cost_price",
                                    "launch_date", "status"], [p[:8] for p in products]))

    customers = []
    for cid in range(1, 18001):
        reg = START - timedelta(days=500) + timedelta(days=int(rng.betavariate(2, 1.3) * 1500))
        reg = min(reg, END)
        customers.append((cid, rng.choice(SURNAMES) + rng.choice(GIVEN) + (rng.choice(GIVEN) if rng.random() < 0.6 else ""),
                          "男" if rng.random() < 0.47 else "女", rng.randint(1960, 2006),
                          rng.choices(regions, region_weight)[0][0], reg, pick(rng, LEVELS),
                          pick(rng, REG_CHANNELS)))
    sql.append(inserts("customers", ["id", "name", "gender", "birth_year", "region_id", "register_date",
                                     "member_level", "register_channel"], customers))
    level_boost = {"普通会员": 1.0, "银卡会员": 1.6, "金卡会员": 2.4, "钻石会员": 3.5}
    cust_weights = [level_boost[c[6]] for c in customers]

    by_top: dict[str, list[tuple]] = {}
    for p in products:
        by_top.setdefault(p[8], []).append(p)
    tops = list(CATEGORY_WEIGHT)

    orders, items, refunds, traffic = [], [], [], []
    day = START
    while day <= END:
        count = int(BASE_DAILY_ORDERS * day_factor(day) * rng.uniform(0.85, 1.15))
        years = (day - START).days / 365
        mini_share = 0.15 + (0.25 * min(1, max(0, years - 1)) if day.year >= 2025 else 0)
        channels = [("APP", 0.45 - mini_share / 2), ("小程序", mini_share), ("PC官网", 0.3 - mini_share / 2),
                    ("线下门店", 0.1)]
        for _ in range(count):
            customer = rng.choices(customers, cust_weights)[0]
            if customer[5] > day:
                continue
            ts = datetime.combine(day, datetime.min.time()) + timedelta(
                seconds=int(rng.triangular(7 * 3600, 86399, 20.5 * 3600)))
            oid = len(orders) + 1
            top = rng.choices(tops, [CATEGORY_WEIGHT[t] for t in tops])[0]
            total = 0.0
            for _ in range(rng.choices([1, 2, 3, 4], [0.6, 0.25, 0.1, 0.05])[0]):
                product = rng.choice(by_top[top] if rng.random() < 0.8 else products)
                qty = rng.choices([1, 2, 3], [0.8, 0.15, 0.05])[0]
                amount = round(product[4] * qty, 2)
                total += amount
                items.append((len(items) + 1, oid, product[0], qty, product[4], amount))
            discount = round(total * rng.choice([0, 0, 0.05, 0.1, 0.15]) if day.month in (6, 11) or rng.random() < 0.3 else 0, 2)
            shipping = 0 if total >= 99 else 8
            pay = round(total - discount + shipping, 2)
            age_days = (END - day).days
            refund_rate = CATEGORIES[top][3]
            r = rng.random()
            if age_days < 3 and r < 0.6:
                status = "配送中"
            elif r < 0.05:
                status = "已取消"
            elif r < 0.05 + refund_rate:
                status = "已退款"
            else:
                status = "已完成"
            orders.append((oid, f"SO{day:%Y%m%d}{oid:07d}", customer[0], ts, status, pick(rng, PAY_METHODS),
                           pick(rng, channels), customer[4], round(total, 2), discount, shipping, pay))
            if status == "已退款":
                refunds.append((len(refunds) + 1, oid, ts + timedelta(days=rng.randint(1, 15), hours=rng.randint(0, 23)),
                                pay, rng.choice(REFUND_REASONS)))
        for ch, share in [("APP", 0.45), ("小程序", mini_share + 0.05), ("PC官网", 0.35)]:
            visits = int(count * share * rng.uniform(28, 40))
            traffic.append((day, ch, visits, int(visits * rng.uniform(0.55, 0.7)),
                            int(count * share * rng.uniform(0.9, 1.05))))
        day += timedelta(days=1)

    sql.append(inserts("orders", ["id", "order_no", "customer_id", "order_time", "status", "pay_method",
                                  "channel", "region_id", "total_amount", "discount_amount", "shipping_fee",
                                  "pay_amount"], orders))
    sql.append(inserts("order_items", ["id", "order_id", "product_id", "quantity", "unit_price", "amount"], items))
    sql.append(inserts("refunds", ["id", "order_id", "refund_time", "refund_amount", "reason"], refunds))
    sql.append(inserts("daily_traffic", ["stat_date", "channel", "visits", "unique_visitors", "orders"], traffic))
    sql.append("SET foreign_key_checks = 1;\n")
    # 让 information_schema 里的行数统计立即可用
    sql.append("ANALYZE TABLE regions, categories, products, customers, orders, order_items, refunds, daily_traffic;\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        f.writelines(sql)
    print(f"shop: orders={len(orders)} items={len(items)} refunds={len(refunds)} customers={len(customers)} "
          f"products={len(products)} -> {OUT.relative_to(HERE)} ({OUT.stat().st_size // 1024} KB)")
    return {"regions": regions, "products": products, "customers": customers, "orders": orders, "items": items,
            "refunds": refunds}


def main() -> None:
    from generate_more import build_all

    build_all(build_shop())


if __name__ == "__main__":
    main()
