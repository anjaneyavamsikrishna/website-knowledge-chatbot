from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.deps import RateLimiter
from app.api.routes import admin, chat, health, ingest, leads
from app.core.config import get_settings
from app.core.container import Container, build_container
from app.core.errors import AppError
from app.core.logging import configure_logging

FRONTEND_DIR = Path(__file__).parent / "frontend"
log = logging.getLogger(__name__)


def create_app(container: Container | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "container", None) is None:
            settings = get_settings()
            configure_logging(settings.log_level)
            app.state.container = build_container(settings)
        c: Container = app.state.container
        app.state.rate_limiter = RateLimiter(c.settings.rate_limit_per_minute)
        if c.settings.auto_ingest_on_startup and c.store.count() == 0:
            log.info("Knowledge base is empty: starting background ingestion")
            threading.Thread(
                target=lambda: c.refresh_service.refresh(trigger="startup"), name="startup-ingest", daemon=True
            ).start()
        yield

    app = FastAPI(title="PlacidWay Website Knowledge Chatbot", version="1.0.0", lifespan=lifespan)
    app.state.container = container
    if container is not None:
        app.state.rate_limiter = RateLimiter(container.settings.rate_limit_per_minute)

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("Unhandled error")
        return JSONResponse(status_code=500, content={"detail": "Unexpected server error."})

    for module in (chat, ingest, health, admin, leads):
        app.include_router(module.router)

    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")

    return app


app = create_app()
