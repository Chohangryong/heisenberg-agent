
# CLAUDE.md

이 프로젝트는 로그인 기반 기사 수집 → 구조화 분석 → 외부 동기화 파이프라인이다.  
상세 설계는 `@docs/design/heisenberg_agent_design_v1.4.md` 를 기준으로 한다.

## 프로젝트 개요

- 대상: `heisenberg.kr`
- 목적: 기사 수집, 구조화 분석, SQLite 저장, ChromaDB/Notion 동기화
- 원칙: **SQLite가 SSOT**, Notion/ChromaDB는 파생 저장소

## 아키텍처

- `CollectorAgent`: 로그인, 목록/상세 수집, 렌더 DOM 파싱, PDF 스냅샷
- `AnalyzerAgent`: structured output 기반 요약/비판 분석
- `SyncAgent`: ChromaDB + Notion 동기화
- `Pipeline`: 실행 순서, 락, 종료 리포트

## 절대 규칙

1. 모든 쓰기는 **SQLite 먼저**.
2. Notion은 **뷰어** 다. 원본 저장소처럼 다루지 말 것.
3. 기사 본문은 `articles.body_text` 하나만 보지 말고 `article_sections` 를 우선 사용할 것.
4. `status` 단일 컬럼으로 상태를 처리하지 말 것. 단계별 상태 컬럼 사용.
5. LLM 응답은 자유 텍스트보다 **JSON schema 기반 structured output** 을 우선할 것.
6. selector, prompt, API version은 하드코딩하지 말고 설정/문서로 분리할 것.
7. 외부 예제 코드를 길게 복사하지 말고, 공식 문서나 신뢰 레포의 **정확한 위치**를 남길 것.
8. Collector/Analyzer/Sync 책임을 섞지 말 것.
9. sync 실패는 전체 파이프라인 실패가 아니라 backlog 재시도 대상으로 처리할 것.
10. 새 파일/모듈을 만들기 전에 기존 모듈 경계를 먼저 확인할 것.

## 구현 우선순위

1. 수집 1건 성공
2. 분석 1건 성공
3. vector sync 1건 성공
4. notion sync 1건 성공
5. retry / resume / no-op sync 구현
6. drift / backup / health report 구현

## 수집 규칙

- 로그인 이후 HTML의 기준은 **rendered DOM**
- 상세 페이지 파싱은 Playwright `page.content()` 결과 기준
- `networkidle` 대신 ready selector + timeout 사용
- PDF는 `screen` media 기준을 우선 고려
- selector는 `config/selectors/*.yaml` 에서만 관리

## 분석 규칙

- `analysis_runs` 에 이력 저장
- `articles.current_analysis_id` 로 최신 분석 연결
- 재분석 기준:
  - `content_hash` 변경
  - `analysis_version` 변경
  - `prompt_bundle_version` 변경
- evidence span 포함 구조를 유지할 것

## 동기화 규칙

- Notion/Chroma는 `SyncAgent` 만 접근
- payload hash 동일 시 no-op skip
- Notion API 버전은 어댑터에서 고정
- sync backlog는 `sync_jobs` 로 관리

## 데이터베이스 규칙

- SQLAlchemy 2.0 스타일 사용
- migration 기반 변경만 허용
- SQLite 연결 시 다음 기본값 적용:
  - `PRAGMA foreign_keys=ON`
  - `PRAGMA journal_mode=WAL`
- 사용자 메모/읽음 상태는 `article_annotations` 에 둔다
- summary/critique를 `articles` 에 직접 누적 저장하지 말고 `analysis_runs` 이력을 유지한다

## 폴더 규칙

- 외부 API 통신: `src/heisenberg_agent/adapters/`
- 도메인 로직: `src/heisenberg_agent/services/`
- DB 접근: `src/heisenberg_agent/storage/repositories/`
- 프롬프트: `src/heisenberg_agent/llm/prompts/`
- 설계/참조 문서: `docs/`

## 로깅/테스트 규칙

- `print()` 금지, 구조화 로그 사용
- live test는 기본 CI에서 돌리지 말 것
- HTML fixture contract test를 유지할 것
- selector drift와 prompt drift를 구분해서 테스트할 것

## 실행 명령(예시)

- 설치: 프로젝트 `README.md` 기준
- 수동 실행: `python -m heisenberg_agent.main --mode manual`
- 스케줄러: `python -m heisenberg_agent.scheduler`
- 테스트: `pytest`
- live test: `pytest -m live`

## 참조 문서

- 전체 설계: `@docs/design/heisenberg_agent_design_v1.4.md`
- 구현 참조 맵: `@docs/references/implementation_reference_map.md`

## 금지 패턴

- raw response HTML과 rendered DOM 혼용
- status 단일 컬럼 의존
- Notion을 원본처럼 취급
- selector 하드코딩
- prompt 문자열 인라인 남발
- sync 실패를 전체 실패로 승격
- 외부 문서 대량 복붙

## 변경 전 체크

다음 변경 전에는 반드시 설계서를 다시 본다.

- DB 스키마 변경
- Notion 속성 변경
- LLM 출력 스키마 변경
- selector profile 변경
- sync 책임 변경
