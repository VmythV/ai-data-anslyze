"""wms / crm / service 三个库的 mock 数据，引用 shop 的客户、商品、订单，用来验证跨库分析。

刻意埋的「坑」——好的分析应当发现并说明：
- wms.shipments：约 3% 的有效订单没有发货记录；另有 150 条发货单对应的订单号在 shop 里不存在（补发/测试单）
- crm：只有约 85% 的 shop 客户注册了会员；member_id 是「M + 8 位客户 ID」，同时有 shop_customer_id 列；
  会员等级叫法与 shop.customers.member_level 不同（普通/银卡/金卡/黑金）；另有 600 个会员没有 shop 账号
- service.tickets：物流慢的订单更容易产生「物流」工单且满意度低；约 10% 的工单没有关联客户（游客）
- events（ClickHouse）只覆盖 2025-01-01 之后，与 shop 的时间范围不一致
"""

from __future__ import annotations

import gzip
import json
import random
from datetime import date, datetime, timedelta

from generate import HERE, END, SEED, inserts, sql_value

CARRIERS = {"顺丰": (0.8, 0.30), "京东物流": (0.9, 0.20), "中通": (1.4, 0.22), "圆通": (1.5, 0.18), "EMS": (2.2, 0.10)}
WAREHOUSES = [
    (1, "WH-SH", "上海青浦仓", "华东", "上海"), (2, "WH-HZ", "杭州萧山仓", "华东", "杭州"),
    (3, "WH-GZ", "广州白云仓", "华南", "广州"), (4, "WH-BJ", "北京大兴仓", "华北", "北京"),
    (5, "WH-WH", "武汉东西湖仓", "华中", "武汉"), (6, "WH-CD", "成都双流仓", "西南", "成都"),
    (7, "WH-XA", "西安临潼仓", "西北", "西安"), (8, "WH-SY", "沈阳于洪仓", "东北", "沈阳"),
]
TIERS = {"普通会员": "普通", "银卡会员": "银卡", "金卡会员": "金卡", "钻石会员": "黑金"}
AGENT_TEAMS = ["售前组", "售后组", "物流专项组", "VIP 服务组"]


def _write(path, text: str) -> None:  # noqa: ANN001
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)
    print(f"  -> {path.relative_to(HERE)} ({path.stat().st_size // 1024} KB)")


def build_wms(shop: dict, rng: random.Random) -> dict[int, tuple[datetime, datetime | None, str]]:
    """返回 订单ID → (发货时间, 签收时间, 承运商)，给工单数据用。"""
    region_of = {r[0]: r[1] for r in shop["regions"]}
    wh_by_region = {}
    for wh in WAREHOUSES:
        wh_by_region.setdefault(wh[3], []).append(wh)
    shipments, delivery = [], {}
    carriers = list(CARRIERS)
    for order in shop["orders"]:
        oid, order_no, _, ts, status = order[:5]
        if status == "已取消" or rng.random() < 0.03:
            continue
        region = region_of[order[7]]
        local = rng.random() < 0.85
        wh = rng.choice(wh_by_region[region]) if local else rng.choice(WAREHOUSES)
        carrier = rng.choices(carriers, [w for _, w in CARRIERS.values()])[0]
        shipped = ts + timedelta(hours=rng.uniform(3, 30))
        days = rng.uniform(0.8, 1.8) * CARRIERS[carrier][0] + (0 if local else rng.uniform(1, 2.5))
        if region in ("西北", "东北"):
            days += rng.uniform(0.5, 2)
        if (ts.month, ts.day) in {(11, 11), (11, 12), (6, 18), (6, 19)} or (ts.month == 11 and ts.day <= 15):
            days += rng.uniform(1, 3)
        delivered = shipped + timedelta(days=days)
        ship_status = "已签收"
        if status == "配送中" or delivered > datetime.combine(END, datetime.min.time()):
            delivered, ship_status = None, "运输中"
        elif rng.random() < 0.01:
            ship_status = "退回"
        delivery[oid] = (shipped, delivered, carrier)
        shipments.append((len(shipments) + 1, order_no, wh[0], carrier, shipped.replace(microsecond=0),
                          delivered.replace(microsecond=0) if delivered else None, ship_status,
                          round(rng.uniform(6, 18) * (1.4 if not local else 1), 2)))
    for i in range(150):
        day = date(2024, 1, 1) + timedelta(days=rng.randint(0, 1000))
        shipped = datetime.combine(day, datetime.min.time()) + timedelta(hours=rng.uniform(8, 20))
        shipments.append((len(shipments) + 1, f"SO{day:%Y%m%d}{9_000_000 + i:07d}", rng.randint(1, 8),
                          rng.choice(carriers), shipped, shipped + timedelta(days=rng.uniform(1, 4)), "已签收",
                          round(rng.uniform(6, 18), 2)))

    inventory = []
    month = date(2024, 1, 1)
    while month <= END:
        for wh in WAREHOUSES:
            for product in shop["products"]:
                if rng.random() < 0.35:
                    continue
                inbound = rng.randint(0, 400)
                outbound = rng.randint(0, 380)
                inventory.append((month, wh[0], product[0], max(0, rng.randint(-50, 600)), inbound, outbound))
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)

    sql = ["SET NAMES utf8mb4;\nSET foreign_key_checks = 0;\nUSE wms;\n",
           inserts("warehouses", ["id", "code", "name", "region", "city"], WAREHOUSES),
           inserts("shipments", ["id", "order_no", "warehouse_id", "carrier", "shipped_at", "delivered_at", "status",
                                 "freight_cost"], shipments),
           inserts("inventory_monthly", ["month", "warehouse_id", "product_id", "on_hand", "inbound_qty",
                                         "outbound_qty"], inventory),
           "ANALYZE TABLE warehouses, shipments, inventory_monthly;\n"]
    print(f"wms: shipments={len(shipments)} inventory_rows={len(inventory)}")
    _write(HERE / "mysql" / "init" / "04-wms-data.sql.gz", "".join(sql))
    return delivery


def build_crm(shop: dict, rng: random.Random) -> None:
    members = []
    for c in shop["customers"]:
        if rng.random() < 0.85:
            joined = datetime.combine(c[5], datetime.min.time()) + timedelta(days=rng.randint(0, 60), hours=rng.randint(8, 22))
            members.append((f"M{c[0]:08d}", c[0], f"{rng.randint(0, 9999):04d}", joined, TIERS[c[6]],
                            rng.choice(["APP 注册", "小程序授权", "门店办卡", "活动拉新"]), rng.random() < 0.7))
    for i in range(600):
        joined = datetime(2024, 1, 1) + timedelta(days=rng.randint(0, 1000), hours=rng.randint(8, 22))
        members.append((f"M9{i:07d}", None, f"{rng.randint(0, 9999):04d}", joined, "普通", "活动拉新",
                        rng.random() < 0.5))
    member_of = {m[1]: m[0] for m in members if m[1] is not None}

    ledger = []
    for o in shop["orders"]:
        member = member_of.get(o[2])
        if member and o[4] == "已完成" and rng.random() < 0.6:
            ledger.append((len(ledger) + 1, member, o[3] + timedelta(days=rng.randint(7, 15)), int(o[11] // 10),
                           "消费积分", o[1]))
            if rng.random() < 0.08:
                ledger.append((len(ledger) + 1, member, o[3] + timedelta(days=rng.randint(20, 90)),
                               -rng.choice([500, 1000, 2000]), "积分兑换", None))

    campaigns = []
    for year in (2024, 2025, 2026):
        campaigns.append((len(campaigns) + 1, f"{year} 年货节", "全渠道", date(year, 1, 10), date(year, 1, 25), 300_000))
        campaigns.append((len(campaigns) + 1, f"{year} 618 大促", "全渠道", date(year, 6, 1), date(year, 6, 20), 1_200_000))
        if year < 2026 or END >= date(year, 11, 1):
            campaigns.append((len(campaigns) + 1, f"{year} 双 11", "全渠道", date(year, 10, 25), date(year, 11, 12),
                              2_000_000))
        for m in range(1, 13):
            start = date(year, m, 15)
            if start <= END:
                campaigns.append((len(campaigns) + 1, f"{year}-{m:02d} 会员日", "小程序", start, start + timedelta(days=2),
                                  80_000))

    orders_by_member: dict[str, list] = {}
    for o in shop["orders"]:
        if o[2] in member_of and o[9] > 0 and o[4] in ("已完成", "配送中"):
            orders_by_member.setdefault(member_of[o[2]], []).append(o)
    coupons, redemptions = [], []
    member_ids = [m[0] for m in members]
    for camp in campaigns:
        cid, _, _, start, end, budget = camp
        # 一半的券定向发给活动期间下过单的会员，另一半随机发
        active = [m for m, os in orders_by_member.items() if any(start <= o[3].date() <= end for o in os)]
        count = min(len(member_ids), budget // 400)
        targeted = rng.sample(active, min(len(active), count // 2))
        for member in targeted + rng.sample(member_ids, count - len(targeted)):
            face = rng.choice([10, 20, 30, 50, 100])
            issued = datetime.combine(start, datetime.min.time()) + timedelta(hours=rng.randint(0, 48))
            coupons.append((len(coupons) + 1, cid, member, face, face * rng.choice([5, 8, 10]), issued,
                            datetime.combine(end, datetime.max.time()).replace(microsecond=0)))
            window = [o for o in orders_by_member.get(member, ()) if start <= o[3].date() <= end]
            if window and rng.random() < 0.6:
                o = rng.choice(window)
                redemptions.append((len(coupons), o[1], o[3], min(float(face), float(o[9]))))

    def pg(table: str, cols: list[str], rows: list[tuple]) -> str:
        return inserts(table, cols, rows).replace("\\'", "''")

    sql = ["SET client_encoding = 'UTF8';\n",
           pg("public.members", ["member_id", "shop_customer_id", "phone_tail", "joined_at", "tier", "source",
                                 "marketing_opt_in"], [m[:6] + (str(m[6]).lower(),) for m in members]).replace("'true'", "true").replace("'false'", "false"),
           pg("public.points_ledger", ["id", "member_id", "occurred_at", "points", "reason", "ref_order_no"], ledger),
           pg("marketing.campaigns", ["id", "name", "channel", "start_date", "end_date", "budget"], campaigns),
           pg("marketing.coupons", ["id", "campaign_id", "member_id", "face_value", "threshold", "issued_at",
                                    "expire_at"], coupons),
           pg("marketing.coupon_redemptions", ["coupon_id", "order_no", "redeemed_at", "discount"], redemptions)]
    print(f"crm: members={len(members)} ledger={len(ledger)} campaigns={len(campaigns)} coupons={len(coupons)} "
          f"redemptions={len(redemptions)}")
    _write(HERE / "postgres" / "init" / "02-data.sql.gz", "".join(sql))


def build_service(shop: dict, delivery: dict, rng: random.Random) -> None:
    agents = [{"_id": i + 1, "name": f"客服{i + 1:02d}", "team": AGENT_TEAMS[i % 4],
               "hired_at": {"$date": f"{rng.choice([2021, 2022, 2023, 2024])}-0{rng.randint(1, 9)}-01T00:00:00Z"}}
              for i in range(24)]
    refunded = {r[1] for r in shop["refunds"]}
    tickets = []

    def iso(ts: datetime) -> dict:
        return {"$date": ts.strftime("%Y-%m-%dT%H:%M:%S+08:00")}

    def ticket(created: datetime, category: str, order: tuple | None, customer_id: int | None, slow: bool) -> None:
        resolved = created + timedelta(hours=rng.uniform(1, 30 if category != "物流" else 72))
        open_ = resolved > datetime.combine(END, datetime.min.time())
        sat = None if open_ or rng.random() < 0.3 else max(1, min(5, round(rng.gauss(2.6 if slow else 4.2, 0.9))))
        msgs = [{"role": "customer", "at": iso(created), "text": {
                    "物流": "快递好几天了还没到，能帮忙查一下吗？", "退款": "申请退款了，钱什么时候退回来？",
                    "质量": "收到的商品有瑕疵，怎么处理？", "咨询": "想问一下这个商品有没有优惠？",
                    "账户": "账号登录不上了"}[category]},
                {"role": "agent", "at": iso(created + timedelta(minutes=rng.randint(1, 40))), "text": "您好，已为您查询，请稍等。"}]
        if not open_:
            msgs.append({"role": "agent", "at": iso(resolved), "text": "问题已处理，感谢您的耐心。"})
        tags = [category] + (["催单"] if slow else []) + (["VIP"] if rng.random() < 0.08 else [])
        tickets.append({
            "ticket_no": f"T{created:%Y%m%d}{len(tickets) + 1:06d}",
            "created_at": iso(created),
            "channel": rng.choice(["在线客服", "电话", "APP 反馈"]),
            "customer": {"shop_customer_id": customer_id, "phone_tail": f"{rng.randint(0, 9999):04d}"},
            "order_no": order[1] if order else None,
            "category": category,
            "priority": "高" if slow or rng.random() < 0.1 else "普通",
            "status": "处理中" if open_ else rng.choice(["已解决", "已解决", "已解决", "已关闭"]),
            "resolved_at": None if open_ else iso(resolved),
            "satisfaction": sat,
            "agent_id": rng.randint(1, len(agents)),
            "tags": tags,
            "messages": msgs,
        })

    for o in shop["orders"]:
        oid = o[0]
        info = delivery.get(oid)
        slow = bool(info and info[1] and (info[1] - info[0]).days >= 4)
        if oid in refunded and rng.random() < 0.55:
            ticket(o[3] + timedelta(days=rng.uniform(1, 6)), "退款", o, o[2], False)
        elif slow and rng.random() < 0.3:
            ticket(info[0] + timedelta(days=rng.uniform(2, 4)), "物流", o, o[2], True)
        elif rng.random() < 0.012:
            ticket(o[3] + timedelta(days=rng.uniform(1, 10)), rng.choice(["质量", "咨询"]), o, o[2], False)
    for _ in range(900):
        created = datetime(2024, 1, 1) + timedelta(days=rng.randint(0, 1030), hours=rng.uniform(8, 22))
        guest = rng.random() < 0.6
        ticket(created, rng.choice(["账户", "咨询"]), None, None if guest else rng.randint(1, 18000), False)
    tickets.sort(key=lambda t: t["created_at"]["$date"])

    out = HERE / "mongo" / "init"
    for name, docs in (("tickets", tickets), ("agents", agents)):
        _write(out / f"{name}.jsonl.gz", "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in docs))
    print(f"service: tickets={len(tickets)} agents={len(agents)}")


def build_all(shop: dict) -> None:
    rng = random.Random(SEED + 1)
    delivery = build_wms(shop, rng)
    build_crm(shop, rng)
    build_service(shop, delivery, rng)


__all__ = ["build_all", "sql_value"]
