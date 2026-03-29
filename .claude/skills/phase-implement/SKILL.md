---
name: phase-implement
description: 구현 단계. generator subagent 호출, 코드 작성, 테스트 실행.
---

# Phase: Implement

planner 계획이 승인된 후 실행하는 구현 워크플로우다.

## 절차

### 1. 계획 확인

- phase-start에서 승인된 planner 계획을 확인한다.
- 작업 목록·대상 파일·의존성·변경 범위를 파악한다.

### 2. generator subagent 호출

- Agent tool을 사용하여 `.claude/agents/generator.md` subagent를 호출한다.
- subagent에게 전달할 정보:
  - 승인된 planner 계획 전문
  - 작업 대상 파일 목록
  - 변경 범위 제약 사항
- generator는 계획 범위 내에서만 코드를 작성하고, 대응 테스트를 동반 생성한다.

### 3. 테스트 실행

- generator 완료 후 테스트를 실행한다:
  ```
  pytest tests/unit/ -v
  pytest tests/contract/ -v
  ```
- 실패 시 generator subagent를 재호출하여 수정한다.
- live test (`pytest -m live`)는 이 단계에서 실행하지 않는다.

### 4. 자체 점검

- `@.claude/checklists/ship-checklist.md` 항목을 대조하여 누락을 확인한다.
- 누락 항목이 있으면 generator를 재호출하여 보완한다.

### 5. 다음 단계

- 자체 점검 통과 후 phase-review로 넘어간다.

## 참조 파일

| 용도 | 경로 |
|---|---|
| generator subagent | `@.claude/agents/generator.md` |
| ship checklist | `@.claude/checklists/ship-checklist.md` |
| 프로젝트 규칙 | `@CLAUDE.md` |
