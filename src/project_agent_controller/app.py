from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from project_agent_controller import __version__
from project_agent_controller.api.routes import router
from project_agent_controller.runtime import build_runtime
from project_agent_controller.settings import Settings

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await app.state.runtime.daemon.start()
    try:
        yield
    finally:
        await app.state.runtime.daemon.stop()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()
    if resolved.host not in _LOOPBACK_HOSTS:
        raise ValueError("v0.1A API host must be a loopback address")
    app = FastAPI(
        title="Project Agent Controller",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = build_runtime(resolved)
    app.include_router(router)
    return app
