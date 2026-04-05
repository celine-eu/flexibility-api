from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel


class SuggestionItem(BaseModel):
    id: str
    suggestion_type: str
    period_start: str
    period_end: str
    from_period: str       # i18n key, e.g. "morning" (shift-away period)
    clock_range: str       # e.g. "09:00–12:00"
    to_is_tomorrow: bool
    to_period: str         # i18n key for the target window
    to_time: str           # e.g. "10:30"
    impact_kwh_estimated: float
    reward_points: int
    confidence: float


class SuggestionRespondRequest(BaseModel):
    response: Literal["accepted", "declined"]
    reward_points: Optional[int] = None  # override reward_points_estimated from window
    period_start: Optional[str] = None   # ISO datetime of window start (required on accepted)
    period_end: Optional[str] = None     # ISO datetime of window end (required on accepted)


class SuggestionRespondResponse(BaseModel):
    commitment_id: Optional[UUID] = None
    status: Literal["committed", "declined"]
    reward_points_estimated: int
