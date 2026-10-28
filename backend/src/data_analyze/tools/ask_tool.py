"""向用户提问：意图不明确时给出选项，让用户在输入框上方点选。

调用后本轮立即结束（ToolSpec.ends_turn），用户的选择作为下一条消息发回来，
所以模型不会在等待期间自行猜测答案继续做下去，也不占着运行名额等人。
"""

from __future__ import annotations

from typing import Any

from data_analyze.store.models import new_id
from data_analyze.tools.base import ToolContext, ToolInputError, ToolOutput, ToolSpec

MAX_QUESTIONS = 4
MIN_OPTIONS, MAX_OPTIONS = 2, 6
HEADER_MAX = 12


def _clean_question(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ToolInputError(f"第 {index} 个问题格式不对")
    question = str(raw.get("question") or "").strip()
    if not question:
        raise ToolInputError(f"第 {index} 个问题缺少 question")
    options = raw.get("options")
    if not isinstance(options, list) or not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise ToolInputError(f"「{question}」需要 {MIN_OPTIONS}~{MAX_OPTIONS} 个选项")
    cleaned, seen = [], set()
    for option in options:
        label = str(option.get("label") if isinstance(option, dict) else option or "").strip()
        if not label:
            raise ToolInputError(f"「{question}」有空的选项")
        if label in seen:
            raise ToolInputError(f"「{question}」的选项重复：{label}")
        seen.add(label)
        description = str(option.get("description") or "").strip() if isinstance(option, dict) else ""
        cleaned.append({"label": label[:60], "description": description[:120]})
    return {
        "question": question[:200],
        "header": str(raw.get("header") or "").strip()[:HEADER_MAX],
        "multi_select": bool(raw.get("multi_select")),
        "options": cleaned,
    }


def ask_tools(ctx: ToolContext) -> list[ToolSpec]:
    async def ask_user(args: dict[str, Any]) -> ToolOutput:
        questions = args.get("questions")
        if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
            raise ToolInputError(f"questions 需要 1~{MAX_QUESTIONS} 个问题")
        cleaned = [_clean_question(q, i + 1) for i, q in enumerate(questions)]
        ctx.emit({"type": "question", "id": new_id(), "questions": cleaned})
        return ToolOutput(
            "问题已展示给用户。本轮到此结束：不要再调用工具，不要替用户做选择。"
            "用户的回答会作为下一条消息发来，届时按回答继续分析。"
        )

    return [
        ToolSpec(
            name="ask_user",
            description=(
                "当用户的意图不明确、且不同理解会得出明显不同的结论时，用它向用户提问，而不是在回答里用文字发问。"
                "问题会以单选/多选的形式出现在用户输入框上方，用户总是还能选「其他」自己填写。"
                "调用后本轮立即结束，用户的选择会作为下一条消息发来。"
                "规则：一次最多 4 个问题，把需要确认的点一次问完；每题 2~6 个具体选项，"
                "把你推荐的选项放在第一个并在 label 末尾标「（推荐）」；"
                "选项之间互斥时用单选，可以同时成立时用多选（multi_select）；"
                "不要问能从数据里查到的事，也不要为无关紧要的细节提问——能按合理默认处理的就直接做并说明假设。"
                "调用前可以先用一两句话说明为什么需要确认。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_QUESTIONS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string", "description": "完整的问题，以问号结尾"},
                                "header": {"type": "string", "description": f"不超过 {HEADER_MAX} 个字的短标签，如「统计口径」"},
                                "multi_select": {"type": "boolean", "description": "true 为多选，默认单选"},
                                "options": {
                                    "type": "array",
                                    "minItems": MIN_OPTIONS,
                                    "maxItems": MAX_OPTIONS,
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {"type": "string", "description": "选项文字，简短"},
                                            "description": {
                                                "type": "string",
                                                "description": "可选，一句话解释这个选项意味着什么",
                                            },
                                        },
                                        "required": ["label"],
                                    },
                                },
                            },
                            "required": ["question", "options"],
                        },
                    }
                },
                "required": ["questions"],
                "additionalProperties": False,
            },
            handler=ask_user,
            ends_turn=True,
        )
    ]
