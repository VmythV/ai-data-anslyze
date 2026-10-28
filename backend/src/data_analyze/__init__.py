"""AI 数据分析平台后端。"""

from __future__ import annotations

import logging


def main() -> None:
    import uvicorn

    from data_analyze.api.app import create_app
    from data_analyze.config import get_settings

    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(
        create_app(settings, static_dir=settings.static_dir),
        host=settings.host,
        port=settings.port,
        # SSE 长连接：关停时别无限等
        timeout_graceful_shutdown=5,
    )
