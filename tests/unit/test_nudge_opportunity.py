"""Opportunity nudges — telling a community that surplus is coming.

Window detection is pure and is tested as such. Dispatch is tested through the fakes,
because the interesting behaviour is what happens when one of the three upstream calls
fails: nothing propagates anywhere, since there is no caller to report to. This runs off
an MQTT event.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from celine.flexibility.services.nudge_opportunity import (
    EXPORT_THRESHOLD_KW,
    _find_opportunity_windows,
    notify_flexibility_opportunity,
)

from tests.fakes import (
    FakeDTClient,
    FakeNudgingClient,
    FakeRegistryClient,
    FetchResult,
    Member,
)

COMMUNITY = "it-energy-community"


def hours(*pairs: tuple[int, float]) -> list[dict]:
    """Forecast rows: `(hour_of_day, predicted_kw)`, on a fixed date."""
    return [
        {"datetime": datetime(2026, 7, 2, h).isoformat(), "prediction": kw}
        for h, kw in pairs
    ]


# ---------------------------------------------------------------------------
# Finding the windows
# ---------------------------------------------------------------------------


# @verifies REQ-0048
def test_consecutive_export_hours_become_one_window():
    windows = _find_opportunity_windows(hours((10, 2.0), (11, 3.0), (12, 1.0)))

    window, = windows
    assert window["window_start"] == datetime(2026, 7, 2, 10)
    assert window["window_end"] == datetime(2026, 7, 2, 13)
    assert window["estimated_kwh"] == 6.0


# @verifies REQ-0048
def test_a_gap_splits_the_window():
    """
    Two separate surpluses are two separate opportunities. Merging them across a
    non-exporting hour would invite the participant to shift load into an hour with no
    surplus to absorb it.
    """
    windows = _find_opportunity_windows(hours((10, 2.0), (11, 2.0), (15, 4.0), (16, 1.0)))

    assert [(w["window_start"].hour, w["window_end"].hour) for w in windows] == [
        (10, 12),
        (15, 17),
    ]


# @verifies REQ-0048
def test_an_hour_at_the_threshold_is_not_a_surplus():
    """
    The comparison is strict: exactly `EXPORT_THRESHOLD_KW` is not exporting. Half a
    kilowatt is inside the noise of a forecast.
    """
    assert _find_opportunity_windows(hours((10, EXPORT_THRESHOLD_KW))) == []
    assert len(_find_opportunity_windows(hours((10, EXPORT_THRESHOLD_KW + 0.01)))) == 1


# @verifies REQ-0048
def test_night_hours_are_never_an_opportunity():
    """
    Before 05:00 there is no solar surplus to speak of, and nobody is asked to run a
    washing machine at four in the morning. The filter is on the hour of the forecast
    row, so a forecast that predicted export at 03:00 is dropped rather than trusted.
    """
    assert _find_opportunity_windows(hours((3, 9.0), (4, 9.0))) == []


# @verifies REQ-0048
def test_a_lone_hour_still_qualifies():
    """
    `MIN_WINDOW_HOURS` is 1 and a single row spans an hour, so one exporting hour is a
    window. The minimum exists to reject nothing that occurs today — it is a guard
    against sub-hourly forecast granularity, not a filter anyone relies on.
    """
    assert len(_find_opportunity_windows(hours((10, 4.0)))) == 1


# @verifies REQ-0048
def test_unusable_forecast_rows_are_skipped_not_fatal():
    """
    The forecast is another service's output and arrives untyped. A row missing its
    prediction, or carrying a string where a number belongs, costs that hour and not the
    whole notification.
    """
    rows = hours((10, 2.0)) + [
        {"datetime": None, "prediction": 5.0},
        {"datetime": datetime(2026, 7, 2, 11).isoformat(), "prediction": None},
        {"datetime": datetime(2026, 7, 2, 12).isoformat(), "prediction": "lots"},
    ]

    window, = _find_opportunity_windows(rows)
    assert window["estimated_kwh"] == 2.0


# @verifies REQ-0048
def test_no_export_at_all_is_no_window():
    assert _find_opportunity_windows(hours((10, 0.1), (11, 0.0))) == []


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


@pytest.fixture
def dt() -> FakeDTClient:
    client = FakeDTClient()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    noon = now.replace(hour=10)
    client.communities.set(
        "rec_forecast",
        FetchResult(
            [
                {"datetime": (noon + timedelta(hours=n)).isoformat(), "prediction": kw}
                for n, kw in ((0, 2.0), (1, 3.0))
            ]
        ),
    )
    return client


@pytest.fixture
def nudging() -> FakeNudgingClient:
    return FakeNudgingClient()


# @verifies REQ-0049
async def test_every_member_of_the_community_is_notified(dt, nudging):
    """
    This is a broadcast: one forecast, one window, one nudge per member. There is no
    per-participant filtering at this stage — the personal figures arrive later, when the
    participant opens the suggestion list.
    """
    registry = FakeRegistryClient(
        [Member("user-a", COMMUNITY), Member("user-b", COMMUNITY)]
    )

    await notify_flexibility_opportunity(dt, registry, nudging)

    payloads = nudging.payloads()
    assert [p["user_id"] for p in payloads] == ["user-a", "user-b"]
    assert payloads[0]["event_type"] == "flexibility_opportunity"
    assert payloads[0]["facts"]["scenario"] == "flexibility_opportunity"


# @verifies REQ-0049
async def test_the_notified_points_are_the_estimate_times_ten(dt, nudging):
    """
    The same ×10 that settlement applies to the measured reading — the participant is
    shown a number computed the same way it will later be earned. If one changes without
    the other, every notification promises a reward the settlement will not pay.
    """
    await notify_flexibility_opportunity(
        dt, FakeRegistryClient([Member("user-a")]), nudging
    )

    facts = nudging.payloads()[0]["facts"]
    assert facts["estimated_kwh"] == "5.0"
    assert facts["reward_points"] == "50"


# @verifies REQ-0049
async def test_only_the_first_window_is_notified(dt, nudging):
    """
    `windows[0]` — the earliest, not the largest. A participant gets one opportunity per
    forecast run whatever the day holds, and a bigger surplus later in the day is never
    mentioned.
    """
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0, hour=8)
    dt.communities.set(
        "rec_forecast",
        FetchResult(
            [
                {"datetime": now.isoformat(), "prediction": 1.0},
                {"datetime": (now + timedelta(hours=5)).isoformat(), "prediction": 40.0},
            ]
        ),
    )

    await notify_flexibility_opportunity(
        dt, FakeRegistryClient([Member("user-a")]), nudging
    )

    assert nudging.payloads()[0]["facts"]["estimated_kwh"] == "1.0"


# @verifies REQ-0049
async def test_a_member_without_a_user_id_is_skipped(dt, nudging):
    """
    The registry's member shape is another service's, and a member row can carry an
    entity with no linked user account.
    """
    registry = FakeRegistryClient([Member(None), Member("user-b")])

    await notify_flexibility_opportunity(dt, registry, nudging)

    assert [p["user_id"] for p in nudging.payloads()] == ["user-b"]


# @verifies REQ-0050
async def test_a_forecast_failure_notifies_nobody(dt, nudging):
    """
    Every failure below is swallowed. This handler is called from the MQTT listener and
    has no caller to return an error to — its only failure mode is silence, and that is
    exactly why nothing here is observable in production without reading the log.
    """
    dt.communities.set("rec_forecast", RuntimeError("digital twin unavailable"))

    await notify_flexibility_opportunity(
        dt, FakeRegistryClient([Member("user-a")]), nudging
    )

    assert nudging.events == []


# @verifies REQ-0050
async def test_an_empty_forecast_notifies_nobody(dt, nudging):
    dt.communities.set("rec_forecast", FetchResult([]))

    await notify_flexibility_opportunity(
        dt, FakeRegistryClient([Member("user-a")]), nudging
    )

    assert nudging.events == []


# @verifies REQ-0050
async def test_a_registry_failure_notifies_nobody(dt, nudging):
    await notify_flexibility_opportunity(
        dt, FakeRegistryClient(RuntimeError("registry down")), nudging
    )

    assert nudging.events == []


# @verifies REQ-0050
async def test_one_rejected_nudge_does_not_stop_the_broadcast(dt, nudging):
    """
    A community has as many members as it has; one event nudging-tool rejects must not
    cost everyone after it in the loop.
    """
    nudging.fails_for = {"user-a"}
    registry = FakeRegistryClient([Member("user-a"), Member("user-b")])

    await notify_flexibility_opportunity(dt, registry, nudging)

    assert [p["user_id"] for p in nudging.payloads()] == ["user-b"]


# @verifies REQ-0050
async def test_the_community_is_hard_coded(dt, nudging):
    """
    Both the forecast fetch and the member list name `it-energy-community` literally.
    A second community would be silently ignored: no error, no nudge, nothing to notice.
    """
    await notify_flexibility_opportunity(
        dt, (registry := FakeRegistryClient([Member("user-a")])), nudging
    )

    assert dt.communities.calls[0]["community_id"] == COMMUNITY
    assert registry.calls == [COMMUNITY]
