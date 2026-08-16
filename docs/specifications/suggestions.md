# Suggestions

The participant-facing read path, and the accept that turns an offer into a commitment.

How a window is shaped into an offer is [windows](windows.md). What is stated here is
everything around that: the Digital Twin calls, how the route degrades when they fail,
and the four side effects of an accept.

`_get_dt_client` builds a `DTClient` **inside the request**, from the caller's own token,
because DT resolves the participant entity from the user JWT. There is no shared client
on this path and no service token — which is why a participant's suggestions cannot be
fetched on their behalf.

---

### REQ-0029 — a community member is offered the community's windows

`GET /api/suggestions` resolves the caller's community from their DT participant profile,
fetches `rec_flexibility_windows` for it, and returns the shaped list.

### REQ-0030 — no community, no windows, and no error

A participant whose profile carries no membership gets `[]`. So does one whose profile
lookup failed, and one whose window fetch failed.

All three render as "no suggestions today". A Digital Twin outage is therefore **the wrong
story told plausibly** — but it is the right failure mode for a screen the participant
cannot act on anyway, and the alternative is an error page that says just as little.

### REQ-0031 — personal enrichment is best-effort and never hides the window

The per-device fetch happens after the community one and its failure is caught. A
participant whose device is unreachable still sees the community's opportunity, with the
personal figures null.

A participant with no metered device is not fetched for at all — the enrichment is
skipped rather than requested and discarded.

### REQ-0032 — a window already committed to is not offered again

Filtered on this participant's `committed` rows. So a window they cancelled reappears,
and a window another participant accepted still shows.

### REQ-0033 — accepting records the commitment against the caller

`POST /api/suggestions/{id}/respond` with `response: "accepted"` writes a `committed` row
carrying the caller's `sub`, the suggestion id, and the resolved community and device.
Both are stored because settlement needs them later and neither is recoverable from the
suggestion id.

Two defaults worth knowing:

- `reward_points` omitted means **10**, flat, unrelated to the window. It is what the
  participant is told they have earned until settlement replaces it with the measured
  figure — so a client that omits the field promises the same reward for every window
  regardless of size.
- A `period_start` or `period_end` that will not parse becomes **now to now + 1 hour**,
  not a `400`. The commitment is recorded against a window the participant did not
  choose, and settlement will later fetch readings for that hour. A client bug here
  produces a plausible wrong settlement rather than a visible failure.

### REQ-0034 — a commitment is recorded even when nothing can be resolved

If the profile and asset lookups both fail, the row is still written, with
`community_id` and `device_id` null and status `committed`.

The participant's decision is the thing of value and it is kept whatever the Digital Twin
says. What it costs is settlement: a row with no community or no device is skipped
forever by `settle_completed_windows` (REQ-0041), so **this commitment can never be paid
out**, and nothing anywhere reports that.

### REQ-0035 — a decline is recorded and awards nothing

`response: "declined"` writes a `rejected` row and returns
`{"commitment_id": null, "status": "declined", "reward_points_estimated": 0}`.

No DT call, no publish, no nudge — a decline is a row and nothing else, which is why a
declined row carries neither community nor device.

The response reports zero points; **the row keeps whatever estimate the client sent**. The
two disagree, and `/export` shows the row's value.

### REQ-0036 — accepting publishes to MQTT, and a broker failure does not fail it

`celine/flexibility/committed/{user_id}`, at least once, carrying the commitment id, the
window, the community, the device and the estimated points. It is how the rest of the
platform learns about a commitment without polling this service.

No broker, a disconnected broker, or a publish that raises: all three are logged and the
response is still a `200`. The commitment is already in the database before the publish
is attempted — failing the response now would tell the participant their commitment was
not recorded when it was, and they would accept again, producing a second row.

### REQ-0037 — accepting schedules a pre-window nudge, and a failure does not fail it

Scheduled through the nudging client built at startup, with the window, the suggestion id
and the estimated points. Absent client, or a scheduling call that raises: logged,
`200` returned.

The reasoning is in the companion's knowledge. A notification
service being briefly unreachable is not a reason to reject a commitment the participant
has already made, and `send_pending_reminders` (REQ-0044) recovers the notification once
the window opens.

**"The accept succeeded" is therefore no evidence that a reminder exists.** They are two
claims and only the first is observable from the response.
