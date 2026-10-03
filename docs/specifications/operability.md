# Operability

Running, degrading, refusing to start, and the entry point that is not a request.

`pipeline_listener` subscribes to `celine/pipelines/runs/+` and is **the only way work
starts in this service that a participant did not ask for**. Nothing calls this service to
make a pipeline event happen, so a broker that is down means opportunities stop being
scheduled and commitments stop being settled — with no failing response anywhere to
notice.

---

### REQ-0055 — outside development, the dev defaults refuse startup

`core/config.py` ships zero-config development defaults on purpose: the local stack's
database password, `CELINE_OIDC_CLIENT_SECRET` equal to the client id
(`svc-flexibility`), and the SDK's local Keycloak as issuer and JWKS. They are safe only
because the lifespan refuses them, **first** — before the MQTT broker is created and
before the settlement fallback is scheduled — unless the environment is development.

The signal is `celine.sdk.posture`'s: `CELINE_ENV`, then `ENVIRONMENT`; **only `dev`
relaxes**. Unset, empty, `staging`, `prod` or a typo is hardened. `task run` exports
`CELINE_ENV=dev`; `CELINE_ENV=staging task run` is the prod-like local mode.

Outside dev, startup raises `InsecureConfiguration` naming every violation at once:

- `DATABASE_URL` carries a development password (`securepassword123`, `postgres`, or a
  trivially weak one);
- `CELINE_OIDC_CLIENT_SECRET` is empty or equal to the client id;
- `CELINE_OIDC_BASE_URL` / `CELINE_OIDC_JWKS_URI` were not set, so the SDK's local
  default is in use;
- the policy bundle (`policies/flexibility.rego`) did not load — REQ-0011.

In dev the same values are logged as one warning and startup proceeds.

### REQ-0051 — a message this service cannot use is dropped, not raised

A payload that will not parse as a `PipelineRunEvent`, a run whose status is not
`completed`, and a flow this service does not handle are all ignored.

The status filter matters more than it looks: a `started` event carries the same flow name
as the `completed` one that follows it, so acting on both would settle every window twice
and send every reminder twice.

The subscription is a wildcard and every pipeline in the platform publishes to it. An
exception escaping the handler would take down the subscription for **every subsequent
message**, not just the malformed one, so nothing is allowed to propagate.

### REQ-0052 — three flows, three handlers

| Flow | Does |
|---|---|
| `meters-flow` | `send_pending_reminders` (REQ-0044) |
| `rec-forecasting-flow` | `notify_flexibility_opportunity` (REQ-0049) |
| `rec-flexibility-flow` | `settle_completed_windows` (REQ-0038) for the **previous day** |

`meters-flow` completes roughly every five minutes, which is what makes it the tick the
reminder loop rides on. **There is no scheduler in this service** — if that flow stops,
reminders stop.

Settlement runs against the day *before* the event's timestamp. The pipeline runs after
midnight and what it has just computed is yesterday's metered data; settling the event's
own date would find no readings, skip every commitment, and never revisit them, because
the next run would look at the wrong day too.

An unparseable timestamp falls back to yesterday by the wall clock, so a malformed event
still settles a day rather than skipping one silently.

### REQ-0053 — a message arriving before startup finished is dropped

The Digital Twin, registry and nudging clients are created in `create_broker()` and each
branch guards for a `None` one. The event is lost rather than queued — there is no queue
behind this.

`get_broker()` and `get_nudging_client()` expose what startup built. The accept path in
`src/celine/flexibility/api/suggestions.py` reaches both through these accessors rather than importing the
globals, which is what lets it publish and schedule without a second set of credentials.

MQTT failing at startup is **non-fatal**: the API still serves requests, and reminders and
nudges do not fire until the broker reconnects. `/health` reports `ok` throughout
(REQ-0004).
