from datetime import datetime
from types import SimpleNamespace

from bson import ObjectId

from data_analyze.config import Settings
from data_analyze.datasources.federation import Federation
from data_analyze.datasources.mongodb import trino_schema
from data_analyze.datasources.sql_guard import check_readonly, federated_violations
from data_analyze.relations.infer import Column, generate_candidates

CATALOGS = {"shop": ("mysql", "shop"), "wms": ("mysql", "wms"), "crm": ("postgresql", "crm"),
            "events": ("clickhouse", "events"), "service": ("mongodb", "service")}


def _violations(sql: str) -> list[str]:
    return federated_violations(check_readonly(sql, "trino"), CATALOGS)


def test_federated_violations() -> None:
    assert _violations("SELECT * FROM events.events.user_events e JOIN crm.marketing.coupons c ON 1=1") == []
    assert _violations("WITH x AS (SELECT 1 a) SELECT * FROM x JOIN shop.shop.orders o ON 1=1") == []
    assert "没写全" in _violations("SELECT * FROM orders")[0]
    assert "没写全" in _violations("SELECT * FROM shop.orders")[0]
    assert "不是本会话已选" in _violations("SELECT * FROM system.runtime.queries")[0]
    assert "不是本会话已选" in _violations("SELECT * FROM other.x.y")[0]
    # 借 shop 的连接读同实例的其他库：不行，要用 wms 自己的 catalog
    assert "应写库名 shop" in _violations("SELECT * FROM shop.wms.shipments")[0]


def _ds(kind: str, **kw):  # noqa: ANN003, ANN202
    base = dict(alias="x", kind=kind, host="127.0.0.1", port=1, username="u", password_enc="", database="db",
                options={})
    return SimpleNamespace(**{**base, **kw})


def test_catalog_properties() -> None:
    fed = Federation(Settings(trino_url="http://trino:8080", trino_host_aliases="127.0.0.1=host.docker.internal"),
                     None, None, None)  # type: ignore[arg-type]
    assert fed.enabled
    connector, props = fed.catalog_properties(_ds("mysql", port=3306), "p")
    assert connector == "mysql" and props["connection-url"] == "jdbc:mysql://host.docker.internal:3306"
    _, props = fed.catalog_properties(_ds("postgresql", host="pg", options={"sslmode": "require"}), "p")
    assert props["connection-url"] == "jdbc:postgresql://pg:1/db?sslmode=require"
    _, props = fed.catalog_properties(_ds("clickhouse", host="ch"), "p")
    assert props["clickhouse.map-string-as-varchar"] == "true"
    _, props = fed.catalog_properties(_ds("mongodb", host="mg", username="a@b", options={"auth_source": "admin"}), "p:w/d")
    assert props["mongodb.connection-url"] == "mongodb://a%40b:p%3Aw%2Fd@mg:1/?authSource=admin"
    assert not Federation(Settings(_env_file=None, trino_url=""), None, None, None).enabled  # type: ignore[arg-type,call-arg]


def test_trino_schema_merges_samples() -> None:
    docs = [
        {"_id": ObjectId(), "no": "T1", "customer": {"phone": "1"}, "order_no": None, "score": 1, "tags": [],
         "messages": [{"role": "c", "at": datetime(2025, 1, 1)}]},
        {"_id": ObjectId(), "no": "T2", "customer": {"id": 7, "phone": "2"}, "order_no": "SO1", "score": 2.5,
         "tags": ["a"], "mixed": 1},
        {"_id": ObjectId(), "no": "T3", "mixed": "x"},
    ]
    fields = {f["name"]: f["type"] for f in trino_schema("t", docs)["fields"]}
    assert fields["_id"] == "ObjectId"
    assert fields["customer"] == 'row("phone" varchar, "id" bigint)'
    assert fields["order_no"] == "varchar"  # 第一个文档里是 null，也要能识别出来
    assert fields["score"] == "double" and fields["tags"] == "array(varchar)" and fields["mixed"] == "varchar"
    assert fields["messages"] == 'array(row("role" varchar, "at" timestamp(3)))'


def _col(ds: str, table: str, name: str, type_: str = "varchar(24)", comment: str = "") -> Column:
    return Column(ds * 32, ds, ds, table, name, type_, comment)


def test_candidates_dedupe_siblings_and_internal() -> None:
    columns = [
        _col("a", "orders", "order_no"), _col("a", "orders", "customer_id", "int", "客户ID，关联 customers.id"),
        _col("a", "customers", "id", "int"), _col("a", "order_items", "product_id", "int"),
        _col("a", "products", "id", "int"),
        _col("b", "shipments", "order_no", comment="对应 a.orders.order_no"),
        _col("c", "tickets", "order_no"),
        _col("c", "tickets", "customer.a_customer_id", "int|null"),
        _col("d", "inventory", "product_id", "int"),
        _col("d", "inventory", "status", "varchar(8)"), _col("a", "orders", "status", "varchar(8)"),
    ]
    existing = {frozenset({("a" * 32, "products", "id"), ("d" * 32, "inventory", "product_id")})}
    found = {(c.left.label(), c.right.label()) for c in generate_candidates(columns, existing)}
    assert ("b.shipments.order_no", "a.orders.order_no") in found
    # tickets.order_no 与 shipments.order_no 都指向 orders.order_no：只保留一条通往 orders 的，不再连兄弟
    order_links = {pair for pair in found if "order_no" in pair[0]}
    assert len(order_links) == 2
    assert ("c.tickets.customer.a_customer_id", "a.customers.id") in found  # 去掉数据源前缀的命名约定
    # 同库内部的外键不进队列；与已知关系同组的兄弟字段（order_items.product_id ↔ inventory.product_id）不重复提
    assert not any(left.startswith("a.") and right.startswith("a.") for left, right in found)
    assert not any("product_id" in left and "product_id" in right for left, right in found)
    assert not any("status" in left for left, _ in found)  # 通用字段名不算
