"""文件清理：删除会话时清掉它留下的所有文件；定期清理长期不用的工作区和孤儿目录。"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from data_analyze.config import Settings
from data_analyze.engine.base import engine_state_dirs
from data_analyze.engine.workspace import workspace_path
from data_analyze.runs.manager import RunManager
from data_analyze.store.models import Conversation

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 3600
#: 孤儿目录至少这么久没变动才删：刚建的会话可能还没出现在清理开始时读到的列表里
ORPHAN_GRACE = timedelta(hours=1)


class Housekeeper:
    def __init__(self, settings: Settings, sessions: async_sessionmaker[AsyncSession], runs: RunManager) -> None:
        self._settings = settings
        self._sessions = sessions
        self._runs = runs

    def conversation_paths(self, conversation_id: str) -> list[Path]:
        workspace = workspace_path(self._settings.workspaces, conversation_id)
        return [
            workspace, self._settings.artifacts_dir / conversation_id,
            *engine_state_dirs(self._settings, conversation_id, workspace),
        ]

    async def remove_conversation_files(self, conversation_id: str) -> None:
        await asyncio.to_thread(_remove_all, self.conversation_paths(conversation_id))

    async def sweep(self, now: datetime | None = None) -> dict[str, int]:
        """清理过期工作区，以及数据库里已经没有对应会话的目录。返回各类清理的数量。"""
        now = now or datetime.now(UTC)
        async with self._sessions() as session:
            rows = (await session.execute(select(Conversation.id, Conversation.updated_at))).all()
        known = {cid for cid, _ in rows}
        stats = {"stale_workspaces": 0, "orphans": 0}

        days = self._settings.workspace_retention_days
        if days:
            cutoff = now - timedelta(days=days)
            for cid, updated in rows:
                updated = updated if updated.tzinfo else updated.replace(tzinfo=UTC)
                active = self._runs.active(cid)
                path = workspace_path(self._settings.workspaces, cid)
                if updated < cutoff and (active is None or active.done) and path.exists():
                    await asyncio.to_thread(shutil.rmtree, path, True)
                    stats["stale_workspaces"] += 1

        expected = {p for cid in known for p in self.conversation_paths(cid)}
        candidates = [
            *_children(self._settings.workspaces),
            *_children(self._settings.artifacts_dir),
            *_children(self._settings.claude_config_dir / "projects"),
            *_children(self._settings.codex_homes_dir),
        ]
        orphans = [
            p for p in candidates
            if p not in expected and now - datetime.fromtimestamp(p.stat().st_mtime, UTC) > ORPHAN_GRACE
        ]
        await asyncio.to_thread(_remove_all, orphans)
        stats["orphans"] = len(orphans)
        if any(stats.values()):
            logger.info("housekeeping: %s", stats)
        return stats

    async def run_forever(self) -> None:
        while True:
            try:
                await self.sweep()
            except Exception:
                logger.exception("housekeeping sweep failed")
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


def _children(root: Path) -> list[Path]:
    return [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []


def _remove_all(paths: list[Path]) -> None:
    for path in paths:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
