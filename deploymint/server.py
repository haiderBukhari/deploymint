"""FastAPI app factory. See docs/05-phase-1-foundation.md Step 1.10.

This module string, `deploymint.server:app`, is exactly what the Dockerfile's
CMD runs — there is no separate CLI subcommand that wraps it."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from deploymint import __version__
from deploymint.api import (
    approvals,
    chat,
    cloud_deploy,
    costs,
    fixes,
    health,
    monitoring,
    projects,
    runs,
    settings,
    ws,
)
from deploymint.db.database import init_db
from deploymint.web import routes as web_routes

WEB_DIR = Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="DeployMint", version=__version__, lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(runs.router)
    app.include_router(ws.router)
    app.include_router(cloud_deploy.router)
    app.include_router(fixes.router)
    app.include_router(approvals.router)
    app.include_router(chat.router)
    app.include_router(costs.router)
    app.include_router(settings.router)
    app.include_router(monitoring.router)
    app.include_router(web_routes.router)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    return app


app = create_app()
