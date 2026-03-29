# Ops Checklist

운영 품질 점검 항목. phase-review에서 참조한다.

## 로깅

- [ ] 구조화 로그 사용 (structlog)
- [ ] `print()` 사용 없음
- [ ] 에러 로그에 충분한 컨텍스트 포함 (article_id, step, error type)
- [ ] 민감 정보 (비밀번호, 토큰) 로그 노출 없음

## 에러 핸들링

- [ ] 외부 API 호출에 timeout 설정
- [ ] sync 실패 시 전체 파이프라인 중단하지 않음
- [ ] 실패 건은 `sync_jobs` backlog로 기록
- [ ] 예외 발생 시 적절한 에러 타입 사용 (bare except 없음)

## 재시도

- [ ] 재시도 가능한 작업에 tenacity 적용
- [ ] 재시도 횟수·간격이 설정 가능
- [ ] 영구 실패와 일시 실패 구분

## 환경변수 / 설정

- [ ] 비밀 값은 환경변수로 관리 (.env)
- [ ] 하드코딩된 credential 없음
- [ ] 설정 변경 시 재시작 없이 반영 가능 여부 확인

## 데이터베이스

- [ ] `PRAGMA foreign_keys=ON` 적용
- [ ] `PRAGMA journal_mode=WAL` 적용
- [ ] migration 기반 스키마 변경
- [ ] 트랜잭션 범위 적절 (너무 넓거나 좁지 않음)
