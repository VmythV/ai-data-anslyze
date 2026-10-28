"""与 Python 沙箱交换文件：把会话工作区打包传过去，把脚本产出的文件解回来。

沙箱不挂共享卷，也就碰不到别的会话的数据。传回来的包按不可信数据处理：
只接受普通文件、相对路径、不能越出工作区，也不能写 Claude Code 会当成配置加载的位置。
"""

from __future__ import annotations

import io
import shutil
import tarfile
from pathlib import Path, PurePosixPath

from data_analyze.engine.workspace import UNTRUSTED_NAMES

#: 不传给沙箱：脚本留档目录，以及本就不该出现在工作区里的配置
_SKIP_OUT = {"scripts", *UNTRUSTED_NAMES}


class BundleTooLargeError(ValueError):
    pass


def pack_workspace(workspace: Path, max_bytes: int) -> bytes:
    buf = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path in sorted(workspace.rglob("*")):
            rel = path.relative_to(workspace)
            if rel.parts[0] in _SKIP_OUT or path.is_symlink() or not path.is_file():
                continue
            total += path.stat().st_size
            if total > max_bytes:
                raise BundleTooLargeError(
                    f"工作区文件合计超过 {max_bytes // 1024 // 1024} MB，请删除不需要的中间结果或改用 SQL 聚合"
                )
            tar.add(path, arcname=rel.as_posix(), recursive=False)
    return buf.getvalue()


def unpack_into_workspace(data: bytes, workspace: Path, max_bytes: int) -> list[str]:
    root = workspace.resolve()
    written: list[str] = []
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar:
            rel = PurePosixPath(member.name)
            if (
                not member.isfile()
                or rel.is_absolute()
                or not rel.parts
                or any(p in ("..", "") for p in rel.parts)
                or rel.parts[0] in _SKIP_OUT
                or any(p in UNTRUSTED_NAMES for p in rel.parts)
            ):
                continue
            total += member.size
            if total > max_bytes:
                raise BundleTooLargeError("脚本产出的文件过大")
            target = root.joinpath(*rel.parts)
            if root not in target.resolve().parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                target.unlink()
            source = tar.extractfile(member)
            if source is None:
                continue
            with source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
            written.append(rel.as_posix())
    return written
