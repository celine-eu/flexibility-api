from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from celine.sdk.audit import configure_audit
from celine.sdk.posture import docs_urls
from fastapi import FastAPI

from celine.flexibility.core.config import settings
from celine.flexibility.routes import register_routes
from celine.flexibility.security.middleware import PolicyMiddleware
from celine.flexibility.security.posture import enforce_posture
from celine.flexibility.services.pipeline_listener import (
    create_broker,
    on_pipeline_run,
    run_settlement_fallback,
)

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)
configure_audit("flexibility-api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s", settings.app_name)

    # First, before any side effect: outside CELINE_ENV=dev the dev defaults and a
    # missing policy bundle refuse startup (InsecureConfiguration).
    enforce_posture()

    broker = create_broker()
    try:
        await broker.connect()
        await broker.subscribe(["celine/pipelines/runs/+"], on_pipeline_run)
        logger.info("MQTT pipeline listener subscribed")
    except Exception as exc:
        # Non-fatal: API still serves requests; reminders/nudges won't fire until reconnect
        logger.warning("MQTT broker unavailable at startup: %s", exc)
        await broker.disconnect()

    stop_fallback = asyncio.Event()
    fallback = asyncio.create_task(run_settlement_fallback(stop_fallback))

    yield

    logger.info("Shutting down %s", settings.app_name)
    stop_fallback.set()
    try:
        await asyncio.wait_for(fallback, timeout=5)
    except Exception:
        pass
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
        # Outside CELINE_ENV=dev no /docs, /redoc or /openapi.json unless
        # CELINE_PUBLIC_DOCS=true (REQ-0004).
        **docs_urls(),
    )
    app.add_middleware(PolicyMiddleware)
    register_routes(app)
    return app
