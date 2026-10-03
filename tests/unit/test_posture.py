"""Unset ⇒ hardened: the dev defaults refuse startup unless `CELINE_ENV=dev`.

`core/config.py` defaults to the local stack's database password, a client secret
equal to the client id, and the SDK's local Keycloak as issuer. Those are deliberate —
and safe only because `security/posture.py` refuses them outside dev, **before** the
lifespan connects MQTT or schedules the settlement fallback. These tests pin that the refusal
happens, that it happens first, and that dev still starts with the same values.
"""

from __future__ import annotations

import logging

import pytest
from celine.sdk.posture import InsecureConfiguration
from celine.sdk.settings.models import OidcSettings

from celine.flexibility import main as main_module
from celine.flexibility.core.config import Settings
from celine.flexibility.security.posture import build_guard, enforce_posture

DEV_DSN = "postgresql+asyncpg://postgres:securepassword123@db.example.org:5432/flexibility"
REAL_DSN = "postgresql+asyncpg://flexibility:generated-7f3a9c@db.example.org:5432/flexibility"
ISSUER = "https://auth.example.org/realms/celine"


@pytest.fixture(autouse=True)
def _no_oidc_env(monkeypatch):
    """Keep a developer's shell from making the SDK defaults look explicit."""
    for name in ("CELINE_OIDC_BASE_URL", "CELINE_OIDC_JWKS_URI", "CELINE_OIDC_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)


def _set_env(monkeypatch, env: str | None) -> None:
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    if env is None:
        monkeypatch.delenv("CELINE_ENV", raising=False)
    else:
        monkeypatch.setenv("CELINE_ENV", env)


def dev_defaults() -> Settings:
    """What a deployment gets when it sets nothing: the code's own defaults."""
    return Settings(
        _env_file=None,
        database_url=DEV_DSN,
        oidc=OidcSettings(audience="svc-flexibility", client_id="svc-flexibility", client_secret="svc-flexibility"),
    )


def configured() -> Settings:
    """What a hardened deployment must look like."""
    return Settings(
        _env_file=None,
        database_url=REAL_DSN,
        oidc=OidcSettings(
            audience="svc-flexibility",
            client_id="svc-flexibility",
            client_secret="a-real-secret-from-the-realm",
            base_url=ISSUER,
            jwks_uri=f"{ISSUER}/protocol/openid-connect/certs",
        ),
    )


HARDENED = pytest.mark.parametrize("env", [None, "", "staging", "prod"])


# @verifies REQ-0055
@HARDENED
def test_outside_dev_the_dev_defaults_refuse_startup(monkeypatch, env):
    """Every violation is reported at once, so a deployment fixes them in one cycle."""
    _set_env(monkeypatch, env)

    with pytest.raises(InsecureConfiguration) as raised:
        enforce_posture(dev_defaults(), policy_loaded=True)

    message = str(raised.value)
    assert "DATABASE_URL" in message
    assert "CELINE_OIDC_CLIENT_SECRET" in message
    assert "CELINE_OIDC_BASE_URL" in message
    assert "CELINE_OIDC_JWKS_URI" in message


# @verifies REQ-0055
@pytest.mark.parametrize(
    "change, setting",
    [
        ({"database_url": DEV_DSN}, "DATABASE_URL"),
        (
            {"oidc": OidcSettings(
                audience="svc-flexibility", client_id="svc-flexibility", client_secret="svc-flexibility",
                base_url=ISSUER, jwks_uri=f"{ISSUER}/certs",
            )},
            "CELINE_OIDC_CLIENT_SECRET",
        ),
        (
            {"oidc": OidcSettings(
                audience="svc-flexibility", client_id="svc-flexibility", client_secret="a-real-secret",
            )},
            "CELINE_OIDC_BASE_URL",
        ),
    ],
)
def test_outside_dev_each_dev_value_alone_refuses_startup(monkeypatch, change, setting):
    _set_env(monkeypatch, "staging")
    cfg = configured().model_copy(update=change)

    with pytest.raises(InsecureConfiguration, match=setting):
        enforce_posture(cfg, policy_loaded=True)


# @verifies REQ-0055
@HARDENED
def test_outside_dev_a_missing_policy_bundle_refuses_startup(monkeypatch, env):
    _set_env(monkeypatch, env)

    with pytest.raises(InsecureConfiguration, match="policies/"):
        enforce_posture(configured(), policy_loaded=False)


# @verifies REQ-0055
@HARDENED
def test_outside_dev_a_configured_deployment_starts(monkeypatch, env):
    _set_env(monkeypatch, env)

    enforce_posture(configured(), policy_loaded=True)


# @verifies REQ-0055
def test_in_dev_the_same_values_start_with_a_warning(monkeypatch, caplog):
    _set_env(monkeypatch, "dev")

    with caplog.at_level(logging.WARNING, logger="celine.sdk.posture"):
        enforce_posture(dev_defaults(), policy_loaded=False)

    assert "development setting(s) in use" in caplog.text
    assert not build_guard(dev_defaults(), policy_loaded=False).hardened


# ---------------------------------------------------------------------------
# The check runs in the lifespan, before anything is opened
# ---------------------------------------------------------------------------


class _Broker:
    async def connect(self):
        pass

    async def subscribe(self, *_args):
        pass

    async def disconnect(self):
        pass


@pytest.fixture
def side_effects(monkeypatch) -> list[str]:
    """Record the lifespan's side effects instead of performing them."""
    calls: list[str] = []

    def _create_broker():
        calls.append("create_broker")
        return _Broker()

    async def _fallback(stop):
        calls.append("settlement_fallback")
        await stop.wait()

    monkeypatch.setattr(main_module, "create_broker", _create_broker)
    monkeypatch.setattr(main_module, "run_settlement_fallback", _fallback)
    return calls


# @verifies REQ-0055
@pytest.mark.parametrize("env", [None, "staging"])
async def test_outside_dev_the_lifespan_refuses_before_opening_anything(
    monkeypatch, side_effects, env
):
    """The suite's own settings are dev values (`test:test`, the default secret)."""
    _set_env(monkeypatch, env)

    with pytest.raises(InsecureConfiguration):
        async with main_module.lifespan(main_module.create_app()):
            pass

    assert side_effects == []


# @verifies REQ-0055
async def test_in_dev_the_lifespan_starts_with_the_same_settings(monkeypatch, side_effects):
    _set_env(monkeypatch, "dev")

    async with main_module.lifespan(main_module.create_app()):
        pass

    assert side_effects == ["create_broker", "settlement_fallback"]
