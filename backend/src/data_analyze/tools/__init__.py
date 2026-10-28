from __future__ import annotations

from data_analyze.tools.ask_tool import ask_tools
from data_analyze.tools.base import ToolContext, ToolSpec
from data_analyze.tools.chart_tool import chart_tools
from data_analyze.tools.data_tools import data_tools
from data_analyze.tools.kpi_tool import kpi_tools
from data_analyze.tools.python_tool import python_tools
from data_analyze.tools.relation_tool import relation_tools
from data_analyze.tools.report_tool import report_tools


def build_tools(ctx: ToolContext) -> list[ToolSpec]:
    return [
        *data_tools(ctx), *python_tools(ctx), *chart_tools(ctx), *kpi_tools(ctx), *report_tools(ctx), *ask_tools(ctx),
        *relation_tools(ctx),
    ]
