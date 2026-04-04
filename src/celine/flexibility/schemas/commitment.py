from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel


class CommitmentCreate(BaseModel):
    user_id: str
    suggestion_id: str
    suggestion_type: str = "shift-consumption"
    community_id: Optional[str] = None
    device_id: Optional[str] = None
    period_start: datetime
    period_end: datetime
    reward_points_estimated: int = 0


class CommitmentSettle(BaseModel):
    reward_points_actual: int
    actual_kwh: Optional[float] = None


class CommitmentOut(BaseModel):
    id: UUID
    user_id: str
    suggestion_id: str
    suggestion_type: str
    community_id: Optional[str]
    device_id: Optional[str]
    period_start: datetime
    period_end: datetime
    committed_at: datetime
    settled_at: Optional[datetime]
    reminded_at: Optional[datetime]
    status: Literal["committed", "settled", "rejected", "cancelled"]
    reward_points_estimated: int
    reward_points_actual: Optional[int]

    model_config = {"from_attributes": True}


class CommitmentListResponse(BaseModel):
    items: list[CommitmentOut]
    total: int
