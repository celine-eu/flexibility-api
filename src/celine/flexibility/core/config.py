from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from celine.sdk.settings.models import OidcSettings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Application
    app_name: str = "flexibility-api"
    log_level: str = "INFO"

    # Database
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@host.docker.internal:15432/flexibility"
    )
    db_schema: str = "flexibility"

    # OIDC — driven by CELINE_OIDC_* env vars; defaults work for local dev
    oidc: OidcSettings = OidcSettings(audience="svc-flexibility")

    # JWT header forwarded by oauth2-proxy
    jwt_header_name: str = "x-auth-request-access-token"


settings = Settings()
