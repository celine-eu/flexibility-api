"""Fair settlement — a commitment is worth what the pipeline's weekly outcome says.

Under the fair gamification (demo3 spec 2026-10-07, design 2026-10-09) the pipeline
evaluates every commitment once a week (`rec_fair_commitment_outcomes`): kept (fixed
points plus true-surplus extra), not kept, or not evaluable. This service only copies
that verdict onto the commitment and tells the member. The Digital Twin is faked at
`dt.communities.fetch_values("rec_fair_commitment_outcome")`.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select

from celine.flexibility.models.commitment import FlexibilityCommitment
from celine.flexibility.services.settlement import settle_fair_outcomes

from tests.conftest import COMMUNITY, DEVICE, USER_SUB
from tests.fakes import FakeDTClient, FakeNudgingClient, FetchResult

PERIOD = date(2026, 10, 1)


def commitment(*, status: str = "committed") -> FlexibilityCommitment:
    return FlexibilityCommitment(
        user_id=USER_SUB, suggestion_id="w1", suggestion_type="shift-consumption",
        community_id=COMMUNITY, device_id=DEVICE,
        period_start=datetime(2026, 10, 1, 11, tzinfo=timezone.utc),
        period_end=datetime(2026, 10, 1, 12, tzinfo=timezone.utc),
        status=status, reward_points_estimated=5,
    )


def outcome(**overrides) -> FetchResult:
    row = {"commitment_id": "x", "device_id": DEVICE, "week_start": "2026-09-28", "matched": True,
           "is_kept": True, "points": 5, "surplus_hours_kept": 1, "surplus_points": 2, "reason": "kept"}
    row.update(overrides)
    return FetchResult([row])


async def arrange(db, *rows):
    db.add_all(rows)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return list(rows)


async def reload(db, row):
    row_id = row.id
    db.expunge_all()
    return (await db.execute(select(FlexibilityCommitment).where(FlexibilityCommitment.id == row_id))).scalar_one()


@pytest.fixture
def dt() -> FakeDTClient:
    return FakeDTClient()


@pytest.fixture
def nudging() -> FakeNudgingClient:
    return FakeNudgingClient()


async def test_a_kept_commitment_earns_the_fixed_points_plus_surplus_and_tells_the_member(db, dt, nudging):
    row, = await arrange(db, commitment())
    dt.communities.set("rec_fair_commitment_outcome", outcome())

    assert await settle_fair_outcomes(db, dt, nudging, PERIOD) == 1

    settled = await reload(db, row)
    assert settled.status == "settled" and settled.reward_points_actual == 7 and settled.settled_at is not None
    call = dt.communities.calls[-1]
    assert call["fetcher_id"] == "rec_fair_commitment_outcome" and call["payload"]["device_id"] == DEVICE
    assert call["payload"]["period_start"] == "2026-10-01T11:00:00+00:00"
    [event] = nudging.payloads()
    assert event["event_type"] == "commitment_settled" and event["user_id"] == USER_SUB
    facts = event["facts"]
    assert facts["scenario"] == "commitment_settled" and facts["kept"] == "true" and facts["reason"] == "kept"
    assert facts["reward_points_actual"] == "7" and facts["commitment_id"] == str(row.id) and facts["period"] == "2026-10-01"


async def test_a_commitment_not_kept_settles_at_zero_and_says_so(db, dt, nudging):
    row, = await arrange(db, commitment())
    dt.communities.set("rec_fair_commitment_outcome", outcome(is_kept=False, points=0, surplus_hours_kept=0, surplus_points=0, reason="not_kept"))

    assert await settle_fair_outcomes(db, dt, nudging, PERIOD) == 1

    settled = await reload(db, row)
    assert settled.status == "settled" and settled.reward_points_actual == 0
    assert nudging.payloads()[0]["facts"]["kept"] == "false"


async def test_a_commitment_without_a_window_is_not_evaluable_never_missed(db, dt, nudging):
    row, = await arrange(db, commitment())
    dt.communities.set("rec_fair_commitment_outcome", outcome(matched=False, is_kept=False, points=0, surplus_hours_kept=0, surplus_points=0, reason="no_window"))

    assert await settle_fair_outcomes(db, dt, nudging, PERIOD) == 1

    settled = await reload(db, row)
    assert settled.status == "settled" and settled.reward_points_actual == 0
    assert nudging.payloads()[0]["facts"]["reason"] == "no_window"


async def test_no_outcome_yet_leaves_the_commitment_for_the_next_weekly_run(db, dt, nudging):
    """Outcomes land weekly: a window closed on Wednesday has none until Monday's run."""
    row, = await arrange(db, commitment())
    dt.communities.set("rec_fair_commitment_outcome", FetchResult([]))

    assert await settle_fair_outcomes(db, dt, nudging, PERIOD) == 0

    assert (await reload(db, row)).status == "committed"
    assert nudging.payloads() == []


async def test_a_twin_failure_skips_the_row_and_retries_later(db, dt, nudging):
    row, = await arrange(db, commitment())
    dt.communities.set("rec_fair_commitment_outcome", RuntimeError("twin down"))

    assert await settle_fair_outcomes(db, dt, nudging, PERIOD) == 0
    assert (await reload(db, row)).status == "committed"


async def test_settles_without_a_nudging_client(db, dt):
    row, = await arrange(db, commitment())
    dt.communities.set("rec_fair_commitment_outcome", outcome())

    assert await settle_fair_outcomes(db, dt, None, PERIOD) == 1
    assert (await reload(db, row)).reward_points_actual == 7


async def test_a_failing_nudge_does_not_undo_the_settlement(db, dt):
    row, = await arrange(db, commitment())
    dt.communities.set("rec_fair_commitment_outcome", outcome())

    assert await settle_fair_outcomes(db, dt, FakeNudgingClient(fails=True), PERIOD) == 1
    assert (await reload(db, row)).status == "settled"


async def test_the_flag_is_off_by_default():
    from celine.flexibility.core.config import settings

    assert settings.fair_settlement_enabled is False


async def test_a_duplicate_window_commitment_settles_at_zero_with_its_reason(db, dt, nudging):
    """A later commitment on an already-committed window is 'duplicate_window': settled, 0 points, told as such."""
    row, = await arrange(db, commitment())
    dt.communities.set("rec_fair_commitment_outcome", outcome(matched=False, is_kept=False, points=0, surplus_hours_kept=0, surplus_points=0, reason="duplicate_window"))

    assert await settle_fair_outcomes(db, dt, nudging, PERIOD) == 1

    settled = await reload(db, row)
    assert settled.status == "settled" and settled.reward_points_actual == 0
    assert nudging.payloads()[0]["facts"]["reason"] == "duplicate_window" and nudging.payloads()[0]["facts"]["kept"] == "false"
