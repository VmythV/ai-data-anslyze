"""表格数据的落盘、读取与 JSON 化。"""

from __future__ import annotations

import datetime as dt
import decimal
import math
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.json as pajson
import pyarrow.parquet as pq

from data_analyze.tools.base import ToolInputError

SUPPORTED_SUFFIXES = (".parquet", ".csv", ".json", ".jsonl")


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S") if value.time() != dt.time() else value.strftime("%Y-%m-%d")
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex() if len(value) <= 64 else f"<{len(value)} bytes>"
    return str(value)


def write_parquet(path: Path, columns: list[str], rows: list[tuple[Any, ...]]) -> None:
    arrays = []
    for i, _ in enumerate(columns):
        values = [_normalize(row[i]) for row in rows]
        try:
            arrays.append(pa.array(values))
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            arrays.append(pa.array([None if v is None else str(v) for v in values]))
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_arrays(arrays, names=_unique(columns)), path)


def read_table(path: Path, max_rows: int) -> tuple[list[str], list[list[Any]], int]:
    """读工作区里的数据文件，返回（列名，前 max_rows 行，总行数）。"""
    if not path.is_file():
        raise ToolInputError(f"文件不存在：{path.name}")
    suffix = path.suffix.lower()
    try:
        if suffix == ".parquet":
            table = pq.read_table(path)
        elif suffix == ".csv":
            table = pacsv.read_csv(path)
        elif suffix in (".json", ".jsonl"):
            table = _read_json(path)
        else:
            raise ToolInputError(f"不支持的数据文件类型 {suffix}，可用：{', '.join(SUPPORTED_SUFFIXES)}")
    except (pa.ArrowInvalid, OSError, ValueError) as exc:
        if isinstance(exc, ToolInputError):
            raise
        raise ToolInputError(f"读取 {path.name} 失败：{exc}") from exc
    head = table.slice(0, max_rows).to_pylist()
    columns = table.column_names
    return columns, [[to_jsonable(r[c]) for c in columns] for r in head], table.num_rows


def _read_json(path: Path) -> pa.Table:
    import json

    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("["):
        records = json.loads(text)
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            raise ToolInputError("JSON 文件需要是对象数组（records 格式）")
        return pa.Table.from_pylist(records)
    return pajson.read_json(path)


def _normalize(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray)):
        return to_jsonable(value)
    return value


def _unique(columns: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for name in columns:
        if name in seen:
            seen[name] += 1
            out.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            out.append(name)
    return out


def preview_text(columns: list[str], rows: list[list[Any]] | list[tuple[Any, ...]], limit: int) -> str:
    """给模型看的预览：管道分隔，比 JSON 省 token。"""
    lines = [" | ".join(columns)]
    for row in rows[:limit]:
        lines.append(" | ".join("" if v is None else str(to_jsonable(v)) for v in row))
    return "\n".join(lines)
