# Heisenberg Agent

회원제 기술 콘텐츠 수집 → 구조화 분석 → 외부 동기화 파이프라인.

## Architecture

```
Collector → SQLite (SSOT) → Analyzer → SyncAgent
                                         ├── ChromaDB (vector search)
                                         └── Notion (read-only viewer)
```

- **SQLite**: 모든 기사 데이터의 Single Source of Truth
- **ChromaDB / Notion**: SQLite에서 파생되는 단방향 동기화 대상

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # 환경변수 설정
```

## Usage

```bash
python -m heisenberg_agent.main
```

## Development

```bash
ruff check src/ tests/
ruff format src/ tests/
mypy src/
pytest
pytest -m live  # live site smoke test (credentials 필요)
```

## Documentation

- 전체 설계: `design/heisenberg_agent_design_v1.4.md`
- 구현 참조 맵: `docs/implementation_reference_map.md`
- Claude Code 가이드: `CLAUDE.md`
