"""MongoDB 驱动（pymongo 异步客户端）。

- 「表」= 集合；字段结构由抽样文档推断（嵌套字段用 a.b.c 表示）
- 查询只走 aggregate，管道先过 check_pipeline：禁止写入（$out/$merge）和执行 JS 的操作符
- 结果拍平成表格：嵌套对象展开成 a.b 列，数组转成 JSON 字符串
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from datetime import datetime
from typing import Any

from bson import ObjectId, json_util
from pymongo import AsyncMongoClient
from pymongo.errors import ExecutionTimeout, PyMongoError

from data_analyze.datasources.base import (
    CatalogColumn,
    ColumnInfo,
    ConnectionConfig,
    DataSourceError,
    Driver,
    QueryResult,
    TableDetail,
    TableInfo,
)

SAMPLE_DOCS = 200
_SAMPLE_ROWS = 3
_TOP_VALUES_LIMIT = 20
_MAX_CATALOG_COLLECTIONS = 100
#: 写入、跨库、执行 JS、窥探服务器状态的阶段与操作符
FORBIDDEN_OPERATORS = {
    "$out", "$merge", "$function", "$accumulator", "$where", "$currentOp", "$listSessions",
    "$listLocalSessions", "$planCacheStats", "$listSearchIndexes", "$changeStream",
}
_FOREIGN_STAGES = {"$lookup", "$graphLookup", "$unionWith"}


class UnsafePipelineError(ValueError):
    pass


def parse_pipeline(raw: Any) -> list[dict[str, Any]]:
    """接受数组或 JSON 字符串；支持扩展 JSON（{"$date": "2025-01-01T00:00:00Z"}、{"$oid": ...}）。"""
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    try:
        pipeline = json_util.loads(text)
    except (ValueError, TypeError) as exc:
        raise UnsafePipelineError(f"pipeline 不是合法的 JSON：{exc}") from exc
    if not isinstance(pipeline, list) or not all(isinstance(s, dict) and len(s) == 1 for s in pipeline):
        raise UnsafePipelineError("pipeline 必须是阶段数组，每个阶段是只有一个键的对象，如 [{\"$match\": {...}}]")
    check_pipeline(pipeline)
    return pipeline


def check_pipeline(node: Any) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in FORBIDDEN_OPERATORS:
                raise UnsafePipelineError(f"不允许使用 {key}")
            if key in _FOREIGN_STAGES:
                spec = value if isinstance(value, dict) else {"coll": value}
                target = spec.get("from", spec.get("coll"))
                # 只能引用同一个库里的集合：{from: {db, coll}} 这种跨库写法不放行
                if not isinstance(target, str):
                    raise UnsafePipelineError(f"{key} 只能引用当前库里的集合（from 写集合名）")
            check_pipeline(value)
    elif isinstance(node, list):
        for item in node:
            check_pipeline(item)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "double"
    if isinstance(value, str):
        return "string"
    if isinstance(value, datetime):
        return "date"
    if isinstance(value, ObjectId):
        return "objectId"
    if isinstance(value, list):
        inner = {_type_name(v) for v in value[:20]} - {"null"}
        return f"array<{'|'.join(sorted(inner)) or '?'}>"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def flatten(doc: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in doc.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            out.update(flatten(value, f"{path}."))
        elif isinstance(value, list):
            out[path] = json.dumps(value, ensure_ascii=False, default=_json_default)
        elif isinstance(value, ObjectId):
            out[path] = str(value)
        else:
            out[path] = value
    return out


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def infer_fields(docs: list[dict[str, Any]]) -> list[ColumnInfo]:
    """抽样文档 → 字段列表：路径、类型、出现率、低基数字段的取值分布。"""
    stats: dict[str, dict[str, Any]] = {}
    for doc in docs:
        for path, value in _walk(doc):
            entry = stats.setdefault(path, {"types": Counter(), "count": 0, "values": Counter()})
            entry["types"][_type_name(value)] += 1
            entry["count"] += 1
            if isinstance(value, (str, bool, int)) and not isinstance(value, ObjectId):
                entry["values"][value] += 1
    total = max(len(docs), 1)
    columns = []
    for path, entry in stats.items():
        presence = entry["count"] / total
        values = entry["values"]
        top = values.most_common(_TOP_VALUES_LIMIT) if 0 < len(values) <= _TOP_VALUES_LIMIT else []
        columns.append(
            ColumnInfo(
                name=path,
                type="|".join(t for t, _ in entry["types"].most_common()),
                nullable=presence < 1 or "null" in entry["types"],
                key="PRI" if path == "_id" else "",
                comment=f"抽样出现率 {presence:.0%}",
                top_values=top,
            )
        )
    return columns


def _walk(doc: dict[str, Any], prefix: str = ""):  # noqa: ANN202
    for key, value in doc.items():
        path = f"{prefix}{key}"
        yield path, value
        if isinstance(value, dict):
            yield from _walk(value, f"{path}.")
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value[:5]):
            # 对象数组：子字段记作 messages[].role
            for item in value[:5]:
                yield from _walk(item, f"{path}[].")


# ---------- 给 Trino 的表结构（联邦查询用） ----------


class _Shape:
    """抽样文档里某个字段的形状：出现过的标量类型、对象子字段、数组元素。"""

    def __init__(self) -> None:
        self.scalars: set[str] = set()
        self.children: dict[str, _Shape] = {}
        self.element: _Shape | None = None

    def add(self, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                self.children.setdefault(key, _Shape()).add(child)
        elif isinstance(value, list):
            self.element = self.element or _Shape()
            for item in value[:50]:
                self.element.add(item)
        elif isinstance(value, bool):
            self.scalars.add("boolean")
        elif isinstance(value, int):
            self.scalars.add("bigint")
        elif isinstance(value, float):
            self.scalars.add("double")
        elif isinstance(value, datetime):
            self.scalars.add("timestamp(3)")
        elif isinstance(value, ObjectId):
            self.scalars.add("ObjectId")
        else:
            self.scalars.add("varchar")

    def trino_type(self) -> str | None:
        """出现过多种形状时退化成 varchar；只出现过 null 的字段返回 None（不声明）。"""
        kinds = len(self.scalars) + bool(self.children) + bool(self.element)
        if kinds == 0:
            return None
        if kinds > 1 and not self.scalars <= {"bigint", "double"}:
            return "varchar"
        if self.children:
            fields = [(k, c.trino_type()) for k, c in self.children.items()]
            inner = ", ".join(f'"{k}" {t}' for k, t in fields if t)
            return f"row({inner})" if inner else None
        if self.element is not None:
            inner = self.element.trino_type()
            return f"array({inner})" if inner else None
        if self.scalars == {"bigint", "double"}:
            return "double"
        return next(iter(self.scalars))


def trino_schema(table: str, docs: list[dict[str, Any]]) -> dict[str, Any]:
    root = _Shape()
    for doc in docs:
        root.add(doc)
    fields = [{"name": "_id", "type": "ObjectId", "hidden": True}]
    for name, shape in root.children.items():
        if name == "_id":
            continue
        trino_type = shape.trino_type()
        if trino_type:
            fields.append({"name": name, "type": trino_type, "hidden": False})
    return {"table": table, "fields": fields}


class MongoDriver(Driver):
    kind = "mongodb"
    language = "mongo"

    def __init__(self, config: ConnectionConfig) -> None:
        super().__init__(config)
        self._client: AsyncMongoClient | None = None

    def _db(self):  # noqa: ANN202
        if self._client is None:
            c = self.config
            kwargs: dict[str, Any] = {
                "host": c.host, "port": c.port, "serverSelectionTimeoutMS": 10_000, "tz_aware": False,
                "readPreference": "secondaryPreferred", "appname": "data-analyze",
            }
            if c.username:
                kwargs |= {"username": c.username, "password": c.password,
                           "authSource": c.options.get("auth_source") or c.database}
            if c.options.get("replica_set"):
                kwargs["replicaSet"] = c.options["replica_set"]
            if c.options.get("tls"):
                kwargs["tls"] = True
            self._client = AsyncMongoClient(**kwargs)
        return self._client[self.config.database]

    async def _run(self, coro, timeout_seconds: int = 30):  # noqa: ANN001, ANN202
        try:
            return await asyncio.wait_for(coro, timeout_seconds + 5)
        except (TimeoutError, ExecutionTimeout) as exc:
            raise DataSourceError(f"查询超时（>{timeout_seconds} 秒），请缩小范围或加 $match 条件") from exc
        except PyMongoError as exc:
            raise DataSourceError(f"MongoDB 错误：{str(exc)[:500]}") from exc

    async def test(self) -> str:
        db = self._db()
        try:
            info = await self._run(db.command("buildInfo"))
        except DataSourceError:
            info = {}
        return f"MongoDB {info.get('version', '?')}，库 {self.config.database}"

    async def list_tables(self) -> list[TableInfo]:
        db = self._db()
        # _schema 是平台给联邦查询（Trino）写的结构信息，不是业务数据
        names = sorted(n for n in await self._run(db.list_collection_names()) if not n.startswith(("system.", "_schema")))
        out = []
        for name in names:
            count = await self._run(db[name].estimated_document_count())
            out.append(TableInfo(name=name, comment="集合", row_estimate=count))
        return out

    async def _sample(self, collection: str, size: int) -> list[dict[str, Any]]:
        cursor = await self._db()[collection].aggregate([{"$sample": {"size": size}}], maxTimeMS=20_000)
        return await self._run(cursor.to_list(size))

    async def catalog_columns(self) -> list[CatalogColumn]:
        out = []
        for table in (await self.list_tables())[:_MAX_CATALOG_COLLECTIONS]:
            for col in infer_fields(await self._sample(table.name, 50)):
                out.append(CatalogColumn(table=table.name, name=col.name, type=col.type, comment=""))
        return out

    async def describe_table(self, table: str) -> TableDetail:
        db = self._db()
        if table not in await self._run(db.list_collection_names()):
            raise DataSourceError(f"集合 {table} 不存在")
        docs = await self._sample(table, SAMPLE_DOCS)
        count = await self._run(db[table].estimated_document_count())
        indexes = await self._run(db[table].index_information())
        return TableDetail(
            table=TableInfo(name=table, comment="集合", row_estimate=count),
            columns=infer_fields(docs),
            sample_rows=[flatten(d) for d in docs[:_SAMPLE_ROWS]],
            indexes=[f"{name}({', '.join(k for k, _ in spec['key'])})" for name, spec in indexes.items()],
            note=(
                f"字段结构由随机抽样的 {len(docs)} 个文档推断，不一定完整；messages[].role 表示数组 messages 中对象的 role 字段。"
                "查询用 run_mongo 的聚合管道；日期比较用扩展 JSON，如 {\"$gte\": {\"$date\": \"2025-01-01T00:00:00+08:00\"}}。"
            ),
        )

    async def sample_values(self, table: str, column: str, limit: int) -> list[Any]:
        if "[]" in column:
            raise DataSourceError("数组内的字段不参与关联推断")
        cursor = await self._db()[table].aggregate(
            [{"$match": {column: {"$ne": None, "$exists": True}}}, {"$group": {"_id": f"${column}"}},
             {"$limit": int(limit)}],
            maxTimeMS=20_000,
        )
        docs = await self._run(cursor.to_list(limit))
        return [d["_id"] for d in docs if not isinstance(d["_id"], (dict, list))]

    async def count_matches(self, table: str, column: str, values: list[Any]) -> int:
        if not values:
            return 0
        # 文档里同一字段可能是数字也可能是字符串：两种形式都去匹配
        candidates: list[Any] = []
        for v in values:
            text = str(v)
            candidates.append(text)
            if text.lstrip("-").isdigit():
                candidates.append(int(text))
        cursor = await self._db()[table].aggregate(
            [{"$match": {column: {"$in": candidates}}}, {"$group": {"_id": {"$toString": f"${column}"}}},
             {"$count": "n"}],
            maxTimeMS=20_000,
        )
        docs = await self._run(cursor.to_list(1))
        return int(docs[0]["n"]) if docs else 0

    async def write_trino_schema(self, sample_size: int = SAMPLE_DOCS) -> int:
        """把各集合（抽样推断）的结构写进 _schema 集合，Trino 按它解析字段。

        Trino 自己只看一个文档推断结构，遇到可空字段就会漏列。需要账号对 _schema 集合有写权限
        （见 deploy/mock/mongo/init/01-load.sh 里的 trinoSchemaWriter 角色）。返回写入的集合数。
        """
        db = self._db()
        names = [n for n in await self._run(db.list_collection_names()) if not n.startswith(("system.", "_schema"))]
        written = 0
        for name in names[:_MAX_CATALOG_COLLECTIONS]:
            schema = trino_schema(name, await self._sample(name, sample_size))
            await self._run(db["_schema"].replace_one({"table": name}, schema, upsert=True))
            written += 1
        return written

    async def aggregate(
        self, collection: str, pipeline: list[dict[str, Any]], max_rows: int, timeout_seconds: int
    ) -> QueryResult:
        db = self._db()
        if collection not in await self._run(db.list_collection_names()):
            raise DataSourceError(f"集合 {collection} 不存在")
        started = time.monotonic()
        limited = [*pipeline, {"$limit": max_rows + 1}]
        cursor = await self._run(
            db[collection].aggregate(limited, maxTimeMS=timeout_seconds * 1000, allowDiskUse=False), timeout_seconds
        )
        docs = await self._run(cursor.to_list(max_rows + 1), timeout_seconds)
        flat = [flatten(d) for d in docs[:max_rows]]
        columns: list[str] = []
        for row in flat:
            columns.extend(k for k in row if k not in columns)
        return QueryResult(
            columns=columns,
            rows=[tuple(row.get(c) for c in columns) for row in flat],
            truncated=len(docs) > max_rows,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
