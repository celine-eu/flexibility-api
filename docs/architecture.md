# Architecture

What the service is and how the parts fit. **What it must do** is
[`docs/specifications/`](specifications/index.md); **why** a technical choice was made is
[`docs/decisions/`](decisions/index.md).

## Overview

flexibility-api owns one thing: the record of a participant's decision to shift load, from
the moment they accept an offer to the moment it is settled against their meter.

It computes no flexibility windows. Those arrive from the Digital Twin, already computed
by the pipelines; this service shapes them into offers, records the answer, and later asks
what the meter said.

```
                    ┌─ meters-flow ──────────► send_pending_reminders ──► nudging-tool
MQTT                │
celine/pipelines/── ├─ rec-forecasting-flow ─► notify_flexibility_opportunity ──► nudging-tool
runs/+              │
                    └─ rec-flexibility-flow ─► settle_completed_windows ──► PostgreSQL
                                                        ▲
                                                        │ rec_settlement_1h
                                               Digital Twin

participant ──► celine-webapp (BFF) ──► GET  /api/suggestions      ──► Digital Twin
                                        POST /api/suggestions/{id}/respond
                                                   │
                                                   ├─► PostgreSQL (the commitment)
                                                   ├─► MQTT  celine/flexibility/committed/{user}
                                                   └─► nudging-tool (pre-window reminder)
```

**No front end talks to this service directly.** The participant app reaches it through
the BFF in `../celine-webapp`, and `../celine-ai-assistant` reaches it through
`celine.sdk.flexibility`. A user-visible flexibility bug may be in the BFF's composition
rather than here.

## The two halves, and why they differ

The read path is **visible**: a wrong suggestion looks wrong on the screen.

The settlement path is **invisible**: a wrong settlement is a plausible number, reported
as a success by this service's own logs and read by the participant through a BFF. Nobody
is positioned to notice it. That asymmetry is why the settlement arithmetic is written out
in [`specifications/settlement.md`](specifications/settlement.md) rather than left to the
code.

## Service dependencies

Everything CELINE arrives through the **`celine-sdk` package**.

| Service | Through | For |
|---|---|---|
| **Digital Twin** | `celine.sdk.dt.client` | flexibility windows, per-device forecasts, settlement readings |
| **nudging-tool** | `celine.sdk.nudging.client`, plus one raw `httpx` call | reminders and opportunity nudges |
| **rec-registry** | `celine.sdk.rec_registry.client` | community membership, for the opportunity broadcast |
| **Keycloak** | `celine.sdk.auth` | identity, via oauth2-proxy or a Bearer token |
| **MQTT broker** | `celine.sdk.broker` | pipeline completion events |

**An SDK version bump changes this service's behaviour with no file here changing** — see
[ADR-0004](decisions/ADR-0004-every-other-boundary-is-faked-at-the-sdk-client.md).

## Database

PostgreSQL, async via SQLAlchemy and asyncpg, in the `flexibility` schema. One model:

`FlexibilityCommitment` — `user_id`, `suggestion_id`, `suggestion_type`, `community_id`,
`device_id`, `period_start`, `period_end`, `committed_at`, `settled_at`, `reminded_at`,
`status`, `reward_points_estimated`, `reward_points_actual`.

Status is `committed`, `settled`, `rejected` or `cancelled`. Only `committed` is live; the
other three are terminal. `rejected` is a **decline** — declines are kept so the
acceptance rate is computable.

`community_id` and `device_id` are nullable, and both are resolved best-effort when the
commitment is created. A row missing either can never be settled, and nothing reports that
(REQ-0034, REQ-0041).

Alembic manages migrations. **No test runs against them** — the suite builds its schema
from the models, so a model that has drifted from its migrations passes everything
([ADR-0003](decisions/ADR-0003-the-database-is-real-and-sqlite.md)).

## Authorisation

`policies/flexibility.rego` is evaluated **in process** by `celine.sdk.policies` — no OPA
server. It is reached from exactly one place, `PolicyMiddleware`, guarding two routes.

Everything else is SQL: the participant-facing routes never consult a policy, and
`WHERE user_id = :sub` is the whole of the separation between two participants.

**The evaluation fails open.** A bundle that will not load and an `allow` query that
raises both return an allow, and neither is distinguishable from a policy that ran and
permitted. The reason string is the only signal — `no-policy-engine` and
`policy-error-permissive` are what an intolerant deployment must alert on.

Until 2026-08-15 that fallback was taken on **every** call: the wrapper built its query by
joining the package path with slashes, which is not valid Rego, so the bundle was never
consulted ([#21](https://github.com/celine-eu/flexibility-api/issues/21)); and the bundle
itself raised a rule conflict on the plainest denial there is
([#22](https://github.com/celine-eu/flexibility-api/issues/22)). Both are fixed and stated
as REQ-0011, REQ-0012 and REQ-0054.

Whether authorisation should fail *closed* instead is still open — nobody has been asked,
and a bad bundle taking the service down is a real cost.

## Pipeline listener

`src/celine/flexibility/services/pipeline_listener.py` subscribes to `celine/pipelines/runs/+` and is the only
way work starts here that a participant did not ask for.

**Nothing calls this service to make a pipeline event happen.** A broker that is down means
reminders stop, opportunities stop being broadcast, and commitments stop being settled —
with no failing response anywhere to notice. `/health` reports `ok` throughout.

## Settlement

`src/celine/flexibility/services/settlement.py`, triggered by `rec-flexibility-flow` completing after midnight,
against the **previous** day.

1. Select `committed` commitments whose window lies inside that day.
2. Fetch `rec_settlement_1h` from the Digital Twin's community domain, for each
   commitment's exact window and device.
3. `reward_points_actual = round(sum(consumption_kwh) × 10)`.
4. Write `status = settled`, the points and `settled_at`.

The community domain is used rather than the participant one because settlement runs under
a service token. A missing reading leaves the commitment open for the next run; it is
never settled as zero.

The exact rules, including the banker's rounding, are
[`specifications/settlement.md`](specifications/settlement.md).

## Tests

```bash
task test          # uv run pytest
```

No external service is required — not PostgreSQL, not the broker, not an OPA server. CI
runs the same suite on 3.12 and 3.13 on every push and pull request, and additionally
checks that the policy bundle loads, that no denial produces a rule conflict, that every
submodule imports, and that the requirement trace holds in both directions
(`.github/workflows/test.yaml`).

The procedure, and what a green run does and does not prove, is
the companion's testing playbook.
