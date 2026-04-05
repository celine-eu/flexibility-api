from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from celine.flexibility.core.config import settings
from celine.flexibility.routes import register_routes
from celine.flexibility.security.middleware import PolicyMiddleware
from celine.flexibility.services.pipeline_listener import create_broker, on_pipeline_run

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s", settings.app_name)

    broker = create_broker()
    try:
        await broker.connect()
        await broker.subscribe(["celine/pipelines/runs/+"], on_pipeline_run)
        logger.info("MQTT pipeline listener subscribed")
    except Exception as exc:
        # Non-fatal: API still serves requests; reminders/nudges won't fire until reconnect
        logger.warning("MQTT broker unavailable at startup: %s", exc)
        await broker.disconnect()

    yield

    logger.info("Shutting down %s", settings.app_name)
    try:
        await broker.disconnect()
    except Exception:
        pass


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
