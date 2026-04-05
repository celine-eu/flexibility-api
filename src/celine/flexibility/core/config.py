from __future__ import annotations
import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from celine.sdk.settings.models import OidcSettings, MqttSettings


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

    # OIDC — driven by CELINE_OIDC_* env vars; defaults work for local dev
    oidc: OidcSettings = OidcSettings(
        audience="svc-flexibility",
        client_id="svc-flexibility",
        client_secret=os.getenv("CELINE_OIDC_CLIENT_SECRET", "svc-flexibility"),
    )

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

    # MQTT — driven by CELINE_MQTT_* env vars
    mqtt: MqttSettings = Field(default_factory=MqttSettings)


settings = Settings()
