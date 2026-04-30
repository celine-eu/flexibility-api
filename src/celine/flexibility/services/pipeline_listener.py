"""MQTT pipeline-run event listener.

Replaces the digital-twin event handlers for flexibility:
  - meters-flow completed        → send_pending_reminders()
  - rec-forecasting-flow completed → notify_flexibility_opportunity()
  - rec_flexibility_flow completed → settle_completed_windows()

All service clients (DT, REC Registry, Nudging) are created once at startup
from settings using OidcClientCredentialsProvider for service-to-service auth.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from celine.sdk.auth import OidcClientCredentialsProvider
from celine.sdk.broker import MqttBroker, MqttConfig, PipelineRunEvent, ReceivedMessage
from celine.sdk.dt.client import DTClient
from celine.sdk.nudging.client import NudgingAdminClient
from celine.sdk.rec_registry.client import RecRegistryAdminClient

from celine.flexibility.core.config import settings
from celine.flexibility.db.session import SessionLocal
from celine.flexibility.services.nudge_opportunity import notify_flexibility_opportunity
from celine.flexibility.services.reminders import send_pending_reminders
from celine.flexibility.services.settlement import settle_completed_windows

logger = logging.getLogger(__name__)

# Module-level instances — created once in create_broker(), reused across requests.
_broker: MqttBroker | None = None
_dt_client: DTClient | None = None
_registry_client: RecRegistryAdminClient | None = None
_nudging_client: NudgingAdminClient | None = None


def get_broker() -> MqttBroker | None:
    """Return the running broker instance (available after startup)."""
    return _broker


def get_nudging_client() -> NudgingAdminClient | None:
    """Return the nudging admin client instance (available after startup)."""
    return _nudging_client


def _make_oidc_provider(scope: str | None) -> OidcClientCredentialsProvider:
    return OidcClientCredentialsProvider(
        base_url=settings.oidc.base_url,
        client_id=settings.oidc.client_id or "",
        client_secret=settings.oidc.client_secret or "",
        scope=scope,
    )


def create_broker() -> MqttBroker:
    """Create service clients and the MQTT broker.

    Called once at application startup from lifespan().
    The returned broker must be connected via broker.connect() before use.
    """
    global _broker, _dt_client, _registry_client, _nudging_client

    _dt_client = DTClient(
        base_url=settings.digital_twin_api_url,
        token_provider=_make_oidc_provider(settings.dt_client_scope),
    )
    _registry_client = RecRegistryAdminClient(
        base_url=settings.rec_registry_url,
        token_provider=_make_oidc_provider(settings.rec_registry_scope),
    )
    _nudging_client = NudgingAdminClient(
        base_url=settings.nudging_api_url,
        token_provider=_make_oidc_provider(settings.nudging_scope),
    )

    cfg = MqttConfig(
        host=settings.mqtt.host,
        port=settings.mqtt.port,
        username=settings.mqtt.username,
        password=settings.mqtt.password,
        use_tls=settings.mqtt.use_tls,
        ca_certs=settings.mqtt.ca_certs,
        keepalive=settings.mqtt.keepalive,
        clean_session=settings.mqtt.clean_session,
        reconnect_interval=settings.mqtt.reconnect_interval,
        max_reconnect_attempts=settings.mqtt.max_reconnect_attempts,
        client_id=settings.mqtt.client_id,
        topic_prefix=settings.mqtt.topic_prefix,
    )
    # Use OIDC JWT auth for the MQTT broker (same client credentials as outbound HTTP calls).
    # MqttBroker sends (access_token, "jwt") as (username, password) when token_provider is set.
    mqtt_token_provider = _make_oidc_provider(None)  # None → all default scopes
    _broker = MqttBroker(cfg, token_provider=mqtt_token_provider)
    return _broker


async def on_pipeline_run(msg: ReceivedMessage) -> None:
    """Handle celine/pipelines/runs/+ messages."""
    try:
        event = PipelineRunEvent.model_validate(msg.payload)
    except Exception as exc:
        logger.warning("Failed to parse PipelineRunEvent: %s", exc)
        return

    if event.status != "completed":
        return

    logger.debug("Pipeline completed: %s.%s", event.namespace, event.flow)

    # meters
    if event.flow == "meters-flow":
        if _nudging_client is None:
            return
        async with SessionLocal() as session:
            count = await send_pending_reminders(session, _nudging_client)
        if count:
            logger.info("Sent %d flexibility reminders on meters-flow tick", count)

    # forecasting
    elif event.flow == "rec-forecasting-flow":
        if _dt_client is None or _registry_client is None or _nudging_client is None:
            return
        await notify_flexibility_opportunity(
            _dt_client, _registry_client, _nudging_client
        )
    # flexibility
    elif event.flow == "rec-flexibility-flow":
        if _dt_client is None:
            return
        # Settle commitments for the period that just completed.
        # Use the event timestamp to determine which date to settle; fall back to today.
        try:
            period_date = datetime.fromisoformat(event.timestamp).date()
        except (ValueError, AttributeError):
            period_date = datetime.now(timezone.utc).date()

        async with SessionLocal() as session:
            count = await settle_completed_windows(session, _dt_client, period_date)
        if count:
            logger.info(
                "Settled %d flexibility commitments for period=%s", count, period_date
            )
