"""Content analysis utilities - ticker extraction, stock relevance detection, sentiment."""

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
    "USE", "WAY", "WHO", "BOY", "DID", "GET", "HAS", "LET",
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
    """Rule-based sentiment for stock content. Returns -1.0 to 1.0.

    This is a fallback when LLM-based analysis is not available.
    """
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


async def analyze_content_with_llm(
    text: str, client: Any, model: str = "gpt-4o-mini"
) -> dict[str, Any]:
    """Use LLM to extract tickers, sentiment, and stock call direction.

    Returns:
        {
            "tickers": [{"symbol": "NVDA", "sentiment": 0.8, "confidence": 0.9}],
            "is_stock_related": True,
            "summary": "Bullish on NVDA due to AI demand"
        }
    """
    prompt = f"""Analyze this social media post about stocks. Extract:
1. Any stock ticker symbols mentioned (use standard US ticker format)
2. For each ticker, the sentiment (-1.0 bearish to 1.0 bullish) and confidence (0-1)
3. Whether this is genuinely about stock investing (not just casually mentioning a company)

Return JSON only:
{{"tickers": [{{"symbol": "XXXX", "sentiment": 0.0, "confidence": 0.0}}], "is_stock_related": true/false, "summary": "brief summary"}}

Post:
{text}"""

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        import json
        return json.loads(response.choices[0].message.content)
    except Exception:
        logger.exception("LLM content analysis failed, falling back to rule-based")
        tickers = extract_tickers(text)
        return {
            "tickers": [
                {"symbol": t, "sentiment": simple_sentiment(text), "confidence": 0.5}
                for t in tickers
            ],
            "is_stock_related": is_stock_related(text),
            "summary": "",
        }
