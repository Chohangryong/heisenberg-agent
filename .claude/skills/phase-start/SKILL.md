---
name: phase-start
description: 작업 착수 단계. 요구사항 확인, planner subagent 호출, 사용자 승인.
---

# Phase: Start

작업 착수 시 실행하는 워크플로우다.

## 절차

### 1. 요구사항 확인

- 사용자의 요구사항을 정리한다.
- `@CLAUDE.md` 의 "변경 전 체크" 항목에 해당하는지 확인한다:
  - DB 스키마 변경 / Notion 속성 변경 / LLM 출력 스키마 변경 / selector profile 변경 / sync 책임 변경
  - 해당 시 설계서 `@design/heisenberg_agent_design_v1.4.md` 를 먼저 읽는다.

### 2. 이전 작업 맥락 확인

- `docs/worklog/` 디렉토리에서 **가장 최근 파일 1개만** 읽는다.
  ```
  ls -t docs/worklog/ | head -1
  ```
- 디렉토리가 없거나 비어있으면 skip 한다. 전체 디렉토리를 읽지 않는다.

### 3. planner subagent 호출

- Agent tool을 사용하여 `.claude/agents/planner.md` subagent를 호출한다.
- subagent에게 전달할 정보:
  - 사용자 요구사항 원문
  - 이전 worklog 맥락 (있는 경우)
  - 변경 전 체크 해당 여부
- planner는 읽기 전용으로 동작하며, 구현 계획을 반환한다.

### 4. 사용자 확인

- planner가 반환한 계획을 사용자에게 제시하고 승인을 받는다.
- 승인 후 phase-implement로 넘어간다.

## 참조 파일

| 용도 | 경로 |
|---|---|
| 프로젝트 규칙 | `@CLAUDE.md` |
| 설계서 | `@design/heisenberg_agent_design_v1.4.md` |
| 구현 참조 맵 | `@docs/implementation_reference_map.md` |
| planner subagent | `@.claude/agents/planner.md` |
