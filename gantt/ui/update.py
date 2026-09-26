"""Обновление изнутри программы: проверить источник, показать «что нового», скачать, заменить себя и перезапуститься."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
                               QPushButton, QTextBrowser, QVBoxLayout)

from .. import APP_NAME, __version__
from .. import install as inst
from ..paths import data_dir, resource_path
from ..update import Manifest, UpdateError, default_source, download, read_manifest, release_notes
from .tasks import run_async


class _Progress(QObject):
    """Живёт в главном потоке: прогресс скачивания из фонового потока приходит сюда очередью."""

    step = Signal(object, object)


def changelog() -> str:
    path = resource_path("docs", "changes.md")
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f}".replace(".", ",") + " МБ"


class UpdateDialog(QDialog):
    """«Доступна версия X»: что нового, «Обновить и перезапустить», «Позже», «Пропустить эту версию»."""

    def __init__(self, win, m: Manifest):
        super().__init__(win)
        self.win, self.m = win, m
        self.cancel_flag = False
        self.path: Path | None = None
        self.setWindowTitle("Обновление")
        self.resize(560, 460)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel(f"<span style='font-size:14pt;font-weight:600'>Доступна версия "
                             f"{inst.short_version(m.version)}</span>"))
        sub = QLabel(f"У вас {inst.short_version()}." + (f" Выпущена {self._date(m.date)}." if m.date else "")
                     + (f" Размер {_mb(m.size)}." if m.size else ""))
        sub.setObjectName("hint")
        lay.addWidget(sub)
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown("### Что нового\n\n" + (m.notes or "Описание изменений не приложено."))
        lay.addWidget(notes, 1)
        self.status = QLabel()
        self.status.setObjectName("hint")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        for w in (self.status, self.bar):
            w.hide()
            lay.addWidget(w)
        row = QHBoxLayout()
        self.skip = QPushButton("Пропустить эту версию")
        self.skip.clicked.connect(self._skip)
        row.addWidget(self.skip)
        row.addStretch()
        self.later = QPushButton("Позже")
        self.later.clicked.connect(self._later)
        row.addWidget(self.later)
        self.go = QPushButton("Обновить и перезапустить")
        self.go.setObjectName("primary")
        self.go.setDefault(True)
        self.go.clicked.connect(self._download)
        row.addWidget(self.go)
        lay.addLayout(row)

    @staticmethod
    def _date(iso: str) -> str:
        try:
            y, mth, d = (int(x) for x in iso[:10].split("-"))
            return f"{d:02d}.{mth:02d}.{y}"
        except ValueError:
            return iso

    def _skip(self) -> None:
        self.win.qs.setValue("update/skip", self.m.version)
        self.reject()

    def _later(self) -> None:
        if self.bar.isVisible():
            self.cancel_flag = True       # «Отмена» во время скачивания
        self.reject()

    def _download(self) -> None:
        self.go.setEnabled(False)
        self.skip.setEnabled(False)
        self.later.setText("Отмена")
        self.status.setText("Скачиваю…")
        self.status.show()
        self.bar.show()
        self.bar.setRange(0, 0)
        prog = _Progress(self)
        prog.step.connect(self._step)

        def work():
            return download(self.m, data_dir() / "updates", lambda d, t: prog.step.emit(d, t),
                            lambda: self.cancel_flag)

        if getattr(self.win, "sync_network", False):
            try:
                self._downloaded(work())
            except UpdateError as e:
                self._failed(e)
        else:
            run_async(work, self._downloaded, self._failed)

    def _step(self, done: int, total: int) -> None:
        if total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(done * 1000 / total))
            self.status.setText(f"Скачиваю… {_mb(done)} из {_mb(total)}")
        else:
            self.status.setText(f"Скачиваю… {_mb(done)}")

    def _downloaded(self, path: Path) -> None:
        if self.cancel_flag:
            return
        self.path = path
        self.accept()

    def _failed(self, e) -> None:
        if self.cancel_flag:
            return
        self.bar.hide()
        self.status.setText(str(e) if isinstance(e, UpdateError) else f"Не удалось скачать: {e}")
        self.go.setEnabled(True)
        self.go.setText("Попробовать ещё раз")
        self.later.setText("Позже")


class UpdateMixin:
    """Часть главного окна: проверка при запуске, «Проверить обновления», «Обновить из файла», «Что нового»."""

    def _init_updates(self) -> None:
        self._checking = False
        if inst.frozen():   # старый .exe после обновления и версия в «Приложениях» — когда прежний процесс уже вышел
            QTimer.singleShot(1500, lambda: (inst.cleanup_old(), inst.sync_registered_version(),
                                             inst.cleanup_downloads()))
        if self.update_auto() and self.update_source():
            QTimer.singleShot(4000, lambda: self.check_updates(silent=True))

    def update_source(self) -> str:
        """Указанный пользователем источник, а если его нет — заложенный при сборке."""
        return self.update_source_own() or default_source()

    def update_source_own(self) -> str:
        return str(self.qs.value("update/source", "") or "")

    def update_auto(self) -> bool:
        return str(self.qs.value("update/auto", "true")).lower() in ("true", "1")

    def set_update_options(self, source: str | None = None, auto: bool | None = None) -> None:
        if source is not None:
            self.qs.setValue("update/source", source.strip())
        if auto is not None:
            self.qs.setValue("update/auto", auto)
        self.qs.sync()

    def check_updates(self, silent: bool = False) -> None:
        """silent — при запуске: сообщаем, только если есть новая версия (и её не пропустили)."""
        source = self.update_source()
        if not source:
            if not silent:
                self._ask_source()
            return
        if self._checking:
            return
        self._checking = True

        def done(m: Manifest) -> None:
            self._checking = False
            skipped = str(self.qs.value("update/skip", "") or "")
            if not m.newer_than():
                if not silent:
                    self.toast(f"У вас последняя версия — {inst.short_version()}")
                return
            if silent and skipped == m.version:
                return
            if silent:
                self.toast(f"Доступна версия {inst.short_version(m.version)}", "Подробнее",
                           lambda: self.show_update(m), ms=9000)
            else:
                self.show_update(m)

        def failed(e) -> None:
            self._checking = False
            if not silent:
                QMessageBox.warning(self, "Обновление", str(e))

        self.net(lambda: read_manifest(source), done, failed)

    def _ask_source(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Обновление")
        box.setText("Не задан источник обновлений — папка, адрес или репозиторий GitHub, где лежит новая версия.")
        box.setInformativeText("Его укажет тот, кто раздаёт программу. Если у вас есть сам файл новой версии — "
                               "«Обновить из файла».")
        folder = box.addButton("Указать папку…", QMessageBox.ButtonRole.AcceptRole)
        file = box.addButton("Обновить из файла…", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is folder:
            d = QFileDialog.getExistingDirectory(self, "Папка обновлений (в ней latest.json)")
            if d:
                self.set_update_options(source=d)
                self.check_updates()
        elif box.clickedButton() is file:
            self.update_from_file()

    def show_update(self, m: Manifest) -> None:
        dlg = UpdateDialog(self, m)
        if dlg.exec() and dlg.path is not None:
            self.apply_update(dlg.path, m.version)

    def update_from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Файл новой версии", "",
                                              "Программа Гант (GanttSetup*.exe Gantt*.exe);;Все файлы (*.*)")
        if not path:
            return
        version = inst.exe_version(Path(path))
        if version is None:
            QMessageBox.warning(self, "Обновление", f"Это не файл программы {APP_NAME}: {Path(path).name}")
            return
        older = inst.parse_version(version) < inst.parse_version(__version__)
        same = inst.parse_version(version) == inst.parse_version(__version__)
        what = "старее установленной" if older else "та же, что сейчас" if same else "новее"
        if QMessageBox.question(self, "Обновление", f"В файле версия {inst.short_version(version)} — {what} "
                                f"(у вас {inst.short_version()}). Поставить её и перезапустить программу?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.apply_update(Path(path), version)

    def apply_update(self, setup: Path, version: str) -> None:
        """Заменить запущенный Gantt.exe новым и перезапуститься. Из исходников — только подсказать."""
        if not inst.frozen():
            box = QMessageBox(self)
            box.setWindowTitle("Обновление")
            box.setText("Программа запущена из исходников (run.bat) — заменить её файлом нельзя.")
            box.setInformativeText(f"Новая версия скачана: {setup}\nЕё можно запустить как установщик.")
            show = box.addButton("Показать файл", QMessageBox.ButtonRole.ActionRole)
            box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is show:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(setup.parent)))
            return
        target = inst.current_exe()
        try:
            inst.replace_exe(setup, target)
        except OSError as e:
            QMessageBox.warning(self, "Обновление",
                                f"Не удалось заменить {target}:\n{e}\n\nЗакройте программу и запустите новую версию "
                                f"как установщик: {setup}")
            return
        self.qs.setValue("update/skip", "")
        inst.launch(target, "--updated", __version__)
        self.close()

    def after_update(self, old: str) -> None:
        """Первый запуск новой версии."""
        if inst.parse_version(old) == inst.parse_version(__version__):
            self.toast(f"{APP_NAME} переустановлен — версия {inst.short_version()}")
            return
        self.toast(f"{APP_NAME} обновлён: {inst.short_version(old)} → {inst.short_version()}", "Что нового",
                   lambda: self.whats_new(since=old), ms=12000)

    def whats_new(self, since: str | None = None) -> None:
        """Изменения из docs/changes.md: все версии новее since, иначе — текущая."""
        text = changelog()
        parts = []
        for line in text.splitlines():
            if line.startswith("## "):
                head = line[3:].strip().split()[0] if line[3:].strip() else ""
                v = inst.parse_version(head)
                if (since and inst.parse_version(since) < v <= inst.parse_version(__version__)) or \
                        (not since and v == inst.parse_version(__version__)):
                    parts.append(f"## {line[3:].strip()}\n\n" + release_notes(text, head))
        dlg = QDialog(self)
        dlg.setWindowTitle("Что нового")
        dlg.resize(600, 520)
        lay = QVBoxLayout(dlg)
        view = QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setMarkdown("\n\n".join(parts) or f"Версия {inst.short_version()}.")
        lay.addWidget(view)
        ok = QPushButton("Закрыть")
        ok.setObjectName("primary")
        ok.clicked.connect(dlg.accept)
        lay.addWidget(ok, 0, Qt.AlignmentFlag.AlignRight)
        dlg.exec()


class UpdateBox(QVBoxLayout):
    """«Обновление программы» в настройках: версия, источник, проверка при запуске, «Проверить сейчас»."""

    def __init__(self, win):
        super().__init__()
        from PySide6.QtWidgets import QLineEdit

        self.win = win
        info = inst.installed()
        where = ("установлена в " + str(info.folder)) if info and inst.frozen() and \
            info.exe.resolve() == inst.current_exe() else ("без установки" if inst.frozen() else "из исходников")
        ver = QLabel(f"Версия {inst.short_version()} · {where}")
        ver.setObjectName("hint")
        ver.setWordWrap(True)
        self.addWidget(ver)
        self.addWidget(QLabel("Откуда брать обновления"))
        row = QHBoxLayout()
        self.source = QLineEdit()
        default = default_source()
        self.source.setPlaceholderText(f"По умолчанию: {default}" if default else
                                       "Папка, адрес или github:владелец/репозиторий")
        self.source.setToolTip("Общая папка или адрес сайта, где лежит latest.json, или выпуски GitHub: "
                               "github:владелец/репозиторий (можно вставить ссылку на репозиторий)")
        self.source.editingFinished.connect(lambda: win.set_update_options(source=self.source.text()))
        row.addWidget(self.source, 1)
        pick = QPushButton("Обзор…")
        pick.setToolTip("Выбрать папку")
        pick.clicked.connect(self._pick)
        row.addWidget(pick)
        self.addLayout(row)
        self.auto = QCheckBox("Проверять при запуске")
        self.auto.toggled.connect(lambda on: win.set_update_options(auto=on))
        self.addWidget(self.auto)
        buttons = QHBoxLayout()
        check = QPushButton("Проверить сейчас")
        check.clicked.connect(lambda: (win.set_update_options(source=self.source.text()), win.check_updates()))
        buttons.addWidget(check)
        from_file = QPushButton("Из файла…")
        from_file.setToolTip("Поставить новую версию из GanttSetup-*.exe")
        from_file.clicked.connect(win.update_from_file)
        buttons.addWidget(from_file)
        buttons.addStretch()
        self.addLayout(buttons)
        self.reload()

    def reload(self) -> None:
        self.source.setText(self.win.update_source_own())
        self.auto.blockSignals(True)
        self.auto.setChecked(self.win.update_auto())
        self.auto.blockSignals(False)

    def _pick(self) -> None:
        d = QFileDialog.getExistingDirectory(self.win, "Папка обновлений (в ней latest.json)", self.source.text())
        if d:
            self.source.setText(str(Path(d)))
            self.win.set_update_options(source=self.source.text())
