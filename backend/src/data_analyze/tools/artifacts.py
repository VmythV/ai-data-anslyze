"""工具产出的图表与报告：写库、写文件。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from data_analyze.store.models import Artifact, new_id


class ArtifactSink:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], conversation_id: str, artifacts_dir: Path
    ) -> None:
        self._sessions = sessions
        self._conversation_id = conversation_id
        self._dir = artifacts_dir

    async def save_chart(self, title: str, option: dict[str, Any]) -> str:
        return await self._save(new_id(), "chart", title, {"option": option})

    async def get_chart(self, chart_id: str) -> Artifact | None:
        async with self._sessions() as session:
            artifact = await session.get(Artifact, chart_id)
        if artifact is None or artifact.kind != "chart" or artifact.conversation_id != self._conversation_id:
            return None
        return artifact

    async def save_report(self, title: str, html: str) -> str:
        artifact_id = new_id()
        relative = f"{self._conversation_id}/{artifact_id}.html"
        path = self._dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        return await self._save(artifact_id, "report", title, {"file": relative})

    async def _save(self, artifact_id: str, kind: str, title: str, spec: dict[str, Any]) -> str:
        async with self._sessions() as session:
            session.add(
                Artifact(
                    id=artifact_id, conversation_id=self._conversation_id, kind=kind, title=title, spec=spec
                )
            )
            await session.commit()
        return artifact_id
