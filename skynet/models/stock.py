"""Stock call tracking and price data models."""

import datetime as dt
import enum

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from skynet.models.base import Base, TimestampMixin


class CallDirection(str, enum.Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class StockCall(TimestampMixin, Base):
    """A KOL's stock call - extracted from content, linked to price performance."""

    __tablename__ = "stock_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kol_id: Mapped[int] = mapped_column(Integer, ForeignKey("kols.id"), index=True)
    content_id: Mapped[int] = mapped_column(Integer, ForeignKey("contents.id"))
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    direction: Mapped[CallDirection] = mapped_column(Enum(CallDirection))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)

    called_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    price_at_call: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Performance tracking (filled in later)
    price_after_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_after_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_after_90d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_90d: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Was this KOL the first to call this ticker? (originality)
    is_first_caller: Mapped[bool] = mapped_column(default=False)
    hours_after_first: Mapped[float | None] = mapped_column(Float, nullable=True)


class StockPrice(Base):
    """Cached stock price data for performance calculations."""

    __tablename__ = "stock_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    date: Mapped[dt.date] = mapped_column()
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)

    __table_args__ = (UniqueConstraint("ticker", "date", name="uq_stock_price_ticker_date"),)
