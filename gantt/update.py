"""Обновления: где лежит новая версия, какая она, скачать и проверить.

Источник обновлений — папка (в том числе сетевая, \\\\сервер\\папка), адрес сайта или выпуски GitHub
(«github:владелец/репозиторий»). В нём лежат latest.json (что за версия, какой файл, контрольная сумма,
что нового) и сам GanttSetup-<версия>.exe. Для папки и сайта их готовит tools/build_exe.py в dist/update,
для GitHub — tools/publish_github.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import UPDATE_SOURCE, __version__
from .install import parse_version

MANIFEST = "latest.json"
CHUNK = 256 * 1024
TIMEOUT = 20


class UpdateError(Exception):
    """Понятное человеку сообщение: что не так с источником или файлом."""


@dataclass
class Manifest:
    version: str
    file: str               # имя файла рядом с latest.json или полный адрес
    sha256: str = ""
    size: int = 0
    date: str = ""
    notes: str = ""         # что нового — Markdown
    base: str = ""          # откуда прочитан latest.json (папка или адрес) — от неё считается file

    @property
    def location(self) -> str:
        """Где лежит установщик: абсолютный путь или адрес."""
        if is_url(self.file) or Path(self.file).is_absolute():
            return self.file
        if is_url(self.base):
            return urllib.parse.urljoin(self.base.rstrip("/") + "/", urllib.parse.quote(self.file))
        return str(Path(self.base) / self.file)

    def newer_than(self, version: str = __version__) -> bool:
        return parse_version(self.version) > parse_version(version)


def is_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


GITHUB = "https://github.com"


def github_repo(source: str) -> str | None:
    """«github:владелец/репозиторий» или https://github.com/владелец/репозиторий/… → «владелец/репозиторий»."""
    s = source.strip().strip('"')
    if s.lower().startswith("github:"):
        repo = s[7:].strip().strip("/")
    else:
        m = re.match(r"https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+)", s, re.IGNORECASE)
        if not m:
            return None
        repo = f"{m.group(1)}/{m.group(2)}"
    repo = repo.removesuffix(".git")
    return repo if re.fullmatch(r"[\w.-]+/[\w.-]+", repo) else None


def github_site() -> str:
    return os.environ.get("GANTT_GITHUB", GITHUB).rstrip("/")     # тесты подменяют своим сервером


def github_latest(repo: str) -> str:
    """Постоянная ссылка GitHub на файлы последнего выпуска — это не API, поэтому без лимита запросов."""
    return f"{github_site()}/{repo}/releases/latest/download"


def default_source() -> str:
    """Источник, если пользователь не указал своего: заложен при сборке (gantt/__init__.py)."""
    return UPDATE_SOURCE


def _manifest_ref(source: str) -> tuple[str, str]:
    """(где latest.json, база для файла). Источник — папка/адрес папки, сам latest.json или репозиторий GitHub."""
    source = source.strip().strip('"')
    if not source:
        raise UpdateError("Источник обновлений не задан. Укажите папку, адрес или github:владелец/репозиторий "
                          "в «Настройках» → «Обновление программы».")
    repo = github_repo(source)
    if repo:
        base = github_latest(repo)
        return base + "/" + MANIFEST, base
    if is_url(source):
        if source.lower().endswith(".json"):
            return source, source.rsplit("/", 1)[0]
        return source.rstrip("/") + "/" + MANIFEST, source.rstrip("/")
    p = Path(source)
    if p.suffix.lower() == ".json":
        return str(p), str(p.parent)
    return str(p / MANIFEST), str(p)


def _open(ref: str):
    if is_url(ref):
        req = urllib.request.Request(ref, headers={"User-Agent": f"Gantt/{__version__}", "Cache-Control": "no-cache"})
        return urllib.request.urlopen(req, timeout=TIMEOUT)
    return open(ref, "rb")


def read_manifest(source: str) -> Manifest:
    ref, base = _manifest_ref(source)
    try:
        with _open(ref) as f:
            raw = f.read(1024 * 1024)
    except FileNotFoundError:
        raise UpdateError(f"В источнике обновлений нет {MANIFEST}: {ref}") from None
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise UpdateError(f"Источник обновлений ответил ошибкой {e.code}: {ref}") from None
        hint = " На GitHub ещё нет выпуска с latest.json — или репозиторий закрытый." if github_repo(source) else ""
        raise UpdateError(f"В источнике обновлений нет {MANIFEST}: {ref}.{hint}") from None
    except OSError as e:     # сюда же — ошибки сети urllib
        raise UpdateError(f"Источник обновлений недоступен: {ref}\n{e}") from None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
        m = Manifest(version=str(data["version"]), file=str(data["file"]), sha256=str(data.get("sha256", "")),
                     size=int(data.get("size") or 0), date=str(data.get("date", "")), notes=str(data.get("notes", "")),
                     base=base)
    except (ValueError, KeyError, TypeError):
        raise UpdateError(f"{MANIFEST} в источнике обновлений повреждён: {ref}") from None
    if parse_version(m.version) == (0, 0, 0):
        raise UpdateError(f"В {MANIFEST} не указана версия: {ref}")
    return m


def download(m: Manifest, dest_dir: Path, progress=None, cancelled=lambda: False) -> Path:
    """Скачать установщик во временный файл, сверить размер и контрольную сумму. progress(сделано, всего)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(urllib.parse.urlparse(m.location).path).name if is_url(m.location) else Path(m.location).name
    dest = dest_dir / (urllib.parse.unquote(name) or f"GanttSetup-{m.version}.exe")
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with _open(m.location) as src, open(part, "wb") as out:
            total = m.size or int(getattr(src, "headers", {}).get("Content-Length", 0) or 0)
            while True:
                if cancelled():
                    raise UpdateError("Скачивание отменено.")
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except FileNotFoundError:
        part.unlink(missing_ok=True)
        raise UpdateError(f"В источнике обновлений нет файла {m.location}") from None
    except OSError as e:
        part.unlink(missing_ok=True)
        raise UpdateError(f"Не удалось скачать {m.location}\n{e}") from None
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    if m.size and done != m.size:
        part.unlink(missing_ok=True)
        raise UpdateError(f"Файл обновления неполный: {done} из {m.size} байт. Попробуйте ещё раз.")
    if m.sha256 and digest.hexdigest().lower() != m.sha256.lower():
        part.unlink(missing_ok=True)
        raise UpdateError("Файл обновления повреждён: контрольная сумма не совпала. Попробуйте ещё раз.")
    part.replace(dest)
    return dest


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def publish(setup: Path, folder: Path, version: str = __version__, notes: str = "") -> Path:
    """Положить установщик и latest.json в папку обновлений (это делает tools/build_exe.py)."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / setup.name
    if setup.resolve() != target.resolve():
        shutil.copyfile(setup, target)
    manifest = {"version": version, "file": setup.name, "sha256": sha256_of(target), "size": target.stat().st_size,
                "date": date.today().isoformat(), "notes": notes}
    out = folder / MANIFEST
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def release_notes(changelog: str, version: str) -> str:
    """Раздел «## 1.1.0 …» из «Что нового» (docs/changes.md) — без заголовка."""
    want = parse_version(version)
    lines, keep = [], False
    for line in changelog.splitlines():
        if line.startswith("## "):
            if keep:
                break
            head = line[3:].strip().split()[0] if line[3:].strip() else ""
            keep = parse_version(head) == want
            continue
        if keep:
            lines.append(line)
    return "\n".join(lines).strip()


def cli_update(source: str | None = None) -> int:
    """Gantt.exe --update: без окон — для администратора или планировщика заданий.
    0 — обновлено, 3 — уже последняя версия, 1 — ошибка. Открытая программа доработает в старой версии
    и получит новую при следующем запуске."""
    from . import install as inst
    from .paths import data_dir
    from .settings import qsettings

    source = source or str(qsettings().value("update/source", "") or "") or default_source()
    try:
        m = read_manifest(source)
        if not m.newer_than():
            print(f"Уже последняя версия: {__version__}")
            return 3
        print(f"Скачиваю {m.version}: {m.location}")
        setup = download(m, data_dir() / "updates")
        target = inst.current_exe() if inst.frozen() else None
        if target is None:
            print(f"Запущено из исходников — заменять нечего. Установщик: {setup}")
            return 1
        inst.replace_exe(setup, target)
        inst.cleanup_downloads()
        info = inst.installed()
        if info is not None and info.exe.resolve() == target:
            inst.register(info.folder, m.version)
    except (UpdateError, OSError) as e:
        print(f"Ошибка обновления: {e}")
        return 1
    print(f"Обновлено до {m.version}: {target}")
    return 0
