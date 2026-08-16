# Notifications

Two kinds, sent to `../nudging-tool`, both triggered by a pipeline completing rather than
by a request.

- **`flexibility_reminder`** — this participant committed to a window and it has opened,
  or is about to.
- **`flexibility_opportunity`** — the community is forecast to export surplus; anyone may
  act on it.

Every failure below is swallowed. There is no caller to return an error to, so the only
failure mode is silence.

---

### REQ-0044 — a reminder goes out once the window has opened

`send_pending_reminders` selects `committed` rows with `period_start <= now`,
`period_end > now` and `reminded_at IS NULL`, and sends one `flexibility_reminder` per
row carrying the commitment id, the window in `HH:MM` and the estimated points.

The end bound is strict — a window closing this instant is already past, and a reminder
nobody can act on is worse than none. A window that has not opened is not reminded
either; the notification says the window is open *now*.

Only `committed` rows. A participant who cancelled must not be reminded of a commitment
they withdrew.

The times are formatted from **UTC**. That is not the community-local clock the suggestion
list labels windows with (REQ-0016), so the two surfaces disagree by two hours in summer.

### REQ-0045 — `reminded_at` is committed before the nudges are sent

The stamp is written and committed first, then the loop dispatches.

The order bounds the damage when nudging-tool is down: the reminder is lost, and it is
lost **once**. Sending first and stamping after would resend on every `meters-flow` tick
for as long as the outage lasted, and the participant would be notified repeatedly about
a window they had already been told about.

Losing it silently is the accepted cost. It also means a `send_pending_reminders`
returning 0 says nothing about whether a reminder was owed.

Dispatch is wrapped per row, so an event nudging-tool rejects costs one participant a
reminder rather than everyone after them in the loop — and that participant is never
retried, because their row is already stamped.

### REQ-0046 — a pre-window nudge is scheduled thirty minutes ahead

On accept. `POST {nudging}/admin/scheduled-events` with `trigger_at = window_start - 30
minutes` and `external_key = flexibility-accept:{user_id}:{suggestion_id}`.

A window opening within the half hour — or already open — is **ingested immediately**
instead: a trigger time in the past would be a scheduled event that never fires, and
accepting an already-open window is legitimate because the suggestion list shows every
open window.

The two paths are exclusive. A scheduled nudge is not also sent now, and an immediate one
is not also scheduled.

The `external_key` is what stops a participant who accepts, cancels and accepts again
from getting two reminders for one window. **That deduplication lives in
`../nudging-tool`, not here**, so the guarantee is invisible from this repository.

The facts carry `window_start`/`window_end` as `HH:MM` UTC and `period` as a date. A
window parsed out of the request body without an offset is naive, and is read as UTC —
treating it as local time would shift every notification by two hours.

### REQ-0047 — a nudge that cannot be scheduled is dropped quietly

No token provider on the nudging client: nothing is scheduled, nothing raises, one log
line. An empty `community_id` is sent as `""` rather than null, because
`DigitalTwinEvent.from_dict` would reject the null — after the commitment had already
been written.

The accept must succeed either way (REQ-0037).

### REQ-0048 — an opportunity window is consecutive forecast export hours

From `rec_forecast` over the next 24 hours. An hour counts when its prediction is
**strictly above** 0.5 kW — exactly at the threshold is inside the noise of a forecast.
Hours before 05:00 are dropped whatever they predict.

Consecutive hours merge into one window; a gap splits them, because merging across a
non-exporting hour would invite a participant to shift load into an hour with no surplus
to absorb it. A window must span at least an hour, which a single row already does.

A forecast row missing its timestamp or its prediction, or carrying a string where a
number belongs, is skipped — it costs that hour, not the notification.

### REQ-0049 — the earliest window is broadcast to every member

`windows[0]` — the earliest, not the largest. One opportunity per forecast run, whatever
else the day holds; a bigger surplus later is never mentioned.

One nudge per community member, with `reward_points = round(estimated_kwh × 10)` — the
same scaling settlement applies to the measured reading (REQ-0039). A member row with no
linked user account is skipped.

There is no per-participant filtering at this stage. The personal figures arrive later,
when the participant opens the suggestion list.

### REQ-0050 — every failure on this path is silent

A forecast fetch that raises, an empty forecast, a registry lookup that fails: nobody is
notified and nothing is reported. One rejected nudge costs that member and the broadcast
continues.

**The community is hard-coded.** Both the forecast fetch and the member list name
`it-energy-community` literally, so a second community would be silently ignored — no
error, no nudge, nothing to notice. Unlike the read path, which resolves the community
from the caller's profile, this one cannot.
