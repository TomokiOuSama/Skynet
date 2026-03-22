"""FastAPI application - REST API for the KOL tracking system."""

from fastapi import FastAPI, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from skynet.models.kol import KOL, KOLType, PlatformAccount
from skynet.models.score import KOLScore, ScoreSnapshot
from skynet.models.stock import StockCall
from skynet.utils.db import get_session, init_db

app = FastAPI(title="Skynet KOL Tracker", version="0.2.0")


@app.on_event("startup")
async def startup():
    await init_db()


@app.get("/api/kols")
async def list_kols(
    session: AsyncSession = Depends(get_session),
    sort_by: str = Query(
        "composite_score",
        enum=["composite_score", "alpha_score", "originality_score", "accuracy_score",
              "social_score", "median_alpha_60d", "win_rate_60d"],
    ),
    kol_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List all tracked KOLs with their scores, sorted by chosen metric."""
    stmt = (
        select(KOL, KOLScore)
        .outerjoin(KOLScore, KOL.id == KOLScore.kol_id)
        .where(KOL.is_active.is_(True))
        .order_by(desc(getattr(KOLScore, sort_by, KOLScore.composite_score)))
        .offset(offset)
        .limit(limit)
    )
    if kol_type:
        stmt = stmt.where(KOL.kol_type == KOLType(kol_type))

    result = await session.execute(stmt)
    rows = result.all()

    # Get platform accounts for each KOL
    kol_ids = [kol.id for kol, _ in rows]
    accounts_result = await session.execute(
        select(PlatformAccount).where(PlatformAccount.kol_id.in_(kol_ids))
    )
    accounts_by_kol: dict[int, list] = {}
    for acc in accounts_result.scalars().all():
        accounts_by_kol.setdefault(acc.kol_id, []).append(acc)

    return [
        {
            "id": kol.id,
            "name": kol.name,
            "kol_type": kol.kol_type.value,
            "volume_tier": kol.volume_tier.value,
            "discovery_depth": kol.discovery_depth,
            "platforms": [
                {
                    "platform": acc.platform.value,
                    "username": acc.username,
                    "followers_count": acc.followers_count,
                }
                for acc in accounts_by_kol.get(kol.id, [])
            ],
            "scores": {
                "composite": score.composite_score if score else 0,
                "alpha": score.alpha_score if score else 0,
                "originality": score.originality_score if score else 0,
                "accuracy": score.accuracy_score if score else 0,
                "social": score.social_score if score else 0,
                "total_calls": score.total_calls if score else 0,
                "high_conviction_calls": score.high_conviction_calls if score else 0,
                "win_rate_60d": score.win_rate_60d if score else None,
                "median_return_60d": score.median_return_60d if score else None,
                "median_alpha_60d": score.median_alpha_60d if score else None,
                "median_alpha_180d": score.median_alpha_180d if score else None,
            },
        }
        for kol, score in rows
    ]


@app.get("/api/kols/{kol_id}")
async def get_kol(kol_id: int, session: AsyncSession = Depends(get_session)):
    """Get detailed info for a specific KOL including all platform accounts."""
    kol = await session.get(KOL, kol_id)
    if not kol:
        return {"error": "KOL not found"}, 404

    # Platform accounts
    accounts_result = await session.execute(
        select(PlatformAccount).where(PlatformAccount.kol_id == kol_id)
    )
    accounts = accounts_result.scalars().all()

    # Score
    score_result = await session.execute(
        select(KOLScore).where(KOLScore.kol_id == kol_id)
    )
    score = score_result.scalar_one_or_none()

    # Recent calls
    calls_result = await session.execute(
        select(StockCall)
        .where(StockCall.kol_id == kol_id, StockCall.is_duplicate.is_(False))
        .order_by(desc(StockCall.called_at))
        .limit(20)
    )
    calls = calls_result.scalars().all()

    # Score history
    history_result = await session.execute(
        select(ScoreSnapshot)
        .where(ScoreSnapshot.kol_id == kol_id)
        .order_by(desc(ScoreSnapshot.snapshot_at))
        .limit(30)
    )
    history = history_result.scalars().all()

    return {
        "id": kol.id,
        "name": kol.name,
        "bio": kol.bio,
        "kol_type": kol.kol_type.value,
        "volume_tier": kol.volume_tier.value,
        "discovery_depth": kol.discovery_depth,
        "discovered_via_kol_id": kol.discovered_via_kol_id,
        "platforms": [
            {
                "platform": acc.platform.value,
                "username": acc.username,
                "display_name": acc.display_name,
                "profile_url": acc.profile_url,
                "followers_count": acc.followers_count,
                "is_discovery_source": acc.is_discovery_source,
                "is_content_source": acc.is_content_source,
            }
            for acc in accounts
        ],
        "scores": {
            "composite": score.composite_score,
            "alpha": score.alpha_score,
            "originality": score.originality_score,
            "accuracy": score.accuracy_score,
            "social": score.social_score,
            "total_calls": score.total_calls,
            "high_conviction_calls": score.high_conviction_calls,
            "median_return_30d": score.median_return_30d,
            "median_return_60d": score.median_return_60d,
            "median_return_180d": score.median_return_180d,
            "median_alpha_30d": score.median_alpha_30d,
            "median_alpha_60d": score.median_alpha_60d,
            "median_alpha_180d": score.median_alpha_180d,
            "win_rate_30d": score.win_rate_30d,
            "win_rate_60d": score.win_rate_60d,
        } if score else None,
        "recent_calls": [
            {
                "ticker": c.ticker,
                "direction": c.direction.value,
                "conviction": c.conviction.value,
                "called_at": c.called_at.isoformat(),
                "benchmark_ticker": c.benchmark_ticker,
                "is_first_caller": c.is_first_caller,
                "return_30d": c.return_30d,
                "return_60d": c.return_60d,
                "return_180d": c.return_180d,
                "alpha_30d": c.alpha_30d,
                "alpha_60d": c.alpha_60d,
                "alpha_180d": c.alpha_180d,
            }
            for c in calls
        ],
        "score_history": [
            {
                "date": s.snapshot_at.isoformat(),
                "composite": s.composite_score,
                "alpha": s.alpha_score,
                "originality": s.originality_score,
                "accuracy": s.accuracy_score,
                "social": s.social_score,
            }
            for s in history
        ],
    }


@app.get("/api/calls/recent")
async def recent_calls(
    session: AsyncSession = Depends(get_session),
    ticker: str | None = Query(None),
    conviction: str | None = Query(None, enum=["high", "low"]),
    limit: int = Query(50, ge=1, le=200),
):
    """Get recent stock calls, optionally filtered by ticker or conviction."""
    stmt = (
        select(StockCall, KOL)
        .join(KOL, StockCall.kol_id == KOL.id)
        .where(StockCall.is_duplicate.is_(False))
        .order_by(desc(StockCall.called_at))
        .limit(limit)
    )
    if ticker:
        stmt = stmt.where(StockCall.ticker == ticker.upper())
    if conviction:
        from skynet.models.stock import ConvictionLevel
        stmt = stmt.where(StockCall.conviction == ConvictionLevel(conviction))

    result = await session.execute(stmt)
    rows = result.all()

    return [
        {
            "kol": {"id": kol.id, "name": kol.name, "kol_type": kol.kol_type.value},
            "ticker": call.ticker,
            "direction": call.direction.value,
            "conviction": call.conviction.value,
            "called_at": call.called_at.isoformat(),
            "benchmark_ticker": call.benchmark_ticker,
            "is_first_caller": call.is_first_caller,
            "hours_after_first": call.hours_after_first,
            "return_60d": call.return_60d,
            "alpha_60d": call.alpha_60d,
        }
        for call, kol in rows
    ]


@app.get("/api/network")
async def network_graph(session: AsyncSession = Depends(get_session)):
    """Get the KOL social network graph for visualization."""
    from skynet.models.kol import KOLRelation

    kols_result = await session.execute(
        select(KOL, KOLScore)
        .outerjoin(KOLScore, KOL.id == KOLScore.kol_id)
        .where(KOL.is_active.is_(True))
    )
    kols = kols_result.all()

    rels_result = await session.execute(select(KOLRelation))
    relations = rels_result.scalars().all()

    return {
        "nodes": [
            {
                "id": kol.id,
                "name": kol.name,
                "kol_type": kol.kol_type.value,
                "score": score.composite_score if score else 0,
                "depth": kol.discovery_depth,
            }
            for kol, score in kols
        ],
        "edges": [
            {
                "source": rel.source_kol_id,
                "target": rel.target_kol_id,
                "type": rel.relation_type,
                "platform": rel.platform.value,
                "weight": rel.weight,
            }
            for rel in relations
        ],
    }


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "0.2.0"}
