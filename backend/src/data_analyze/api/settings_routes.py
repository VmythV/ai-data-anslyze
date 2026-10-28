"""模型渠道与数据源的配置接口。"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from data_analyze.api.deps import Services, db_session, services
from data_analyze.api.schemas import (
    ChannelIn,
    ChannelOut,
    DataSourceIn,
    DataSourceOut,
    DataSourceTestIn,
    DomainIn,
    DomainOut,
    InferCandidate,
    InferIn,
    InferOut,
    RelationIn,
    RelationOut,
    SchemaTable,
    TestResult,
)
from data_analyze.datasources.base import ConnectionConfig, DataSourceError
from data_analyze.datasources.federation import RESERVED_CATALOGS
from data_analyze.datasources.registry import create_driver
from data_analyze.datasources.sources import domain_datasource_ids
from data_analyze.relations.infer import MIN_MATCH_RATE
from data_analyze.security import mask
from data_analyze.store.models import DataDomain, DataSource, DomainDataSource, ModelChannel, Relation

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
Session = Annotated[AsyncSession, Depends(db_session)]
Svc = Annotated[Services, Depends(services)]

SUPPORTED_PROTOCOLS = {"anthropic", "openai"}


def _channel_out(channel: ModelChannel, svc: Services) -> ChannelOut:
    return ChannelOut(
        id=channel.id, name=channel.name, protocol=channel.protocol, base_url=channel.base_url,
        api_key_masked=mask(svc.box.decrypt(channel.api_key_enc)), model=channel.model,
        is_default=channel.is_default, supported=channel.protocol in SUPPORTED_PROTOCOLS,
    )


def _datasource_out(ds: DataSource) -> DataSourceOut:
    return DataSourceOut(
        id=ds.id, name=ds.name, alias=ds.alias or "", kind=ds.kind, host=ds.host, port=ds.port,
        username=ds.username, database=ds.database, options=ds.options or {}, description=ds.description,
        schema_refreshed_at=ds.schema_refreshed_at, schema_error=ds.schema_error,
    )


# ---------- 模型渠道 ----------


@router.get("/channels")
async def list_channels(session: Session, svc: Svc) -> list[ChannelOut]:
    rows = (await session.scalars(select(ModelChannel).order_by(ModelChannel.created_at))).all()
    return [_channel_out(c, svc) for c in rows]


@router.post("/channels")
async def create_channel(body: ChannelIn, session: Session, svc: Svc) -> ChannelOut:
    if not body.api_key:
        raise HTTPException(400, "API Key 不能为空")
    has_any = (await session.scalars(select(ModelChannel.id).limit(1))).first() is not None
    channel = ModelChannel(
        name=body.name, protocol=body.protocol, base_url=body.base_url.rstrip("/"),
        api_key_enc=svc.box.encrypt(body.api_key), model=body.model, is_default=body.is_default or not has_any,
    )
    if channel.is_default:
        await session.execute(update(ModelChannel).values(is_default=False))
    session.add(channel)
    await session.commit()
    return _channel_out(channel, svc)


@router.put("/channels/{channel_id}")
async def update_channel(channel_id: str, body: ChannelIn, session: Session, svc: Svc) -> ChannelOut:
    channel = await session.get(ModelChannel, channel_id)
    if channel is None:
        raise HTTPException(404, "渠道不存在")
    if body.is_default and not channel.is_default:
        await session.execute(update(ModelChannel).values(is_default=False))
    channel.name, channel.protocol, channel.model = body.name, body.protocol, body.model
    channel.base_url = body.base_url.rstrip("/")
    channel.is_default = body.is_default or channel.is_default
    if body.api_key:
        channel.api_key_enc = svc.box.encrypt(body.api_key)
    await session.commit()
    return _channel_out(channel, svc)


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str, session: Session) -> dict[str, bool]:
    channel = await session.get(ModelChannel, channel_id)
    if channel is not None:
        await session.delete(channel)
        await session.commit()
    return {"ok": True}


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: str, session: Session, svc: Svc) -> TestResult:
    channel = await session.get(ModelChannel, channel_id)
    if channel is None:
        raise HTTPException(404, "渠道不存在")
    key = svc.box.decrypt(channel.api_key_enc)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if channel.protocol == "anthropic":
                resp = await client.post(
                    f"{channel.base_url}/v1/messages",
                    headers={"x-api-key": key, "authorization": f"Bearer {key}", "anthropic-version": "2023-06-01"},
                    json={"model": channel.model, "max_tokens": 16, "messages": [{"role": "user", "content": "ping"}]},
                )
            else:
                # Codex 引擎只走 Responses API
                resp = await client.post(
                    f"{channel.base_url}/responses",
                    headers={"authorization": f"Bearer {key}"},
                    json={"model": channel.model, "max_output_tokens": 16, "input": "ping"},
                )
    except httpx.HTTPError as exc:
        return TestResult(ok=False, message=f"请求失败：{exc!r}")
    if resp.status_code >= 400:
        return TestResult(ok=False, message=f"HTTP {resp.status_code}：{resp.text[:300]}")
    return TestResult(ok=True, message=f"连接成功，模型 {channel.model} 可用")


# ---------- 数据源 ----------


async def _check_alias(session: AsyncSession, alias: str, exclude_id: str | None = None) -> None:
    if alias in RESERVED_CATALOGS:
        raise HTTPException(400, f"标识「{alias}」是联邦查询引擎的保留名，请换一个")
    query = select(DataSource.id).where(DataSource.alias == alias)
    if exclude_id:
        query = query.where(DataSource.id != exclude_id)
    if (await session.scalars(query)).first() is not None:
        raise HTTPException(400, f"标识「{alias}」已被其他数据源使用")


def _later(coro) -> None:  # noqa: ANN001
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _sync_federation(svc: Services, datasource_id: str, old_alias: str | None = None) -> None:
    if not svc.federation.enabled:
        return
    if old_alias:
        await svc.federation.drop(old_alias)
    async with svc.db.sessions() as session:
        ds = await session.get(DataSource, datasource_id)
    if ds is None:
        return
    try:
        await svc.federation.sync(ds)
    except DataSourceError as exc:
        logger.warning("federation sync %s failed: %s", ds.alias, exc)


def _refresh_later(svc: Services, datasource_id: str, old_alias: str | None = None) -> None:
    # 表结构缓存、联邦查询 catalog 都在后台刷新，不拖慢保存
    _later(svc.catalog.refresh_quietly(datasource_id))
    _later(_sync_federation(svc, datasource_id, old_alias))


_background: set[asyncio.Task[None]] = set()


@router.get("/datasources")
async def list_datasources(session: Session) -> list[DataSourceOut]:
    rows = (await session.scalars(select(DataSource).order_by(DataSource.created_at))).all()
    return [_datasource_out(d) for d in rows]


@router.post("/datasources")
async def create_datasource(body: DataSourceIn, session: Session, svc: Svc) -> DataSourceOut:
    await _check_alias(session, body.alias)
    ds = DataSource(
        name=body.name, alias=body.alias, kind=body.kind, host=body.host, port=body.port, username=body.username,
        password_enc=svc.box.encrypt(body.password), database=body.database, options=body.options,
        description=body.description,
    )
    session.add(ds)
    await session.commit()
    _refresh_later(svc, ds.id)
    return _datasource_out(ds)


@router.put("/datasources/{datasource_id}")
async def update_datasource(datasource_id: str, body: DataSourceIn, session: Session, svc: Svc) -> DataSourceOut:
    ds = await session.get(DataSource, datasource_id)
    if ds is None:
        raise HTTPException(404, "数据源不存在")
    await _check_alias(session, body.alias, datasource_id)
    old_alias = ds.alias if ds.alias != body.alias else None
    ds.name, ds.alias, ds.kind, ds.host, ds.port = body.name, body.alias, body.kind, body.host, body.port
    ds.username, ds.database, ds.description, ds.options = body.username, body.database, body.description, body.options
    if body.password:
        ds.password_enc = svc.box.encrypt(body.password)
    await session.commit()
    await svc.drivers.evict(datasource_id)
    _refresh_later(svc, ds.id, old_alias)
    return _datasource_out(ds)


@router.delete("/datasources/{datasource_id}")
async def delete_datasource(datasource_id: str, session: Session, svc: Svc) -> dict[str, bool]:
    ds = await session.get(DataSource, datasource_id)
    if ds is not None:
        alias = ds.alias
        await session.delete(ds)
        await session.commit()
        await svc.drivers.evict(datasource_id)
        if alias:
            _later(svc.federation.drop(alias))
    return {"ok": True}


@router.post("/datasources/test")
async def test_datasource(body: DataSourceTestIn, session: Session, svc: Svc) -> TestResult:
    password = body.password
    if not password and body.id:
        existing = await session.get(DataSource, body.id)
        if existing is not None:
            password = svc.box.decrypt(existing.password_enc)
    driver = create_driver(
        ConnectionConfig(kind=body.kind, host=body.host, port=body.port, username=body.username,
                         password=password, database=body.database, options=body.options)
    )
    try:
        info = await driver.test()
        tables = await driver.list_tables()
        unit = "个集合" if driver.language == "mongo" else "张表"
        return TestResult(ok=True, message=f"连接成功：{info}，共 {len(tables)} {unit}")
    except DataSourceError as exc:
        return TestResult(ok=False, message=str(exc))
    finally:
        await driver.close()


@router.get("/datasources/{datasource_id}/schema")
async def datasource_schema(datasource_id: str, svc: Svc) -> list[SchemaTable]:
    tables = await svc.catalog.tables(datasource_id)
    return [SchemaTable(name=name, **info) for name, info in tables.items()]


@router.post("/datasources/{datasource_id}/refresh-schema")
async def refresh_schema(datasource_id: str, svc: Svc) -> TestResult:
    try:
        tables, columns = await svc.catalog.refresh(datasource_id)
    except DataSourceError as exc:
        return TestResult(ok=False, message=str(exc))
    return TestResult(ok=True, message=f"已刷新：{tables} 张表、{columns} 个字段")


# ---------- 数据域 ----------


async def _domain_out(session: AsyncSession, domain: DataDomain) -> DomainOut:
    ids = await domain_datasource_ids(session, domain.id)
    return DomainOut(id=domain.id, name=domain.name, description=domain.description, datasource_ids=ids)


async def _set_domain_sources(session: AsyncSession, domain_id: str, ids: list[str]) -> None:
    existing = set((await session.scalars(select(DataSource.id).where(DataSource.id.in_(ids)))).all())
    missing = set(ids) - existing
    if missing:
        raise HTTPException(400, "部分数据源不存在")
    await session.execute(delete(DomainDataSource).where(DomainDataSource.domain_id == domain_id))
    session.add_all(DomainDataSource(domain_id=domain_id, datasource_id=i) for i in dict.fromkeys(ids))


@router.get("/domains")
async def list_domains(session: Session) -> list[DomainOut]:
    rows = (await session.scalars(select(DataDomain).order_by(DataDomain.created_at))).all()
    return [await _domain_out(session, d) for d in rows]


@router.post("/domains")
async def create_domain(body: DomainIn, session: Session) -> DomainOut:
    domain = DataDomain(name=body.name, description=body.description)
    session.add(domain)
    await session.flush()
    await _set_domain_sources(session, domain.id, body.datasource_ids)
    await session.commit()
    return await _domain_out(session, domain)


@router.put("/domains/{domain_id}")
async def update_domain(domain_id: str, body: DomainIn, session: Session) -> DomainOut:
    domain = await session.get(DataDomain, domain_id)
    if domain is None:
        raise HTTPException(404, "数据域不存在")
    domain.name, domain.description = body.name, body.description
    await _set_domain_sources(session, domain.id, body.datasource_ids)
    await session.commit()
    return await _domain_out(session, domain)


@router.delete("/domains/{domain_id}")
async def delete_domain(domain_id: str, session: Session) -> dict[str, bool]:
    domain = await session.get(DataDomain, domain_id)
    if domain is not None:
        await session.delete(domain)
        await session.commit()
    return {"ok": True}


# ---------- 关联关系 ----------


def _relation_out(r: Relation) -> RelationOut:
    return RelationOut(
        id=r.id, left_datasource_id=r.left_datasource_id, left_table=r.left_table, left_column=r.left_column,
        right_datasource_id=r.right_datasource_id, right_table=r.right_table, right_column=r.right_column,
        cardinality=r.cardinality, transform=r.transform, description=r.description, source=r.source,
        status=r.status, match_rate=r.match_rate, created_at=r.created_at,
    )


async def _check_relation(session: AsyncSession, body: RelationIn) -> None:
    for ds_id in (body.left_datasource_id, body.right_datasource_id):
        if await session.get(DataSource, ds_id) is None:
            raise HTTPException(400, "数据源不存在")
    if (body.left_datasource_id, body.left_table, body.left_column) == (
        body.right_datasource_id, body.right_table, body.right_column
    ):
        raise HTTPException(400, "关联的两端不能是同一个字段")


@router.get("/relations")
async def list_relations(session: Session) -> list[RelationOut]:
    rows = (await session.scalars(select(Relation).order_by(Relation.created_at))).all()
    return [_relation_out(r) for r in rows]


@router.post("/relations")
async def create_relation(body: RelationIn, session: Session) -> RelationOut:
    await _check_relation(session, body)
    relation = Relation(**body.model_dump(), source="manual", status="confirmed")
    session.add(relation)
    await session.commit()
    return _relation_out(relation)


@router.put("/relations/{relation_id}")
async def update_relation(relation_id: str, body: RelationIn, session: Session) -> RelationOut:
    relation = await session.get(Relation, relation_id)
    if relation is None:
        raise HTTPException(404, "关联不存在")
    await _check_relation(session, body)
    for key, value in body.model_dump().items():
        setattr(relation, key, value)
    await session.commit()
    return _relation_out(relation)


@router.post("/relations/infer")
async def infer_relations(body: InferIn, svc: Svc) -> InferOut:
    """推断跨数据源关联：匹配率达标的存为「待审核」。"""
    candidates = await svc.inference.infer(body.datasource_ids or None)
    return InferOut(
        evaluated=len(candidates),
        created=sum(1 for c in candidates if c.match_rate is not None and c.match_rate >= MIN_MATCH_RATE),
        candidates=[
            InferCandidate(left=c.left.label(), right=c.right.label(), reason=c.reason, match_rate=c.match_rate,
                           sampled=c.sampled, error=c.error)
            for c in candidates
        ],
    )


@router.get("/federation")
async def federation_status(svc: Svc) -> dict:
    return await svc.federation.status()


@router.post("/relations/{relation_id}/confirm")
async def confirm_relation(relation_id: str, session: Session) -> RelationOut:
    """采纳 AI 推断的关联（待审核 → 已确认）。"""
    relation = await session.get(Relation, relation_id)
    if relation is None:
        raise HTTPException(404, "关联不存在")
    relation.status = "confirmed"
    await session.commit()
    return _relation_out(relation)


@router.delete("/relations/{relation_id}")
async def delete_relation(relation_id: str, session: Session) -> dict[str, bool]:
    relation = await session.get(Relation, relation_id)
    if relation is not None:
        await session.delete(relation)
        await session.commit()
    return {"ok": True}
