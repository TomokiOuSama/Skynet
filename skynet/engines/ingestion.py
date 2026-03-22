"""Content Ingestion Pipeline - fetches and processes content from all tracked KOLs.

This engine:
1. Fetches new content from each tracked KOL
2. Analyzes content for stock mentions and sentiment
3. Creates StockCall records for ticker mentions
4. Records timeline for originality scoring
"""

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from skynet.models.content import Content, ContentType, TickerMention
from skynet.models.kol import KOL, Platform
from skynet.models.stock import CallDirection, StockCall
from skynet.scrapers.twitter import TwitterScraper
from skynet.utils.content_analyzer import extract_tickers, is_stock_related, simple_sentiment

logger = logging.getLogger(__name__)

# Map tweepy reference types to our ContentType
_TWEET_TYPE_MAP = {
    "original": ContentType.ORIGINAL,
    "retweeted": ContentType.RETWEET,
    "quoted": ContentType.QUOTE,
    "replied_to": ContentType.REPLY,
}


class IngestionEngine:
    """Ingests content from all sources and extracts stock signals."""

    def __init__(self, session: AsyncSession, twitter_scraper: TwitterScraper):
        self.session = session
        self.twitter = twitter_scraper

    async def run_ingestion_cycle(self) -> int:
        """Ingest new content from all active KOLs. Returns count of new items."""
        stmt = select(KOL).where(KOL.is_active.is_(True))
        result = await self.session.execute(stmt)
        kols = result.scalars().all()

        total_new = 0
        for kol in kols:
            try:
                count = await self._ingest_kol(kol)
                total_new += count
            except Exception:
                logger.exception(f"Ingestion failed for {kol}")

        logger.info(f"Ingestion cycle complete: {total_new} new items from {len(kols)} KOLs")
        return total_new

    async def _ingest_kol(self, kol: KOL) -> int:
        """Ingest content from a single KOL."""
        if kol.platform == Platform.TWITTER:
            return await self._ingest_twitter(kol)
        # Reddit and Substack follow similar patterns
        return 0

    async def _ingest_twitter(self, kol: KOL) -> int:
        """Ingest tweets from a Twitter KOL."""
        tweets = await self.twitter.get_user_tweets(kol.platform_user_id)
        new_count = 0

        for tweet in tweets:
            # Skip if already ingested
            existing = await self.session.execute(
                select(Content).where(
                    Content.platform == Platform.TWITTER.value,
                    Content.platform_content_id == tweet["id"],
                )
            )
            if existing.scalar_one_or_none():
                continue

            # Skip non-stock-related content
            if not is_stock_related(tweet["text"]):
                continue

            content_type = _TWEET_TYPE_MAP.get(tweet["type"], ContentType.ORIGINAL)
            published_at = (
                dt.datetime.fromisoformat(tweet["created_at"])
                if tweet["created_at"]
                else dt.datetime.now(dt.timezone.utc)
            )

            content = Content(
                kol_id=kol.id,
                platform=Platform.TWITTER.value,
                platform_content_id=tweet["id"],
                content_type=content_type,
                text=tweet["text"],
                url=f"https://x.com/{kol.username}/status/{tweet['id']}",
                published_at=published_at,
                likes=tweet.get("likes", 0),
                reposts=tweet.get("retweets", 0),
                replies=tweet.get("replies", 0),
                views=tweet.get("views", 0),
            )
            self.session.add(content)
            await self.session.flush()

            # Extract tickers and create mentions + calls
            tickers = tweet.get("cashtags", []) or extract_tickers(tweet["text"])
            for ticker in tickers:
                sentiment = simple_sentiment(tweet["text"])

                mention = TickerMention(
                    content_id=content.id,
                    ticker=ticker,
                    sentiment=sentiment,
                    confidence=0.6,
                )
                self.session.add(mention)

                # Only create stock calls for original content (not retweets)
                if content_type in (ContentType.ORIGINAL, ContentType.QUOTE):
                    direction = (
                        CallDirection.BULLISH if sentiment > 0.2
                        else CallDirection.BEARISH if sentiment < -0.2
                        else CallDirection.NEUTRAL
                    )

                    # Check originality - was anyone else first?
                    first_call = await self.session.execute(
                        select(StockCall)
                        .where(
                            StockCall.ticker == ticker,
                            StockCall.called_at >= published_at - dt.timedelta(days=7),
                            StockCall.called_at < published_at,
                        )
                        .order_by(StockCall.called_at.asc())
                        .limit(1)
                    )
                    first = first_call.scalar_one_or_none()

                    call = StockCall(
                        kol_id=kol.id,
                        content_id=content.id,
                        ticker=ticker,
                        direction=direction,
                        confidence=0.6,
                        called_at=published_at,
                        is_first_caller=first is None,
                        hours_after_first=(
                            (published_at - first.called_at).total_seconds() / 3600
                            if first
                            else 0.0
                        ),
                    )
                    self.session.add(call)

            new_count += 1

        await self.session.commit()
        return new_count
