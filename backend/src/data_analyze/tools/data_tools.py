"""取数工具：找表、看表结构、执行只读 SQL / Mongo 聚合。所有工具都可以指定数据源（datasource = 标识）。"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlglot import exp

from data_analyze.datasources.base import DataSourceError, QueryResult
from data_analyze.datasources.mongodb import UnsafePipelineError, parse_pipeline
from data_analyze.datasources.sources import SourceHandle
from data_analyze.datasources.sql_guard import (
    UnsafeSqlError,
    check_readonly,
    federated_violations,
    referenced_databases,
    with_row_limit,
)
from data_analyze.tools.base import ToolContext, ToolInputError, ToolOutput, ToolSpec
from data_analyze.tools.tabular import preview_text, to_jsonable, write_parquet

#: 回给模型的预览行数；完整结果在文件里
MODEL_PREVIEW_ROWS = 30
#: 推给页面表格的行数
UI_PREVIEW_ROWS = 200
_SAFE_STEM = re.compile(r"[^0-9A-Za-z_\-一-鿿]+")

_DATASOURCE_PARAM = {"type": "string", "description": "数据源标识；本会话只有一个数据源时可省略"}


class _Federated:
    """联邦查询结果的「数据源」：文件名、表格徽标用。"""

    alias = "federated"
    language = "sql"


FEDERATED = _Federated()


def same_connection_peers(ctx: ToolContext, handle: SourceHandle) -> list[SourceHandle]:
    """与 handle 同一连接、可以在一条查询里直接引用的已选数据源（含自身）。"""
    key = handle.connection_key
    if key is None:
        return [handle]
    return [s for s in ctx.sources.values() if s.connection_key == key]


def data_tools(ctx: ToolContext) -> list[ToolSpec]:
    languages = {s.driver.language for s in ctx.sources.values()}

    async def list_tables(args: dict[str, Any]) -> ToolOutput:
        handle = ctx.source(args.get("datasource"))
        tables = await handle.driver.list_tables()
        if not tables:
            return ToolOutput(f"数据源 {handle.alias} 里没有可访问的表")
        unit = "集合" if handle.driver.language == "mongo" else "表"
        lines = [f"数据源 {handle.alias}（{handle.name}，{handle.kind}）共 {len(tables)} 个{unit}（名称 | 约行数 | 说明）："]
        lines += [f"- {t.name} | {t.row_estimate if t.row_estimate else '?'} | {t.comment}" for t in tables]
        return ToolOutput("\n".join(lines))

    async def describe_table(args: dict[str, Any]) -> ToolOutput:
        handle = ctx.source(args.get("datasource"))
        names = args.get("tables")
        if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
            raise ToolInputError("tables 需要是非空的表名数组")
        parts = []
        for name in names[:8]:
            try:
                detail = await handle.driver.describe_table(name)
            except DataSourceError as exc:
                parts.append(f"## {handle.alias}.{name}\n错误：{exc}")
                continue
            t = detail.table
            lines = [f"## {handle.alias} · {t.name}（约 {t.row_estimate} 行）{t.comment}",
                     "字段 | 类型 | 键 | 说明 | 常见取值"]
            for c in detail.columns:
                top = "、".join(f"{to_jsonable(v)}({n})" for v, n in c.top_values) if c.top_values else ""
                lines.append(f"{c.name} | {c.type}{'' if c.nullable else ' NOT NULL'} | {c.key} | {c.comment} | {top}")
            if detail.indexes:
                lines.append("索引：" + "；".join(detail.indexes))
            if detail.sample_rows:
                columns = list(dict.fromkeys(k for row in detail.sample_rows for k in row))
                rows = [tuple(row.get(c) for c in columns) for row in detail.sample_rows]
                lines.append("样例数据：\n" + preview_text(columns, rows, len(rows)))
            lines.append(detail.note or "（常见取值基于抽样统计，不代表全表分布）")
            parts.append("\n".join(lines))
        return ToolOutput("\n\n".join(parts))

    async def search_schema(args: dict[str, Any]) -> ToolOutput:
        keywords = args.get("keywords")
        if isinstance(keywords, str):
            keywords = re.split(r"[\s,，、]+", keywords)
        if not isinstance(keywords, list) or not keywords:
            raise ToolInputError("keywords 需要是关键词数组")
        wanted = args.get("datasources") or list(ctx.sources)
        handles = [ctx.source(a) for a in wanted]
        hits = await ctx.catalog.search([h.id for h in handles], [str(k) for k in keywords])
        if not hits:
            return ToolOutput("没有找到匹配的表或字段。换个说法（中文业务词、英文字段名）再试，或用 list_tables 浏览。")
        alias_of = {h.id: h.alias for h in handles}
        lines = [f"按相关度排列的前 {len(hits)} 张表（数据源 · 表 | 约行数 | 说明；缩进为命中的字段）："]
        for hit in hits:
            rows = hit.row_estimate if hit.row_estimate else "?"
            lines.append(f"- {alias_of[hit.datasource_id]} · {hit.table} | {rows} | {hit.comment}")
            lines += [f"    {name} {dtype} {comment}".rstrip() for name, dtype, comment in hit.columns]
        lines.append("（来自表结构缓存；写查询前仍需 describe_table 确认字段和取值）")
        return ToolOutput("\n".join(lines))

    async def run_sql(args: dict[str, Any]) -> ToolOutput:
        handle = ctx.source(args.get("datasource"), "sql")
        sql = args.get("sql")
        if not isinstance(sql, str):
            raise ToolInputError("sql 必须是字符串")
        dialect = handle.driver.dialect
        try:
            tree = check_readonly(sql, dialect)
        except UnsafeSqlError as exc:
            return ToolOutput(f"SQL 未执行：{exc}", is_error=True)
        peers = same_connection_peers(ctx, handle)
        allowed = {p.database.lower() for p in peers}
        outside = sorted(q for q in referenced_databases(tree, dialect) if q.lower() not in allowed)
        if outside:
            if len(peers) > 1:
                scope = "可以直接引用的库：" + "、".join(f"{p.database}（数据源 {p.alias}）" for p in peers)
            elif dialect == "postgres":
                scope = "PostgreSQL 不能跨 database 查询（同库内不同 schema 可以，写成 schema.表名）"
            else:
                scope = f"{handle.alias} 没有同一连接上的其他已选数据源"
            return ToolOutput(
                f"SQL 未执行：引用了库 {', '.join(outside)}，它不在本会话已选、且与 {handle.alias} 同一连接的数据源里。"
                f"{scope}。其他数据源的数据请分别查询，再在 run_python 里用 DuckDB/pandas 关联。",
                is_error=True,
            )
        settings = ctx.settings
        limited = with_row_limit(tree, settings.sql_max_rows, dialect)
        try:
            result = await handle.driver.query(limited, settings.sql_max_rows, settings.sql_timeout_seconds)
        except DataSourceError as exc:
            return ToolOutput(f"SQL 执行失败：{exc}", is_error=True)
        return _finish(handle, result, args, sql.strip())

    async def run_mongo(args: dict[str, Any]) -> ToolOutput:
        handle = ctx.source(args.get("datasource"), "mongo")
        collection = str(args.get("collection") or "").strip()
        if not collection:
            raise ToolInputError("collection 不能为空")
        try:
            pipeline = parse_pipeline(args.get("pipeline") or [])
        except UnsafePipelineError as exc:
            return ToolOutput(f"查询未执行：{exc}", is_error=True)
        settings = ctx.settings
        try:
            result = await handle.driver.aggregate(
                collection, pipeline, settings.sql_max_rows, settings.sql_timeout_seconds
            )
        except DataSourceError as exc:
            return ToolOutput(f"查询执行失败：{exc}", is_error=True)
        shown = json.dumps(args.get("pipeline") or [], ensure_ascii=False, indent=2)
        return _finish(handle, result, args, f"db.{collection}.aggregate({shown})")

    async def run_federated_sql(args: dict[str, Any]) -> ToolOutput:
        federation = ctx.federation
        if federation is None or not federation.enabled:
            raise ToolInputError("联邦查询引擎未启用，请分别查询后在 run_python 里关联")
        sql = args.get("sql")
        if not isinstance(sql, str):
            raise ToolInputError("sql 必须是字符串")
        try:
            tree = check_readonly(sql, "trino")
        except UnsafeSqlError as exc:
            return ToolOutput(f"SQL 未执行：{exc}", is_error=True)
        catalogs = {s.alias: (s.kind, s.database) for s in ctx.sources.values()}
        problems = federated_violations(tree, catalogs)
        if problems:
            return ToolOutput("SQL 未执行：" + "；".join(dict.fromkeys(problems)), is_error=True)
        used = {t.catalog for t in tree.find_all(exp.Table) if t.catalog}
        settings = ctx.settings
        limited = with_row_limit(tree, settings.sql_max_rows, "trino")
        try:
            result = await federation.query(
                [d for d in ctx.datasources if d.alias in used], limited, settings.sql_max_rows,
                settings.federated_timeout_seconds,
            )
        except DataSourceError as exc:
            return ToolOutput(f"联邦查询失败：{exc}", is_error=True)
        return _finish(FEDERATED, result, args, sql.strip())

    def _finish(handle: SourceHandle | _Federated, result: QueryResult, args: dict[str, Any], query_text: str) -> ToolOutput:
        settings = ctx.settings
        purpose = str(args.get("purpose") or "查询结果")[:80]
        index = ctx.next_index("sql")
        stem = _SAFE_STEM.sub("_", str(args.get("save_as") or "")).strip("_")[:40]
        relative = f"data/q{index:03d}_{handle.alias}{'_' + stem if stem else ''}.parquet"
        write_parquet(ctx.workspace / relative, result.columns, result.rows)
        ctx.emit(
            {
                "type": "table",
                "title": purpose,
                "datasource": handle.alias,
                "language": handle.language,
                "sql": query_text,
                "columns": result.columns,
                "rows": [[to_jsonable(v) for v in row] for row in result.rows[:UI_PREVIEW_ROWS]],
                "total_rows": len(result.rows),
                "truncated": result.truncated,
                "file": relative,
            }
        )
        head = f"查询完成（{handle.alias}）：{len(result.rows)} 行 × {len(result.columns)} 列，用时 {result.elapsed_ms} ms。"
        if result.truncated:
            head += f"\n注意：结果超过上限，只保留了前 {settings.sql_max_rows} 行。请改用聚合查询。"
        head += f"\n完整结果已保存为 {relative}（run_python 里用 pandas.read_parquet 读取，或作为 render_chart 的 data_file）。"
        if not result.rows:
            return ToolOutput(head + "\n结果为空。")
        shown = min(len(result.rows), MODEL_PREVIEW_ROWS)
        return ToolOutput(f"{head}\n前 {shown} 行：\n{preview_text(result.columns, result.rows, shown)}")

    specs = [
        ToolSpec(
            name="search_schema",
            description=(
                "按关键词在已选数据源的表名、字段名、注释里搜索相关的表（读的是表结构缓存，很快）。"
                "数据源或表比较多时，先用它找候选表，再用 describe_table 看细节。关键词中英文都可以，给多个同义词效果更好。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "keywords": {"type": "array", "items": {"type": "string"},
                                 "description": "关键词，如 [\"会员\", \"member\", \"等级\"]"},
                    "datasources": {"type": "array", "items": {"type": "string"},
                                    "description": "可选，只在这些数据源里搜；默认全部已选数据源"},
                },
                "required": ["keywords"],
                "additionalProperties": False,
            },
            handler=search_schema,
            max_result_chars=40_000,
        ),
        ToolSpec(
            name="list_tables",
            description="列出一个数据源的所有表（MongoDB 为集合），含说明和估算行数。",
            input_schema={
                "type": "object",
                "properties": {"datasource": _DATASOURCE_PARAM},
                "additionalProperties": False,
            },
            handler=list_tables,
            max_result_chars=60_000,
        ),
        ToolSpec(
            name="describe_table",
            description=(
                "查看表结构：字段类型、注释、索引、枚举型字段的常见取值和样例数据（MongoDB 为抽样推断的字段结构）。"
                "写查询前必须先看相关表的结构，不要猜字段名和取值。PostgreSQL 表名写成 schema.表名。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "datasource": _DATASOURCE_PARAM,
                    "tables": {"type": "array", "items": {"type": "string"}, "description": "表名，一次最多 8 张"},
                },
                "required": ["tables"],
                "additionalProperties": False,
            },
            handler=describe_table,
            max_result_chars=60_000,
        ),
    ]
    if "sql" in languages or not languages:
        specs.append(
            ToolSpec(
                name="run_sql",
                description=(
                    "在一个 SQL 类数据源（MySQL / PostgreSQL / ClickHouse）上执行一条只读查询，方言跟随该数据源。"
                    "与它同一连接的其他已选数据源可以在同一条 SQL 里用「库名.表名」直接关联（系统提示里列出了哪些库可以这样做）。"
                    "结果以表格展示给用户，完整结果保存为工作区 parquet 文件，你只会收到前 30 行预览。尽量在 SQL 里完成聚合。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "datasource": _DATASOURCE_PARAM,
                        "sql": {"type": "string", "description": "单条只读 SQL"},
                        "purpose": {"type": "string", "description": "一句话说明这次查询要回答什么，作为表格标题展示给用户"},
                        "save_as": {"type": "string", "description": "可选，结果文件名后缀，如 monthly_gmv"},
                    },
                    "required": ["sql", "purpose"],
                    "additionalProperties": False,
                },
                handler=run_sql,
                max_result_chars=40_000,
            )
        )
    federation = ctx.federation
    if federation is not None and federation.enabled and len(ctx.sources) >= 2:
        specs.append(
            ToolSpec(
                name="run_federated_sql",
                description=(
                    "联邦查询：用一条 SQL（Trino 方言）同时查询多个数据源并直接关联，适合不同引擎之间的明细级关联、"
                    "或两边数据量都大、没法先聚合再在 Python 里关联的情况。表名写成「数据源标识.schema.表名」，"
                    "写法见系统提示。比单库查询慢，能用 run_sql 在单个数据源（或同一连接）里完成的不要用它。"
                    "结果同样以表格展示、保存为 parquet，你只会收到前 30 行预览。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "单条只读 SQL（Trino 方言）"},
                        "purpose": {"type": "string", "description": "一句话说明这次查询要回答什么，作为表格标题展示给用户"},
                        "save_as": {"type": "string", "description": "可选，结果文件名后缀"},
                    },
                    "required": ["sql", "purpose"],
                    "additionalProperties": False,
                },
                handler=run_federated_sql,
                max_result_chars=40_000,
            )
        )
    if "mongo" in languages:
        specs.append(
            ToolSpec(
                name="run_mongo",
                description=(
                    "在 MongoDB 数据源上执行只读聚合管道（aggregate）。不允许 $out/$merge/$where/$function 等。"
                    "日期等特殊类型用扩展 JSON：{\"$date\": \"2025-01-01T00:00:00+08:00\"}。"
                    "结果会拍平成表格（嵌套字段变成 a.b 列、数组转成 JSON 文本），保存为 parquet，你只会收到前 30 行预览。"
                    "尽量在管道里用 $match / $group / $project 完成筛选和聚合。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "datasource": _DATASOURCE_PARAM,
                        "collection": {"type": "string", "description": "集合名"},
                        "pipeline": {"type": "array", "items": {"type": "object"}, "description": "聚合管道阶段数组"},
                        "purpose": {"type": "string", "description": "一句话说明这次查询要回答什么，作为表格标题展示给用户"},
                        "save_as": {"type": "string", "description": "可选，结果文件名后缀"},
                    },
                    "required": ["collection", "pipeline", "purpose"],
                    "additionalProperties": False,
                },
                handler=run_mongo,
                max_result_chars=40_000,
            )
        )
    return specs
