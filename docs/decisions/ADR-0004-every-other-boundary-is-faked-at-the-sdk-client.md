# ADR-0004 — every other boundary is faked at the SDK client

**Date:** 2026-08-15
**Status:** accepted

## Context

This service reaches four things it does not own — the Digital Twin, rec-registry,
nudging-tool and an MQTT broker — and all four arrive through `celine-sdk`. It also
verifies JWTs against Keycloak's JWKS.

None of them can be in the suite. Two of them (the broker, the JWKS) have no request
behind them at all.

The cut has to go somewhere, and where it goes decides what the suite can say.

## Decision

Fake each one at the **SDK client object**, written from what this code reaches for rather
than from the SDK's own classes: `FakeDTClient` exposes `.communities.fetch_values` and
`.participants.{profile,assets,fetch_values}` because those are the four calls this
service makes, and nothing else.

`JwtUser.from_token` is replaced and nothing around it is: header selection, the Bearer
fallback and the `401` mapping stay real. Signing an RS256 token and serving a JWKS would
be testing PyJWT.

Two exceptions, both stated elsewhere: the policy bundle is real (ADR-0002) and the
database is real (ADR-0003).

One boundary is faked lower, and it is worth naming. `schedule_pre_window_nudge` does not
go through the SDK client — it builds an `httpx.AsyncClient` by hand against
`settings.nudging_api_url`, so there is no client method to substitute. The transport is
replaced instead, which keeps the URL, the headers and the JSON body under test.

## Consequences

**An SDK version bump changes this service's behaviour with no file here changing and no
test that would notice.** That is the cost, and it is not small: `celine-sdk` owns the
shape of every fetch result, every event payload and the `JwtUser` this service
authorises on. A green run says the code is correct *given these shapes*.

Two things reduce it slightly and neither closes it:

- `make_user` builds a real `JwtUser` and lets the SDK's own `is_service_account` decide
  what kind of principal it is, so the fake cannot disagree with the SDK about that.
- `make_profile` constructs a real `UserMembershipSchema`, because the route narrows with
  `isinstance` and a duck-typed membership would silently take the other branch.

The MQTT listener is tested by calling `on_pipeline_run` directly with a message-shaped
object. **Nothing exercises the subscription itself** — that a broker outage means silent
non-delivery is stated in REQ-0051 to REQ-0053 and verified by nothing.

Closing the gap needs contract tests against a pinned `celine-sdk`, or the SDK publishing
example payloads the consumers can assert against. Neither exists.
