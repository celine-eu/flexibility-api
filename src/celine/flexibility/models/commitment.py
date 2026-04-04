"""SQLAlchemy model for FlexibilityCommitment.

Moved from celine-webapp BFF; `reminded_at` column added.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, MetaData, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

from celine.flexibility.core.config import settings


class Base(DeclarativeBase):
    metadata = MetaData(schema=settings.db_schema)


class FlexibilityCommitment(Base):
    __tablename__ = "flexibility_commitments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    suggestion_id: Mapped[str] = mapped_column(String(255), nullable=False)
    suggestion_type: Mapped[str] = mapped_column(String(50), nullable=False)
    community_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    settled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reminded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # committed | settled | rejected | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="committed")

    reward_points_estimated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reward_points_actual: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
