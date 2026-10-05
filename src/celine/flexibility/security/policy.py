"""OPA access policy for the flexibility API.

Evaluates decisions using the celine.sdk.policies engine loaded from the
`flexibility.rego` bundle that `policies_dir()` finds.

**Fails closed outside development.** A missing engine or an `allow` evaluation that
raises is a denial unless ``CELINE_ENV=dev`` (``celine.sdk.posture``); a missing
engine also refuses startup there (``security/posture.py``). Only in dev do both
degrade to an allow with a warning.

`PolicyEngine.evaluate()` takes a **Rego query**, not a package name — passing
"celine/flexibility/access" made every evaluation raise, and the then-unconditional
permissive fallback turned every raise into an allow.  Queries are therefore built as
`data.<dotted package>.<rule>`, which is what the SDK's own `evaluate_decision` does
internally.

`evaluate_decision` itself is not used: it builds its own input document from a
`PolicyInput`, whose `ResourceType` is a closed enum with no member for a flexibility
commitment, and whose subject shape (`type: "service"`) is not the one this bundle is
written against (`is_service: true`).  Adopting it would mean rewriting the .rego rather
than fixing a malformed query.

**What the bundle is told about the caller** (REQ-0056) is ``subject_document``: id,
account type, scopes and realm roles. Platform roles travel in ``subject.roles``; there
is no ``groups`` key, so no realm group and no organisation group reaches the bundle, and
the two levels are never merged into one list.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from celine.sdk.auth import JwtUser, realm_roles
from celine.sdk.posture import is_dev
from fastapi import Request

logger = logging.getLogger(__name__)

# Where the bundle is, first match wins. Never the working directory (REQ-0011):
#   1. CELINE_POLICIES_POLICIES_DIR, when a deployment mounts its own bundle;
#   2. the copy packaged into the wheel (`force-include` in pyproject.toml) — what an
#      installed service, the image, has;
#   3. the repository's `policies/`, for a source checkout (editable install, tests).
# The image used to look only for 3, which resolves into site-packages there, so it
# shipped `/app/policies` and never loaded it.
_PACKAGED_POLICIES = Path(__file__).resolve().parent.parent / "policies"
_CHECKOUT_POLICIES = Path(__file__).resolve().parents[4] / "policies"


def policies_dir() -> Path:
    """The bundle directory this process loads (see the order above)."""
    configured = os.environ.get("CELINE_POLICIES_POLICIES_DIR", "").strip()
    if configured:
        return Path(configured)
    if _PACKAGED_POLICIES.is_dir():
        return _PACKAGED_POLICIES
    return _CHECKOUT_POLICIES


# The package as Rego addresses it. The dots matter; slashes are not a package path.
_PACKAGE = "celine.flexibility.access"


def subject_document(user: JwtUser, *, is_service: bool | None = None) -> dict[str, Any]:
    """The ``input.subject`` the bundle is evaluated against.

    ``roles`` is the token's realm roles (``realm_access.roles``) and is the only
    platform-level grant a token carries. No group is read — not the realm ``groups``
    claim, which grants nothing, and not ``organization.<alias>.groups``, because no
    request here concerns an organisation. ``is_service`` defaults to the SDK's
    classification of the token.
    """
    return {
        "id": user.sub,
        "is_service": user.is_service_account if is_service is None else is_service,
        "scopes": (user.claims.get("scope") or "").split(),
        "roles": realm_roles(user.claims),
    }


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None = None
    # The verified caller the decision was made about, ``None`` when there was none.
    # Carried so that whoever refuses the request can record who was refused.
    caller: JwtUser | None = None


class AccessPolicy:
    """Enforce OPA policies via celine.sdk.policies.PolicyEngine.

    Loaded once at startup; decisions are cached per request input hash.

    When the engine is unavailable or the `allow` evaluation raises, the decision is a
    denial unless ``CELINE_ENV=dev``, where it is an allow with a warning. The posture
    is read per decision, not at construction.
    """

    def __init__(self) -> None:
        self._engine = None
        try:
            from celine.sdk.policies import PolicyEngine  # type: ignore[import]

            directory = policies_dir()
            if directory.is_dir():
                self._engine = PolicyEngine(policies_dir=str(directory))
                self._engine.load()
                logger.info("OPA policy engine loaded from %s", directory)
            else:
                logger.warning("Policies dir %s not found — running without OPA", directory)
        except ImportError:
            logger.warning("celine.sdk.policies not available — running without OPA")

    @staticmethod
    def _value(result: Any) -> Any:
        """Pull the single value out of a regorus query result.

        Shape: {"result": [{"expressions": [{"value": …}]}]}. An undefined rule comes
        back with an empty "result", which is not an error — it is the answer.
        """
        try:
            return result["result"][0]["expressions"][0]["value"]
        except (KeyError, IndexError, TypeError):
            return None

    def _reason(self, input_data: dict) -> str | None:
        """The decision's reason, or None if it cannot be read.

        Separate from the allow query, and separately fallible on purpose: a reason is a
        message and an allow is an authorisation. A bundle whose `reason` rules conflict
        must not be able to change who gets in — which is exactly what happened before
        the two were split.
        """
        try:
            reason = self._value(self._engine.evaluate(f"data.{_PACKAGE}.reason", input_data))
        except Exception as exc:
            logger.warning("OPA reason unavailable: %s", exc)
            return None
        return reason if isinstance(reason, str) else None

    @property
    def loaded(self) -> bool:
        """True when a Rego bundle is loaded and decisions are real."""
        return self._engine is not None

    async def _evaluate(self, input_data: dict) -> Decision:
        if self._engine is None:
            if is_dev():
                logger.warning(
                    "No policy engine — allowing %r (CELINE_ENV=dev)", input_data.get("action")
                )
                return Decision(True, "no-policy-engine")
            logger.error("No policy engine — denying %r (fail closed)", input_data.get("action"))
            return Decision(False, "no-policy-engine")
        try:
            allowed = self._value(self._engine.evaluate(f"data.{_PACKAGE}.allow", input_data))
        except Exception as exc:
            if is_dev():
                logger.warning("OPA evaluation error, allowing (CELINE_ENV=dev): %s", exc)
                return Decision(True, "policy-error-permissive")
            logger.error("OPA evaluation error, denying (fail closed): %s", exc)
            return Decision(False, "policy-error")
        return Decision(allowed=allowed is True, reason=self._reason(input_data))

    async def allow_user_commitment(self, request: Request, user_id: str, action: str) -> Decision:
        """Check if the caller may read/write a commitment belonging to user_id."""
        from celine.flexibility.security.auth import verify_request

        try:
            user: JwtUser = verify_request(request)
        except Exception:
            return Decision(False, "unauthenticated")

        input_data = {
            "action": {"name": action},
            "resource": {
                "type": "flexibility.commitment",
                "attributes": {"owner_id": user_id},
            },
            "subject": subject_document(user),
        }
        return replace(await self._evaluate(input_data), caller=user)

    async def allow_service(self, request: Request, action: str) -> Decision:
        """Check if the caller is a service account with adequate scope."""
        from celine.flexibility.security.auth import verify_request

        try:
            user: JwtUser = verify_request(request)
        except Exception:
            return Decision(False, "unauthenticated")

        if not user.is_service_account:
            return Decision(False, "not-a-service-account", user)

        input_data = {
            "action": {"name": action},
            "resource": {"type": "flexibility.commitment"},
            "subject": subject_document(user, is_service=True),
        }
        return replace(await self._evaluate(input_data), caller=user)
