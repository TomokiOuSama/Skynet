"""Stock Performance Tracker - fetches price data and scores call accuracy.

v2 improvements:
- Extended windows: 7d, 30d, 60d, 90d, 180d, 360d
- Benchmark alpha: tracks sector ETF performance alongside stock
- Caches ETF prices alongside stock prices
"""

import datetime as dt
import logging

import yfinance as yf
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from skynet.models.stock import SECTOR_ETF_MAP, StockCall, StockPrice

logger = logging.getLogger(__name__)

# All time windows we track
WINDOWS = [
    ("price_after_7d", "return_7d", "benchmark_return_7d", "alpha_7d", 7),
    ("price_after_30d", "return_30d", "benchmark_return_30d", "alpha_30d", 30),
    ("price_after_60d", "return_60d", "benchmark_return_60d", "alpha_60d", 60),
    ("price_after_90d", "return_90d", "benchmark_return_90d", "alpha_90d", 90),
    ("price_after_180d", "return_180d", "benchmark_return_180d", "alpha_180d", 180),
    ("price_after_360d", "return_360d", "benchmark_return_360d", "alpha_360d", 360),
]


class StockTracker:
    """Tracks stock prices and evaluates KOL call accuracy with benchmark alpha."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def update_price_cache(self, tickers: list[str], days: int = 400):
        """Fetch and cache recent price data for stocks AND their benchmark ETFs."""
        # Collect all unique benchmark ETFs
        benchmark_tickers = set(SECTOR_ETF_MAP.values())
        all_tickers = set(tickers) | benchmark_tickers

        end = dt.date.today()
        start = end - dt.timedelta(days=days)

        for ticker in all_tickers:
            try:
                stock = yf.Ticker(ticker)
                hist = stock.history(start=start.isoformat(), end=end.isoformat())

                for date_idx, row in hist.iterrows():
                    trade_date = date_idx.date()
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
            except Exception:
                logger.exception(f"Failed to fetch prices for {ticker}")

    async def evaluate_calls(self):
        """Evaluate all stock calls with both absolute returns and benchmark alpha."""
        now = dt.datetime.now(dt.timezone.utc)

        for price_col, return_col, bench_return_col, alpha_col, days in WINDOWS:
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

                # Stock price at target date
                stock_price = await self._get_closest_price(call.ticker, target_date)
                if stock_price and call.price_at_call:
                    setattr(call, price_col, stock_price)
                    stock_return = (stock_price - call.price_at_call) / call.price_at_call
                    setattr(call, return_col, stock_return)

                    # Benchmark return over same period
                    if call.benchmark_ticker and call.benchmark_price_at_call:
                        bench_price = await self._get_closest_price(
                            call.benchmark_ticker, target_date
                        )
                        if bench_price:
                            bench_return = (
                                (bench_price - call.benchmark_price_at_call)
                                / call.benchmark_price_at_call
                            )
                            setattr(call, bench_return_col, bench_return)
                            setattr(call, alpha_col, stock_return - bench_return)

        await self.session.commit()
        logger.info("Call evaluation complete")

    async def backfill_call_prices(self):
        """Fill in price_at_call and benchmark_price_at_call for new calls."""
        stmt = select(StockCall).where(StockCall.price_at_call.is_(None))
        result = await self.session.execute(stmt)
        calls = result.scalars().all()

        for call in calls:
            # Stock price at call time
            price = await self._get_closest_price(call.ticker, call.called_at.date())
            if price:
                call.price_at_call = price

            # Benchmark price at call time
            if call.benchmark_ticker:
                bench_price = await self._get_closest_price(
                    call.benchmark_ticker, call.called_at.date()
                )
                if bench_price:
                    call.benchmark_price_at_call = bench_price

        await self.session.commit()

    async def _get_closest_price(self, ticker: str, target_date: dt.date) -> float | None:
        """Get the closing price closest to a target date (within 5 day window)."""
        stmt = (
            select(StockPrice)
            .where(
                StockPrice.ticker == ticker,
                StockPrice.date >= target_date - dt.timedelta(days=5),
                StockPrice.date <= target_date + dt.timedelta(days=5),
            )
            .order_by(StockPrice.date.asc())
            .limit(10)
        )
        result = await self.session.execute(stmt)
        rows = result.scalars().all()

        if not rows:
            return None

        # Find the closest date
        closest = min(rows, key=lambda r: abs((r.date - target_date).days))
        return closest.close

    async def get_active_tickers(self) -> list[str]:
        """Get all tickers with recent calls (within 360 days)."""
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=360)
        stmt = (
            select(StockCall.ticker)
            .where(StockCall.called_at >= cutoff)
            .distinct()
        )
        result = await self.session.execute(stmt)
        return [row[0] for row in result.all()]
