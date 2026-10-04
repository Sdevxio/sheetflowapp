from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.uds_routes import router as uds_router
from app.config import get_settings
from app.logging_config import setup_logging
from app.services.jobs import start_worker, stop_worker, storage_root


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    storage_root()
    start_worker()
    yield
    stop_worker()


def _frontend_dir() -> Path | None:
    settings = get_settings()
    candidate = Path(settings.sheetflow_static) if settings.sheetflow_static else Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (candidate / "index.html").is_file():
        return candidate
    return None


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="SheetFlow", version=settings.transform_version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    app.include_router(uds_router)
    frontend = _frontend_dir()
    if frontend is not None:
        app.frontend("/", directory=str(frontend), fallback="index.html", check_dir=True)
    else:

        @app.get("/")
        def root() -> dict:
            return {"service": "sheetflow", "docs": "/docs", "health": "/api/health"}

    return app


app = create_app()
