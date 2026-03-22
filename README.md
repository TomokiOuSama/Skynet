# Skynet KOL Tracker

Self-evolving tracking system for stock market KOLs (Key Opinion Leaders) focused on small/mid-cap equities, especially semiconductors.

## Core Capabilities

### 1. Network Discovery
Not a fixed watchlist — the system automatically expands by crawling social graphs. When a tracked KOL retweets or quotes someone who posts stock-related content, that person gets added to the tracking network.

### 2. Dynamic Scoring
Every KOL gets a composite score based on three dimensions:

| Dimension | Weight | What It Measures |
|-----------|--------|-----------------|
| **Originality** | 35% | How often they're first to call a ticker |
| **Accuracy** | 40% | Stock performance after their calls (7d/30d/90d) |
| **Social Authority** | 25% | Network position via PageRank on the social graph |

All scores use time decay — recent performance matters more.

### 3. Content Pipeline
- **Twitter/X**: Tweets, retweets, quotes with cashtag extraction
- **Reddit**: Hot posts from stock-related subreddits
- **Substack**: Newsletter articles via RSS

## Architecture

```
skynet/
├── models/          # SQLAlchemy data models
│   ├── kol.py       # KOL profiles + social graph edges
│   ├── content.py   # Ingested posts/tweets/articles
│   ├── stock.py     # Stock calls + price cache
│   └── score.py     # Dynamic scores + history
├── engines/
│   ├── discovery.py # Network expansion engine
│   ├── ingestion.py # Content fetching + ticker extraction
│   └── stock_tracker.py  # Price data + call evaluation
├── scrapers/
│   ├── twitter.py   # Twitter API v2 client
│   ├── reddit.py    # Reddit PRAW client
│   └── substack.py  # RSS feed scraper
├── scoring/
│   └── scorer.py    # Three-dimensional scoring system
├── api/
│   └── app.py       # FastAPI REST endpoints
├── utils/
│   ├── config.py    # TOML config loader
│   ├── db.py        # Async database session
│   └── content_analyzer.py  # Ticker extraction + sentiment
└── orchestrator.py  # Main loop: discover → ingest → track → score
```

## Setup

```bash
# 1. Install dependencies
pip install -e .

# 2. Configure
cp config/settings.example.toml config/settings.toml
# Edit settings.toml with your API keys

# 3. Run
python -m skynet
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/kols` | List KOLs ranked by score |
| `GET /api/kols/{id}` | KOL detail with call history |
| `GET /api/calls/recent` | Recent stock calls across all KOLs |
| `GET /api/network` | Social graph data for visualization |

## Run Cycle

Every cycle (default: 24h), the system:
1. **Discovers** new KOLs from tracked accounts' interactions
2. **Ingests** new content and extracts stock calls
3. **Tracks** stock prices and evaluates call accuracy
4. **Scores** all KOLs with time-decayed composite metrics
