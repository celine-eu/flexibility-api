"""Commitment endpoints.

POST   /api/commitments                  create — user or service JWT
GET    /api/commitments                  list   — user sees own; service sees all (filter by user_id)
DELETE /api/commitments/{id}             cancel — user JWT (ownership enforced)
GET    /api/commitments/pending          due now — service only (via PolicyMiddleware)
PATCH  /api/commitments/{id}/settle      settle  — service only (via PolicyMiddleware)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from celine.flexibility.api.deps import DbDep, ServiceDep, UserDep
from celine.flexibility.models.commitment import FlexibilityCommitment
from celine.flexibility.schemas.commitment import (
    CommitmentCreate,
    CommitmentListResponse,
    CommitmentOut,
    CommitmentSettle,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/commitments", tags=["commitments"])


# ── helpers ──────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_out(row: FlexibilityCommitment) -> CommitmentOut:
    return CommitmentOut.model_validate(row)


# ── endpoints ────────────────────────────────────────────────────────────────

@router.post("", response_model=CommitmentOut, status_code=201)
async def create_commitment(
    body: CommitmentCreate,
    user: UserDep,
    db: DbDep,
) -> CommitmentOut:
    """Create a new commitment.

    A user JWT may only create commitments for itself (body.user_id ignored — always
    set to caller's sub).  A service JWT may create on behalf of any user_id.
    """
    effective_user_id = body.user_id if user.is_service_account else user.sub

    async with db as session:
        row = FlexibilityCommitment(
            user_id=effective_user_id,
            suggestion_id=body.suggestion_id,
            suggestion_type=body.suggestion_type,
            community_id=body.community_id,
            device_id=body.device_id,
            period_start=body.period_start,
            period_end=body.period_end,
            status="committed",
            reward_points_estimated=body.reward_points_estimated,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _row_to_out(row)


@router.get("", response_model=CommitmentListResponse)
async def list_commitments(
    user: UserDep,
    db: DbDep,
    user_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> CommitmentListResponse:
    """List commitments.

    Users see only their own.  Service accounts may filter by any user_id.
    """
    effective_user_id = user_id if user.is_service_account else user.sub

    async with db as session:
        q = select(FlexibilityCommitment).where(
            FlexibilityCommitment.user_id == effective_user_id
        )
        if status:
            q = q.where(FlexibilityCommitment.status == status)
        q = q.order_by(FlexibilityCommitment.committed_at.desc()).limit(limit).offset(offset)
        rows = (await session.execute(q)).scalars().all()

    return CommitmentListResponse(items=[_row_to_out(r) for r in rows], total=len(rows))


@router.delete("/{commitment_id}", status_code=204)
async def cancel_commitment(
    commitment_id: uuid.UUID,
    user: UserDep,
    db: DbDep,
) -> None:
    """Cancel a pending commitment.  Only the owning user may cancel."""
    async with db as session:
        row = (
            await session.execute(
                select(FlexibilityCommitment).where(
                    FlexibilityCommitment.id == commitment_id,
                    FlexibilityCommitment.user_id == user.sub,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            raise HTTPException(status_code=404, detail="Commitment not found")
        if row.status != "committed":
            raise HTTPException(status_code=409, detail="Only committed commitments can be cancelled")

        row.status = "cancelled"
        await session.commit()


@router.get("/pending", response_model=list[CommitmentOut])
async def get_pending(
    _svc: ServiceDep,
    db: DbDep,
) -> list[CommitmentOut]:
    """Return commitments whose window has opened but not yet closed.

    Called by DT on each pipeline tick (meters-flow, every 5 min).
    Marks returned rows as reminded_at = now so they are not re-sent on the
    next tick within the same window.
    """
    now = _now()
    async with db as session:
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

        # Stamp reminded_at so these won't be returned again
        for row in rows:
            row.reminded_at = now

        if rows:
            await session.commit()

    return [_row_to_out(r) for r in rows]


@router.patch("/{commitment_id}/settle", response_model=CommitmentOut)
async def settle_commitment(
    commitment_id: uuid.UUID,
    body: CommitmentSettle,
    _svc: ServiceDep,
    db: DbDep,
) -> CommitmentOut:
    """Settle a commitment with actual kWh and reward points.  Service only."""
    async with db as session:
        row = (
            await session.execute(
                select(FlexibilityCommitment).where(
                    FlexibilityCommitment.id == commitment_id,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            raise HTTPException(status_code=404, detail="Commitment not found")
        if row.status != "committed":
            raise HTTPException(status_code=409, detail="Only committed commitments can be settled")

        row.status = "settled"
        row.reward_points_actual = body.reward_points_actual
        row.settled_at = _now()
        await session.commit()
        await session.refresh(row)
        return _row_to_out(row)
