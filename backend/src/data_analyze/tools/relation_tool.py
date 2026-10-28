"""AI 在分析中发现的关联：核对匹配率后提交为「待审核」，管理员在设置页采纳后对所有会话生效。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select

from data_analyze.datasources.base import DataSourceError
from data_analyze.tools.base import ToolContext, ToolInputError, ToolOutput, ToolSpec

SAMPLE_SIZE = 300
MIN_MATCH_RATE = 0.3
_SIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "datasource": {"type": "string", "description": "数据源标识"},
        "table": {"type": "string", "description": "表名（PostgreSQL 写 schema.表名，MongoDB 写集合名）"},
        "column": {"type": "string", "description": "字段名（MongoDB 嵌套字段用 a.b）"},
    },
    "required": ["datasource", "table", "column"],
    "additionalProperties": False,
}


def relation_tools(ctx: ToolContext) -> list[ToolSpec]:
    if len(ctx.sources) < 2 or ctx.sessions is None:
        return []

    async def propose_relation(args: dict[str, Any]) -> ToolOutput:
        from data_analyze.store.models import Relation

        sides = []
        for key in ("left", "right"):
            raw = args.get(key)
            if not isinstance(raw, dict):
                raise ToolInputError(f"{key} 需要包含 datasource、table、column")
            handle = ctx.source(raw.get("datasource"))
            table, column = str(raw.get("table") or "").strip(), str(raw.get("column") or "").strip()
            if not table or not column:
                raise ToolInputError(f"{key} 的 table、column 不能为空")
            sides.append((handle, table, column))
        (lh, lt, lc), (rh, rt, rc) = sides
        label = f"{lh.alias}.{lt}.{lc} ↔ {rh.alias}.{rt}.{rc}"

        async with ctx.sessions() as session:
            existing = (
                await session.scalars(
                    select(Relation).where(
                        or_(
                            (Relation.left_datasource_id == lh.id) & (Relation.left_table == lt)
                            & (Relation.left_column == lc) & (Relation.right_datasource_id == rh.id)
                            & (Relation.right_table == rt) & (Relation.right_column == rc),
                            (Relation.left_datasource_id == rh.id) & (Relation.left_table == rt)
                            & (Relation.left_column == rc) & (Relation.right_datasource_id == lh.id)
                            & (Relation.right_table == lt) & (Relation.right_column == lc),
                        )
                    )
                )
            ).first()
        if existing is not None:
            state = "已确认" if existing.status == "confirmed" else "待审核"
            return ToolOutput(f"这条关联已经存在（{state}），不用重复提交：{label}")

        transform = str(args.get("transform") or "").strip()
        rate: float | None = None
        if not transform:
            # 没有取值转换时，平台自己抽样核对，不采信模型报的数字
            try:
                values = [v for v in await lh.driver.sample_values(lt, lc, SAMPLE_SIZE) if v not in (None, "")]
                matched = await rh.driver.count_matches(rt, rc, values) if values else 0
            except DataSourceError as exc:
                return ToolOutput(f"核对匹配率失败：{exc}", is_error=True)
            if not values:
                return ToolOutput(f"{lh.alias}.{lt}.{lc} 没有可抽样的取值，无法核对", is_error=True)
            rate = matched / len(values)
            if rate < MIN_MATCH_RATE:
                return ToolOutput(
                    f"未提交：抽样 {len(values)} 个取值，只有 {rate:.0%} 能在 {rh.alias}.{rt}.{rc} 中找到。"
                    "请检查是否选错了字段，或两边取值需要转换（转换规则写在 transform 里）。",
                    is_error=True,
                )

        cardinality = args.get("cardinality") if args.get("cardinality") in ("1:1", "1:n", "n:1", "n:n") else "n:1"
        reason = str(args.get("reason") or "").strip()[:500]
        rate_text = f"抽样核对匹配 {rate:.0%}" if rate is not None else "含取值转换，未自动核对"
        async with ctx.sessions() as session:
            session.add(
                Relation(
                    left_datasource_id=lh.id, left_table=lt, left_column=lc,
                    right_datasource_id=rh.id, right_table=rt, right_column=rc,
                    cardinality=cardinality, transform=transform,
                    description=f"AI 在对话中发现：{reason}（{rate_text}）" if reason else f"AI 在对话中发现（{rate_text}）",
                    source="ai", status="pending", match_rate=round(rate, 4) if rate is not None else None,
                    conversation_id=ctx.conversation_id,
                )
            )
            await session.commit()
        ctx.emit({"type": "relation", "left": f"{lh.alias}.{lt}.{lc}", "right": f"{rh.alias}.{rt}.{rc}",
                  "match_rate": rate, "status": "pending"})
        return ToolOutput(f"已提交给管理员审核：{label}（{rate_text}）。采纳前只在本会话里可用。")

    return [
        ToolSpec(
            name="propose_relation",
            description=(
                "把你在分析中发现、且已经验证可用的跨数据源关联提交给管理员审核（系统提示里的已知关联不用提交）。"
                "平台会抽样核对匹配率，太低的会被拒绝。采纳后，以后的会话都会知道这条关联。"
                "只提交真正有复用价值的「键对键」关联，不要提交临时的、靠计算得出的匹配。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "left": {**_SIDE_SCHEMA, "description": "引用方（通常是多的一侧，如订单表里的客户 ID）"},
                    "right": {**_SIDE_SCHEMA, "description": "被引用方（如客户表的 ID）"},
                    "cardinality": {"type": "string", "enum": ["n:1", "1:1", "1:n", "n:n"]},
                    "transform": {
                        "type": "string",
                        "description": "两边取值需要转换时写清楚规则，如「去掉前缀 M 后转整数」；不需要就留空",
                    },
                    "reason": {"type": "string", "description": "一句话说明依据，如「按此关联 97% 的订单能找到发货单」"},
                },
                "required": ["left", "right", "reason"],
                "additionalProperties": False,
            },
            handler=propose_relation,
        )
    ]
