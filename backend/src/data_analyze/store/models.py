"""平台元数据：模型渠道、数据源、会话、消息、产物。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ModelChannel(Base):
    __tablename__ = "model_channels"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64))
    #: anthropic（Claude Agent SDK）| openai（Codex SDK，Responses API）
    protocol: Mapped[str] = mapped_column(String(16))
    base_url: Mapped[str] = mapped_column(String(512))
    api_key_enc: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(128))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DataSource(Base):
    __tablename__ = "datasources"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64))
    #: 英文短标识，AI 调用工具、写跨库 SQL 时用它指代这个数据源；全局唯一
    alias: Mapped[str | None] = mapped_column(String(32), index=True)
    #: mysql | postgresql | clickhouse | mongodb
    kind: Mapped[str] = mapped_column(String(16))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str] = mapped_column(String(128))
    password_enc: Mapped[str] = mapped_column(Text)
    database: Mapped[str] = mapped_column(String(128))
    #: 驱动的额外参数：PG sslmode、ClickHouse secure、Mongo auth_source / replica_set / tls
    options: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    #: 给 AI 的补充说明：业务口径、表之间的关系、注意事项
    description: Mapped[str] = mapped_column(Text, default="")
    #: 表结构缓存最近一次刷新的时间与结果
    schema_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schema_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DataDomain(Base):
    """数据域：管理员把相关的数据源编成一组，会话可以整组选择。"""

    __tablename__ = "data_domains"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64))
    #: 这组数据源之间的业务关系、跨库口径（自由文本，写进 AI 的系统提示）
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DomainDataSource(Base):
    __tablename__ = "domain_datasources"

    domain_id: Mapped[str] = mapped_column(ForeignKey("data_domains.id", ondelete="CASCADE"), primary_key=True)
    datasource_id: Mapped[str] = mapped_column(ForeignKey("datasources.id", ondelete="CASCADE"), primary_key=True)


class Relation(Base):
    """跨数据源的字段关联（也可以是同一数据源内没有外键的表之间）。"""

    __tablename__ = "relations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    left_datasource_id: Mapped[str] = mapped_column(ForeignKey("datasources.id", ondelete="CASCADE"), index=True)
    left_table: Mapped[str] = mapped_column(String(255))
    left_column: Mapped[str] = mapped_column(String(255))
    right_datasource_id: Mapped[str] = mapped_column(ForeignKey("datasources.id", ondelete="CASCADE"), index=True)
    right_table: Mapped[str] = mapped_column(String(255))
    right_column: Mapped[str] = mapped_column(String(255))
    #: 1:1 | 1:n | n:1 | n:n
    cardinality: Mapped[str] = mapped_column(String(8), default="n:1")
    #: 取值转换说明，如「crm 的 member_id 去掉前缀 M 后等于 shop 客户 ID」
    transform: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    #: manual（管理员配置）| ai（AI 推断）
    source: Mapped[str] = mapped_column(String(16), default="manual")
    #: confirmed | pending（AI 推断、待管理员审核）
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    #: AI 推断时抽样核对的匹配率（0~1）
    match_rate: Mapped[float | None] = mapped_column(nullable=True)
    #: 在哪个会话里由 AI 提交的：待审核期间只对这个会话可见（设置页推断出来的为空，采纳前不进任何会话）
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SchemaEntry(Base):
    """表结构缓存：每张表一行（column 为空），每个字段一行。供 search_schema 与关联配置使用。"""

    __tablename__ = "schema_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    datasource_id: Mapped[str] = mapped_column(ForeignKey("datasources.id", ondelete="CASCADE"), index=True)
    table_name: Mapped[str] = mapped_column(String(255))
    column_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data_type: Mapped[str] = mapped_column(String(128), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    row_estimate: Mapped[int | None] = mapped_column(nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(128), default="新对话")
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("model_channels.id", ondelete="SET NULL"))
    #: 已废弃：单数据源时代的字段，启动时迁移到 conversation_datasources，之后不再读写
    datasource_id: Mapped[str | None] = mapped_column(ForeignKey("datasources.id", ondelete="SET NULL"))
    #: 选中的数据域；与 conversation_datasources 里手动勾选的数据源取并集
    domain_id: Mapped[str | None] = mapped_column(
        ForeignKey("data_domains.id", ondelete="SET NULL"), nullable=True
    )
    #: 引擎侧的会话 ID（Claude 的 session_id / Codex 的 thread_id），续聊时用
    engine_session_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConversationDataSource(Base):
    __tablename__ = "conversation_datasources"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    datasource_id: Mapped[str] = mapped_column(ForeignKey("datasources.id", ondelete="CASCADE"), primary_key=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    #: 用户消息：[{kind: text, text}]；助手消息：见 runs/blocks.py
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    #: completed | interrupted | error
    status: Mapped[str] = mapped_column(String(16), default="completed")
    #: 助手消息这一轮的用量：duration_ms / num_turns / input_tokens / output_tokens / cache_read_tokens / cost_usd
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    #: chart | report
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(256))
    #: chart：ECharts option；report：{"file": 相对 artifacts_dir 的路径}
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
