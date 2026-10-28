"""工作区文件和技能工具：给没有内置文件工具的引擎（Codex 关掉了 shell）用。

只能读写本会话工作区；技能正文从内置技能目录读，模型给不了任意路径。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from data_analyze.config import SKILLS_PLUGIN_DIR
from data_analyze.engine.guard import PROTECTED_NAMES
from data_analyze.engine.prompts import skill_catalog
from data_analyze.tools.base import ToolContext, ToolInputError, ToolOutput, ToolSpec, resolve_in_workspace

READ_LINE_LIMIT = 2000
READ_CHAR_LIMIT = 60_000
LIST_LIMIT = 500
GREP_MATCH_LIMIT = 200
GREP_LINE_CHARS = 300
#: 单个文件超过这个大小就不逐行搜索
GREP_FILE_BYTES = 5 * 1024 * 1024
WRITE_CHAR_LIMIT = 2_000_000


def _path_arg(args: dict[str, Any], key: str = "path", default: str | None = None) -> str:
    value = args.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ToolInputError(f"{key} 不能为空")
    if Path(value).is_absolute():
        raise ToolInputError(f"{key} 必须是相对工作区的路径：{value}")
    return value.strip()


def _writable(ctx: ToolContext, relative: str) -> Path:
    target = resolve_in_workspace(ctx.workspace, relative)
    parts = target.relative_to(ctx.workspace.resolve()).parts
    if not parts or any(part in PROTECTED_NAMES for part in parts):
        raise ToolInputError(f"不允许写入 {relative}")
    return target


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if b"\0" in raw[:8192]:
        raise ToolInputError(f"{path.name} 是二进制文件（如 parquet），请用 run_python 读取")
    return raw.decode("utf-8", errors="replace")


def _rel(ctx: ToolContext, path: Path) -> str:
    return path.relative_to(ctx.workspace.resolve()).as_posix()


def _iter_files(ctx: ToolContext, pattern: str) -> list[Path]:
    if pattern.startswith("/") or ".." in Path(pattern).parts:
        raise ToolInputError("pattern 只能是相对工作区的 glob 模式")
    root = ctx.workspace.resolve()
    # 符号链接可能指到工作区外：解析后仍在工作区内的才算
    return sorted(
        p for p in root.glob(pattern)
        if p.is_file() and (p.resolve() == root or root in p.resolve().parents)
    )


def file_tools(ctx: ToolContext) -> list[ToolSpec]:
    async def read_file(args: dict[str, Any]) -> ToolOutput:
        relative = _path_arg(args)
        path = resolve_in_workspace(ctx.workspace, relative)
        if not path.is_file():
            raise ToolInputError(f"文件不存在：{relative}")
        offset = max(int(args.get("offset") or 1), 1)
        limit = min(max(int(args.get("limit") or READ_LINE_LIMIT), 1), READ_LINE_LIMIT)
        lines = (await asyncio.to_thread(_read_text, path)).splitlines()
        chunk = lines[offset - 1 : offset - 1 + limit]
        body = "\n".join(f"{offset + i:>6}\t{line}" for i, line in enumerate(chunk))
        if len(body) > READ_CHAR_LIMIT:
            body = body[:READ_CHAR_LIMIT] + "\n…（内容过长已截断，用 offset/limit 分段读取）"
        rest = len(lines) - (offset - 1 + len(chunk))
        tail = f"\n…（还有 {rest} 行，用 offset={offset + len(chunk)} 继续读）" if rest > 0 else ""
        return ToolOutput((body or "（空文件）") + tail)

    async def list_files(args: dict[str, Any]) -> ToolOutput:
        pattern = str(args.get("pattern") or "**/*")
        files = await asyncio.to_thread(_iter_files, ctx, pattern)
        if not files:
            return ToolOutput("没有匹配的文件")
        shown = [f"{_rel(ctx, p)}  ({p.stat().st_size} B)" for p in files[:LIST_LIMIT]]
        more = f"\n…（共 {len(files)} 个，只列出前 {LIST_LIMIT} 个）" if len(files) > LIST_LIMIT else ""
        return ToolOutput("\n".join(shown) + more)

    async def grep_files(args: dict[str, Any]) -> ToolOutput:
        raw = args.get("pattern")
        if not isinstance(raw, str) or not raw:
            raise ToolInputError("pattern 不能为空")
        try:
            regex = re.compile(raw, re.IGNORECASE if args.get("ignore_case") else 0)
        except re.error as exc:
            raise ToolInputError(f"正则表达式有误：{exc}") from exc

        def search() -> list[str]:
            hits: list[str] = []
            for path in _iter_files(ctx, str(args.get("glob") or "**/*")):
                if path.stat().st_size > GREP_FILE_BYTES:
                    continue
                try:
                    text = _read_text(path)
                except ToolInputError:
                    continue
                for number, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        hits.append(f"{_rel(ctx, path)}:{number}: {line[:GREP_LINE_CHARS]}")
                        if len(hits) >= GREP_MATCH_LIMIT:
                            return hits
            return hits

        hits = await asyncio.to_thread(search)
        if not hits:
            return ToolOutput("没有匹配的内容")
        more = f"\n…（只列出前 {GREP_MATCH_LIMIT} 处）" if len(hits) >= GREP_MATCH_LIMIT else ""
        return ToolOutput("\n".join(hits) + more)

    async def write_file(args: dict[str, Any]) -> ToolOutput:
        relative = _path_arg(args)
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolInputError("content 必须是字符串")
        if len(content) > WRITE_CHAR_LIMIT:
            raise ToolInputError("内容过大，请改用 run_python 生成文件")
        target = _writable(ctx, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_text, content, encoding="utf-8")
        return ToolOutput(f"已写入 {_rel(ctx, target)}（{len(content)} 字符）")

    async def edit_file(args: dict[str, Any]) -> ToolOutput:
        relative = _path_arg(args)
        old, new = args.get("old_string"), args.get("new_string")
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ToolInputError("old_string 不能为空，new_string 必须是字符串")
        target = _writable(ctx, relative)
        if not target.is_file():
            raise ToolInputError(f"文件不存在：{relative}")
        text = await asyncio.to_thread(_read_text, target)
        count = text.count(old)
        if count == 0:
            raise ToolInputError("old_string 在文件里找不到，请先 read_file 确认原文")
        if count > 1 and not args.get("replace_all"):
            raise ToolInputError(f"old_string 出现了 {count} 次，请给出更长的上下文，或设 replace_all=true")
        text = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        await asyncio.to_thread(target.write_text, text, encoding="utf-8")
        return ToolOutput(f"已修改 {_rel(ctx, target)}（替换 {count if args.get('replace_all') else 1} 处）")

    skills = {name: desc for name, desc in skill_catalog()}

    async def load_skill(args: dict[str, Any]) -> ToolOutput:
        name = str(args.get("name") or "").strip().removeprefix("analyst:")
        if name not in skills:
            raise ToolInputError(f"没有技能「{name}」，可用：{', '.join(skills)}")
        path = SKILLS_PLUGIN_DIR / "skills" / name / "SKILL.md"
        return ToolOutput(await asyncio.to_thread(path.read_text, encoding="utf-8"))

    path_param = {"type": "string", "description": "相对工作区的路径，如 output/summary.md"}
    return [
        ToolSpec(
            name="read_file",
            description="读取工作区里的文本文件（带行号）。parquet 等二进制文件请用 run_python 读。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": path_param,
                    "offset": {"type": "integer", "description": "从第几行开始，默认 1"},
                    "limit": {"type": "integer", "description": f"最多读几行，默认 {READ_LINE_LIMIT}"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=read_file,
        ),
        ToolSpec(
            name="list_files",
            description="按 glob 模式列出工作区里的文件，如 data/*.parquet、output/**/*。默认列出全部。",
            input_schema={
                "type": "object",
                "properties": {"pattern": {"type": "string", "description": "相对工作区的 glob 模式"}},
                "additionalProperties": False,
            },
            handler=list_files,
        ),
        ToolSpec(
            name="grep_files",
            description="在工作区的文本文件里按正则搜索，返回 文件:行号: 内容。",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "glob": {"type": "string", "description": "只搜这些文件，默认全部"},
                    "ignore_case": {"type": "boolean"},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
            handler=grep_files,
        ),
        ToolSpec(
            name="write_file",
            description="在工作区里新建或覆盖一个文本文件（自动建目录）。产出文件放 output/。",
            input_schema={
                "type": "object",
                "properties": {"path": path_param, "content": {"type": "string"}},
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            handler=write_file,
        ),
        ToolSpec(
            name="edit_file",
            description="把工作区文件里的一段原文替换成新内容；old_string 必须与原文完全一致且唯一（或设 replace_all）。",
            input_schema={
                "type": "object",
                "properties": {
                    "path": path_param,
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_string", "new_string"],
                "additionalProperties": False,
            },
            handler=edit_file,
        ),
        ToolSpec(
            name="load_skill",
            description="加载一项专业技能的完整说明。可用技能：" + "；".join(f"{n}（{d}）" for n, d in skills.items()),
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string", "enum": list(skills)}},
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=load_skill,
        ),
    ]
