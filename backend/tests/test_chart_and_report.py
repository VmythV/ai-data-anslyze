from pathlib import Path
from types import SimpleNamespace

import pytest

from data_analyze.report.renderer import ReportDocument, render_report
from data_analyze.tools.base import ToolInputError
from data_analyze.tools.chart_tool import prepare_option
from data_analyze.tools.tabular import write_parquet


def _ctx(ws: Path) -> SimpleNamespace:
    return SimpleNamespace(workspace=ws)


def test_chart_from_data_file(tmp_path: Path) -> None:
    write_parquet(tmp_path / "data/q001.parquet", ["月份", "GMV"], [("2025-01", 1.5), ("2025-02", 2.0)])
    option = prepare_option(
        _ctx(tmp_path),
        {"data_file": "data/q001.parquet", "option": {
            "title": {"text": "dup"}, "xAxis": {"type": "category"}, "yAxis": {},
            "series": [{"type": "bar", "encode": {"x": "月份", "y": "GMV"}, "label": {"show": True, "position": "right"}}]}},
    )
    assert "title" not in option
    assert option["dataset"]["source"][0] == ["月份", "GMV"]
    assert option["grid"]["right"] == 56


@pytest.mark.parametrize(
    "args",
    [
        {"option": {"series": []}},
        {"option": {"series": [{"type": "bogus", "data": [1]}]}},
        {"option": {"series": [{"type": "line"}]}},
        {"option": {"series": [{"type": "line"}]}, "data_file": "../../etc/passwd"},
    ],
)
def test_chart_rejects(tmp_path: Path, args: dict) -> None:
    with pytest.raises(ToolInputError):
        prepare_option(_ctx(tmp_path), args)


def test_report_escapes_and_inlines() -> None:
    html = render_report(ReportDocument(
        title="<script>alert(1)</script>",
        datasource="mock",
        summary="- **结论** <img src=x onerror=alert(1)>",
        sections=[{"type": "chart", "title": "c", "caption": "", "height": 300,
                   "option": {"series": [{"type": "bar", "data": ["</script><b>"]}]}}],
    ))
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x" not in html
    assert "</script><b>" not in html
    assert "echarts" in html and "<strong>结论</strong>" in html


def test_polish_option() -> None:
    from data_analyze.tools.chart_tool import polish_option

    option = {
        "legend": {}, "xAxis": {"type": "category", "data": ["a", "b"]},
        "yAxis": {"type": "value", "name": "满意度（5 分制）"},
        "series": [{"type": "line", "data": [1, 2]}, {"type": "line", "data": list(range(40))}],
    }
    out = polish_option(option)
    assert out["legend"]["bottom"] == 0 and out["grid"]["bottom"] == 36
    assert out["yAxis"]["nameTextStyle"]["align"] == "left"
    assert out["series"][0]["showSymbol"] is True and "showSymbol" not in out["series"][1]
    assert polish_option(out) == out  # 可重复执行
    assert option["legend"] == {}  # 不改传入的对象
    # 用户自己指定过的位置不动
    kept = polish_option({"legend": {"top": 0}, "series": [{"type": "bar", "data": [1]}]})
    assert kept["legend"] == {"top": 0} and kept["grid"] == {"top": 12}
    # 数据集里的点数也算
    ds = polish_option({"dataset": {"source": [["x", "y"], [1, 2]]}, "series": {"type": "line"}})
    assert ds["series"]["showSymbol"] is True
