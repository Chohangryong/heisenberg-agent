# Worklog: sync hardening batch 1 — safe unlock, ordering, naive UTC 통일

- 날짜: 2026-03-30
- 파일명: `2026-03-30-sync-hardening-batch1.md`
- 브랜치: feat/sync-hardening-next
- 커밋: 3383cf2

## 계획

sync production hardening의 Batch 1을 진행한다.
우선순위: safe unlock > ORDER BY > now_utc 통일.

## 변경 파일

| 파일 | 변경 유형 | 설명 |
|---|---|---|
| src/heisenberg_agent/agents/sync_agent.py | 버그 수정 | `_process_target` finally: rollback 후 `session.refresh` 실패 시 `force_unlock` 폴백 |
| src/heisenberg_agent/storage/repositories/sync_jobs.py | 버그 수정 + 개선 | `_now_naive_utc()` 헬퍼 추가, 5개 call site 통일. `find_pending_jobs`에 ORDER BY. `force_unlock()` 추가. `datetime.utcnow()` 제거 |
| tests/unit/test_sync_jobs_repo.py | 테스트 추가 | `test_find_pending_jobs_ordered_by_created_at` |
| tests/integration/test_sync_flow.py | 테스트 추가 | `test_process_target_unexpected_error_unlocks_job`, `test_process_target_unexpected_error_no_stale_lock` |

## 테스트 결과

- unit: PASS (전체)
- integration: PASS (전체)
- 전체: 223 passed (11.78s)
- live: SKIP (외부 API 미사용)

## 리뷰 결과

- evaluator 판정: PASS (한 가지 수정 후 승인 → 수정 완료)
- 주요 피드백: `find_pending_jobs`에서 aware datetime으로 비교하던 문제 → `_now_naive_utc()` 헬퍼로 repository 전체 통일

## 특이사항

- `datetime.utcnow()` (Python 3.12 deprecated) 잔존 0건 확인
- `_now_naive_utc()`는 sync_jobs repository 내부 전용. 앱/도메인 레벨은 aware UTC 유지
- `force_unlock`은 ORM 객체 없이 직접 UPDATE로 구현 — detached instance 문제 완전 회피

## 후속 작업

- [ ] Batch 2-1: Notion adapter transient retry (tenacity, 429 제외)
- [ ] Batch 2-2: SyncAgent.run() target별 stats 반환 (notion 카운터 버그)
- [ ] Batch 2-3: payload 크기 사전 검증 (max_blocks)
- [ ] Batch 2-4 (micro-batch): collected_at payload key 정리
- [ ] Batch 3: health report, drift detection, dry_run, backpressure
