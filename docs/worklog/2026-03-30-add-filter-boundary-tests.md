# Worklog: collector _filter() 경계 edge case 테스트 추가

- 날짜: 2026-03-30
- 파일명: `2026-03-30-add-filter-boundary-tests.md` (영문 소문자, 숫자, 하이픈만)
- 브랜치: feat/claude-harness

## 계획

collector `_filter()` 메서드의 safety window 경계 edge case를 테스트로 문서화한다.
- `collected_at == cutoff` → SKIP (경계 exclusive, `>` 연산자)
- `collected_at == cutoff + 1초` → RECHECK

## 변경 파일

| 파일 | 변경 유형 | 설명 |
|---|---|---|
| tests/unit/test_collector_filter.py | 테스트 추가 | edge case 테스트 2개 추가 (기존 4개 유지) |

## 테스트 결과

- unit: PASS (6/6)
- contract: SKIP (변경 없음)
- live: SKIP (내부 테스트만)

## 리뷰 결과

- evaluator 판정: PASS
- 주요 피드백: 없음. ship-checklist, ops-checklist, CLAUDE.md 규칙 전항목 통과.

## 특이사항

- `now_utc`를 monkeypatch로 고정하여 결정론적 경계값 테스트 구현
- 패치 대상: `heisenberg_agent.agents.collector.now_utc` (사용처 모듈 레벨 패칭)
- `collected_at`은 NOT NULL 컬럼이므로 None 케이스 테스트는 제외
- 이 작업은 Claude Code harness 워크플로우 (phase-start → implement → review → finish) 검증용으로 수행됨

## 후속 작업

- [ ] selector_profile_version drift 감지 로직 구현 (현재 저장만 되고 비교에 사용되지 않음)
