"""FastAPI application - REST API for the KOL tracking system."""

from fastapi import FastAPI, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from skynet.models.kol import KOL
from skynet.models.content import Content
from skynet.models.score import KOLScore, ScoreSnapshot
from skynet.models.stock import StockCall
from skynet.utils.db import get_session, init_db

app = FastAPI(title="Skynet KOL Tracker", version="0.1.0")


@app.on_event("startup")
async def startup():
    await init_db()


@app.get("/api/kols")
async def list_kols(
    session: AsyncSession = Depends(get_session),
    sort_by: str = Query("composite_score", enum=["composite_score", "originality_score", "accuracy_score", "social_score", "followers_count"]),
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
    result = await session.execute(stmt)
    rows = result.all()

    return [
        {
            "id": kol.id,
            "platform": kol.platform.value,
            "username": kol.username,
            "display_name": kol.display_name,
            "followers_count": kol.followers_count,
            "discovery_depth": kol.discovery_depth,
            "scores": {
                "composite": score.composite_score if score else 0,
                "originality": score.originality_score if score else 0,
                "accuracy": score.accuracy_score if score else 0,
                "social": score.social_score if score else 0,
                "total_calls": score.total_calls if score else 0,
                "accurate_calls": score.accurate_calls if score else 0,
                "first_caller_count": score.first_caller_count if score else 0,
            },
        }
        for kol, score in rows
    ]


@app.get("/api/kols/{kol_id}")
async def get_kol(kol_id: int, session: AsyncSession = Depends(get_session)):
    """Get detailed info for a specific KOL."""
    kol = await session.get(KOL, kol_id)
    if not kol:
        return {"error": "KOL not found"}, 404

    score_result = await session.execute(
        select(KOLScore).where(KOLScore.kol_id == kol_id)
    )
    score = score_result.scalar_one_or_none()

    # Recent calls
    calls_result = await session.execute(
        select(StockCall)
        .where(StockCall.kol_id == kol_id)
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
        "platform": kol.platform.value,
        "username": kol.username,
        "display_name": kol.display_name,
        "bio": kol.bio,
        "followers_count": kol.followers_count,
        "discovery_depth": kol.discovery_depth,
        "discovered_via_kol_id": kol.discovered_via_kol_id,
        "scores": {
            "composite": score.composite_score if score else 0,
            "originality": score.originality_score if score else 0,
            "accuracy": score.accuracy_score if score else 0,
            "social": score.social_score if score else 0,
        } if score else None,
        "recent_calls": [
            {
                "ticker": c.ticker,
                "direction": c.direction.value,
                "called_at": c.called_at.isoformat(),
                "is_first_caller": c.is_first_caller,
                "return_7d": c.return_7d,
                "return_30d": c.return_30d,
                "return_90d": c.return_90d,
            }
            for c in calls
        ],
        "score_history": [
            {
                "date": s.snapshot_at.isoformat(),
                "composite": s.composite_score,
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
    limit: int = Query(50, ge=1, le=200),
):
    """Get recent stock calls, optionally filtered by ticker."""
    stmt = (
        select(StockCall, KOL)
        .join(KOL, StockCall.kol_id == KOL.id)
        .order_by(desc(StockCall.called_at))
        .limit(limit)
    )
    if ticker:
        stmt = stmt.where(StockCall.ticker == ticker.upper())

    result = await session.execute(stmt)
    rows = result.all()

    return [
        {
            "kol": {"id": kol.id, "username": kol.username, "platform": kol.platform.value},
            "ticker": call.ticker,
            "direction": call.direction.value,
            "called_at": call.called_at.isoformat(),
            "is_first_caller": call.is_first_caller,
            "hours_after_first": call.hours_after_first,
            "return_7d": call.return_7d,
            "return_30d": call.return_30d,
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
                "username": kol.username,
                "platform": kol.platform.value,
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
                "weight": rel.weight,
            }
            for rel in relations
        ],
    }


@app.get("/api/health")
async def health():
    return {"status": "ok"}
