"""Точка входа: разбор командной строки, запуск окна, открытие файла."""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

from . import APP_NAME, __version__


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="Gantt", description=f"{APP_NAME} — диаграмма Ганта для управления проектами.")
    parser.add_argument("path", nargs="?", help="файл для открытия: Excel-отчёт 1С (.xlsx) или файл обмена (.json)")
    parser.add_argument("--import", dest="import_path", metavar="ПУТЬ", help="то же, что путь")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    setup = parser.add_argument_group("установка и удаление (для администраторов)")
    setup.add_argument("--install", action="store_true", help="установить программу — то же, что запустить "
                       "GanttSetup-*.exe")
    setup.add_argument("--uninstall", action="store_true", help="удалить программу")
    setup.add_argument("--silent", action="store_true", help="без окон: для развёртывания на много ПК")
    setup.add_argument("--dir", metavar="ПАПКА", help="куда установить (по умолчанию %%LOCALAPPDATA%%\Programs\Гант)")
    setup.add_argument("--no-desktop", action="store_true", help="без ярлыка на рабочем столе")
    setup.add_argument("--no-start-menu", action="store_true", help="без ярлыка в меню «Пуск»")
    setup.add_argument("--update-source", metavar="ПАПКА|АДРЕС", help="откуда программа будет брать обновления")
    setup.add_argument("--remove-data", action="store_true", help="при удалении стереть и правки, и настройки")
    setup.add_argument("--update", action="store_true", help="обновить без окон из источника обновлений "
                       "(или --update-source): 0 — обновлено, 3 — уже последняя версия, 1 — ошибка")
    parser.add_argument("--updated", metavar="ВЕРСИЯ", help=argparse.SUPPRESS)  # первый запуск после обновления
    return parser.parse_args(argv)


def _attach_console(argv: list[str]) -> None:
    """У оконного .exe нет консоли. Для --help и --version подключаемся к консоли, из которой запустили;
    при обычном запуске — нет, иначе окно консоли (например, от run.bat) не закроется, пока открыта программа."""
    if sys.stdout is not None:
        return
    if not any(a in ("-h", "--help", "--version", "--silent", "--update") for a in argv):
        sys.stdout = sys.stderr = io.StringIO()
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            kernel32.SetConsoleOutputCP(65001)
            sys.stdout = sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            return
    except (AttributeError, OSError):
        pass
    sys.stdout = sys.stderr = io.StringIO()  # консоли нет — текст покажем окном


def _parse_or_show(argv: list[str] | None) -> argparse.Namespace:
    try:
        return parse_args(argv)
    except SystemExit:
        text = sys.stdout.getvalue() if isinstance(sys.stdout, io.StringIO) else ""
        if text.strip():
            from PySide6.QtWidgets import QApplication, QMessageBox

            QApplication.instance() or QApplication(sys.argv[:1])
            QMessageBox.information(None, APP_NAME, text)
        raise


def _russian(app) -> None:
    """Русские надписи в стандартных окнах Qt (кнопки, календарь, выбор цвета и файлов)."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    QLocale.setDefault(QLocale(QLocale.Language.Russian, QLocale.Country.Russia))
    tr = QTranslator(app)
    if tr.load(QLocale(), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(tr)


def main(argv: list[str] | None = None) -> int:
    _attach_console(sys.argv[1:] if argv is None else argv)
    args = _parse_or_show(argv)
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .paths import resource_path
    from .settings import load_view, qsettings
    from .single_instance import InstanceServer, send_to_running
    from .ui.main_window import MainWindow
    from .ui.widgets import RippleFilter

    from . import install as inst

    if args.update:
        from .update import cli_update
        return cli_update(args.update_source)
    app = QApplication(sys.argv[:1])
    setup_mode = args.install or (inst.frozen() and inst.is_setup_name(inst.current_exe()) and not args.uninstall)
    if setup_mode or args.uninstall:   # установщик — та же программа: своё окно вместо главного
        _russian(app)
        app.setApplicationName(APP_NAME)
        app.setWindowIcon(QIcon(str(resource_path("gantt", "resources", "gantt.ico"))))
        app.setStyle("Fusion")
        from .ui.setup import run_setup, run_uninstall
        from .ui.theme import apply_theme
        apply_theme(app, load_view())
        return run_uninstall(args) if args.uninstall else run_setup(args)
    if args.updated:   # прежняя версия ещё закрывается — иначе новая отдала бы ей управление и вышла
        from .single_instance import wait_until_closed
        wait_until_closed(10)
    path = args.import_path or args.path
    if path:
        path = str(Path(path).absolute())  # у открытого окна другая текущая папка
    single = not os.environ.get("GANTT_SCREENSHOT") and not os.environ.get("GANTT_MULTI")
    if single and send_to_running(path or ""):
        return 0
    _russian(app)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(resource_path("gantt", "resources", "gantt.ico"))))
    app.setStyle("Fusion")
    ripple = RippleFilter(app)
    app.installEventFilter(ripple)
    win = MainWindow(load_view(), ripple=ripple)
    win.show()
    if single:
        server = InstanceServer(app)
        server.received.connect(win.open_external)
        server.quitRequested.connect(win.close)   # установщик просит закрыться перед заменой файлов
    if not path and win.settings.reopen_last:
        last = qsettings().value("lastFile", "")
        server = qsettings().value("lastServer", "")
        if last and Path(last).exists():
            path = last
        elif server:
            from .http_source import load_profiles
            profile = next((p for p in load_profiles() if p.name == server), None)
            if profile is not None:
                QTimer.singleShot(0, lambda: win.connect_profile(profile, silent=True))
    if path:
        QTimer.singleShot(0, lambda: win.open_file(str(Path(path))))
    if args.updated:
        QTimer.singleShot(600, lambda: win.after_update(args.updated))
    shot = os.environ.get("GANTT_SCREENSHOT")
    if shot:  # служебное: сохранить снимок окна и выйти — для проверки сборки без экрана
        QTimer.singleShot(2500, lambda: (win.grab().save(shot), app.quit()))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
