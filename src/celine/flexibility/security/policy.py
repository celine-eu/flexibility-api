"""OPA access policy for the flexibility API.

Evaluates decisions using the celine.sdk.policies engine loaded from
./policies/flexibility.rego.  Falls back to permissive if policies are
not configured (dev/test convenience).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

logger = logging.getLogger(__name__)

_POLICIES_DIR = Path(__file__).parent.parent.parent.parent.parent / "policies"


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

    async def _evaluate(self, package: str, input_data: dict) -> Decision:
        if self._engine is None:
            return Decision(True, "no-policy-engine")
        try:
            result = self._engine.evaluate(package, input_data)
            return Decision(
                allowed=bool(result.get("allow", False)),
                reason=result.get("reason"),
            )
        except Exception as exc:
            logger.warning("OPA evaluation error: %s", exc)
            return Decision(True, "policy-error-permissive")

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
                "groups": user.claims.get("groups", []),
            },
        }
        return await self._evaluate("celine/flexibility/access", input_data)

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
        return await self._evaluate("celine/flexibility/access", input_data)
