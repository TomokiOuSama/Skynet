"""Substack scraper - fetches newsletter content via RSS feeds."""

import logging
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class SubstackScraper:
    """Scrapes Substack newsletters via their RSS feeds."""

    def __init__(self):
        self.http = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "skynet-kol-tracker/0.1"},
        )

    async def get_newsletter_posts(self, newsletter_url: str, limit: int = 20) -> list[dict[str, Any]]:
        """Fetch recent posts from a Substack newsletter via RSS.

        Args:
            newsletter_url: Base URL like "https://example.substack.com"
            limit: Max posts to fetch
        """
        feed_url = f"{newsletter_url.rstrip('/')}/feed"
        posts = []

        try:
            resp = await self.http.get(feed_url)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            items = soup.find_all("item")[:limit]

            for item in items:
                title = item.find("title")
                link = item.find("link")
                description = item.find("description")
                pub_date = item.find("pubdate")
                creator = item.find("dc:creator")

                # Extract text content from HTML description
                content_text = ""
                if description:
                    content_soup = BeautifulSoup(description.text, "html.parser")
                    content_text = content_soup.get_text(separator=" ", strip=True)

                posts.append({
                    "title": title.text.strip() if title else "",
                    "url": link.text.strip() if link else "",
                    "text": content_text,
                    "author": creator.text.strip() if creator else "",
                    "published_at": pub_date.text.strip() if pub_date else "",
                    "source_url": newsletter_url,
                })
        except Exception:
            logger.exception(f"Failed to fetch Substack feed from {newsletter_url}")

        return posts

    async def close(self):
        await self.http.aclose()
