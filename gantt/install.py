"""Установка, обновление на месте и удаление программы — без прав администратора, только для этого пользователя.

Установщик — это та же программа: файл GanttSetup-<версия>.exe (или Gantt.exe --install) копирует себя
в %LOCALAPPDATA%\\Programs\\Гант\\Gantt.exe, делает ярлыки и запись в «Приложения» Windows.
Запущенный .exe Windows удалить не даёт, но переименовать — даёт: так программа обновляет сама себя.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import APP_NAME, AUTHOR, __version__

EXE_NAME = "Gantt.exe"
PUBLISHER = AUTHOR
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Gantt"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def reg_key() -> str:
    """Ключ записи в «Приложениях». Тесты подменяют его своим (GANTT_REG_KEY), чтобы не трогать настоящий."""
    return os.environ.get("GANTT_REG_KEY") or UNINSTALL_KEY


def default_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "Programs" / APP_NAME


def nice_path(path: Path | str) -> str:
    """Путь внутри LOCALAPPDATA — как %LOCALAPPDATA%\\Гант: короче и одинаково у всех."""
    text = str(path)
    root = os.environ.get("LOCALAPPDATA")
    if root and text.lower().startswith(root.lower()):
        return "%LOCALAPPDATA%" + text[len(root):]
    return text


def frozen() -> bool:
    """Запущен собранный Gantt.exe, а не исходники."""
    return bool(getattr(sys, "frozen", False))


def current_exe() -> Path:
    return Path(sys.executable).resolve()


def is_setup_name(path: Path) -> bool:
    """GanttSetup-1.0.0.exe, «GanttSetup-1.0.0 (1).exe» — файл-установщик."""
    return path.name.lower().startswith("ganttsetup")


def parse_version(text: str) -> tuple[int, ...]:
    """«1.2.3» → (1, 2, 3); лишнее («1.2.3-beta», «v1.2») отбрасывается."""
    nums = []
    for part in text.strip().lstrip("vV").split("."):
        digits = ""
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        nums.append(int(digits))
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def short_version(text: str = __version__) -> str:
    """1.0.0 → «1.0», 1.2.3 → «1.2.3»: так версию называют люди."""
    v = parse_version(text)
    return f"{v[0]}.{v[1]}" if len(v) == 3 and v[2] == 0 else ".".join(map(str, v))


# ---------- запись в «Приложениях» Windows ----------
@dataclass
class Installed:
    folder: Path
    version: str

    @property
    def exe(self) -> Path:
        return self.folder / EXE_NAME


def installed() -> Installed | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_key()) as k:
            folder = winreg.QueryValueEx(k, "InstallLocation")[0]
            version = winreg.QueryValueEx(k, "DisplayVersion")[0]
    except (ImportError, OSError):
        return None
    return Installed(Path(folder), str(version)) if folder else None


def register(folder: Path, version: str = __version__) -> None:
    import winreg

    exe = folder / EXE_NAME
    size_kb = exe.stat().st_size // 1024 if exe.exists() else 0
    values = {
        "DisplayName": APP_NAME, "DisplayVersion": version, "Publisher": PUBLISHER,
        "DisplayIcon": f"{exe},0", "InstallLocation": str(folder),
        "UninstallString": f'"{exe}" --uninstall', "QuietUninstallString": f'"{exe}" --uninstall --silent',
        "InstallDate": date.today().strftime("%Y%m%d"), "Comments": "Диаграмма Ганта для управления проектами",
    }
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, reg_key()) as k:
        for name, value in values.items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
        for name, value in (("EstimatedSize", size_kb), ("NoModify", 1), ("NoRepair", 1)):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)


def unregister() -> None:
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, reg_key())
    except (ImportError, OSError):
        pass


def sync_registered_version() -> None:
    """После обновления на месте: в «Приложениях» — версия, которая сейчас запущена."""
    info = installed()
    if info is None or not frozen():
        return
    try:
        same = info.exe.resolve() == current_exe()
    except OSError:
        same = False
    if same and info.version != __version__:
        try:
            register(info.folder, __version__)
        except OSError:
            pass


# ---------- ярлыки ----------
def shortcut_dirs() -> dict[str, Path]:
    from PySide6.QtCore import QStandardPaths

    override = os.environ.get("GANTT_SHORTCUTS_DIR")   # тесты: не трогать настоящий рабочий стол и «Пуск»
    if override:
        return {"desktop": Path(override) / "Desktop", "start_menu": Path(override) / "Programs"}
    loc = QStandardPaths.StandardLocation
    return {"desktop": Path(QStandardPaths.writableLocation(loc.DesktopLocation)),
            "start_menu": Path(QStandardPaths.writableLocation(loc.ApplicationsLocation))}


def make_shortcut(target: Path, folder: Path) -> Path:
    from PySide6.QtCore import QFile

    folder.mkdir(parents=True, exist_ok=True)
    link = folder / f"{APP_NAME}.lnk"
    if link.exists():
        link.unlink()
    if not QFile.link(str(target), str(link)):
        raise OSError(f"Не удалось создать ярлык {link}")
    return link


def remove_shortcuts(dirs: dict[str, Path] | None = None) -> None:
    for folder in (dirs or shortcut_dirs()).values():
        link = folder / f"{APP_NAME}.lnk"
        try:
            link.unlink()
        except OSError:
            pass


# ---------- файлы ----------
def replace_exe(new: Path, target: Path) -> Path | None:
    """Поставить new на место target, даже если target сейчас запущен: запущенный файл переименовывается
    в Gantt.old*.exe (удалится при следующем запуске). Возвращает, куда переименован старый, или None."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and new.resolve() == target.resolve():
        return None      # установщик запущен из папки программы — копировать нечего
    old = None
    if target.exists():
        for i in range(20):
            cand = target.with_name(f"Gantt.old{i or ''}.exe")
            try:
                if cand.exists():
                    cand.unlink()
            except OSError:
                continue    # ещё занят прошлым запуском — берём другое имя
            os.replace(target, cand)
            old = cand
            break
        else:
            raise OSError(f"Не удалось освободить {target}")
    tmp = target.with_name(target.name + ".new")
    try:
        shutil.copyfile(new, tmp)
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)
        if old is not None and not target.exists():
            os.replace(old, target)       # вернуть как было
        raise
    return old


def cleanup_old(folder: Path | None = None) -> int:
    """Удалить Gantt.old*.exe, оставшиеся после обновления. Возвращает, сколько удалено."""
    folder = folder or (current_exe().parent if frozen() else None)
    if folder is None or not folder.exists():
        return 0
    n = 0
    for f in folder.glob("Gantt.old*.exe"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n


def cleanup_downloads() -> None:
    """Скачанные установщики нужны только до замены программы — потом это 50 МБ мусора."""
    from .paths import data_dir
    shutil.rmtree(data_dir() / "updates", ignore_errors=True)


@dataclass
class InstallOptions:
    folder: Path
    desktop: bool = True
    start_menu: bool = True
    update_source: str | None = None


def install(src: Path, opts: InstallOptions, dirs: dict[str, Path] | None = None) -> Path:
    """Скопировать программу, сделать ярлыки и запись в «Приложениях». Возвращает путь к Gantt.exe."""
    folder = opts.folder
    folder.mkdir(parents=True, exist_ok=True)
    probe = folder / ".gantt-write-test"
    probe.write_text("", encoding="utf-8")     # нет прав — ошибка сразу, до копирования
    probe.unlink()
    exe = folder / EXE_NAME
    replace_exe(src, exe)
    dirs = dirs or shortcut_dirs()
    remove_shortcuts(dirs)
    if opts.desktop:
        make_shortcut(exe, dirs["desktop"])
    if opts.start_menu:
        make_shortcut(exe, dirs["start_menu"])
    register(folder)
    if opts.update_source:
        from .settings import qsettings
        qs = qsettings()
        if not qs.value("update/source", ""):
            qs.setValue("update/source", opts.update_source)
            qs.sync()
    return exe


def sibling_update_source(setup: Path) -> str | None:
    """Установщик лежит в папке обновлений (рядом latest.json) — оттуда программа и будет обновляться."""
    return str(setup.parent) if (setup.parent / "latest.json").exists() else None


def remove_data() -> None:
    from .paths import data_dir
    shutil.rmtree(data_dir(), ignore_errors=True)


def uninstall(folder: Path, dirs: dict[str, Path] | None = None, keep_data: bool = True) -> None:
    """Ярлыки, запись и файлы. Запущенный Gantt.exe удаляет фоновый процесс, когда программа закроется."""
    remove_shortcuts(dirs)
    unregister()
    if not keep_data:
        remove_data()
    running = current_exe() if frozen() else None
    for f in list(folder.iterdir()) if folder.exists() else []:
        if running is not None and f.resolve() == running:
            continue
        try:
            shutil.rmtree(f) if f.is_dir() else f.unlink()
        except OSError:
            pass
    if running is not None and running.parent == folder.resolve():
        remove_later(folder)
    else:
        try:
            folder.rmdir()
        except OSError:
            pass


def remove_later(folder: Path) -> None:
    """Папку с запущенным .exe удалит PowerShell, как только программа закроется (пробует до 30 с)."""
    path = str(folder).replace("'", "''")
    script = (f"for($i=0;$i -lt 60;$i++){{try{{Remove-Item -LiteralPath '{path}' -Recurse -Force "
              f"-ErrorAction Stop;break}}catch{{Start-Sleep -Milliseconds 500}}}}")
    # только скрытая консоль: без консоли вовсе (DETACHED_PROCESS) PowerShell молча ничего не выполняет
    subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
                     creationflags=CREATE_NO_WINDOW, close_fds=True)


def launch(exe: Path, *args: str) -> None:
    """Запустить программу отдельным процессом — она переживёт закрытие этого."""
    subprocess.Popen([str(exe), *args], creationflags=DETACHED_PROCESS, close_fds=True, cwd=str(exe.parent))


def exe_version(path: Path) -> str | None:
    """Версия из свойств .exe («Свойства → Подробно»). None — файла нет или это не Гант."""
    try:
        import ctypes
        from ctypes import wintypes

        ver = ctypes.WinDLL("version")
    except (ImportError, OSError, AttributeError):
        return None
    ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    ver.GetFileVersionInfoW.restype = wintypes.BOOL
    ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(wintypes.UINT)]
    ver.VerQueryValueW.restype = wintypes.BOOL
    size = ver.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
        return None

    def string(name: str) -> str:
        ptr, n = ctypes.c_void_p(), wintypes.UINT()
        if ver.VerQueryValueW(buf, rf"\StringFileInfo\041904B0\{name}", ctypes.byref(ptr), ctypes.byref(n)) \
                and ptr.value and n.value:
            return ctypes.wstring_at(ptr.value)
        return ""

    return string("ProductVersion") or None if string("ProductName") == APP_NAME else None
