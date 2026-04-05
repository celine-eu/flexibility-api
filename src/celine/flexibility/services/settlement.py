"""Flexibility commitment settlement.

Triggered when the rec_flexibility_flow pipeline completes.

Fetches rec_settlement_1h via DTClient community domain to get actual
per-device virtual consumption kWh for the specific commitment window,
then settles any open commitments whose window falls within that period.

The community domain fetcher is used (not participant) because settlement runs
with a service token — the participant domain requires a user JWT for REC
Registry entity resolution.

Access control is enforced by dataset-api (access_level: internal, requires
dataset.query scope). svc-flexibility carries that scope so the token chain
flexibility-api → DT → dataset-api is fully authorised.

reward_points_actual derives from rec_settlement_1h which is computed from
actual measured virtual self-consumption kWh at 15-min granularity, rolled up
to hourly. Points = round(sum(virtual_consumption_kwh) × 10).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celine.sdk.dt.client import DTClient

from celine.flexibility.models.commitment import FlexibilityCommitment

logger = logging.getLogger(__name__)


def _float(val: object, default: float = 0.0) -> float:
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


async def settle_completed_windows(
    session: AsyncSession,
    dt: DTClient,
    period_date: date,
) -> int:
    """Settle commitments whose window closed on period_date.

    Fetches rec_settlement_1h for each commitment's exact window from DTClient
    community domain, sums virtual_consumption_kwh, and computes
    reward_points_actual = round(sum × 10).

    Returns the count of settled commitments.
    """
    period_start = datetime.combine(period_date, datetime.min.time(), tzinfo=timezone.utc)
    period_end = datetime.combine(period_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    rows = (
        await session.execute(
            select(FlexibilityCommitment).where(
                FlexibilityCommitment.status == "committed",
                FlexibilityCommitment.period_start >= period_start,
                FlexibilityCommitment.period_end <= period_end,
            )
        )
    ).scalars().all()

    if not rows:
        logger.debug("No open commitments to settle for period=%s", period_date)
        return 0

    # Group by community_id — the community domain accepts service tokens
    # (participant domain requires user JWT for REC Registry entity resolution).
    by_community: dict[str, list[FlexibilityCommitment]] = {}
    for row in rows:
        cid = row.community_id or ""
        if not cid:
            logger.warning("Commitment %s has no community_id — cannot settle", row.id)
            continue
        by_community.setdefault(cid, []).append(row)

    settled = 0
    now = datetime.now(timezone.utc)

    for community_id, community_rows in by_community.items():
        for row in community_rows:
            device_id = row.device_id or ""
            if not device_id:
                logger.warning("Commitment %s has no device_id — skipping", row.id)
                continue

            window_start = row.period_start.isoformat()
            window_end = row.period_end.isoformat()

            try:
                result = await dt.communities.fetch_values(
                    community_id=community_id,
                    fetcher_id="rec_settlement_1h",
                    payload={
                        "device_id": device_id,
                        "window_start": window_start,
                        "window_end": window_end,
                    },
                )
            except Exception as exc:
                logger.warning(
                    "Failed to fetch rec_settlement_1h commitment=%s device=%s: %s",
                    row.id, device_id, exc,
                )
                continue

            if not result or result.count == 0:
                logger.debug(
                    "No settlement data for device=%s window=%s–%s — skipping",
                    device_id, window_start, window_end,
                )
                continue

            actual_virtual_kwh = sum(
                _float(item.to_dict().get("virtual_consumption_kwh"))
                for item in result.items
            )
            actual_points = round(actual_virtual_kwh * 10)

            row.status = "settled"
            row.reward_points_actual = actual_points
            row.settled_at = now
            settled += 1
            logger.info(
                "Settled commitment=%s community=%s device=%s window=%s–%s "
                "actual_kwh=%.3f pts=%d",
                row.id, community_id, device_id, window_start, window_end,
                actual_virtual_kwh, actual_points,
            )

    if settled:
        await session.commit()

    return settled
