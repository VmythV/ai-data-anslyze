"""Claude Code 内置文件工具的路径边界：只能读工作区和 Skill 目录，只能写工作区。

Agent 没有 Bash；脚本走 run_python 进沙箱。这里管住剩下几个直接碰本机文件系统的内置工具。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

READ_TOOLS = {"Read", "Glob", "Grep"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
GUARDED_TOOLS = READ_TOOLS | WRITE_TOOLS
GUARDED_MATCHER = "|".join(sorted(GUARDED_TOOLS))

#: 工作区里也不许写：Claude Code / Codex 会把它们当配置或指令加载
PROTECTED_NAMES = {".claude", "CLAUDE.md", "CLAUDE.local.md", ".mcp.json", ".codex", ".agents", "AGENTS.md", "AGENTS.override.md"}


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def check_path(tool: str, tool_input: dict[str, Any], workspace: Path, readable: list[Path]) -> str | None:
    """返回拒绝原因；允许时返回 None。"""
    workspace = workspace.resolve()
    raw_paths = [tool_input.get(key) for key in ("file_path", "path", "notebook_path")]
    pattern = tool_input.get("pattern")
    if tool == "Glob" and isinstance(pattern, str) and (pattern.startswith("/") or ".." in pattern):
        return "Glob 只能使用相对工作区的模式"
    paths = [p for p in raw_paths if isinstance(p, str) and p]
    if tool in READ_TOOLS and not paths:
        return None  # 默认就是工作目录
    for raw in paths:
        candidate = Path(raw)
        resolved = (candidate if candidate.is_absolute() else workspace / candidate).resolve()
        if tool in WRITE_TOOLS:
            if not _within(resolved, workspace):
                return f"只能写工作区内的文件：{raw}"
            if any(part in PROTECTED_NAMES for part in resolved.relative_to(workspace).parts):
                return f"不允许写入 {raw}"
        elif not any(_within(resolved, root.resolve()) for root in [workspace, *readable]):
            return f"只能读取工作区内的文件：{raw}"
    return None
