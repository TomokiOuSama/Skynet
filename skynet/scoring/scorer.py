"""Dynamic KOL Scoring System v2.

Key improvements from Reddit post analysis:
1. Alpha vs benchmark replaces absolute returns as primary accuracy metric
2. Median returns instead of average (resistant to outlier winners)
3. Volume tier awareness (compare within tier)
4. Win rate tracking
5. Extended time horizons (up to 360d)
6. Only scores stock_picker KOLs (macro/sector excluded from stock-picking rankings)

Scoring dimensions:
  Originality (35%): First-caller frequency with time decay
  Alpha (30%):       Median alpha vs sector benchmark across time windows
  Win Rate (25%):    Directionally correct calls
  Social (10%):      PageRank on the KOL social graph
"""

import datetime as dt
import json
import logging
import math
import statistics

import networkx as nx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from skynet.models.kol import KOL, KOLRelation, KOLType, VolumeTier
from skynet.models.score import KOLScore, ScoreSnapshot
from skynet.models.stock import CallDirection, ConvictionLevel, StockCall
from skynet.utils.config import get_settings

logger = logging.getLogger(__name__)


def _classify_volume_tier(total_calls: int) -> VolumeTier:
    if total_calls >= 100:
        return VolumeTier.HIGH_VOLUME
    elif total_calls >= 30:
        return VolumeTier.MODERATE
    return VolumeTier.SELECTIVE


def _safe_median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


class KOLScorer:
    """Computes and updates dynamic scores for all stock-picker KOLs."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()
        self.scoring = self.settings.scoring

    async def score_all(self) -> dict[int, float]:
        """Recompute scores for all active stock-picker KOLs."""
        # Only score stock pickers (macro/sector KOLs need different methodology)
        stmt = select(KOL).where(
            KOL.is_active.is_(True),
            KOL.kol_type.in_([KOLType.STOCK_PICKER, KOLType.UNCLASSIFIED]),
        )
        result = await self.session.execute(stmt)
        kols = result.scalars().all()

        social_scores = await self._compute_social_scores()

        scores = {}
        for kol in kols:
            call_data = await self._get_call_data(kol.id)

            # Update volume tier
            kol.volume_tier = _classify_volume_tier(call_data["total"])

            originality = await self._compute_originality(kol.id)
            alpha = self._compute_alpha_score(call_data)
            accuracy = self._compute_win_rate_score(call_data)
            social = social_scores.get(kol.id, 0.0)

            composite = (
                0.35 * originality
                + 0.30 * alpha
                + 0.25 * accuracy
                + 0.10 * social
            )

            # Upsert score record
            existing = await self.session.execute(
                select(KOLScore).where(KOLScore.kol_id == kol.id)
            )
            score_record = existing.scalar_one_or_none()

            score_data = {
                "originality_score": originality,
                "accuracy_score": accuracy,
                "alpha_score": alpha,
                "social_score": social,
                "composite_score": composite,
                "total_calls": call_data["total"],
                "high_conviction_calls": call_data["high_conviction"],
                "accurate_calls": call_data["accurate"],
                "first_caller_count": call_data["first"],
                "median_return_30d": call_data["median_return_30d"],
                "median_return_60d": call_data["median_return_60d"],
                "median_return_180d": call_data["median_return_180d"],
                "median_alpha_30d": call_data["median_alpha_30d"],
                "median_alpha_60d": call_data["median_alpha_60d"],
                "median_alpha_180d": call_data["median_alpha_180d"],
                "win_rate_30d": call_data["win_rate_30d"],
                "win_rate_60d": call_data["win_rate_60d"],
            }

            if score_record:
                for k, v in score_data.items():
                    setattr(score_record, k, v)
            else:
                score_record = KOLScore(kol_id=kol.id, **score_data)
                self.session.add(score_record)

            # Snapshot
            snapshot = ScoreSnapshot(
                kol_id=kol.id,
                snapshot_at=dt.datetime.now(dt.timezone.utc),
                originality_score=originality,
                accuracy_score=accuracy,
                alpha_score=alpha,
                social_score=social,
                composite_score=composite,
                details_json=json.dumps({
                    k: v for k, v in call_data.items()
                    if not isinstance(v, list)
                }),
            )
            self.session.add(snapshot)

            scores[kol.id] = composite
            logger.debug(
                f"Scored {kol}: orig={originality:.2f} alpha={alpha:.2f} "
                f"acc={accuracy:.2f} soc={social:.2f} => {composite:.2f}"
            )

        await self.session.commit()
        logger.info(f"Scored {len(scores)} KOLs")
        return scores

    async def _get_call_data(self, kol_id: int) -> dict:
        """Gather all call statistics for a KOL."""
        stmt = select(StockCall).where(
            StockCall.kol_id == kol_id,
            StockCall.is_duplicate.is_(False),
        )
        result = await self.session.execute(stmt)
        calls = result.scalars().all()

        # Collect returns and alphas
        returns_30d = []
        returns_60d = []
        returns_180d = []
        alphas_30d = []
        alphas_60d = []
        alphas_180d = []
        all_alphas = []

        wins_30d = 0
        total_30d = 0
        wins_60d = 0
        total_60d = 0
        accurate = 0
        first = 0
        high_conviction = 0

        for call in calls:
            if call.conviction == ConvictionLevel.HIGH:
                high_conviction += 1
            if call.is_first_caller:
                first += 1

            # Collect returns per window
            for ret_attr, alpha_attr, ret_list, alpha_list, win_count_name, total_count_name in [
                ("return_30d", "alpha_30d", returns_30d, alphas_30d, "wins_30d", "total_30d"),
                ("return_60d", "alpha_60d", returns_60d, alphas_60d, "wins_60d", "total_60d"),
                ("return_180d", "alpha_180d", returns_180d, alphas_180d, None, None),
            ]:
                ret = getattr(call, ret_attr)
                alpha = getattr(call, alpha_attr)
                if ret is not None:
                    ret_list.append(ret)
                    if win_count_name:
                        locals_ref = locals()
                        locals_ref[total_count_name] = locals_ref.get(total_count_name, 0)
                if alpha is not None:
                    alpha_list.append(alpha)
                    all_alphas.append(alpha)

            # Win rate: was the call directionally correct at 30d?
            if call.return_30d is not None:
                total_30d += 1
                if (call.direction == CallDirection.BULLISH and call.return_30d > 0) or \
                   (call.direction == CallDirection.BEARISH and call.return_30d < 0):
                    wins_30d += 1

            if call.return_60d is not None:
                total_60d += 1
                if (call.direction == CallDirection.BULLISH and call.return_60d > 0) or \
                   (call.direction == CallDirection.BEARISH and call.return_60d < 0):
                    wins_60d += 1

            # Count accurate (directionally correct at any window)
            for ret_attr in ("return_7d", "return_30d", "return_60d", "return_90d"):
                ret = getattr(call, ret_attr)
                if ret is not None:
                    if (call.direction == CallDirection.BULLISH and ret > 0) or \
                       (call.direction == CallDirection.BEARISH and ret < 0):
                        accurate += 1
                        break

        return {
            "total": len(calls),
            "high_conviction": high_conviction,
            "accurate": accurate,
            "first": first,
            "all_alphas": all_alphas,
            "median_return_30d": _safe_median(returns_30d),
            "median_return_60d": _safe_median(returns_60d),
            "median_return_180d": _safe_median(returns_180d),
            "median_alpha_30d": _safe_median(alphas_30d),
            "median_alpha_60d": _safe_median(alphas_60d),
            "median_alpha_180d": _safe_median(alphas_180d),
            "win_rate_30d": (wins_30d / total_30d) if total_30d > 0 else None,
            "win_rate_60d": (wins_60d / total_60d) if total_60d > 0 else None,
        }

    def _compute_alpha_score(self, call_data: dict) -> float:
        """Score based on median alpha across time windows.

        Alpha > 0 means the KOL's picks beat their sector benchmark.
        Normalized to 0-1 scale where 0.5 = market-matching performance.
        """
        alphas = call_data["all_alphas"]
        if not alphas:
            return 0.0

        median_alpha = statistics.median(alphas)

        # Sigmoid-like normalization: 0% alpha → 0.5, +20% alpha → ~0.9
        # score = 1 / (1 + exp(-10 * median_alpha))
        try:
            score = 1.0 / (1.0 + math.exp(-10.0 * median_alpha))
        except OverflowError:
            score = 1.0 if median_alpha > 0 else 0.0

        return score

    def _compute_win_rate_score(self, call_data: dict) -> float:
        """Score based on win rate (% of calls directionally correct)."""
        win_rate = call_data.get("win_rate_60d") or call_data.get("win_rate_30d")
        if win_rate is None:
            return 0.0
        # 50% win rate = 0.0 score, 70% = 0.8, 80% = 1.0
        return min(1.0, max(0.0, (win_rate - 0.5) * 4.0))

    async def _compute_originality(self, kol_id: int) -> float:
        """Compute originality score based on first-caller frequency."""
        now = dt.datetime.now(dt.timezone.utc)
        half_life = self.scoring.score_decay_half_life

        stmt = select(StockCall).where(
            StockCall.kol_id == kol_id,
            StockCall.is_duplicate.is_(False),
        )
        result = await self.session.execute(stmt)
        calls = result.scalars().all()

        if not calls:
            return 0.0

        weighted_first = 0.0
        total_weight = 0.0

        for call in calls:
            days_ago = (now - call.called_at).total_seconds() / 86400
            decay = math.exp(-0.693 * days_ago / half_life)

            total_weight += decay
            if call.is_first_caller:
                weighted_first += decay
            elif call.hours_after_first is not None and call.hours_after_first < 24:
                early_bonus = max(0, 1 - call.hours_after_first / 24) * 0.5
                weighted_first += decay * early_bonus

        return weighted_first / total_weight if total_weight > 0 else 0.0

    async def _compute_social_scores(self) -> dict[int, float]:
        """Compute social authority using PageRank on the KOL relation graph."""
        stmt = select(KOLRelation)
        result = await self.session.execute(stmt)
        relations = result.scalars().all()

        if not relations:
            return {}

        G = nx.DiGraph()
        for rel in relations:
            if G.has_edge(rel.source_kol_id, rel.target_kol_id):
                G[rel.source_kol_id][rel.target_kol_id]["weight"] += rel.weight
            else:
                G.add_edge(
                    rel.source_kol_id, rel.target_kol_id, weight=rel.weight
                )

        if len(G.nodes) == 0:
            return {}

        try:
            pr = nx.pagerank(G, weight="weight", alpha=0.85)
        except nx.PowerIterationFailedConvergence:
            pr = {node: 1.0 / len(G.nodes) for node in G.nodes}

        max_pr = max(pr.values()) if pr else 1.0
        return {kol_id: score / max_pr for kol_id, score in pr.items()}
