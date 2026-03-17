
# Implementation Reference Map

이 문서는 구현 시 먼저 확인할 **공식 문서 / 신뢰 가능한 공개 레포 경로**를 모아둔 참조 맵이다.  
원칙은 **공식 문서 → 공식/사실상 표준 SDK → 검증된 공개 템플릿** 순서다.

## 1. Playwright

| 용도 | 위치 | URL |
|---|---|---|
| 인증 상태 저장/재사용 | Playwright Python Docs → `Authentication` | https://playwright.dev/python/docs/auth |
| 페이지 PDF 생성 | Playwright Python Docs → `Page API` → `page.pdf()` | https://playwright.dev/python/docs/api/class-page |
| 미디어 에뮬레이션 | Playwright Python Docs → `Page API` → `page.emulate_media()` | https://playwright.dev/python/docs/api/class-page |

## 2. Notion

| 용도 | 위치 | URL |
|---|---|---|
| 요청 제한 | Notion Docs → `Request limits` | https://developers.notion.com/reference/request-limits |
| Data source 개념 | Notion Docs → `Data source` | https://developers.notion.com/reference/data-source |
| API 변경 이력 | Notion Docs → `Changelog` | https://developers.notion.com/page/changelog |
| 2026-03-11 업그레이드 | Notion Docs → `Upgrade guide 2026-03-11` | https://developers.notion.com/guides/get-started/upgrade-guide-2026-03-11 |
| Python SDK | `ramnes/notion-sdk-py` → repo root README | https://github.com/ramnes/notion-sdk-py |

## 3. ChromaDB

| 용도 | 위치 | URL |
|---|---|---|
| 데이터 추가 | Chroma Docs → `Collections` → `Adding Data` | https://docs.trychroma.com/docs/collections/add-data |
| update / upsert | Chroma Docs → `Collections` → `Update Data` | https://docs.trychroma.com/docs/collections/update-data |
| collection 관리 | Chroma Docs → `Collections` → `Manage Collections` | https://docs.trychroma.com/docs/collections/manage-collections |

## 4. SQLAlchemy / SQLite

| 용도 | 위치 | URL |
|---|---|---|
| SQLAlchemy 2.0 스타일 | SQLAlchemy Docs → `Unified Tutorial` | https://docs.sqlalchemy.org/en/20/tutorial/index.html |
| ORM quick start | SQLAlchemy Docs → `ORM Quick Start` | https://docs.sqlalchemy.org/orm/quickstart.html |
| SQLite foreign keys | SQLite Docs → `Foreign Key Support` | https://sqlite.org/foreignkeys.html |
| SQLite WAL | SQLite Docs → `Write-Ahead Logging` | https://sqlite.org/wal.html |
| SQLite FTS5 | SQLite Docs → `FTS5 Extension` | https://www.sqlite.org/fts5.html |

## 5. 설정 / 재시도 / 로깅

| 용도 | 위치 | URL |
|---|---|---|
| 환경변수 설정 관리 | Pydantic Docs → `Settings Management` | https://docs.pydantic.dev/latest/concepts/pydantic_settings/ |
| 재시도 | Tenacity Docs → homepage / examples | https://tenacity.readthedocs.io/ |
| 구조화 로그 | structlog Docs → homepage | https://www.structlog.org/ |

## 6. LLM

| 용도 | 위치 | URL |
|---|---|---|
| LiteLLM 시작점 | LiteLLM Docs → `Getting Started` | https://docs.litellm.ai/docs/ |
| LiteLLM routing/fallback | LiteLLM Docs → `Routing` | https://docs.litellm.ai/docs/routing |
| LiteLLM structured outputs | LiteLLM Docs → `Structured Outputs (JSON Mode)` | https://docs.litellm.ai/docs/completion/json_mode |
| LiteLLM exception mapping | LiteLLM Docs → `Exception Mapping` | https://docs.litellm.ai/docs/exception_mapping |
| Anthropic structured outputs | Claude API Docs → `Structured outputs` | https://platform.claude.com/docs/en/build-with-claude/structured-outputs |
| Anthropic prompt guidance | Claude API Docs → `Prompting best practices` | https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices |

## 7. Scheduler / Claude Code / 공개 템플릿

| 용도 | 위치 | URL |
|---|---|---|
| 스케줄러 동시 실행 / misfire | APScheduler Docs → `User guide` | https://apscheduler.readthedocs.io/en/3.x/userguide.html |
| CLAUDE.md 범위/로딩 | Claude Code Docs → `How Claude remembers your project` | https://code.claude.com/docs/en/memory |
| CLAUDE.md 작성 팁 | Claude Code Docs → `Best Practices` | https://code.claude.com/docs/en/best-practices |
| Python 프로젝트 템플릿 | `johnthagen/python-blueprint` → repo root README, `pyproject.toml`, `.github/workflows/ci.yml`, `noxfile.py` | https://github.com/johnthagen/python-blueprint |
| Python 패키지 템플릿 | `microsoft/python-package-template` → repo root README, `pyproject.toml`, `.github/workflows/CI.yml` | https://github.com/microsoft/python-package-template |

## 8. 사이트 확인용 URL

| 용도 | 위치 | URL |
|---|---|---|
| 로그인 페이지 | `heisenberg.kr/login/` | https://heisenberg.kr/login/ |
| 최신 기사 목록 | `heisenberg.kr/latest/` | https://heisenberg.kr/latest/ |
| 샘플 기사 구조 확인 | `grail_samsung` | https://heisenberg.kr/grail_samsung/ |

## 9. 사용 원칙

- 외부 코드 예제를 그대로 가져오지 않는다.
- 구현 전 먼저 이 문서의 URL을 열고 현재 시점 기준 사양을 재확인한다.
- 버전이 바뀐 경우, 설계서와 CLAUDE.md를 함께 갱신한다.
