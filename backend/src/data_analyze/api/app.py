from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from data_analyze.api import artifact_routes, conversation_routes, settings_routes
from data_analyze.api.deps import Services, services
from data_analyze.api.schemas import LoginIn
from data_analyze.config import Settings, get_settings
from data_analyze.datasources.catalog import SchemaCatalog
from data_analyze.datasources.federation import Federation
from data_analyze.datasources.registry import DriverRegistry
from data_analyze.engine.mcp_gateway import GATEWAY, MOUNT_PATH
from data_analyze.relations.infer import RelationInference
from data_analyze.runs.housekeeping import Housekeeper
from data_analyze.runs.manager import RunManager
from data_analyze.security import SESSION_COOKIE, SESSION_TTL_SECONDS, SecretBox
from data_analyze.store.db import Database
from data_analyze.store.migrate import run_migrations

logger = logging.getLogger(__name__)
_PUBLIC_API = {"/api/auth/login", "/api/auth/me", "/api/health"}


def create_app(settings: Settings | None = None, static_dir: Path | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        db = Database(settings.database_url)
        await db.create_all()
        await run_migrations(db.sessions)
        box = SecretBox(settings.resolved_secret())
        drivers = DriverRegistry()
        catalog = SchemaCatalog(db.sessions, box, drivers)
        federation = Federation(settings, db.sessions, box, drivers)
        inference = RelationInference(db.sessions, box, drivers, catalog)
        runs = RunManager(settings, db.sessions, box, drivers, catalog, federation)
        housekeeper = Housekeeper(settings, db.sessions, runs)
        app.state.services = Services(
            settings=settings, db=db, box=box, drivers=drivers, catalog=catalog, federation=federation,
            inference=inference, runs=runs, housekeeper=housekeeper,
        )
        background = [asyncio.create_task(housekeeper.run_forever())]
        if federation.enabled:
            background.append(asyncio.create_task(federation.keep_synced()))
        if not settings.access_password:
            logger.warning("DA_ACCESS_PASSWORD 未设置：任何人都能访问，仅限本地开发")
        yield
        for task in background:
            task.cancel()
        await runs.shutdown()
        await drivers.close_all()
        await db.dispose()

    app = FastAPI(title="AI 数据分析", lifespan=lifespan)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
            allow_methods=["*"], allow_headers=["*"],
        )

    @app.middleware("http")
    async def require_login(request: Request, call_next):  # noqa: ANN001, ANN202
        path = request.url.path
        if settings.access_password and path.startswith("/api/") and path not in _PUBLIC_API:
            box: SecretBox = request.app.state.services.box
            if not box.verify_session(request.cookies.get(SESSION_COOKIE)):
                return JSONResponse({"detail": "请先登录"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/auth/me")
    async def me(request: Request, svc: Annotated[Services, Depends(services)]) -> dict[str, bool]:
        required = bool(settings.access_password)
        return {
            "login_required": required,
            "authenticated": not required or svc.box.verify_session(request.cookies.get(SESSION_COOKIE)),
        }

    @app.post("/api/auth/login")
    async def login(body: LoginIn, request: Request, response: Response,
                    svc: Annotated[Services, Depends(services)]) -> dict[str, bool]:
        import hmac

        if settings.access_password and not hmac.compare_digest(body.password, settings.access_password):
            raise HTTPException(401, "口令不正确")
        response.set_cookie(
            SESSION_COOKIE, svc.box.issue_session(), max_age=SESSION_TTL_SECONDS, httponly=True,
            samesite="lax", secure=request.url.scheme == "https",
        )
        return {"ok": True}

    @app.post("/api/auth/logout")
    async def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(SESSION_COOKIE)
        return {"ok": True}

    app.include_router(settings_routes.router)
    app.include_router(conversation_routes.router)
    app.include_router(artifact_routes.router)
    # Codex 子进程取平台工具的入口：按每轮的随机令牌鉴权，不走登录态
    app.mount(MOUNT_PATH, GATEWAY)

    if static_dir is not None and static_dir.is_dir():
        root = static_dir.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = (root / path).resolve()
            if path and root in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    return app
