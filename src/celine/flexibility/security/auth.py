"""JWT authentication helpers, following celine-webapp/api/deps.py pattern."""
from __future__ import annotations

import logging

import jwt as pyjwt
from fastapi import HTTPException, Request

from celine.sdk.auth import JwtUser
from celine.flexibility.core.config import settings

logger = logging.getLogger(__name__)


def _extract_token(request: Request) -> str | None:
    token = request.headers.get(settings.jwt_header_name)
    if token:
        return token
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def get_user_from_request(request: Request) -> JwtUser:
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


def get_service_token(request: Request) -> JwtUser:
    """Require a service-account (client-credentials) JWT."""
    user = get_user_from_request(request)
    if not user.is_service_account:
        raise HTTPException(status_code=403, detail="Service account required")
    return user


def get_raw_token(request: Request) -> str:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")
    return token
