"""The pre-window nudge scheduled at accept time.

Two paths, chosen by the clock: a window more than thirty minutes away is scheduled
through nudging-tool's `/admin/scheduled-events`; one already imminent is ingested
immediately, because a scheduled event with a trigger in the past would never fire.

Everything here is best-effort by design. The commitment is the thing of value and it is
already persisted by the time this runs — see
`.agents/knowledge/nudge-scheduling-is-best-effort.md`. That makes "the accept
succeeded" no evidence at all that a reminder exists, which is why these are separate
tests rather than assertions inside the accept test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from celine.flexibility.services import schedule_nudge as module
from celine.flexibility.services.schedule_nudge import (
    _build_facts,
    schedule_pre_window_nudge,
)

from tests.conftest import COMMUNITY, USER_SUB
from tests.fakes import FakeNudgingClient


@pytest.fixture
def posted(monkeypatch) -> list[dict]:
    """Capture the raw httpx POST `schedule_pre_window_nudge` makes.

    The scheduling call does not go through the SDK client — it is a hand-rolled
    `httpx.AsyncClient` against `settings.nudging_api_url`, so there is no client method
    to fake. The transport is replaced instead, which keeps the URL, the headers and the
    JSON body under test.
    """
    calls: list[dict] = []

    class FakeAsyncClient:
        def __init__(self, *_args, **_kwargs) -> None:
            self.status_code = 201

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc) -> None:
            return None

        async def post(self, url, *, headers=None, json=None):
            calls.append({"url": url, "headers": headers or {}, "json": json or {}})
            return httpx.Response(
                calls[-1].get("status", 201), json={}, request=httpx.Request("POST", url)
            )

    monkeypatch.setattr(module.httpx, "AsyncClient", FakeAsyncClient)
    return calls


def in_hours(n: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=n)


# ---------------------------------------------------------------------------
# The facts a notification is rendered from
# ---------------------------------------------------------------------------


# @verifies REQ-0046
def test_facts_carry_the_window_in_utc_hours_and_the_period_as_a_date():
    """
    The nudging templates read these strings verbatim, so their formats are part of the
    contract with `../nudging-tool` and not an internal detail.
    """
    facts = _build_facts(
        commitment_id="c-1",
        suggestion_id="w1",
        window_start=datetime(2026, 7, 2, 9, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 2, 12, tzinfo=timezone.utc),
        reward_points_estimated=14,
    )

    assert facts == {
        "facts_version": "1.0",
        "scenario": "flexibility_reminder",
        "commitment_id": "c-1",
        "suggestion_id": "w1",
        "window_start": "09:00",
        "window_end": "12:00",
        "reward_points_estimated": "14",
        "period": "2026-07-02",
    }


# @verifies REQ-0046
def test_a_naive_window_is_read_as_utc_not_as_local_time():
    """
    `respond_to_suggestion` parses `period_start` out of the request body with
    `datetime.fromisoformat`, which yields a **naive** datetime whenever the client
    omitted an offset. Treating that as local time would shift every notification by the
    server's timezone — two hours, in the community this runs in.
    """
    facts = _build_facts(
        commitment_id="c-1",
        suggestion_id="w1",
        window_start=datetime(2026, 7, 2, 9),
        window_end=datetime(2026, 7, 2, 12),
        reward_points_estimated=1,
    )

    assert facts["window_start"] == "09:00"


# ---------------------------------------------------------------------------
# Scheduling ahead
# ---------------------------------------------------------------------------


# @verifies REQ-0046
async def test_a_future_window_is_scheduled_thirty_minutes_before_it_opens(posted):
    nudging = FakeNudgingClient(token="svc-token")
    window_start = in_hours(4)

    await schedule_pre_window_nudge(
        nudging,
        commitment_id="c-1",
        user_id=USER_SUB,
        community_id=COMMUNITY,
        suggestion_id="w1",
        window_start=window_start,
        window_end=window_start + timedelta(hours=3),
        reward_points_estimated=14,
    )

    call, = posted
    assert call["url"].endswith("/admin/scheduled-events")
    assert call["headers"]["Authorization"] == "Bearer svc-token"
    assert call["json"]["event_type"] == "flexibility_reminder"

    trigger_at = datetime.fromisoformat(call["json"]["trigger_at"])
    assert trigger_at == window_start - timedelta(minutes=30)
    assert nudging.events == [], "a scheduled nudge must not also be sent now"


# @verifies REQ-0046
async def test_the_external_key_is_stable_per_user_and_suggestion(posted):
    """
    `flexibility-accept:<user>:<suggestion>` is what stops a participant who accepts,
    cancels and accepts again from receiving two reminders for one window. It is
    nudging-tool's deduplication key, not ours — which means the guarantee lives in
    another repository and is invisible here.
    """
    nudging = FakeNudgingClient(token="svc-token")
    for _ in range(2):
        await schedule_pre_window_nudge(
            nudging,
            commitment_id="c-1",
            user_id=USER_SUB,
            community_id=COMMUNITY,
            suggestion_id="w1",
            window_start=in_hours(4),
            window_end=in_hours(7),
            reward_points_estimated=14,
        )

    assert {c["json"]["external_key"] for c in posted} == {
        f"flexibility-accept:{USER_SUB}:w1"
    }


# @verifies REQ-0047
async def test_without_a_token_provider_nothing_is_scheduled_and_nothing_raises(posted):
    """
    The client is built at startup from OIDC settings; if that wiring is absent the
    provider is `None`. The accept must still succeed — so this returns quietly, and the
    only trace is a log line.
    """
    nudging = FakeNudgingClient(token=None)

    await schedule_pre_window_nudge(
        nudging,
        commitment_id="c-1",
        user_id=USER_SUB,
        community_id=COMMUNITY,
        suggestion_id="w1",
        window_start=in_hours(4),
        window_end=in_hours(7),
        reward_points_estimated=14,
    )

    assert posted == []
    assert nudging.events == []


# ---------------------------------------------------------------------------
# The imminent window
# ---------------------------------------------------------------------------


# @verifies REQ-0046
async def test_a_window_opening_within_the_half_hour_is_sent_immediately(posted):
    """
    A trigger time in the past would be a scheduled event that never fires. Sending now
    is late but not lost.
    """
    nudging = FakeNudgingClient(token="svc-token")

    await schedule_pre_window_nudge(
        nudging,
        commitment_id="c-1",
        user_id=USER_SUB,
        community_id=COMMUNITY,
        suggestion_id="w1",
        window_start=in_hours(0.1),
        window_end=in_hours(3),
        reward_points_estimated=14,
    )

    assert posted == [], "an immediate send must not also be scheduled"
    payload, = nudging.payloads()
    assert payload["event_type"] == "flexibility_reminder"
    assert payload["user_id"] == USER_SUB
    assert payload["community_id"] == COMMUNITY


# @verifies REQ-0046
async def test_a_window_already_open_is_still_notified(posted):
    """
    Accepting a window that has already started is legitimate — the suggestion list
    shows every open window. There is nothing to remind about in advance, so the
    notification goes out at once.
    """
    nudging = FakeNudgingClient(token="svc-token")

    await schedule_pre_window_nudge(
        nudging,
        commitment_id="c-1",
        user_id=USER_SUB,
        community_id=COMMUNITY,
        suggestion_id="w1",
        window_start=in_hours(-1),
        window_end=in_hours(2),
        reward_points_estimated=14,
    )

    assert len(nudging.events) == 1
    assert posted == []


# @verifies REQ-0047
async def test_a_missing_community_becomes_an_empty_string_not_a_null(posted):
    """
    `community_id` is resolved best-effort at accept time and can legitimately be empty.
    `DigitalTwinEvent.from_dict` is what would reject a `None` here, and it would reject
    it *after* the commitment had been written.
    """
    nudging = FakeNudgingClient(token="svc-token")

    await schedule_pre_window_nudge(
        nudging,
        commitment_id="c-1",
        user_id=USER_SUB,
        community_id="",
        suggestion_id="w1",
        window_start=in_hours(0.1),
        window_end=in_hours(3),
        reward_points_estimated=14,
    )

    assert nudging.payloads()[0]["community_id"] == ""
