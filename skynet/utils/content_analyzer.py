"""Content analysis utilities - ticker extraction, stock relevance, sentiment, LLM extraction.

v2: Added high-conviction extraction prompt inspired by the Reddit post methodology.
Uses a structured two-phase extraction (candidate identification → conviction rating)
with four conditions for high-conviction classification.
"""

import json
import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Common stock ticker pattern ($AAPL, $NVDA, etc.)
CASHTAG_PATTERN = re.compile(r"\$([A-Z]{1,5})\b")

# Common non-ticker cashtags to ignore
FALSE_TICKERS = {
    "USD", "EUR", "GBP", "JPY", "BTC", "ETH", "SOL", "DOGE",
    "THE", "FOR", "AND", "NOT", "ALL", "ARE", "BUT", "CAN",
    "HAS", "HER", "HIM", "HIS", "HOW", "ITS", "MAY", "NEW",
    "NOW", "OLD", "OUR", "OUT", "OWN", "SAY", "SHE", "TOO",
    "USE", "WAY", "WHO", "BOY", "DID", "GET", "LET",
    "PUT", "RUN", "TOP", "YES",
}

# Keywords indicating stock-related content
STOCK_KEYWORDS = [
    r"\$[A-Z]{1,5}\b",
    r"\b(?:bull|bear)ish\b",
    r"\b(?:long|short)\s+(?:position|term)\b",
    r"\bDD\b",
    r"\bearnings?\b",
    r"\brevenue\b",
    r"\bmarket\s*cap\b",
    r"\bP/?E\s*ratio\b",
    r"\bsemiconductor[s]?\b",
    r"\bstock[s]?\b",
    r"\bticker[s]?\b",
    r"\bshares?\b",
    r"\bequit(?:y|ies)\b",
    r"\bvaluation\b",
    r"\bgrowth\s*stock\b",
    r"\bsmall[\s-]?cap\b",
    r"\bmid[\s-]?cap\b",
    r"\bundervalued\b",
    r"\bovervalued\b",
    r"\bprice\s*target\b",
    r"\bupside\b",
    r"\bdownside\b",
    r"\bcatalyst\b",
    r"\bfab\b",
    r"\bwafer\b",
    r"\bchip\s*(?:maker|design)\b",
    r"\bfoundry\b",
]

STOCK_PATTERN = re.compile("|".join(STOCK_KEYWORDS), re.IGNORECASE)


def extract_tickers(text: str) -> list[str]:
    """Extract stock ticker symbols from text."""
    matches = CASHTAG_PATTERN.findall(text)
    return [t for t in matches if t not in FALSE_TICKERS]


def is_stock_related(text: str) -> bool:
    """Check if text is related to stocks/investing."""
    if not text:
        return False
    return bool(STOCK_PATTERN.search(text))


def simple_sentiment(text: str) -> float:
    """Rule-based sentiment for stock content. Returns -1.0 to 1.0."""
    text_lower = text.lower()

    bullish_words = [
        "bullish", "buy", "long", "upside", "undervalued", "moon",
        "rocket", "breakout", "catalyst", "accumulate", "strong buy",
        "price target", "growth", "beat", "outperform", "upgrade",
    ]
    bearish_words = [
        "bearish", "sell", "short", "downside", "overvalued", "dump",
        "crash", "breakdown", "risk", "avoid", "weak", "miss",
        "underperform", "downgrade", "bubble",
    ]

    bull_count = sum(1 for w in bullish_words if w in text_lower)
    bear_count = sum(1 for w in bearish_words if w in text_lower)

    total = bull_count + bear_count
    if total == 0:
        return 0.0
    return (bull_count - bear_count) / total


# ---------------------------------------------------------------------------
# LLM-based high-conviction extraction
# ---------------------------------------------------------------------------

HIGH_CONVICTION_PROMPT = """ROLE: You are a professional financial analyst. Deeply analyze the following newsletter article and extract investment-related information.

PHASE A - Candidate Identification
1. Only identify companies/tickers explicitly mentioned in the article
2. If a company name can't be uniquely mapped to a ticker (multi-listed or ambiguous), skip it - do not guess
3. Every candidate must have a supporting evidence sentence from the text

PHASE B - Classification & Conviction Rating
1. Assign direction: bullish / bearish / neutral
2. Conviction: only high or low
3. HIGH conviction requires ALL FOUR conditions simultaneously:
   - Clear directional conclusion (long/short), not just factual description
   - >= 3 dedicated analysis sentences about this specific ticker
   - >= 2 independent supporting arguments
   - >= 1 quantitative data point (valuation / growth / guidance / target / financials)
   -> If ANY condition is not met -> low
4. Neutral -> always low
5. Same ticker in both bullish and bearish -> neutral (low)

KEY RULES (Strict)
1. Determine article_type first, then extract tickers:
   - macro: macro/policy/rates/geopolitics focused, stocks as examples only
   - stock_analysis: core is company/stock analysis with investment conclusions
   - mixed: both macro and stock-level depth
2. If article_type = macro OR title contains weekly/update/roundup ->
   default ALL tickers to low; only upgrade to high if all 4 conditions met
3. Companies merely "cited as examples" in sector discussion -> low
4. If article is not investment-related -> return empty arrays

SELF-CHECK BEFORE OUTPUT
- All neutral entries must be low conviction
- No duplicate tickers
- No conflicting directions for the same ticker
- Every high-conviction call must satisfy all four conditions

Return valid JSON only:
{
  "article_type": "stock_analysis|macro|mixed",
  "tickers": [
    {
      "symbol": "NVDA",
      "direction": "bullish",
      "conviction": "high",
      "sentiment": 0.8,
      "evidence": "brief supporting quote from article"
    }
  ]
}

ARTICLE:
"""


async def extract_high_conviction_calls(
    text: str, client: Any, model: str = "gpt-4o-mini"
) -> dict[str, Any]:
    """Use LLM with structured prompt to extract high-conviction stock calls.

    This uses the four-condition framework from the Reddit methodology:
    1. Clear directional conclusion
    2. >= 3 dedicated analysis sentences
    3. >= 2 independent supporting arguments
    4. >= 1 quantitative data point

    Returns:
        {
            "article_type": "stock_analysis",
            "tickers": [
                {
                    "symbol": "NVDA",
                    "direction": "bullish",
                    "conviction": "high",
                    "sentiment": 0.8,
                    "evidence": "..."
                }
            ]
        }
    """
    prompt = HIGH_CONVICTION_PROMPT + text[:8000]  # Cap input length

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        result = json.loads(response.choices[0].message.content)

        # Validate structure
        if "tickers" not in result:
            result["tickers"] = []
        for t in result["tickers"]:
            if t.get("conviction") not in ("high", "low"):
                t["conviction"] = "low"
            if t.get("direction") == "neutral":
                t["conviction"] = "low"

        return result

    except Exception:
        logger.exception("LLM high-conviction extraction failed, falling back to rule-based")
        tickers = extract_tickers(text)
        return {
            "article_type": "unknown",
            "tickers": [
                {
                    "symbol": t,
                    "direction": "bullish" if simple_sentiment(text) > 0.2
                    else "bearish" if simple_sentiment(text) < -0.2
                    else "neutral",
                    "conviction": "low",
                    "sentiment": simple_sentiment(text),
                    "evidence": "",
                }
                for t in tickers
            ],
        }


async def classify_kol_type(
    recent_texts: list[str], client: Any, model: str = "gpt-4o-mini"
) -> str:
    """Use LLM to classify a KOL as stock_picker, macro, or sector.

    Sends the last ~10 posts and asks the model to classify the author's style.
    """
    combined = "\n---\n".join(t[:500] for t in recent_texts[:10])

    prompt = f"""Analyze these recent posts from a financial newsletter author.
Classify their primary content type:
- "stock_picker": >50% of posts are thesis-driven calls on individual stocks
- "macro": >50% focus on ETFs, indices, rates, crypto, or geopolitics
- "sector": primarily sector/industry reviews without specific stock calls

Return JSON: {{"type": "stock_picker|macro|sector", "reasoning": "brief explanation"}}

Posts:
{combined}"""

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        result = json.loads(response.choices[0].message.content)
        return result.get("type", "stock_picker")
    except Exception:
        logger.exception("KOL classification failed")
        return "stock_picker"
