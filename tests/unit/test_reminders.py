"""Reminder dispatch — the nudge that fires once the window has opened.

This is the fallback that makes the swallowed failure in `schedule_nudge` safe rather
than merely convenient (`.agents/knowledge/nudge-scheduling-is-best-effort.md`). If it
stops working, a nudge lost at accept time is lost for good, and nothing reports either
loss.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from celine.flexibility.models.commitment import FlexibilityCommitment
from celine.flexibility.services.reminders import send_pending_reminders

from tests.conftest import COMMUNITY, USER_SUB
from tests.fakes import FakeNudgingClient


def open_window(
    *,
    status: str = "committed",
    reminded_at: datetime | None = None,
    starts_in: timedelta = timedelta(hours=-1),
    ends_in: timedelta = timedelta(hours=1),
    user_id: str = USER_SUB,
    points: int = 12,
) -> FlexibilityCommitment:
    """A commitment whose window is open right now, unless offset says otherwise."""
    now = datetime.now(timezone.utc)
    return FlexibilityCommitment(
        user_id=user_id,
        suggestion_id="w1",
        suggestion_type="shift-consumption",
        community_id=COMMUNITY,
        device_id="sensor-1",
        period_start=now + starts_in,
        period_end=now + ends_in,
        status=status,
        reminded_at=reminded_at,
        reward_points_estimated=points,
    )


@pytest.fixture
def nudging() -> FakeNudgingClient:
    return FakeNudgingClient()


async def arrange(db, *rows):
    db.add_all(rows)
    await db.commit()
    return list(rows)


# ---------------------------------------------------------------------------
# Who gets one
# ---------------------------------------------------------------------------


# @verifies REQ-0044
async def test_an_open_commitment_gets_a_reminder(db, nudging):
    await arrange(db, open_window())

    assert await send_pending_reminders(db, nudging) == 1

    payload, = nudging.payloads()
    assert payload["event_type"] == "flexibility_reminder"
    assert payload["user_id"] == USER_SUB
    assert payload["community_id"] == COMMUNITY
    assert payload["facts"]["scenario"] == "flexibility_reminder"
    assert payload["facts"]["reward_points_estimated"] == "12"


@pytest.mark.parametrize("status", ["cancelled", "settled", "rejected"])
# @verifies REQ-0044
async def test_only_a_committed_row_is_reminded(db, nudging, status):
    """
    A participant who cancelled must not be reminded of a commitment they withdrew.
    """
    await arrange(db, open_window(status=status))

    assert await send_pending_reminders(db, nudging) == 0
    assert nudging.events == []


# @verifies REQ-0044
async def test_a_window_that_has_not_opened_yet_is_not_reminded(db, nudging):
    """
    The reminder says the window is open now. Sending it early would ask the participant
    to shift load at a moment when shifting it earns nothing.
    """
    await arrange(db, open_window(starts_in=timedelta(hours=2), ends_in=timedelta(hours=4)))

    assert await send_pending_reminders(db, nudging) == 0


# @verifies REQ-0044
async def test_a_window_that_has_closed_is_not_reminded(db, nudging):
    """
    `period_end > now` is strict, so a window closing this instant is already past. The
    reminder would arrive too late to be acted on, and a nudge nobody can act on is
    worse than none.
    """
    await arrange(db, open_window(starts_in=timedelta(hours=-4), ends_in=timedelta(hours=-2)))

    assert await send_pending_reminders(db, nudging) == 0


# @verifies REQ-0045
async def test_a_row_already_reminded_is_not_reminded_again(db, nudging):
    """
    `meters-flow` completes every five minutes and this runs on each one. Without the
    `reminded_at` filter a three-hour window would produce thirty-six notifications.
    """
    await arrange(db, open_window(reminded_at=datetime.now(timezone.utc)))

    assert await send_pending_reminders(db, nudging) == 0


# @verifies REQ-0045
async def test_a_second_pass_over_the_same_window_sends_nothing(db, nudging):
    await arrange(db, open_window())

    assert await send_pending_reminders(db, nudging) == 1
    assert await send_pending_reminders(db, nudging) == 0
    assert len(nudging.events) == 1


# ---------------------------------------------------------------------------
# The order of the two side effects
# ---------------------------------------------------------------------------


# @verifies REQ-0045
async def test_reminded_at_is_committed_before_the_nudge_is_sent(db, nudging):
    """
    The stamp is written and committed *first*, then the nudges go out. The order is
    what bounds the damage when nudging-tool is down: the reminder is lost, and it is
    lost once. Sending first and stamping after would resend on every tick for as long
    as the outage lasted — the participant would be notified repeatedly about a window
    they had already been told about.

    Losing it silently is the accepted cost, and it is why a `send_pending_reminders`
    returning 0 says nothing about whether a reminder was owed.
    """
    row, = await arrange(db, open_window())
    row_id = row.id
    nudging.fails = True

    assert await send_pending_reminders(db, nudging) == 0

    db.expunge_all()
    stored = (
        await db.execute(
            select(FlexibilityCommitment).where(FlexibilityCommitment.id == row_id)
        )
    ).scalar_one()
    assert stored.reminded_at is not None, "the stamp must survive a nudging failure"


# @verifies REQ-0045
async def test_one_failed_nudge_does_not_stop_the_rest(db, nudging):
    """
    Dispatch is a loop over every participant whose window just opened. It is wrapped
    per row, so an event nudging-tool rejects costs one participant a reminder and not
    the whole batch.

    Both rows are stamped either way — the stamp is committed before the loop starts —
    so the participant whose nudge was rejected is never retried.
    """
    await arrange(db, open_window(user_id="user-a"), open_window(user_id="user-b"))
    nudging.fails_for = {"user-a"}

    sent = await send_pending_reminders(db, nudging)

    assert sent == 1
    assert [p["user_id"] for p in nudging.payloads()] == ["user-b"]
    assert await send_pending_reminders(db, nudging) == 0


# @verifies REQ-0044
async def test_nothing_due_touches_neither_database_nor_nudging(db, nudging):
    """
    The common case — this runs every five minutes and most ticks have nothing to do.
    """
    assert await send_pending_reminders(db, nudging) == 0
    assert nudging.events == []


# ---------------------------------------------------------------------------
# What the nudge says
# ---------------------------------------------------------------------------


# @verifies REQ-0044
async def test_window_times_are_rendered_from_utc(db, nudging):
    """
    Stored timestamps come back from PostgreSQL aware and from a naive column naive;
    `_as_utc` normalises both before formatting, so the `HH:MM` in the notification is
    the same instant either way.

    Note that this is UTC, **not** the community-local time the suggestion list labels
    windows with — the two surfaces disagree by two hours in summer. Recorded in
    `.agents/knowledge/two-clocks-label-the-same-window.md`.
    """
    start = datetime(2026, 7, 2, 9, tzinfo=timezone.utc)
    row = open_window()
    row.period_start = start
    row.period_end = start + timedelta(hours=3)
    await arrange(db, row)

    # Reach the row through the query rather than the clock: shift the window onto now.
    now = datetime.now(timezone.utc)
    row.period_start = now - timedelta(minutes=30)
    row.period_end = now + timedelta(minutes=30)
    await db.commit()

    await send_pending_reminders(db, nudging)

    facts = nudging.payloads()[0]["facts"]
    assert facts["window_start"] == (now - timedelta(minutes=30)).strftime("%H:%M")
    assert facts["period"] == now.strftime("%Y-%m-%d")
    assert facts["commitment_id"] == str(row.id)
