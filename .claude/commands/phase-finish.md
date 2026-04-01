# Phase: Finish

리뷰 통과 후 실행하는 마무리 워크플로우다.

## 절차

### 1. 최종 점검

- `@.claude/checklists/ship-checklist.md` 항목을 최종 확인한다.
- `@.claude/checklists/live-smoke-checklist.md` 항목을 확인한다.
- live test가 필요한 변경인지 판단한다:
  - selector 변경, 로그인 로직 변경, 외부 API 연동 변경 → live smoke 대상
  - 내부 로직만 변경 → skip 가능
- live test 실행이 필요하면 사용자에게 확인 후 `pytest -m live` 를 실행한다.

### 2. worklog 생성

- `@.claude/templates/worklog-template.md` 를 기반으로 `docs/worklog/` 에 새 파일을 생성한다.
- 파일명 형식: `YYYY-MM-DD-{slug}.md`
  - slug: 영문 소문자, 숫자, 하이픈만 사용. 공백·슬래시·특수문자 불가.
  - 예: `2026-03-29-add-collector-retry.md`
- 다음을 기록한다:
  - planner 계획 요약
  - 실제 변경된 파일 목록
  - evaluator 리뷰 결과 요약
  - 특이사항·후속 작업

### 3. 커밋 준비

- 변경 파일을 확인하고 커밋 메시지 초안을 사용자에게 제시한다.
- 사용자 승인 후에만 커밋한다.
- worklog 파일도 커밋에 포함한다.

### 4. 완료 보고

- 변경 요약, 테스트 결과, 후속 작업을 사용자에게 보고한다.

## 참조 파일

| 용도 | 경로 |
|---|---|
| ship checklist | `@.claude/checklists/ship-checklist.md` |
| live smoke checklist | `@.claude/checklists/live-smoke-checklist.md` |
| worklog 템플릿 | `@.claude/templates/worklog-template.md` |
| 프로젝트 규칙 | `@CLAUDE.md` |
