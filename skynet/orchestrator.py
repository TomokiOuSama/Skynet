"""Main Orchestrator - coordinates all engines on a scheduled loop.

Run cycle:
1. Discovery: Expand KOL network via Twitter social graphs
2. Ingestion: Fetch content from all platforms (Twitter tweets, Substack articles)
3. Stock Tracking: Update prices + benchmark ETFs, evaluate call accuracy + alpha
4. Scoring: Recompute all KOL scores (median alpha, win rate, originality, social)
"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from skynet.engines.discovery import DiscoveryEngine
from skynet.engines.ingestion import IngestionEngine
from skynet.engines.stock_tracker import StockTracker
from skynet.models.kol import KOLType
from skynet.scoring.scorer import KOLScorer
from skynet.scrapers.substack import SubstackScraper
from skynet.scrapers.twitter import TwitterScraper
from skynet.utils.config import get_settings
from skynet.utils.db import get_session_factory, init_db

logger = logging.getLogger(__name__)


async def run_full_cycle():
    """Execute one full tracking cycle."""
    settings = get_settings()
    session_factory = get_session_factory()

    async with session_factory() as session:
        twitter = TwitterScraper()
        substack = SubstackScraper()

        # 1. Discovery (via Twitter social graphs only)
        logger.info("=== Phase 1: Discovery (Twitter) ===")
        discovery = DiscoveryEngine(session, twitter)
        new_kols = await discovery.run_discovery_cycle()
        logger.info(f"Discovered {len(new_kols)} new KOLs")

        # 2. Ingestion (from all platform accounts)
        logger.info("=== Phase 2: Ingestion (Twitter + Substack) ===")
        ingestion = IngestionEngine(session, twitter, substack)
        new_content = await ingestion.run_ingestion_cycle()
        logger.info(f"Ingested {new_content} new content items")

        # 3. Stock Tracking (with benchmark ETFs)
        logger.info("=== Phase 3: Stock Tracking + Alpha ===")
        tracker = StockTracker(session)
        tickers = await tracker.get_active_tickers()
        await tracker.update_price_cache(tickers)
        await tracker.backfill_call_prices()
        await tracker.evaluate_calls()
        logger.info(f"Tracked {len(tickers)} active tickers")

        # 4. Scoring (median alpha + win rate + originality + social)
        logger.info("=== Phase 4: Scoring ===")
        scorer = KOLScorer(session)
        scores = await scorer.score_all()
        logger.info(f"Scored {len(scores)} KOLs")

        await substack.close()

    logger.info("=== Full cycle complete ===")


async def seed_and_run():
    """Initialize database, seed KOLs, and run first cycle."""
    settings = get_settings()
    await init_db()

    session_factory = get_session_factory()
    async with session_factory() as session:
        twitter = TwitterScraper()
        discovery = DiscoveryEngine(session, twitter)

        # Seed KOLs with multi-platform accounts
        for seed in settings.seeds:
            await discovery.seed_kol(
                name=seed.name,
                twitter_username=seed.twitter,
                substack_slug=seed.substack,
                kol_type=KOLType(seed.kol_type) if seed.kol_type else KOLType.UNCLASSIFIED,
            )

    await run_full_cycle()


def start_scheduler():
    """Start the periodic scheduler."""
    settings = get_settings()
    scheduler = AsyncIOScheduler()

    interval_hours = settings.discovery.discovery_interval_hours
    scheduler.add_job(run_full_cycle, "interval", hours=interval_hours)

    scheduler.start()
    logger.info(f"Scheduler started: running every {interval_hours} hours")
    return scheduler


async def main():
    """Entry point: seed, run once, then schedule."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    await seed_and_run()

    scheduler = start_scheduler()

    import uvicorn
    from skynet.api.app import app

    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
