"""Выложить собранную версию в выпуски GitHub — оттуда её заберут установленные программы.

Сначала сборка: .venv\\Scripts\\python tools\\build_exe.py
Потом одно из двух:

  set GITHUB_TOKEN=<токен>        — токен с правом «Contents: read and write» на этот репозиторий
  .venv\\Scripts\\python tools\\publish_github.py владелец/репозиторий

  .venv\\Scripts\\python tools\\publish_github.py владелец/репозиторий --prepare
      — только подготовить dist\\github; выпуск v<версия> создаёте на сайте GitHub сами
        и прикладываете оба файла оттуда (сначала установщик, потом latest.json).

Без GITHUB_TOKEN берётся вход в GitHub, сохранённый в Git (диспетчер учётных данных Windows, тот же,
что у git push); если его нет — Git сам предложит войти. Токен нигде не печатается и не записывается.
Репозиторий должен быть открытым: из закрытого установленные программы скачать выпуск не смогут.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gantt import APP_NAME, __version__  # noqa: E402
from gantt.install import short_version  # noqa: E402
from gantt.update import MANIFEST, github_site, release_notes, sha256_of  # noqa: E402

API = "https://api.github.com"


def tag(version: str) -> str:
    return f"v{version}"


def prepare(repo: str, setup: Path, out: Path, version: str = __version__, notes: str = "") -> list[Path]:
    """dist/github: установщик и latest.json, где файл — полная ссылка на этот выпуск (а не «последний»):
    если между чтением latest.json и скачиванием выйдет ещё версия, скачается ровно та, что описана."""
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    exe = out / setup.name
    shutil.copyfile(setup, exe)
    url = f"{github_site()}/{repo}/releases/download/{tag(version)}/{urllib.parse.quote(setup.name)}"
    manifest = {"version": version, "file": url, "sha256": sha256_of(exe), "size": exe.stat().st_size,
                "date": date.today().isoformat(), "notes": notes}
    man = out / MANIFEST
    man.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return [exe, man]      # порядок важен: сначала установщик, потом описание, которое на него ссылается


class GitHubError(Exception):
    pass


class GitHub:
    def __init__(self, repo: str, token: str, api: str | None = None):
        self.repo, self.token = repo, token
        self.api = (api or os.environ.get("GANTT_GITHUB_API") or API).rstrip("/")

    def call(self, method: str, url: str, body=None, data: bytes | None = None, ctype: str = "application/json"):
        if not url.startswith("http"):
            url = f"{self.api}/repos/{self.repo}{url}"
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": f"Gantt-publish/{__version__}",
            "Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 and method == "GET":
                return None
            try:
                msg = json.loads(e.read().decode("utf-8")).get("message", "")
            except ValueError:
                msg = ""
            hint = {401: "токен неверный или просрочен", 403: "у токена нет права записи в этот репозиторий",
                    404: "нет такого репозитория (или токен его не видит)"}.get(e.code, "")
            if "empty" in msg.lower():
                hint = "репозиторий пустой — добавьте в него хотя бы README на сайте GitHub"
            raise GitHubError(f"GitHub ответил {e.code}: {msg or e.reason}" + (f" — {hint}" if hint else "")) from None
        except OSError as e:
            raise GitHubError(f"Нет связи с GitHub: {e}") from None
        return json.loads(raw.decode("utf-8")) if raw else {}

    def release(self, version: str, notes: str) -> dict:
        rel = self.call("GET", f"/releases/tags/{tag(version)}")
        body = {"name": f"{APP_NAME} {short_version(version)}", "body": notes, "make_latest": "true"}
        if rel is None:
            return self.call("POST", "/releases", {"tag_name": tag(version), **body})
        return self.call("PATCH", f"/releases/{rel['id']}", body) | {"assets": rel.get("assets", [])}

    def upload(self, rel: dict, path: Path) -> None:
        for asset in rel.get("assets", []):       # выкладываем заново — старый файл с тем же именем убрать
            if asset.get("name") == path.name:
                self.call("DELETE", f"/releases/assets/{asset['id']}")
        base = rel["upload_url"].split("{", 1)[0]
        ctype = "application/json" if path.suffix == ".json" else "application/octet-stream"
        self.call("POST", f"{base}?name={urllib.parse.quote(path.name)}", data=path.read_bytes(), ctype=ctype)


def git_token() -> str | None:
    """Вход в GitHub из Git: git credential fill — тот же, которым пользуется git push."""
    try:
        r = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                           capture_output=True, text=True, encoding="utf-8", timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return next((ln[9:] for ln in r.stdout.splitlines() if ln.startswith("password=")), None)


def publish(repo: str, token: str, files: list[Path], version: str = __version__, notes: str = "") -> str:
    gh = GitHub(repo, token)
    rel = gh.release(version, notes)
    for f in files:
        print(f"  загружаю {f.name} ({f.stat().st_size / 1024 / 1024:.1f} МБ)…")
        gh.upload(rel, f)
    return rel.get("html_url", "")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Выложить версию в выпуски GitHub")
    ap.add_argument("repo", help="владелец/репозиторий, например imkita339-afk/gantt-releases")
    ap.add_argument("--prepare", action="store_true", help="только подготовить dist\\github для загрузки руками")
    args = ap.parse_args()
    repo = args.repo.strip().strip("/").removeprefix("https://github.com/").removesuffix(".git")
    setup = ROOT / "dist" / f"GanttSetup-{__version__}.exe"
    if not setup.exists():
        print(f"Нет {setup} — сначала соберите: .venv\\Scripts\\python tools\\build_exe.py")
        return 1
    notes = release_notes((ROOT / "docs" / "changes.md").read_text(encoding="utf-8"), __version__)
    files = prepare(repo, setup, ROOT / "dist" / "github", __version__, notes)
    if args.prepare:
        print(f"Готово: {files[0].parent}\nНа GitHub: Releases → Draft a new release → тег {tag(__version__)} → "
              f"приложите {files[0].name}, затем {files[1].name} → Publish release.")
        return 0
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or git_token()
    if not token:
        print("Нет входа в GitHub: set GITHUB_TOKEN=<токен> (или запустите с --prepare и загрузите файлы на сайте).")
        return 1
    try:
        url = publish(repo, token, files, __version__, notes)
    except GitHubError as e:
        print(e)
        return 1
    print(f"Выложено: {url}\nПрограммы с источником github:{repo} увидят версию {short_version()} при запуске.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
