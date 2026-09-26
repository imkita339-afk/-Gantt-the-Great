"""Источник по HTTP-контракту (docs/api.md): 1С или любая система. Профили подключений, пароль — в Windows.

Пароль не пишется ни в настройки, ни в файлы программы: он хранится в диспетчере учётных данных Windows.
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from urllib.parse import quote

from . import APP_NAME, __version__
from .paths import data_dir


@dataclass
class Profile:
    name: str
    url: str                      # базовый адрес, например http://сервер/база/hs/gantt/v1
    login: str = ""               # пусто — без авторизации
    timeout: float = 30.0
    refresh_minutes: int = 5      # 0 — обновлять только вручную

    @property
    def base(self) -> str:
        return self.url.rstrip("/")


class HttpError(Exception):
    def __init__(self, message: str, details: list[str] | None = None, offline: bool = False, code: int = 0):
        super().__init__(message)
        self.message, self.details, self.offline, self.code = message, details or [], offline, code


# ---------- профили ----------
def _profiles_path():
    return data_dir() / "connections.json"


def load_profiles() -> list[Profile]:
    try:
        raw = json.loads(_profiles_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for d in raw if isinstance(raw, list) else []:
        try:
            out.append(Profile(**{k: v for k, v in d.items() if k in Profile.__dataclass_fields__}))
        except TypeError:
            continue
    return out


def save_profiles(profiles: list[Profile]) -> None:
    _profiles_path().write_text(json.dumps([asdict(p) for p in profiles], ensure_ascii=False, indent=1),
                                encoding="utf-8")


# ---------- пароль: диспетчер учётных данных Windows ----------
_memory: dict[str, str] = {}      # не Windows (разработка, тесты) — только на время работы программы


def _target(p: Profile) -> str:
    return f"{APP_NAME}:{p.base}|{p.login}"


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _CRED(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]

    _adv = ctypes.WinDLL("advapi32", use_last_error=True)
    _adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CRED))]
    _adv.CredWriteW.argtypes = [ctypes.POINTER(_CRED), wintypes.DWORD]
    _adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _adv.CredFree.argtypes = [ctypes.c_void_p]
    _GENERIC, _LOCAL_MACHINE = 1, 2

    def get_password(p: Profile) -> str | None:
        ptr = ctypes.POINTER(_CRED)()
        if not _adv.CredReadW(_target(p), _GENERIC, 0, ctypes.byref(ptr)):
            return _memory.get(_target(p))
        try:
            c = ptr.contents
            return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
        finally:
            _adv.CredFree(ptr)

    def set_password(p: Profile, password: str) -> bool:
        blob = password.encode("utf-16-le")
        buf = ctypes.create_string_buffer(blob, len(blob))
        cred = _CRED(Type=_GENERIC, TargetName=_target(p), CredentialBlobSize=len(blob),
                     CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)), Persist=_LOCAL_MACHINE,
                     UserName=p.login)
        return bool(_adv.CredWriteW(ctypes.byref(cred), 0))

    def forget_password(p: Profile) -> None:
        _memory.pop(_target(p), None)
        _adv.CredDeleteW(_target(p), _GENERIC, 0)
else:
    def get_password(p: Profile) -> str | None:
        return _memory.get(_target(p))

    def set_password(p: Profile, password: str) -> bool:
        _memory[_target(p)] = password
        return False

    def forget_password(p: Profile) -> None:
        _memory.pop(_target(p), None)


def remember_for_session(p: Profile, password: str) -> None:
    """Пароль только до закрытия программы (флажок «сохранить» снят)."""
    _memory[_target(p)] = password


# ---------- запросы ----------
class HttpClient:
    def __init__(self, profile: Profile, password: str | None = None):
        self.p = profile
        self.password = password if password is not None else get_password(profile)

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.p.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", f"Gantt/{__version__}")
        if data is not None:
            req.add_header("Content-Type", "application/json; charset=utf-8")
        if self.p.login:
            token = base64.b64encode(f"{self.p.login}:{self.password or ''}".encode("utf-8")).decode("ascii")
            req.add_header("Authorization", "Basic " + token)
        try:
            with urllib.request.urlopen(req, timeout=self.p.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            try:
                info = json.loads(e.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError, OSError):
                info = {}
            msg = info.get("error") if isinstance(info, dict) else None
            details = info.get("details", []) if isinstance(info, dict) else []
            if e.code in (401, 403):
                raise HttpError(msg or "Нет доступа: проверьте логин и пароль.", details, code=e.code)
            if e.code == 404:
                raise HttpError("По этому адресу нет сервиса Ганта. Проверьте адрес (должен кончаться на /v1).",
                                code=404)
            if e.code >= 500:
                raise HttpError(msg or f"Ошибка сервера ({e.code}).", details, offline=True, code=e.code)
            raise HttpError(msg or f"Сервер отказал ({e.code}).", details, code=e.code)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise HttpError(f"Нет связи с сервером: {reason}", offline=True)
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as e:
            raise HttpError("Сервер ответил не JSON.", [str(e)])

    def ping(self) -> dict:
        return self._request("GET", "/ping")

    def export(self, cursor: str | None = None) -> dict:
        return self._request("GET", "/export" + (f"?changedSince={quote(cursor)}" if cursor else ""))

    def send(self, changes_doc: dict) -> dict:
        return self._request("POST", "/import", changes_doc)


def cache_path(p: Profile):
    import hashlib

    folder = data_dir() / "cache"
    folder.mkdir(exist_ok=True)
    return folder / (hashlib.md5(p.base.encode("utf-8")).hexdigest() + ".gantt.json")
