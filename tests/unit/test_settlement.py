"""Settlement — what a commitment is finally worth.

This is the half of the service where a defect is invisible. A wrong suggestion looks
wrong; a wrong settlement is a plausible number, reported as a success by this service's
own logs and read by the participant through a BFF. Nobody is positioned to notice it,
which is why the arithmetic is pinned here digit by digit.

The Digital Twin is faked at `dt.communities.fetch_values`. Everything below it — the
query that selects which rows are due, the summation, the rounding, the status
transition and the single commit — is the code that ships.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from celine.flexibility.models.commitment import FlexibilityCommitment
from celine.flexibility.services.settlement import settle_completed_windows

from tests.conftest import COMMUNITY, DEVICE, USER_SUB
from tests.fakes import FakeDTClient, FetchResult

PERIOD = date(2026, 7, 2)


def commitment(
    *,
    status: str = "committed",
    start: datetime | None = None,
    end: datetime | None = None,
    community_id: str | None = COMMUNITY,
    device_id: str | None = DEVICE,
    user_id: str = USER_SUB,
    points_estimated: int = 12,
) -> FlexibilityCommitment:
    """A commitment whose window sits inside PERIOD unless told otherwise."""
    return FlexibilityCommitment(
        user_id=user_id,
        suggestion_id="w1",
        suggestion_type="shift-consumption",
        community_id=community_id,
        device_id=device_id,
        period_start=start or datetime(2026, 7, 2, 9, tzinfo=timezone.utc),
        period_end=end or datetime(2026, 7, 2, 12, tzinfo=timezone.utc),
        status=status,
        reward_points_estimated=points_estimated,
    )


@pytest.fixture
def dt() -> FakeDTClient:
    return FakeDTClient()


def settlement_rows(*kwh: float) -> FetchResult:
    return FetchResult([{"consumption_kwh": v} for v in kwh])


async def arrange(db, *rows: FlexibilityCommitment) -> list[FlexibilityCommitment]:
    db.add_all(rows)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return list(rows)


async def reload(db, row: FlexibilityCommitment) -> FlexibilityCommitment:
    """Re-read the row from the database rather than trusting the in-memory copy.

    `settle_completed_windows` mutates the very objects the session handed it, so
    asserting on `row.status` directly would pass even if nothing were ever committed.
    Detaching first is what makes the assertion about the database.
    """
    row_id = row.id
    db.expunge_all()
    return (
        await db.execute(
            select(FlexibilityCommitment).where(FlexibilityCommitment.id == row_id)
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# Which rows are settled
# ---------------------------------------------------------------------------


# @verifies REQ-0038
async def test_a_committed_window_inside_the_period_is_settled(db, dt):
    row, = await arrange(db, commitment())
    dt.communities.set("rec_settlement_1h", settlement_rows(1.0, 0.5))

    assert await settle_completed_windows(db, dt, PERIOD) == 1

    settled = await reload(db, row)
    assert settled.status == "settled"


@pytest.mark.parametrize("status", ["settled", "cancelled", "rejected"])
# @verifies REQ-0038
async def test_only_committed_rows_are_settled(db, dt, status):
    """
    Settling twice would overwrite `reward_points_actual` with a second reading, and the
    pipeline that triggers this runs on every completion — so the status filter is what
    makes the operation safe to repeat.
    """
    await arrange(db, commitment(status=status))
    dt.communities.set("rec_settlement_1h", settlement_rows(1.0))

    assert await settle_completed_windows(db, dt, PERIOD) == 0


# @verifies REQ-0038
async def test_a_window_outside_the_period_is_left_alone(db, dt):
    """
    The bounds are the whole of the day in UTC — `period_start >= 00:00` and
    `period_end <= 24:00` — so a window that merely *overlaps* the day is not settled by
    it. A window crossing midnight belongs to neither day and is settled by nothing.
    """
    yesterday = commitment(
        start=datetime(2026, 7, 1, 9, tzinfo=timezone.utc),
        end=datetime(2026, 7, 1, 12, tzinfo=timezone.utc),
    )
    crosses_midnight = commitment(
        start=datetime(2026, 7, 2, 23, tzinfo=timezone.utc),
        end=datetime(2026, 7, 3, 1, tzinfo=timezone.utc),
    )
    await arrange(db, yesterday, crosses_midnight)
    dt.communities.set("rec_settlement_1h", settlement_rows(1.0))

    assert await settle_completed_windows(db, dt, PERIOD) == 0


# @verifies REQ-0038
async def test_no_open_commitments_asks_the_digital_twin_nothing(db, dt):
    """
    The pipeline fires this on every `rec-flexibility-flow` completion, most of which
    have nothing to settle. The early return is what keeps that from being a fetch per
    tick.
    """
    assert await settle_completed_windows(db, dt, PERIOD) == 0
    assert dt.communities.calls == []


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------


# @verifies REQ-0039
async def test_points_are_the_summed_kwh_times_ten(db, dt):
    """
    `rec_settlement_1h` returns one row per hour of the window; the reward is the whole
    window, so the rows are summed before scaling. Asserting the sum rather than a
    single row is what would catch a first-row-only regression.
    """
    row, = await arrange(db, commitment())
    dt.communities.set("rec_settlement_1h", settlement_rows(1.2, 0.8, 0.5))

    await settle_completed_windows(db, dt, PERIOD)

    assert (await reload(db, row)).reward_points_actual == 25


# @verifies REQ-0039
async def test_the_actual_points_do_not_have_to_match_the_estimate(db, dt):
    """
    The estimate is what the participant was shown when they accepted; the actual is
    what the meter said. They are stored separately and the estimate is never revised —
    the gap between them is the only record of how good the forecast was.
    """
    row, = await arrange(db, commitment(points_estimated=40))
    dt.communities.set("rec_settlement_1h", settlement_rows(0.3))

    await settle_completed_windows(db, dt, PERIOD)

    settled = await reload(db, row)
    assert settled.reward_points_estimated == 40
    assert settled.reward_points_actual == 3


# @verifies REQ-0039
async def test_an_unparseable_reading_counts_as_zero(db, dt):
    """
    `_float` swallows the bad value rather than failing the whole batch, so one
    malformed hour costs the participant that hour's points and nothing else. The
    alternative — raising — would leave every other participant's commitment unsettled
    too.
    """
    row, = await arrange(db, commitment())
    dt.communities.set(
        "rec_settlement_1h",
        FetchResult([{"consumption_kwh": 1.0}, {"consumption_kwh": "nonsense"},
                     {"consumption_kwh": None}, {}]),
    )

    await settle_completed_windows(db, dt, PERIOD)

    assert (await reload(db, row)).reward_points_actual == 10


# @verifies REQ-0039
async def test_a_half_point_rounds_to_even_not_up(db, dt):
    """
    Characterisation, and worth stating out loud because it is money-adjacent: `round()`
    is Python's banker's rounding, so 0.25 kWh scores **2** and 0.35 kWh scores **4**.
    Two participants who shifted a quarter-kilowatt-hour apart can be rounded in
    opposite directions.

    Nobody has said this is wrong. It is written down so that changing it is a decision
    rather than an accident.
    """
    quarter, = await arrange(db, commitment())
    dt.communities.set("rec_settlement_1h", settlement_rows(0.25))
    await settle_completed_windows(db, dt, PERIOD)
    assert (await reload(db, quarter)).reward_points_actual == 2

    other, = await arrange(db, commitment())
    dt.communities.set("rec_settlement_1h", settlement_rows(0.35))
    await settle_completed_windows(db, dt, PERIOD)
    assert (await reload(db, other)).reward_points_actual == 4


# ---------------------------------------------------------------------------
# What is asked of the Digital Twin
# ---------------------------------------------------------------------------


# @verifies REQ-0040
async def test_the_fetch_names_the_exact_window(db, dt):
    """
    The commitment's own window is sent, not the day. A settlement fetched for the whole
    period would credit the participant for consumption outside the window they
    committed to.
    """
    await arrange(db, commitment())
    dt.communities.set("rec_settlement_1h", settlement_rows(1.0))

    await settle_completed_windows(db, dt, PERIOD)

    call, = dt.communities.calls
    assert call["community_id"] == COMMUNITY
    assert call["fetcher_id"] == "rec_settlement_1h"
    assert call["payload"]["device_id"] == DEVICE
    assert call["payload"]["window_start"].startswith("2026-07-02T09:00:00")
    assert call["payload"]["window_end"].startswith("2026-07-02T12:00:00")


@pytest.mark.parametrize("missing", ["community_id", "device_id"])
# @verifies REQ-0041
async def test_a_row_without_a_community_or_a_device_is_skipped(db, dt, missing):
    """
    Both are nullable on the model and both are resolved best-effort at accept time, so
    a commitment can legitimately reach settlement without one. It stays `committed`
    forever — there is nothing to fetch a reading against, and no retry that would ever
    succeed.
    """
    row, = await arrange(db, commitment(**{missing: None}))
    dt.communities.set("rec_settlement_1h", settlement_rows(1.0))

    assert await settle_completed_windows(db, dt, PERIOD) == 0
    assert (await reload(db, row)).status == "committed"


# @verifies REQ-0042
async def test_a_digital_twin_failure_leaves_the_commitment_open(db, dt):
    """
    A settlement lost to a transient DT failure is recoverable — the row stays
    `committed` and the next `rec-flexibility-flow` completion picks it up. Marking it
    settled with zero points would not be.
    """
    row, = await arrange(db, commitment())
    dt.communities.set("rec_settlement_1h", RuntimeError("digital twin unavailable"))

    assert await settle_completed_windows(db, dt, PERIOD) == 0
    assert (await reload(db, row)).status == "committed"


# @verifies REQ-0042
async def test_one_participants_failure_does_not_stop_the_others(db, dt):
    """
    Settlement is a batch over every open commitment of the day. A single unreachable
    device must cost that participant their settlement, not everyone's.
    """
    failing = commitment(device_id="sensor-broken", user_id="user-carol")
    healthy = commitment(device_id=DEVICE)
    await arrange(db, failing, healthy)

    def by_device(payload: dict):
        if payload["device_id"] == "sensor-broken":
            raise RuntimeError("device unreachable")
        return settlement_rows(2.0)

    dt.communities.set("rec_settlement_1h", by_device)

    assert await settle_completed_windows(db, dt, PERIOD) == 1
    assert (await reload(db, failing)).status == "committed"
    assert (await reload(db, healthy)).status == "settled"


# @verifies REQ-0042
async def test_an_empty_reading_leaves_the_commitment_open(db, dt):
    """
    No data is not the same as no consumption. `count == 0` means the meter has not
    reported for that window yet, and settling it as zero would take the participant's
    reward away for a reading that had not arrived.
    """
    row, = await arrange(db, commitment())
    dt.communities.set("rec_settlement_1h", FetchResult([]))

    assert await settle_completed_windows(db, dt, PERIOD) == 0
    assert (await reload(db, row)).status == "committed"


# ---------------------------------------------------------------------------
# What settlement writes
# ---------------------------------------------------------------------------


# @verifies REQ-0043
async def test_settling_stamps_status_points_and_time_together(db, dt):
    """
    All three are written in one transaction. A row that is `settled` with no
    `settled_at`, or with no `reward_points_actual`, is not a state any reader handles.
    """
    before = datetime.now(timezone.utc) - timedelta(seconds=1)
    row, = await arrange(db, commitment())
    dt.communities.set("rec_settlement_1h", settlement_rows(1.0))

    await settle_completed_windows(db, dt, PERIOD)

    settled = await reload(db, row)
    assert settled.status == "settled"
    assert settled.reward_points_actual == 10
    assert settled.settled_at is not None
    assert settled.settled_at.replace(tzinfo=timezone.utc) >= before
