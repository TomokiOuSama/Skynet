"""Content Ingestion Pipeline - multi-platform content fetching.

Platform roles:
  - Twitter:   Short-form signals (tweets with cashtags, sentiment)
  - Substack:  Long-form research articles (high-conviction call extraction via LLM)

All content feeds into the same KOL identity node for unified scoring.

Dedup: Same KOL + same ticker within 14 days = only first mention counts.
"""

import datetime as dt
import logging

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from skynet.models.content import Content, ContentType, TickerMention
from skynet.models.kol import KOL, Platform, PlatformAccount
from skynet.models.stock import CallDirection, ConvictionLevel, StockCall, SECTOR_ETF_MAP
from skynet.scrapers.twitter import TwitterScraper
from skynet.scrapers.substack import SubstackScraper
from skynet.utils.content_analyzer import extract_tickers, is_stock_related, simple_sentiment

logger = logging.getLogger(__name__)

DEDUP_WINDOW_DAYS = 14

_TWEET_TYPE_MAP = {
    "original": ContentType.ORIGINAL,
    "retweeted": ContentType.RETWEET,
    "quoted": ContentType.QUOTE,
    "replied_to": ContentType.REPLY,
}


def _guess_sector_etf(ticker: str, text: str) -> str:
    """Guess the sector benchmark ETF for a ticker based on context."""
    text_lower = text.lower()
    if any(kw in text_lower for kw in ("semiconductor", "chip", "fab", "wafer", "foundry", "soxx")):
        return SECTOR_ETF_MAP["semiconductors"]
    if any(kw in text_lower for kw in ("saas", "software", "cloud", "igv")):
        return SECTOR_ETF_MAP["saas"]
    if any(kw in text_lower for kw in ("china", "chinese", "kweb", "alibaba", "tencent")):
        return SECTOR_ETF_MAP["china_tech"]
    if any(kw in text_lower for kw in ("biotech", "pharma", "fda")):
        return SECTOR_ETF_MAP["biotech"]
    if any(kw in text_lower for kw in ("gold", "miner", "gld")):
        return SECTOR_ETF_MAP["gold"]
    return SECTOR_ETF_MAP["default"]


class IngestionEngine:
    """Ingests content from all platform accounts and extracts stock signals."""

    def __init__(
        self,
        session: AsyncSession,
        twitter_scraper: TwitterScraper,
        substack_scraper: SubstackScraper | None = None,
    ):
        self.session = session
        self.twitter = twitter_scraper
        self.substack = substack_scraper

    async def run_ingestion_cycle(self) -> int:
        """Ingest new content from all active KOLs' platform accounts."""
        stmt = (
            select(PlatformAccount, KOL)
            .join(KOL, PlatformAccount.kol_id == KOL.id)
            .where(
                KOL.is_active.is_(True),
                PlatformAccount.is_content_source.is_(True),
                PlatformAccount.is_active.is_(True),
            )
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        total_new = 0
        for account, kol in rows:
            try:
                if account.platform == Platform.TWITTER:
                    count = await self._ingest_twitter(account, kol)
                elif account.platform == Platform.SUBSTACK and self.substack:
                    count = await self._ingest_substack(account, kol)
                else:
                    count = 0
                total_new += count
            except Exception:
                logger.exception(f"Ingestion failed for {kol} via {account}")

        logger.info(f"Ingestion cycle: {total_new} new items from {len(rows)} accounts")
        return total_new

    async def _check_dedup(self, kol_id: int, ticker: str, published_at: dt.datetime) -> bool:
        """Check if this KOL already called this ticker within 14 days. Returns True if duplicate."""
        cutoff = published_at - dt.timedelta(days=DEDUP_WINDOW_DAYS)
        stmt = select(StockCall).where(
            and_(
                StockCall.kol_id == kol_id,
                StockCall.ticker == ticker,
                StockCall.called_at >= cutoff,
                StockCall.called_at < published_at,
                StockCall.is_duplicate.is_(False),
            )
        ).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _create_stock_call(
        self,
        kol: KOL,
        content: Content,
        ticker: str,
        sentiment: float,
        confidence: float,
        conviction: ConvictionLevel,
        published_at: dt.datetime,
    ) -> StockCall | None:
        """Create a StockCall with dedup check and originality tracking."""
        is_dup = await self._check_dedup(kol.id, ticker, published_at)

        direction = (
            CallDirection.BULLISH if sentiment > 0.2
            else CallDirection.BEARISH if sentiment < -0.2
            else CallDirection.NEUTRAL
        )

        # Check originality across ALL KOLs
        first_call_stmt = (
            select(StockCall)
            .where(
                StockCall.ticker == ticker,
                StockCall.called_at >= published_at - dt.timedelta(days=7),
                StockCall.called_at < published_at,
                StockCall.is_duplicate.is_(False),
            )
            .order_by(StockCall.called_at.asc())
            .limit(1)
        )
        first_result = await self.session.execute(first_call_stmt)
        first = first_result.scalar_one_or_none()

        # Guess sector benchmark
        benchmark_ticker = _guess_sector_etf(ticker, content.text or "")

        call = StockCall(
            kol_id=kol.id,
            content_id=content.id,
            ticker=ticker,
            direction=direction,
            conviction=conviction,
            confidence=confidence,
            called_at=published_at,
            benchmark_ticker=benchmark_ticker,
            is_first_caller=(first is None and not is_dup),
            hours_after_first=(
                (published_at - first.called_at).total_seconds() / 3600
                if first else 0.0
            ),
            is_duplicate=is_dup,
        )
        self.session.add(call)
        return call

    async def _ingest_twitter(self, account: PlatformAccount, kol: KOL) -> int:
        """Ingest tweets from a Twitter account."""
        tweets = await self.twitter.get_user_tweets(account.username)
        new_count = 0

        for tweet in tweets:
            existing = await self.session.execute(
                select(Content).where(
                    Content.platform == Platform.TWITTER.value,
                    Content.platform_content_id == tweet["id"],
                )
            )
            if existing.scalar_one_or_none():
                continue

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
                url=f"https://x.com/{account.username}/status/{tweet['id']}",
                published_at=published_at,
                likes=tweet.get("likes", 0),
                reposts=tweet.get("retweets", 0),
                replies=tweet.get("replies", 0),
                views=tweet.get("views", 0),
            )
            self.session.add(content)
            await self.session.flush()

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

                if content_type in (ContentType.ORIGINAL, ContentType.QUOTE):
                    await self._create_stock_call(
                        kol, content, ticker, sentiment, 0.6,
                        ConvictionLevel.LOW, published_at,
                    )

            new_count += 1

        await self.session.commit()
        return new_count

    async def _ingest_substack(self, account: PlatformAccount, kol: KOL) -> int:
        """Ingest articles from a Substack newsletter.

        Substack articles are long-form — they go through LLM analysis
        for high-conviction call extraction (when LLM is configured).
        """
        if not account.profile_url:
            return 0

        posts = await self.substack.get_newsletter_posts(account.profile_url)
        new_count = 0

        for post in posts:
            post_id = post.get("url", post.get("title", ""))
            if not post_id:
                continue

            existing = await self.session.execute(
                select(Content).where(
                    Content.platform == Platform.SUBSTACK.value,
                    Content.platform_content_id == post_id,
                )
            )
            if existing.scalar_one_or_none():
                continue

            full_text = f"{post.get('title', '')} {post.get('text', '')}"
            if not is_stock_related(full_text):
                continue

            published_at = dt.datetime.now(dt.timezone.utc)  # RSS dates need parsing

            content = Content(
                kol_id=kol.id,
                platform=Platform.SUBSTACK.value,
                platform_content_id=post_id,
                content_type=ContentType.ARTICLE,
                text=full_text[:10000],  # Cap stored text
                url=post.get("url"),
                published_at=published_at,
            )
            self.session.add(content)
            await self.session.flush()

            # For Substack articles, extract tickers with higher confidence
            # (articles are more thoughtful than tweets)
            tickers = extract_tickers(full_text)
            for ticker in tickers:
                sentiment = simple_sentiment(full_text)
                mention = TickerMention(
                    content_id=content.id,
                    ticker=ticker,
                    sentiment=sentiment,
                    confidence=0.7,
                )
                self.session.add(mention)

                await self._create_stock_call(
                    kol, content, ticker, sentiment, 0.7,
                    ConvictionLevel.LOW, published_at,  # Upgraded to HIGH by LLM if available
                )

            new_count += 1

        await self.session.commit()
        return new_count
