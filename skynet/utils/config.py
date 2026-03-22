"""Configuration loader from TOML settings file."""

import tomllib
from pathlib import Path
from pydantic import BaseModel
from pydantic_settings import BaseSettings


class TwitterConfig(BaseModel):
    bearer_token: str = ""
    seed_accounts: list[str] = []


class RedditConfig(BaseModel):
    client_id: str = ""
    client_secret: str = ""
    user_agent: str = "skynet-kol-tracker/0.1"
    subreddits: list[str] = ["semiconductors", "stocks"]


class SubstackConfig(BaseModel):
    seed_newsletters: list[str] = []


class StockConfig(BaseModel):
    alpha_vantage_key: str = ""
    focus_sectors: list[str] = ["semiconductors", "technology"]
    max_market_cap: int = 10000
    min_market_cap: int = 100


class ScoringConfig(BaseModel):
    originality_weight: float = 0.35
    accuracy_weight: float = 0.40
    social_weight: float = 0.25
    accuracy_window_days: list[int] = [7, 30, 90]
    score_decay_half_life: int = 60


class DiscoveryConfig(BaseModel):
    max_discovery_depth: int = 3
    min_engagement_threshold: int = 100
    discovery_interval_hours: int = 24


class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: str = ""


class DatabaseConfig(BaseModel):
    url: str = "sqlite+aiosqlite:///skynet.db"


class Settings(BaseSettings):
    twitter: TwitterConfig = TwitterConfig()
    reddit: RedditConfig = RedditConfig()
    substack: SubstackConfig = SubstackConfig()
    stock: StockConfig = StockConfig()
    scoring: ScoringConfig = ScoringConfig()
    discovery: DiscoveryConfig = DiscoveryConfig()
    llm: LLMConfig = LLMConfig()
    database: DatabaseConfig = DatabaseConfig()

    @classmethod
    def from_toml(cls, path: str | Path = "config/settings.toml") -> "Settings":
        path = Path(path)
        if not path.exists():
            return cls()
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return cls(**data)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_toml()
    return _settings
