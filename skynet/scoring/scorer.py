"""Dynamic KOL Scoring System.

Computes three sub-scores for each KOL:

1. Originality Score (0-1):
   - Based on how often the KOL is the *first* to call a ticker
   - Weighted by time decay (recent calls matter more)

2. Accuracy Score (0-1):
   - Based on stock performance after the KOL's calls
   - A bullish call followed by price increase = accurate
   - Weighted by confidence and time window

3. Social Score (0-1):
   - Based on position in the KOL network graph
   - Uses PageRank-like algorithm on the social graph
   - Accounts with more high-quality inbound connections score higher

Final composite = w1*originality + w2*accuracy + w3*social
"""

import datetime as dt
import json
import logging
import math

import networkx as nx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from skynet.models.kol import KOL, KOLRelation
from skynet.models.score import KOLScore, ScoreSnapshot
from skynet.models.stock import CallDirection, StockCall
from skynet.utils.config import get_settings

logger = logging.getLogger(__name__)


class KOLScorer:
    """Computes and updates dynamic scores for all KOLs."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()
        self.scoring = self.settings.scoring

    async def score_all(self) -> dict[int, float]:
        """Recompute scores for all active KOLs. Returns {kol_id: composite_score}."""
        stmt = select(KOL).where(KOL.is_active.is_(True))
        result = await self.session.execute(stmt)
        kols = result.scalars().all()

        # Compute social scores using graph analysis
        social_scores = await self._compute_social_scores()

        scores = {}
        for kol in kols:
            originality = await self._compute_originality(kol.id)
            accuracy = await self._compute_accuracy(kol.id)
            social = social_scores.get(kol.id, 0.0)

            composite = (
                self.scoring.originality_weight * originality
                + self.scoring.accuracy_weight * accuracy
                + self.scoring.social_weight * social
            )

            # Upsert score record
            existing = await self.session.execute(
                select(KOLScore).where(KOLScore.kol_id == kol.id)
            )
            score_record = existing.scalar_one_or_none()

            # Count stats
            call_stats = await self._get_call_stats(kol.id)

            if score_record:
                score_record.originality_score = originality
                score_record.accuracy_score = accuracy
                score_record.social_score = social
                score_record.composite_score = composite
                score_record.total_calls = call_stats["total"]
                score_record.accurate_calls = call_stats["accurate"]
                score_record.first_caller_count = call_stats["first"]
            else:
                score_record = KOLScore(
                    kol_id=kol.id,
                    originality_score=originality,
                    accuracy_score=accuracy,
                    social_score=social,
                    composite_score=composite,
                    total_calls=call_stats["total"],
                    accurate_calls=call_stats["accurate"],
                    first_caller_count=call_stats["first"],
                )
                self.session.add(score_record)

            # Save snapshot
            snapshot = ScoreSnapshot(
                kol_id=kol.id,
                snapshot_at=dt.datetime.now(dt.timezone.utc),
                originality_score=originality,
                accuracy_score=accuracy,
                social_score=social,
                composite_score=composite,
                details_json=json.dumps(call_stats),
            )
            self.session.add(snapshot)

            scores[kol.id] = composite
            logger.debug(
                f"Scored {kol}: orig={originality:.2f} acc={accuracy:.2f} "
                f"soc={social:.2f} => {composite:.2f}"
            )

        await self.session.commit()
        logger.info(f"Scored {len(scores)} KOLs")
        return scores

    async def _compute_originality(self, kol_id: int) -> float:
        """Compute originality score based on first-caller frequency."""
        now = dt.datetime.now(dt.timezone.utc)
        half_life = self.scoring.score_decay_half_life

        stmt = select(StockCall).where(StockCall.kol_id == kol_id)
        result = await self.session.execute(stmt)
        calls = result.scalars().all()

        if not calls:
            return 0.0

        weighted_first = 0.0
        total_weight = 0.0

        for call in calls:
            # Time decay: recent calls weigh more
            days_ago = (now - call.called_at).total_seconds() / 86400
            decay = math.exp(-0.693 * days_ago / half_life)  # ln(2) ≈ 0.693

            total_weight += decay
            if call.is_first_caller:
                weighted_first += decay
            elif call.hours_after_first is not None:
                # Partial credit for being early (within 24h of first)
                if call.hours_after_first < 24:
                    early_bonus = max(0, 1 - call.hours_after_first / 24) * 0.5
                    weighted_first += decay * early_bonus

        return weighted_first / total_weight if total_weight > 0 else 0.0

    async def _compute_accuracy(self, kol_id: int) -> float:
        """Compute accuracy score based on call performance."""
        now = dt.datetime.now(dt.timezone.utc)
        half_life = self.scoring.score_decay_half_life

        stmt = select(StockCall).where(
            StockCall.kol_id == kol_id,
            StockCall.price_at_call.isnot(None),
        )
        result = await self.session.execute(stmt)
        calls = result.scalars().all()

        if not calls:
            return 0.0

        weighted_correct = 0.0
        total_weight = 0.0

        for call in calls:
            days_ago = (now - call.called_at).total_seconds() / 86400
            decay = math.exp(-0.693 * days_ago / half_life)

            # Check each time window
            for ret_attr in ("return_7d", "return_30d", "return_90d"):
                ret = getattr(call, ret_attr)
                if ret is None:
                    continue

                weight = decay * call.confidence
                total_weight += weight

                # Was the call directionally correct?
                if call.direction == CallDirection.BULLISH and ret > 0:
                    weighted_correct += weight * min(abs(ret), 1.0)
                elif call.direction == CallDirection.BEARISH and ret < 0:
                    weighted_correct += weight * min(abs(ret), 1.0)
                elif call.direction == CallDirection.NEUTRAL and abs(ret) < 0.05:
                    weighted_correct += weight * 0.5

        return weighted_correct / total_weight if total_weight > 0 else 0.0

    async def _compute_social_scores(self) -> dict[int, float]:
        """Compute social authority using PageRank on the KOL relation graph."""
        stmt = select(KOLRelation)
        result = await self.session.execute(stmt)
        relations = result.scalars().all()

        if not relations:
            return {}

        G = nx.DiGraph()
        for rel in relations:
            G.add_edge(
                rel.source_kol_id,
                rel.target_kol_id,
                weight=rel.weight,
                type=rel.relation_type,
            )

        if len(G.nodes) == 0:
            return {}

        # PageRank with edge weights
        try:
            pr = nx.pagerank(G, weight="weight", alpha=0.85)
        except nx.PowerIterationFailedConvergence:
            pr = {node: 1.0 / len(G.nodes) for node in G.nodes}

        # Normalize to 0-1
        max_pr = max(pr.values()) if pr else 1.0
        return {kol_id: score / max_pr for kol_id, score in pr.items()}

    async def _get_call_stats(self, kol_id: int) -> dict[str, int]:
        """Get basic call statistics for a KOL."""
        total_result = await self.session.execute(
            select(func.count()).select_from(StockCall).where(StockCall.kol_id == kol_id)
        )
        total = total_result.scalar() or 0

        first_result = await self.session.execute(
            select(func.count())
            .select_from(StockCall)
            .where(StockCall.kol_id == kol_id, StockCall.is_first_caller.is_(True))
        )
        first = first_result.scalar() or 0

        # Count accurate calls (directionally correct at any window)
        accurate = 0
        stmt = select(StockCall).where(
            StockCall.kol_id == kol_id,
            StockCall.price_at_call.isnot(None),
        )
        result = await self.session.execute(stmt)
        for call in result.scalars():
            for ret_attr in ("return_7d", "return_30d", "return_90d"):
                ret = getattr(call, ret_attr)
                if ret is None:
                    continue
                if (call.direction == CallDirection.BULLISH and ret > 0) or (
                    call.direction == CallDirection.BEARISH and ret < 0
                ):
                    accurate += 1
                    break

        return {"total": total, "accurate": accurate, "first": first}
