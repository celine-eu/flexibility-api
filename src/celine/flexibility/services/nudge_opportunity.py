"""Flexibility opportunity nudge dispatch.

Replaces digital-twin/nudging/flexibility.py.
Triggered when the rec-forecasting-flow pipeline completes.

For every community the registry lists, fetches that community's 24h forecast via
DTClient, detects net-export windows (surplus solar), and sends a
flexibility_opportunity nudge to each of its members.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from celine.sdk.dt.client import DTClient
from celine.sdk.nudging.client import NudgingAdminClient
from celine.sdk.openapi.nudging.models import DigitalTwinEvent
from celine.sdk.rec_registry.client import RecRegistryAdminClient

logger = logging.getLogger(__name__)

# Threshold above which the community is considered to be net-exporting (kWh surplus)
EXPORT_THRESHOLD_KW = 0.5
# Minimum window duration to trigger a notification (hours)
MIN_WINDOW_HOURS = 1
# Registry page size for the community and member lists
REGISTRY_PAGE = 200


def _find_opportunity_windows(forecast_items: list[dict]) -> list[dict]:
    """Find consecutive net-export windows from forecast data."""
    export_hours = []
    for item in forecast_items:
        prediction = item.get("prediction")
        dt_val = item.get("datetime")
        if prediction is None or dt_val is None:
            continue
        try:
            val = float(prediction)
        except (TypeError, ValueError):
            continue
        if isinstance(dt_val, str):
            dt_val = datetime.fromisoformat(dt_val.replace(" ", "T").split("+")[0])
        if dt_val.hour < 5:
            continue
        if val > EXPORT_THRESHOLD_KW:
            export_hours.append((dt_val, val))

    if not export_hours:
        return []

    windows = []
    current_start = export_hours[0][0]
    current_end = export_hours[0][0] + timedelta(hours=1)
    current_kwh = export_hours[0][1]

    for dt_val, kwh in export_hours[1:]:
        if dt_val <= current_end + timedelta(minutes=5):
            current_end = dt_val + timedelta(hours=1)
            current_kwh += kwh
        else:
            duration_h = (current_end - current_start).total_seconds() / 3600
            if duration_h >= MIN_WINDOW_HOURS:
                windows.append({
                    "window_start": current_start,
                    "window_end": current_end,
                    "estimated_kwh": round(current_kwh, 2),
                })
            current_start = dt_val
            current_end = dt_val + timedelta(hours=1)
            current_kwh = kwh

    duration_h = (current_end - current_start).total_seconds() / 3600
    if duration_h >= MIN_WINDOW_HOURS:
        windows.append({
            "window_start": current_start,
            "window_end": current_end,
            "estimated_kwh": round(current_kwh, 2),
        })

    return windows


async def _pages(list_page) -> list:
    """Every item of a paginated registry list; raises when a page does not parse."""
    items: list = []
    cursor: str | None = None
    while True:
        response = await list_page(cursor)
        page = getattr(response, "parsed", None)
        page_items = getattr(page, "items", None)
        if page_items is None:
            raise RuntimeError(
                f"REC Registry returned HTTP {getattr(response, 'status_code', '?')}"
            )
        items.extend(page_items)
        next_cursor = getattr(page, "next_cursor", None)
        if not isinstance(next_cursor, str) or not next_cursor:
            return items
        cursor = next_cursor


async def notify_flexibility_opportunity(
    dt: DTClient,
    registry: RecRegistryAdminClient,
    nudging: NudgingAdminClient,
) -> None:
    """Notify each community's members about its upcoming flexibility opportunity.

    Triggered on rec-forecasting-flow completion. Every community the registry lists
    is handled on its own: its 24h forecast, its net-export windows, its members.
    A failure costs that community, never the others (REQ-0050).
    """
    try:
        communities = await _pages(
            lambda cursor: registry.list_communities(limit=REGISTRY_PAGE, cursor=cursor)
        )
    except Exception as exc:
        logger.warning("Failed to list communities for flexibility nudging: %s", exc)
        return

    for community in communities:
        community_id = getattr(community, "key", None)
        if isinstance(community_id, str) and community_id:
            await _notify_community(dt, registry, nudging, community_id)


async def _notify_community(
    dt: DTClient,
    registry: RecRegistryAdminClient,
    nudging: NudgingAdminClient,
    community_id: str,
) -> None:
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=24)

    try:
        forecast_response = await dt.communities.fetch_values(
            community_id=community_id,
            fetcher_id="rec_forecast",
            payload={
                "start": now.isoformat(),
                "end": end.isoformat(),
            },
        )
    except Exception as exc:
        logger.warning(
            "Failed to fetch rec_forecast for flexibility nudging community=%s: %s",
            community_id, exc,
        )
        return

    if not forecast_response or forecast_response.count == 0:
        logger.debug("No rec_forecast data for flexibility nudging community=%s", community_id)
        return

    items = [item.to_dict() for item in forecast_response.items]
    windows = _find_opportunity_windows(items)

    if not windows:
        logger.debug("No flexibility opportunity windows community=%s", community_id)
        return

    best_window = windows[0]
    window_start_str = best_window["window_start"].strftime("%H:%M")
    window_end_str = best_window["window_end"].strftime("%H:%M")
    estimated_kwh = best_window["estimated_kwh"]
    reward_points = round(estimated_kwh * 10)
    period = best_window["window_start"].strftime("%Y-%m-%d")

    try:
        members = await _pages(
            lambda cursor: registry.list_members(
                community_id, limit=REGISTRY_PAGE, cursor=cursor
            )
        )
    except Exception as exc:
        logger.warning(
            "Failed to fetch members for flexibility nudging community=%s: %s",
            community_id, exc,
        )
        return

    for member in members:
        user_id = getattr(member, "user_id", None)
        if not user_id:
            continue

        payload = {
            "event_type": "flexibility_opportunity",
            "user_id": user_id,
            "community_id": community_id,
            "facts": {
                "facts_version": "1.0",
                "scenario": "flexibility_opportunity",
                "window_start": window_start_str,
                "window_end": window_end_str,
                "estimated_kwh": str(estimated_kwh),
                "reward_points": str(reward_points),
                "period": period,
            },
        }
        try:
            await nudging.ingest_event(DigitalTwinEvent.from_dict(payload))
            logger.debug(
                "Sent flexibility_opportunity to user=%s community=%s window=%s-%s",
                user_id, community_id, window_start_str, window_end_str,
            )
        except Exception as exc:
            logger.warning("Failed to send flexibility nudge to user=%s: %s", user_id, exc)
