"""Python 沙箱执行服务（只用标准库）。

隔离方式：
- 容器只在内部网络上（不能出网、连不到业务库），不挂任何数据卷，根文件系统只读
- 每次执行在 /jobs 下的一次性目录里进行：调用方把本会话的文件打包传进来，跑完把新增/修改的文件打包传回去，
  目录随即删除。/jobs 本身不可列目录
- 并发的任务各用一个独立 uid（10001 起），目录权限 700：互相看不到文件，也读不到对方的 /proc；
  任务结束后把该 uid 名下残留的进程全部杀掉，下一个任务复用这个 uid 时是干净的

POST /run {"code": 脚本, "timeout": 秒, "files_tar": base64(tar.gz)}
→ {"exit_code", "stdout", "stderr", "duration_ms", "timed_out", "files", "files_tar"}
"""

from __future__ import annotations

import base64
import io
import json
import os
import queue
import resource
import shutil
import signal
import subprocess
import tarfile
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

JOBS_ROOT = Path(os.environ.get("SANDBOX_JOBS", "/jobs"))
MAX_CONCURRENT = int(os.environ.get("SANDBOX_MAX_CONCURRENT", "4"))
MEMORY_LIMIT = int(os.environ.get("SANDBOX_MEMORY_MB", "2048")) * 1024 * 1024
MAX_TIMEOUT = int(os.environ.get("SANDBOX_MAX_TIMEOUT", "300"))
MAX_REQUEST_BYTES = int(os.environ.get("SANDBOX_MAX_REQUEST_MB", "256")) * 1024 * 1024
MAX_RESULT_BYTES = int(os.environ.get("SANDBOX_MAX_RESULT_MB", "128")) * 1024 * 1024
FILE_SIZE_LIMIT = 512 * 1024 * 1024
MAX_PROCESSES = 64
OUTPUT_LIMIT = 200_000
UID_BASE = 10000
#: 任务目录里不回传的部分：脚本本身（调用方已有）、临时目录
_PRIVATE = {"scripts", ".tmp"}

IS_ROOT = os.geteuid() == 0
_uids: queue.Queue[int] = queue.Queue()
for _i in range(MAX_CONCURRENT):
    _uids.put(UID_BASE + 1 + _i)


class BadRequest(ValueError):
    pass


def _safe_relative(name: str) -> PurePosixPath | None:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(p in ("..", "") for p in path.parts):
        return None
    return path


def unpack(data: bytes, dest: Path) -> None:
    """只解出普通文件；绝对路径、..、链接、设备文件一律跳过。"""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar:
            rel = _safe_relative(member.name)
            if rel is None or not member.isfile() or rel.parts[0] in _PRIVATE:
                continue
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is not None:
                with source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out)


def pack(root: Path, paths: list[str]) -> bytes:
    buf = io.BytesIO()
    total = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in paths:
            path = root / rel
            if not path.is_file() or path.is_symlink():
                continue
            total += path.stat().st_size
            if total > MAX_RESULT_BYTES:
                raise BadRequest(f"脚本产出的文件超过 {MAX_RESULT_BYTES // 1024 // 1024} MB")
            tar.add(path, arcname=rel, recursive=False)
    return buf.getvalue()


def _snapshot(root: Path) -> dict[str, tuple[float, int]]:
    out = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if rel.parts[0] in _PRIVATE or path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        out[rel.as_posix()] = (stat.st_mtime, stat.st_size)
    return out


def _demote(uid: int, cpu_seconds: int) -> None:
    """在子进程 exec 之前执行：新会话、资源限制、降权到任务 uid。"""
    os.setsid()
    limits = [
        (resource.RLIMIT_AS, MEMORY_LIMIT),
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_FSIZE, FILE_SIZE_LIMIT),
        (resource.RLIMIT_CORE, 0),
    ]
    if IS_ROOT:
        limits.append((resource.RLIMIT_NPROC, MAX_PROCESSES))
    for limit, value in limits:
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            pass  # macOS 本地调试时部分限制不可用
    if IS_ROOT:
        os.setgroups([])
        os.setgid(uid)
        os.setuid(uid)
    os.umask(0o077)


def _kill_uid(uid: int) -> None:
    """杀掉该 uid 名下的所有进程（包括脚本 fork 出去、脱离进程组的）。"""
    if not IS_ROOT:
        return
    pid = os.fork()
    if pid == 0:
        try:
            os.setgid(uid)
            os.setuid(uid)
            os.kill(-1, signal.SIGKILL)  # Linux 上不会杀到调用者自己
        except OSError:
            pass
        os._exit(0)
    os.waitpid(pid, 0)


def _drain(stream, sink: bytearray) -> None:  # noqa: ANN001
    """读到 EOF，只保留末尾 OUTPUT_LIMIT 字节。"""
    while chunk := stream.read(65536):
        sink.extend(chunk)
        if len(sink) > OUTPUT_LIMIT * 2:
            del sink[: len(sink) - OUTPUT_LIMIT]
    stream.close()


def _chown_tree(root: Path, uid: int) -> None:
    for dirpath, _dirs, files in os.walk(root):
        os.chown(dirpath, uid, uid)
        for name in files:
            os.chown(os.path.join(dirpath, name), uid, uid)


def run(code: str, timeout: int, files_tar: bytes | None) -> dict:
    try:
        uid = _uids.get(timeout=60)
    except queue.Empty as exc:
        raise TimeoutError("沙箱繁忙，请稍后重试") from exc
    job = JOBS_ROOT / uuid.uuid4().hex
    try:
        job.mkdir(mode=0o700)
        if files_tar:
            unpack(files_tar, job)
        for sub in ("scripts", "output", ".tmp"):
            (job / sub).mkdir(exist_ok=True)
        script = f"scripts/run_{datetime.now():%Y%m%d_%H%M%S}.py"
        (job / script).write_text(code, encoding="utf-8")
        before = _snapshot(job)
        if IS_ROOT:
            _chown_tree(job, uid)
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": str(job / ".tmp"),
            "TMPDIR": str(job / ".tmp"),
            "LANG": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "MPLBACKEND": "Agg",
            "OMP_NUM_THREADS": "2",
        }
        started = time.monotonic()
        proc = subprocess.Popen(
            ["python", "-I", "-X", "utf8", script],
            cwd=job, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            preexec_fn=lambda: _demote(uid, timeout + 5),
        )
        # 输出在后台线程里读：脚本 fork 出去的进程可能一直占着管道，不能等它们自己关
        stdout_buf, stderr_buf = bytearray(), bytearray()
        readers = [
            threading.Thread(target=_drain, args=(proc.stdout, stdout_buf), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, stderr_buf), daemon=True),
        ]
        for reader in readers:
            reader.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        # 主进程结束（或超时）后，该 uid 名下的一切进程都不该再活着
        _kill_uid(uid)
        if timed_out:
            proc.kill()
            proc.wait()
        for reader in readers:
            reader.join(timeout=5)
        stdout, stderr = bytes(stdout_buf), bytes(stderr_buf)
        duration = int((time.monotonic() - started) * 1000)
        after = _snapshot(job)
        changed = sorted(p for p, sig in after.items() if before.get(p) != sig)
        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode("utf-8", "replace")[-OUTPUT_LIMIT:],
            "stderr": stderr.decode("utf-8", "replace")[-OUTPUT_LIMIT:],
            "duration_ms": duration,
            "timed_out": timed_out,
            "files": changed,
            "files_tar": base64.b64encode(pack(job, changed)).decode() if changed else "",
        }
    finally:
        _kill_uid(uid)
        shutil.rmtree(job, ignore_errors=True)
        _uids.put(uid)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            return self._send(404, {"error": "not found"})
        self._send(200, {"status": "ok", "isolation": "uid" if IS_ROOT else "none"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/run":
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_REQUEST_BYTES * 4 // 3 + 4096:
            return self._send(413, {"error": "传入的文件过大"})
        try:
            body = json.loads(self.rfile.read(length))
            code = str(body["code"])
            timeout = max(1, min(int(body.get("timeout", 120)), MAX_TIMEOUT))
            files_tar = base64.b64decode(body["files_tar"]) if body.get("files_tar") else None
        except (ValueError, KeyError, TypeError):
            return self._send(400, {"error": "请求格式错误"})
        try:
            self._send(200, run(code, timeout, files_tar))
        except TimeoutError as exc:
            self._send(503, {"error": str(exc)})
        except (BadRequest, tarfile.TarError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception as exc:  # 不让单个任务的意外把连接直接断掉
            print(f"[sandbox] run failed: {exc!r}", flush=True)
            self._send(500, {"error": f"沙箱内部错误：{type(exc).__name__}"})

    def log_message(self, fmt: str, *args) -> None:  # noqa: ANN002
        print(f"[sandbox] {self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    if IS_ROOT:
        # 任务 uid 只能穿过 /jobs 进入自己的目录，列不出别人的
        os.chmod(JOBS_ROOT, 0o711)
    else:
        print("[sandbox] 警告：未以 root 启动，无法按任务切换 uid，并发任务之间没有隔离", flush=True)
    port = int(os.environ.get("SANDBOX_PORT", "8901"))
    host = os.environ.get("SANDBOX_HOST", "0.0.0.0")
    print(f"[sandbox] listening on {host}:{port}, jobs={JOBS_ROOT}, isolation={'uid' if IS_ROOT else 'none'}",
          flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
