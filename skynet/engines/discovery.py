"""KOL Discovery Engine - automatically expands the tracking network.

The discovery engine crawls the social graph starting from seed accounts.
When a tracked KOL interacts with (retweets, quotes, mentions) another account
that posts stock-related content, that account is added to the tracking network.

Discovery rules:
1. Follow retweet/quote chains from tracked KOLs
2. Check if the discovered account posts stock-related content
3. Apply minimum engagement threshold
4. Respect max discovery depth (hops from seed)
5. Record the social graph edge for scoring
"""

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from skynet.models.kol import KOL, KOLRelation, Platform
from skynet.scrapers.twitter import TwitterScraper
from skynet.utils.config import get_settings
from skynet.utils.content_analyzer import is_stock_related

logger = logging.getLogger(__name__)


class DiscoveryEngine:
    """Discovers new KOLs by crawling social graphs of tracked accounts."""

    def __init__(self, session: AsyncSession, twitter_scraper: TwitterScraper):
        self.session = session
        self.twitter = twitter_scraper
        self.settings = get_settings()

    async def run_discovery_cycle(self) -> list[KOL]:
        """Run one full discovery cycle across all active KOLs."""
        discovered = []

        # Get all active KOLs within discovery depth
        stmt = select(KOL).where(
            KOL.is_active.is_(True),
            KOL.discovery_depth < self.settings.discovery.max_discovery_depth,
        )
        result = await self.session.execute(stmt)
        active_kols = result.scalars().all()

        logger.info(f"Running discovery cycle for {len(active_kols)} active KOLs")

        for kol in active_kols:
            try:
                new_kols = await self._discover_from_kol(kol)
                discovered.extend(new_kols)
            except Exception:
                logger.exception(f"Discovery failed for {kol}")

        logger.info(f"Discovery cycle complete: found {len(discovered)} new KOLs")
        return discovered

    async def _discover_from_kol(self, kol: KOL) -> list[KOL]:
        """Discover new KOLs from a single tracked KOL's interactions."""
        discovered = []

        if kol.platform != Platform.TWITTER:
            return discovered

        # Get recent interactions (retweets, quotes, mentions)
        interactions = await self.twitter.get_recent_interactions(kol.platform_user_id)

        for interaction in interactions:
            target_user_id = interaction["user_id"]
            target_username = interaction["username"]
            relation_type = interaction["type"]  # retweet, quote, mention

            # Skip if already tracked
            existing = await self.session.execute(
                select(KOL).where(
                    KOL.platform == Platform.TWITTER,
                    KOL.platform_user_id == target_user_id,
                )
            )
            if existing.scalar_one_or_none():
                # Still record/update the relation
                await self._upsert_relation(kol.id, existing.scalar_one_or_none(), relation_type)
                continue

            # Check if this person posts stock-related content
            user_info = await self.twitter.get_user_info(target_user_id)
            recent_tweets = await self.twitter.get_user_tweets(target_user_id, max_results=20)

            stock_tweet_count = sum(1 for t in recent_tweets if is_stock_related(t["text"]))
            stock_ratio = stock_tweet_count / max(len(recent_tweets), 1)

            # Must have at least 30% stock-related content and meet engagement threshold
            if stock_ratio < 0.3:
                logger.debug(f"Skipping @{target_username}: stock ratio {stock_ratio:.0%}")
                continue

            if user_info.get("followers_count", 0) < self.settings.discovery.min_engagement_threshold:
                logger.debug(f"Skipping @{target_username}: low followers")
                continue

            # Add to tracking network
            new_kol = KOL(
                platform=Platform.TWITTER,
                platform_user_id=target_user_id,
                username=target_username,
                display_name=user_info.get("name"),
                bio=user_info.get("description"),
                followers_count=user_info.get("followers_count", 0),
                discovered_via_kol_id=kol.id,
                discovery_depth=kol.discovery_depth + 1,
            )
            self.session.add(new_kol)
            await self.session.flush()

            await self._upsert_relation(kol.id, new_kol, relation_type)
            discovered.append(new_kol)
            logger.info(
                f"Discovered @{target_username} via @{kol.username} "
                f"(depth={new_kol.discovery_depth}, stock_ratio={stock_ratio:.0%})"
            )

        await self.session.commit()
        return discovered

    async def _upsert_relation(self, source_kol_id: int, target_kol: KOL, relation_type: str):
        """Create or update a social graph edge."""
        existing = await self.session.execute(
            select(KOLRelation).where(
                KOLRelation.source_kol_id == source_kol_id,
                KOLRelation.target_kol_id == target_kol.id,
                KOLRelation.relation_type == relation_type,
            )
        )
        rel = existing.scalar_one_or_none()
        if rel:
            rel.weight += 1.0
            rel.last_seen_at = dt.datetime.now(dt.timezone.utc)
        else:
            rel = KOLRelation(
                source_kol_id=source_kol_id,
                target_kol_id=target_kol.id,
                relation_type=relation_type,
                weight=1.0,
                last_seen_at=dt.datetime.now(dt.timezone.utc),
            )
            self.session.add(rel)

    async def seed_initial_kols(self, platform: Platform, usernames: list[str]) -> list[KOL]:
        """Add seed KOLs to bootstrap the discovery network."""
        seeded = []
        for username in usernames:
            existing = await self.session.execute(
                select(KOL).where(KOL.platform == platform, KOL.username == username)
            )
            if existing.scalar_one_or_none():
                continue

            if platform == Platform.TWITTER:
                user_info = await self.twitter.get_user_by_username(username)
                if not user_info:
                    logger.warning(f"Could not find Twitter user @{username}")
                    continue

                kol = KOL(
                    platform=Platform.TWITTER,
                    platform_user_id=user_info["id"],
                    username=username,
                    display_name=user_info.get("name"),
                    bio=user_info.get("description"),
                    followers_count=user_info.get("followers_count", 0),
                    discovery_depth=0,
                )
            else:
                kol = KOL(
                    platform=platform,
                    platform_user_id=username,
                    username=username,
                    discovery_depth=0,
                )

            self.session.add(kol)
            seeded.append(kol)

        await self.session.commit()
        logger.info(f"Seeded {len(seeded)} KOLs on {platform.value}")
        return seeded
