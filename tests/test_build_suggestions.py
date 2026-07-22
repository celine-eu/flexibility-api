"""Unit tests for the community-first suggestion builder."""
from datetime import date, datetime

from celine.flexibility.api.suggestions import _build_suggestions


def _window(_id: str = "w1", start: str = "2026-07-02T09:00:00",
            end: str = "2026-07-02T12:00:00", kwh: float = 120.0) -> dict:
    return {"_id": _id, "window_start": start, "window_end": end,
            "community_kwh": kwh, "confidence": 0.75}


def test_window_visible_without_enrichment() -> None:
    items = _build_suggestions([_window()], {}, set(), date(2026, 7, 2))
    assert len(items) == 1
    assert items[0].impact_kwh_estimated is None
    assert items[0].reward_points is None
    assert items[0].community_kwh == 120.0
    assert items[0].id == "w1"


def test_enrichment_populates_personal_figures() -> None:
    key = (datetime(2026, 7, 2, 9), datetime(2026, 7, 2, 12))
    enrichment = {key: {"estimated_kwh": 1.8, "reward_points_estimated": 14, "confidence": 0.8}}
    items = _build_suggestions([_window()], enrichment, set(), date(2026, 7, 2))
    assert items[0].impact_kwh_estimated == 1.8
    assert items[0].reward_points == 14
    assert items[0].confidence == 0.8


def test_small_personal_impact_is_not_hidden() -> None:
    key = (datetime(2026, 7, 2, 9), datetime(2026, 7, 2, 12))
    enrichment = {key: {"estimated_kwh": 0.08, "reward_points_estimated": 1, "confidence": 0.75}}
    items = _build_suggestions([_window()], enrichment, set(), date(2026, 7, 2))
    assert len(items) == 1
    assert items[0].impact_kwh_estimated == 0.08


def test_committed_window_hidden() -> None:
    items = _build_suggestions([_window()], {}, {"w1"}, date(2026, 7, 2))
    assert items == []


def test_ranked_by_personal_impact_then_community_and_none_dropped() -> None:
    windows = [
        _window("w1", "2026-07-02T06:00:00", "2026-07-02T09:00:00", kwh=94.0),
        _window("w2", "2026-07-02T09:00:00", "2026-07-02T12:00:00", kwh=126.0),
        _window("w3", "2026-07-02T12:00:00", "2026-07-02T15:00:00", kwh=343.0),
    ]
    key = (datetime(2026, 7, 2, 9), datetime(2026, 7, 2, 12))
    enrichment = {key: {"estimated_kwh": 2.0, "reward_points_estimated": 15, "confidence": 0.75}}
    items = _build_suggestions(windows, enrichment, set(), date(2026, 7, 2))
    # Every open window stays visible (was capped at 2, which revealed them one by one).
    assert [i.id for i in items] == ["w2", "w3", "w1"]
    assert items[0].id == "w2"                   # enriched window ranks first
    assert items[1].id == "w3"                   # then highest community_kwh


def test_enrichment_with_null_reward_points_does_not_crash() -> None:
    key = (datetime(2026, 7, 2, 9), datetime(2026, 7, 2, 12))
    enrichment = {key: {"estimated_kwh": 1.8, "reward_points_estimated": None, "confidence": 0.75}}
    items = _build_suggestions([_window()], enrichment, set(), date(2026, 7, 2))
    assert len(items) == 1
    assert items[0].reward_points == 0
    assert items[0].impact_kwh_estimated == 1.8


def test_confidence_absent_stays_none() -> None:
    window = _window()
    del window["confidence"]
    items = _build_suggestions([window], {}, set(), date(2026, 7, 2))
    assert items[0].confidence is None


def test_confidence_null_stays_none() -> None:
    window = _window()
    window["confidence"] = None
    items = _build_suggestions([window], {}, set(), date(2026, 7, 2))
    assert items[0].confidence is None


def test_confidence_value_passes_through() -> None:
    window = _window()
    window["confidence"] = 0.62
    items = _build_suggestions([window], {}, set(), date(2026, 7, 2))
    assert items[0].confidence == 0.62


# ── timezone handling: gold windows are naive UTC, users see Europe/Rome ──────

def test_naive_window_timestamps_are_emitted_as_utc_aware() -> None:
    """Gold window_start is `timestamp without time zone` holding UTC.

    Emitting it naively made the frontend read it as local time (2h early in
    summer) while commitments — stored tz-aware — rendered correctly, so the
    same window appeared twice at different hours.
    """
    items = _build_suggestions([_window()], {}, set(), date(2026, 7, 2))
    assert items[0].period_start.endswith("+00:00")
    assert items[0].period_end.endswith("+00:00")
    assert items[0].period_start.startswith("2026-07-02T09:00:00")


def test_clock_labels_use_italian_local_time() -> None:
    """09:00–12:00 UTC in July is 11:00–14:00 in Europe/Rome (CEST, +2)."""
    items = _build_suggestions([_window()], {}, set(), date(2026, 7, 2))
    assert items[0].to_time == "11:00"
    assert items[0].clock_range == "11:00–14:00"
    assert items[0].to_period == "late_morning"   # local hour 11, not UTC hour 9


def test_window_ending_after_21_local_is_excluded() -> None:
    """18:00–21:00 UTC = 20:00–23:00 local — too late to ask for a load shift."""
    late = _window("late", "2026-07-02T18:00:00", "2026-07-02T21:00:00")
    assert _build_suggestions([late], {}, set(), date(2026, 7, 2)) == []


def test_window_ending_exactly_at_21_local_is_kept() -> None:
    """16:00–19:00 UTC = 18:00–21:00 local — ends exactly at the cutoff, allowed."""
    edge = _window("edge", "2026-07-02T16:00:00", "2026-07-02T19:00:00")
    items = _build_suggestions([edge], {}, set(), date(2026, 7, 2))
    assert [i.id for i in items] == ["edge"]
    assert items[0].clock_range == "18:00–21:00"


def test_enrichment_lookup_survives_utc_normalisation() -> None:
    """Enrichment keys arrive naive from the per-device fetcher — still must match."""
    key = (datetime(2026, 7, 2, 9), datetime(2026, 7, 2, 12))
    enrichment = {key: {"estimated_kwh": 3.3, "reward_points_estimated": 21, "confidence": 0.7}}
    items = _build_suggestions([_window()], enrichment, set(), date(2026, 7, 2))
    assert items[0].impact_kwh_estimated == 3.3
    assert items[0].reward_points == 21


# ── visibility cap ────────────────────────────────────────────────────────────

def test_all_windows_visible_up_to_cap_of_eight() -> None:
    """Users must see every open window at once, not two at a time."""
    windows = [
        _window(f"w{h}", f"2026-07-02T{h:02d}:00:00", f"2026-07-02T{h + 1:02d}:00:00", kwh=float(h))
        for h in range(5, 15)          # 10 windows, all ending before 21:00 local
    ]
    items = _build_suggestions(windows, {}, set(), date(2026, 7, 2))
    assert len(items) == 8
