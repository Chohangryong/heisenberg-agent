# Heisenberg Agent

회원제 기술 콘텐츠(heisenberg.kr) 수집 → 구조화 분석 → 외부 동기화 파이프라인.

## Architecture

```
Collector → SQLite (SSOT) → Analyzer → SyncAgent
                                         ├── ChromaDB (vector search)
                                         └── Notion (read-only viewer)
```

- **SQLite**: 모든 기사 데이터의 Single Source of Truth
- **ChromaDB / Notion**: SQLite에서 파생되는 단방향 동기화 대상

## Requirements

- Python 3.11+
- Playwright (Chromium)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium

cp .env.example .env
# .env 파일을 열고 credentials 입력
```

### .env 설정

| 변수 | 용도 | 필수 |
|---|---|---|
| `HEISENBERG_USERNAME_OR_EMAIL` | heisenberg.kr 로그인 계정 | O |
| `HEISENBERG_PASSWORD` | heisenberg.kr 로그인 비밀번호 | O |
| `ANTHROPIC_API_KEY` | Claude API (primary analyzer) | O |
| `OPENAI_API_KEY` | OpenAI API (fallback analyzer, embedding) | O |
| `NOTION_API_KEY` | Notion integration token | sync 사용 시 |
| `NOTION_PARENT_PAGE_ID` | Notion 동기화 대상 페이지 ID | sync 사용 시 |
| `LOG_LEVEL` | 로그 레벨 (default: INFO) | - |

## Usage

```bash
# Full pipeline 1회 실행 (collect → analyze → sync)
python -m heisenberg_agent.main

# 수동 모드 (동일)
python -m heisenberg_agent.main --mode manual

# Cron 스케줄러 (08:00, 13:00, 19:00 KST)
python -m heisenberg_agent.scheduler
```

## Configuration

| 파일 | 역할 |
|---|---|
| `config/settings.yaml` | 수집 주기, DB 경로, sync 설정 등 |
| `config/llm_config.yaml` | LLM provider/model, fallback, temperature |
| `config/selectors/heisenberg.yaml` | CSS selector profile (사이트 구조 변경 시 수정) |

## Project Structure

```
src/heisenberg_agent/
├── agents/          # Collector, Analyzer, SyncAgent
├── adapters/        # Playwright, Notion, ChromaDB 어댑터
├── llm/             # LLM client, schemas, prompts
├── orchestrator/    # Pipeline (실행 순서, 락, 리포트)
├── parsers/         # Section 파서 (DOM → 구조화 데이터)
├── scrapers/        # List/detail page HTML 파서
├── storage/         # SQLAlchemy models, repositories, migrations
├── utils/           # Hashing, datetime, logging
├── main.py          # CLI 진입점
└── scheduler.py     # APScheduler cron
```

## Testing

```bash
# Unit + integration + contract tests
pytest

# Live smoke test (실제 사이트 접속, credentials 필요)
LIVE_SMOKE=1 pytest -m live -v
```

Live smoke는 실제 heisenberg.kr에 로그인하여 collect → analyze 전 구간을 검증한다.
`ANTHROPIC_API_KEY` 없이도 OpenAI fallback으로 통과 가능.

## Documentation

- 전체 설계: `docs/design/heisenberg_agent_design_v1.4.md`
- 구현 참조 맵: `docs/references/implementation_reference_map.md`
- Claude Code 가이드: `CLAUDE.md`
