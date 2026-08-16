# Settlement

What a commitment was finally worth, computed from metered consumption once the window
has closed.

**This is the half of the service where a defect is invisible.** A wrong suggestion looks
wrong; a wrong settlement is a plausible number, reported as a success by this service's
own logs and read by the participant through a BFF. Nobody is positioned to notice it,
which is why the arithmetic below is stated digit by digit.

Triggered by `rec-flexibility-flow` completing (REQ-0052), which runs after midnight
against the previous day.

---

### REQ-0038 — only a committed window lying inside the period is settled

`status == "committed"`, `period_start >= 00:00` of the period and `period_end <= 24:00`
of it, in UTC.

The bounds are containment, not overlap, so **a window crossing midnight belongs to
neither day and is settled by nothing**. Nothing reports that; the row simply stays
`committed`.

The status filter is what makes the operation safe to repeat, and it has to be: the
pipeline that triggers it runs on every completion. Settling twice would overwrite
`reward_points_actual` with a second reading.

With nothing open for the period, the Digital Twin is not called at all — most ticks have
nothing to settle.

### REQ-0039 — points are the summed consumption times ten, rounded

`reward_points_actual = round(sum(consumption_kwh) × 10)`.

`rec_settlement_1h` returns one row per hour of the window, and the reward is for the
whole window, so the rows are summed before scaling. A reading that will not parse — a
string, a null, a missing field — counts as **zero** rather than failing the batch: one
malformed hour costs that participant that hour's points, where raising would leave every
other participant's commitment unsettled too.

`round()` is Python's, which is banker's rounding. **0.25 kWh scores 2 and 0.35 kWh scores
4.** Two participants who shifted a quarter-kilowatt-hour apart can round in opposite
directions. Nobody has said this is wrong; it is written down so that changing it is a
decision rather than an accident.

The same ×10 appears in the opportunity nudge (REQ-0049). If one changes without the
other, every notification promises a reward the settlement will not pay.

### REQ-0040 — the reading is fetched for the commitment's exact window

The commitment's own `period_start` and `period_end` are sent to `rec_settlement_1h`,
with its `device_id`, against the community domain.

Not the day. A settlement fetched for the whole period would credit the participant for
consumption outside the window they committed to.

The **community** domain is used rather than the participant one because settlement runs
under a service token, and the participant domain needs a user JWT for REC Registry
entity resolution. Access is enforced downstream by dataset-api, on the
`dataset.query` scope that `svc-flexibility` carries.

### REQ-0041 — a commitment with no community or no device is skipped

Both columns are nullable and both are resolved best-effort at accept time (REQ-0034), so
a commitment can legitimately reach settlement without one.

It stays `committed` **forever**. There is nothing to fetch a reading against and no
retry that would ever succeed, and nothing raises an alarm about it.

### REQ-0042 — a failed or empty reading leaves the commitment open

A Digital Twin call that raises, and a result with `count == 0`, both leave the row
`committed` for the next run to pick up.

No data is not the same as no consumption. Settling an absent reading as zero would take
the participant's reward away for a measurement that had not arrived yet, and there is no
path back out of `settled`.

Settlement is a batch over every open commitment of the day and the failure is caught per
row, so one unreachable device costs that participant their settlement and not everyone's.

### REQ-0043 — settling writes status, points and time together, and commits once

`status = "settled"`, `reward_points_actual`, `settled_at`, in one transaction, committed
after the whole batch.

A row that is `settled` with no `settled_at`, or with no `reward_points_actual`, is not a
state any reader handles.

`reward_points_estimated` is never revised. The gap between the estimate the participant
accepted and the actual they were paid is the only record of how good the forecast was.
