"""KOL Discovery Engine - automatically expands the tracking network.

Multi-platform discovery strategy:
  - Twitter is the PRIMARY discovery source. We crawl retweet/quote/mention
    graphs from tracked KOLs' Twitter accounts to find new people.
  - Substack is a CONTENT source only. It has no social graph to crawl,
    but we ingest articles for deep analysis and stock call extraction.

When a new KOL is discovered via Twitter, we also try to find their
Substack (via bio links) and link it to the same identity node.
"""

import datetime as dt
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from skynet.models.kol import KOL, KOLRelation, KOLType, Platform, PlatformAccount
from skynet.scrapers.twitter import TwitterScraper
from skynet.utils.config import get_settings
from skynet.utils.content_analyzer import is_stock_related

logger = logging.getLogger(__name__)

# Pattern to find Substack URLs in Twitter bios
SUBSTACK_URL_PATTERN = re.compile(r"https?://([a-zA-Z0-9-]+)\.substack\.com")


class DiscoveryEngine:
    """Discovers new KOLs by crawling Twitter social graphs.

    Discovery only happens via Twitter. Substack has no social graph
    to crawl — it's used purely for content ingestion.
    """

    def __init__(self, session: AsyncSession, twitter_scraper: TwitterScraper):
        self.session = session
        self.twitter = twitter_scraper
        self.settings = get_settings()

    async def run_discovery_cycle(self) -> list[KOL]:
        """Run one full discovery cycle across all active KOLs' Twitter accounts."""
        discovered = []

        # Get Twitter accounts of active KOLs within discovery depth
        stmt = (
            select(PlatformAccount, KOL)
            .join(KOL, PlatformAccount.kol_id == KOL.id)
            .where(
                KOL.is_active.is_(True),
                KOL.discovery_depth < self.settings.discovery.max_discovery_depth,
                PlatformAccount.platform == Platform.TWITTER,
                PlatformAccount.is_discovery_source.is_(True),
            )
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        logger.info(f"Running discovery cycle for {len(rows)} Twitter accounts")

        for account, kol in rows:
            try:
                new_kols = await self._discover_from_twitter(account, kol)
                discovered.extend(new_kols)
            except Exception:
                logger.exception(f"Discovery failed for {kol} via {account}")

        logger.info(f"Discovery cycle complete: found {len(discovered)} new KOLs")
        return discovered

    async def _discover_from_twitter(
        self, account: PlatformAccount, kol: KOL
    ) -> list[KOL]:
        """Discover new KOLs from a Twitter account's interactions."""
        discovered = []

        interactions = await self.twitter.get_recent_interactions(account.platform_user_id)

        for interaction in interactions:
            target_user_id = interaction["user_id"]
            target_username = interaction["username"]
            relation_type = interaction["type"]

            # Check if this Twitter account is already linked to any KOL
            existing_account = await self.session.execute(
                select(PlatformAccount).where(
                    PlatformAccount.platform == Platform.TWITTER,
                    PlatformAccount.platform_user_id == target_user_id,
                )
            )
            existing = existing_account.scalar_one_or_none()
            if existing:
                # Update relation weight between the two KOLs
                await self._upsert_relation(
                    kol.id, existing.kol_id, relation_type, Platform.TWITTER
                )
                continue

            # Check if this person posts stock-related content
            user_info = await self.twitter.get_user_info(target_user_id)
            recent_tweets = await self.twitter.get_user_tweets(
                target_user_id, max_results=20
            )

            stock_tweet_count = sum(
                1 for t in recent_tweets if is_stock_related(t["text"])
            )
            stock_ratio = stock_tweet_count / max(len(recent_tweets), 1)

            if stock_ratio < 0.3:
                logger.debug(f"Skipping @{target_username}: stock ratio {stock_ratio:.0%}")
                continue

            followers = user_info.get("followers_count", 0)
            if followers < self.settings.discovery.min_engagement_threshold:
                logger.debug(f"Skipping @{target_username}: low followers ({followers})")
                continue

            # Create new KOL identity node
            new_kol = KOL(
                name=user_info.get("name", target_username),
                bio=user_info.get("description"),
                kol_type=KOLType.UNCLASSIFIED,
                discovered_via_kol_id=kol.id,
                discovery_depth=kol.discovery_depth + 1,
            )
            self.session.add(new_kol)
            await self.session.flush()

            # Link Twitter account to the new KOL
            twitter_account = PlatformAccount(
                kol_id=new_kol.id,
                platform=Platform.TWITTER,
                platform_user_id=target_user_id,
                username=target_username,
                display_name=user_info.get("name"),
                followers_count=followers,
                is_discovery_source=True,   # Twitter = discovery
                is_content_source=True,     # Also ingest tweets
            )
            self.session.add(twitter_account)

            # Try to find Substack from Twitter bio
            bio = user_info.get("description", "")
            substack_match = SUBSTACK_URL_PATTERN.search(bio)
            if substack_match:
                substack_slug = substack_match.group(1)
                substack_account = PlatformAccount(
                    kol_id=new_kol.id,
                    platform=Platform.SUBSTACK,
                    platform_user_id=substack_slug,
                    username=substack_slug,
                    profile_url=f"https://{substack_slug}.substack.com",
                    is_discovery_source=False,  # Substack has no social graph
                    is_content_source=True,     # Substack = deep content
                )
                self.session.add(substack_account)
                logger.info(
                    f"Also linked Substack '{substack_slug}' to @{target_username}"
                )

            # Record the social graph edge
            await self._upsert_relation(
                kol.id, new_kol.id, relation_type, Platform.TWITTER
            )

            discovered.append(new_kol)
            logger.info(
                f"Discovered @{target_username} via @{account.username} "
                f"(depth={new_kol.discovery_depth}, stock_ratio={stock_ratio:.0%})"
            )

        await self.session.commit()
        return discovered

    async def _upsert_relation(
        self,
        source_kol_id: int,
        target_kol_id: int,
        relation_type: str,
        platform: Platform,
    ):
        """Create or update a social graph edge between two KOL identity nodes."""
        existing = await self.session.execute(
            select(KOLRelation).where(
                KOLRelation.source_kol_id == source_kol_id,
                KOLRelation.target_kol_id == target_kol_id,
                KOLRelation.relation_type == relation_type,
                KOLRelation.platform == platform,
            )
        )
        rel = existing.scalar_one_or_none()
        if rel:
            rel.weight += 1.0
            rel.last_seen_at = dt.datetime.now(dt.timezone.utc)
        else:
            rel = KOLRelation(
                source_kol_id=source_kol_id,
                target_kol_id=target_kol_id,
                relation_type=relation_type,
                platform=platform,
                weight=1.0,
                last_seen_at=dt.datetime.now(dt.timezone.utc),
            )
            self.session.add(rel)

    async def seed_kol(
        self,
        name: str,
        twitter_username: str | None = None,
        substack_slug: str | None = None,
        kol_type: KOLType = KOLType.UNCLASSIFIED,
    ) -> KOL:
        """Seed a KOL with one or more platform accounts.

        Example::

            await discovery.seed_kol(
                name="SemiAnalysis",
                twitter_username="SemiAnalysis",
                substack_slug="semianalysis",
                kol_type=KOLType.STOCK_PICKER,
            )
        """
        kol = KOL(name=name, kol_type=kol_type, discovery_depth=0)
        self.session.add(kol)
        await self.session.flush()

        if twitter_username:
            user_info = await self.twitter.get_user_by_username(twitter_username)
            if user_info:
                account = PlatformAccount(
                    kol_id=kol.id,
                    platform=Platform.TWITTER,
                    platform_user_id=user_info["id"],
                    username=twitter_username,
                    display_name=user_info.get("name"),
                    followers_count=user_info.get("followers_count", 0),
                    is_discovery_source=True,
                    is_content_source=True,
                )
                self.session.add(account)
                kol.bio = kol.bio or user_info.get("description")

        if substack_slug:
            account = PlatformAccount(
                kol_id=kol.id,
                platform=Platform.SUBSTACK,
                platform_user_id=substack_slug,
                username=substack_slug,
                profile_url=f"https://{substack_slug}.substack.com",
                is_discovery_source=False,
                is_content_source=True,
            )
            self.session.add(account)

        await self.session.commit()
        logger.info(f"Seeded KOL: {kol}")
        return kol

    async def link_platform_account(
        self,
        kol_id: int,
        platform: Platform,
        username: str,
        platform_user_id: str | None = None,
    ) -> PlatformAccount:
        """Link a new platform account to an existing KOL identity."""
        account = PlatformAccount(
            kol_id=kol_id,
            platform=platform,
            platform_user_id=platform_user_id or username,
            username=username,
            is_discovery_source=(platform == Platform.TWITTER),
            is_content_source=True,
        )
        if platform == Platform.SUBSTACK:
            account.profile_url = f"https://{username}.substack.com"

        self.session.add(account)
        await self.session.commit()
        logger.info(f"Linked {platform.value}/@{username} to KOL #{kol_id}")
        return account
