"""Сборка: Gantt.exe, установщик GanttSetup-<версия>.exe и папка обновлений.

Запуск: .venv\\Scripts\\python tools\\build_exe.py
  dist\\Gantt.exe                  — программа (можно запускать и без установки)
  dist\\GanttSetup-<версия>.exe     — тот же файл под именем установщика: двойной щелчок — установка
  dist\\update\\                     — установщик + latest.json: скопируйте содержимое в источник обновлений
Нужен PyInstaller в окружении: .venv\\Scripts\\python -m pip install -r requirements-dev.txt
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gantt import APP_NAME, AUTHOR, __version__  # noqa: E402
from gantt.update import publish, release_notes  # noqa: E402

BUILD = ROOT / "build"
VERSION_FILE = BUILD / "version_info.txt"


def version_info() -> None:
    nums = [int(x) for x in __version__.split(".")] + [0]
    t = tuple(nums[:4])
    VERSION_FILE.parent.mkdir(exist_ok=True)
    VERSION_FILE.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
                    date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041904B0', [
      StringStruct('FileDescription', '{APP_NAME} — диаграмма Ганта для управления проектами'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('CompanyName', '{AUTHOR}'),
      StringStruct('LegalCopyright', '© 2026 {AUTHOR}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('ProductVersion', '{__version__}'),
      StringStruct('OriginalFilename', 'Gantt.exe'),
      StringStruct('InternalName', 'Gantt')])]),
    VarFileInfo([VarStruct('Translation', [0x419, 1200])])
  ]
)
""", encoding="utf-8")


def main() -> None:
    version_info()
    sep = ";"  # разделитель --add-data на Windows
    args = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "Gantt",
        "--icon", str(ROOT / "gantt" / "resources" / "gantt.ico"),
        "--version-file", str(VERSION_FILE),
        "--paths", str(ROOT),
        "--add-data", f"{ROOT / 'schema' / 'gantt-exchange.schema.json'}{sep}schema",
        "--add-data", f"{ROOT / 'examples' / 'full.gantt.json'}{sep}examples",
        "--add-data", f"{ROOT / 'gantt' / 'resources'}{sep}gantt/resources",
        "--add-data", f"{ROOT / 'docs' / 'guide.md'}{sep}docs",
        "--add-data", f"{ROOT / 'docs' / 'changes.md'}{sep}docs",
        "--add-data", f"{ROOT / 'docs' / 'img'}{sep}docs/img",
        "--exclude-module", "tkinter",
        "--distpath", str(ROOT / "dist"), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
        str(ROOT / "gantt" / "__main__.py"),
    ]
    subprocess.run(args, check=True, cwd=ROOT)
    exe = ROOT / "dist" / "Gantt.exe"
    setup = ROOT / "dist" / f"GanttSetup-{__version__}.exe"
    shutil.copyfile(exe, setup)          # установщик — та же программа: узнаёт себя по имени файла
    notes = release_notes((ROOT / "docs" / "changes.md").read_text(encoding="utf-8"), __version__)
    update_dir = ROOT / "dist" / "update"
    shutil.rmtree(update_dir, ignore_errors=True)
    manifest = publish(setup, update_dir, __version__, notes)
    print(f"Готово: {exe} ({exe.stat().st_size / 1024 / 1024:.0f} МБ)")
    print(f"Установщик: {setup}")
    print(f"Папка обновлений: {update_dir} ({manifest.name} + {setup.name}) — скопируйте в источник обновлений")


if __name__ == "__main__":
    main()
