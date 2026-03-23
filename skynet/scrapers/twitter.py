"""Twitter/X scraper using TwitterAPI.io.

Drop-in replacement for the tweepy-based scraper. Uses username-based
lookups since TwitterAPI.io endpoints are username-oriented.
"""

import logging
from typing import Any

import httpx

from skynet.utils.config import get_settings

logger = logging.getLogger(__name__)

BASE_URL = "https://api.twitterapi.io/twitter"


class TwitterScraper:
    """Async Twitter client backed by TwitterAPI.io."""

    def __init__(self, api_key: str | None = None):
        settings = get_settings()
        self.api_key = api_key or settings.twitter.api_key
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"X-API-Key": self.api_key},
            timeout=30.0,
        )

    async def close(self):
        await self._client.aclose()

    # ------------------------------------------------------------------
    # User lookup
    # ------------------------------------------------------------------

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """Look up a user by username."""
        try:
            resp = await self._client.get(
                "/user/by_username", params={"userName": username}
            )
            resp.raise_for_status()
            data = resp.json()
            user = data.get("data") or data
            if not user or not user.get("userName"):
                return None
            return _normalize_user(user)
        except Exception:
            logger.exception("Failed to look up user @%s", username)
        return None

    async def get_user_info(self, username: str) -> dict[str, Any]:
        """Get user info by username (was by ID, now username-based)."""
        result = await self.get_user_by_username(username)
        return result or {}

    # ------------------------------------------------------------------
    # Tweets
    # ------------------------------------------------------------------

    async def get_user_tweets(
        self, username: str, max_results: int = 50
    ) -> list[dict[str, Any]]:
        """Fetch recent tweets from a user by username."""
        tweets: list[dict[str, Any]] = []
        cursor: str | None = None

        try:
            while len(tweets) < max_results:
                params: dict[str, Any] = {"userName": username}
                if cursor:
                    params["cursor"] = cursor

                resp = await self._client.get("/user/last_tweets", params=params)
                resp.raise_for_status()
                data = resp.json()

                batch = data.get("tweets") or []
                if not batch:
                    break

                for raw in batch:
                    tweets.append(_normalize_tweet(raw))
                    if len(tweets) >= max_results:
                        break

                if not data.get("has_next_page") or not data.get("next_cursor"):
                    break
                cursor = data["next_cursor"]

        except Exception:
            logger.exception("Failed to fetch tweets for @%s", username)

        return tweets

    async def search_tweets(
        self, query: str, max_results: int = 100
    ) -> list[dict[str, Any]]:
        """Advanced search (supports $TICKER, from:user, min_faves, etc.)."""
        tweets: list[dict[str, Any]] = []
        cursor: str | None = None

        try:
            while len(tweets) < max_results:
                params: dict[str, Any] = {"query": query}
                if cursor:
                    params["cursor"] = cursor

                resp = await self._client.get(
                    "/tweet/advanced_search", params=params
                )
                resp.raise_for_status()
                data = resp.json()

                batch = data.get("tweets") or []
                if not batch:
                    break

                for raw in batch:
                    tweets.append(_normalize_tweet(raw))
                    if len(tweets) >= max_results:
                        break

                if not data.get("has_next_page") or not data.get("next_cursor"):
                    break
                cursor = data["next_cursor"]

        except Exception:
            logger.exception("Failed to search tweets: %s", query)

        return tweets

    # ------------------------------------------------------------------
    # Social graph (for discovery)
    # ------------------------------------------------------------------

    async def get_recent_interactions(
        self, username: str
    ) -> list[dict[str, Any]]:
        """Get accounts this user recently retweeted, quoted, or mentioned.

        Returns interaction dicts with username and type.
        """
        interactions: list[dict[str, Any]] = []
        tweets = await self.get_user_tweets(username, max_results=100)

        seen_users: set[str] = set()
        for tweet in tweets:
            tweet_type = tweet["type"]
            if tweet_type not in ("retweeted", "quoted"):
                continue

            ref_username = tweet.get("referenced_username")
            if not ref_username or ref_username == username:
                continue
            if ref_username in seen_users:
                continue

            seen_users.add(ref_username)
            interactions.append({
                "username": ref_username,
                "type": tweet_type.replace("retweeted", "retweet").replace(
                    "quoted", "quote"
                ),
            })

        return interactions


# ======================================================================
# Response normalization helpers
# ======================================================================

def _normalize_user(raw: dict) -> dict[str, Any]:
    """Normalize TwitterAPI.io user object to our internal format."""
    return {
        "id": str(raw.get("id") or raw.get("userId") or raw.get("rest_id", "")),
        "name": raw.get("name", ""),
        "username": raw.get("userName") or raw.get("username", ""),
        "description": raw.get("description") or raw.get("bio", ""),
        "followers_count": raw.get("followers") or raw.get("followersCount", 0),
    }


def _normalize_tweet(raw: dict) -> dict[str, Any]:
    """Normalize TwitterAPI.io tweet object to our internal format."""
    # Determine tweet type from TwitterAPI.io fields
    tweet_type = "original"
    referenced_username = None

    if raw.get("isRetweet"):
        tweet_type = "retweeted"
        rt_author = raw.get("retweetedTweet", {}).get("author", {})
        referenced_username = rt_author.get("userName")
    elif raw.get("isQuote") or raw.get("quoted_tweet"):
        tweet_type = "quoted"
        qt = raw.get("quoted_tweet") or raw.get("quotedTweet", {})
        qt_author = qt.get("author", {})
        referenced_username = qt_author.get("userName")
    elif raw.get("isReply") or raw.get("inReplyToId"):
        tweet_type = "replied_to"

    # Extract cashtags from entities or text
    cashtags: list[str] = []
    entities = raw.get("entities", {})
    if entities and "symbols" in entities:
        cashtags = [s.get("text", "") for s in entities["symbols"] if s.get("text")]
    elif entities and "cashtags" in entities:
        cashtags = [c.get("tag", "") for c in entities["cashtags"] if c.get("tag")]

    # Fall back to text-based extraction if no entity cashtags
    if not cashtags:
        import re
        text = raw.get("text", "")
        cashtags = re.findall(r"\$([A-Z]{1,5})\b", text)

    author = raw.get("author", {})

    return {
        "id": str(raw.get("id") or raw.get("tweetId", "")),
        "text": raw.get("text", ""),
        "created_at": raw.get("createdAt") or raw.get("created_at"),
        "likes": raw.get("likeCount", 0),
        "retweets": raw.get("retweetCount", 0),
        "replies": raw.get("replyCount", 0),
        "views": raw.get("viewCount", 0),
        "type": tweet_type,
        "referenced_username": referenced_username,
        "cashtags": cashtags,
        "author_username": author.get("userName", ""),
    }
