"""Persistent flexibility reminder dispatch.

Replaces the in-memory _pending dict in digital-twin/nudging/commitment.py.
Commitment state is read from the DB so reminders survive process restarts.

Called on every meters-flow pipeline tick (every ~5 min) via the MQTT listener.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.sdk.nudging.client import NudgingAdminClient
from celine.sdk.openapi.nudging.models import DigitalTwinEvent

from celine.flexibility.models.commitment import FlexibilityCommitment

logger = logging.getLogger(__name__)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def send_pending_reminders(
    session: AsyncSession,
    nudging: NudgingAdminClient,
) -> int:
    """Query committed+due commitments, stamp reminded_at, send flexibility_reminder nudge.

    Returns the count of reminders dispatched.
    """
    now = datetime.now(timezone.utc)

    rows = (
        await session.execute(
            select(FlexibilityCommitment).where(
                FlexibilityCommitment.status == "committed",
                FlexibilityCommitment.period_start <= now,
                FlexibilityCommitment.period_end > now,
                FlexibilityCommitment.reminded_at.is_(None),
            )
        )
    ).scalars().all()

    if not rows:
        return 0

    for row in rows:
        row.reminded_at = now
    await session.commit()

    sent = 0
    for row in rows:
        window_start = _as_utc(row.period_start)
        window_end = _as_utc(row.period_end)
        nudge_payload = {
            "event_type": "flexibility_reminder",
            "user_id": row.user_id,
            "community_id": row.community_id or "",
            "facts": {
                "facts_version": "1.0",
                "scenario": "flexibility_reminder",
                "commitment_id": str(row.id),
                "window_start": window_start.strftime("%H:%M"),
                "window_end": window_end.strftime("%H:%M"),
                "reward_points_estimated": str(row.reward_points_estimated),
                "period": window_start.strftime("%Y-%m-%d"),
            },
        }
        try:
            await nudging.ingest_event(DigitalTwinEvent.from_dict(nudge_payload))
            logger.info(
                "Sent flexibility_reminder user=%s commitment=%s window=%s-%s pts=%d",
                row.user_id,
                row.id,
                window_start.strftime("%H:%M"),
                window_end.strftime("%H:%M"),
                row.reward_points_estimated,
            )
            sent += 1
        except Exception as exc:
            logger.warning(
                "Failed to send flexibility_reminder for commitment=%s: %s", row.id, exc
            )

    return sent
