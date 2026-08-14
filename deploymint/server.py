"""FastAPI app factory. See docs/05-phase-1-foundation.md Step 1.10.

This module string, `deploymint.server:app`, is exactly what the Dockerfile's
CMD runs — there is no separate CLI subcommand that wraps it."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from deploymint import __version__
from deploymint.api import health, projects
from deploymint.db.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="DeployMint", version=__version__, lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(projects.router)
    return app


app = create_app()
