"""Установка, обновление и руководство — без настоящего реестра, рабочего стола и сети."""
import http.server
import sys
import urllib.parse
import json
import threading
import uuid
import zlib
from functools import partial
from pathlib import Path

import pytest

from gantt import __version__
from gantt import install as inst
from gantt import update as upd


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Свой ключ реестра и своя папка ярлыков: настоящие «Приложения» и рабочий стол не трогаем."""
    key = rf"Software\GanttTest\Uninstall-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("GANTT_REG_KEY", key)
    monkeypatch.setenv("GANTT_SHORTCUTS_DIR", str(tmp_path / "shortcuts"))
    yield
    inst.unregister()
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\GanttTest")
    except OSError:
        pass


def fake_exe(path: Path, payload: bytes = b"MZ fake gantt") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def test_versions():
    assert inst.parse_version("1.0") == (1, 0, 0)
    assert inst.parse_version("v1.2.3-beta") == (1, 2, 3)
    assert inst.parse_version("1.10.0") > inst.parse_version("1.9.9")
    assert inst.short_version("1.0.0") == "1.0" and inst.short_version("1.2.3") == "1.2.3"
    assert __version__ == "1.0.0"
    assert inst.is_setup_name(Path("GanttSetup-1.0.0 (1).exe")) and not inst.is_setup_name(Path("Gantt.exe"))


def test_install_register_shortcuts_and_uninstall(tmp_path):
    src = fake_exe(tmp_path / "dl" / "GanttSetup-1.0.0.exe")
    (src.parent / "latest.json").write_text("{}", encoding="utf-8")
    folder = tmp_path / "Programs" / "Гант"
    source = inst.sibling_update_source(src)
    exe = inst.install(src, inst.InstallOptions(folder, desktop=True, start_menu=True, update_source=source))
    assert exe == folder / "Gantt.exe" and exe.read_bytes() == src.read_bytes()
    dirs = inst.shortcut_dirs()
    assert (dirs["desktop"] / "Гант.lnk").exists() and (dirs["start_menu"] / "Гант.lnk").exists()
    info = inst.installed()
    assert info is not None and info.folder == folder and info.version == __version__
    from gantt.settings import qsettings
    assert qsettings().value("update/source") == str(src.parent)       # обновления — из папки установщика
    inst.uninstall(folder)
    assert inst.installed() is None and not folder.exists()
    assert not (dirs["desktop"] / "Гант.lnk").exists()


def test_install_without_shortcuts_over_previous(tmp_path):
    folder = tmp_path / "Гант"
    inst.install(fake_exe(tmp_path / "a.exe", b"old"), inst.InstallOptions(folder, False, False))
    inst.install(fake_exe(tmp_path / "b.exe", b"new"), inst.InstallOptions(folder, False, False))
    assert (folder / "Gantt.exe").read_bytes() == b"new"
    assert not list(inst.shortcut_dirs()["desktop"].glob("*.lnk"))
    assert inst.cleanup_old(folder) <= 1 and not list(folder.glob("Gantt.old*.exe"))


def test_replace_exe_keeps_old_until_cleanup(tmp_path):
    target = fake_exe(tmp_path / "app" / "Gantt.exe", b"v1")
    new = fake_exe(tmp_path / "GanttSetup-1.1.0.exe", b"v2")
    old = inst.replace_exe(new, target)
    assert target.read_bytes() == b"v2" and old is not None and old.read_bytes() == b"v1"
    assert inst.cleanup_old(target.parent) == 1 and not old.exists()


def make_release(folder: Path, version: str, payload: bytes = b"MZ new version", notes: str = "- стало лучше") -> Path:
    setup = fake_exe(folder.parent / "build" / f"GanttSetup-{version}.exe", payload)
    return upd.publish(setup, folder, version, notes)


def test_manifest_from_folder_and_download(tmp_path):
    folder = tmp_path / "Обновления Гант"
    make_release(folder, "1.1.0")
    m = upd.read_manifest(str(folder))
    assert m.version == "1.1.0" and m.newer_than("1.0.0") and not m.newer_than("1.1.0")
    assert m.notes == "- стало лучше" and Path(m.location) == folder / "GanttSetup-1.1.0.exe"
    assert upd.read_manifest(str(folder / "latest.json")).version == "1.1.0"
    seen = []
    got = upd.download(m, tmp_path / "dl", progress=lambda d, t: seen.append((d, t)))
    assert got.read_bytes() == b"MZ new version" and seen[-1][0] == seen[-1][1]


def test_download_rejects_damaged_file(tmp_path):
    folder = tmp_path / "upd"
    make_release(folder, "1.1.0")
    (folder / "GanttSetup-1.1.0.exe").write_bytes(b"MZ new versioX")      # тот же размер, другая сумма
    with pytest.raises(upd.UpdateError, match="контрольная сумма"):
        upd.download(upd.read_manifest(str(folder)), tmp_path / "dl")
    assert not list((tmp_path / "dl").glob("*"))


def test_manifest_errors(tmp_path):
    with pytest.raises(upd.UpdateError, match="не задан"):
        upd.read_manifest("")
    with pytest.raises(upd.UpdateError, match="нет latest.json"):
        upd.read_manifest(str(tmp_path))
    (tmp_path / "latest.json").write_text("{не json", encoding="utf-8")
    with pytest.raises(upd.UpdateError, match="повреждён"):
        upd.read_manifest(str(tmp_path))


def test_manifest_over_http(tmp_path):
    folder = tmp_path / "site"
    make_release(folder, "2.0.0")
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/gantt/"
        (folder / "gantt").mkdir()
        for f in ("latest.json", "GanttSetup-2.0.0.exe"):
            (folder / "gantt" / f).write_bytes((folder / f).read_bytes())
        m = upd.read_manifest(url)
        assert m.location == url + "GanttSetup-2.0.0.exe"
        assert upd.download(m, tmp_path / "dl").read_bytes() == b"MZ new version"
    finally:
        srv.shutdown()


def test_release_notes_from_changelog(root):
    text = (root / "docs" / "changes.md").read_text(encoding="utf-8")
    notes = upd.release_notes(text, __version__)
    assert notes and "##" not in notes


def test_window_checks_updates(make_win, tmp_path):
    from gantt.ui import update as ui_update
    from gantt.ui.widgets import Toast

    w = make_win()
    folder = tmp_path / "upd"
    make_release(folder, "9.0.0")
    w.set_update_options(source=str(folder))
    w.check_updates(silent=True)
    assert Toast.current is not None                       # «Доступна версия 9.0»
    w.qs.setValue("update/skip", "9.0.0")
    Toast.current = None
    w.check_updates(silent=True)
    assert Toast.current is None                           # пропущенную версию при запуске не предлагаем
    dlg = ui_update.UpdateDialog(w, upd.read_manifest(str(folder)))
    dlg._download()
    assert dlg.path is not None and dlg.path.read_bytes() == b"MZ new version"


def test_help_window_and_pdf(qapp, tmp_path):
    from gantt.ui.help import HelpWindow

    h = HelpWindow()
    heads = h.headings()
    assert len(heads) > 10 and any("Установка" in x for x in heads)
    assert h.open_section("Горячие клавиши")
    pdf = tmp_path / "guide.pdf"
    assert h.save_pdf(str(pdf)) and pdf.read_bytes()[:4] == b"%PDF" and pdf.stat().st_size > 20000


def test_guide_images_exist(root):
    import re
    text = (root / "docs" / "guide.md").read_text(encoding="utf-8")
    images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
    assert images
    for img in images:
        assert (root / "docs" / img).exists(), img


def test_setup_dialog_installs(qapp, tmp_path, monkeypatch):
    from gantt.ui import setup

    src = fake_exe(tmp_path / "GanttSetup-1.0.0.exe")
    dlg = setup.SetupDialog(src)
    dlg.folder.setText(str(tmp_path / "Гант"))
    dlg.desktop.setChecked(False)
    monkeypatch.setattr(setup, "is_running", lambda *a: False)
    dlg._install()
    assert dlg.pages.currentIndex() == 2 and (tmp_path / "Гант" / "Gantt.exe").exists()
    assert inst.installed().folder == tmp_path / "Гант"
    un = setup.UninstallDialog(tmp_path / "Гант")
    un.run()
    assert inst.installed() is None and not (tmp_path / "Гант").exists()


def test_manifest_json_written_by_publish(tmp_path):
    out = make_release(tmp_path / "u", "1.2.0")
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["file"] == "GanttSetup-1.2.0.exe" and len(data["sha256"]) == 64 and data["size"] > 0


def test_cli_update_reports(tmp_path, capsys):
    folder = tmp_path / "upd"
    make_release(folder, __version__)
    assert upd.cli_update(str(folder)) == 3                  # уже последняя
    make_release(folder, "9.9.0")
    assert upd.cli_update(str(folder)) == 1                  # из исходников заменять нечего, но скачал
    assert "Установщик" in capsys.readouterr().out
    assert upd.cli_update(str(tmp_path / "нет")) == 1


def test_remove_later_deletes_folder_in_background(tmp_path):
    import time
    folder = fake_exe(tmp_path / "Programs" / "Гант" / "Gantt.exe").parent
    inst.remove_later(folder)
    for _ in range(60):
        if not folder.exists():
            break
        time.sleep(0.25)
    assert not folder.exists()


class FakeGitHub(http.server.BaseHTTPRequestHandler):
    """API выпусков, загрузка файлов и сайт с переадресацией «latest/download» — как у настоящего GitHub."""

    releases: dict = {}      # тег → {"id", "assets": {имя: байты}}

    def log_message(self, *a):
        pass

    def _json(self, code, data):
        raw = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _base(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def _rel(self, tag):
        r = self.releases[tag]
        return {"id": r["id"], "tag_name": tag, "html_url": f"{self._base()}/o/r/releases/tag/{tag}",
                "upload_url": f"{self._base()}/uploads/{r['id']}{{?name,label}}",
                "assets": [{"id": zlib.crc32(n.encode()), "name": n} for n in r["assets"]]}   # номера постоянные

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/repos/o/r/releases/tags/"):
            tag = path.rsplit("/", 1)[1]
            return self._json(200, self._rel(tag)) if tag in self.releases else self._json(404, {"message": "Not Found"})
        if path.startswith("/o/r/releases/latest/download/"):
            if not self.releases:
                return self._json(404, {})
            tag = sorted(self.releases)[-1]
            self.send_response(302)
            self.send_header("Location", f"/o/r/releases/download/{tag}/{path.rsplit('/', 1)[1]}")
            self.end_headers()
            return
        if path.startswith("/o/r/releases/download/"):
            _, tag, name = path.rsplit("/", 2)
            data = self.releases.get(tag, {}).get("assets", {}).get(urllib.parse.unquote(name))
            if data is None:
                return self._json(404, {})
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._json(404, {})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if u.path == "/api/repos/o/r/releases":
            tag = json.loads(body)["tag_name"]
            self.releases[tag] = {"id": len(self.releases) + 1, "assets": {}}
            return self._json(201, self._rel(tag))
        if u.path.startswith("/uploads/"):
            rid = int(u.path.rsplit("/", 1)[1])
            name = urllib.parse.parse_qs(u.query)["name"][0]
            rel = next(r for r in self.releases.values() if r["id"] == rid)
            rel["assets"][name] = body
            return self._json(201, {"name": name})
        self._json(404, {})

    def do_PATCH(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        rid = int(self.path.rsplit("/", 1)[1])
        tag = next(t for t, r in self.releases.items() if r["id"] == rid)
        self._json(200, self._rel(tag))

    def do_DELETE(self):
        aid = int(self.path.rsplit("/", 1)[1])
        for r in self.releases.values():
            for n in list(r["assets"]):
                if zlib.crc32(n.encode()) == aid:
                    del r["assets"][n]
        self.send_response(204)
        self.end_headers()


def test_github_releases_round_trip(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import publish_github

    FakeGitHub.releases = {}
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setenv("GANTT_GITHUB", base)
    monkeypatch.setenv("GANTT_GITHUB_API", base + "/api")
    try:
        with pytest.raises(upd.UpdateError, match="нет выпуска"):
            upd.read_manifest("github:o/r")
        setup = fake_exe(tmp_path / "GanttSetup-9.1.0.exe", b"MZ from github")
        files = publish_github.prepare("o/r", setup, tmp_path / "github", "9.1.0", "- с гитхаба")
        assert [f.name for f in files] == ["GanttSetup-9.1.0.exe", "latest.json"]
        url = publish_github.publish("o/r", "token", files, "9.1.0", "- с гитхаба")
        assert url.endswith("/releases/tag/v9.1.0")
        for source in ("github:o/r", "https://github.com/o/r"):
            m = upd.read_manifest(source)
            assert m.version == "9.1.0" and m.notes == "- с гитхаба"
            assert m.location == f"{base}/o/r/releases/download/v9.1.0/GanttSetup-9.1.0.exe"
        assert upd.download(m, tmp_path / "dl").read_bytes() == b"MZ from github"
        publish_github.publish("o/r", "token", files, "9.1.0", "- ещё раз")   # повторная выкладка той же версии
        assert set(FakeGitHub.releases["v9.1.0"]["assets"]) == {"GanttSetup-9.1.0.exe", "latest.json"}
    finally:
        srv.shutdown()


def test_github_source_names():
    assert upd.github_repo("github:imkita/gantt-releases") == "imkita/gantt-releases"
    assert upd.github_repo("https://github.com/imkita/gantt-releases.git") == "imkita/gantt-releases"
    assert upd.github_repo("https://github.com/o/r/releases/latest") == "o/r"
    assert upd.github_repo(r"\server\soft\Гант") is None and upd.github_repo("https://example.com/a/b") is None
