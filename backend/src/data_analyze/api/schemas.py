from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Protocol = Literal["anthropic", "openai"]


class LoginIn(BaseModel):
    password: str


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    protocol: Protocol = "anthropic"
    base_url: str = Field(min_length=1, max_length=512)
    #: 编辑时留空表示不修改
    api_key: str = ""
    model: str = Field(min_length=1, max_length=128)
    is_default: bool = False


class ChannelOut(BaseModel):
    id: str
    name: str
    protocol: str
    base_url: str
    api_key_masked: str
    model: str
    is_default: bool
    #: 该协议是否有对应的 Agent 引擎
    supported: bool


DataSourceKind = Literal["mysql", "postgresql", "clickhouse", "mongodb"]
ALIAS_REGEX = r"^[a-z][a-z0-9_]{0,31}$"


class DataSourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    #: 英文短标识：AI 用它指代数据源，跨库 SQL 里不用它（用库名）
    alias: str = Field(pattern=ALIAS_REGEX, description="小写字母开头，只含小写字母、数字、下划线")
    kind: DataSourceKind = "mysql"
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=3306, ge=1, le=65535)
    #: MongoDB 可以不开认证，其余必填（由前端约束）
    username: str = Field(default="", max_length=128)
    #: 编辑时留空表示不修改
    password: str = ""
    database: str = Field(min_length=1, max_length=128)
    options: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(default="", max_length=8000)


class DataSourceTestIn(DataSourceIn):
    #: 编辑已有数据源时测试：密码留空则用库里存的
    id: str | None = None
    #: 测试连接时还没起标识也可以
    alias: str = Field(default="tmp", pattern=ALIAS_REGEX)


class DataSourceOut(BaseModel):
    id: str
    name: str
    alias: str
    kind: str
    host: str
    port: int
    username: str
    database: str
    options: dict[str, Any]
    description: str
    schema_refreshed_at: datetime | None
    schema_error: str | None


class DomainIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=8000)
    datasource_ids: list[str] = Field(default_factory=list)


class DomainOut(DomainIn):
    id: str


Cardinality = Literal["1:1", "1:n", "n:1", "n:n"]


class RelationIn(BaseModel):
    left_datasource_id: str
    left_table: str = Field(min_length=1, max_length=255)
    left_column: str = Field(min_length=1, max_length=255)
    right_datasource_id: str
    right_table: str = Field(min_length=1, max_length=255)
    right_column: str = Field(min_length=1, max_length=255)
    cardinality: Cardinality = "n:1"
    transform: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=2000)


class RelationOut(RelationIn):
    id: str
    source: str
    status: str
    match_rate: float | None
    created_at: datetime


class InferIn(BaseModel):
    #: 只在这些数据源之间推断；空 = 全部
    datasource_ids: list[str] = Field(default_factory=list)


class InferCandidate(BaseModel):
    left: str
    right: str
    reason: str
    match_rate: float | None
    sampled: int
    error: str


class InferOut(BaseModel):
    evaluated: int
    created: int
    candidates: list[InferCandidate]


class SchemaTable(BaseModel):
    name: str
    comment: str
    row_estimate: int | None
    columns: list[tuple[str, str, str]]


class ConversationIn(BaseModel):
    channel_id: str | None = None
    datasource_ids: list[str] = Field(default_factory=list)
    domain_id: str | None = None


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    channel_id: str | None = None
    datasource_ids: list[str] | None = None
    domain_id: str | None = None


class ConversationOut(BaseModel):
    id: str
    title: str
    channel_id: str | None
    #: 手动勾选的数据源（不含数据域展开的）
    datasource_ids: list[str]
    domain_id: str | None
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: str
    role: str
    blocks: list[dict[str, Any]]
    status: str
    usage: dict[str, Any] | None = None
    created_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]
    #: 正在运行时，页面用它订阅事件流
    running: bool


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class TestResult(BaseModel):
    ok: bool
    message: str
