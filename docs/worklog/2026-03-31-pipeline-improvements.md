# Worklog: Pipeline Improvements — LLM 통합, 점진적 Sync

- 날짜: 2026-03-31
- 파일명: `2026-03-31-pipeline-improvements.md`
- 브랜치: feat/pipeline-improvements

## 계획

1. summary+critique 2회 LLM 호출 → 1회 통합 호출 (AnalysisResult)
2. max_tokens 1800 → 4096 (한국어 JSON 잘림 해결)
3. 모델 claude-sonnet-4-5 → claude-sonnet-4-6 업그레이드
4. 배치 파이프라인 → 기사별 analyze+sync 점진적 실행
5. Gemini fallback 체인 지원 추가

## 변경 파일

| 파일 | 변경 유형 | 설명 |
|---|---|---|
| `src/heisenberg_agent/llm/schemas.py` | 추가 | AnalysisResult 통합 스키마 |
| `src/heisenberg_agent/llm/prompts/analysis.md` | 신규 | 통합 프롬프트 |
| `config/llm_config.yaml` | 수정 | analysis 섹션, sonnet-4-6, max_tokens:4096, Gemini fallback |
| `src/heisenberg_agent/llm/client.py` | 수정 | 기본 모델 업데이트, fallback 체인 지원 |
| `src/heisenberg_agent/storage/models.py` | 수정 | analysis_json 컬럼 추가 |
| `src/heisenberg_agent/agents/analyzer.py` | 수정 | 단일 LLM 호출, find_targets/analyze_one 분리 |
| `src/heisenberg_agent/agents/sync_agent.py` | 수정 | sync_one per-article 메서드 추가 |
| `src/heisenberg_agent/storage/repositories/sync_jobs.py` | 수정 | find_pending_jobs_for_article 추가 |
| `src/heisenberg_agent/orchestrator/pipeline.py` | 수정 | 점진적 analyze+sync 실행 흐름 |
| `src/heisenberg_agent/settings.py` | 수정 | gemini_api_key 필드 추가 |
| `src/heisenberg_agent/main.py` | 수정 | GEMINI_API_KEY export 추가 |
| `tests/integration/test_analyze_save.py` | 수정 | AnalysisResult 기반 FakeLLMClient |
| `tests/integration/test_pipeline_run.py` | 수정 | Fake agents에 per-article 메서드 추가 |

## 테스트 결과

- unit: PASS (233 passed, 0 failed)
- contract: PASS
- live: SKIP (LLM/사이트 호출 필요)

## 리뷰 결과

- evaluator 판정: PASS (ship checklist 전항목 통과)

## 특이사항

- DB 마이그레이션: `ALTER TABLE analysis_runs ADD COLUMN analysis_json TEXT` 수동 실행
- 기존 summary_json/critique_json 컬럼은 하위호환 위해 유지 (통합 결과에서 분리 저장)
- sync_payload.py 변경 불필요 (summary_json/critique_json 그대로 읽음)

## 후속 작업

- [ ] Gemini API 키 발급 후 .env에 설정
- [ ] live smoke test: pipeline 1회 실행하여 한국어 출력, Notion 점진적 동기화 확인
- [ ] prompt_bundle_version 변경하여 기존 기사 재분석 트리거
