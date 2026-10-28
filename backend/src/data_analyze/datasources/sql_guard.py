"""只读 SQL 校验。数据库账号本身也应当只读 —— 这里是第二道防线，也给 AI 更清楚的报错。"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

_READ_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)
_FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.TruncateTable, exp.Command, exp.Into, exp.Lock, exp.Set, exp.Use,
)
#: 能读写文件、访问外部系统、睡眠、拖垮或干扰服务器的函数（含 ClickHouse 表函数）
_FORBIDDEN_FUNCTIONS = {
    # MySQL
    "LOAD_FILE", "SLEEP", "BENCHMARK", "GET_LOCK", "RELEASE_LOCK",
    # PostgreSQL
    "PG_SLEEP", "PG_SLEEP_FOR", "PG_SLEEP_UNTIL", "PG_READ_FILE", "PG_READ_BINARY_FILE", "PG_LS_DIR",
    "PG_STAT_FILE", "LO_IMPORT", "LO_EXPORT", "LO_GET", "DBLINK", "DBLINK_EXEC", "DBLINK_CONNECT",
    "PG_TERMINATE_BACKEND", "PG_CANCEL_BACKEND", "SET_CONFIG", "NEXTVAL", "SETVAL", "PG_ADVISORY_LOCK",
    "QUERY_TO_XML", "PG_RELOAD_CONF",
    # ClickHouse 表函数：读文件、访问网络或其他数据库
    "URL", "FILE", "S3", "S3CLUSTER", "MYSQL", "POSTGRESQL", "REMOTE", "REMOTESECURE", "HDFS", "JDBC",
    "ODBC", "EXECUTABLE", "INPUT", "CLUSTER", "CLUSTERALLREPLICAS", "AZUREBLOBSTORAGE", "GCS", "MONGODB",
    "REDIS", "SQLITE", "URLCLUSTER", "DICTGET", "FILECLUSTER", "SLEEPEACHROW",
}


class UnsafeSqlError(ValueError):
    pass


def check_readonly(sql: str, dialect: str) -> exp.Expression:
    """返回解析后的语句；不是单条只读查询就抛 UnsafeSqlError。"""
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise UnsafeSqlError("SQL 为空")
    try:
        statements = [s for s in sqlglot.parse(text, read=dialect) if s is not None]
    except ParseError as exc:
        raise UnsafeSqlError(f"SQL 解析失败：{exc}") from exc
    if len(statements) != 1:
        raise UnsafeSqlError("一次只能执行一条 SQL")
    tree = statements[0]
    if not isinstance(tree, _READ_ROOTS):
        raise UnsafeSqlError(f"只允许查询语句（SELECT / WITH），收到的是 {tree.key.upper()}")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN):
            raise UnsafeSqlError(f"查询中不允许出现 {node.key.upper()}")
        name = _function_name(node)
        if name and name in _FORBIDDEN_FUNCTIONS:
            raise UnsafeSqlError(f"不允许调用函数 {name.lower()}")
    if tree.args.get("locks"):
        raise UnsafeSqlError("查询中不允许加锁（FOR UPDATE / LOCK IN SHARE MODE）")
    return tree


def _function_name(node: exp.Expression) -> str | None:
    if isinstance(node, exp.Anonymous):
        return str(node.name).upper()
    if isinstance(node, exp.Func):
        return node.sql_name().upper()
    return None


def referenced_databases(tree: exp.Expression, dialect: str) -> set[str]:
    """语句引用到的「库」限定符。

    MySQL / ClickHouse：`库.表` 里的库；PostgreSQL：`库.schema.表` 里的库（schema 不算，同库内随意引用）。
    CTE 名称、子查询别名不带限定符，不会出现在结果里。
    """
    out = set()
    for table in tree.find_all(exp.Table):
        qualifier = table.catalog if dialect == "postgres" else table.db
        if qualifier:
            out.add(qualifier)
    return out


def federated_violations(tree: exp.Expression, catalogs: dict[str, tuple[str, str]]) -> list[str]:
    """联邦 SQL 的引用检查。catalogs：可用的 {标识: (数据源类型, 库名)}。返回问题列表，空表示通过。

    - 每张表都要写成「标识.schema.表名」（CTE 名除外）
    - 只能引用本会话已选的数据源
    - MySQL / ClickHouse / MongoDB 的 schema 必须是该数据源自己的库：catalog 背后的连接能看到同实例的其他库，
      但那些库没有被选进会话
    """
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    problems = []
    for table in tree.find_all(exp.Table):
        name, schema, catalog = table.name, table.db, table.catalog
        if not catalog:
            if not schema and name in ctes:
                continue
            problems.append(f"表 {table.sql(dialect='trino')} 没写全，联邦查询里要写成「数据源标识.schema.表名」")
            continue
        if catalog not in catalogs:
            problems.append(f"{catalog} 不是本会话已选的数据源（可用：{', '.join(catalogs)}）")
            continue
        kind, database = catalogs[catalog]
        if kind != "postgresql" and schema.lower() != database.lower() and schema.lower() != "information_schema":
            problems.append(f"{catalog} 的 schema 应写库名 {database}（{catalog}.{database}.表名），而不是 {schema}")
    return problems


def _quote_non_ascii(tree: exp.Expression) -> exp.Expression:
    """中文等非 ASCII 标识符（常见于列别名）加引号：MySQL 接受裸写，ClickHouse 不接受。"""
    tree = tree.copy()
    for ident in tree.find_all(exp.Identifier):
        if not ident.quoted and not str(ident.this).isascii():
            ident.set("quoted", True)
    return tree


def with_row_limit(tree: exp.Expression, max_rows: int, dialect: str) -> str:
    """多取一行用来判断是否被截断。原语句的 LIMIT 更小时保持不变。"""
    tree = _quote_non_ascii(tree)
    cap = max_rows + 1
    if isinstance(tree, exp.Select):
        limit = tree.args.get("limit")
        current = _literal_int(limit.expression) if limit is not None else None
        if current is None or current > cap:
            tree = tree.limit(cap, copy=True)
        return tree.sql(dialect=dialect)
    # UNION 等：整体包一层子查询再限行
    return f"SELECT * FROM ({tree.sql(dialect=dialect)}) AS _q LIMIT {cap}"


def _literal_int(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and node.is_int:
        return int(node.this)
    return None
