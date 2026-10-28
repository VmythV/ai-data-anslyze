import io
import tarfile
from pathlib import Path

import pytest

from data_analyze.tools.bundle import BundleTooLargeError, pack_workspace, unpack_into_workspace


def _tar(entries: list[tuple[tarfile.TarInfo, bytes]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for info, data in entries:
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data) if info.isfile() else None)
    return buf.getvalue()


def _file(name: str) -> tarfile.TarInfo:
    return tarfile.TarInfo(name)


def test_pack_skips_scripts_and_config(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data/q001.parquet").write_bytes(b"x")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/run.py").write_text("print(1)")
    (tmp_path / "CLAUDE.md").write_text("x")
    with tarfile.open(fileobj=io.BytesIO(pack_workspace(tmp_path, 1024)), mode="r:gz") as tar:
        assert tar.getnames() == ["data/q001.parquet"]
    with pytest.raises(BundleTooLargeError):
        pack_workspace(tmp_path, 0)


def test_unpack_rejects_dangerous_entries(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    link = tarfile.TarInfo("output/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    data = _tar([
        (_file("output/ok.csv"), b"a,b"),
        (_file("../escape.txt"), b"x"),
        (_file("/abs.txt"), b"x"),
        (_file(".claude/settings.json"), b"{}"),
        (_file("sub/CLAUDE.md"), b"x"),
        (_file("scripts/run.py"), b"x"),
        (link, b""),
    ])
    assert unpack_into_workspace(data, ws, 1024) == ["output/ok.csv"]
    assert (ws / "output/ok.csv").read_text() == "a,b"
    assert not (tmp_path / "escape.txt").exists()
    assert not (ws / ".claude").exists() and not (ws / "sub/CLAUDE.md").exists()
    assert not (ws / "output/link").exists()


def test_unpack_does_not_follow_existing_symlink_dir(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    outside = tmp_path / "outside"
    ws.mkdir()
    outside.mkdir()
    (ws / "output").symlink_to(outside)
    assert unpack_into_workspace(_tar([(_file("output/x.csv"), b"1")]), ws, 1024) == []
    assert not (outside / "x.csv").exists()
