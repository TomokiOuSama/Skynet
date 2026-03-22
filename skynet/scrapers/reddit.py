"""Reddit scraper using PRAW for monitoring stock-related subreddits."""

import logging
from typing import Any

import praw

from skynet.utils.config import get_settings

logger = logging.getLogger(__name__)


class RedditScraper:
    """Scrapes Reddit for stock-related posts and tracks influential posters."""

    def __init__(self):
        settings = get_settings()
        self.reddit = praw.Reddit(
            client_id=settings.reddit.client_id,
            client_secret=settings.reddit.client_secret,
            user_agent=settings.reddit.user_agent,
        )
        self.subreddits = settings.reddit.subreddits

    def get_hot_posts(self, subreddit: str, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch hot posts from a subreddit."""
        posts = []
        try:
            sub = self.reddit.subreddit(subreddit)
            for post in sub.hot(limit=limit):
                posts.append({
                    "id": post.id,
                    "title": post.title,
                    "text": post.selftext,
                    "url": post.url,
                    "author": str(post.author) if post.author else "[deleted]",
                    "author_id": str(post.author) if post.author else None,
                    "score": post.score,
                    "upvote_ratio": post.upvote_ratio,
                    "num_comments": post.num_comments,
                    "created_utc": post.created_utc,
                    "subreddit": subreddit,
                })
        except Exception:
            logger.exception(f"Failed to fetch posts from r/{subreddit}")
        return posts

    def get_user_posts(self, username: str, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch recent posts from a specific user."""
        posts = []
        try:
            user = self.reddit.redditor(username)
            for submission in user.submissions.new(limit=limit):
                posts.append({
                    "id": submission.id,
                    "title": submission.title,
                    "text": submission.selftext,
                    "url": submission.url,
                    "author": username,
                    "score": submission.score,
                    "created_utc": submission.created_utc,
                    "subreddit": str(submission.subreddit),
                })
        except Exception:
            logger.exception(f"Failed to fetch posts for u/{username}")
        return posts

    def scan_all_subreddits(self) -> list[dict[str, Any]]:
        """Scan all configured subreddits for stock-related posts."""
        all_posts = []
        for sub in self.subreddits:
            posts = self.get_hot_posts(sub)
            all_posts.extend(posts)
            logger.info(f"Fetched {len(posts)} posts from r/{sub}")
        return all_posts
