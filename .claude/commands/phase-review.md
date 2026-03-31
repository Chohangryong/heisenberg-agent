# Phase: Review

구현 완료 후 실행하는 리뷰 워크플로우다.

## 절차

### 1. 변경 사항 확인

- `git diff --name-only` 로 변경된 파일 목록을 확인한다.
- planner 계획 범위 밖의 파일이 변경되었는지 점검한다.

### 2. evaluator subagent 호출

- Agent tool을 사용하여 `.claude/agents/evaluator.md` subagent를 호출한다.
- subagent에게 전달할 정보:
  - 변경된 파일 목록
  - planner 계획 원문
  - 구현 시 특이사항 (있는 경우)
- evaluator는 다음을 수행한다:
  - `@.claude/checklists/ship-checklist.md` 항목별 통과 판정
  - `@.claude/checklists/ops-checklist.md` 항목별 통과 판정
  - `@CLAUDE.md` 절대 규칙·금지 패턴 대조
  - 설계서 정합성 확인

### 3. 피드백 반영

- evaluator가 FAIL을 반환하면:
  - 미통과 항목과 수정 제안을 확인한다.
  - phase-implement로 돌아가 generator subagent를 재호출하여 수정한다.
  - 수정 후 다시 phase-review를 실행한다.
- evaluator가 PASS를 반환하면 다음 단계로 진행한다.

### 4. 다음 단계

- PASS 확인 후 phase-finish로 넘어간다.

## 참조 파일

| 용도 | 경로 |
|---|---|
| evaluator subagent | `@.claude/agents/evaluator.md` |
| ship checklist | `@.claude/checklists/ship-checklist.md` |
| ops checklist | `@.claude/checklists/ops-checklist.md` |
| 프로젝트 규칙 | `@CLAUDE.md` |
| 설계서 | `@design/heisenberg_agent_design_v1.4.md` |
