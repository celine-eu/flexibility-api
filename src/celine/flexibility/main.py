from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from celine.flexibility.core.config import settings
from celine.flexibility.routes import register_routes
from celine.flexibility.security.middleware import PolicyMiddleware

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s", settings.app_name)
    yield
    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    app = FastAPI(
        title="CELINE Flexibility API",
        version="0.1.0",
        description="Commitment store for voluntary and automated load-shifting.",
        lifespan=lifespan,
    )
    app.add_middleware(PolicyMiddleware)
    register_routes(app)
    return app
