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

        # Service-only endpoints. `/export` is its own action: it returns every
        # participant's commitments, so it takes `flexibility.commitments.export`
        # and not merely a service account.
        action = None
        if path.endswith("/pending") or ("/settle" in path and method == "PATCH"):
            action = "service"
        elif path.rstrip("/").endswith("/commitments/export"):
            action = "export"
        if action:
            d = await self.policy.allow_service(request, action)
            if not d.allowed:
                return JSONResponse(
                    {"detail": d.reason or "Service access required"}, status_code=403
                )

        return await call_next(request)
