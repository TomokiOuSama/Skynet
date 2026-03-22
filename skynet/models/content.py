"""Content models - tweets, posts, articles ingested from KOLs."""

import datetime as dt
import enum

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from skynet.models.base import Base, TimestampMixin


class ContentType(str, enum.Enum):
    ORIGINAL = "original"
    RETWEET = "retweet"
    QUOTE = "quote"
    REPLY = "reply"
    ARTICLE = "article"  # substack / blog
    POST = "post"  # reddit


class Content(TimestampMixin, Base):
    """A piece of content (tweet, post, article) from a KOL."""

    __tablename__ = "contents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kol_id: Mapped[int] = mapped_column(Integer, ForeignKey("kols.id"))
    platform: Mapped[str] = mapped_column(String(32))
    platform_content_id: Mapped[str] = mapped_column(String(256))
    content_type: Mapped[ContentType] = mapped_column(Enum(ContentType))

    text: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String(1024))
    published_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    # Engagement metrics
    likes: Mapped[int] = mapped_column(Integer, default=0)
    reposts: Mapped[int] = mapped_column(Integer, default=0)
    replies: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)

    # If this is a retweet/quote, link to original
    original_content_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("contents.id"), nullable=True
    )

    # Relationships
    kol: Mapped["KOL"] = relationship(back_populates="contents")  # noqa: F821
    ticker_mentions: Mapped[list["TickerMention"]] = relationship(
        back_populates="content", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("platform", "platform_content_id", name="uq_content_platform"),
    )


class TickerMention(TimestampMixin, Base):
    """A stock ticker mentioned in a piece of content, with extracted sentiment."""

    __tablename__ = "ticker_mentions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_id: Mapped[int] = mapped_column(Integer, ForeignKey("contents.id"))
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    sentiment: Mapped[float] = mapped_column(Float, default=0.0)  # -1.0 to 1.0
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # 0.0 to 1.0

    content: Mapped["Content"] = relationship(back_populates="ticker_mentions")
