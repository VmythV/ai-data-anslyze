from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from bson import ObjectId

from data_analyze.config import Settings
from data_analyze.datasources.catalog import SchemaCatalog
from data_analyze.datasources.mongodb import UnsafePipelineError, flatten, infer_fields, parse_pipeline
from data_analyze.datasources.sql_guard import UnsafeSqlError, check_readonly, referenced_databases, with_row_limit
from data_analyze.store.db import Database
from data_analyze.store.migrate import make_alias, run_migrations
from data_analyze.store.models import Conversation, ConversationDataSource, DataSource, SchemaEntry
from data_analyze.tools.base import ToolContext, ToolInputError


@pytest.mark.parametrize("dialect,sql", [
    ("postgres", "select pg_sleep(10)"),
    ("postgres", "select pg_read_file('/etc/passwd')"),
    ("postgres", "select nextval('s')"),
    ("clickhouse", "select * from url('http://x/a.csv', CSV)"),
    ("clickhouse", "select * from remote('10.0.0.1', db.t)"),
    ("clickhouse", "select * from file('/etc/passwd', 'LineAsString')"),
    ("clickhouse", "insert into t values (1)"),
])
def test_dialect_specific_rejections(dialect: str, sql: str) -> None:
    with pytest.raises(UnsafeSqlError):
        check_readonly(sql, dialect)


def test_referenced_databases() -> None:
    mysql = check_readonly("with x as (select 1) select * from orders o join wms.shipments s on 1=1, x", "mysql")
    assert referenced_databases(mysql, "mysql") == {"wms"}
    # PG：schema 不算跨库，database 才算
    pg = check_readonly("select * from public.members m join marketing.coupons c on 1=1", "postgres")
    assert referenced_databases(pg, "postgres") == set()
    assert referenced_databases(check_readonly("select * from other.public.t", "postgres"), "postgres") == {"other"}


def test_non_ascii_aliases_are_quoted() -> None:
    sql = with_row_limit(check_readonly("select count() as 会话数 from t order by 会话数", "clickhouse"), 5, "clickhouse")
    assert '"会话数"' in sql and "LIMIT 6" in sql


def test_mongo_pipeline_guard() -> None:
    pipeline = parse_pipeline('[{"$match": {"t": {"$gte": {"$date": "2025-01-01T00:00:00Z"}}}}, {"$limit": 5}]')
    assert isinstance(pipeline[0]["$match"]["t"]["$gte"], datetime)
    for bad in (
        [{"$out": "x"}],
        [{"$merge": {"into": "x"}}],
        [{"$match": {"$where": "sleep(1000)"}}],
        [{"$group": {"_id": None, "x": {"$accumulator": {}}}}],
        [{"$lookup": {"from": {"db": "admin", "coll": "system.users"}, "as": "u", "pipeline": []}}],
        [{"$match": {}, "$limit": 1}],
        {"$match": {}},
    ):
        with pytest.raises(UnsafePipelineError):
            parse_pipeline(bad)


def test_flatten_and_infer() -> None:
    doc = {"_id": ObjectId(), "customer": {"id": 1, "name": "a"}, "tags": ["x", "y"], "messages": [{"role": "c"}]}
    flat = flatten(doc)
    assert flat["customer.id"] == 1 and flat["tags"] == '["x", "y"]' and isinstance(flat["_id"], str)
    fields = {c.name: c for c in infer_fields([doc, {"_id": ObjectId(), "customer": {"id": 2}}])}
    assert fields["customer.name"].comment == "抽样出现率 50%" and fields["customer.name"].nullable
    assert "messages[].role" in fields and fields["tags"].type == "array<string>"


def test_make_alias() -> None:
    assert make_alias("shop", set()) == "shop"
    assert make_alias("shop", {"shop"}) == "shop_2"
    assert make_alias("电商库", set()) == "ds"
    assert make_alias("2024-Sales DB", set()) == "ds_2024_sales_db"


async def _db(tmp_path: Path) -> Database:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'app.db'}")
    await db.create_all()
    return db


async def test_migration_of_single_source_conversations(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    async with db.sessions() as s:
        s.add(DataSource(id="d" * 32, name="x", kind="mysql", host="h", port=1, username="u", password_enc="",
                         database="shop"))
        await s.flush()
        s.add(Conversation(id="c" * 32, datasource_id="d" * 32))
        await s.commit()
    await run_migrations(db.sessions)
    await run_migrations(db.sessions)  # 可重复执行
    async with db.sessions() as s:
        assert (await s.get(DataSource, "d" * 32)).alias == "shop"
        assert (await s.get(Conversation, "c" * 32)).datasource_id is None
        links = (await s.execute(ConversationDataSource.__table__.select())).all()
        assert len(links) == 1
    await db.dispose()


async def test_schema_search_ranks_by_relevance(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    async with db.sessions() as s:
        for i in ("a", "b"):
            s.add(DataSource(id=i * 32, alias=i, name=i, kind="mysql", host="h", port=1, username="u",
                             password_enc="", database=i, schema_refreshed_at=datetime.now()))
        s.add_all([
            SchemaEntry(datasource_id="a" * 32, table_name="members", comment="会员档案", row_estimate=10),
            SchemaEntry(datasource_id="a" * 32, table_name="members", column_name="tier", comment="会员等级"),
            SchemaEntry(datasource_id="b" * 32, table_name="orders", comment="订单"),
            SchemaEntry(datasource_id="b" * 32, table_name="orders", column_name="member_level", comment="会员等级"),
            SchemaEntry(datasource_id="b" * 32, table_name="logs", comment="日志"),
        ])
        await s.commit()
    catalog = SchemaCatalog(db.sessions, None, None)  # type: ignore[arg-type]
    hits = await catalog.search(["a" * 32, "b" * 32], ["会员", "member"])
    assert [h.table for h in hits] == ["members", "orders"]
    assert hits[0].comment == "会员档案" and hits[0].columns[0][0] == "tier"
    assert await catalog.search(["b" * 32], ["日志"]) and not await catalog.search(["a" * 32], ["日志"])
    await db.dispose()


def _ctx(*specs: tuple[str, str]) -> ToolContext:
    sources = {
        alias: SimpleNamespace(alias=alias, kind=lang, driver=SimpleNamespace(language=lang)) for alias, lang in specs
    }
    return ToolContext(conversation_id="x", workspace=Path("."), settings=Settings(), sources=sources,  # type: ignore[arg-type]
                       catalog=None, artifacts=None, emit=lambda e: None)  # type: ignore[arg-type]


def test_source_resolution() -> None:
    single = _ctx(("shop", "sql"))
    assert single.source(None).alias == "shop"
    multi = _ctx(("shop", "sql"), ("crm", "sql"), ("service", "mongo"))
    with pytest.raises(ToolInputError, match="请用 datasource 参数指定"):
        multi.source(None, "sql")
    assert multi.source(None, "mongo").alias == "service"  # 只有一个 Mongo 源时可以省略
    with pytest.raises(ToolInputError, match="改用 run_mongo"):
        multi.source("service", "sql")
    with pytest.raises(ToolInputError, match="没有标识"):
        multi.source("nope")
    with pytest.raises(ToolInputError, match="没有选择数据源"):
        _ctx().source(None)
