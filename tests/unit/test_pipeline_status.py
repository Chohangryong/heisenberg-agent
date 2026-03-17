"""Unit tests for StageSummary, derive_status, compute_errors."""

from heisenberg_agent.orchestrator.pipeline import (
    StageSummary,
    compute_errors,
    derive_status,
)


# ---------------------------------------------------------------------------
# derive_status
# ---------------------------------------------------------------------------


def test_all_success():
    summaries = [
        StageSummary(stage="collect", processed=5, succeeded=5),
        StageSummary(stage="analyze", processed=5, succeeded=5),
        StageSummary(stage="sync", processed=10, succeeded=10),
    ]
    assert derive_status(summaries) == "success"


def test_empty_cycle_is_success():
    summaries = [
        StageSummary(stage="collect", processed=0, succeeded=0),
        StageSummary(stage="analyze", processed=0, succeeded=0),
        StageSummary(stage="sync", processed=0, succeeded=0),
    ]
    assert derive_status(summaries) == "success"


def test_partial_failure():
    summaries = [
        StageSummary(stage="collect", processed=5, succeeded=3, failed=2),
        StageSummary(stage="analyze", processed=3, succeeded=3),
        StageSummary(stage="sync", processed=6, succeeded=6),
    ]
    assert derive_status(summaries) == "partial"


def test_fatal_with_some_success_is_partial():
    summaries = [
        StageSummary(stage="collect", fatal_error="auth failed"),
        StageSummary(stage="analyze", processed=3, succeeded=3),
        StageSummary(stage="sync", processed=6, succeeded=6),
    ]
    assert derive_status(summaries) == "partial"


def test_fatal_no_success_is_failed():
    summaries = [
        StageSummary(stage="collect", fatal_error="auth failed"),
        StageSummary(stage="analyze", processed=0, succeeded=0),
        StageSummary(stage="sync", processed=0, succeeded=0),
    ]
    assert derive_status(summaries) == "failed"


def test_all_stages_fatal_is_failed():
    summaries = [
        StageSummary(stage="collect", fatal_error="auth"),
        StageSummary(stage="analyze", fatal_error="config"),
        StageSummary(stage="sync", fatal_error="db"),
    ]
    assert derive_status(summaries) == "failed"


# ---------------------------------------------------------------------------
# compute_errors
# ---------------------------------------------------------------------------


def test_errors_no_failures():
    summaries = [
        StageSummary(stage="collect", succeeded=5),
        StageSummary(stage="analyze", succeeded=5),
    ]
    assert compute_errors(summaries) == 0


def test_errors_counts_failures():
    summaries = [
        StageSummary(stage="collect", succeeded=3, failed=2),
        StageSummary(stage="analyze", failed=1),
    ]
    assert compute_errors(summaries) == 3


def test_errors_counts_fatals():
    summaries = [
        StageSummary(stage="collect", fatal_error="auth failed"),
    ]
    assert compute_errors(summaries) == 1


def test_errors_combines_failures_and_fatals():
    summaries = [
        StageSummary(stage="collect", fatal_error="auth"),
        StageSummary(stage="analyze", failed=2),
        StageSummary(stage="sync", failed=1),
    ]
    assert compute_errors(summaries) == 4  # 1 fatal + 2 + 1


def test_failed_status_implies_nonzero_errors():
    """If status is failed, errors must be >= 1."""
    summaries = [
        StageSummary(stage="collect", fatal_error="auth"),
        StageSummary(stage="analyze"),
        StageSummary(stage="sync"),
    ]
    status = derive_status(summaries)
    errors = compute_errors(summaries)
    assert status == "failed"
    assert errors >= 1


# ---------------------------------------------------------------------------
# StageSummary defaults
# ---------------------------------------------------------------------------


def test_summary_defaults():
    s = StageSummary(stage="test")
    assert s.processed == 0
    assert s.succeeded == 0
    assert s.failed == 0
    assert s.skipped == 0
    assert s.fatal_error is None
