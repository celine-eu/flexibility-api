# Windows

How a community surplus window becomes something a participant is offered.
`_build_suggestions` is the whole of it: pure, and the only part of the read path with no
I/O in it.

The input is two fetches that have already happened — the community's
`rec_flexibility_windows` rows, and the same fetcher run per device — plus the set of
suggestion ids this participant has already committed to.

---

### REQ-0015 — the community window is the offer; personal figures decorate it

A window is offered on community membership alone. `impact_kwh_estimated`,
`reward_points` and `confidence` are filled in from the per-device forecast when there is
one and left **null** when there is not.

Null rather than zero, and the distinction is load-bearing: zero reads as "worth nothing
to you", which is a different claim from "not known". The UI hides the figure on null and
renders it on zero.

There is no minimum. A window worth 0.08 kWh to one household is still shown — the
community benefit is the reason the window exists, and a threshold here would hide a
community event behind one household's forecast.

Two details that follow from that:

- An enrichment row carrying `reward_points_estimated: null` collapses to **0** points,
  not null, because the window *is* enriched — it has a forecast, and the forecast says
  nearly nothing.
- Enrichment is keyed on `(window_start, window_end)`, and the keys arrive naive from the
  per-device fetcher while the community rows are normalised to UTC. Both sides are
  normalised before the lookup. **A miss here is silent** — the window is offered with no
  personal figures and looks merely unprofitable.

### REQ-0016 — timestamps are UTC-aware, labels are community-local

`period_start` and `period_end` are emitted as aware UTC ISO strings. `clock_range`,
`to_time` and `to_period` are rendered in `Europe/Rome`.

The gold `rec_flexibility_windows` table stores `timestamp without time zone` holding
UTC. Emitting those values naively made the participant app read them as local time —
two hours early in summer — while commitments, stored tz-aware, rendered correctly. The
same window appeared twice, at different hours, depending on which side it came from.

The machine-readable fields and the labels must never disagree about the instant, which
is why one function does both.

### REQ-0017 — a window that runs past 21:00 local is not offered

Evaluated in community time, not UTC. A window ending exactly at 21:00 is kept; 21:01 is
not, and neither is one that crosses midnight.

Nobody is asked to shift load late at night. The cutoff is a product rule, and it is
applied after localisation — applying it to the UTC hour would move it by an hour twice a
year.

### REQ-0018 — ranking, the cap, and the malformed row

Ranked with enriched windows first, by personal impact descending, then everything else
by community surplus descending. Nothing is dropped for lacking enrichment.

At most **eight** are returned. That is a ceiling on the response size, not a curation
rule: a day producing more than eight open windows has some silently withheld, and
nothing says which.

A row that cannot be read — no `window_start`, no `_id`, an unparseable timestamp — is
skipped and logged, and the rest of the list is returned. The rows are another service's
output, and the alternative is a participant seeing nothing because one gold row was
written badly.
