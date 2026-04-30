"""Schedule a pre-window nudge when a user accepts a flexibility suggestion.

Posts to the nudging API's /admin/scheduled-events so the notification fires
~15 minutes before the flexibility window opens.  If the window is already
imminent (trigger_at in the past), falls back to an immediate ingest_event().
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from celine.sdk.nudging.client import NudgingAdminClient
from celine.sdk.openapi.nudging.models import DigitalTwinEvent

from celine.flexibility.core.config import settings

logger = logging.getLogger(__name__)

_PRE_WINDOW_MINUTES = 15


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _build_facts(
    *,
    commitment_id: str,
    suggestion_id: str,
    window_start: datetime,
    window_end: datetime,
    reward_points_estimated: int,
) -> dict:
    ws = _as_utc(window_start)
    we = _as_utc(window_end)
    return {
        "facts_version": "1.0",
        "scenario": "flexibility_reminder",
        "commitment_id": commitment_id,
        "suggestion_id": suggestion_id,
        "window_start": ws.strftime("%H:%M"),
        "window_end": we.strftime("%H:%M"),
        "reward_points_estimated": str(reward_points_estimated),
        "period": ws.strftime("%Y-%m-%d"),
    }


async def schedule_pre_window_nudge(
    nudging: NudgingAdminClient,
    *,
    commitment_id: str,
    user_id: str,
    community_id: str,
    suggestion_id: str,
    window_start: datetime,
    window_end: datetime,
    reward_points_estimated: int,
) -> None:
    """Schedule a flexibility_reminder nudge before the window opens.

    Fire-and-forget from the caller's perspective — any failure is logged
    but must not fail the accept response.  The existing send_pending_reminders
    service provides a fallback once the window actually opens.
    """
    now = datetime.now(timezone.utc)
    trigger_at = _as_utc(window_start) - timedelta(minutes=_PRE_WINDOW_MINUTES)

    facts = _build_facts(
        commitment_id=commitment_id,
        suggestion_id=suggestion_id,
        window_start=window_start,
        window_end=window_end,
        reward_points_estimated=reward_points_estimated,
    )

    if trigger_at <= now:
        payload = {
            "event_type": "flexibility_reminder",
            "user_id": user_id,
            "community_id": community_id or "",
            "facts": facts,
        }
        await nudging.ingest_event(DigitalTwinEvent.from_dict(payload))
        logger.info(
            "Sent immediate flexibility_reminder (window imminent) user=%s suggestion=%s",
            user_id, suggestion_id,
        )
        return

    # Schedule for the future via /admin/scheduled-events (raw httpx,
    # same approach as celine-webapp BFF).
    if nudging._token_provider is None:
        logger.warning("No token provider on nudging client — cannot schedule nudge")
        return

    access_token = await nudging._token_provider.get_token()
    external_key = f"flexibility-accept:{user_id}:{suggestion_id}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{settings.nudging_api_url.rstrip('/')}/admin/scheduled-events",
            headers={"Authorization": f"Bearer {access_token.access_token}"},
            json={
                "event_type": "flexibility_reminder",
                "user_id": user_id,
                "external_key": external_key,
                "trigger_at": trigger_at.isoformat(),
                "facts": facts,
            },
        )

    if response.status_code in {200, 201}:
        logger.info(
            "Scheduled pre-window nudge user=%s suggestion=%s trigger_at=%s",
            user_id, suggestion_id, trigger_at.isoformat(),
        )
    else:
        logger.warning(
            "Failed to schedule pre-window nudge user=%s suggestion=%s: %s %s",
            user_id, suggestion_id, response.status_code, response.text[:200],
        )
