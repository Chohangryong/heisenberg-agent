# Live Smoke Checklist

라이브 환경 스모크 테스트 항목. phase-finish에서 참조한다.
`pytest -m live` 실행 전 사용자 승인 필요.

## 대상 판단

다음 중 하나라도 변경되었으면 live smoke 대상이다:
- selector profile (`config/selectors/*.yaml`)
- 로그인 로직 (`src/heisenberg_agent/scrapers/`, `src/heisenberg_agent/adapters/`)
- Notion/ChromaDB 연동 (`src/heisenberg_agent/adapters/`)
- 파이프라인 실행 순서 (`src/heisenberg_agent/orchestrator/`)

## 점검 항목

### 수집 (Collector)

- [ ] 로그인 성공
- [ ] 목록 페이지 기사 1건 이상 파싱
- [ ] 상세 페이지 본문 추출
- [ ] `article_sections` 정상 저장
- [ ] PDF 스냅샷 생성 (해당 시)

### 분석 (Analyzer)

- [ ] 수집된 기사 1건 분석 성공
- [ ] `analysis_runs` 이력 생성
- [ ] structured output 스키마 일치

### 동기화 (Sync)

- [ ] ChromaDB upsert 성공
- [ ] Notion 페이지 생성/갱신 성공
- [ ] payload hash 동일 시 no-op skip 동작

### 파이프라인

- [ ] 종료 리포트 정상 출력
- [ ] 에러 발생 시 backlog 재시도 대상으로 처리됨
