from types import SimpleNamespace

import pytest

from data_analyze.tools.ask_tool import ask_tools
from data_analyze.tools.base import ToolInputError


def _tool():
    events: list = []
    spec = ask_tools(SimpleNamespace(emit=events.append))[0]  # type: ignore[arg-type]
    return spec, events


async def test_emits_question_and_ends_turn() -> None:
    spec, events = _tool()
    assert spec.ends_turn
    out = await spec.handler({"questions": [
        {"question": "看哪段时间？", "header": "时间范围，超过十二个字的部分截断", "options": [
            {"label": "最近 30 天（推荐）", "description": "和前 30 天对比"}, "今年以来"]},
        {"question": "看哪些维度？", "multi_select": True, "options": [{"label": "大区"}, {"label": "品类"}]},
    ]})
    assert not out.is_error and "本轮到此结束" in out.text
    event = events[0]
    assert event["type"] == "question" and len(event["id"]) == 32
    q1, q2 = event["questions"]
    assert q1["header"] == "时间范围，超过十二个字的部"[:12] and not q1["multi_select"]
    assert q1["options"][1] == {"label": "今年以来", "description": ""}
    assert q2["multi_select"]


@pytest.mark.parametrize("questions", [
    [],
    [{"question": "q?", "options": [{"label": "a"}]}],
    [{"question": "q?", "options": [{"label": "a"}, {"label": "a"}]}],
    [{"question": "", "options": [{"label": "a"}, {"label": "b"}]}],
    [{"question": "q?", "options": [{"label": "a"}, {"label": "b"}]}] * 5,
])
async def test_rejects_bad_questions(questions: list) -> None:
    spec, events = _tool()
    with pytest.raises(ToolInputError):
        await spec.handler({"questions": questions})
    assert events == []


async def test_render_kpis() -> None:
    from data_analyze.tools.kpi_tool import kpi_tools

    events: list = []
    spec = kpi_tools(SimpleNamespace(emit=events.append))[0]  # type: ignore[arg-type]
    out = await spec.handler({"title": "概况", "items": [
        {"label": "GMV", "value": "3,120 万元", "delta": "同比 +24.8%", "tone": "good"},
        {"label": "退款率", "value": "6.1%", "tone": "worse"},
    ]})
    assert not out.is_error and events[0]["type"] == "kpis"
    assert events[0]["items"][1]["tone"] == "neutral" and events[0]["items"][1]["delta"] == ""
    with pytest.raises(ToolInputError):
        await spec.handler({"items": [{"label": "x"}]})
    with pytest.raises(ToolInputError):
        await spec.handler({"items": [{"label": "x", "value": "1"}] * 7})
