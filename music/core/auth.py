"""브라우저 요청 헤더 정제 및 로컬 계정 인증."""

import os
import re
import tempfile
from pathlib import Path

from requests.exceptions import ConnectionError, Timeout
from ytmusicapi import YTMusic, setup


class AuthError(ValueError):
    """화면에 표시할 수 있는 인증 오류. 헤더 값은 포함하지 않는다."""


# 계정 선택과 인증에 필요한 헤더만 보관한다. 요청 주소는 라이브러리에서 결정한다.
_FIELDS = {
    "authorization": "Authorization",
    "cookie": "Cookie",
    "x-goog-authuser": "X-Goog-AuthUser",
    "x-goog-pageid": "X-Goog-PageId",
    "x-goog-visitor-id": "X-Goog-Visitor-Id",
}
_HEADER = re.compile(r"^([A-Za-z][A-Za-z0-9-]*):\s*(.*)$")
_BARE_HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")


def clean_headers(raw: str) -> str:
    """DevTools의 한 줄/두 줄 표기, 접힌 헤더, HTTP/2 의사 헤더를 처리한다."""
    values: dict[str, str] = {}
    active: str | None = None
    for line in raw.splitlines():
        part = line.strip()
        if not part:
            continue
        if part.startswith(":"):
            active = None
            continue
        match = _HEADER.match(part)
        if match:
            active = match[1].lower()
            if active in _FIELDS:
                values[active] = match[2].strip()
            continue
        if active in _FIELDS and (not values[active] or line[0].isspace()) and part.lower() not in _FIELDS:
            values[active] = f"{values[active]} {part}".strip()
        elif _BARE_HEADER.fullmatch(part):
            active = part.lower()
            if active in _FIELDS:
                values[active] = ""
        elif active in _FIELDS:
            # Cookie 문자열이 줄바꿈된 복사 형식도 받아들인다.
            values[active] = f"{values[active]} {part}".strip()

    missing = [_FIELDS[key] for key in ("authorization", "cookie", "x-goog-authuser") if not values.get(key)]
    if missing:
        raise AuthError(f"{', '.join(missing)} 헤더가 없습니다. 로그인한 계정의 browse 요청에서 요청 헤더 전체를 복사하세요.")
    return "\n".join(f"{key}: {value}" for key, value in values.items())


def connect_account(path: Path) -> tuple[YTMusic, str]:
    """저장 파일을 읽고 로그인 계정 전용 API로 세션을 검증한다."""
    if not path.is_file():
        raise AuthError("browser.json 파일이 없습니다. 아래 안내에 따라 요청 헤더를 입력하세요.")
    try:
        client = YTMusic(str(path))
        info = client.get_account_info()
        if not info.get("accountName"):
            raise AuthError("계정 정보를 받지 못했습니다. 로그인 상태와 요청 헤더를 확인하세요.")
    except (ConnectionError, Timeout):
        raise AuthError("YouTube Music에 연결할 수 없습니다. 인터넷 연결을 확인한 후 다시 시도하세요.") from None
    except AuthError:
        raise
    except Exception:
        raise AuthError("계정 연결에 실패했습니다. 네트워크를 확인하고 로그인한 계정의 새 browse 헤더로 인증하세요.") from None
    handle = info.get("channelHandle")
    label = f"{info['accountName']} ({handle})" if handle else str(info["accountName"])
    return client, label


def connect(path: Path) -> YTMusic:
    return connect_account(path)[0]


def setup_account(path: Path, raw: str) -> tuple[YTMusic, str]:
    """검증에 성공한 파일만 원자적으로 게시하여 기존 인증을 보존한다."""
    headers = clean_headers(raw)
    temporary: Path | None = None
    try:
        # Windows에서는 열린 임시 파일에 setup()이 쓸 수 없어 먼저 닫는다.
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".ytmusic-auth-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
        setup(filepath=str(temporary), headers_raw=headers)
        account = connect_account(temporary)
        os.replace(temporary, path)
        return account
    except AuthError:
        raise
    except OSError:
        raise AuthError("인증 파일을 저장할 수 없습니다. 앱 폴더의 쓰기 권한을 확인하세요.") from None
    except Exception:
        raise AuthError("인증 파일을 만들 수 없습니다. browse 요청의 헤더를 다시 복사하세요.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def setup_and_connect(path: Path, raw: str) -> YTMusic:
    return setup_account(path, raw)[0]
