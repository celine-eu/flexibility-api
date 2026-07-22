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
    # Personal enrichment — None when this member's device has no usable forecast
    # for the window (visibility is community-driven, never gated on these).
    impact_kwh_estimated: float | None = None
    reward_points: int | None = None
    # Community surplus budget for the window (kWh) — always present.
    community_kwh: float = 0.0
    # Measured window confidence (realized hit-rate from the pipeline); None until
    # enough scored history exists — the UI hides the bar when absent.
    confidence: float | None = None


class SuggestionRespondRequest(BaseModel):
    response: Literal["accepted", "declined"]
    reward_points: Optional[int] = None  # override reward_points_estimated from window
    period_start: Optional[str] = None   # ISO datetime of window start (required on accepted)
    period_end: Optional[str] = None     # ISO datetime of window end (required on accepted)


class SuggestionRespondResponse(BaseModel):
    commitment_id: Optional[UUID] = None
    status: Literal["committed", "declined"]
    reward_points_estimated: int
