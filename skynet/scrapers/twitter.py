"""Twitter/X scraper using the v2 API via tweepy."""

import logging
from typing import Any

import tweepy
from tweepy.asynchronous import AsyncClient

from skynet.utils.config import get_settings

logger = logging.getLogger(__name__)


class TwitterScraper:
    """Async Twitter API client for fetching tweets and user data."""

    def __init__(self, bearer_token: str | None = None):
        settings = get_settings()
        token = bearer_token or settings.twitter.bearer_token
        self.client = AsyncClient(bearer_token=token, wait_on_rate_limit=True)

    async def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        """Look up a user by username."""
        try:
            resp = await self.client.get_user(
                username=username,
                user_fields=["id", "name", "username", "description", "public_metrics"],
            )
            if resp.data:
                user = resp.data
                return {
                    "id": str(user.id),
                    "name": user.name,
                    "username": user.username,
                    "description": user.description or "",
                    "followers_count": user.public_metrics.get("followers_count", 0),
                }
        except tweepy.errors.TweepyException:
            logger.exception(f"Failed to look up user @{username}")
        return None

    async def get_user_info(self, user_id: str) -> dict[str, Any]:
        """Get user info by ID."""
        try:
            resp = await self.client.get_user(
                id=user_id,
                user_fields=["id", "name", "username", "description", "public_metrics"],
            )
            if resp.data:
                user = resp.data
                return {
                    "id": str(user.id),
                    "name": user.name,
                    "username": user.username,
                    "description": user.description or "",
                    "followers_count": user.public_metrics.get("followers_count", 0),
                }
        except tweepy.errors.TweepyException:
            logger.exception(f"Failed to get user info for {user_id}")
        return {}

    async def get_user_tweets(
        self, user_id: str, max_results: int = 50
    ) -> list[dict[str, Any]]:
        """Fetch recent tweets from a user."""
        tweets = []
        try:
            resp = await self.client.get_users_tweets(
                id=user_id,
                max_results=min(max_results, 100),
                tweet_fields=[
                    "id", "text", "created_at", "public_metrics",
                    "referenced_tweets", "entities",
                ],
                expansions=["referenced_tweets.id", "referenced_tweets.id.author_id"],
                user_fields=["id", "username"],
            )
            if resp.data:
                includes_users = {
                    str(u.id): u.username for u in (resp.includes.get("users", []) or [])
                }
                for tweet in resp.data:
                    tweet_data = {
                        "id": str(tweet.id),
                        "text": tweet.text,
                        "created_at": tweet.created_at.isoformat() if tweet.created_at else None,
                        "likes": tweet.public_metrics.get("like_count", 0),
                        "retweets": tweet.public_metrics.get("retweet_count", 0),
                        "replies": tweet.public_metrics.get("reply_count", 0),
                        "views": tweet.public_metrics.get("impression_count", 0),
                        "type": "original",
                        "referenced_users": includes_users,
                    }
                    if tweet.referenced_tweets:
                        ref = tweet.referenced_tweets[0]
                        tweet_data["type"] = ref.type  # retweeted, quoted, replied_to
                        tweet_data["referenced_tweet_id"] = str(ref.id)

                    # Extract tickers from entities or text
                    cashtags = []
                    if tweet.entities and "cashtags" in tweet.entities:
                        cashtags = [c["tag"] for c in tweet.entities["cashtags"]]
                    tweet_data["cashtags"] = cashtags

                    tweets.append(tweet_data)
        except tweepy.errors.TweepyException:
            logger.exception(f"Failed to fetch tweets for user {user_id}")
        return tweets

    async def get_recent_interactions(self, user_id: str) -> list[dict[str, Any]]:
        """Get accounts this user recently retweeted, quoted, or mentioned.

        Returns a list of interaction dicts with user_id, username, and type.
        """
        interactions = []
        tweets = await self.get_user_tweets(user_id, max_results=100)

        seen_users = set()
        for tweet in tweets:
            if tweet["type"] in ("retweeted", "quoted"):
                ref_users = tweet.get("referenced_users", {})
                for uid, uname in ref_users.items():
                    if uid != user_id and uid not in seen_users:
                        seen_users.add(uid)
                        interactions.append({
                            "user_id": uid,
                            "username": uname,
                            "type": tweet["type"].replace("retweeted", "retweet").replace(
                                "quoted", "quote"
                            ),
                        })
        return interactions
