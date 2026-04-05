"""Suggestion endpoints.

GET  /api/suggestions                       list   — user JWT
POST /api/suggestions/{suggestion_id}/respond  respond — user JWT

Suggestions are fetched from rec_flexibility_windows via DTClient (service OIDC
token). The participant_id is user.sub resolved from the incoming JWT.

Commitment creation (on acceptance) calls existing DB logic and publishes a
flexibility.committed event to MQTT so downstream services (DT legacy path,
and the pipeline_listener reminder loop) can pick it up.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from celine.sdk.auth.static import StaticTokenProvider
from celine.sdk.broker import BrokerMessage, QoS
from celine.sdk.dt.client import DTClient

from celine.flexibility.api.deps import DbDep, UserDep
from celine.flexibility.core.config import settings
from celine.flexibility.models.commitment import FlexibilityCommitment
from celine.flexibility.schemas.suggestion import (
    SuggestionItem,
    SuggestionRespondRequest,
    SuggestionRespondResponse,
)
from celine.flexibility.security.auth import get_raw_token
from celine.flexibility.services.pipeline_listener import get_broker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/suggestions", tags=["suggestions"])

_MIN_IMPACT_KWH = 0.5
_MAX_SUGGESTIONS = 2


# ── period helpers (mirrored from celine-webapp) ──────────────────────────────

def _period_from_hour(hour: int) -> str:
    if hour < 5:
        return "night"
    if hour < 8:
        return "early_morning"
    if hour < 11:
        return "morning"
    if hour < 12:
        return "late_morning"
    if hour < 14:
        return "midday"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "night"


_PERIOD_CLOCK: dict[str, str] = {
    "night":         "22:00–06:00",
    "early_morning": "06:00–08:00",
    "morning":       "08:00–10:00",
    "late_morning":  "11:00–12:00",
    "midday":        "12:00–14:00",
    "afternoon":     "14:00–17:00",
    "evening":       "17:00–21:00",
}


def _shift_from(window_start: datetime, today_date: object) -> tuple[str, str]:
    is_tomorrow = window_start.date() > today_date  # type: ignore[operator]
    if is_tomorrow:
        return "evening", _PERIOD_CLOCK["evening"]
    return "", ""


def _float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _get_dt_client(raw_token: str) -> DTClient:
    """DTClient that forwards the user's JWT — required so DT resolves the participant entity."""
    return DTClient(
        base_url=settings.digital_twin_api_url,
        token_provider=StaticTokenProvider(raw_token),
    )


# ── endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[SuggestionItem])
async def list_suggestions(
    request: Request,
    user: UserDep,
    db: DbDep,
) -> list[SuggestionItem]:
    """Return load-shift windows from rec_flexibility_windows for this participant.

    Uses a service OIDC token to call DTClient on behalf of the user; the
    participant_id is taken from user.sub (as in celine-webapp).
    """
    today_dt = datetime.now(timezone.utc).date()
    dt = _get_dt_client(get_raw_token(request))

    # Resolve device_id — required for per-device flexibility windows
    device_id = ""
    try:
        assets = await dt.participants.assets(user.sub)
        if assets and assets.items:
            for asset in assets.items:
                if asset.sensor_id:
                    device_id = asset.sensor_id
                    break
    except Exception as exc:
        logger.warning("Failed to fetch assets for user=%s: %s", user.sub, exc)

    if not device_id:
        return []

    # Filter out suggestion_ids already committed by this user
    async with db as session:
        committed_ids: set[str] = set(
            (
                await session.execute(
                    select(FlexibilityCommitment.suggestion_id).where(
                        FlexibilityCommitment.user_id == user.sub,
                        FlexibilityCommitment.status == "committed",
                    )
                )
            ).scalars().all()
        )

    try:
        res = await dt.participants.fetch_values(
            participant_id=user.sub,
            fetcher_id="rec_flexibility_windows",
            payload={"device_id": device_id},
        )
    except Exception as exc:
        logger.warning("rec_flexibility_windows fetch failed for user=%s: %s", user.sub, exc)
        return []

    if not res or res.count == 0:
        return []

    result: list[SuggestionItem] = []
    for item in res.items:
        d: dict[str, Any] = item.to_dict()
        try:
            window_id = str(d.get("_id", ""))
            if window_id in committed_ids:
                continue
            impact = _float(d.get("estimated_kwh"))
            if impact < _MIN_IMPACT_KWH:
                continue
            window_start = datetime.fromisoformat(str(d["window_start"]))
            window_end = datetime.fromisoformat(str(d["window_end"]))
            is_tomorrow = window_start.date() > today_dt
            from_period, from_clock = _shift_from(window_start, today_dt)
            window_clock = f"{window_start.strftime('%H:%M')}–{window_end.strftime('%H:%M')}"
            result.append(
                SuggestionItem(
                    id=window_id,
                    suggestion_type="shift-consumption",
                    period_start=window_start.isoformat(),
                    period_end=window_end.isoformat(),
                    from_period=from_period,
                    clock_range=from_clock if from_period else window_clock,
                    to_is_tomorrow=is_tomorrow,
                    to_period=_period_from_hour(window_start.hour),
                    to_time=window_start.strftime("%H:%M"),
                    impact_kwh_estimated=impact,
                    reward_points=int(d.get("reward_points_estimated", 0)),
                    confidence=_float(d.get("confidence"), 0.75),
                )
            )
        except (KeyError, ValueError) as exc:
            logger.warning("Skipping malformed flexibility window: %s", exc)

    return result[:_MAX_SUGGESTIONS]


@router.post("/{suggestion_id}/respond", response_model=SuggestionRespondResponse)
async def respond_to_suggestion(
    suggestion_id: str,
    body: SuggestionRespondRequest,
    request: Request,
    user: UserDep,
    db: DbDep,
) -> SuggestionRespondResponse:
    """Record a user's response to a flexibility suggestion.

    On acceptance:
      1. Resolves community_id and device_id via DTClient.
      2. Creates a FlexibilityCommitment record in the DB.
      3. Publishes flexibility.committed to MQTT.

    Gamification points are awarded by celine-webapp (BFF layer), not here.
    """
    now = datetime.now(timezone.utc)
    reward_points = body.reward_points if body.reward_points is not None else 10

    if body.response == "declined":
        return SuggestionRespondResponse(
            commitment_id=None,
            status="declined",
            reward_points_estimated=0,
        )

    # Resolve community_id and device_id for the commitment record and MQTT payload
    dt = _get_dt_client(get_raw_token(request))
    community_id = ""
    device_id = ""

    try:
        from celine.sdk.openapi.dt.models import UserMembershipSchema
        participant = await dt.participants.profile(user.sub)
        _m = participant.membership
        if isinstance(_m, UserMembershipSchema):
            community_id = _m.community.key
    except Exception as exc:
        logger.warning("Failed to fetch participant profile for user=%s: %s", user.sub, exc)

    try:
        assets = await dt.participants.assets(user.sub)
        if assets and assets.items:
            for asset in assets.items:
                if asset.sensor_id:
                    device_id = asset.sensor_id
                    break
    except Exception as exc:
        logger.warning("Failed to fetch assets for user=%s: %s", user.sub, exc)

    # Parse window timestamps
    try:
        window_start = datetime.fromisoformat(body.period_start) if body.period_start else now
        window_end = datetime.fromisoformat(body.period_end) if body.period_end else now + timedelta(hours=1)
    except (ValueError, TypeError):
        window_start = now
        window_end = now + timedelta(hours=1)

    # Persist the commitment
    commitment_row = FlexibilityCommitment(
        user_id=user.sub,
        suggestion_id=suggestion_id,
        suggestion_type="shift-consumption",
        community_id=community_id or None,
        device_id=device_id or None,
        period_start=window_start,
        period_end=window_end,
        status="committed",
        reward_points_estimated=reward_points,
    )
    async with db as session:
        session.add(commitment_row)
        await session.commit()
        await session.refresh(commitment_row)

    # Publish flexibility.committed to MQTT (fire-and-forget; commitment is already persisted)
    await _publish_committed(commitment_row, community_id, device_id)

    return SuggestionRespondResponse(
        commitment_id=commitment_row.id,
        status="committed",
        reward_points_estimated=commitment_row.reward_points_estimated,
    )


async def _publish_committed(
    row: FlexibilityCommitment,
    community_id: str,
    device_id: str,
) -> None:
    """Publish flexibility.committed to MQTT.

    Topic: celine/flexibility/committed/{user_id}
    Fire-and-forget — failure is logged but does not fail the API response.
    Uses the shared broker created at startup; no-ops if broker is not connected.
    """
    broker = get_broker()
    if broker is None or not broker.is_connected:
        logger.warning(
            "MQTT broker not available — skipping publish for commitment=%s", row.id
        )
        return

    payload = {
        "user_id": row.user_id,
        "community_id": community_id,
        "commitment_id": str(row.id),
        "device_id": device_id,
        "window_start": row.period_start.isoformat(),
        "window_end": row.period_end.isoformat(),
        "reward_points_estimated": row.reward_points_estimated,
    }
    topic = f"celine/flexibility/committed/{row.user_id}"

    try:
        result = await broker.publish(
            BrokerMessage(topic=topic, payload=payload, qos=QoS.AT_LEAST_ONCE)
        )
        if not result.success:
            logger.warning("MQTT publish failed for commitment=%s: %s", row.id, result.error)
        else:
            logger.debug("Published flexibility.committed for commitment=%s", row.id)
    except Exception as exc:
        logger.warning("Failed to publish flexibility.committed for commitment=%s: %s", row.id, exc)
