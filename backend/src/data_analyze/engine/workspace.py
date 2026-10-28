"""会话工作区：查询结果、脚本、脚本输出都放在这里，与 Python 沙箱共享。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from data_analyze.engine.guard import PROTECTED_NAMES

_CONVERSATION_ID = re.compile(r"^[0-9a-f]{32}$")
SUBDIRS = ("data", "output", "scripts")
#: 这些东西在每轮开始前清掉，免得被 Claude Code / Codex 当成配置或指令加载
UNTRUSTED_NAMES = tuple(sorted(PROTECTED_NAMES))


def workspace_path(root: Path, conversation_id: str) -> Path:
    if not _CONVERSATION_ID.match(conversation_id):
        raise ValueError("非法的会话 ID")
    return root / conversation_id


def prepare_workspace(root: Path, conversation_id: str) -> Path:
    path = workspace_path(root, conversation_id)
    for sub in SUBDIRS:
        (path / sub).mkdir(parents=True, exist_ok=True)
    for name in UNTRUSTED_NAMES:
        target = path / name
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
    return path


def next_query_index(path: Path) -> int:
    """续聊时 SQL 结果文件接着编号，不覆盖上一轮的。"""
    numbers = [int(m.group(1)) for f in (path / "data").glob("q*.parquet") if (m := re.match(r"q(\d+)", f.name))]
    return max(numbers, default=0)
