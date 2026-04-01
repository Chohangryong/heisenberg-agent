"""SQLAlchemy 2.0 ORM models — SQLite SSOT schema.

Design references:
- design/heisenberg_agent_design_v1.4.md §6
- CLAUDE.md 데이터베이스 규칙
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Float,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# articles — metadata header + collect/analyze status + current analysis pointer
#
# body_text is a DERIVED CACHE of article_sections (not the SSOT).
# The SSOT for article content is article_sections.
# ---------------------------------------------------------------------------

class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Identity
    source_site: Mapped[str] = mapped_column(Text, default="heisenberg.kr")
    slug: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text, default=None)

    # Metadata
    title: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text, default=None)
    category: Mapped[str | None] = mapped_column(Text, default=None)
    content_kind: Mapped[str] = mapped_column(Text, default="article")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    source_timezone: Mapped[str] = mapped_column(Text, default="Asia/Seoul")
    collected_at: Mapped[datetime] = mapped_column(DateTime)

    # Rendered HTML (raw archive)
    rendered_html: Mapped[str | None] = mapped_column(Text, default=None)
    rendered_html_hash: Mapped[str | None] = mapped_column(Text, default=None)

    # Derived cache — SSOT is article_sections
    body_text: Mapped[str | None] = mapped_column(Text, default=None)
    body_text_hash: Mapped[str | None] = mapped_column(Text, default=None)

    # Canonical content hash (for dedup / reanalysis trigger)
    content_hash: Mapped[str | None] = mapped_column(Text, default=None)

    # Version tracking
    selector_profile_version: Mapped[str | None] = mapped_column(Text, default=None)
    parser_version: Mapped[str | None] = mapped_column(Text, default=None)
    content_version: Mapped[int] = mapped_column(Integer, default=1)

    # PDF snapshot
    snapshot_path: Mapped[str | None] = mapped_column(Text, default=None)
    snapshot_sha256: Mapped[str | None] = mapped_column(Text, default=None)
    snapshot_byte_size: Mapped[int | None] = mapped_column(Integer, default=None)
    snapshot_page_count: Mapped[int | None] = mapped_column(Integer, default=None)

    # Stage-level status (collect / analyze only — sync status lives in sync_jobs)
    collect_status: Mapped[str] = mapped_column(Text, default="PENDING")
    analyze_status: Mapped[str] = mapped_column(Text, default="PENDING")

    # Retry tracking (collect / analyze only)
    collect_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    analyze_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(Text, default=None)
    last_error_message: Mapped[str | None] = mapped_column(Text, default=None)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    # Current analysis pointer (SET NULL on analysis delete)
    current_analysis_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("analysis_runs.id", ondelete="SET NULL"),
        default=None,
    )

    # Timestamps
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    sections: Mapped[list["ArticleSection"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    analysis_runs: Mapped[list["AnalysisRun"]] = relationship(
        back_populates="article",
        cascade="all, delete-orphan",
        foreign_keys="AnalysisRun.article_id",
    )
    sync_jobs: Mapped[list["SyncJob"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    images: Mapped[list["ArticleImage"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )
    annotations: Mapped["ArticleAnnotation | None"] = relationship(
        back_populates="article", cascade="all, delete-orphan", uselist=False
    )
    events: Mapped[list["ArticleEvent"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("source_site", "slug", name="uq_articles_site_slug"),
        UniqueConstraint("source_site", "url", name="uq_articles_site_url"),
        Index("idx_articles_published_at", "published_at"),
        Index("idx_articles_category", "category"),
        Index("idx_articles_collect_status", "collect_status"),
        Index("idx_articles_analyze_status", "analyze_status"),
        Index("idx_articles_content_hash", "content_hash"),
    )


# ---------------------------------------------------------------------------
# article_sections — content SSOT
# ---------------------------------------------------------------------------

class ArticleSection(Base):
    __tablename__ = "article_sections"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("articles.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)

    section_kind: Mapped[str] = mapped_column(Text)
    section_title: Mapped[str | None] = mapped_column(Text, default=None)
    access_tier: Mapped[str] = mapped_column(Text, default="unknown")
    is_gated_notice: Mapped[bool] = mapped_column(Boolean, default=False)

    body_text: Mapped[str | None] = mapped_column(Text, default=None)
    body_html: Mapped[str | None] = mapped_column(Text, default=None)
    content_hash: Mapped[str | None] = mapped_column(Text, default=None)

    # Drift tracking: which selector extracted this section
    selector_used: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    article: Mapped["Article"] = relationship(back_populates="sections")

    __table_args__ = (
        UniqueConstraint("article_id", "ordinal", name="uq_sections_article_ordinal"),
        Index("idx_sections_article_kind", "article_id", "section_kind"),
    )


# ---------------------------------------------------------------------------
# tags / article_tags
# ---------------------------------------------------------------------------

class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True)


class ArticleTag(Base):
    __tablename__ = "article_tags"

    article_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


# ---------------------------------------------------------------------------
# article_images
# ---------------------------------------------------------------------------

class ArticleImage(Base):
    __tablename__ = "article_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("articles.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)

    image_url: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(Text, default=None)
    sha256: Mapped[str | None] = mapped_column(Text, default=None)
    byte_size: Mapped[int | None] = mapped_column(Integer, default=None)

    article: Mapped["Article"] = relationship(back_populates="images")

    __table_args__ = (
        UniqueConstraint("article_id", "ordinal", name="uq_images_article_ordinal"),
    )


# ---------------------------------------------------------------------------
# analysis_runs — immutable analysis history
#
# Partial unique index ensures at most one is_current=true per article.
# ---------------------------------------------------------------------------

class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("articles.id", ondelete="CASCADE")
    )

    # Reanalysis trigger fields
    source_content_hash: Mapped[str] = mapped_column(Text)
    analysis_version: Mapped[str] = mapped_column(Text)
    prompt_bundle_version: Mapped[str] = mapped_column(Text)

    # Structured output results
    analysis_json: Mapped[str | None] = mapped_column(Text, default=None)
    summary_json: Mapped[str | None] = mapped_column(Text, default=None)
    critique_json: Mapped[str | None] = mapped_column(Text, default=None)

    # Top-level promoted fields for query convenience
    importance: Mapped[str | None] = mapped_column(Text, default=None)
    keywords_json: Mapped[str | None] = mapped_column(Text, default=None)

    # LLM metadata
    llm_provider: Mapped[str | None] = mapped_column(Text, default=None)
    llm_model: Mapped[str | None] = mapped_column(Text, default=None)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    output_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    cost_usd: Mapped[float | None] = mapped_column(Float, default=None)
    latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)

    # Run status
    status: Mapped[str] = mapped_column(Text, default="pending")
    error_code: Mapped[str | None] = mapped_column(Text, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)

    # At most one is_current=true per article (enforced by partial unique index)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    article: Mapped["Article"] = relationship(
        back_populates="analysis_runs", foreign_keys=[article_id]
    )

    __table_args__ = (
        Index("idx_analysis_article_current", "article_id", "is_current"),
        # Partial unique index: only one is_current=true per article
        Index(
            "uq_analysis_one_current_per_article",
            "article_id",
            unique=True,
            sqlite_where=(is_current == True),  # noqa: E712
        ),
    )


# ---------------------------------------------------------------------------
# sync_jobs — sole authority for sync status (Notion / ChromaDB)
# ---------------------------------------------------------------------------

class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("articles.id", ondelete="CASCADE")
    )
    target: Mapped[str] = mapped_column(Text)  # "notion" | "vector"

    payload_hash: Mapped[str | None] = mapped_column(Text, default=None)
    embedding_version: Mapped[str | None] = mapped_column(Text, default=None)

    status: Mapped[str] = mapped_column(Text, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    last_error_code: Mapped[str | None] = mapped_column(Text, default=None)
    last_error_message: Mapped[str | None] = mapped_column(Text, default=None)

    # External system ID (Notion page_id or Chroma doc id)
    external_id: Mapped[str | None] = mapped_column(Text, default=None)

    # Which analysis_run this job was last synced with
    synced_analysis_id: Mapped[int | None] = mapped_column(Integer, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    article: Mapped["Article"] = relationship(back_populates="sync_jobs")

    __table_args__ = (
        UniqueConstraint("article_id", "target", name="uq_sync_jobs_article_target"),
        Index("idx_sync_jobs_queue", "target", "status", "next_retry_at"),
    )


# ---------------------------------------------------------------------------
# article_annotations — user domain data (isolated from pipeline)
# ---------------------------------------------------------------------------

class ArticleAnnotation(Base):
    __tablename__ = "article_annotations"

    article_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("articles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    starred: Mapped[bool] = mapped_column(Boolean, default=False)
    rating: Mapped[int | None] = mapped_column(Integer, default=None)
    user_memo: Mapped[str | None] = mapped_column(Text, default=None)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    article: Mapped["Article"] = relationship(back_populates="annotations")


# ---------------------------------------------------------------------------
# article_events — article-level event log
# ---------------------------------------------------------------------------

class ArticleEvent(Base):
    __tablename__ = "article_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("articles.id", ondelete="CASCADE")
    )
    stage: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    article: Mapped["Article"] = relationship(back_populates="events")

    __table_args__ = (
        Index("idx_events_article_stage", "article_id", "stage", "created_at"),
    )


# ---------------------------------------------------------------------------
# collection_runs — pipeline execution report
# ---------------------------------------------------------------------------

class CollectionRun(Base):
    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger_type: Mapped[str] = mapped_column(Text)  # "scheduled" | "manual" | "pipeline"

    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    status: Mapped[str] = mapped_column(Text)  # "running" | "success" | "partial" | "failed"

    articles_found: Mapped[int] = mapped_column(Integer, default=0)
    articles_collected: Mapped[int] = mapped_column(Integer, default=0)
    articles_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    articles_synced_notion: Mapped[int] = mapped_column(Integer, default=0)
    articles_synced_vector: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    report_json: Mapped[str | None] = mapped_column(Text, default=None)


# ---------------------------------------------------------------------------
# app_state — runtime KV store
# ---------------------------------------------------------------------------

class AppState(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
