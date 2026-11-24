# 두 PC에서 Git으로 업데이트하기

공유 저장소는 `junis2002/Youtube_Playlist`, 배포 브랜치는 `main`입니다. 직장 PC는 `work`, 집 PC는 `home`으로 구분합니다. 클라우드에서 새 기능을 배포하면 각 PC가 실제로 동기화한 Git 커밋을 기준으로 버전을 비교합니다.

## 첫 연결

Windows에서 Git과 Python 3.11 이상을 사용합니다. Git for Windows의 Git Credential Manager가 브라우저를 열면 이 저장소에 쓰기 권한이 있는 GitHub 계정으로 로그인합니다. YouTube OAuth는 앱의 재생목록 등록에만 사용합니다.

기존 ZIP 설치 폴더는 그대로 두고 GitHub 소스를 임시 폴더로 먼저 복제합니다. 아래는 직장 PC의 PowerShell 예시입니다. 집 PC에서는 첫 줄을 `$place = "home"`으로 설정하세요.

```powershell
$place = "work"
$projectDir = "C:\My Projects\Youtube_Playlist"
$stagingDir = Join-Path ([IO.Path]::GetTempPath()) ("playlist-main-" + [guid]::NewGuid().ToString("N"))
git clone --branch main --single-branch https://github.com/junis2002/Youtube_Playlist.git "$stagingDir"
if ($LASTEXITCODE -ne 0) { throw "GitHub 복제 실패" }
py -3 "$stagingDir\tools\adopt_checkout.py" --project "$projectDir"
```

미리보기에서 전환할 수 있다고 표시되면 음원·영상 앱을 모두 종료한 뒤 **같은 PowerShell 창**에서 적용합니다. 기존 배포본에서 교체할 파일의 원본은 프로젝트 옆 백업 폴더에 보관합니다. 이미 이 저장소의 정상적인 `main` 체크아웃을 사용하고 있다면 역할 등록 단계부터 진행하세요.

```powershell
py -3 "$stagingDir\tools\adopt_checkout.py" --project "$projectDir" --apply
if ($LASTEXITCODE -ne 0) { throw "설치 폴더 전환 실패" }
Set-Location -LiteralPath "$projectDir"
py -3 tools/pc_sync.py init $place
if ($LASTEXITCODE -ne 0) { throw "PC 역할 등록 실패" }
py -3 tools/pc_sync.py pull
if ($LASTEXITCODE -ne 0) { throw "동기화 결과 확인 필요" }
py -3 tools/pc_sync.py status
```

다른 프로젝트나 별도 이력이 있는 Git 폴더로 판정되면 로컬 작업과 오류 메시지를 확인합니다. 미추적 파일을 삭제하는 `git clean`이나 작업 파일을 되돌리는 `git reset --hard`는 전환 절차에 사용하지 않습니다.

## 일상적인 작업

아래 명령은 프로젝트 루트에서 실행합니다. 클라우드에서 배포한 변경과 현재 PC의 마지막 수신 버전을 맞출 때 사용합니다.

```powershell
py -3 tools/pc_sync.py pull
```

PC에서 공유할 소스를 직접 수정한 경우, 대상 파일과 차이를 검토해 의도한 파일만 커밋합니다. 그런 다음 커밋을 올립니다. 실행 헬퍼는 사용자의 변경을 자동으로 스테이징하거나 커밋하지 않습니다.

```powershell
py -3 tools/pc_sync.py push
```

양쪽 PC 상태는 같은 명령으로 확인합니다. 클라우드에서는 Python 실행기로 `python`을 사용합니다.

```powershell
py -3 tools/pc_sync.py status
```

## 상태 해석

- 상태 조회는 GitHub의 최신 `main`과 `sync/work/{pull,push,head}`, `sync/home/{pull,push,head}` 참조를 읽습니다. `pull`과 `push`는 마지막 성공한 해당 명령의 커밋, `head`는 그 PC가 마지막으로 동기화를 보고한 버전입니다.
- 역할을 등록하기만 한 PC는 첫 동기화 전까지 미보고입니다. 일반 Git 명령이나 GitHub Desktop에서 직접 수행한 작업, PC에만 있는 미커밋 수정은 별도로 보고되지 않습니다. 두 PC의 일상적인 수신·송신은 위 헬퍼로 실행하세요.
- 클라우드 작업자는 배포 전후와 사용자가 PC를 바꾸거나 업데이트 현황을 물을 때 `python tools/pc_sync.py status`를 실행합니다. 필요하면 두 커밋 사이 변경 파일도 Git으로 대조합니다. GitHub가 반환한 버전과 실기 검증 결과는 구분해 회신합니다.
- 동기화는 소스 버전을 다룹니다. 각 PC의 로그인, SQLite 작업, 입력 파일, 가상환경과 같은 로컬 상태는 프로젝트 폴더에 보관합니다.
