from __future__ import annotations
import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from celine.sdk.settings.models import OidcSettings, MqttSettings


_LOCAL_OIDC_DEFAULTS = {
    "audience": "svc-flexibility",
    "client_id": "svc-flexibility",
    "client_secret": "svc-flexibility",
}


def _oidc_with_local_defaults() -> OidcSettings:
    unset = {
        field: value
        for field, value in _LOCAL_OIDC_DEFAULTS.items()
        if not os.environ.get(f"CELINE_OIDC_{field.upper()}")
    }
    return OidcSettings(**unset)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Application
    app_name: str = "flexibility-api"
    log_level: str = "INFO"

    # Database
    database_url: str = (
        "postgresql+asyncpg://postgres:securepassword123@host.docker.internal:15432/flexibility"
    )
    db_schema: str = "flexibility"

    # OIDC — driven by CELINE_OIDC_* env vars; the local realm's identity is the
    # default for whatever the environment does not set. Passed as constructor
    # arguments these would override the environment, so the client could not be
    # configured at all (security/posture.py refuses the dev secret outside dev).
    oidc: OidcSettings = Field(default_factory=lambda: _oidc_with_local_defaults())

    # JWT header forwarded by oauth2-proxy
    jwt_header_name: str = "x-auth-request-access-token"

    # Downstream service URLs
    nudging_api_url: str = "http://host.docker.internal:8016"
    digital_twin_api_url: str = "http://host.docker.internal:8002"
    rec_registry_url: str = "http://host.docker.internal:8004"

    # Service-to-service OIDC scopes for outbound calls
    dt_client_scope: str | None = None
    rec_registry_scope: str | None = None
    nudging_scope: str | None = None

    # Settlement also runs on this timer (seconds), not only on the pipeline's MQTT
    # message — see services.pipeline_listener.run_settlement_fallback.
    settlement_fallback_seconds: float = 6 * 3600.0

    # Fair gamification (demo3 spec 2026-10-07, design 2026-10-09): when true, commitments
    # are settled from the pipeline's weekly outcomes (rec_fair_commitment_outcome: fixed
    # points if kept) instead of kWh × 10, and the member gets a commitment_settled nudge.
    # Flip together with the webapp's GAMIFICATION_VARIANT=fair at a season start.
    fair_settlement_enabled: bool = False

    # MQTT — driven by CELINE_MQTT_* env vars
    mqtt: MqttSettings = Field(default_factory=MqttSettings)


settings = Settings()
