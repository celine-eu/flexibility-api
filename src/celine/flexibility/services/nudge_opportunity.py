"""Flexibility opportunity nudge dispatch.

Replaces digital-twin/nudging/flexibility.py.
Triggered when the rec-forecasting-flow pipeline completes.

Fetches the 24h REC forecast via DTClient, detects net-export windows (surplus
solar), and sends a flexibility_opportunity nudge to each community participant.
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


async def notify_flexibility_opportunity(
    dt: DTClient,
    registry: RecRegistryAdminClient,
    nudging: NudgingAdminClient,
) -> None:
    """Notify community participants about upcoming flexibility opportunities.

    Triggered on rec-forecasting-flow completion. Fetches the 24h REC forecast,
    detects net-export windows, and sends flexibility_opportunity nudges.
    """
    now = datetime.now(timezone.utc)
    end = now + timedelta(hours=24)

    try:
        forecast_response = await dt.communities.fetch_values(
            community_id="it-energy-community",
            fetcher_id="rec_forecast",
            payload={
                "start": now.isoformat(),
                "end": end.isoformat(),
            },
        )
    except Exception as exc:
        logger.warning("Failed to fetch rec_forecast for flexibility nudging: %s", exc)
        return

    if not forecast_response or forecast_response.count == 0:
        logger.debug("No rec_forecast data available for flexibility nudging")
        return

    items = [item.to_dict() for item in forecast_response.items]
    windows = _find_opportunity_windows(items)

    if not windows:
        logger.debug("No flexibility opportunity windows found in forecast")
        return

    best_window = windows[0]
    window_start_str = best_window["window_start"].strftime("%H:%M")
    window_end_str = best_window["window_end"].strftime("%H:%M")
    estimated_kwh = best_window["estimated_kwh"]
    reward_points = round(estimated_kwh * 10)
    period = best_window["window_start"].strftime("%Y-%m-%d")

    try:
        members_result = await registry.list_members("it-energy-community")
    except Exception as exc:
        logger.warning("Failed to fetch community members for flexibility nudging: %s", exc)
        return

    if not members_result:
        logger.debug("No community members found for flexibility nudging")
        return

    # list_members returns raw response; handle both list and paginated wrapper
    members = members_result if isinstance(members_result, list) else getattr(members_result, "items", []) or []

    for member in members:
        user_id = getattr(member, "user_id", None) or getattr(member, "owner_user_id", None)
        community_id = getattr(member, "community_key", None) or getattr(member, "community_id", None)

        if not user_id:
            continue

        payload = {
            "event_type": "flexibility_opportunity",
            "user_id": user_id,
            "community_id": community_id or "",
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
