# Ship Checklist

커밋/PR 전 필수 점검 항목. phase-implement와 phase-review에서 참조한다.

## 테스트

- [ ] `pytest tests/unit/` 전체 통과
- [ ] `pytest tests/contract/` 전체 통과
- [ ] 변경한 코드에 대응하는 테스트가 존재함
- [ ] 새 모듈 추가 시 테스트 파일도 동반 생성됨

## CLAUDE.md 절대 규칙

- [ ] 모든 쓰기는 SQLite 먼저
- [ ] Notion을 원본 저장소로 취급하지 않음
- [ ] 기사 본문은 `article_sections` 우선 사용
- [ ] 단계별 상태 컬럼 사용 (status 단일 컬럼 아님)
- [ ] LLM 응답은 JSON schema 기반 structured output
- [ ] selector, prompt, API version 하드코딩 없음
- [ ] 외부 예제 대량 복붙 없음
- [ ] Collector/Analyzer/Sync 책임 분리 유지
- [ ] sync 실패는 backlog 재시도 처리
- [ ] 새 파일 전 기존 모듈 경계 확인

## 금지 패턴

- [ ] raw response HTML과 rendered DOM 혼용 없음
- [ ] status 단일 컬럼 의존 없음
- [ ] Notion을 원본처럼 취급하지 않음
- [ ] selector 하드코딩 없음
- [ ] prompt 문자열 인라인 남발 없음
- [ ] sync 실패를 전체 실패로 승격하지 않음
- [ ] 외부 문서 대량 복붙 없음

## 폴더 규칙

- [ ] 외부 API 통신 → `src/heisenberg_agent/adapters/`
- [ ] 도메인 로직 → `src/heisenberg_agent/services/`
- [ ] DB 접근 → `src/heisenberg_agent/storage/repositories/`
- [ ] 프롬프트 → `src/heisenberg_agent/llm/prompts/`

## 코드 품질

- [ ] `print()` 사용 없음 (구조화 로그 사용)
- [ ] SQLAlchemy 2.0 스타일 사용
- [ ] selector는 `config/selectors/*.yaml` 에서만 관리
