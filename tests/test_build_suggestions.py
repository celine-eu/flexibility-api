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


def test_ranked_by_personal_impact_then_community_capped_at_two() -> None:
    windows = [
        _window("w1", "2026-07-02T06:00:00", "2026-07-02T09:00:00", kwh=94.0),
        _window("w2", "2026-07-02T09:00:00", "2026-07-02T12:00:00", kwh=126.0),
        _window("w3", "2026-07-02T12:00:00", "2026-07-02T15:00:00", kwh=343.0),
    ]
    key = (datetime(2026, 7, 2, 9), datetime(2026, 7, 2, 12))
    enrichment = {key: {"estimated_kwh": 2.0, "reward_points_estimated": 15, "confidence": 0.75}}
    items = _build_suggestions(windows, enrichment, set(), date(2026, 7, 2))
    assert len(items) == 2                       # _MAX_SUGGESTIONS cap
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
