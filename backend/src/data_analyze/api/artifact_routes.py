from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from data_analyze.api.deps import Services, db_session, services
from data_analyze.report.theme import chart_themes
from data_analyze.store.models import Artifact

router = APIRouter(prefix="/api")
Session = Annotated[AsyncSession, Depends(db_session)]
Svc = Annotated[Services, Depends(services)]

#: 报告是 AI 生成的页面：只许运行自带的内联脚本，不许联网、不许提交表单，也拿不到本站的 Cookie
REPORT_CSP = (
    "sandbox allow-scripts allow-popups allow-modals; default-src 'none'; "
    "script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:"
)


@router.get("/chart-theme")
async def chart_theme() -> dict[str, Any]:
    return chart_themes()


@router.get("/artifacts/{artifact_id}/report.html")
async def report_html(
    artifact_id: str, session: Session, svc: Svc, download: Annotated[bool, Query()] = False
) -> HTMLResponse:
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None or artifact.kind != "report":
        raise HTTPException(404, "报告不存在")
    path = (svc.settings.artifacts_dir / artifact.spec["file"]).resolve()
    if svc.settings.artifacts_dir not in path.parents or not path.is_file():
        raise HTTPException(404, "报告文件不存在")
    headers = {"Content-Security-Policy": REPORT_CSP, "X-Content-Type-Options": "nosniff"}
    if download:
        filename = quote(f"{artifact.title}.html")
        headers["Content-Disposition"] = f"attachment; filename=report.html; filename*=UTF-8''{filename}"
    return HTMLResponse(path.read_text(encoding="utf-8"), headers=headers)
