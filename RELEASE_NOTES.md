# v0.1.0 — Heisenberg Agent MVP

## Overview

heisenberg.kr 회원제 기술 콘텐츠를 자동 수집하고, LLM으로 구조화 분석한 뒤,
ChromaDB/Notion에 동기화하는 파이프라인의 최소 동작 버전(MVP).

## What's included

- **Collector**: Playwright 로그인 → 목록 페이지 순회 → 상세 수집 → section 파싱 → SQLite 저장
- **Analyzer**: summary + critique 구조화 분석 (Claude primary, GPT-4o fallback)
- **SyncAgent**: ChromaDB vector upsert + Notion page sync (one-way)
- **Pipeline**: collect → analyze → sync 순차 실행, 파일 락, 완료 리포트
- **Scheduler**: APScheduler cron (하루 3회)
- **Live smoke**: 실제 사이트 대상 전 구간 검증 테스트

## Known limitations

- Sync는 scaffolding 완료, production 환경에서의 안정성은 미검증
- Notion sync의 rate limit backoff은 구현되었으나 대량 동기화 미테스트
- ChromaDB embedding은 OpenAI text-embedding-3-small 단일 모델
- 스케줄러 재시작 시 missed job recovery는 APScheduler misfire grace에 의존
- selector profile은 사이트 구조 변경 시 수동 업데이트 필요

## Next (v0.2.0 방향)

- Notion/ChromaDB sync production 안정화
- selector drift 자동 감지
- 분석 품질 평가 및 prompt 개선
- 멀티 사이트 확장 준비
