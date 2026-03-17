"""Unit tests for scheduler — pipeline factory + job wrapper."""

from heisenberg_agent.runtime.locks import LockError
from heisenberg_agent.scheduler import _run_pipeline_job


class FakePipeline:
    def __init__(self, *, should_fail: Exception | None = None):
        self.run_count = 0
        self._should_fail = should_fail

    def run(self) -> int:
        if self._should_fail:
            raise self._should_fail
        self.run_count += 1
        return 1


def _make_factory(pipeline: FakePipeline, cleanup_tracker: list | None = None):
    """Create a factory that returns the given pipeline + a tracking cleanup."""
    call_count = [0]

    def factory():
        call_count[0] += 1

        def cleanup():
            if cleanup_tracker is not None:
                cleanup_tracker.append(call_count[0])

        return pipeline, cleanup

    factory.call_count = call_count
    return factory


def test_job_calls_pipeline_run():
    pipeline = FakePipeline()
    factory = _make_factory(pipeline)
    _run_pipeline_job(factory)
    assert pipeline.run_count == 1


def test_job_creates_fresh_pipeline_per_call():
    """Each job invocation gets a distinct pipeline instance."""
    instances: list[FakePipeline] = []

    def factory():
        p = FakePipeline()
        instances.append(p)
        return p, lambda: None

    _run_pipeline_job(factory)
    _run_pipeline_job(factory)

    assert len(instances) == 2
    assert instances[0] is not instances[1]  # different objects
    assert instances[0].run_count == 1
    assert instances[1].run_count == 1


def test_job_catches_lock_error():
    """LockError is caught — scheduler keeps running."""
    pipeline = FakePipeline(should_fail=LockError("locked"))
    factory = _make_factory(pipeline)
    _run_pipeline_job(factory)  # should not raise


def test_job_catches_generic_exception():
    """Generic exceptions are caught — scheduler keeps running."""
    pipeline = FakePipeline(should_fail=RuntimeError("boom"))
    factory = _make_factory(pipeline)
    _run_pipeline_job(factory)  # should not raise


def test_cleanup_called_on_success():
    """Cleanup runs after successful job."""
    cleanups: list[int] = []
    pipeline = FakePipeline()
    factory = _make_factory(pipeline, cleanup_tracker=cleanups)
    _run_pipeline_job(factory)
    assert len(cleanups) == 1


def test_cleanup_called_on_failure():
    """Cleanup runs even when pipeline fails."""
    cleanups: list[int] = []
    pipeline = FakePipeline(should_fail=RuntimeError("boom"))
    factory = _make_factory(pipeline, cleanup_tracker=cleanups)
    _run_pipeline_job(factory)
    assert len(cleanups) == 1


def test_cleanup_called_on_lock_error():
    """Cleanup runs even on LockError."""
    cleanups: list[int] = []
    pipeline = FakePipeline(should_fail=LockError("locked"))
    factory = _make_factory(pipeline, cleanup_tracker=cleanups)
    _run_pipeline_job(factory)
    assert len(cleanups) == 1
