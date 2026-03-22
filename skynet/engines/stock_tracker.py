"""Stock Performance Tracker - fetches price data and scores call accuracy.

Uses yfinance for free stock price data. Tracks price performance
at 7, 30, and 90 day windows after each KOL's stock call.
"""

import datetime as dt
import logging

import yfinance as yf
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from skynet.models.stock import StockCall, StockPrice

logger = logging.getLogger(__name__)


class StockTracker:
    """Tracks stock prices and evaluates KOL call accuracy."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def update_price_cache(self, tickers: list[str], days: int = 120):
        """Fetch and cache recent price data for given tickers."""
        end = dt.date.today()
        start = end - dt.timedelta(days=days)

        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(start=start.isoformat(), end=end.isoformat())

                for date, row in hist.iterrows():
                    trade_date = date.date()
                    existing = await self.session.execute(
                        select(StockPrice).where(
                            StockPrice.ticker == ticker,
                            StockPrice.date == trade_date,
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue

                    price = StockPrice(
                        ticker=ticker,
                        date=trade_date,
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=int(row["Volume"]),
                    )
                    self.session.add(price)

                await self.session.commit()
                logger.info(f"Updated price cache for {ticker}")
            except Exception:
                logger.exception(f"Failed to fetch prices for {ticker}")

    async def evaluate_calls(self):
        """Evaluate all stock calls that have matured (enough time has passed)."""
        now = dt.datetime.now(dt.timezone.utc)

        # Find calls missing performance data
        windows = [
            ("price_after_7d", "return_7d", 7),
            ("price_after_30d", "return_30d", 30),
            ("price_after_90d", "return_90d", 90),
        ]

        for price_col, return_col, days in windows:
            cutoff = now - dt.timedelta(days=days)

            stmt = select(StockCall).where(
                and_(
                    getattr(StockCall, price_col).is_(None),
                    StockCall.called_at <= cutoff,
                    StockCall.price_at_call.isnot(None),
                )
            )
            result = await self.session.execute(stmt)
            calls = result.scalars().all()

            for call in calls:
                target_date = call.called_at.date() + dt.timedelta(days=days)
                price = await self._get_closest_price(call.ticker, target_date)

                if price and call.price_at_call:
                    setattr(call, price_col, price)
                    ret = (price - call.price_at_call) / call.price_at_call
                    setattr(call, return_col, ret)

        await self.session.commit()
        logger.info("Call evaluation complete")

    async def backfill_call_prices(self):
        """Fill in price_at_call for calls that are missing it."""
        stmt = select(StockCall).where(StockCall.price_at_call.is_(None))
        result = await self.session.execute(stmt)
        calls = result.scalars().all()

        for call in calls:
            price = await self._get_closest_price(call.ticker, call.called_at.date())
            if price:
                call.price_at_call = price

        await self.session.commit()

    async def _get_closest_price(self, ticker: str, target_date: dt.date) -> float | None:
        """Get the closing price closest to a target date."""
        # Look within a 5-day window (weekends/holidays)
        stmt = (
            select(StockPrice)
            .where(
                StockPrice.ticker == ticker,
                StockPrice.date >= target_date - dt.timedelta(days=5),
                StockPrice.date <= target_date + dt.timedelta(days=5),
            )
            .order_by(
                # Sort by distance from target date
                (StockPrice.date - target_date).asc()
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        price_row = result.scalar_one_or_none()
        return price_row.close if price_row else None

    async def get_active_tickers(self) -> list[str]:
        """Get all tickers with recent calls."""
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)
        stmt = (
            select(StockCall.ticker)
            .where(StockCall.called_at >= cutoff)
            .distinct()
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]
