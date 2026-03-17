"""Article repository — CRUD for articles and child tables.

All save/update operations run in a single transaction.
Callers pass a Session; this module calls commit/rollback.

Failure strategy:
- save_new_article: on rollback, article row does not exist.
  Caller must use record_run_error() to log to CollectionRun (new tx).
- update_article: on rollback, existing article row survives.
  Caller should use mark_failed() to set status + event (new tx).
- mark_noop: on rollback, no data loss. Log-only fallback.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from heisenberg_agent.storage.models import (
    Article,
    ArticleEvent,
    ArticleImage,
    ArticleSection,
    ArticleTag,
    CollectionRun,
    Tag,
)
from heisenberg_agent.utils.dt import now_utc
from heisenberg_agent.utils.logger import get_logger

logger = get_logger()


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def find_by_slug(session: Session, source_site: str, slug: str) -> Article | None:
    """Find an article by source_site + slug."""
    stmt = select(Article).where(
        Article.source_site == source_site,
        Article.slug == slug,
    )
    return session.execute(stmt).scalar_one_or_none()


def find_by_url(session: Session, source_site: str, url: str) -> Article | None:
    """Find an article by source_site + url (matches UNIQUE constraint)."""
    stmt = select(Article).where(
        Article.source_site == source_site,
        Article.url == url,
    )
    return session.execute(stmt).scalar_one_or_none()


def find_existing_slugs(
    session: Session, source_site: str, slugs: list[str]
) -> dict[str, Article]:
    """Bulk lookup: slug → Article for existing articles.

    Returns dict keyed by slug for O(1) lookup in filter step.
    """
    if not slugs:
        return {}
    stmt = select(Article).where(
        Article.source_site == source_site,
        Article.slug.in_(slugs),
    )
    results = session.execute(stmt).scalars().all()
    return {a.slug: a for a in results}


# ---------------------------------------------------------------------------
# Save — new article (single transaction)
# ---------------------------------------------------------------------------


def save_new_article(
    session: Session,
    *,
    article_data: dict[str, Any],
    sections: list[dict[str, Any]],
    image_urls: list[str],
    tag_names: list[str],
) -> Article:
    """Insert a new article with all child rows in a single transaction.

    Args:
        session: SQLAlchemy session (caller manages engine).
        article_data: Column values for articles table.
        sections: List of dicts matching ArticleSection columns.
        image_urls: Image URLs found in content.
        tag_names: Tag strings to upsert.

    Returns:
        Created Article instance.

    Raises:
        Exception: On commit failure. Session is rolled back.
            Caller should use record_run_error() in a NEW transaction
            since article row does not exist after rollback.
    """
    now = now_utc()

    article = Article(
        **article_data,
        collect_status="SUCCEEDED",
        last_seen_at=now,
    )
    session.add(article)
    session.flush()  # article.id available

    # Sections
    for sec in sections:
        session.add(ArticleSection(article_id=article.id, **sec))

    # Images
    for idx, url in enumerate(image_urls):
        session.add(ArticleImage(
            article_id=article.id, ordinal=idx, image_url=url,
        ))

    # Tags
    _upsert_tags(session, article.id, tag_names)

    # Event
    session.add(ArticleEvent(
        article_id=article.id,
        stage="collector",
        event_type="detail.saved",
    ))

    session.commit()
    logger.info("repo.article_saved", slug=article.slug, article_id=article.id)
    return article


# ---------------------------------------------------------------------------
# Update — existing article with changed content (single transaction)
# ---------------------------------------------------------------------------


def update_article(
    session: Session,
    article: Article,
    *,
    article_data: dict[str, Any],
    sections: list[dict[str, Any]],
    image_urls: list[str],
    tag_names: list[str],
) -> Article:
    """Replace child rows and update article in a single transaction.

    Deletes existing sections/images/tags, inserts new ones,
    updates article metadata, bumps content_version,
    resets analyze_status to PENDING for reanalysis.

    Raises:
        Exception: On commit failure. Session is rolled back.
            Caller should use mark_failed() in a NEW transaction
            since existing article row survives rollback.
    """
    now = now_utc()

    # Delete existing child rows
    session.query(ArticleSection).filter_by(article_id=article.id).delete()
    session.query(ArticleImage).filter_by(article_id=article.id).delete()
    session.query(ArticleTag).filter_by(article_id=article.id).delete()

    # Insert new sections
    for sec in sections:
        session.add(ArticleSection(article_id=article.id, **sec))

    # Insert new images
    for idx, url in enumerate(image_urls):
        session.add(ArticleImage(
            article_id=article.id, ordinal=idx, image_url=url,
        ))

    # Upsert tags
    _upsert_tags(session, article.id, tag_names)

    # Update article fields
    for key, value in article_data.items():
        setattr(article, key, value)

    article.content_version = (article.content_version or 1) + 1
    article.analyze_status = "PENDING"  # trigger reanalysis
    article.collect_status = "SUCCEEDED"
    article.last_seen_at = now

    # Event
    session.add(ArticleEvent(
        article_id=article.id,
        stage="collector",
        event_type="detail.updated",
    ))

    session.commit()
    logger.info(
        "repo.article_updated",
        slug=article.slug,
        article_id=article.id,
        content_version=article.content_version,
    )
    return article


# ---------------------------------------------------------------------------
# NOOP — content unchanged, update last_seen_at only
# ---------------------------------------------------------------------------


def mark_noop(session: Session, article: Article) -> None:
    """Update last_seen_at and log noop event.

    Even when content is identical, we record that this cycle
    confirmed the article still exists on the source site.
    """
    article.last_seen_at = now_utc()

    session.add(ArticleEvent(
        article_id=article.id,
        stage="collector",
        event_type="detail.skipped_noop",
    ))

    session.commit()
    logger.info("repo.article_noop", slug=article.slug, article_id=article.id)


# ---------------------------------------------------------------------------
# Failure recording
# ---------------------------------------------------------------------------


def mark_failed(
    session: Session,
    article: Article,
    error_code: str,
    error_message: str,
) -> None:
    """Record collect failure on an existing article (new transaction).

    Called AFTER a rollback when the article row already existed.
    Opens a new transaction to persist failure state.
    """
    try:
        article.collect_status = "FAILED"
        article.collect_attempt_count = (article.collect_attempt_count or 0) + 1
        article.last_error_code = error_code
        article.last_error_message = error_message[:500]

        session.add(ArticleEvent(
            article_id=article.id,
            stage="collector",
            event_type="save_failed",
            payload_json=json.dumps(
                {"error_code": error_code, "error": error_message[:500]},
                ensure_ascii=False,
            ),
        ))
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(
            "repo.mark_failed_error",
            slug=article.slug,
            original_error=error_message,
            secondary_error=str(e),
        )


def record_run_error(
    session: Session,
    run_id: int,
    slug: str,
    url: str,
    error: str,
) -> None:
    """Append error to CollectionRun.report_json (new transaction).

    Called when a new article INSERT failed — article row does not exist,
    so we record against the run instead.
    """
    try:
        run = session.get(CollectionRun, run_id)
        if run is None:
            logger.error("repo.run_not_found", run_id=run_id)
            return

        run.errors = (run.errors or 0) + 1

        errors_list: list[dict[str, str]] = json.loads(run.report_json or "[]")
        errors_list.append({
            "slug": slug,
            "url": url,
            "error": error[:500],
            "stage": "collect.save",
            "timestamp": now_utc().isoformat(),
        })
        run.report_json = json.dumps(errors_list, ensure_ascii=False)

        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(
            "repo.record_run_error_failed",
            run_id=run_id,
            slug=slug,
            secondary_error=str(e),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _upsert_tags(session: Session, article_id: int, tag_names: list[str]) -> None:
    """Upsert tags and create article_tags join rows."""
    for name in tag_names:
        name = name.strip()
        if not name:
            continue

        tag = session.execute(
            select(Tag).where(Tag.name == name)
        ).scalar_one_or_none()

        if tag is None:
            tag = Tag(name=name)
            session.add(tag)
            session.flush()

        session.add(ArticleTag(article_id=article_id, tag_id=tag.id))
