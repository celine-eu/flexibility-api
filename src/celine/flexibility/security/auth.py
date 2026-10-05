"""JWT authentication helpers, following celine-webapp/api/deps.py pattern.

A token that was presented and refused (``401``) and a verified caller refused for what
it is (``403``) each leave one ``celine.audit`` record (REQ-0057). A request carrying no
token at all is not recorded: there is no caller to name.
"""
from __future__ import annotations

import logging

import jwt as pyjwt
from celine.sdk.audit import audit_denied
from celine.sdk.auth import JwtUser
from fastapi import HTTPException, Request

from celine.flexibility.core.config import settings

logger = logging.getLogger(__name__)

# The action a refusal at the token check is recorded under.
AUTHENTICATE = "flexibility.authenticate"


def _extract_token(request: Request) -> str | None:
    token = request.headers.get(settings.jwt_header_name)
    if token:
        return token
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def verify_request(request: Request) -> JwtUser:
    """The verified caller, or ``HTTPException(401)``. Records nothing.

    ``AccessPolicy`` calls this and turns the failure into its own decision, which
    ``PolicyMiddleware`` records; the dependency below records its own.
    """
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    try:
        return JwtUser.from_token(token, oidc=settings.oidc)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except pyjwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {e}")


def _token_failure(exc: HTTPException) -> str:
    if exc.detail == "Token has expired":
        return "token_expired"
    if str(exc.detail).startswith("Invalid token"):
        return "token_invalid"
    return "token_unverified"


def get_user_from_request(request: Request) -> JwtUser:
    try:
        return verify_request(request)
    except HTTPException as exc:
        if _extract_token(request):
            # The claims of a token that failed verification are not trusted, so the
            # record names no caller.
            audit_denied(AUTHENTICATE, reason=_token_failure(exc), request=request)
        raise


def get_service_token(request: Request) -> JwtUser:
    """Require a service-account (client-credentials) JWT."""
    user = get_user_from_request(request)
    if not user.is_service_account:
        audit_denied(
            "flexibility.service", caller=user, reason="not-a-service-account", request=request
        )
        raise HTTPException(status_code=403, detail="Service account required")
    return user


def get_raw_token(request: Request) -> str:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    return token
