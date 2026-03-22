# Skynet KOL Tracker

Self-evolving tracking system for stock market KOLs (Key Opinion Leaders) focused on small/mid-cap equities, especially semiconductors.

## Core Capabilities

### 1. Network Discovery
Not a fixed watchlist — the system automatically expands by crawling Twitter social graphs. When a tracked KOL retweets or quotes someone who posts stock-related content, that person gets added to the tracking network. Substack accounts are auto-linked from Twitter bios.

### 2. Multi-Platform Identity
Each KOL is a platform-agnostic node with linked accounts:
- **Twitter**: Social graph exploration (discovery of new KOLs)
- **Substack**: Deep content analysis (stock call extraction, alpha scoring)

### 3. Dynamic Scoring
Every KOL gets a composite score based on four dimensions:

| Dimension | Weight | What It Measures |
|-----------|--------|-----------------|
| **Originality** | 35% | How often they're first to call a ticker |
| **Alpha** | 30% | Median alpha vs sector benchmark (SOXX, IGV, etc.) |
| **Win Rate** | 25% | % of calls directionally correct |
| **Social Authority** | 10% | Network position via PageRank on the social graph |

All scores use time decay — recent performance matters more. Returns measured at 7/30/60/90/180/360 day windows.

### 4. High-Conviction Extraction
LLM-based structured extraction with four conditions for HIGH conviction:
1. Clear directional conclusion (long/short)
2. ≥ 3 dedicated analysis sentences
3. ≥ 2 independent supporting arguments
4. ≥ 1 quantitative data point

## Architecture

```
skynet/
├── models/          # SQLAlchemy data models
│   ├── kol.py       # KOL identity + PlatformAccount + social graph
│   ├── content.py   # Ingested tweets/articles
│   ├── stock.py     # Stock calls + benchmark alpha + price cache
│   └── score.py     # Dynamic scores + median returns + history
├── engines/
│   ├── discovery.py # Twitter-based network expansion
│   ├── ingestion.py # Multi-platform content fetching + call extraction
│   └── stock_tracker.py  # Price data + benchmark ETF + alpha evaluation
├── scrapers/
│   ├── twitter.py   # Twitter API v2 client
│   └── substack.py  # RSS feed scraper
├── scoring/
│   └── scorer.py    # Alpha + originality + social + win rate scoring
├── api/
│   └── app.py       # FastAPI REST endpoints
├── utils/
│   ├── config.py    # TOML config loader
│   ├── db.py        # Async database session
│   └── content_analyzer.py  # Ticker extraction + sentiment + LLM prompt
└── orchestrator.py  # Main loop: discover → ingest → track → score
```

## Setup

```bash
# 1. Install dependencies
pip install -e .

# 2. Configure
cp config/settings.example.toml config/settings.toml
# Edit settings.toml with your API keys and seed KOLs

# 3. Run
python -m skynet
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/kols` | List KOLs ranked by score (sortable by alpha, originality, etc.) |
| `GET /api/kols/{id}` | KOL detail with platform accounts, calls, alpha history |
| `GET /api/calls/recent` | Recent stock calls, filterable by ticker and conviction |
| `GET /api/network` | Social graph data for visualization |

## Run Cycle

Every cycle (default: 24h), the system:
1. **Discovers** new KOLs via Twitter social graph crawling
2. **Ingests** content from Twitter tweets and Substack articles
3. **Tracks** stock prices + benchmark ETFs, evaluates alpha at all windows
4. **Scores** all KOLs with median alpha, win rate, originality, and social authority
