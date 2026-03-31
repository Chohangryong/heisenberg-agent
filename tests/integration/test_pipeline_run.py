"""Integration tests for Pipeline — fake agents + test SQLite."""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from heisenberg_agent.orchestrator.pipeline import Pipeline, StageSummary
from heisenberg_agent.runtime.locks import LockError
from heisenberg_agent.storage.models import CollectionRun


# ---------------------------------------------------------------------------
# Fake agents
# ---------------------------------------------------------------------------


@dataclass
class FakeRunResult:
    """Mimics CollectorAgent.run() return (CollectionRun-like)."""
    articles_found: int = 0
    articles_collected: int = 0
    errors: int = 0


class FakeCollectorAgent:
    def __init__(
        self, found: int = 0, collected: int = 0, errors: int = 0,
        fatal: str | None = None,
    ):
        self._found = found
        self._collected = collected
        self._errors = errors
        self._fatal = fatal

    def run(self, **kw):
        if self._fatal:
            raise RuntimeError(self._fatal)
        return FakeRunResult(
            articles_found=self._found,
            articles_collected=self._collected,
            errors=self._errors,
        )


class _FakeArticle:
    """Minimal article stub for fake pipeline tests."""
    def __init__(self, id: int = 1):
        self.id = id
        self.current_analysis_id = id


class FakeAnalyzerAgent:
    def __init__(
        self, analyzed: int = 0, skipped: int = 0, failed: int = 0,
        fatal: str | None = None,
    ):
        self._analyzed = analyzed
        self._skipped = skipped
        self._failed = failed
        self._fatal = fatal

    def run(self, **kw):
        if self._fatal:
            raise RuntimeError(self._fatal)
        return {
            "analyzed": self._analyzed,
            "skipped": self._skipped,
            "failed": self._failed,
        }

    def find_targets(self):
        if self._fatal:
            raise RuntimeError(self._fatal)
        total = self._analyzed + self._skipped + self._failed
        return [_FakeArticle(i) for i in range(total)]

    def analyze_one(self, article):
        if self._analyzed > 0:
            self._analyzed -= 1
            return "analyzed"
        if self._skipped > 0:
            self._skipped -= 1
            return "skipped"
        if self._failed > 0:
            self._failed -= 1
            return "failed"
        return "skipped"


class FakeSyncAgent:
    def __init__(
        self, synced: int = 0, skipped: int = 0, failed: int = 0,
        fatal: str | None = None,
    ):
        self._synced = synced
        self._skipped = skipped
        self._failed = failed
        self._fatal = fatal
        self._notion_rate_limited = False

    def run(self, **kw):
        if self._fatal:
            raise RuntimeError(self._fatal)
        return {
            "ensured": self._synced + self._skipped + self._failed,
            "synced": self._synced,
            "skipped": self._skipped,
            "failed": self._failed,
        }

    def sync_one(self, article):
        if self._fatal:
            raise RuntimeError(self._fatal)
        result = {"ensured": 0, "synced": 0, "skipped": 0, "failed": 0, "deferred": 0}
        if self._synced > 0:
            self._synced -= 1
            result["ensured"] = 1
            result["synced"] = 2  # vector + notion
        return result

    @property
    def is_notion_rate_limited(self):
        return self._notion_rate_limited


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pipeline_full_success(db_session: Session, tmp_path: Path):
    pipeline = Pipeline(
        session=db_session,
        collector=FakeCollectorAgent(found=5, collected=5),
        analyzer=FakeAnalyzerAgent(analyzed=5),
        syncer=FakeSyncAgent(synced=10),
        lock_path=str(tmp_path / "test.lock"),
    )
    run_id = pipeline.run()

    run = db_session.get(CollectionRun, run_id)
    assert run.status == "success"
    assert run.trigger_type == "pipeline"
    assert run.articles_found == 5
    assert run.articles_collected == 5
    assert run.articles_analyzed == 5
    assert run.errors == 0
    assert run.finished_at is not None

    # report_json has stages
    report = json.loads(run.report_json)
    assert "stages" in report
    stages = report["stages"]
    assert len(stages) == 3
    stage_names = [s["stage"] for s in stages]
    assert "collect" in stage_names
    assert "analyze" in stage_names
    assert "sync" in stage_names


def test_pipeline_partial_failure(db_session: Session, tmp_path: Path):
    pipeline = Pipeline(
        session=db_session,
        collector=FakeCollectorAgent(found=5, collected=3, errors=2),
        analyzer=FakeAnalyzerAgent(analyzed=3),
        syncer=FakeSyncAgent(synced=6),
        lock_path=str(tmp_path / "test.lock"),
    )
    run_id = pipeline.run()

    run = db_session.get(CollectionRun, run_id)
    assert run.status == "partial"
    assert run.errors == 2
    assert run.articles_collected == 3


def test_pipeline_collect_fatal_analyze_continues(db_session: Session, tmp_path: Path):
    """Collect fatal error does not prevent analyze/sync from running."""
    pipeline = Pipeline(
        session=db_session,
        collector=FakeCollectorAgent(fatal="auth failed"),
        analyzer=FakeAnalyzerAgent(analyzed=3),
        syncer=FakeSyncAgent(synced=6),
        lock_path=str(tmp_path / "test.lock"),
    )
    run_id = pipeline.run()

    run = db_session.get(CollectionRun, run_id)
    assert run.status == "partial"  # fatal + some success
    assert run.errors >= 1
    assert run.articles_analyzed == 3  # analyze ran despite collect fatal

    report = json.loads(run.report_json)
    collect_stage = [s for s in report["stages"] if s["stage"] == "collect"][0]
    assert collect_stage["fatal_error"] is not None


def test_pipeline_lock_prevents_concurrent(db_session: Session, tmp_path: Path):
    """Second pipeline run fails with LockError while first holds lock."""
    lock_path = str(tmp_path / "test.lock")

    # First pipeline — use a collector that blocks (simulate via lock file pre-creation)
    from heisenberg_agent.runtime.locks import acquire
    handle = acquire(lock_path)

    # Second pipeline should fail
    pipeline2 = Pipeline(
        session=db_session,
        collector=FakeCollectorAgent(),
        analyzer=FakeAnalyzerAgent(),
        syncer=FakeSyncAgent(),
        lock_path=lock_path,
    )
    with pytest.raises(LockError):
        pipeline2.run()

    # Release first lock
    from heisenberg_agent.runtime.locks import release
    release(handle)


def test_pipeline_report_json_structure(db_session: Session, tmp_path: Path):
    """report_json contains proper stage summaries."""
    pipeline = Pipeline(
        session=db_session,
        collector=FakeCollectorAgent(found=3, collected=2, errors=1),
        analyzer=FakeAnalyzerAgent(analyzed=2, skipped=1, failed=0),
        syncer=FakeSyncAgent(synced=3, skipped=1, failed=0),
        lock_path=str(tmp_path / "test.lock"),
    )
    run_id = pipeline.run()

    run = db_session.get(CollectionRun, run_id)
    report = json.loads(run.report_json)

    for stage in report["stages"]:
        assert "stage" in stage
        assert "processed" in stage
        assert "succeeded" in stage
        assert "failed" in stage
        assert "skipped" in stage
        assert "fatal_error" in stage


def test_pipeline_lock_released_on_error(db_session: Session, tmp_path: Path):
    """Lock is released even if pipeline raises an unexpected error."""
    lock_path = str(tmp_path / "test.lock")

    class ExplodingAgent:
        def run(self, **kw):
            raise RuntimeError("unexpected explosion")

    pipeline = Pipeline(
        session=db_session,
        collector=ExplodingAgent(),
        analyzer=FakeAnalyzerAgent(),
        syncer=FakeSyncAgent(),
        lock_path=lock_path,
    )

    # Pipeline should complete (error caught per-stage)
    run_id = pipeline.run()

    # Lock should be released
    assert not Path(lock_path).exists()

    run = db_session.get(CollectionRun, run_id)
    assert run.status == "failed"  # all stages have fatal or zero success
