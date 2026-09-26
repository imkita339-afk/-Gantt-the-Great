"""Окна установки и удаления. Установщик — та же программа, запущенная как GanttSetup-<версия>.exe или с --install."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from .. import install as inst
from ..paths import data_dir, resource_path
from ..single_instance import is_running, send_command, wait_until_closed
from ..update import default_source


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("hint")
    lab.setWordWrap(True)
    return lab


class _Frame(QDialog):
    """Общий вид: значок и заголовок сверху, страницы, кнопки снизу."""

    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self.setWindowTitle(title)
        self.setWindowIcon(QIcon(str(resource_path("gantt", "resources", "gantt.ico"))))
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(14)
        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(QIcon(str(resource_path("gantt", "resources", "gantt.ico"))).pixmap(QSize(48, 48)))
        head.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        self.title = QLabel(f"<span style='font-size:16pt;font-weight:600'>{title}</span>")
        texts.addWidget(self.title)
        texts.addWidget(_hint(subtitle))
        head.addLayout(texts, 1)
        lay.addLayout(head)
        self.pages = QStackedWidget()
        lay.addWidget(self.pages, 1)
        self.buttons = QHBoxLayout()
        self.buttons.addStretch()
        lay.addLayout(self.buttons)

    def page(self) -> QVBoxLayout:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.pages.addWidget(w)
        return lay

    def button(self, text: str, slot, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setMinimumHeight(34)
        if primary:
            b.setObjectName("primary")
            b.setDefault(True)
        b.clicked.connect(slot)
        self.buttons.addWidget(b)
        return b

    def show_buttons(self, *shown: QPushButton) -> None:
        for i in range(self.buttons.count()):
            w = self.buttons.itemAt(i).widget()
            if w is not None:
                w.setVisible(w in shown)

    def close_running(self) -> bool:
        """Программа открыта — попросить её закрыться (правки и настройки она сохраняет сама)."""
        if not is_running():
            return True
        send_command("quit")
        return wait_until_closed(8)


class SetupDialog(_Frame):
    """Установка: папка, ярлыки → копирование → «Запустить Гант»."""

    def __init__(self, src: Path):
        super().__init__(f"Установка {APP_NAME} {inst.short_version()}",
                         "Диаграмма Ганта для управления проектами. Ставится только для вас — права "
                         "администратора не нужны.")
        self.src = src
        self.exe: Path | None = None
        self.prev = inst.installed()
        self.update_source = inst.sibling_update_source(src)

        lay = self.page()   # 0 — параметры
        if self.prev is not None:
            same = inst.parse_version(self.prev.version) == inst.parse_version(__version__)
            verb = "будет установлена заново" if same else f"будет заменена версией {inst.short_version()}"
            lay.addWidget(QLabel(f"Сейчас установлена версия <b>{inst.short_version(self.prev.version)}</b> — "
                                 f"она {verb}. Ваши правки и настройки сохранятся."))
        lay.addWidget(QLabel("Папка программы"))
        row = QHBoxLayout()
        self.folder = QLineEdit(str(self.prev.folder if self.prev else inst.default_dir()))
        row.addWidget(self.folder, 1)
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        lay.addLayout(row)
        self.desktop = QCheckBox("Ярлык на рабочем столе")
        self.start_menu = QCheckBox("Ярлык в меню «Пуск»")
        for c in (self.desktop, self.start_menu):
            c.setChecked(True)
            lay.addWidget(c)
        if self.update_source:
            lay.addWidget(_hint(f"Обновления будут приходить отсюда же: {self.update_source}"))
        elif default_source():
            lay.addWidget(_hint(f"Обновления будут приходить из {default_source()}"))
        lay.addWidget(_hint("Ваши данные (правки, настройки, шаблоны) хранятся отдельно: "
                            f"{inst.nice_path(data_dir())}"))
        lay.addStretch()

        lay = self.page()   # 1 — копирование
        self.status = QLabel("Устанавливаю…")
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        lay.addWidget(self.bar)
        lay.addStretch()

        lay = self.page()   # 2 — готово
        self.done_text = QLabel()
        self.done_text.setWordWrap(True)
        lay.addWidget(self.done_text)
        self.run_after = QCheckBox(f"Запустить {APP_NAME}")
        self.run_after.setChecked(True)
        lay.addWidget(self.run_after)
        lay.addStretch()

        lay = self.page()   # 3 — ошибка
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.error)
        lay.addStretch()

        self.b_cancel = self.button("Отмена", self.reject)
        self.b_back = self.button("Назад", lambda: (self.pages.setCurrentIndex(0),
                                                    self.show_buttons(self.b_cancel, self.b_install)))
        self.b_install = self.button("Установить", self.run, primary=True)
        self.b_done = self.button("Готово", self.finish, primary=True)
        self.show_buttons(self.b_cancel, self.b_install)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Папка программы", self.folder.text())
        if d:
            self.folder.setText(str(Path(d) / APP_NAME) if Path(d).name != APP_NAME else d)

    def options(self) -> inst.InstallOptions:
        return inst.InstallOptions(Path(self.folder.text().strip() or inst.default_dir()), self.desktop.isChecked(),
                                   self.start_menu.isChecked(), self.update_source)

    def run(self) -> None:
        self.pages.setCurrentIndex(1)
        self.show_buttons()
        QTimer.singleShot(50, self._install)

    def _install(self) -> None:
        opts = self.options()
        try:
            if not self.close_running():
                raise OSError(f"{APP_NAME} сейчас открыт и не закрылся. Закройте его и нажмите «Установить» ещё раз.")
            self.status.setText(f"Копирую программу в {opts.folder}…")
            QApplication.processEvents()
            self.exe = inst.install(self.src, opts)
        except OSError as e:
            hint = "" if "открыт" in str(e) else "\n\nЕсли нет прав на запись в эту папку — выберите другую, например " \
                                                 f"{inst.default_dir()}."
            self.error.setText(f"<b>Установить не получилось.</b><br><br>{e}{hint}".replace("\n", "<br>"))
            self.pages.setCurrentIndex(3)
            self.show_buttons(self.b_cancel, self.b_back)
            return
        where = [x for x, on in (("на рабочем столе", opts.desktop), ("в меню «Пуск»", opts.start_menu)) if on]
        self.done_text.setText(f"<b>{APP_NAME} {inst.short_version()} установлен.</b><br><br>Папка: {opts.folder}"
                               + (f"<br>Ярлыки: {' и '.join(where)}." if where else "")
                               + "<br>Удалить программу можно в «Параметры Windows → Приложения».")
        self.pages.setCurrentIndex(2)
        self.show_buttons(self.b_done)

    def finish(self) -> None:
        if self.exe is not None and self.run_after.isChecked():
            inst.launch(self.exe)
        self.accept()


class UninstallDialog(_Frame):
    def __init__(self, folder: Path):
        super().__init__(f"Удаление {APP_NAME}", f"Будут удалены программа из {folder}, ярлыки и запись в "
                                                 "«Приложениях» Windows.")
        self.folder = folder
        lay = self.page()
        self.remove_data = QCheckBox("Удалить и мои данные: правки, настройки, шаблоны таблиц, подключения")
        lay.addWidget(self.remove_data)
        lay.addWidget(_hint(f"Папка данных: {inst.nice_path(data_dir())}. Если её оставить, после новой установки "
                             "всё вернётся."))
        lay.addStretch()
        lay = self.page()
        self.result = QLabel()
        self.result.setWordWrap(True)
        lay.addWidget(self.result)
        lay.addStretch()
        self.b_cancel = self.button("Отмена", self.reject)
        self.b_remove = self.button("Удалить", self.run, primary=True)
        self.b_close = self.button("Закрыть", self.accept, primary=True)
        self.show_buttons(self.b_cancel, self.b_remove)

    def run(self) -> None:
        if not self.close_running():
            self.result.setText(f"{APP_NAME} сейчас открыт и не закрылся. Закройте его и попробуйте ещё раз.")
            self.pages.setCurrentIndex(1)
            self.show_buttons(self.b_cancel)
            return
        inst.uninstall(self.folder, keep_data=not self.remove_data.isChecked())
        self.result.setText(f"<b>{APP_NAME} удалён.</b>" + ("" if self.remove_data.isChecked() else
                            f"<br><br>Ваши данные остались в {inst.nice_path(data_dir())}."))
        self.pages.setCurrentIndex(1)
        self.show_buttons(self.b_close)


def run_setup(args) -> int:
    """--install / GanttSetup*.exe: окно установки или тихая установка (--silent) для администраторов."""
    src = inst.current_exe()
    if not inst.frozen():
        print("Установщик работает только в собранной программе: tools/build_exe.py → dist/GanttSetup-*.exe")
        return 2
    if args.silent:
        opts = inst.InstallOptions(Path(args.dir) if args.dir else inst.default_dir(), not args.no_desktop,
                                   not args.no_start_menu, args.update_source or inst.sibling_update_source(src))
        if is_running():
            send_command("quit")
            wait_until_closed(8)
        try:
            inst.install(src, opts)
        except OSError as e:
            print(f"Ошибка установки: {e}")
            return 1
        return 0
    dlg = SetupDialog(src)
    if args.dir:
        dlg.folder.setText(args.dir)
    if args.update_source:
        dlg.update_source = args.update_source
    return 0 if dlg.exec() else 1


def run_uninstall(args) -> int:
    info = inst.installed()
    folder = info.folder if info else inst.current_exe().parent
    if args.silent:
        inst.uninstall(folder, keep_data=not args.remove_data)
        return 0
    return 0 if UninstallDialog(folder).exec() else 1
