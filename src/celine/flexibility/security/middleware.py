from celine.sdk.audit import audit_denied
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
        action = audited = None
        if path.endswith("/pending"):
            action, audited = "service", "flexibility.commitments.pending"
        elif "/settle" in path and method == "PATCH":
            action, audited = "service", "flexibility.commitments.settle"
        elif path.rstrip("/").endswith("/commitments/export"):
            action, audited = "export", "flexibility.commitments.export"
        if action:
            d = await self.policy.allow_service(request, action)
            if not d.allowed:
                # Returned, not raised, so recorded here (REQ-0057). No route has been
                # matched yet: the record carries the method and no route.
                audit_denied(audited, caller=d.caller, reason=d.reason, request=request)
                return JSONResponse(
                    {"detail": d.reason or "Service access required"}, status_code=403
                )

        return await call_next(request)
