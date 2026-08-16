"""OPA access policy for the flexibility API.

Evaluates decisions using the celine.sdk.policies engine loaded from
./policies/flexibility.rego.  Falls back to permissive if policies are
not configured (dev/test convenience).

`PolicyEngine.evaluate()` takes a **Rego query**, not a package name — passing
"celine/flexibility/access" made every evaluation raise, and the permissive fallback
below turned every raise into an allow.  Queries are therefore built as
`data.<dotted package>.<rule>`, which is what the SDK's own `evaluate_decision` does
internally.

`evaluate_decision` itself is not used: it builds its own input document from a
`PolicyInput`, whose `ResourceType` is a closed enum with no member for a flexibility
commitment, and whose subject shape (`type: "service"`) is not the one this bundle is
written against (`is_service: true`).  Adopting it would mean rewriting the .rego rather
than fixing a malformed query.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from celine.sdk.auth.jwt import extract_groups
from fastapi import Request

logger = logging.getLogger(__name__)

_POLICIES_DIR = Path(__file__).parent.parent.parent.parent.parent / "policies"

# The package as Rego addresses it. The dots matter; slashes are not a package path.
_PACKAGE = "celine.flexibility.access"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None = None


class AccessPolicy:
    """Enforce OPA policies via celine.sdk.policies.PolicyEngine.

    Loaded once at startup; decisions are cached per request input hash.
    """

    def __init__(self) -> None:
        self._engine = None
        try:
            from celine.sdk.policies import PolicyEngine  # type: ignore[import]

            if _POLICIES_DIR.exists():
                self._engine = PolicyEngine(policies_dir=str(_POLICIES_DIR))
                self._engine.load()
                logger.info("OPA policy engine loaded from %s", _POLICIES_DIR)
            else:
                logger.warning("Policies dir %s not found — running without OPA", _POLICIES_DIR)
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

    async def _evaluate(self, input_data: dict) -> Decision:
        if self._engine is None:
            logger.warning("No policy engine — allowing %r", input_data.get("action"))
            return Decision(True, "no-policy-engine")
        try:
            allowed = self._value(self._engine.evaluate(f"data.{_PACKAGE}.allow", input_data))
        except Exception as exc:
            logger.warning("OPA evaluation error: %s", exc)
            return Decision(True, "policy-error-permissive")
        return Decision(allowed=allowed is True, reason=self._reason(input_data))

    async def allow_user_commitment(self, request: Request, user_id: str, action: str) -> Decision:
        """Check if the caller may read/write a commitment belonging to user_id."""
        from celine.sdk.auth import JwtUser
        from celine.flexibility.security.auth import get_user_from_request

        try:
            user: JwtUser = get_user_from_request(request)
        except Exception:
            return Decision(False, "unauthenticated")

        input_data = {
            "action": {"name": action},
            "resource": {
                "type": "flexibility.commitment",
                "attributes": {"owner_id": user_id},
            },
            "subject": {
                "id": user.sub,
                "is_service": user.is_service_account,
                "scopes": (user.claims.get("scope") or "").split(),
                "groups": extract_groups(user.claims),
            },
        }
        return await self._evaluate(input_data)

    async def allow_service(self, request: Request, action: str) -> Decision:
        """Check if the caller is a service account with adequate scope."""
        from celine.sdk.auth import JwtUser
        from celine.flexibility.security.auth import get_user_from_request

        try:
            user: JwtUser = get_user_from_request(request)
        except Exception:
            return Decision(False, "unauthenticated")

        if not user.is_service_account:
            return Decision(False, "not-a-service-account")

        input_data = {
            "action": {"name": action},
            "resource": {"type": "flexibility.commitment"},
            "subject": {
                "id": user.sub,
                "is_service": True,
                "scopes": (user.claims.get("scope") or "").split(),
                "groups": [],
            },
        }
        return await self._evaluate(input_data)
