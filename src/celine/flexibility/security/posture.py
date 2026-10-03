"""Startup posture check: the dev defaults are refused unless ``CELINE_ENV=dev``.

`core/config.py` ships zero-config development defaults on purpose — the local
stack's database password, a client secret equal to the client id, the SDK's local
Keycloak as issuer — and `AccessPolicy` can run without a loaded bundle. Those
defaults are safe only because this check refuses them anywhere that is not
explicitly development. The rule is ``celine.sdk.posture``'s: only ``CELINE_ENV=dev``
relaxes; unset, empty, ``staging``, ``prod`` or a typo is hardened.

It runs first in the lifespan, before MQTT is connected or the settlement fallback is
scheduled, so a misconfigured deployment fails before it touches anything. In dev
every violation is logged as one warning and startup proceeds.
"""

from __future__ import annotations

from celine.sdk.posture import PostureGuard

from celine.flexibility.core.config import Settings

SERVICE = "flexibility-api"


def build_guard(
    cfg: Settings,
    *,
    policy_loaded: bool,
    env: str | None = None,
) -> PostureGuard:
    """Register every dev-only value this service can start with.

    ``env`` overrides the environment signal; ``None`` reads ``CELINE_ENV`` /
    ``ENVIRONMENT`` as the SDK does.
    """
    from celine.flexibility.security.policy import _POLICIES_DIR

    guard = PostureGuard(SERVICE, env=env)
    guard.forbid_dev_database_url("DATABASE_URL", cfg.database_url)
    guard.forbid_secret_equal_to_client_id(
        "CELINE_OIDC_CLIENT_SECRET", cfg.oidc.client_id, cfg.oidc.client_secret
    )
    guard.require_explicit_oidc(cfg.oidc)
    if not policy_loaded:
        guard.add(
            "policies/",
            f"no policy bundle loaded from {_POLICIES_DIR} — outside dev every "
            "authorisation decision would be a denial",
            "Ship policies/flexibility.rego with the service.",
        )
    return guard


def enforce_posture(cfg: Settings | None = None, *, policy_loaded: bool | None = None) -> None:
    """Raise ``InsecureConfiguration`` outside dev if any dev-only value is in use.

    Defaults to the module-level ``settings`` and to whether ``AccessPolicy`` loads.
    """
    if cfg is None:
        from celine.flexibility.core.config import settings as cfg
    if policy_loaded is None:
        from celine.flexibility.security.policy import AccessPolicy

        policy_loaded = AccessPolicy().loaded
    build_guard(cfg, policy_loaded=policy_loaded).enforce()
