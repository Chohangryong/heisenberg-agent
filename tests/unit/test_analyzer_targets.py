"""Unit tests for needs_analysis() decision logic."""

from dataclasses import dataclass

from heisenberg_agent.storage.repositories.analyses import AnalysisDecision, needs_analysis


@dataclass
class FakeArticle:
    analyze_status: str = "PENDING"
    content_hash: str = "abc"


@dataclass
class FakeRun:
    source_content_hash: str = "abc"
    analysis_version: str = "v1"
    prompt_bundle_version: str = "p1"


def test_pending_status():
    d = needs_analysis(FakeArticle("PENDING"), None, "v1", "p1")
    assert d.should_analyze is True
    assert d.reason == "status_pending"


def test_no_current_run():
    d = needs_analysis(FakeArticle("SUCCEEDED"), None, "v1", "p1")
    assert d.should_analyze is True
    assert d.reason == "no_current_run"


def test_content_hash_changed():
    d = needs_analysis(
        FakeArticle("SUCCEEDED", content_hash="new_hash"),
        FakeRun(source_content_hash="old_hash"),
        "v1", "p1",
    )
    assert d.should_analyze is True
    assert d.reason == "content_hash_changed"


def test_analysis_version_changed():
    d = needs_analysis(
        FakeArticle("SUCCEEDED"),
        FakeRun(analysis_version="v1"),
        "v2", "p1",
    )
    assert d.should_analyze is True
    assert d.reason == "analysis_version_changed"


def test_prompt_bundle_version_changed():
    d = needs_analysis(
        FakeArticle("SUCCEEDED"),
        FakeRun(prompt_bundle_version="p1"),
        "v1", "p2",
    )
    assert d.should_analyze is True
    assert d.reason == "prompt_bundle_version_changed"


def test_up_to_date():
    d = needs_analysis(
        FakeArticle("SUCCEEDED"),
        FakeRun(),
        "v1", "p1",
    )
    assert d.should_analyze is False
    assert d.reason == "up_to_date"


def test_failed_with_matching_run_still_checks_hash():
    """Even if status is FAILED, if there's a current run we check hashes."""
    d = needs_analysis(
        FakeArticle("FAILED"),
        FakeRun(),
        "v1", "p1",
    )
    assert d.should_analyze is False
    assert d.reason == "up_to_date"
