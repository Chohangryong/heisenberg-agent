# Changelog

All notable changes to this project will be documented in this file.
Format based on [Keep a Changelog](https://keepachangelog.com/).

## [0.1.0] - 2026-03-17

### Added
- **Collector**: Playwright 기반 로그인, 목록/상세 수집, PDF 스냅샷
- **Analyzer**: Claude (primary) + GPT-4o (fallback) structured output 분석
- **SyncAgent**: ChromaDB vector sync + Notion one-way sync
- **Pipeline**: collect → analyze → sync 오케스트레이션, 파일 락, 리포트
- **Scheduler**: APScheduler cron (08:00, 13:00, 19:00 KST)
- **Selector v2**: heisenberg.kr 실제 DOM 구조 기반 CSS selector profile
- **Live smoke tests**: auth → list → detail → collect → analyze 전 구간 검증
- **Contract tests**: HTML fixture 기반 parser 계약 테스트

### Fixed
- Collector discover 단계 cross-page 중복 기사 dedupe (url 기준)
- IntegrityError 시 동일 기사 duplicate absorption (에러 카운트 미증가)
- OpenAI strict structured output 호환 (`additionalProperties: false`, `required` 보정)
