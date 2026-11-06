"""Flexibility commitment settlement.

Triggered when the rec_flexibility_flow pipeline completes.

Fetches rec_settlement_1h via DTClient community domain to get actual
per-device consumption kWh for the specific commitment window,
then settles any open commitments whose window falls within that period.

The community domain fetcher is used (not participant) because settlement runs
with a service token — the participant domain requires a user JWT for REC
Registry entity resolution.

Access control is enforced by dataset-api (access_level: internal, requires
dataset.query scope). svc-flexibility carries that scope so the token chain
flexibility-api → DT → dataset-api is fully authorised.

reward_points_actual derives from rec_settlement_1h which is computed from
actual measured consumption kWh at 15-min granularity (×0.25 kW→kWh), rolled
up to hourly. Points = round(sum(consumption_kwh) × 10).
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
    """Settle every open commitment whose window closed on or before period_date.

    The only bound is the end of period_date (UTC). There is deliberately no lower
    bound: a commitment skipped by an earlier run — DT unreachable, readings not there
    yet, pipeline message missed, pod restarting — is looked at again on the next one.
    Selecting on `status = committed` is what keeps that retry idempotent. (Until
    2026-09-07 the selection was the single day, and 34 commitments skipped once in
    staging stayed `committed` for weeks.)

    Fetches rec_settlement_1h for each commitment's exact window from DTClient
    community domain, sums consumption_kwh, and computes
    reward_points_actual = round(sum × 10).

    Returns the count of settled commitments.
    """
    period_end = datetime.combine(period_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    rows = (
        await session.execute(
            select(FlexibilityCommitment).where(
                FlexibilityCommitment.status == "committed",
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
                # WARNING, not DEBUG: a skip is retried tomorrow, but a commitment
                # that keeps being skipped is the only visible trace of missing data.
                logger.warning(
                    "No settlement data for commitment=%s device=%s window=%s–%s "
                    "— left committed, retried on the next run",
                    row.id, device_id, window_start, window_end,
                )
                continue

            actual_kwh = sum(
                _float(item.to_dict().get("consumption_kwh"))
                for item in result.items
            )
            actual_points = round(actual_kwh * 10)

            row.status = "settled"
            row.reward_points_actual = actual_points
            row.settled_at = now
            settled += 1
            logger.info(
                "Settled commitment=%s community=%s device=%s window=%s–%s "
                "actual_kwh=%.3f pts=%d",
                row.id, community_id, device_id, window_start, window_end,
                actual_kwh, actual_points,
            )

    if settled:
        await session.commit()

    return settled


# ─── Fair gamification settlement (demo3 spec 2026-10-07, design 2026-10-09) ──────


def _iso_utc(value: datetime) -> str:
    """ISO timestamp with an explicit offset; a naive value is taken as UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _int(val: object, default: int = 0) -> int:
    try:
        return int(round(float(val)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


async def settle_fair_outcomes(
    session: AsyncSession,
    dt: DTClient,
    nudging: object | None,
    period_date: date,
) -> int:
    """Settle open commitments from the pipeline's weekly fair outcomes.

    For every `committed` row whose window closed by the end of `period_date`, the
    community-domain fetcher `rec_fair_commitment_outcome` is asked for the verdict the
    rec_flexibility pipeline computed (`rec_fair_commitment_outcomes`): kept → the fixed
    points plus the true-surplus extra; not kept → 0; no overlapping window → 0 and
    "not evaluable", never "missed". Outcomes land once a week, so a row with no outcome
    yet is left `committed` and looked at again on the next run (idempotent, like
    `settle_completed_windows`).

    Each settled commitment is announced to the member with a `commitment_settled`
    nudge carrying `kept` and `reason`, when a nudging client is available; a failed
    nudge never undoes the settlement.

    Returns the count of settled commitments.
    """
    period_end = datetime.combine(period_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    rows = (
        await session.execute(
            select(FlexibilityCommitment).where(
                FlexibilityCommitment.status == "committed",
                FlexibilityCommitment.period_end <= period_end,
            )
        )
    ).scalars().all()
    if not rows:
        return 0

    settled = 0
    announcements: list[tuple[FlexibilityCommitment, dict]] = []
    now = datetime.now(timezone.utc)
    for row in rows:
        community_id = row.community_id or ""
        device_id = row.device_id or ""
        if not community_id or not device_id:
            logger.warning("Commitment %s has no community/device — cannot settle (fair)", row.id)
            continue
        try:
            result = await dt.communities.fetch_values(
                community_id=community_id,
                fetcher_id="rec_fair_commitment_outcome",
                payload={
                    "device_id": device_id,
                    "period_start": _iso_utc(row.period_start),
                    "period_end": _iso_utc(row.period_end),
                },
            )
        except Exception as exc:
            logger.warning("Failed to fetch rec_fair_commitment_outcome commitment=%s: %s", row.id, exc)
            continue
        if not result or result.count == 0:
            logger.info("No fair outcome yet for commitment=%s — retried on the next run", row.id)
            continue

        outcome = result.items[0].to_dict()
        kept = bool(outcome.get("is_kept"))
        points = _int(outcome.get("points")) + _int(outcome.get("surplus_points"))
        reason = str(outcome.get("reason") or ("kept" if kept else "not_kept"))

        row.status = "settled"
        row.reward_points_actual = points
        row.settled_at = now
        settled += 1
        announcements.append(
            (
                row,
                {
                    "facts_version": "1.0",
                    "scenario": "commitment_settled",
                    "commitment_id": str(row.id),
                    "kept": "true" if kept else "false",
                    "reason": reason,
                    "reward_points_actual": str(points),
                    "actual_kwh": "",
                    "window_start": row.period_start.strftime("%H:%M"),
                    "window_end": row.period_end.strftime("%H:%M"),
                    "period": row.period_start.date().isoformat(),
                },
            )
        )
        logger.info("Fair-settled commitment=%s device=%s kept=%s reason=%s pts=%d", row.id, device_id, kept, reason, points)

    if settled:
        await session.commit()

    if nudging is not None:
        from celine.sdk.openapi.nudging.models import DigitalTwinEvent

        for row, facts in announcements:
            payload = {
                "event_type": "commitment_settled",
                "user_id": row.user_id,
                "community_id": row.community_id,
                "facts": facts,
            }
            try:
                await nudging.ingest_event(DigitalTwinEvent.from_dict(payload))  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning("Failed to send commitment_settled to user=%s: %s", row.user_id, exc)

    return settled
