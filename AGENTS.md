# PC 동기화와 로컬 검증

이 프로젝트의 클라우드 작업은 GitHub `main`과 직장(`work`)·집(`home`) PC의 마지막 동기화 버전을 추적한다. PC가 바뀌거나 사용자가 업데이트 현황을 묻거나 배포·로컬 검증을 인계할 때 `python tools/pc_sync.py status`로 원격 상태를 조회한다. 상태가 미보고인 PC를 최신으로 간주하지 않는다. 실기 결과는 작업 번호·PC 구분·소스 버전으로 추적하며, 인증 JSON·SQLite·개인 계정 정보는 커밋하지 않는다. Windows에서는 SYNC.md의 pull/push 헬퍼를 사용한다.
