from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .policy import AccessPolicy


class PolicyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, policy: AccessPolicy | None = None):
        super().__init__(app)
        self.policy = policy or AccessPolicy()

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method.upper()

        # Public paths — no policy check
        if path in {"/health", "/docs", "/redoc", "/openapi.json"}:
            return await call_next(request)

        # Service-only endpoints
        if path.endswith("/pending") or (
            "/settle" in path and method == "PATCH"
        ):
            d = await self.policy.allow_service(request, "service")
            if not d.allowed:
                return JSONResponse(
                    {"detail": d.reason or "Service access required"}, status_code=403
                )

        return await call_next(request)
