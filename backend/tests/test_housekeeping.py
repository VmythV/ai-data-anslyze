import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import text

from data_analyze.config import Settings
from data_analyze.runs.housekeeping import Housekeeper
from data_analyze.store.db import Database
from data_analyze.store.models import Conversation


def _age(path: Path, hours: float) -> None:
    ts = time.time() - hours * 3600
    os.utime(path, (ts, ts))


async def test_sweep(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, workspace_retention_days=30)
    settings.ensure_dirs()
    db = Database(settings.database_url)
    await db.create_all()
    now = datetime.now(UTC)
    fresh, stale, running = "a" * 32, "b" * 32, "c" * 32
    async with db.sessions() as session:
        session.add(Conversation(id=fresh, updated_at=now))
        session.add(Conversation(id=stale, updated_at=now - timedelta(days=40)))
        session.add(Conversation(id=running, updated_at=now - timedelta(days=40)))
        await session.commit()
    runs = SimpleNamespace(active=lambda cid: SimpleNamespace(done=False) if cid == running else None)
    keeper = Housekeeper(settings, db.sessions, runs)  # type: ignore[arg-type]

    for cid in (fresh, stale, running):
        for path in keeper.conversation_paths(cid):
            path.mkdir(parents=True)
    old_orphan = settings.workspaces / ("d" * 32)
    new_orphan = settings.artifacts_dir / ("e" * 32)
    old_orphan.mkdir()
    new_orphan.mkdir()
    _age(old_orphan, 2)

    stats = await keeper.sweep(now)
    assert stats == {"stale_workspaces": 1, "orphans": 1}
    ws = settings.workspaces
    assert (ws / fresh).exists() and (ws / running).exists() and not (ws / stale).exists()
    # 过期只清工作区，报告和对话记录还在
    assert (settings.artifacts_dir / stale).exists()
    assert all(p.exists() for p in keeper.conversation_paths(stale)[1:])
    assert not old_orphan.exists() and new_orphan.exists()

    await keeper.remove_conversation_files(fresh)
    assert not any(p.exists() for p in keeper.conversation_paths(fresh))
    await db.dispose()


async def test_adds_missing_nullable_column(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'old.db'}"
    db = Database(url)
    async with db.engine.begin() as conn:
        # 上一个版本的 messages 表：没有 usage 列
        await conn.execute(text(
            "CREATE TABLE messages (id VARCHAR(32) PRIMARY KEY, conversation_id VARCHAR(32), role VARCHAR(16), "
            "blocks JSON, status VARCHAR(16), created_at DATETIME)"
        ))
    await db.create_all()
    async with db.engine.connect() as conn:
        columns = [r[1] for r in (await conn.execute(text("PRAGMA table_info(messages)"))).all()]
    assert "usage" in columns
    await db.dispose()
