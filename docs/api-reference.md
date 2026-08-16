# API Reference

Interactive OpenAPI docs at `http://localhost:8017/docs`.

This page describes the shapes. **What each route must do** is
[`docs/specifications/`](specifications/index.md), where every rule carries a `REQ-####`
and a test that names it; the requirement is the authority where the two disagree.

## Authentication

Every route below except `/health` and the documentation needs a JWT, in
`x-auth-request-access-token` (forwarded by oauth2-proxy) or `Authorization: Bearer`.
Missing or undecodable is a `401` — except on `/api/commitments/pending` and
`PATCH …/settle`, where it is a `403` (REQ-0014).

"Service only" below means a Keycloak client-credentials token. On `/api/commitments/pending`
and `PATCH …/settle` it must also carry `flexibility.read` or `flexibility.admin`; without
one the response is `403 missing flexibility scope`, straight from the policy bundle
(REQ-0054). A participant token on either route is `403 not-a-service-account`.

---

## Suggestions

### `GET /api/suggestions`

Load-shift windows for the caller's community, ranked and shaped for display. Requires a
**user** token — the Digital Twin resolves the participant from it, so these cannot be
fetched on someone's behalf.

Returns `[]`, not an error, when the caller has no community or when the Digital Twin is
unreachable (REQ-0030). At most 8 items. Windows already committed to are omitted; windows
running past 21:00 community time are not offered.

```json
[
  {
    "id": "0e0f…",
    "suggestion_type": "shift-consumption",
    "period_start": "2026-07-02T09:00:00+00:00",
    "period_end": "2026-07-02T12:00:00+00:00",
    "from_period": "",
    "clock_range": "11:00–14:00",
    "to_is_tomorrow": false,
    "to_period": "late_morning",
    "to_time": "11:00",
    "impact_kwh_estimated": 1.8,
    "reward_points": 14,
    "community_kwh": 120.0,
    "confidence": 0.8
  }
]
```

`period_start` / `period_end` are UTC; `clock_range`, `to_time` and `to_period` are
community-local (`Europe/Rome`). `impact_kwh_estimated`, `reward_points` and `confidence`
are **null when not known** — never 0 (REQ-0015).

### `POST /api/suggestions/{suggestion_id}/respond`

Record the participant's answer. Accepting creates a commitment, publishes to MQTT and
schedules a pre-window reminder; the last two are fire-and-forget and never fail the
response.

**Request:**
```json
{
  "response": "accepted",
  "period_start": "2026-07-02T09:00:00+00:00",
  "period_end": "2026-07-02T12:00:00+00:00",
  "reward_points": 14
}
```

`response` is `"accepted"` or `"declined"`. The other three are optional: an omitted
`reward_points` defaults to **10**, and window timestamps that are missing or unparseable
become **now to now + 1 hour** rather than a `400` (REQ-0033).

**Response:**
```json
{ "commitment_id": "6f3d…", "status": "committed", "reward_points_estimated": 14 }
```

A decline returns `commitment_id: null`, `status: "declined"` and
`reward_points_estimated: 0` — while storing a `rejected` row that keeps the estimate that
was sent (REQ-0035).

---

## Commitments

### `POST /api/commitments`

Create a commitment directly. `201`.

A **user** token always writes its own `sub`; the `user_id` in the body is ignored, not
rejected. A **service** token writes the `user_id` given (REQ-0019, REQ-0020).

```json
{
  "user_id": "user-alice",
  "suggestion_id": "0e0f…",
  "suggestion_type": "shift-consumption",
  "community_id": "it-energy-community",
  "device_id": "sensor-alice-1",
  "period_start": "2026-07-02T09:00:00+00:00",
  "period_end": "2026-07-02T12:00:00+00:00",
  "reward_points_estimated": 14
}
```

### `GET /api/commitments`

List commitments. A user token sees only its own and the `user_id` parameter is ignored; a
service token must name a `user_id` or it matches nothing (REQ-0021, REQ-0022).

**Query params:**
- `user_id` — service tokens only
- `status` — `committed` | `settled` | `rejected` | `cancelled`
- `limit` — default 50, **max 200** (`422` above that)
- `offset`

Ordered by `committed_at` descending. **`total` is the size of the page returned, not the
number of matching rows** (REQ-0023).

### `DELETE /api/commitments/{commitment_id}`

Cancel. `204`.

Owner only — another participant's commitment is a `404`, not a `403`. Anything not
`committed` is a `409` (REQ-0024).

### `GET /api/commitments/pending`

**Service only.** Commitments whose window is open *right now* and that have not yet been
reminded — not "the caller's pending commitments".

Every row returned is stamped `reminded_at = now` in the same request, so a second call
returns nothing (REQ-0026).

### `PATCH /api/commitments/{commitment_id}/settle`

**Service only.** Settle with measured points. Anything not `committed` is a `409`.

```json
{ "reward_points_actual": 9, "actual_kwh": 0.9 }
```

`actual_kwh` is accepted and **discarded** — there is no column for it (REQ-0027).

### `GET /api/commitments/export`

**Service only.** Every commitment of every participant, in every status, ordered by
`committed_at` ascending. Used by the `rec_flexibility_commitments` pipeline mirror.

**Query params:**
- `created_after` — ISO datetime, filters on `committed_at`. URL-encode it: the `+00:00`
  offset decodes to a space otherwise and the request is a `422`.

The `flexibility.commitments.export` scope the policy defines is **not enforced on this
route** — `PolicyMiddleware` does not match this path, so any service token reaches every
commitment in the table (REQ-0028). Unlike `/pending` and `/settle`, this one was not
closed by [#21](https://github.com/celine-eu/flexibility-api/issues/21); it is a routing
gap, not an evaluation one.

---

## Health

### `GET /health`

`{"status": "ok"}`, unauthenticated.

Liveness only. It does not check the database, the broker, or whether the policy bundle
loaded — a process that has lost every dependency still reports `ok` (REQ-0004).
