"""数据源驱动接口。新增数据库类型 = 新写一个 Driver 并在 registry 里登记。

两类查询语言：
- sql：MySQL / PostgreSQL / ClickHouse，经 run_sql 执行，sqlglot 按 `dialect` 校验
- mongo：MongoDB，经 run_mongo 执行聚合管道
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ConnectionConfig:
    kind: str
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    database: str
    #: 各驱动的额外参数，如 PG 的 sslmode、Mongo 的 auth_source
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TableInfo:
    name: str
    comment: str
    row_estimate: int | None


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    key: str
    comment: str
    #: 低基数字段的取值分布 [(值, 行数)]；高基数或非文本字段为空
    top_values: list[tuple[Any, int]] = field(default_factory=list)


@dataclass(frozen=True)
class TableDetail:
    table: TableInfo
    columns: list[ColumnInfo]
    sample_rows: list[dict[str, Any]]
    indexes: list[str]
    #: 给 AI 的补充说明，如 Mongo 的字段结构是抽样推断出来的
    note: str = ""


@dataclass(frozen=True)
class CatalogColumn:
    """表结构缓存用：一次拉全库字段，供 search_schema 和关联配置使用。"""

    table: str
    name: str
    type: str
    comment: str


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool
    elapsed_ms: int


class DataSourceError(Exception):
    """连接或查询失败；消息直接展示给用户和 AI，不得包含密码。"""


class Driver(ABC):
    kind: str
    language: Literal["sql", "mongo"] = "sql"
    #: sqlglot 方言名（sql 类驱动）
    dialect: str = ""

    def __init__(self, config: ConnectionConfig) -> None:
        self.config = config

    def connection_key(self) -> str | None:
        """同一个 key 的数据源可以在一条查询里互相引用（跨库 JOIN）；None 表示不支持。"""
        return None

    @abstractmethod
    async def test(self) -> str:
        """测试连通性，返回服务端版本等简短信息。"""

    @abstractmethod
    async def list_tables(self) -> list[TableInfo]: ...

    @abstractmethod
    async def describe_table(self, table: str) -> TableDetail: ...

    @abstractmethod
    async def catalog_columns(self) -> list[CatalogColumn]:
        """全部表的字段（表结构缓存用）。"""

    async def sample_values(self, table: str, column: str, limit: int) -> list[Any]:
        """某字段的若干个不同的非空取值（推断关联时核对匹配率用）。"""
        raise DataSourceError(f"{self.kind} 不支持取值抽样")

    async def count_matches(self, table: str, column: str, values: list[Any]) -> int:
        """values 里有多少个（按文本比较）在该字段中出现过。"""
        raise DataSourceError(f"{self.kind} 不支持取值核对")

    async def query(self, sql: str, max_rows: int, timeout_seconds: int) -> QueryResult:
        """执行**已校验过**的只读 SQL。"""
        raise DataSourceError(f"{self.kind} 不支持 SQL 查询")

    async def aggregate(
        self, collection: str, pipeline: list[dict[str, Any]], max_rows: int, timeout_seconds: int
    ) -> QueryResult:
        """执行**已校验过**的 MongoDB 聚合管道。"""
        raise DataSourceError(f"{self.kind} 不支持聚合管道查询")

    @abstractmethod
    async def close(self) -> None: ...
