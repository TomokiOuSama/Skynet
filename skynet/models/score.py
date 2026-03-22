"""KOL scoring models - dynamic weight assignment."""

import datetime as dt

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from skynet.models.base import Base, TimestampMixin


class KOLScore(TimestampMixin, Base):
    """Current composite score for a KOL - updated periodically."""

    __tablename__ = "kol_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kol_id: Mapped[int] = mapped_column(Integer, ForeignKey("kols.id"), unique=True)

    # Sub-scores (0.0 - 1.0)
    originality_score: Mapped[float] = mapped_column(Float, default=0.0)
    accuracy_score: Mapped[float] = mapped_column(Float, default=0.0)
    social_score: Mapped[float] = mapped_column(Float, default=0.0)

    # Weighted composite
    composite_score: Mapped[float] = mapped_column(Float, default=0.0)

    # Stats backing the scores
    total_calls: Mapped[int] = mapped_column(Integer, default=0)
    accurate_calls: Mapped[int] = mapped_column(Integer, default=0)
    first_caller_count: Mapped[int] = mapped_column(Integer, default=0)

    kol: Mapped["KOL"] = relationship(back_populates="scores")  # noqa: F821


class ScoreSnapshot(Base):
    """Historical snapshots of KOL scores for trend analysis."""

    __tablename__ = "score_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kol_id: Mapped[int] = mapped_column(Integer, ForeignKey("kols.id"), index=True)
    snapshot_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    originality_score: Mapped[float] = mapped_column(Float)
    accuracy_score: Mapped[float] = mapped_column(Float)
    social_score: Mapped[float] = mapped_column(Float)
    composite_score: Mapped[float] = mapped_column(Float)

    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
