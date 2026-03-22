"""KOL (Key Opinion Leader) data models.

Core design: A KOL is a platform-agnostic identity node. Each KOL can have
multiple PlatformAccounts (Twitter, Substack, Reddit). Different platforms
serve different roles:
  - Twitter:   Social graph exploration (discovery of new KOLs)
  - Substack:  Deep content analysis (stock call extraction, alpha scoring)
  - Reddit:    Supplementary signal source
"""

import datetime as dt
import enum

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from skynet.models.base import Base, TimestampMixin


class Platform(str, enum.Enum):
    TWITTER = "twitter"
    REDDIT = "reddit"
    SUBSTACK = "substack"


class KOLType(str, enum.Enum):
    """Classification of KOL content style — determines scoring methodology."""
    STOCK_PICKER = "stock_picker"   # Thesis-driven individual stock calls
    MACRO = "macro"                 # ETFs, indices, rates, geopolitics
    SECTOR = "sector"              # Sector reviews, valuable but not actionable picks
    UNCLASSIFIED = "unclassified"  # Not yet classified


class VolumeTier(str, enum.Enum):
    """Call frequency tier — KOLs are compared within their tier."""
    SELECTIVE = "selective"   # < 30 calls
    MODERATE = "moderate"     # 30-99 calls
    HIGH_VOLUME = "high_volume"  # 100+ calls


class KOL(TimestampMixin, Base):
    """A tracked Key Opinion Leader — platform-agnostic identity node.

    A single KOL (e.g. SemiAnalysis) may have accounts on Twitter, Substack,
    and Reddit. All content and scores roll up to this identity.
    """

    __tablename__ = "kols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256))  # Canonical display name
    bio: Mapped[str | None] = mapped_column(Text)

    # Classification
    kol_type: Mapped[KOLType] = mapped_column(
        Enum(KOLType), default=KOLType.UNCLASSIFIED
    )
    volume_tier: Mapped[VolumeTier] = mapped_column(
        Enum(VolumeTier), default=VolumeTier.SELECTIVE
    )

    # Discovery metadata
    discovered_via_kol_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("kols.id"), nullable=True
    )
    discovery_depth: Mapped[int] = mapped_column(Integer, default=0)  # 0 = seed
    is_active: Mapped[bool] = mapped_column(default=True)

    # Relationships
    platform_accounts: Mapped[list["PlatformAccount"]] = relationship(
        back_populates="kol", cascade="all, delete-orphan"
    )
    contents: Mapped[list["Content"]] = relationship(  # noqa: F821
        back_populates="kol", cascade="all, delete-orphan"
    )
    scores: Mapped[list["KOLScore"]] = relationship(  # noqa: F821
        back_populates="kol", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<KOL #{self.id} {self.name} ({self.kol_type.value})>"


class PlatformAccount(TimestampMixin, Base):
    """A KOL's account on a specific platform.

    Multiple PlatformAccounts can point to the same KOL, enabling
    cross-platform identity resolution:
      - Twitter @SemiAnalysis  →  KOL "SemiAnalysis"
      - Substack semianalysis  →  KOL "SemiAnalysis"
    """

    __tablename__ = "platform_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kol_id: Mapped[int] = mapped_column(Integer, ForeignKey("kols.id"), index=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform))
    platform_user_id: Mapped[str] = mapped_column(String(128))
    username: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str | None] = mapped_column(String(512))
    profile_url: Mapped[str | None] = mapped_column(String(1024))
    followers_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(default=True)

    # Platform-specific role
    # Twitter accounts are used for discovery; Substack for deep content
    is_discovery_source: Mapped[bool] = mapped_column(default=False)
    is_content_source: Mapped[bool] = mapped_column(default=False)

    kol: Mapped["KOL"] = relationship(back_populates="platform_accounts")

    __table_args__ = (
        UniqueConstraint("platform", "platform_user_id", name="uq_platform_account"),
    )

    def __repr__(self) -> str:
        return f"<Account {self.platform.value}/@{self.username}>"


class KOLRelation(TimestampMixin, Base):
    """Social graph edges between KOLs (follows, retweets, mentions).

    These edges are primarily discovered via Twitter interactions but
    represent relationships at the KOL identity level, not platform level.
    """

    __tablename__ = "kol_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_kol_id: Mapped[int] = mapped_column(Integer, ForeignKey("kols.id"))
    target_kol_id: Mapped[int] = mapped_column(Integer, ForeignKey("kols.id"))
    relation_type: Mapped[str] = mapped_column(String(32))  # follow, retweet, mention, quote
    platform: Mapped[Platform] = mapped_column(Enum(Platform))  # Where the relation was observed
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "source_kol_id", "target_kol_id", "relation_type", "platform",
            name="uq_kol_relation"
        ),
    )
