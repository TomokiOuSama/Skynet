"""Stock call tracking and price data models.

Key improvements over v1:
- Extended time windows: 7d, 30d, 60d, 90d, 180d, 360d
- Benchmark alpha: each call tracks sector ETF performance for alpha calculation
- Conviction level: high vs low (from LLM extraction)
- 14-day dedup window per KOL per ticker
"""

import datetime as dt
import enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from skynet.models.base import Base, TimestampMixin


class CallDirection(str, enum.Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class ConvictionLevel(str, enum.Enum):
    HIGH = "high"
    LOW = "low"


# Sector ETF mapping for benchmark alpha calculation
SECTOR_ETF_MAP = {
    "semiconductors": "SOXX",
    "software": "IGV",
    "saas": "IGV",
    "china_tech": "KWEB",
    "japan": "EWJ",
    "gold": "GLD",
    "biotech": "XBI",
    "energy": "XLE",
    "financials": "XLF",
    "default": "SPY",
}


class StockCall(TimestampMixin, Base):
    """A KOL's stock call — extracted from content, linked to price performance.

    Each call tracks both absolute return AND alpha vs sector benchmark.
    """

    __tablename__ = "stock_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kol_id: Mapped[int] = mapped_column(Integer, ForeignKey("kols.id"), index=True)
    content_id: Mapped[int] = mapped_column(Integer, ForeignKey("contents.id"))
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    direction: Mapped[CallDirection] = mapped_column(Enum(CallDirection))
    conviction: Mapped[ConvictionLevel] = mapped_column(
        Enum(ConvictionLevel), default=ConvictionLevel.LOW
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.5)

    called_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    price_at_call: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Sector benchmark ETF for alpha calculation
    benchmark_ticker: Mapped[str | None] = mapped_column(String(16), nullable=True)
    benchmark_price_at_call: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Performance tracking — absolute returns
    price_after_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_after_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_after_60d: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_after_90d: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_after_180d: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_after_360d: Mapped[float | None] = mapped_column(Float, nullable=True)

    return_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_60d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_90d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_180d: Mapped[float | None] = mapped_column(Float, nullable=True)
    return_360d: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Benchmark returns (sector ETF over same period)
    benchmark_return_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_return_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_return_60d: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_return_90d: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_return_180d: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_return_360d: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Alpha = return - benchmark_return (filled in by stock tracker)
    alpha_7d: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha_30d: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha_60d: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha_90d: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha_180d: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha_360d: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Originality
    is_first_caller: Mapped[bool] = mapped_column(default=False)
    hours_after_first: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Dedup: was this call deduplicated (same KOL, same ticker within 14 days)?
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)


class StockPrice(Base):
    """Cached stock price data for performance calculations.

    Stores both individual stock prices and ETF benchmark prices.
    """

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
