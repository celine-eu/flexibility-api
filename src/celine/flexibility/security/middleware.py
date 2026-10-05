from celine.sdk.audit import audit_denied
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Host, Match, Mount

from .policy import AccessPolicy


def route_template(request: Request) -> str | None:
    """The template of the route this request will reach, or ``None``.

    The middleware refuses before routing, so the request carries no matched route
    yet. This resolves it the way the router will: the first route matching the
    path and the method, else the first matching the path alone (the router answers
    that one ``405``). It is the template (``/api/commitments/{commitment_id}/settle``),
    never the raw path, which carries commitment ids. A path no route serves, or one
    under a mount, gives ``None``: the record then carries no route, as before
    routing (REQ-0057).
    """
    router = getattr(request.scope.get("app"), "router", None)
    found = None
    for route in getattr(router, "routes", ()):
        template = getattr(route, "path_format", None)
        if not template or isinstance(route, (Mount, Host)):
            continue
        # A regex match on the path and a method check: no handler, no dependency.
        match, _ = route.matches(request.scope)
        if match is Match.FULL:
            found = template
            break
        if match is Match.PARTIAL and found is None:
            found = template
    return (request.scope.get("root_path") or "") + found if found else None


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
                # matched yet, so the record names the template the router will match.
                audit_denied(
                    audited,
                    caller=d.caller,
                    reason=d.reason,
                    request=request,
                    route=route_template(request),
                )
                return JSONResponse(
                    {"detail": d.reason or "Service access required"}, status_code=403
                )

        return await call_next(request)
