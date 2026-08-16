# Identity

Who the caller is. Every route but `/health` and the documentation needs a token, and
this service verifies it itself — there is no gateway doing it first.

---

### REQ-0001 — the token is read from the proxy header, with a Bearer fallback

`x-auth-request-access-token` is checked first, then `Authorization: Bearer`. The header
name is `settings.jwt_header_name` and is configurable.

Both paths exist because both callers exist: the participant app reaches this service
through oauth2-proxy, which forwards the token in the proxy header, while
`../celine-ai-assistant` and every service-to-service call send a Bearer token directly.

The proxy header wins when both are present.

### REQ-0002 — a request with no token is refused

`401 Missing authentication token`, before any handler runs.

The two routes `PolicyMiddleware` guards are the exception — see REQ-0014.

### REQ-0003 — a token that will not decode is a `401`, never a `500`

Expired, malformed, wrong issuer, JWKS unreachable: every failure of `JwtUser.from_token`
maps to `401`. A `500` would report a caller's problem as a service problem and page the
wrong people.

The distinction between the causes is in the response detail and nowhere else — an
unreachable JWKS endpoint and a forged token are the same status to the client, which is
correct for the client and unhelpful for an operator.

### REQ-0004 — the health check and the API documentation need no token

`/health`, `/docs`, `/redoc` and `/openapi.json` pass `PolicyMiddleware` unauthenticated.
The liveness probe has no token to present.

`/health` returns `{"status": "ok"}` unconditionally. **It does not check the database,
the broker, or whether the policy bundle loaded** — a process that is up but has lost
every one of its dependencies reports healthy. That is deliberate for a liveness probe
and wrong for a readiness one; there is no readiness endpoint.
