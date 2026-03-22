"""KOL (Key Opinion Leader) data models."""

import datetime as dt
import enum

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from skynet.models.base import Base, TimestampMixin


class Platform(str, enum.Enum):
    TWITTER = "twitter"
    REDDIT = "reddit"
    SUBSTACK = "substack"


class KOL(TimestampMixin, Base):
    """A tracked Key Opinion Leader / influencer."""

    __tablename__ = "kols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform))
    platform_user_id: Mapped[str] = mapped_column(String(128))
    username: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str | None] = mapped_column(String(512))
    bio: Mapped[str | None] = mapped_column(Text)
    followers_count: Mapped[int] = mapped_column(Integer, default=0)

    # Discovery metadata
    discovered_via_kol_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("kols.id"), nullable=True
    )
    discovery_depth: Mapped[int] = mapped_column(Integer, default=0)  # 0 = seed
    is_active: Mapped[bool] = mapped_column(default=True)

    # Relationships
    contents: Mapped[list["Content"]] = relationship(  # noqa: F821
        back_populates="kol", cascade="all, delete-orphan"
    )
    scores: Mapped[list["KOLScore"]] = relationship(  # noqa: F821
        back_populates="kol", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("platform", "platform_user_id", name="uq_kol_platform_user"),
    )

    def __repr__(self) -> str:
        return f"<KOL {self.platform.value}/@{self.username}>"


class KOLRelation(TimestampMixin, Base):
    """Social graph edges between KOLs (follows, retweets, mentions)."""

    __tablename__ = "kol_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_kol_id: Mapped[int] = mapped_column(Integer, ForeignKey("kols.id"))
    target_kol_id: Mapped[int] = mapped_column(Integer, ForeignKey("kols.id"))
    relation_type: Mapped[str] = mapped_column(String(32))  # follow, retweet, mention, quote
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "source_kol_id", "target_kol_id", "relation_type", name="uq_kol_relation"
        ),
    )
