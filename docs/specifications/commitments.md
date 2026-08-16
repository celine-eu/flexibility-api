# Commitments

The record of a participant's decision, and the only state this service owns.

**Isolation between two participants is SQL, not policy.** No route here consults
`AccessPolicy`; the `WHERE user_id = :sub` in `src/celine/flexibility/api/commitments.py` is the whole of it.
That is why REQ-0021 and REQ-0024 are stated from both sides — the row that must be
visible and the row that must not.

A commitment is `committed`, `settled`, `rejected` or `cancelled`. Only `committed` is a
live state; the other three are terminal and nothing moves out of them.

---

### REQ-0019 — a participant creates commitments only for themselves

`POST /api/commitments` with a user token writes the caller's `sub`, whatever `user_id`
the body carries. The body field is **ignored, not rejected** — a client that sends the
wrong one gets a commitment for itself and a `201`, not a `400`.

This is the only thing standing between a participant and a commitment written against
another participant's account.

### REQ-0020 — a service account creates on behalf of a named participant

The same route with a service token honours `body.user_id`. The BFF and the pipeline both
write commitments this way; a service token has no `sub` of its own worth recording.

### REQ-0021 — a participant lists only their own commitments

`GET /api/commitments` filters on the caller's `sub`. The `user_id` query parameter is
honoured only for a service token; for a user token it is **discarded silently**, so a
participant asking for someone else's rows gets their own back rather than an error.

### REQ-0022 — a service account filters by participant, and gets nobody by default

A service token may name any `user_id`. Omitting it filters on `user_id IS NULL`, which
matches nothing.

Worth stating because the natural reading is the opposite: there is no "every
participant" listing on this route. `/export` is that, and it is scoped separately.

### REQ-0023 — listing filters by status, pages, and caps the page

`status` filters exactly. `limit` defaults to 50 and is capped at 200 — an unbounded
limit on a table that grows with every accept and decline is a way to ask this service to
read its whole history into memory. `offset` pages. Ordered by `committed_at` descending.

**`total` is the size of the page returned, not the number of matching rows.** A client
paging on it stops early, and it cannot render "showing 2 of 30". Characterised rather
than corrected: `../celine-webapp` reads the field today and changing it is a contract
change.

### REQ-0024 — only the owner cancels, and only a live commitment

`DELETE /api/commitments/{id}` matches on id **and** `user_id`, so another participant's
commitment is a `404` — indistinguishable from one that does not exist. That is the right
answer: a `403` would confirm the id is real.

A commitment that is not `committed` is refused with `409`. A settled commitment has
already been paid out in points, and cancelling it would leave a balance nothing accounts
for.

A malformed id is a `422` from the path parser, before any query runs.

### REQ-0025 — pending returns the windows that are open right now

`GET /api/commitments/pending`, service only. `committed`, `period_start <= now`,
`period_end > now`, `reminded_at IS NULL`.

The end bound is strict, so a window closing this instant is already past.

### REQ-0026 — pending stamps what it returned, in the same request

Every row it hands out gets `reminded_at = now` before the response is sent, so a second
call within the same window returns nothing.

DT polls this on every meters tick. The stamp is what turns a three-hour window into one
notification instead of thirty-six. It also means **a caller that crashes before sending
has still consumed the row** — the reminder is lost rather than retried. That is the same
trade as REQ-0045 and it is deliberate.

### REQ-0027 — settling over HTTP records points and closes the commitment

`PATCH /api/commitments/{id}/settle`, service only. Writes `status = settled`,
`reward_points_actual`, `settled_at`. Refuses anything not `committed` with `409`, because
the pipeline that calls it retries and a second settlement would overwrite the first
reading.

`CommitmentSettle.actual_kwh` is accepted, parsed, and **written nowhere** — the model has
no column for it. Only the points survive, so the reading that produced them cannot be
audited from this service.

This route and `settle_completed_windows` (REQ-0043) both settle, by different paths, and
both are live.

### REQ-0028 — export returns every participant and every status

`GET /api/commitments/export`, service only, ordered by `committed_at` ascending, with an
optional `created_after` filter on that column.

It is scoped to nobody. Declines are included — the acceptance rate is only computable
because `rejected` rows are kept.

The `flexibility.commitments.export` scope the bundle demands (REQ-0010) is **not
checked**: `PolicyMiddleware` does not match this path, so any service token reads the
whole table.

`created_after` takes an ISO timestamp, which ends in `+00:00`, and a `+` in a query
string decodes to a space. A caller that concatenates the URL by hand gets a `422` rather
than a wrong window — the better of the two failures, but a trap either way.
