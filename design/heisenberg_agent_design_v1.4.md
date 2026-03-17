
# 회원제 기술 콘텐츠 수집·분석·동기화 에이전트 시스템 설계서

> **문서 버전**: v1.4  
> **작성일**: 2026-03-17  
> **문서 성격**: Claude Code 구현용 설계서 + 운영/유지보수 기준서  
> **대상 1차 사이트**: `heisenberg.kr`  
> **목표**: 로그인 기반 기사 수집 → 구조화 분석 → SQLite SSOT 저장 → ChromaDB 검색 색인 → Notion 읽기 전용 뷰어 동기화

---

## 0. 문서 작성 원칙

이 문서는 **바로 구현에 들어갈 수 있는 수준의 설계 기준서**를 목표로 한다.  
문서 안의 예시 코드는 최소화하고, 외부 자료는 **본문 대량 인용 대신 “공식 문서 URL + 정확한 위치(섹션/파일 경로)”**만 남긴다. 구현자는 해당 위치를 열어 최신 사양을 확인한 뒤 반영한다.

### 0.1 문서 사용 방법

- **설계 판단 기준**은 본 문서가 우선이다.
- 기술별 API 사용법은 문서 마지막의 **참조 맵(§14)** 에서 공식 문서를 먼저 찾는다.
- `CLAUDE.md`에는 본 문서의 핵심 제약만 요약하고, 세부 내용은 본 문서를 참조하도록 한다.
- 외부 블로그/커뮤니티 글은 아이디어 확인용으로만 사용하고, 구현 근거는 공식 문서 또는 검증된 오픈소스 프로젝트에 둔다.

### 0.2 설계 핵심 원칙

1. **SQLite가 단일 원본 저장소(SSOT)** 이다.
2. **Notion은 읽기 전용 뷰어** 이며, 쓰기 원본이 아니다.
3. **수집 원문은 “기사 1덩어리”가 아니라 “섹션 구조”** 로 저장한다.
4. **분석 결과는 재생성 가능 자산** 으로 보고 버전과 히스토리를 남긴다.
5. **상태는 단계별로 분리** 하며, `status` 단일 컬럼에 모든 의미를 몰아넣지 않는다.
6. **동기화 책임은 SyncAgent로 일원화** 한다.  
   - Collector: 수집 전용  
   - Analyzer: 분석 전용  
   - SyncAgent: Notion + ChromaDB 동기화 전용
7. **로그인 이후 HTML의 기준은 렌더링된 DOM(rendered DOM)** 이다.
8. **재시도보다 idempotency가 우선** 이다. 중복 실행해도 데이터가 깨지지 않아야 한다.
9. **운영 편의성(백업, resume, drift 감지, dry-run)** 을 기능과 동급으로 취급한다.
10. **Claude Code 친화적 구조** 로 설계한다. `CLAUDE.md`는 짧고 명확하게 유지하고, 세부 규칙은 하위 문서로 분리한다.

---

## 1. 프로젝트 목표와 범위

### 1.1 목표

하이젠버그 회원제 기사 데이터를 개인 지식 DB로 구축하기 위한 내부용 파이프라인을 만든다.

파이프라인의 1차 목표는 다음과 같다.

- 로그인 후 접근 가능한 기사 페이지를 안정적으로 수집
- 기사 본문과 구조적 섹션을 분리 저장
- 구조화 요약/비판 분석 생성
- SQLite를 기준으로 Notion/ChromaDB에 재동기화
- 상위 에이전트(블로그 작성, 리서치, 아카이빙)에서 재사용 가능한 서브모듈화

### 1.2 1차 범위

| 항목 | 포함 | 제외 |
|---|---|---|
| 대상 사이트 | `heisenberg.kr` | 타 사이트 실구현 |
| 콘텐츠 유형 | 기사 페이지, 기사 메타데이터, 기사 내 이미지 URL, PDF 스냅샷 | 댓글, 커뮤니티, 멤버십 영상 플레이어 내부 데이터 |
| 분석 | 요약, 비판 분석, 중요도, 키워드, 근거 구간(span) | 실시간 뉴스 감지, 자동 투자 의사결정 |
| 저장소 | SQLite, ChromaDB, Notion, 로컬 파일 | SaaS 데이터 웨어하우스 |
| 실행 환경 | 로컬 Windows PC, 스케줄러 | 상시 클라우드 운영 |

### 1.3 비목표

- 브라우저 지문 위장, CAPTCHA 우회, 강한 anti-bot 회피 구현
- 멀티유저 서비스화
- 외부 사용자에게 원문 재배포하는 기능
- 실시간 스트리밍 처리

---

## 2. 사이트 관찰 결과와 파싱 전제

> 아래 내용은 **2026-03-17 기준 공개 페이지/샘플 페이지 관찰 결과**를 바탕으로 한 구현 전제다.  
> 실제 구현 시에는 Collector live smoke test로 다시 검증한다.

### 2.1 공개 페이지 기준 확인 사항

- 로그인 페이지에는 `아이디 또는 이메일`, `비밀번호`, 허니팟성 문구 `Only fill in if you are not human` 가 노출된다.
- 최신 글 목록은 `/latest/` 와 `/latest/page/{n}/` 형태의 페이지네이션을 가진다.
- 공개 기사 목록에서 제목, 발췌문, 저자, 카테고리, 날짜, 태그를 파싱할 수 있다.
- 기사 상세 페이지 샘플에서는 다음과 같은 구조가 관찰된다.
  - 연구자 정보
  - 1분 요약
  - 본문
  - 멤버십 등급별 게이트 문구
  - 연구자 의견
  - 문의하기 / 커피챗 등 추가 섹션
- 따라서 **본문 전체를 하나의 `body_text`로만 다루는 모델은 불충분** 하다.

### 2.2 구현에 반영할 파싱 전제

1. 상세 페이지 기준 저장 단위는 `article` + `article_sections` 이다.
2. 게이트 문구(`스탠다드 등급 이상…`, `비즈니스 등급 이상…`)는 별도 섹션으로 저장한다.
3. `1분 요약`은 별도 섹션으로 저장한다.
4. 렌더링 후 DOM을 파싱 기준으로 사용한다.
5. 기사 URL의 slug만 믿지 않고 `canonical_url`, `content_hash`도 함께 본다.

### 2.3 수집기에서 기대하는 섹션 종류

| section_kind | 설명 |
|---|---|
| `researcher_profile` | 연구자 소개 정보 |
| `one_minute_summary` | 1분 요약 |
| `main_body` | 본문 텍스트 |
| `membership_gate_notice` | 구독 등급 필요 문구 |
| `researcher_opinion` | 연구자 의견 |
| `qa` | 문의/질문 블록 |
| `coffeechat` | 커피챗/후속 상호작용 블록 |
| `misc` | 분류되지 않은 기타 섹션 |

### 2.4 접근 등급(access tier) 모델

| access_tier | 의미 |
|---|---|
| `public` | 로그인 없이 노출되는 구간 |
| `logged_in` | 로그인 후 노출 |
| `standard` | 스탠다드 이상 |
| `business` | 비즈니스 이상 |
| `vip` | VIP 이상 |
| `vvip` | VVIP 이상 |
| `unknown` | 판정 불가 |

---

## 3. 전체 시스템 아키텍처

## 3.1 상위 구조

```text
[Scheduler / Manual Trigger]
          |
          v
+---------------------+
| Pipeline Orchestrator |
+----------+----------+
           |
           +---------------------+
           |                     |
           v                     v
+------------------+      +------------------+
| CollectorAgent   | ---> | SQLite (SSOT)    |
| - login          |      | - articles       |
| - list crawl     |      | - sections       |
| - detail parse   |      | - analyses       |
| - snapshot       |      | - sync jobs      |
+------------------+      +------------------+
                                   |
                                   v
                           +------------------+
                           | AnalyzerAgent    |
                           | - preprocess     |
                           | - structured LLM |
                           | - analysis run   |
                           +------------------+
                                   |
                                   v
                           +------------------+
                           | SyncAgent        |
                           | - Chroma sync    |
                           | - Notion sync    |
                           | - retry queue    |
                           +------------------+
```

### 3.2 에이전트 책임 분리

| 에이전트 | 책임 | 하지 않는 일 |
|---|---|---|
| CollectorAgent | 로그인, 목록/상세 수집, 렌더 DOM 추출, 스냅샷, SQLite 원문 저장 | LLM 분석, Notion/Chroma 동기화 |
| AnalyzerAgent | 구조화 요약/비판 분석, 분석 메타 기록 | 수집, 외부 동기화 |
| SyncAgent | Notion/Chroma 동기화, 외부 시스템 재시도 | 크롤링, LLM 분석 |
| Pipeline Orchestrator | 실행 순서, 단일 실행 락, 종료 리포트 | 개별 에이전트 세부 로직 |

### 3.3 책임 일원화 결정

v1.3 문서에서는 ChromaDB 동기화 책임이 Agent 2와 Agent 3 사이에 혼재되어 있었다.  
v1.4에서는 **외부 저장소 동기화는 전부 SyncAgent 책임** 으로 통일한다.

이유는 다음과 같다.

- 분석과 외부 API 동기화의 실패 원인을 분리할 수 있다.
- `ANALYZED but NOT SYNCED` 상태를 자연스럽게 표현할 수 있다.
- Notion과 Chroma의 재시도 정책을 공통 큐(`sync_jobs`)로 관리할 수 있다.
- 운영 중 재동기화(dry-run / backfill / rebuild) 시 SyncAgent만 독립 실행하면 된다.

---

## 4. 운영 및 유지보수 목표

### 4.1 비기능 요구사항

| 항목 | 목표 |
|---|---|
| idempotency | 동일 기사 재실행 시 중복 row/중복 page/중복 vector가 생기지 않을 것 |
| recoverability | 중간 실패 후 다음 실행에서 안전하게 resume 가능할 것 |
| observability | 기사 단위 이벤트와 실행 단위 리포트를 모두 남길 것 |
| portability | 로컬 Windows 기준 경로/권한/스케줄링을 명시할 것 |
| debuggability | selector drift, prompt drift, sync drift를 구분해서 볼 수 있을 것 |
| maintainability | 설정/프롬프트/셀렉터/API 어댑터를 코드에서 분리할 것 |

### 4.2 운영 기본 정책

- **single-run lock**: 동시에 두 사이클이 돌지 않게 한다.
- **backup before schema migration**: SQLite 마이그레이션 전에 백업을 남긴다.
- **resume-first**: 실패하면 처음부터 다시가 아니라 실패 단계부터 재개한다.
- **dry-run**: Notion/Chroma 동기화에는 dry-run 모드를 둔다.
- **no-op skip**: payload hash가 동일하면 Notion 업데이트를 생략한다.
- **contract tests**: live site만 믿지 않고 HTML fixture 기반 파싱 계약 테스트를 유지한다.

---

## 5. 데이터 아키텍처

## 5.1 저장소 역할

| 계층 | 저장소 | 역할 |
|---|---|---|
| Layer 1 | SQLite | SSOT, 운영 상태, 이력, 사용자 주석 |
| Layer 2 | ChromaDB | 시맨틱 검색 색인 |
| Layer 3 | Notion | 사람이 읽는 뷰어 |

### 5.2 저장 원칙

1. 모든 원본/분석/상태 변화는 **SQLite 먼저** 기록한다.
2. ChromaDB와 Notion은 **재생성 가능한 파생 저장소** 로 취급한다.
3. 외부 동기화 실패는 치명적 실패가 아니라 **재시도 가능한 비동기 실패** 로 본다.
4. 로컬 파일(PDF, 이미지)은 DB row와 해시로 연결한다.

---

## 6. SQLite 스키마 설계

> 구현은 SQLAlchemy 2.0 스타일 ORM + Alembic 마이그레이션을 권장한다.  
> DDL은 이해를 위한 기준 스키마이며, 실제 구현에서는 migration revision으로 관리한다.

### 6.1 스키마 설계 철학

- `articles`는 기사 단위의 정규화된 헤더 정보
- `article_sections`는 렌더링 구조를 반영한 본문 저장
- `analysis_runs`는 재생성 가능한 LLM 결과 이력
- `sync_jobs`는 외부 동기화 큐
- `article_events`는 article-level event log
- `article_annotations`는 사용자 메모/읽음 상태 등 운영 외부 도메인

### 6.2 핵심 테이블

#### 6.2.1 `articles`

```sql
CREATE TABLE articles (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    source_site             TEXT NOT NULL DEFAULT 'heisenberg.kr',
    slug                    TEXT NOT NULL,
    url                     TEXT NOT NULL,
    canonical_url           TEXT,
    title                   TEXT NOT NULL,
    author                  TEXT,
    category                TEXT,
    content_kind            TEXT NOT NULL DEFAULT 'article',
    published_at            DATETIME,
    source_updated_at       DATETIME,
    source_timezone         TEXT NOT NULL DEFAULT 'Asia/Seoul',
    collected_at            DATETIME NOT NULL,

    rendered_html           TEXT,
    body_text               TEXT,   -- section 합본의 denormalized text
    body_text_hash          TEXT,
    rendered_html_hash      TEXT,
    content_hash            TEXT,   -- 최종 본문 기준 canonical hash

    selector_profile_version TEXT,
    parser_version          TEXT,
    content_version         INTEGER NOT NULL DEFAULT 1,

    snapshot_path           TEXT,
    snapshot_sha256         TEXT,
    snapshot_byte_size      INTEGER,
    snapshot_page_count     INTEGER,

    collect_status          TEXT NOT NULL DEFAULT 'PENDING',
    analyze_status          TEXT NOT NULL DEFAULT 'PENDING',
    notion_sync_status      TEXT NOT NULL DEFAULT 'PENDING',
    vector_sync_status      TEXT NOT NULL DEFAULT 'PENDING',

    collect_attempt_count   INTEGER NOT NULL DEFAULT 0,
    analyze_attempt_count   INTEGER NOT NULL DEFAULT 0,
    notion_attempt_count    INTEGER NOT NULL DEFAULT 0,
    vector_attempt_count    INTEGER NOT NULL DEFAULT 0,

    last_error_code         TEXT,
    last_error_message      TEXT,
    next_retry_at           DATETIME,
    last_success_at         DATETIME,

    current_analysis_id     INTEGER,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(source_site, slug),
    UNIQUE(source_site, url)
);
```

#### 6.2.2 `article_sections`

```sql
CREATE TABLE article_sections (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id              INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    ordinal                 INTEGER NOT NULL,
    section_kind            TEXT NOT NULL,
    section_title           TEXT,
    access_tier             TEXT NOT NULL DEFAULT 'unknown',
    is_gated_notice         BOOLEAN NOT NULL DEFAULT FALSE,
    body_text               TEXT,
    body_html               TEXT,
    content_hash            TEXT,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(article_id, ordinal)
);
```

#### 6.2.3 `tags` / `article_tags`

```sql
CREATE TABLE tags (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL UNIQUE
);

CREATE TABLE article_tags (
    article_id              INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    tag_id                  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY(article_id, tag_id)
);
```

#### 6.2.4 `article_images`

```sql
CREATE TABLE article_images (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id              INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    ordinal                 INTEGER NOT NULL DEFAULT 0,
    image_url               TEXT NOT NULL,
    local_path              TEXT,
    sha256                  TEXT,
    byte_size               INTEGER
);
```

#### 6.2.5 `analysis_runs`

```sql
CREATE TABLE analysis_runs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id              INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    source_content_hash     TEXT NOT NULL,
    analysis_version        TEXT NOT NULL,
    prompt_bundle_version   TEXT NOT NULL,

    summary_json            TEXT NOT NULL,
    critique_json           TEXT NOT NULL,

    llm_provider            TEXT NOT NULL,
    llm_model               TEXT NOT NULL,
    fallback_used           BOOLEAN NOT NULL DEFAULT FALSE,

    input_tokens            INTEGER,
    output_tokens           INTEGER,
    cost_usd                REAL,
    latency_ms              INTEGER,

    is_current              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

#### 6.2.6 `article_annotations`

```sql
CREATE TABLE article_annotations (
    article_id              INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    is_read                 BOOLEAN NOT NULL DEFAULT FALSE,
    starred                 BOOLEAN NOT NULL DEFAULT FALSE,
    rating                  INTEGER,
    user_memo               TEXT,
    updated_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

#### 6.2.7 `sync_jobs`

```sql
CREATE TABLE sync_jobs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id              INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    target                  TEXT NOT NULL,      -- notion | vector
    payload_hash            TEXT,
    status                  TEXT NOT NULL,      -- pending | running | success | failed | skipped
    attempt_count           INTEGER NOT NULL DEFAULT 0,
    next_retry_at           DATETIME,
    locked_at               DATETIME,
    last_error_code         TEXT,
    last_error_message      TEXT,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(article_id, target)
);
```

#### 6.2.8 `article_events`

```sql
CREATE TABLE article_events (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id              INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    stage                   TEXT NOT NULL,
    event_type              TEXT NOT NULL,
    payload_json            TEXT,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

#### 6.2.9 `collection_runs`

```sql
CREATE TABLE collection_runs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_type            TEXT NOT NULL,      -- scheduled | manual
    started_at              DATETIME NOT NULL,
    finished_at             DATETIME,
    status                  TEXT NOT NULL,      -- success | partial | failed
    articles_found          INTEGER NOT NULL DEFAULT 0,
    articles_collected      INTEGER NOT NULL DEFAULT 0,
    articles_analyzed       INTEGER NOT NULL DEFAULT 0,
    articles_synced_notion  INTEGER NOT NULL DEFAULT 0,
    articles_synced_vector  INTEGER NOT NULL DEFAULT 0,
    errors                  INTEGER NOT NULL DEFAULT 0,
    report_json             TEXT
);
```

#### 6.2.10 `app_state`

```sql
CREATE TABLE app_state (
    key                     TEXT PRIMARY KEY,
    value                   TEXT NOT NULL,
    updated_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 6.3 인덱스

```sql
CREATE INDEX idx_articles_published_at ON articles(published_at);
CREATE INDEX idx_articles_category ON articles(category);
CREATE INDEX idx_articles_collect_status ON articles(collect_status);
CREATE INDEX idx_articles_analyze_status ON articles(analyze_status);
CREATE INDEX idx_articles_notion_status ON articles(notion_sync_status);
CREATE INDEX idx_articles_vector_status ON articles(vector_sync_status);
CREATE INDEX idx_articles_content_hash ON articles(content_hash);
CREATE INDEX idx_sections_article_kind ON article_sections(article_id, section_kind);
CREATE INDEX idx_analysis_article_current ON analysis_runs(article_id, is_current);
CREATE INDEX idx_sync_jobs_target_status ON sync_jobs(target, status, next_retry_at);
CREATE INDEX idx_events_article_stage ON article_events(article_id, stage, created_at);
```

### 6.4 SQLite 운영 기본값

초기 연결 시 다음을 명시한다.

- `PRAGMA foreign_keys = ON`
- `PRAGMA journal_mode = WAL`
- 필요 시 `PRAGMA synchronous = NORMAL`
- optional: `busy_timeout`

추가로 로컬 검색 편의성을 위해 `FTS5` 미러 테이블을 둘 수 있다.

예:

- `articles_fts(title, body_text, summary_text, critique_text)`

단, FTS는 **운영 필수 기능이 아니라 선택 기능** 으로 시작한다.

---

## 7. ChromaDB 설계

### 7.1 역할

- 시맨틱 검색
- 상위 에이전트 RAG context retrieval
- 기사 간 유사도 탐색

### 7.2 저장 단위

v1.4 기준 기본 저장 단위는 **기사 요약 단위 1 record / article** 로 한다.

나중에 확장할 경우에만 section-level 색인을 추가한다.

#### 기본 record

```json
{
  "id": "article:heisenberg.kr:gtc2026",
  "document": "{title}\n\n{core_thesis}\n\n{supporting_points_joined}\n\n{keywords_joined}",
  "metadata": {
    "source_site": "heisenberg.kr",
    "slug": "gtc2026",
    "url": "https://heisenberg.kr/gtc2026/",
    "category": "AI",
    "published_at": "2026-03-17T00:00:00+09:00",
    "analysis_version": "analysis.v1",
    "embedding_version": "embed.v1",
    "content_hash": "..."
  }
}
```

### 7.3 저장 정책

1. Chroma는 **파생 저장소** 이므로 rebuild 가능해야 한다.
2. 기본 동기화는 `upsert` 성격으로 처리한다.
3. `payload_hash` 또는 `content_hash + embedding_version` 이 같으면 재임베딩하지 않는다.
4. raw body 전체를 중복 저장하지 않는다.  
   기본은 `title + 구조화 요약 + 키워드` 위주 최소 텍스트로 시작한다.
5. 필요 시 section-level 인덱스는 별도 collection으로 분리한다.

### 7.4 임베딩 버전 관리

- `embedding_provider`
- `embedding_model`
- `embedding_version`
- `embedding_source_hash`

가 바뀌면 sync backlog를 다시 생성한다.

---

## 8. Notion 설계

### 8.1 역할

- 사람 읽기용 뷰어
- 모바일/노션 UI 기반 분류/열람
- 원본 저장소가 아닌 **파생 뷰어**

### 8.2 핵심 정책

1. 쓰기는 항상 `SQLite -> Notion` 단방향.
2. Notion의 페이지 속성 변경은 원칙적으로 SQLite에 역반영하지 않는다.
3. `payload_hash` 동일 시 Notion 업데이트를 생략한다.
4. Notion API 버전은 명시적으로 고정한다.
5. Notion API 개념 변화(`database`/`data source`, `archived`/`in_trash`)에 대비해 **thin adapter 계층** 뒤에 숨긴다.

### 8.3 권장 속성 매핑

| Notion 속성 | 타입 | 소스 |
|---|---|---|
| 제목 | Title | `articles.title` |
| 발행일 | Date | `articles.published_at` |
| 저장일 | Date | `articles.collected_at` |
| 원본URL | URL | `articles.url` |
| 카테고리 | Select | `articles.category` |
| 저자 | Select | `articles.author` |
| 태그 | Multi-select | `article_tags` |
| 중요도 | Select | `analysis_runs.summary_json.importance` |
| 키워드 | Multi-select | `analysis_runs.summary_json.keywords` |
| 읽음 | Checkbox | `article_annotations.is_read` |
| 별표 | Checkbox | `article_annotations.starred` |
| 요약모델 | Rich text | `analysis_runs.llm_model` |
| 분석버전 | Rich text | `analysis_runs.analysis_version` |
| 스냅샷생성여부 | Checkbox | `snapshot_path IS NOT NULL` |

### 8.4 본문 블록 구성

```text
## 기사 요약
- 핵심 논지
- 주요 근거
- 결론
- 키워드

## 비판적 분석
- 논리적 허점
- 누락된 관점
- 검증 필요 주장
- 이해관계 분석
- 종합 평가

## 메타
- category
- author
- published_at
- analysis_version
- embedding_version
- content_hash
```

### 8.5 Notion에 넣지 않는 것

- 로컬 절대 경로 그대로의 `snapshot_path`
- rendered HTML 원문
- article_sections 전체 raw body
- 장문 debug 정보
- retry / internal error log

---

## 9. 상태 모델 및 재시도 정책

### 9.1 상태 분리 원칙

기존의 `DISCOVERED -> COLLECTED -> ANALYZED -> PUBLISHED` 단일 상태 흐름은 운영 정보가 너무 뭉툭하다.  
v1.4에서는 단계를 분리한다.

### 9.2 단계별 상태값

| 컬럼 | 상태 |
|---|---|
| `collect_status` | `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `SKIPPED` |
| `analyze_status` | `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `SKIPPED` |
| `notion_sync_status` | `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `SKIPPED` |
| `vector_sync_status` | `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `SKIPPED` |

### 9.3 파생 최종 상태

UI나 리포트용으로만 계산한다.

| 파생 상태 | 조건 |
|---|---|
| `DISCOVERED` | article row만 존재 |
| `COLLECTED` | collect만 성공 |
| `ANALYZED` | collect + analyze 성공 |
| `PARTIAL_SYNCED` | analyze 성공 + notion/vector 일부 성공 |
| `PUBLISHED` | analyze, notion, vector 모두 성공 |
| `FAILED` | 어느 단계든 재시도 가능 상태가 아닌 실패 |

### 9.4 실패 처리 정책

| 실패 유형 | 재시도 | 폴백 | 최종 처리 |
|---|---|---|---|
| 로그인 실패 | 3회 | 저장된 auth state 재검증 | 사이클 실패 |
| 목록 페이지 접근 실패 | 2회 | 없음 | run partial 실패 |
| 상세 페이지 파싱 실패 | 2회 | selector profile downgrade | article failed |
| PDF 스냅샷 실패 | 2회 | 텍스트만 저장 | collect success + warning |
| 요약 실패 | 2회 | fallback model | analyze failed |
| 비판 실패 | 2회 | fallback model | analyze failed |
| Notion 429/5xx | 지수 백오프 | 없음 | sync job pending |
| Vector sync 실패 | 3회 | local embedding fallback | sync job pending |

### 9.5 article-level event logging

모든 상태 전이는 `article_events` 에 기록한다.

예:

- `collector.detail.loaded`
- `collector.section.parsed`
- `analyzer.summary.succeeded`
- `analyzer.critique.failed`
- `sync.notion.skipped_noop`
- `sync.vector.upserted`

이벤트 로그를 남기면 운영 중 “왜 이 기사가 누락되었는지”를 SQL만으로 추적할 수 있다.

---

## 10. CollectorAgent 상세 설계

### 10.1 로그인 전략

- **기본 전략**: Playwright 로그인 + `storage_state` 저장/재사용
- **보조 전략**: auth state 만료 시 새 로그인
- 로그인 성공 여부는 “멤버십 영역 노출 여부”나 “로그인 전용 메뉴 변화”로 검증한다.

### 10.2 HTML 기준

- 리스트 페이지: Playwright로 가져온 HTML 파싱
- 상세 페이지: Playwright 렌더 완료 후 `page.content()` 기반 파싱
- BeautifulSoup는 **HTTP raw response parser가 아니라 rendered HTML parser** 로 사용

### 10.3 목록 수집 흐름

1. `latest` 페이지 열기
2. 기사 카드 목록 파싱
3. slug/url/title/date/category/tag 추출
4. SQLite existing set과 비교
5. `duplicate_safety_window_days` 범위 내 최근 기사에 대해서는 content hash 재확인
6. 신규 또는 업데이트 후보만 상세 수집

### 10.4 상세 수집 흐름

1. 상세 페이지 로드
2. ready selector 대기
3. 제목/저자/카테고리/발행일 파싱
4. 섹션 분해(`article_sections`)
5. 이미지 URL 추출
6. denormalized `body_text` 생성
7. `content_hash` 계산
8. PDF 스냅샷 생성
9. SQLite 저장
10. `sync_jobs` 생성(`target=vector`, `target=notion`은 analyze 완료 후 생성)

### 10.5 PDF 스냅샷 정책

- 기본은 `page.emulate_media(media="screen")` 후 `page.pdf()`
- `print_background = true`
- `prefer_css_page_size = true` 는 선택
- `networkidle` 기준 완료 판정은 사용하지 않고, ready selector + 타임아웃 조합 사용
- PDF 품질보다는 **재현 가능성과 파싱 실패 시 증적 보존** 이 목적

### 10.6 selector drift 대응

- `selectors/heisenberg.yaml` 로 관리
- selector profile version을 DB에 남긴다
- live smoke test에서 실패율이 일정 임계치를 넘으면 drift 경고를 발생시킨다

---

## 11. AnalyzerAgent 상세 설계

### 11.1 입력 데이터

Analyzer는 `articles.body_text` 전체를 그대로 사용하지 않는다.  
우선순위는 아래와 같다.

1. `main_body` 섹션
2. `one_minute_summary` 섹션
3. `researcher_opinion` 섹션
4. article-level `body_text` fallback

### 11.2 분석 결과 구조

분석 결과는 자유 텍스트가 아니라 **구조화 JSON** 으로 받는다.

#### Summary schema

```json
{
  "core_thesis": "string",
  "supporting_points": ["string"],
  "conclusion": "string",
  "keywords": ["string"],
  "importance": "high|medium|low",
  "confidence": 0.0,
  "evidence_spans": [
    {
      "section_kind": "main_body",
      "quote_hint": "string",
      "reason": "string"
    }
  ]
}
```

#### Critique schema

```json
{
  "logic_gaps": ["string"],
  "missing_views": ["string"],
  "claims_to_verify": ["string"],
  "interest_analysis": "string",
  "overall_assessment": "string",
  "confidence": 0.0
}
```

### 11.3 분석 결과 저장 방식

- `analysis_runs` 에 immutable history 저장
- `articles.current_analysis_id` 로 현재 분석 버전 지정
- 새 분석 생성 시 기존 run의 `is_current=false`

### 11.4 재분석 트리거

다음 중 하나가 바뀌면 재분석 backlog를 만든다.

- `content_hash`
- `analysis_version`
- `prompt_bundle_version`
- LLM provider/model policy
- evidence span 생성 규칙

### 11.5 비용/지연 메타

각 분석 run에 아래를 남긴다.

- `input_tokens`
- `output_tokens`
- `cost_usd`
- `latency_ms`
- `fallback_used`

---

## 12. SyncAgent 상세 설계

### 12.1 역할

- `sync_jobs` 큐를 읽어 외부 저장소에 반영
- Notion, ChromaDB 공통 retry/backoff 처리
- no-op skip, payload hash, dry-run 제공

### 12.2 큐 처리 원칙

1. `status=pending`
2. `next_retry_at <= now`
3. `locked_at IS NULL` 또는 락 만료
4. target별 worker loop 실행

### 12.3 Notion sync 규칙

- current analysis 기준 payload 생성
- payload hash 계산
- 기존 page와 동일 payload면 skip
- 429면 `Retry-After` 반영
- 큰 본문은 chunking
- 생성 성공 후 `app_state` 또는 article mapping 갱신

### 12.4 Vector sync 규칙

- `content_hash + embedding_version` 으로 변경 감지
- 변경 없으면 skip
- upsert 후 `vector_sync_status=SUCCEEDED`

### 12.5 SyncAgent를 분리하는 이유

- Notion rate limit과 vector rebuild는 수집/분석보다 훨씬 다른 실패 특성을 가진다.
- 외부 API 장애가 있어도 수집과 분석을 계속할 수 있다.
- backlog 재처리가 쉽다.

---

## 13. 설정 관리

### 13.1 비밀값과 런타임 상태 분리

#### `.env` 에 둘 것

- 로그인 자격
- API keys
- Notion parent page id
- manual trigger token

#### 런타임 상태 DB(`app_state`)에 둘 것

- Notion data source id
- last_successful_run_at
- auth_state_path
- current parser version
- current selector profile version

### 13.2 환경변수 네이밍

```bash
HEISENBERG_USERNAME_OR_EMAIL=
HEISENBERG_PASSWORD=

NOTION_API_KEY=
NOTION_PARENT_PAGE_ID=

ANTHROPIC_API_KEY=
OPENAI_API_KEY=

LOG_LEVEL=INFO
APP_ENV=local
MANUAL_TRIGGER_TOKEN=
```

### 13.3 `settings.yaml` 예시

```yaml
app:
  timezone: "Asia/Seoul"
  environment: "local"
  data_dir: "./data"
  log_dir: "./logs"

collector:
  base_url: "https://heisenberg.kr"
  login_url: "https://heisenberg.kr/login/"
  latest_url: "https://heisenberg.kr/latest/"
  auth_mode: "playwright_storage_state"
  html_source_of_truth: "rendered_dom"
  max_pages_to_scan: 3
  max_articles_per_cycle: 20
  duplicate_safety_window_days: 7
  request_delay_seconds:
    min: 2
    max: 5

snapshot:
  enabled: true
  emulate_media: "screen"
  print_background: true
  ready_selector: "article, .entry-content, .post-content"
  max_wait_ms: 10000

analysis:
  analysis_version: "analysis.v1"
  prompt_bundle_version: "prompt-bundle.v1"
  summary:
    provider: "anthropic"
    model: "claude-sonnet-4-5"
    max_tokens: 1800
    temperature: 0.2
  critique:
    provider: "anthropic"
    model: "claude-sonnet-4-5"
    max_tokens: 1800
    temperature: 0.3
  fallback:
    provider: "openai"
    model: "gpt-4o"

vectordb:
  provider: "chromadb"
  persist_dir: "./data/vectordb"
  collection_name: "heisenberg_articles"
  embedding_provider: "openai"
  embedding_model: "text-embedding-3-small"
  embedding_version: "embed.v1"

notion:
  api_version: "2026-03-11"
  sync_mode: "one_way"
  obey_retry_after: true
  max_blocks_per_payload: 200
  max_payload_bytes: 200000
  dry_run: false

scheduler:
  cron_hours: [8, 13, 19]
  max_instances: 1
  coalesce: true
  misfire_grace_time_seconds: 3600
  manual_trigger_bind: "127.0.0.1"
  manual_trigger_port: 8321
```

### 13.4 설정 로더 원칙

- `pydantic-settings` 기반으로 `.env`, 환경변수, dotenv, 테스트 오버라이드를 처리한다.
- YAML은 앱 구조 설정, `.env`는 비밀값/머신별 설정에 집중한다.
- 중첩 환경변수(`GENERATION_LLM_API_KEY` 스타일)를 허용할 수 있게 설계한다.

---

## 14. 프로젝트 구조(권장)

```text
heisenberg-agent/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── alembic.ini
├── migrations/
│   └── versions/
├── config/
│   ├── settings.yaml
│   ├── llm.yaml
│   ├── notion.yaml
│   └── selectors/
│       └── heisenberg.yaml
├── docs/
│   ├── design/
│   │   └── heisenberg_agent_design_v1.4.md
│   └── references/
│       └── implementation_reference_map.md
├── src/
│   └── heisenberg_agent/
│       ├── __init__.py
│       ├── main.py
│       ├── scheduler.py
│       ├── orchestrator/
│       │   ├── __init__.py
│       │   └── pipeline.py
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   ├── collector.py
│       │   ├── analyzer.py
│       │   └── syncer.py
│       ├── scrapers/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   └── heisenberg.py
│       ├── parsers/
│       │   ├── __init__.py
│       │   ├── sections.py
│       │   └── normalizers.py
│       ├── llm/
│       │   ├── __init__.py
│       │   ├── client.py
│       │   ├── schemas.py
│       │   ├── prompt_loader.py
│       │   └── prompts/
│       │       ├── summary.md
│       │       └── critique.md
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── notion_adapter.py
│       │   ├── chroma_adapter.py
│       │   └── playwright_adapter.py
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── db.py
│       │   ├── models.py
│       │   ├── repositories/
│       │   │   ├── articles.py
│       │   │   ├── analyses.py
│       │   │   ├── sync_jobs.py
│       │   │   └── app_state.py
│       │   └── migrations/
│       ├── services/
│       │   ├── dedupe.py
│       │   ├── snapshot.py
│       │   ├── sync_service.py
│       │   └── article_merge.py
│       ├── runtime/
│       │   ├── locks.py
│       │   ├── paths.py
│       │   └── health.py
│       └── utils/
│           ├── hashing.py
│           ├── logger.py
│           ├── datetime.py
│           └── errors.py
├── data/
│   ├── db/
│   ├── snapshots/
│   ├── runtime/
│   └── vectordb/
├── logs/
└── tests/
    ├── unit/
    ├── integration/
    ├── contract/
    ├── live/
    └── fixtures/
```

### 14.1 네이밍 정리

- `publisher.py` 대신 `syncer.py` 사용을 권장
- `sqlite_db.py` / `local_db.py` 중복 표현을 피하고 `storage/db.py`, `repositories/*` 로 정리
- 수집/분석/동기화 코드는 agent 안에만 두지 말고, 외부 의존성이 있는 코드는 `adapters/`, 도메인 로직은 `services/` 로 분리

---

## 15. 테스트 전략

### 15.1 테스트 계층

| 계층 | 목표 |
|---|---|
| unit | 순수 함수/도메인 로직 검증 |
| contract | HTML fixture 기반 selector/section parser 검증 |
| integration | SQLite/Chroma/Notion adapter wiring 검증 |
| live | 실제 사이트 smoke test |
| regression | prompt schema / payload hash / drift 재현 |

### 15.2 꼭 필요한 테스트

1. **selector contract test**
   - 샘플 HTML에서 title/author/date/category/sections 추출 검증
2. **content hash stability test**
   - 공백/광고 제거 후 동일 본문이면 hash 동일
3. **analysis schema validation test**
   - LLM 응답이 JSON schema/Pydantic 검증 통과
4. **sync no-op test**
   - payload hash가 동일하면 Notion update 생략
5. **resume test**
   - collect 성공, analyze 실패 후 다음 실행에서 analyze만 재개
6. **single-run lock test**
   - 동시 실행 차단
7. **migration smoke test**
   - 기존 DB 백업 후 migration 적용 가능

### 15.3 live test 정책

- 기본 CI에서는 live test를 돌리지 않는다.
- `pytest -m live` 또는 `--live` 플래그로만 수동 실행한다.
- live test는 최소 건수만 돌린다.
- credentials가 없으면 skip한다.

---

## 16. 로깅, 리포트, 운영 편의성

### 16.1 로깅 원칙

- `print()` 금지
- `structlog` 기반 JSON 로그 권장
- run id / article id / slug / stage / event_type를 공통 필드로 남긴다.

### 16.2 권장 로그 필드

- `run_id`
- `article_id`
- `slug`
- `source_site`
- `stage`
- `event_type`
- `duration_ms`
- `attempt`
- `status`
- `error_code`

### 16.3 운영용 리포트

각 run 종료 시 다음을 요약한다.

- found
- collected
- analyzed
- notion synced
- vector synced
- skipped
- failed
- selector drift warnings
- sync backlog size

### 16.4 백업/복구

- SQLite는 최소 하루 1회 파일 백업
- 마이그레이션 직전 백업 강제
- 스냅샷 파일은 sha256으로 무결성 검증 가능하게 한다
- `app_state` 포함 백업
- restore 절차를 문서화한다

---

## 17. 보안 및 로컬 운영 주의사항

### 17.1 민감 자산

- `.env`
- Playwright `storage_state`
- SQLite DB
- PDF 스냅샷
- Notion / LLM API key

### 17.2 보안 원칙

- `data/runtime/auth_state.json` 은 gitignore
- `data/` 전체를 기본적으로 VCS 제외
- manual trigger는 `127.0.0.1` 로만 bind
- manual trigger에는 토큰 검증 추가
- 에러 로그에 비밀번호/쿠키/Authorization header를 남기지 않는다

### 17.3 Windows 운영 메모

- 경로는 항상 `pathlib.Path` 기반 절대 경로 사용
- Windows Task Scheduler 사용 시 working directory를 명시한다
- `.env` 인코딩은 UTF-8로 통일
- 장시간 절전 모드 이후 재개 시 auth state를 재검증한다

---

## 18. Claude Code / CLAUDE.md 고려사항

### 18.1 설계 원칙

Claude Code 문서 기준으로 `CLAUDE.md` 는 **프로젝트 전반 규칙과 공용 워크플로우를 짧고 명확하게 요약** 해야 한다. 세부 설명은 별도 문서로 넘긴다.

### 18.2 본 프로젝트에서 CLAUDE.md에 반드시 들어갈 내용

- 프로젝트 개요
- 빌드/테스트/실행 명령
- 아키텍처 요약
- SQLite SSOT 원칙
- selector / prompt / schema 변경 규칙
- Notion은 뷰어라는 원칙
- 외부 문서 참조 위치
- 금지 패턴

### 18.3 CLAUDE.md에 넣지 말아야 할 것

- 장문 배경 설명
- 거대한 코드 예제
- 외부 문서 전체 복붙
- 지나치게 세세한 DDL 전문
- 자주 바뀌는 운영 로그 예시

### 18.4 Claude Code 친화적 규칙

- `CLAUDE.md` 는 200줄 안팎 유지
- 자세한 규칙은 `@docs/design/...` 또는 `.claude/rules/` 로 분리
- `/init` 생성 초안을 출발점으로 쓰되, 이 프로젝트 제약으로 덮어쓴다
- “공식 문서를 먼저 확인하고 구현하라”는 규칙을 명시한다

---

## 19. 구현 우선순위

### Phase 0 — 리포지토리 기반 만들기
- `pyproject.toml`
- `src/` layout
- `CLAUDE.md`
- logger / settings loader
- DB bootstrap / migration setup
- reference map 작성

### Phase 1 — 수집 최소 경로
- Playwright login
- latest page parse
- detail page parse
- article + section 저장
- snapshot 저장

### Phase 2 — 분석 최소 경로
- structured output schemas
- summary / critique prompt
- analysis_runs 저장
- current_analysis_id 연결

### Phase 3 — 동기화 최소 경로
- Chroma upsert
- Notion create/update
- sync_jobs 큐
- payload hash / noop skip

### Phase 4 — 운영성 보강
- backup/restore
- single-run lock
- dry-run
- drift detection
- health report

### Phase 5 — 확장성
- multi-site base scraper
- section-level vector index
- FTS5 local search
- UI / dashboard

---

## 20. 변경 요약(v1.3 → v1.4)

1. `Publisher` 책임을 `SyncAgent` 로 재정의하고 Chroma/Notion 동기화를 일원화
2. `status` 단일 컬럼 중심 설계에서 단계별 상태 설계로 전환
3. `body_text` 단일 저장에서 `article_sections` 구조 저장으로 확장
4. `summary/critique` 인라인 저장에서 `analysis_runs` 히스토리 저장으로 확장
5. `Notion DB` 직접 가정에서 API version pin + adapter 계층 설계로 보강
6. `.env` 집중 설계에서 runtime state 분리
7. selector drift / resume / no-op sync / backup/restore 등 운영 항목 추가
8. CLAUDE.md 친화적 문서 구조 반영
9. 외부 자료 인용 대신 **공식 URL + 정확한 위치** 를 남기는 참조 맵 추가

---

## 21. 구현 참조 맵 (공식 문서/신뢰 프로젝트 우선)

> 원칙: **공식 문서 → 공식 SDK/공식 레포 → 검증된 템플릿 레포** 순서로 확인한다.  
> 아래는 “복붙용 코드 출처”가 아니라 “구현 전에 확인할 위치”다.

### 21.1 Playwright

| 용도 | 참조 위치 | URL |
|---|---|---|
| 인증 상태 저장/재사용 | Playwright Python Docs → `Authentication` | https://playwright.dev/python/docs/auth |
| PDF 생성 | Playwright Python Docs → `Page API` → `page.pdf()` | https://playwright.dev/python/docs/api/class-page |
| print/screen 미디어 차이 | Playwright Python Docs → `Page API` → `page.emulate_media()` | https://playwright.dev/python/docs/api/class-page |

### 21.2 Notion

| 용도 | 참조 위치 | URL |
|---|---|---|
| rate limit / size limit | Notion Docs → `Request limits` | https://developers.notion.com/reference/request-limits |
| data source 개념 | Notion Docs → `Data source` | https://developers.notion.com/reference/data-source |
| API 변경 이력 | Notion Docs → `Changelog` | https://developers.notion.com/page/changelog |
| 2026-03-11 변경점 | Notion Docs → `Upgrade guide 2026-03-11` | https://developers.notion.com/guides/get-started/upgrade-guide-2026-03-11 |
| Python SDK | `ramnes/notion-sdk-py` → repo root README | https://github.com/ramnes/notion-sdk-py |

### 21.3 ChromaDB

| 용도 | 참조 위치 | URL |
|---|---|---|
| record 추가 | Chroma Docs → `Collections` → `Adding Data` | https://docs.trychroma.com/docs/collections/add-data |
| update / upsert semantics | Chroma Docs → `Collections` → `Update Data` | https://docs.trychroma.com/docs/collections/update-data |
| collection 관리 | Chroma Docs → `Collections` → `Manage Collections` | https://docs.trychroma.com/docs/collections/manage-collections |

### 21.4 SQLAlchemy / SQLite

| 용도 | 참조 위치 | URL |
|---|---|---|
| ORM 2.0 스타일 | SQLAlchemy Docs → `Unified Tutorial` | https://docs.sqlalchemy.org/en/20/tutorial/index.html |
| ORM quick start | SQLAlchemy Docs → `ORM Quick Start` | https://docs.sqlalchemy.org/orm/quickstart.html |
| SQLite foreign keys | SQLite Docs → `Foreign Key Support` | https://sqlite.org/foreignkeys.html |
| SQLite WAL | SQLite Docs → `Write-Ahead Logging` | https://sqlite.org/wal.html |
| SQLite FTS5 | SQLite Docs → `FTS5 Extension` | https://www.sqlite.org/fts5.html |

### 21.5 설정 / 재시도 / 로깅

| 용도 | 참조 위치 | URL |
|---|---|---|
| env 기반 설정 | Pydantic Docs → `Settings Management` | https://docs.pydantic.dev/latest/concepts/pydantic_settings/ |
| 재시도 | Tenacity Docs → homepage / examples | https://tenacity.readthedocs.io/ |
| 구조화 로그 | structlog Docs → homepage | https://www.structlog.org/ |

### 21.6 LLM 호출

| 용도 | 참조 위치 | URL |
|---|---|---|
| LiteLLM 기본 사용 | LiteLLM Docs → `Getting Started` | https://docs.litellm.ai/docs/ |
| LiteLLM routing/fallback | LiteLLM Docs → `Routing` | https://docs.litellm.ai/docs/routing |
| LiteLLM structured output | LiteLLM Docs → `Structured Outputs (JSON Mode)` | https://docs.litellm.ai/docs/completion/json_mode |
| LiteLLM exception mapping | LiteLLM Docs → `Exception Mapping` | https://docs.litellm.ai/docs/exception_mapping |
| Anthropic structured outputs | Claude API Docs → `Structured outputs` | https://platform.claude.com/docs/en/build-with-claude/structured-outputs |
| Anthropic prompt guidance | Claude API Docs → `Prompting best practices` | https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices |

### 21.7 Scheduler / Claude Code / 템플릿 레포

| 용도 | 참조 위치 | URL |
|---|---|---|
| 스케줄러 동시 실행/미스파이어 | APScheduler Docs → `User guide` | https://apscheduler.readthedocs.io/en/3.x/userguide.html |
| CLAUDE.md 범위/로딩 규칙 | Claude Code Docs → `How Claude remembers your project` | https://code.claude.com/docs/en/memory |
| CLAUDE.md 작성 팁 | Claude Code Docs → `Best Practices` | https://code.claude.com/docs/en/best-practices |
| Python 프로젝트 구조 템플릿 | `johnthagen/python-blueprint` → repo root README, `pyproject.toml`, `.github/workflows/ci.yml`, `noxfile.py` | https://github.com/johnthagen/python-blueprint |
| Python 패키지 템플릿 | `microsoft/python-package-template` → repo root README, `pyproject.toml`, `.github/workflows/CI.yml` | https://github.com/microsoft/python-package-template |

---

## 22. 구현 금지 패턴

| 금지 패턴 | 이유 | 대안 |
|---|---|---|
| raw HTML과 rendered HTML 혼용 | 파싱 결과 일관성 붕괴 | 상세 페이지는 rendered DOM만 기준 |
| `status` 단일 컬럼에 모든 단계 상태 저장 | 운영성 낮음 | 단계별 상태 컬럼 분리 |
| Notion/Chroma를 원본처럼 사용 | 데이터 정합성 깨짐 | SQLite SSOT |
| 원문 통째로 Chroma 중복 저장 | 비용/중복/재색인 부담 | 최소 문서 또는 요약 중심 |
| summary/critique를 `articles`에만 저장 | 버전 추적 불가 | `analysis_runs` 별도 저장 |
| `.env`에 runtime id/state까지 저장 | 운영 중 꼬임 | `app_state` 분리 |
| selector 하드코딩 | drift 대응 어려움 | YAML selector profiles |
| LLM 자유 텍스트 파싱 | validation 불안정 | structured output + Pydantic |
| sync 실패를 전체 사이클 실패로 처리 | 운영 중단 | sync backlog 재시도 |
| 긴 CLAUDE.md 하나에 모든 규칙 | Claude context 오염 | 요약 + 참조 문서 분리 |

---

## 부록 A. 권장 구현 체크리스트

- [ ] root `CLAUDE.md` 작성
- [ ] `docs/design/` 와 `docs/references/` 생성
- [ ] SQLAlchemy 2.0 + Alembic 부트스트랩
- [ ] `PRAGMA foreign_keys=ON`, `journal_mode=WAL` 초기화
- [ ] Playwright auth state 경로 분리
- [ ] Collector live smoke test 1건 작성
- [ ] HTML fixture contract test 작성
- [ ] structured output schema 작성
- [ ] `analysis_runs` / `sync_jobs` 구현
- [ ] Notion adapter에 version pin 반영
- [ ] payload hash 기반 no-op sync 구현
- [ ] single-run lock 구현
- [ ] backup/restore 스크립트 작성

---

## 부록 B. 권장 파일 배치

- `docs/design/heisenberg_agent_design_v1.4.md`  
  전체 설계서
- `docs/references/implementation_reference_map.md`  
  기술별 공식 문서 바로가기
- `CLAUDE.md`  
  Claude Code용 프로젝트 지침 요약
- `.claude/rules/*.md`  
  선택: 파일 유형별 세부 규칙

---

## 부록 C. 구현 메모

- v1.4의 핵심은 “기능 추가”보다 “운영 가능성 추가”다.
- 처음부터 모든 기능을 넣지 말고, 아래 순서로 얇게 통과시킨다.
  1. 1건 수집
  2. 1건 분석
  3. 1건 vector sync
  4. 1건 notion sync
  5. resume / retry / no-op
- 문서에 없는 신규 외부 의존성을 넣을 때는 **왜 필요한지와 공식 참조 위치**를 PR/커밋 메시지에 남긴다.
