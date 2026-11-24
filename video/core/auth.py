"""Windows 로컬 브라우저에서 Google 데스크톱 앱 OAuth를 수행한다."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from requests.exceptions import RequestException

SCOPES = ["https://www.googleapis.com/auth/youtube"]


class AuthError(Exception):
    """인증 화면에 표시할 수 있는 한국어 오류."""


def _save_private(path: Path, text: str) -> None:
    # 같은 디렉터리에서 임시 파일을 닫은 뒤 교체해야 Windows에서도 원자적으로 저장된다.
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False,
            prefix=".youtube-auth-", suffix=".tmp",
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError:
        raise AuthError("인증 파일을 저장할 수 없습니다. 프로젝트 폴더의 쓰기 권한을 확인하세요.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _client_config(raw: bytes) -> dict[str, Any]:
    try:
        installed = json.loads(raw.decode("utf-8-sig"))["installed"]
        # 업로드한 파일의 임의 주소로 로그인 정보가 전송되지 않도록 Google 발급 주소만 사용한다.
        if not isinstance(installed, dict) or not all(
            isinstance(installed.get(key), str) and installed[key].strip()
            for key in ("client_id", "client_secret")
        ):
            raise ValueError
        if installed.get("auth_uri") not in {
            "https://accounts.google.com/o/oauth2/auth",
            "https://accounts.google.com/o/oauth2/v2/auth",
        } or installed.get("token_uri") != "https://oauth2.googleapis.com/token":
            raise ValueError
        return {"installed": installed}
    except (ValueError, KeyError, TypeError):
        raise AuthError(
            "Google Cloud Console에서 ‘데스크톱 앱’ 유형의 OAuth 클라이언트 JSON을 다시 다운로드하세요."
        ) from None


def save_client_secrets(root: Path, raw: bytes) -> None:
    """선택한 OAuth 클라이언트 JSON을 검증한 후 로컬 앱 폴더에 저장한다."""
    _save_private(root / "client_secrets.json", json.dumps(_client_config(raw)))


def _read_client(root: Path) -> dict[str, Any]:
    try:
        return _client_config((root / "client_secrets.json").read_bytes())
    except OSError:
        raise AuthError("client_secrets.json을 업로드하거나 영상 앱 폴더에 넣은 뒤 다시 로그인하세요.") from None


def _authorize(config: dict[str, Any]) -> Credentials:
    # 루프백 콜백은 임시 포트를 사용하며 OAuth 인증 코드는 앱 화면에 표시하지 않는다.
    flow = InstalledAppFlow.from_client_config(config, SCOPES, autogenerate_code_verifier=True)
    try:
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            open_browser=True,
            authorization_prompt_message=None,
            success_message="Sign-in complete. Return to the playlist app.",
            timeout_seconds=180,
            prompt="consent select_account",
            access_type="offline",
        )
    except Exception:
        raise AuthError("브라우저 로그인을 완료하지 못했습니다. 테스트 사용자, 네트워크 및 동의를 확인하고 다시 시도하세요.") from None
    if not credentials.refresh_token or (
        credentials.granted_scopes and not set(SCOPES).issubset(credentials.granted_scopes)
    ):
        raise AuthError("YouTube 권한을 모두 허용한 뒤 다시 로그인하세요.")
    return credentials


def get_service(root: Path, login: bool = False) -> Resource | None:
    """저장된 토큰을 사용한다. login=True이면 계정을 명시적으로 선택한다."""
    token = root / "token.json"
    if login:
        credentials = _authorize(_read_client(root))
        _save_private(token, credentials.to_json())
    elif not token.exists():
        return None
    else:
        try:
            stored = json.loads(token.read_text(encoding="utf-8-sig"))
            credentials = Credentials.from_authorized_user_info(stored)
            if not credentials.has_scopes(SCOPES):
                raise AuthError("저장한 토큰의 YouTube 권한이 부족합니다. Google 계정 로그인을 다시 진행하세요.")
            if (root / "client_secrets.json").is_file() and (
                _read_client(root)["installed"]["client_id"] != credentials.client_id
            ):
                raise AuthError("OAuth 클라이언트가 변경되었습니다. 새 클라이언트로 다시 로그인하세요.")
            if not credentials.valid:
                credentials.refresh(Request())
                _save_private(token, credentials.to_json())
        except (KeyError, TypeError, ValueError, GoogleAuthError, OSError, RequestException):
            raise AuthError("저장한 인증을 사용할 수 없습니다. 인터넷 연결을 확인한 뒤 Google 계정 로그인을 다시 진행하세요.") from None

    try:
        # 정적 discovery 문서를 사용하는 API 클라이언트 구성 자체는 유닛을 소비하지 않는다.
        return build("youtube", "v3", credentials=credentials, cache_discovery=False, static_discovery=True)
    except Exception:
        raise AuthError("YouTube API 클라이언트를 열 수 없습니다. 앱을 닫고 run.bat으로 다시 실행하세요.") from None
