"""Tests for content analysis utilities."""

from skynet.utils.content_analyzer import extract_tickers, is_stock_related, simple_sentiment


def test_extract_tickers():
    text = "Looking at $NVDA and $AMD for semiconductor plays, $TSMC too"
    tickers = extract_tickers(text)
    assert "NVDA" in tickers
    assert "AMD" in tickers
    assert "TSMC" in tickers


def test_extract_tickers_filters_false_positives():
    text = "I have $USD in my account and $BTC in my wallet"
    tickers = extract_tickers(text)
    assert "USD" not in tickers
    assert "BTC" not in tickers


def test_is_stock_related():
    assert is_stock_related("$NVDA is bullish, great semiconductor play")
    assert is_stock_related("Market cap of this company is undervalued")
    assert is_stock_related("Earnings beat expectations, stock is up")
    assert not is_stock_related("Had a great lunch today")
    assert not is_stock_related("")


def test_simple_sentiment():
    bullish = simple_sentiment("Very bullish on this stock, strong buy signal")
    assert bullish > 0

    bearish = simple_sentiment("Bearish outlook, I would sell and avoid this risk")
    assert bearish < 0

    neutral = simple_sentiment("The weather is nice today")
    assert neutral == 0.0
