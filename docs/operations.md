# Operations Guide

Heisenberg Agent macOS 운영 가이드.

## Prerequisites

- Python 3.11+ (pyenv 권장)
- 프로젝트 설치 완료 (`pip install -e ".[dev]"`)
- Playwright chromium 설치 (`playwright install chromium`)
- `.env` 파일에 credential 입력 완료

## Directory Setup

launchd의 `StandardOutPath`/`StandardErrorPath`는 프로세스 시작 **전**에
대상 디렉토리가 존재해야 합니다. 최초 설치 시 반드시 실행:

```bash
cd /Users/hangryongcho/heisenberg-agent
mkdir -p logs data/runtime data/db data/snapshots data/vectordb
```

## .env 로드 방식

`settings.py`의 `_project_root()`가 프로젝트 루트를 절대경로로 resolve하고,
그 하위의 `.env`를 명시적으로 읽습니다. CWD에 의존하지 않으므로
launchd, cron 등 어디서 실행하든 동일한 `.env`가 로드됩니다.

> **Note**: `_project_root()`는 editable install (`pip install -e .`) 기준으로
> `src/heisenberg_agent/settings.py`에서 3단계 상위 디렉토리를 프로젝트 루트로
> 판단합니다. 레포 구조가 변경되면 이 함수도 수정해야 합니다.

## Execution Modes

```bash
# 1회 수집만
python -m heisenberg_agent.main --mode collect

# 1회 전체 파이프라인 (collect → analyze → sync)
python -m heisenberg_agent.main --mode pipeline

# Cron 스케줄러 (상주 프로세스, 08:00/13:00/19:00 KST)
python -m heisenberg_agent.main --mode scheduler
```

운영 환경에서는 `--mode scheduler`를 launchd로 관리합니다.

## launchd Setup

### plist 설치

```bash
cp deploy/com.heisenberg.agent.plist ~/Library/LaunchAgents/
```

> **Important**: plist 내부의 모든 경로는 **절대경로**여야 합니다.
> `ProgramArguments`, `WorkingDirectory`, `StandardOutPath`, `StandardErrorPath`
> 모두 해당. launchd는 `~`, `$HOME`, 환경변수를 확장하지 않습니다.
> 사용자 홈 경로가 다르면 plist를 직접 수정하세요.

### 서비스 등록 및 시작

```bash
# 등록 (로그인 시 자동 시작 포함)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.heisenberg.agent.plist

# 즉시 시작 (RunAtLoad=true이면 bootstrap만으로 시작되지만, 명시적으로)
launchctl kickstart gui/$(id -u)/com.heisenberg.agent
```

### 상태 확인

```bash
launchctl print gui/$(id -u)/com.heisenberg.agent
```

`state = running` 확인. PID, exit status 등도 여기서 확인 가능.

### 정지 및 해제

```bash
launchctl bootout gui/$(id -u)/com.heisenberg.agent
```

graceful shutdown: SIGTERM → scheduler가 `scheduler.stopped` 로그 남기고 exit 0.
`KeepAlive.SuccessfulExit=false`이므로 exit 0 후 재시작되지 않음.

### plist 수정 후 재적용

이미 등록된 상태에서 plist를 수정했을 때:

```bash
# 1. 기존 서비스 해제
launchctl bootout gui/$(id -u)/com.heisenberg.agent

# 2. 수정된 plist 복사
cp deploy/com.heisenberg.agent.plist ~/Library/LaunchAgents/

# 3. 재등록
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.heisenberg.agent.plist

# 4. 즉시 시작 (필요 시 -k 옵션으로 강제 재시작)
launchctl kickstart -k gui/$(id -u)/com.heisenberg.agent
```

## Crash Recovery

- `ThrottleInterval=30`: crash 후 30초 대기 후 재시작
- `KeepAlive.SuccessfulExit=false`: 비정상 종료만 재시작
- Pipeline FileLock: crash 시 stale lock 자동 복구 (PID liveness check)

## Duplicate Execution Prevention

3계층 보호:

1. **launchd**: 동일 Label의 job은 1개만 실행
2. **APScheduler**: `max_instances=1`, `coalesce=true`
3. **FileLock**: `data/runtime/pipeline.lock` (PID 기반, stale 자동 복구)

scheduler 실행 중 `--mode pipeline` 수동 실행 시:
→ FileLock이 LockError 발생 → "pipeline_locked" 로그 → exit 1.

## Log Files

| 파일 | 내용 |
|---|---|
| `logs/heisenberg.log` | 앱 구조화 로그 (structlog JSON) |
| `logs/launchd-stdout.log` | launchd가 캡처한 stdout |
| `logs/launchd-stderr.log` | launchd가 캡처한 stderr |

### 로그 확인

```bash
# 앱 로그 실시간
tail -f logs/heisenberg.log

# launchd stderr 확인 (startup 에러 디버깅)
cat logs/launchd-stderr.log

# 최근 pipeline 실행 결과
grep "pipeline_finished\|scheduler.job_completed\|scheduler.job_failed" logs/heisenberg.log | tail -5
```

## Manual Trigger

`MANUAL_TRIGGER_TOKEN`이 `.env`에 설정되어 있으면,
scheduler가 `127.0.0.1:8321`에서 HTTP trigger 서버를 함께 실행합니다.

```bash
curl -X POST http://127.0.0.1:8321/trigger \
  -H "Authorization: Bearer <token>"
```

bind는 `127.0.0.1` (localhost only)로 고정. 외부 노출 없음.

## Operational Checklist

서비스 등록 후 아래를 순서대로 확인:

- [ ] `launchctl print gui/$(id -u)/com.heisenberg.agent` → `state = running`
- [ ] `logs/launchd-stdout.log` 파일 생성됨
- [ ] `logs/launchd-stderr.log` 파일 생성됨, 에러 없음
- [ ] `logs/heisenberg.log`에 `scheduler.starting` 로그
- [ ] `logs/heisenberg.log`에 `scheduler.cron_registered` 로그
- [ ] cron 시간 도달 시 `scheduler.job_completed` 로그
- [ ] `launchctl bootout` 후 `scheduler.stopped` 로그, 재시작 안 됨
- [ ] macOS 재부팅 후 로그인 → `launchctl print` → running
- [ ] scheduler 중 `--mode pipeline` 수동 실행 → LockError 거부 (정상)
- [ ] manual trigger 설정 시: `127.0.0.1` bind 확인, token 인증 정상 작동

## Notion Sync

### 스키마 관리 (SSOT)

`config/notion_schema.yaml`이 Notion property 매핑의 **단일 정의**다.
adapter 코드에 property name이 하드코딩되어 있지 않으며,
런타임에 이 yaml을 로드하여 매핑을 생성한다.

**운영 규칙: yaml의 `name` 필드는 실제 Notion Data Source의 property 이름과
정확히 일치해야 한다.** Notion에서 property 이름을 변경하면 yaml도 함께 수정해야 하고,
yaml을 수정하면 Notion Data Source도 함께 맞춰야 한다.
드리프트 방지를 위해 `test_schema_keys_match_payload_keys` 테스트가
payload ↔ yaml 키 일치를 CI에서 자동 검증한다.

### 환경변수

| 변수 | 필수 | 설명 |
|---|---|---|
| `NOTION_API_KEY` | notion.enabled=True 시 | Notion integration token |
| `NOTION_DATA_SOURCE_ID` | notion.enabled=True 시 | 페이지 생성 대상 Data Source UUID |
| `NOTION_PARENT_PAGE_ID` | 선택 | DB/data source 자동 생성 시에만 사용 |

`NOTION_DATA_SOURCE_ID`가 비어있고 `notion.enabled=True`이면
앱은 정상 기동하되 notion sync를 skip하고 경고 로그를 남긴다.

### Body 정책: Viewer-Only Managed Content

Notion 페이지의 body(본문)는 파이프라인이 전체 교체하는 **managed content**다.
수동 메모를 Notion body에 쓰면 다음 sync에서 삭제된다.

- 수동 메모/주석은 `article_annotations.user_memo`에 기록
- body는 summary → critique → meta 고정 순서
- 변경 감지는 payload_hash 기반. hash 불일치 시 full replace

### body replace의 비원자성

body replace는 delete all → append all 순서로 진행되며 **원자적이지 않다.**
중간 실패 시 페이지에 body가 일부만 남거나 빈 상태가 될 수 있다.

- 실패 시 job은 `failed`로 마킹되고 `payload_hash`는 **갱신되지 않는다**
- 다음 run에서 hash 불일치를 감지하고 full replace를 재시도한다
- `payload_hash`는 properties + body **둘 다 성공한 후에만** 갱신된다

### Sync 모니터링

```bash
# 최근 sync 결과
grep "sync.run_finished" logs/heisenberg.log | tail -5

# 실패한 job 확인
grep "sync.job_failed" logs/heisenberg.log | tail -10

# Notion rate limit (429 circuit breaker)
grep "sync.notion_rate_limited" logs/heisenberg.log | tail -5

# exhausted jobs (재시도 한도 초과)
grep "exhausted" logs/heisenberg.log | tail -5
```

### Sync 트러블슈팅

**Notion sync가 skip됨 ("notion.skip_no_data_source_id")**:
→ `.env`에 `NOTION_DATA_SOURCE_ID`를 설정하라.

**Property 이름 불일치 에러 (400 Bad Request)**:
→ Notion Data Source의 실제 property 이름과 `config/notion_schema.yaml`의
`name` 필드가 일치하는지 확인하라.

**Body가 비어있음 (partial replace 실패)**:
→ 다음 sync run에서 자동 복구된다 (payload_hash 미갱신 → full replace 재시도).
→ 즉시 복구하려면 수동 실행: `python -m heisenberg_agent.main --mode sync`

**Rate limit (429) 반복**:
→ circuit breaker가 작동하여 나머지 job을 defer한다.
→ 기본 retry_after (60초) 후 재시도. 지속적이면 cron 주기를 늘려라.

## Troubleshooting

**scheduler가 시작 안 됨**:
1. `logs/launchd-stderr.log` 확인 — Python/모듈 import 에러
2. plist 경로가 모두 절대경로인지 확인
3. `.venv/bin/python` 경로 존재 확인

**pipeline이 partial로 끝남**:
1. `logs/heisenberg.log`에서 `article_failed` 또는 `llm.fallback_failed` 검색
2. API key 유효성 확인 (Anthropic/OpenAI)
3. Rate limit인 경우 다음 cron까지 대기

**lock file이 남아있음**:
1. `cat data/runtime/pipeline.lock` — PID 확인
2. 해당 PID가 살아있으면 정상 (실행 중)
3. 죽어있으면 다음 실행 시 자동 복구됨 (stale lock detection)
