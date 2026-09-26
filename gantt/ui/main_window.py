"""Главное окно: таблица + шкала, файлы, правки с отменой, фильтры, режимы, темы, плавность."""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import socket
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import (QAbstractAnimation, QByteArray, QEasingCurve, QEvent, QFileSystemWatcher, QModelIndex, QPoint,
                            QSequentialAnimationGroup, QSize, Qt, QTimer, QUrl, QVariantAnimation)
from PySide6.QtGui import QAction, QActionGroup, QColor, QCursor, QDesktopServices, QKeySequence, QUndoCommand, \
    QUndoStack
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QSizePolicy,
                               QStackedWidget, QToolBar, QToolButton, QVBoxLayout, QWidget, QWidgetAction)

from .. import APP_NAME, AUTHOR, __version__
from ..exchange import OpenError, apply_incremental, check, write_json
from ..formats import (OPEN_FILTER, WRITERS, NeedsWizard, ReadResult, find_template, gantt_table, msproject,
                       open_path, save_template, table_result)
from ..formats.table import read_clipboard_text
from ..model import DATE_FIELDS, EDIT_FIELDS, Dataset, Node, fmt
from ..paths import data_dir, resource_path
from ..settings import COLUMN_KEYS, ViewSettings, qsettings, save_view
from ..store import Store
from .columns import ColumnList
from .dialogs import NodeEditDialog, SettingsDialog, SettingsPanel, ThemePanel, swatch_icon, theme_icon
from .export import SaveAsDialog, write_change_list_pdf, write_change_list_xlsx
from .incoming import ChangesImportDialog, ResultsDialog
from .server import ServerMixin
from .update import UpdateMixin
from .help import show_help
from .filters import FilterPanel
from .hovercard import HoverCard
from .legend import LegendBar, LegendPanel
from .panels import ChangesPanel, ConflictsDialog, ImportReportDialog, PersonDialog
from .smooth import SmoothScroll, crossfade
from .table import TableView
from .theme import THEMES, apply_theme, color_name, palette_names, project_palette, resolve_theme
from .timeline import ZOOM_PRESETS, TimelineView
from .tree_model import COL, COLUMNS, Criteria, FilterProxy, GanttModel, NodeRole
from .widgets import Popover, Pulse, SnapSplitter, Toast, potato_icon

ZOOM_TITLES = {"day": "День", "week": "Неделя", "month": "Месяц", "quarter": "Квартал", "year": "Год"}
MAX_RECENT = 8


class SetFieldsCommand(QUndoCommand):
    """Правка полей узла. Отмена возвращает прежние видимые значения."""

    def __init__(self, win: "MainWindow", node: Node, new: dict, text: str):
        super().__init__(text)
        self.win, self.node = win, node
        self.new = new
        self.old = {f: win.ds.get(node, f) for f in new}

    def redo(self):
        self.win.apply_values(self.node, self.new)

    def undo(self):
        self.win.apply_values(self.node, self.old)


class StartPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        outer = QVBoxLayout(self)
        outer.addStretch(2)
        box = QVBoxLayout()
        box.setSpacing(14)
        title = QLabel("<span style='font-size:22pt;font-weight:600'>Откуда взять данные?</span>")
        sub = QLabel("Excel-отчёт 1С, любая таблица (Excel, CSV), MS Project или файл обмена. "
                     "Можно перетащить файл в окно или вставить скопированную таблицу.")
        sub.setStyleSheet("color: gray;")
        self.row = QHBoxLayout()
        open_btn = QPushButton("Открыть файл…")
        open_btn.setObjectName("primary")
        open_btn.setMinimumHeight(38)
        open_btn.clicked.connect(win.open_dialog)
        paste = QPushButton("Вставить таблицу")
        paste.setMinimumHeight(38)
        paste.setToolTip("Скопируйте строки в Excel или любой таблице и нажмите сюда (Ctrl+Shift+V)")
        paste.clicked.connect(win.paste_table)
        server = QPushButton("Подключиться к серверу…")
        server.setMinimumHeight(38)
        server.setToolTip("1С или любая система по открытому HTTP-контракту; есть тестовый сервер")
        server.clicked.connect(win.open_connections)
        demo = QPushButton("Посмотреть на демо-данных")
        demo.setMinimumHeight(38)
        demo.clicked.connect(win.open_demo)
        self.row.addWidget(open_btn)
        self.row.addWidget(paste)
        self.row.addWidget(server)
        self.row.addWidget(demo)
        self.row.addStretch()
        self.recent = QListWidget()
        self.recent.setMaximumHeight(210)
        self.recent.itemActivated.connect(lambda it: win.open_file(it.data(Qt.ItemDataRole.UserRole)))
        self.recent_label = QLabel("<b>Недавние файлы</b>")
        for w in (title, sub):
            box.addWidget(w)
        box.addLayout(self.row)
        box.addSpacing(10)
        box.addWidget(self.recent_label)
        box.addWidget(self.recent)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addLayout(box, 3)
        h.addStretch(1)
        outer.addLayout(h)
        outer.addStretch(3)

    def add_button(self, text: str, slot) -> None:
        b = QPushButton(text)
        b.setMinimumHeight(38)
        b.clicked.connect(slot)
        self.row.insertWidget(self.row.count() - 1, b)

    def refresh(self, recent: list[str]) -> None:
        self.recent.clear()
        for p in recent:
            it = QListWidgetItem(f"{Path(p).name}   —   {Path(p).parent}")
            it.setData(Qt.ItemDataRole.UserRole, p)
            self.recent.addItem(it)
        self.recent_label.setVisible(bool(recent))
        self.recent.setVisible(bool(recent))


class MainWindow(UpdateMixin, ServerMixin, QMainWindow):
    def __init__(self, settings: ViewSettings, store: Store | None = None, ripple=None):
        super().__init__()
        # шрифт и тема — до создания виджетов: иначе сохранённый размер шрифта при запуске до них не доходит
        apply_theme(QApplication.instance(), settings)
        self._theme_key = None
        self.settings = settings
        self.store = store or Store()
        self.qs = qsettings()
        self.ripple = ripple
        self.ds: Dataset | None = None
        self.path: Path | None = None
        self.source_label = ""
        self.warnings: list[str] = []
        self.conflicts: list[dict] = []
        self.table_mappings: dict[str, object] = {}   # файл → сопоставление из мастера (для перечитывания)
        self.tokens = None
        self.me_mode = False
        self.palette_cache: list[QColor] = []
        self.undo = QUndoStack(self)
        self.setAcceptDrops(True)
        self.setWindowTitle(APP_NAME)
        self.setDockOptions(QMainWindow.DockOption.AnimatedDocks | QMainWindow.DockOption.AllowTabbedDocks)

        self.model = GanttModel(self)
        self.model.colors = self.chip_color
        self.proxy = FilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table = TableView()
        self.timeline = TimelineView()
        for v in (self.table, self.timeline):
            v.settings = settings
            v.project_color = self.project_color
        self.table.setModel(self.proxy)
        self.timeline.setModel(self.proxy)
        self.timeline.setSelectionModel(self.table.selectionModel())
        self.vscroll = SmoothScroll(self.timeline.verticalScrollBar())
        self.hscroll = SmoothScroll(self.timeline.horizontalScrollBar())
        self.table.smooth_v = self.timeline.smooth_v = self.vscroll
        self.timeline.smooth_h = self.hscroll
        self.splitter = SnapSplitter()
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.timeline)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(5)
        self.splitter.setSizes([640, 900])
        self.start = StartPage(self)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.start)
        chart = QWidget()
        cl = QVBoxLayout(chart)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self.splitter, 1)
        self.legend = LegendBar(self)
        cl.addWidget(self.legend)
        self.stack.addWidget(chart)
        self.setCentralWidget(self.stack)

        self.filters = FilterPanel(self.qs)
        self.filters_pop = Popover(self, self.filters, width=380)
        self.legend_panel = LegendPanel(self)
        self.legend_pop = Popover(self, self.legend_panel, width=520)
        self.legend_pop.preferred_height = 600
        self.card = HoverCard(self)
        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(90)
        self._collapse_timer.timeout.connect(self._collapse_gap)
        self._collapse_anim = None
        self._pre_search_expanded: set[str] | None = None
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(160)
        self._search_timer.timeout.connect(self.apply_filter)
        self.changes = ChangesPanel()
        self.changes_pop = Popover(self, self.changes, width=430)
        self.changes_pop.preferred_height = 470
        self.theme_panel = ThemePanel(settings)
        self.theme_pop = Popover(self, self.theme_panel, width=340)
        self.settings_panel = SettingsPanel(settings, THEMES["light"], self)
        self.settings_pop = Popover(self, self.settings_panel, width=900)
        self.pops = [self.filters_pop, self.legend_pop, self.changes_pop, self.theme_pop, self.settings_pop]
        self._bulk = False

        self.watcher = QFileSystemWatcher(self)
        self.poll = QTimer(self)
        self.poll.setInterval(3000)
        self._file_sig = None

        self._build_actions()
        self._connect()
        self._build_toolbar()
        self._build_menus()
        self.status = QLabel()
        self.statusBar().addWidget(self.status, 1)
        self.edits_label = QLabel()
        self.statusBar().addPermanentWidget(self.edits_label)
        self.potato_btn = QToolButton()
        self.potato_btn.setIcon(potato_icon(16))
        self.potato_btn.setAutoRaise(True)
        self.potato_btn.setToolTip("Режим картошки: без анимаций, теней и сглаживания.\nЩёлкните, чтобы выключить.")
        self.potato_btn.clicked.connect(lambda: self.set_potato(False))
        self.statusBar().addPermanentWidget(self.potato_btn)
        self._init_server()
        self._init_updates()
        self.apply_settings(settings, save=False)
        self._restore_window()
        self.start.refresh(self.recent_files())

    # ---------- связи двух половин ----------
    def _connect(self) -> None:
        t, tl = self.table, self.timeline
        t.expanded.connect(tl.expand)
        t.collapsed.connect(tl.collapse)
        a, b = t.verticalScrollBar(), tl.verticalScrollBar()
        a.valueChanged.connect(b.setValue)
        b.valueChanged.connect(a.setValue)
        tl.datesEdited.connect(self.on_dates_dragged)
        for v in (t, tl):
            v.editRequested.connect(self.edit_node)
            v.contextRequested.connect(self.context_menu)
            v.hovered.connect(self._hover)
        t.cellDoubleClicked.connect(self.on_double_click)
        t.header().customContextMenuRequested.connect(self.header_menu)
        t.header().picked.connect(self.make_group)
        t.header().groupToggled.connect(self.toggle_group)
        t.header().sectionResized.connect(lambda *_: self._update_table_limit())
        t.header().sectionMoved.connect(lambda *_: self._update_table_limit())
        tl.zoomChanged.connect(self.on_zoom_changed)
        self.proxy.layoutChanged.connect(self.sync_expansion)
        self.proxy.modelReset.connect(self.sync_expansion)
        self.splitter.handleReleased.connect(self._snap_splitter)
        self.filters.changed.connect(self.apply_filter)
        self.changes.revert.connect(lambda node, fld: self._revert(node, [fld]))
        self.changes.revert_all.connect(self._revert_all)
        self.changes.save_as.connect(lambda: (self.changes_pop.close_(), self.save_as()))
        self.changes.save_list.connect(lambda: (self.changes_pop.close_(), self.save_change_list()))
        self.changes.send.connect(lambda: (self.changes_pop.close_(), self.send_changes()))
        self.watcher.fileChanged.connect(lambda _p: QTimer.singleShot(900, self._reload_if_changed))
        self.poll.timeout.connect(self._reload_if_changed)
        self.undo.indexChanged.connect(lambda _i: self._edits_changed())
        self.filters_pop.closed.connect(lambda: self.a_filters.setChecked(False))
        self.legend.more.connect(self.show_legend)
        for view in (t, tl):
            view.viewport().installEventFilter(self)
        self.changes_pop.closed.connect(lambda: self.a_changes.setChecked(False))
        self.theme_panel.changed.connect(self.apply_settings)
        self.settings_panel.changed.connect(self.apply_settings)
        t.header().levelClicked.connect(self.expand_to_level)
        t.expanded.connect(self._manual_expand)
        t.collapsed.connect(self._manual_expand)

    def sync_expansion(self, *_):
        with self._no_anim():
            def walk(parent: QModelIndex):
                for r in range(self.proxy.rowCount(parent)):
                    idx = self.proxy.index(r, 0, parent)
                    expanded = self.table.isExpanded(idx)
                    if self.timeline.isExpanded(idx) != expanded:
                        self.timeline.setExpanded(idx, expanded)
                    if expanded:
                        walk(idx)
            walk(QModelIndex())
        self.timeline.verticalScrollBar().setValue(self.table.verticalScrollBar().value())

    @contextmanager
    def _no_anim(self):
        """Массовое раскрытие — без анимации, иначе сотни строк «поедут»."""
        was, bulk = self.table.isAnimated(), self._bulk
        self.table.setAnimated(False)
        self.timeline.setAnimated(False)
        self._bulk = True
        try:
            yield
        finally:
            self._bulk = bulk
            self.table.setAnimated(was)
            self.timeline.setAnimated(was)

    def _hover(self, node) -> None:
        old = self.table.hover_node
        if node is not old:
            self.table.hover_node = self.timeline.hover_node = node
            for n in (old, node):   # перерисовать только две строки, а не оба окна целиком
                if n is None or self.ds is None:
                    continue
                for view in (self.table, self.timeline):
                    r = view.row_rect(n)
                    if r is not None:
                        view.viewport().update(r)
        if self.settings.hover_card and not self.timeline._drag:
            delay = 650 if self.table.viewport().underMouse() else 420
            self.card.hover(node, QCursor.pos(), delay)

    def eventFilter(self, obj, event) -> bool:
        et = event.type()
        if et == QEvent.Type.MouseMove:
            self.card.follow(event.globalPosition().toPoint())
        elif et in (QEvent.Type.MouseButtonPress, QEvent.Type.Wheel, QEvent.Type.MouseButtonDblClick):
            self.card.dismiss()
        return super().eventFilter(obj, event)

    # ---------- всплывающие панели ----------
    def toggle_filters(self, on: bool | None = None) -> None:
        if on is None:
            on = not self.filters_pop.is_open()
        if on and not self.filters_pop.is_open():
            self._toggle_pop(self.filters_pop, self._tool_widget(self.a_filters))
        elif not on:
            self.filters_pop.close_()
        self.a_filters.setChecked(self.filters_pop.is_open())

    def show_legend(self) -> None:
        self.legend_panel.refresh()
        self._toggle_pop(self.legend_pop, self.legend.btn if self.legend.isVisible() else
                         self._tool_widget(self.a_filters))

    def toast_bottom(self) -> int:
        """Нижний край для уведомлений — над строкой обозначений."""
        if self.legend.isVisible():
            return self.legend.mapTo(self, QPoint(0, 0)).y() - 10
        return self.height() - self.statusBar().height() - 12

    def toggle_legend(self, on: bool) -> None:
        self.apply_settings(replace(self.settings, show_legend=on))

    def _tool_widget(self, action: QAction) -> QWidget:
        for tb in self.findChildren(QToolBar):
            w = tb.widgetForAction(action)
            if w is not None:
                return w
        return self.search

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        for pop in (self.filters_pop, self.legend_pop):
            if pop.is_open():
                pop.place()

    # ---------- разделитель: таблица не «рвётся», разделитель прилипает ----------
    def _update_table_limit(self) -> None:
        cw = max(240, self.table.content_width())
        if self._collapse_anim is not None and self._collapse_anim.state() == QAbstractAnimation.State.Running:
            return   # идёт отскок — ограничение вернётся в его конце
        self.table.setMaximumWidth(cw)
        if self.splitter.sizes() and self.splitter.sizes()[0] > cw + 1:
            self._collapse_timer.start()   # таблица стала уже — пустое место схлопнется

    def _collapse_gap(self) -> None:
        """Пустое место между таблицей и шкалой схлопывается к краю колонок — с лёгким отскоком."""
        sizes = self.splitter.sizes()
        if len(sizes) < 2:
            return
        cw = max(240, self.table.content_width())
        if sizes[0] > cw + 1:
            self._bounce_split(cw)

    def _grow_table(self, old_cw: int) -> None:
        """Колонка добавилась — таблица расширяется на её ширину, тоже с отскоком."""
        sizes = self.splitter.sizes()
        if len(sizes) < 2 or self.ds is None:
            return
        left, total = sizes[0], sum(sizes)
        new_cw = self.table.content_width()
        grow = new_cw - old_cw
        target = min(left + grow, new_cw, total - 320)
        if grow > 0 and target > left + 1:
            self._bounce_split(target)

    def _bounce_split(self, target: int) -> None:
        """Разделитель едет к target, чуть проскакивает и возвращается; в конце — лёгкий пульс."""
        sizes = self.splitter.sizes()
        left, total = sizes[0], sum(sizes)
        if self._collapse_anim is not None:
            self._collapse_anim.stop()
        if not self.settings.smooth or not self.isVisible():
            self.splitter.setSizes([target, total - target])
            self._update_table_limit()
            return
        direction = 1 if target > left else -1
        bounce = direction * min(12, max(4, abs(left - target) * 0.08))
        self.table.setMaximumWidth(16777215)   # на время отскока таблица может быть чуть шире колонок

        def step(v):
            self.splitter.setSizes([int(v), total - int(v)])

        go = QVariantAnimation(self)
        go.setDuration(260)
        go.setStartValue(float(left))
        go.setEndValue(float(target + bounce))
        go.setEasingCurve(QEasingCurve.Type.InOutCubic)
        go.valueChanged.connect(step)
        back = QVariantAnimation(self)
        back.setDuration(220)
        back.setStartValue(float(target + bounce))
        back.setEndValue(float(target))
        back.setEasingCurve(QEasingCurve.Type.OutBack)
        back.valueChanged.connect(step)
        group = QSequentialAnimationGroup(self)
        group.addAnimation(go)
        group.addAnimation(back)

        def done():
            self.table.setMaximumWidth(max(240, self.table.content_width()))
            x = self.splitter.handle(1).mapTo(self.stack, self.splitter.handle(1).rect().center()).x()
            Pulse(self.stack, x, QColor(self.settings.accent))

        group.finished.connect(done)
        group.start()
        self._collapse_anim = group

    def _snap_splitter(self) -> None:
        sizes = self.splitter.sizes()
        cur, total = sizes[0], sum(sizes)
        edges = [e for e in self.table.column_edges() if e >= 180] or self.table.column_edges()
        if not edges:
            return
        self.table.horizontalScrollBar().setValue(0)
        target = min(edges, key=lambda e: abs(e - cur))

        def pulse():
            x = self.splitter.sizes()[0] + self.splitter.handleWidth() // 2
            if self.settings.smooth:
                Pulse(self.stack, x, QColor(self.settings.accent))

        if target == cur or not self.settings.smooth:
            self.splitter.setSizes([target, total - target])
            pulse()
            return
        anim = QVariantAnimation(self)
        anim.setDuration(220)
        anim.setStartValue(cur)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(lambda v: self.splitter.setSizes([int(v), total - int(v)]))
        anim.finished.connect(pulse)
        anim.start()
        self._snap_anim = anim

    # ---------- действия ----------
    def _build_actions(self) -> None:
        def act(text, slot, shortcut=None, tip=None, checkable=False):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setToolTip(tip)
            a.setCheckable(checkable)
            a.triggered.connect(slot)
            self.addAction(a)
            return a

        self.a_open = act("Открыть…", self.open_dialog, "Ctrl+O")
        self.a_save = act("Сохранить как…", lambda: self.save_as(), "Ctrl+Shift+S")
        self.a_wizard = act("Открыть таблицу через мастер…", self.open_with_wizard, None,
                            "Сопоставить колонки заново, даже если шаблон уже есть")
        self.a_paste = act("Вставить таблицу из буфера", self.paste_table, "Ctrl+Shift+V",
                           "Скопируйте строки в Excel или другой программе (Ctrl+Shift+V)")
        self.a_change_list = act("Лист изменений (Excel, PDF)…", self.save_change_list)
        self.a_connect = act("Подключение к серверу…", self.open_connections, None,
                             "1С или любая система по HTTP-контракту")
        self.a_refresh = act("Обновить с сервера", lambda: self.refresh_from_server(), "F5")
        self.a_send = act("Отправить правки в источник…", self.send_changes, "Ctrl+Shift+Return",
                          "Записать правки на сервер — после «Подтвердить»")
        self.a_disconnect = act("Отключиться от сервера", self.disconnect_server)
        self.a_export_changes = act("Выгрузить правки файлом обмена…", self.export_changes, None,
                                    "Пакет правок (changes) — для источника или другой программы")
        self.a_quit = act("Выход", self.close)
        self.a_undo = self.undo.createUndoAction(self, "Отменить")
        self.a_undo.setShortcut(QKeySequence("Ctrl+Z"))
        self.a_redo = self.undo.createRedoAction(self, "Повторить")
        self.a_redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.a_edit = act("Изменить…", lambda: self.edit_node(self.current_node()), "F2")
        self.a_today = act("Сегодня", lambda: self.timeline.scroll_to_date(self.timeline.today, animate=True),
                           "Ctrl+T", "К сегодняшнему дню (Ctrl+T)")
        self.a_expand = act("Развернуть всё", self.expand_all, "Ctrl+Shift+E", "Развернуть всё (Ctrl+Shift+E)")
        self.a_projects = act("Свернуть до проектов", self.collapse_to_projects, "Ctrl+Shift+P",
                              "Разделы раскрыты, проекты свёрнуты (Ctrl+Shift+P)")
        self.a_collapse = act("Свернуть всё", self.collapse_all, "Ctrl+Shift+C")
        self.a_settings = act("Настройки вида…", lambda: self.open_settings(), "Ctrl+,", "Настройки вида (Ctrl+,)")
        self.a_zoom_in = act("Крупнее", lambda: self.timeline.animate_zoom(self.timeline.scale.ppd * 1.4), "Ctrl+=")
        self.a_zoom_out = act("Мельче", lambda: self.timeline.animate_zoom(self.timeline.scale.ppd / 1.4), "Ctrl+-")
        self.a_search = act("Поиск", lambda: self.search.setFocus(), "Ctrl+F")
        self.a_about = act("О программе", self.about)
        self.a_guide = act("Руководство пользователя", lambda: show_help(self), "Ctrl+F1",
                           "Как пользоваться программой — с картинками, поиском и сохранением в PDF")
        self.a_whats_new = act("Что нового", lambda: self.whats_new())
        self.a_check_updates = act("Проверить обновления…", lambda: self.check_updates())
        self.a_update_file = act("Обновить из файла…", self.update_from_file, None,
                                 "Поставить новую версию из присланного файла GanttSetup-*.exe")
        self.a_filters = act("Фильтры", self.toggle_filters, "Ctrl+Shift+F",
                             "Фильтры — окно поверх таблицы (Ctrl+Shift+F)", checkable=True)
        self.a_legend = act("Строка обозначений", self.toggle_legend, None,
                            "Что значат полосы и цвета — строка под диаграммой", checkable=True)
        self.a_legend_all = act("Все обозначения…", self.show_legend, "F1")
        self.a_card = act("Карточка при наведении", lambda on: self.apply_settings(
            replace(self.settings, hover_card=on)), None, "Описание, план и факт при наведении на строку",
            checkable=True)
        self.a_potato = act("Режим картошки", self.set_potato, None,
                            "Для слабых компьютеров: без анимаций, теней и сглаживания", checkable=True)
        self.a_potato.setIcon(potato_icon(16))
        self.a_changes = act("Изменения", self.toggle_changes, None,
                             "Ожидающие правки — окно поверх таблицы", checkable=True)
        self.a_who = act("Кто я…", self.pick_me)
        self.mode_group = QActionGroup(self)
        self.a_mode_all = act("Руководитель", lambda: self.set_mode(False), None, "Все проекты и задания",
                              checkable=True)
        self.a_mode_me = act("Исполнитель", lambda: self.set_mode(True), None, "Только мои задания и их проекты",
                             checkable=True)
        for a in (self.a_mode_all, self.a_mode_me):
            self.mode_group.addAction(a)
        self.a_mode_all.setChecked(True)
        self.zoom_group = QActionGroup(self)
        self.zoom_actions = {}
        for key, title in ZOOM_TITLES.items():
            a = QAction(title, self)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, k=key: self.set_zoom(k))
            self.zoom_group.addAction(a)
            self.zoom_actions[key] = a
        self.theme_menu = QMenu("Тема", self)
        self.theme_group = QActionGroup(self)
        self.theme_actions = {}
        for key, t in list(THEMES.items()) + [("system", None)]:
            a = self.theme_menu.addAction(t.title if t else "Как в системе")
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, k=key: self.apply_settings(replace(self.settings, theme=k)))
            self.theme_group.addAction(a)
            self.theme_actions[key] = a

    def _build_toolbar(self) -> None:
        tb = self.addToolBar("Главная")
        tb.setObjectName("main")
        tb.setMovable(False)
        tb.setIconSize(QSize(16, 16))
        tb.addAction(self.a_open)
        tb.addSeparator()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск: название, номер, исполнитель")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(210)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        tb.addWidget(self.search)
        tb.addAction(self.a_filters)
        tb.addSeparator()
        self._short(tb, self.a_mode_all, "Все")
        self._short(tb, self.a_mode_me, "Мои")
        tb.addSeparator()
        for a in self.zoom_actions.values():
            tb.addAction(a)
        tb.addAction(self.a_today)
        tb.addSeparator()
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.changes_btn = QToolButton()
        self.changes_btn.setDefaultAction(self.a_changes)
        tb.addWidget(self.changes_btn)
        self._short(tb, self.a_undo, "Отменить")
        self.theme_btn = QToolButton()
        self.theme_btn.setText("Тема")
        self.theme_btn.setToolTip("Тема, акцент и палитра проектов")
        self.theme_btn.setCheckable(True)
        self.theme_btn.clicked.connect(lambda: self.toggle_theme())
        tb.addWidget(self.theme_btn)
        self.settings_btn = QToolButton()
        self.settings_btn.setText("Настройки")
        self.settings_btn.setToolTip("Настройки вида (Ctrl+,)")
        self.settings_btn.setCheckable(True)
        self.settings_btn.clicked.connect(lambda: self.open_settings())
        tb.addWidget(self.settings_btn)
        self.theme_pop.closed.connect(lambda: self.theme_btn.setChecked(False))
        self.settings_pop.closed.connect(lambda: self.settings_btn.setChecked(False))

    @staticmethod
    def _short(tb, action: QAction, text: str) -> None:
        """Кнопка на панели с коротким текстом; в меню у действия остаётся полное название."""
        b = QToolButton()
        b.setDefaultAction(action)
        b.setText(text)
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        action.changed.connect(lambda: b.setText(text))
        tb.addWidget(b)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("Файл")
        self.file_menu = m
        m.addAction(self.a_open)
        m.addAction(self.a_wizard)
        m.addAction(self.a_paste)
        self.recent_menu = m.addMenu("Последние файлы")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        m.addSeparator()
        m.addAction(self.a_save)
        m.addAction(self.a_change_list)
        m.addAction(self.a_export_changes)
        m.addSeparator()
        m.addAction(self.a_quit)
        m = mb.addMenu("Сервер")
        m.addAction(self.a_connect)
        m.addAction(self.a_refresh)
        m.addAction(self.a_send)
        m.addSeparator()
        m.addAction(self.a_disconnect)
        m = mb.addMenu("Правка")
        m.addAction(self.a_undo)
        m.addAction(self.a_redo)
        m.addSeparator()
        m.addAction(self.a_edit)
        m.addAction(self.a_changes)
        m = mb.addMenu("Вид")
        zm = m.addMenu("Масштаб")
        for a in self.zoom_actions.values():
            zm.addAction(a)
        zm.addSeparator()
        zm.addAction(self.a_zoom_in)
        zm.addAction(self.a_zoom_out)
        m.addAction(self.a_today)
        m.addSeparator()
        m.addAction(self.a_expand)
        m.addAction(self.a_projects)
        m.addAction(self.a_collapse)
        m.addSeparator()
        m.addAction(self.a_filters)
        m.addAction(self.a_legend)
        m.addAction(self.a_card)
        m.addAction(self.a_mode_all)
        m.addAction(self.a_mode_me)
        m.addAction(self.a_who)
        m.addSeparator()
        m.addMenu(self.theme_menu)
        m.addAction(self.a_potato)
        m.addAction(self.a_settings)
        m = mb.addMenu("Справка")
        m.addAction(self.a_guide)
        m.addAction(self.a_legend_all)
        m.addSeparator()
        m.addAction(self.a_whats_new)
        m.addAction(self.a_check_updates)
        m.addAction(self.a_update_file)
        m.addSeparator()
        m.addAction(self.a_about)

    # ---------- цвета ----------
    def project_color(self, node: Node) -> QColor:
        ds = self.ds
        c = ds.get(node, "color") if ds else None
        if c:
            return QColor(c)
        pal = self.palette_cache
        return pal[ds.project_index.get(node.id, 0) % len(pal)] if ds else pal[0]

    def chip_color(self, node: Node) -> QColor | None:
        if self.ds is not None and (self.ds.role(node) == "project" or self.ds.get(node, "color")):
            return self.project_color(node)
        return None

    def color_title(self, c: QColor) -> str:
        names = dict((x.name(), n) for x, n in zip(self.palette_cache, palette_names(self.settings)))
        return names.get(c.name(), color_name(c))

    # ---------- настройки вида ----------
    def apply_settings(self, s: ViewSettings, save: bool = True) -> None:
        old = self.settings if self.tokens is not None else None
        big = old is not None and (old.theme != s.theme or old.accent != s.accent or old.palette != s.palette)
        if big and s.smooth:
            crossfade(self, lambda: self._apply(s))
        else:
            self._apply(s)
        if save:
            save_view(s)

    def _apply(self, s: ViewSettings) -> None:
        self.settings = s
        key = (resolve_theme(s.theme), s.accent, s.font_size)
        if self.tokens is None or key != self._theme_key:   # стиль всей программы — только когда он поменялся
            self.tokens = apply_theme(QApplication.instance(), s)
            self._theme_key = key
        tokens = self.tokens
        self.palette_cache = project_palette(s, tokens)
        self.model.section_bg = QColor(tokens.section_row)
        self.model._chips.clear()
        for v in (self.table, self.timeline):
            v.settings, v.tokens, v.accent = s, tokens, QColor(s.accent)
            v.restyle()
            v.setAnimated(s.smooth)
            v.fast = s.potato
            v.pan.inertia = s.smooth
            v.doItemsLayout()
            v.viewport().update()
        for pop in self.pops:
            pop.tokens, pop.animations, pop.fast = tokens, s.smooth, s.potato
            pop.update()
        if self.theme_pop.is_open():
            self.theme_panel.reload(s)
        self.legend.setVisible(s.show_legend)
        self.legend.update()
        self.legend_panel.refresh()
        self.a_legend.setChecked(s.show_legend)
        self.a_card.setChecked(s.hover_card)
        self.a_potato.setChecked(s.potato)
        self.potato_btn.setVisible(s.potato)
        self.card.enabled = s.hover_card
        self.card.restyle()
        if not s.hover_card:
            self.card.dismiss()
        self.timeline.animations = s.smooth
        self.vscroll.enabled = self.hscroll.enabled = s.smooth
        if self.ripple is not None:
            self.ripple.enabled = s.smooth
            self.ripple.color = QColor(s.accent)
        self._apply_columns(s)
        self.timeline.header().viewport().update()
        if self.ds is not None and self.timeline.scale and abs(self.timeline.scale.ppd - s.zoom_ppd) > 1e-6:
            self.timeline.zoom_to(s.zoom_ppd)
        for key, a in self.theme_actions.items():
            a.setChecked(key == s.theme)
            if key in THEMES:
                a.setIcon(theme_icon(THEMES[key], s.accent))
        self._mark_zoom()
        self._watch(self.path)

    def _apply_columns(self, s: ViewSettings) -> None:
        """Колонки: скрытые в меню + свёрнутые группы. Появилась колонка — таблица плавно расширяется."""
        header = self.table.header()
        groups = []
        for i, g in enumerate(s.column_groups):
            cols = [k for k in g.get("cols", []) if k in COL and k not in s.hidden_columns]
            if cols:   # все колонки группы выключены в меню — над заголовком ни скобки, ни «+»
                groups.append({"cols": cols, "collapsed": bool(g.get("collapsed")), "index": i})
        header.groups = groups
        folded = {k for g in groups if g["collapsed"] for k in g["cols"]}
        old_cw = self.table.content_width()
        appeared = False
        for key in COLUMN_KEYS:
            hide = key in s.hidden_columns or key in folded
            if not hide and self.table.isColumnHidden(COL[key]):
                appeared = True
            self.table.setColumnHidden(COL[key], hide)
        header.viewport().update()
        self._update_table_limit()
        if appeared and self.ds is not None and self.isVisible() and self.stack.currentIndex() == 1:
            QTimer.singleShot(0, lambda: self._grow_table(old_cw))

    # ---------- группы колонок ----------
    def make_group(self, keys: list[str]) -> None:
        """Ctrl+щелчок по заголовкам: отмеченные колонки становятся рядом и образуют группу."""
        header = self.table.header()
        keys = sorted({k for k in keys if k in COL}, key=lambda k: header.visualIndex(COL[k]))
        if not keys:
            return
        target = header.visualIndex(COL[keys[0]])
        for n, k in enumerate(keys):  # рядом — в порядке показа, начиная с первой отмеченной
            header.moveSection(header.visualIndex(COL[k]), target + n)
        groups = [dict(g, cols=[c for c in g["cols"] if c not in keys]) for g in self.settings.column_groups]
        groups = [g for g in groups if g["cols"]] + [{"cols": keys, "collapsed": False}]
        self.apply_settings(replace(self.settings, column_groups=groups))
        titles = dict(COLUMNS)
        self.toast(f"Группа: {', '.join(titles[k] for k in keys)} — «−» над заголовком сворачивает её")

    def toggle_group(self, g: int) -> None:
        groups = [dict(x) for x in self.settings.column_groups]
        if 0 <= g < len(groups):
            groups[g]["collapsed"] = not groups[g].get("collapsed")
            self.apply_settings(replace(self.settings, column_groups=groups))

    def ungroup(self, g: int | None = None) -> None:
        groups = [] if g is None else [x for i, x in enumerate(self.settings.column_groups) if i != g]
        self.apply_settings(replace(self.settings, column_groups=groups))

    def set_potato(self, on: bool) -> None:
        """Режим картошки: всё рисуется проще — для слабых компьютеров и удалённого рабочего стола."""
        if on == self.settings.potato:
            return
        self.apply_settings(replace(self.settings, potato=on))
        if self.settings_pop.is_open():
            self.settings_panel.reload(self.settings, self.tokens)
        self.toast("Режим картошки: без анимаций, теней и сглаживания" if on else "Режим картошки выключен")

    def open_settings(self) -> None:
        """Панелью под кнопкой; если окно программы узкое — отдельным окном."""
        self.settings_panel.reload(self.settings, self.tokens)
        if self.width() < self.settings_panel.sizeHint().width() + 60 or \
                self.height() < self.settings_panel.sizeHint().height() + 160:
            self.settings_btn.setChecked(False)
            dlg = SettingsDialog(self.settings, self.tokens, self)
            dlg.changed.connect(self.apply_settings)
            dlg.exec()
            return
        self._toggle_pop(self.settings_pop, self.settings_btn)
        self.settings_btn.setChecked(self.settings_pop.is_open())

    def toggle_theme(self) -> None:
        self.theme_panel.reload(self.settings)
        self._toggle_pop(self.theme_pop, self.theme_btn)
        self.theme_btn.setChecked(self.theme_pop.is_open())

    def toggle_changes(self, on: bool | None = None) -> None:
        n = self.ds.edit_count if self.ds is not None else 0
        self.changes_pop.preferred_height = 170 + 50 * min(n, 7)   # по числу правок, без пустого поля
        if on is None or on != self.changes_pop.is_open():
            self._toggle_pop(self.changes_pop, self.changes_btn)
        self.a_changes.setChecked(self.changes_pop.is_open())

    def _toggle_pop(self, pop: Popover, anchor: QWidget) -> None:
        """Одна панель за раз: открыть эту, остальные закрыть; повторное нажатие — закрыть."""
        if pop.is_open():
            pop.close_()
            return
        for other in self.pops:
            if other is not pop:
                other.close_()
        self.card.dismiss()
        pop.open(anchor)

    # ---------- уровни: кнопки «1 2 3» в заголовке таблицы ----------
    def expand_to_level(self, n: int) -> None:
        """Показать дерево до уровня n: 1 — только верхний, последний — всё."""
        if self.ds is None:
            return
        header = self.table.header()
        with self._no_anim():
            if n >= header.levels:
                self.table.expandAll()
                self.timeline.expandAll()
            else:
                self.table.collapseAll()
                self.timeline.collapseAll()
                for idx in self._all_indexes():
                    node = idx.data(NodeRole)
                    if node is not None and node.depth < n - 1:
                        self.table.expand(idx)
        self.sync_expansion()
        header.level_active = n
        header.viewport().update()

    def _manual_expand(self, *_):
        if not self._bulk and self.table.header().level_active is not None:
            self.table.header().level_active = None
            self.table.header().viewport().update()

    def set_zoom(self, key: str) -> None:
        self.timeline.animate_zoom(ZOOM_PRESETS[key])

    def on_zoom_changed(self, ppd: float) -> None:
        self.settings.zoom_ppd = ppd
        save_view(self.settings)
        self._mark_zoom()

    def _mark_zoom(self) -> None:
        ppd = self.settings.zoom_ppd
        key = min(ZOOM_PRESETS, key=lambda k: abs(ZOOM_PRESETS[k] - ppd))
        for k, a in self.zoom_actions.items():
            a.setChecked(k == key and abs(ZOOM_PRESETS[k] - ppd) / ZOOM_PRESETS[k] < 0.05)

    # ---------- режимы и фильтры ----------
    def _me_id(self) -> str | None:
        if self.ds is None or not self.settings.me:
            return None
        for p in self.ds.people.values():
            if self.settings.me in (p["name"], p.get("fullName")):
                return p["id"]
        return None

    def pick_me(self) -> bool:
        if self.ds is None or not self.ds.people:
            QMessageBox.information(self, APP_NAME, "В открытых данных нет списка людей.")
            return False
        dlg = PersonDialog(self.ds, self.settings.me, self)
        if not dlg.exec():
            return False
        pid, name = dlg.person()
        person = self.ds.people.get(pid or "", {})
        self.settings.me = person.get("fullName") or person.get("name") or name
        save_view(self.settings)
        self.apply_filter()
        return True

    def set_mode(self, me: bool) -> None:
        if me and self._me_id() is None and not self.pick_me():
            self.a_mode_all.setChecked(True)
            return
        self.me_mode = me
        self.apply_filter()
        if me:
            self.expand_all()

    def criteria(self) -> Criteria:
        c = self.filters.criteria()
        c.text = self.search.text().strip().lower()
        c.me = self._me_id() if self.me_mode else None
        return c

    def apply_filter(self, *_):
        c = self.criteria()
        searching = bool(c.text or c.me)
        if searching and self._pre_search_expanded is None:  # запомнить раскрытие до поиска
            self._pre_search_expanded = {idx.data(NodeRole).id for idx in self._all_indexes()
                                         if self.table.isExpanded(idx)}
        self.proxy.set_criteria(c)
        if searching:
            self.expand_all()
        elif self._pre_search_expanded is not None:  # поиск очищен — раскрытие как было
            ids, self._pre_search_expanded = self._pre_search_expanded, None
            with self._no_anim():
                self.table.collapseAll()
                for idx in self._all_indexes():
                    if idx.data(NodeRole).id in ids:
                        self.table.expand(idx)
            self.sync_expansion()
        else:
            self.sync_expansion()
        n = sum(x is not None for x in (c.sections, c.statuses, c.executors, c.priorities, c.urgencies, c.impacts,
                                          c.initiators, c.period)) + sum([c.hide_done, c.only_overdue,
                                                                          c.only_edited, c.hide_empty])
        self.a_filters.setText(f"Фильтры · {n}" if n else "Фильтры")
        self.update_status()

    # ---------- файлы ----------
    def open_dialog(self) -> None:
        start = str(self.path.parent) if self.path else self.qs.value("lastDir", "")
        path, _ = QFileDialog.getOpenFileName(self, "Открыть", start, OPEN_FILTER)
        if path:
            self.open_file(path)

    def open_demo(self) -> None:
        self.open_file(str(resource_path("examples", "full.gantt.json")))

    def open_with_wizard(self) -> None:
        start = str(self.path.parent) if self.path else self.qs.value("lastDir", "")
        path, _ = QFileDialog.getOpenFileName(self, "Открыть таблицу через мастер", start,
                                              "Таблицы (*.xlsx *.xlsm *.csv *.tsv *.txt)")
        if path:
            self.open_file(path, wizard=True)

    def run_wizard(self, table, key: str | None = None, clipboard: bool = False) -> ReadResult | None:
        from .wizard import TableWizard

        dlg = TableWizard(table, self, clipboard=clipboard)
        if not dlg.exec():
            return None
        table, mapping, remember = dlg.result_mapping()
        try:
            res = table_result(table, mapping)
        except OpenError as e:
            self._error("Таблица не открылась", e)
            return None
        if remember:
            save_template(mapping)
        if key:
            self.table_mappings[key] = mapping
        return res

    def _error(self, title: str, e: OpenError, path: str | None = None) -> None:
        box = QMessageBox(QMessageBox.Icon.Warning, title, e.message, parent=self)
        if e.errors:
            box.setInformativeText((f"{Path(path).name}\n" if path else "") +
                                   f"Найдено замечаний: {len(e.errors)}. Подробности — «Показать подробности».")
            box.setDetailedText("\n".join(e.errors))
        box.exec()

    def open_file(self, path: str, reload: bool = False, wizard: bool = False) -> bool:
        key = str(Path(path).absolute())
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            try:
                res = open_path(path, wizard=wizard)
            except NeedsWizard as nw:
                mapping = self.table_mappings.get(key)
                if mapping is not None and not wizard:   # перечитывание: то же сопоставление, без мастера
                    res = table_result(nw.table, mapping)
                elif reload:
                    raise OpenError("Таблица изменилась так, что сопоставление колонок больше не подходит.")
                else:
                    QApplication.restoreOverrideCursor()
                    try:
                        res = self.run_wizard(nw.table, key)
                    finally:
                        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                    if res is None:
                        return False
        except OpenError as e:
            QApplication.restoreOverrideCursor()
            if reload:
                self.toast("Файл изменился, но не прочитался — показаны прежние данные")
                return False
            self._error("Не удалось открыть", e, path)
            if not Path(path).exists():
                self.remove_recent(path)
            return False
        except Exception as e:  # noqa: BLE001 — не роняем программу на неожиданном файле
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Не удалось открыть", f"Неожиданная ошибка при чтении файла:\n{e}")
            return False
        QApplication.restoreOverrideCursor()
        if res.kind == "changes":
            return self.import_changes(res.document)
        if res.kind == "results":
            return self.import_results(res.document)
        if res.kind == "incremental":
            return self.merge_incremental(res.document)
        return self.show_result(res, path, reload)

    def show_result(self, res: ReadResult, path: str | None, reload: bool = False) -> bool:
        if self.server is not None and not reload:
            self.disconnect_server()      # открыли файл — дальше работаем с ним, а не с сервером
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            view = self._view_state() if reload else None
            adopted = self.load_document(res.document, path, res.source_label, res.warnings, view)
        finally:
            QApplication.restoreOverrideCursor()
        if reload:
            self.toast(f"Файл обновлён: {Path(path).name}")
            if self.conflicts:
                QTimer.singleShot(300, self.resolve_conflicts)
        else:
            c = self.ds.counts()
            text = f"Открыто: разделов {c['group']} · проектов {c['project']} · заданий {c['task']}"
            if adopted:
                text += f" · правок из файла: {adopted}"
            if self.warnings or self.conflicts:
                self.toast(text + f" · замечаний {len(self.warnings) + len(self.conflicts)}", "Подробнее",
                           self.show_report, ms=6000)
            else:
                self.toast(text)
        return True

    def paste_table(self) -> None:
        text = QApplication.clipboard().text()
        if not text.strip() or not any(ch in text for ch in "\t;,"):
            self.toast("В буфере обмена нет таблицы — скопируйте строки вместе с заголовками")
            return
        table = read_clipboard_text(text)
        if len(table.rows) < 2:
            self.toast("В буфере одна строка — скопируйте строки вместе с заголовками")
            return
        try:
            if gantt_table.is_gantt_table(table.rows[0]):
                res = ReadResult(gantt_table.read_rows(table.rows, table.name), [], "Буфер обмена · Гант-таблица")
                errors = check(res.document).errors
                if errors:
                    raise OpenError("Таблица прочитана, но не прошла проверку.", errors[:50])
            else:
                m = find_template(table)
                res = table_result(table, m) if m is not None else self.run_wizard(table, clipboard=True)
        except OpenError as e:
            self._error("Таблица из буфера не открылась", e)
            return
        if res is not None:
            res.source_label = "Буфер обмена" + (f" · шаблон «{res.document['meta']['sourceName']}»"
                                                 if "шаблон" in res.source_label else "")
            self.show_result(res, None)

    # ---------- пришедшие документы обмена ----------
    def _need_data(self, what: str) -> bool:
        if self.ds is None:
            QMessageBox.information(self, APP_NAME, f"Это {what}. Сначала откройте данные, к которым он относится, "
                                                     "а потом этот файл.")
            return False
        return True

    def import_changes(self, doc: dict) -> bool:
        if not self._need_data("пакет правок"):
            return False
        dlg = ChangesImportDialog(self.ds, doc, self)
        if not dlg.exec():
            return False
        chosen = dlg.chosen()
        if not chosen:
            return False
        self.undo.beginMacro(f"Правки из файла ({len(chosen)})")
        for node, fld, value in chosen:
            self.undo.push(SetFieldsCommand(self, node, {fld: value}, "правка из файла"))
        self.undo.endMacro()
        self.toast(f"Добавлено в мои правки: {len(chosen)}", "Отменить", self.undo.undo)
        return True

    def import_results(self, doc: dict, ask: bool = True) -> bool:
        """ask=False — после отправки на сервер: окно только если есть конфликты или отказы."""
        if not self._need_data("ответ источника на правки"):
            return False
        dlg = ResultsDialog(self.ds, doc, self)
        if not dlg.matched:
            QMessageBox.information(self, APP_NAME, "Ни одна правка из ответа не найдена в очереди: их уже изменили, "
                                                     "вернули или ответ относится к другим данным.")
            return False
        if (ask or dlg.needs_choice) and not dlg.exec():
            return False
        closed = kept = 0
        for node, fld, action, value in dlg.decisions():
            raw_value = value.isoformat() if fld in DATE_FIELDS and value else value
            mine = self.ds.overrides.get((node.id, fld))
            node.set_source(fld, raw_value)  # значение источника теперь такое, каким его прислал ответ
            self.ds.invalidate()
            if action in ("applied", "source"):  # правка больше не нужна
                self.ds.set_value(node, fld, node.source(fld))
                self.store.delete(self.ds.key, node.id, fld)
                closed += 1
            else:  # оставить моё — отправить снова
                self.ds.set_value(node, fld, mine)
                if (node.id, fld) in self.ds.overrides:
                    self.store.save(self.ds.key, node.id, fld, mine, node.source(fld))
                    kept += 1
            self.model.node_changed(node)
        self.undo.clear()
        self._edits_changed()
        self.timeline.viewport().update()
        self.toast(f"Ответ источника учтён: закрыто правок {closed}" + (f", оставлено {kept}" if kept else ""))
        return True

    def merge_incremental(self, inc: dict) -> bool:
        if not self._need_data("файл изменений источника"):
            return False
        meta = inc["meta"]
        if f"{meta['source']}|{meta.get('sourceName') or ''}" != self.ds.key:
            QMessageBox.warning(self, APP_NAME, "Изменения относятся к другим данным: "
                                                f"«{meta.get('sourceName') or meta['source']}». "
                                                "Открытые данные не изменены.")
            return False
        doc, stats, warnings = apply_incremental(self.ds.doc, inc)
        res = check(doc)
        if res.errors:
            self._error("Изменения не применились", OpenError("После применения данные не прошли проверку. "
                                                             "Текущие данные не изменены.", res.errors))
            return False
        self.load_document(doc, str(self.path) if self.path else None, self.source_label,
                           self.warnings + warnings + res.warnings, self._view_state())
        self.toast(f"Изменения источника: обновлено {stats['updated']}, новых {stats['added']}, "
                   f"удалено {stats['deleted']}. Сохранить результат — «Сохранить как»", ms=7000)
        if self.conflicts:
            QTimer.singleShot(300, self.resolve_conflicts)
        return True

    def load_document(self, doc: dict, path: str | None, source_label: str, warnings: list[str],
                      view: dict | None = None) -> int:
        """Показать документ: правки из хранилища, расхождения с ними, раскрытие, масштаб."""
        self._save_expanded()
        key = f"{doc['meta']['source']}|{doc['meta'].get('sourceName') or ''}"
        stored, sources = self.store.load(key), self.store.load_sources(key)
        ids = {n["id"] for n in doc.get("nodes", [])}
        adopted = 0
        for c in doc.get("changes") or []:  # снимок с правками: чего нет у себя — берём в свою очередь
            k = (c["nodeId"], c["field"])
            if c["nodeId"] in ids and c["field"] in EDIT_FIELDS and k not in stored:
                dates = c["field"] in DATE_FIELDS
                new, old = c.get("newValue"), c.get("oldValue")
                stored[k] = date.fromisoformat(new) if dates and new else new
                sources[k] = date.fromisoformat(old) if dates and old else old
                self.store.save(key, k[0], k[1], stored[k], sources[k])
                adopted += 1
        ds = Dataset(doc, overrides={k: v for k, v in stored.items() if k[0] in ids})
        self.conflicts = []
        for (nid, fld), mine in stored.items():
            node = ds.nodes.get(nid)
            if node is None:
                self.conflicts.append({"node_id": nid, "field": fld, "name": "Узел, которого нет в новых данных",
                                       "deleted": True, "mine": mine})
                continue
            new_src, old_src = node.source(fld), sources.get((nid, fld))
            if new_src == mine:  # источник уже принял это значение — правка больше не нужна
                ds.set_value(node, fld, mine)
                self.store.delete(key, nid, fld)
            elif old_src != new_src:
                self.conflicts.append({"node_id": nid, "field": fld, "name": node.name, "deleted": False,
                                       "mine": mine, "old_source": old_src, "new_source": new_src})
        self.ds, self.warnings, self.source_label = ds, warnings, source_label
        self.path = Path(path) if path else None
        self.undo.clear()
        self.model.set_dataset(ds)
        self.table.ds = self.timeline.ds = ds
        header = self.table.header()
        header.levels = min(6, max((n.depth for n in ds.nodes.values()), default=0) + 1)
        header.level_names = [x["name"] for x in sorted(ds.levels.values(), key=lambda x: x.get("order", 0))]
        header.level_active = None
        self.filters.load_dataset(ds)
        self.proxy.set_criteria(self.criteria())
        lo, hi = ds.date_range()
        self.timeline.set_data_range(lo - timedelta(days=21), hi + timedelta(days=90))
        self.timeline.set_scale(self.timeline.make_scale(self.settings.zoom_ppd))
        self._restore_expanded()
        self.stack.setCurrentIndex(1)
        if path:
            self.add_recent(path)
            self.qs.setValue("lastFile", str(path))
            self.qs.setValue("lastDir", str(Path(path).parent))
        self._watch(self.path)
        self.update_title()
        self._edits_changed()
        if view:
            QTimer.singleShot(0, lambda: self._restore_view_state(view))
        else:
            QTimer.singleShot(0, lambda: self.timeline.scroll_to_date(self.timeline.today))
        if getattr(self, "_fresh_split", False):  # первый запуск: таблица — во все колонки, но не больше половины
            self._fresh_split = False
            QTimer.singleShot(0, self._fit_table)
        self._pre_search_expanded = None
        return adopted

    def _fit_table(self) -> None:
        sizes = self.splitter.sizes()
        total = sum(sizes)
        if total > 0:
            left = min(self.table.content_width(), int(total * 0.56))
            self.splitter.setSizes([left, total - left])

    def _view_state(self) -> dict:
        node = self.current_node()
        return {"v": self.timeline.verticalScrollBar().value(), "h": self.timeline.horizontalScrollBar().value(),
                "sel": node.id if node else None}

    def _restore_view_state(self, st: dict) -> None:
        self.timeline.verticalScrollBar().setValue(st["v"])
        self.timeline.horizontalScrollBar().setValue(st["h"])
        node = self.ds.nodes.get(st.get("sel") or "")
        if node is not None:
            self.table.setCurrentIndex(self.proxy.mapFromSource(self.model.index_for(node)))

    def show_report(self) -> None:
        if self.ds is None:
            return
        dlg = ImportReportDialog(self.ds, self.source_label, self.path.name if self.path else "", self.warnings,
                                 len(self.conflicts), self)
        dlg.exec()
        if dlg.resolve:
            self.resolve_conflicts()

    def resolve_conflicts(self) -> None:
        if not self.conflicts or self.ds is None:
            return
        dlg = ConflictsDialog(self.ds, self.conflicts, self)
        if not dlg.exec():
            return
        for c, drop in dlg.decisions():
            node = self.ds.nodes.get(c["node_id"])
            if drop:
                if node is not None:
                    self.ds.set_value(node, c["field"], node.source(c["field"]))
                    self.model.node_changed(node)
                self.store.delete(self.ds.key, c["node_id"], c["field"])
            elif node is not None:  # оставить моё — запомнить новое значение источника
                self.store.save(self.ds.key, node.id, c["field"], c["mine"], c["new_source"])
        self.conflicts = []
        self.undo.clear()
        self._edits_changed()
        self.toast("Расхождения разобраны")

    # слежение за файлом
    @staticmethod
    def _sig(path: Path | None):
        try:
            st = path.stat()
            return st.st_mtime_ns, st.st_size
        except (OSError, AttributeError):
            return None

    def _watch(self, path: Path | None) -> None:
        for p in self.watcher.files():
            self.watcher.removePath(p)
        self.poll.stop()
        if path is None or not self.settings.watch_file or not path.exists():
            return
        self.watcher.addPath(str(path))
        self._file_sig = self._sig(path)
        self.poll.start()

    def _reload_if_changed(self) -> None:
        if self.path is None or not self.settings.watch_file:
            return
        sig = self._sig(self.path)
        if sig is None or sig == self._file_sig:
            if str(self.path) not in self.watcher.files() and self.path.exists():
                self.watcher.addPath(str(self.path))
            return
        self._file_sig = sig
        self.open_file(str(self.path), reload=True)

    def _author(self) -> str:
        me = self._me_id()
        if me and self.ds is not None:
            return self.ds.person_name(me)
        return self.settings.me or getpass.getuser()

    def _save_path(self, title: str, ext: str, filt: str) -> str | None:
        base = (self.path.stem if self.path else self.ds.title or "gantt").replace(".gantt", "")
        folder = self.path.parent if self.path else Path(self.qs.value("lastDir", str(Path.home())))
        path, _ = QFileDialog.getSaveFileName(self, title, str(folder / f"{base}{ext}"), filt)
        if not path:
            return None
        if not path.lower().endswith(ext) and not (ext.endswith(".json") and path.lower().endswith(".json")):
            path += ext
        return path

    def save_as(self, fmt_key: str | None = None) -> None:
        if self.ds is None:
            return
        dlg = SaveAsDialog(self.qs, self.ds.title, self)
        if fmt_key:
            dlg.radios[fmt_key].setChecked(True)
        if not dlg.exec():
            return
        v = dlg.values()
        key = v["format"]
        _k, name, ext, _d, _l = next(w for w in WRITERS if w[0] == key)
        path = self._save_path("Сохранить как", ext, f"{name} (*{'.json' if ext.endswith('.json') else ext})")
        if path:
            self.write_as(key, path, v)

    def write_as(self, key: str, path: str, v: dict) -> bool:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        gen = {"name": APP_NAME, "version": __version__}
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if key in ("pdf", "png"):
                from .render import RenderOptions, save_pdf, save_png
                opts = RenderOptions(v.get("scope", "visible"), v.get("period", "data"), v.get("page", "A4"),
                                     v.get("title", ""))
                n = save_pdf(self, path, opts) if key == "pdf" else save_png(self, path, opts)
                done = f"Сохранено: {Path(path).name}" + (f" · листов {n}" if key == "pdf" else "")
            else:
                doc = self.ds.to_document(gen, stamp, bake=key != "json")
                res = check(doc)
                if res.errors:
                    raise ValueError("Снимок не прошёл проверку:\n" + "\n".join(res.errors[:10]))
                if key == "json":
                    write_json(doc, path)
                elif key == "xlsx":
                    gantt_table.write_xlsx(doc, path, self._colors(), chart=v.get("chart", True))
                elif key == "csv":
                    gantt_table.write_csv(doc, path)
                else:
                    msproject.write(doc, path)
                done = f"Сохранено: {Path(path).name}"
        except (OSError, ValueError) as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Не сохранено", str(e))
            return False
        QApplication.restoreOverrideCursor()
        self.qs.setValue("lastDir", str(Path(path).parent))
        self.toast(done, "Открыть", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)), ms=6000)
        return True

    def _colors(self) -> dict[str, str]:
        """Цвета проектов, как на экране, — для Excel."""
        out = {}
        for node in self.ds.walk():
            if self.ds.role(node) == "project" or self.ds.get(node, "color"):
                out[node.id] = self.project_color(node).name().upper()
        return out

    def save_change_list(self) -> None:
        if self.ds is None:
            return
        if not self.ds.edit_count:
            self.toast("Правок нет — лист изменений был бы пустым")
            return
        base = (self.path.stem if self.path else "gantt").replace(".gantt", "")
        folder = self.path.parent if self.path else Path(self.qs.value("lastDir", str(Path.home())))
        path, filt = QFileDialog.getSaveFileName(self, "Лист изменений", str(folder / f"{base} — изменения.xlsx"),
                                                 "Excel (*.xlsx);;PDF (*.pdf)")
        if path:
            self.write_change_list(path, filt.startswith("PDF"))

    def write_change_list(self, path: str, pdf_filter: bool = False) -> bool:
        pdf = path.lower().endswith(".pdf") or (pdf_filter and not path.lower().endswith(".xlsx"))
        if pdf and not path.lower().endswith(".pdf"):
            path += ".pdf"
        elif not pdf and not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            n = (write_change_list_pdf if pdf else write_change_list_xlsx)(self.ds, path, self._author())
        except OSError as e:
            QMessageBox.warning(self, "Не сохранено", str(e))
            return False
        self.toast(f"Лист изменений: правок {n} · {Path(path).name}", "Открыть",
                   lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)), ms=6000)
        return True

    def export_changes(self) -> None:
        if self.ds is None:
            return
        doc = self.changes_pack()
        if not doc["changes"]:
            self.toast("Правок данных нет (цвета в источник не отправляются)")
            return
        res = check(doc)
        if res.errors:
            QMessageBox.warning(self, "Не выгружено", "\n".join(res.errors[:10]))
            return
        path = self._save_path("Выгрузить правки", ".changes.json", "Файл обмена (*.json)")
        if not path:
            return
        write_json(doc, path)
        self.toast(f"Выгружено правок: {len(doc['changes'])} · {Path(path).name}")

    def changes_pack(self) -> dict:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        return self.ds.changes_document({"name": APP_NAME, "version": __version__}, stamp, self._author(),
                                        os.environ.get("COMPUTERNAME") or socket.gethostname())

    def recent_files(self) -> list[str]:
        raw = self.qs.value("recent", "[]")
        try:
            return [p for p in json.loads(raw) if isinstance(p, str)]
        except (TypeError, ValueError):
            return []

    def add_recent(self, path: str) -> None:
        path = str(Path(path))
        items = [p for p in self.recent_files() if p != path]
        self.qs.setValue("recent", json.dumps([path] + items[:MAX_RECENT - 1], ensure_ascii=False))
        self.start.refresh(self.recent_files())

    def remove_recent(self, path: str) -> None:
        self.qs.setValue("recent", json.dumps([p for p in self.recent_files() if p != str(Path(path))],
                                              ensure_ascii=False))
        self.start.refresh(self.recent_files())

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        for p in self.recent_files():
            a = self.recent_menu.addAction(Path(p).name)
            a.setToolTip(p)
            a.triggered.connect(lambda _=False, p=p: self.open_file(p))
        if not self.recent_menu.actions():
            self.recent_menu.addAction("Пусто").setEnabled(False)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.open_file(urls[0])

    def open_external(self, path: str) -> None:
        """Файл из второго запуска программы: окно выходит вперёд и открывает его."""
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        if path:
            self.open_file(path)

    # ---------- правки ----------
    def current_node(self) -> Node | None:
        return self.table.currentIndex().data(NodeRole)

    def toast(self, text: str, action: str | None = None, callback=None, ms: int = 4200) -> None:
        Toast(self, text, action, callback, ms, dark=bool(self.tokens and self.tokens.dark))

    def on_double_click(self, node: Node, column: int) -> None:
        if node is None or self.ds is None:
            return
        role = self.ds.role(node)
        if column == COL["status"] and role == "task" and self.ds.statuses:
            self.status_menu(node).exec(QCursor.pos())
            return
        if role == "group":
            idx = self.proxy.mapFromSource(self.model.index_for(node))
            self.table.setExpanded(idx, not self.table.isExpanded(idx))
            return
        self.edit_node(node)

    def edit_node(self, node: Node | None) -> None:
        if node is None or self.ds is None or self.ds.role(node) == "group":
            return
        current = self.project_color(node) if self.ds.role(node) == "project" else None
        dlg = NodeEditDialog(self.ds, node, self.palette_cache, palette_names(self.settings), current, self)
        if not dlg.exec():
            return
        if dlg.reset_requested:
            self._revert(node, self.ds.edited_fields(node))
            return
        changes = dlg.changes()
        if changes:
            self.push(node, changes, f"Правка «{node.name[:40]}»", "Изменено")

    def push(self, node: Node, changes: dict, undo_text: str, toast: str | None = None) -> None:
        self.undo.push(SetFieldsCommand(self, node, changes, undo_text))
        if toast:
            self.toast(f"{toast}: {node.name[:48]}", "Отменить", self.undo.undo)

    def on_dates_dragged(self, node: Node, start, end) -> None:
        if not (self.ds.can_edit(node, "planStart") and self.ds.can_edit(node, "planEnd")):
            self.toast("Источник не разрешает менять сроки этого узла")
            self.timeline.viewport().update()
            return
        changes = {}
        if start != self.ds.get(node, "planStart"):
            changes["planStart"] = start
        if end != self.ds.get(node, "planEnd"):
            changes["planEnd"] = end
        if changes:
            self.undo.push(SetFieldsCommand(self, node, changes, f"Сроки «{node.name[:40]}»"))
            self.toast(f"План: {fmt(start, False)} → {fmt(end, False)}", "Отменить", self.undo.undo)

    def set_color(self, node: Node, color: str | None) -> None:
        name = self.color_title(QColor(color)) if color else "авто"
        self.push(node, {"color": color}, f"Цвет «{node.name[:40]}»", f"Цвет «{name.lower()}»")

    def set_status(self, node: Node, status_id: str) -> None:
        changes = {"status": status_id}
        kind = self.ds.statuses.get(status_id, {}).get("kind")
        if kind == "done" and not self.ds.get(node, "factEnd"):
            changes["factEnd"] = date.today()   # выполнено — факт окончания сегодня, если не задан
        name = self.ds.statuses.get(status_id, {}).get("name", status_id)
        self.push(node, changes, f"Статус «{node.name[:40]}»", f"Статус «{name}»")

    def _revert(self, node: Node, fields: list[str]) -> None:
        fields = [f for f in fields if (node.id, f) in self.ds.overrides]
        if fields:
            self.push(node, {f: node.source(f) for f in fields}, f"Возврат «{node.name[:40]}»", "Возвращено")

    def _revert_all(self) -> None:
        if self.ds is None or not self.ds.overrides:
            return
        if QMessageBox.question(self, APP_NAME, f"Вернуть все правки ({self.ds.edit_count}) как в источнике?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.undo.beginMacro("Вернуть все правки")
        for (nid, fld) in list(self.ds.overrides):
            node = self.ds.nodes.get(nid)
            if node is not None:
                self.undo.push(SetFieldsCommand(self, node, {fld: node.source(fld)}, "возврат"))
        self.undo.endMacro()
        self.toast("Все правки возвращены", "Отменить", self.undo.undo)

    def apply_values(self, node: Node, values: dict) -> None:
        for fld, value in values.items():
            self.ds.set_value(node, fld, value)
            if (node.id, fld) in self.ds.overrides:
                self.store.save(self.ds.key, node.id, fld, value, node.source(fld))
            else:
                self.store.delete(self.ds.key, node.id, fld)
        if "color" in values:
            self.model._chips.clear()
        self.model.node_changed(node)
        if self.proxy.c.active():
            self.proxy.set_criteria(self.proxy.c)
        self.timeline.viewport().update()
        self.table.viewport().update()

    def _edits_changed(self) -> None:
        n = self.ds.edit_count if self.ds else 0
        self.a_changes.setText(f"Изменения · {n}" if n else "Изменения")
        self.changes.refresh(self.ds)
        self.update_status()

    def status_menu(self, node: Node) -> QMenu:
        m = QMenu("Статус", self)
        cur = self.ds.get(node, "status")
        allowed = self.ds.can_edit(node, "status")
        for st in self.ds.status_choices():
            a = m.addAction(st["name"])
            a.setCheckable(True)
            a.setChecked(st["id"] == cur)
            a.setEnabled(allowed)
            a.triggered.connect(lambda _=False, sid=st["id"]: self.set_status(node, sid))
        return m

    def context_menu(self, node: Node, pos) -> None:
        if node is None or self.ds is None:
            return
        m = QMenu(self)
        role = self.ds.role(node)
        if role != "group":
            m.addAction("Изменить план, факт" + (" и цвет…" if role == "project" else " и статус…"),
                        lambda: self.edit_node(node))
        if role == "task" and self.ds.statuses:
            m.addMenu(self.status_menu(node))
        if role == "project":
            cm = m.addMenu("Цвет проекта")
            for c, name in zip(self.palette_cache, palette_names(self.settings)):
                a = cm.addAction(swatch_icon(c), name)
                a.triggered.connect(lambda _=False, c=c: self.set_color(node, c.name().upper()))
            cm.addSeparator()
            cm.addAction("Авто (из палитры)", lambda: self.set_color(node, None))
        if self.ds.is_edited(node):
            m.addAction("Вернуть как в источнике", lambda: self._revert(node, self.ds.edited_fields(node)))
        if node.raw.get("url"):
            m.addAction("Открыть в источнике", lambda: QDesktopServices.openUrl(QUrl(node.raw["url"])))
        if node.children:
            m.addSeparator()
            idx = self.proxy.mapFromSource(self.model.index_for(node))
            if self.table.isExpanded(idx):
                m.addAction("Свернуть", lambda: self.table.collapse(idx))
            else:
                m.addAction("Развернуть", lambda: self.table.expand(idx))
            m.addAction("Развернуть всё внутри", lambda: self._expand_branch(idx))
        m.exec(pos)

    # ---------- вложенность ----------
    def _all_indexes(self, parent=QModelIndex()):
        for r in range(self.proxy.rowCount(parent)):
            idx = self.proxy.index(r, 0, parent)
            yield idx
            yield from self._all_indexes(idx)

    def expand_all(self) -> None:
        with self._no_anim():
            self.table.expandAll()
            self.timeline.expandAll()
        self._mark_level(self.table.header().levels)

    def _mark_level(self, n: int | None) -> None:
        self.table.header().level_active = n
        self.table.header().viewport().update()

    def collapse_all(self) -> None:
        with self._no_anim():
            self.table.collapseAll()
            self.timeline.collapseAll()
        self._mark_level(1)

    def collapse_to_projects(self) -> None:
        """Разделы раскрыты, проекты свёрнуты — как обзор портфеля."""
        with self._no_anim():
            self.table.collapseAll()
            self.timeline.collapseAll()
            for idx in self._all_indexes():
                node = idx.data(NodeRole)
                if node is not None and self.ds.role(node) == "group":
                    self.table.expand(idx)
        proj = next((n for n in self.ds.walk() if self.ds.role(n) == "project"), None)
        self._mark_level(proj.depth + 1 if proj is not None else None)

    def _expand_branch(self, idx) -> None:
        with self._no_anim():
            self.table.expandRecursively(idx)
        self.sync_expansion()

    def _expanded_key(self) -> str:
        return "expanded/" + hashlib.md5(self.ds.key.encode("utf-8")).hexdigest()

    def _save_expanded(self) -> None:
        if self.ds is None:
            return
        ids = [idx.data(NodeRole).id for idx in self._all_indexes() if self.table.isExpanded(idx)]
        self.qs.setValue(self._expanded_key(), json.dumps(ids))

    def _restore_expanded(self) -> None:
        raw = self.qs.value(self._expanded_key(), None)
        if raw is None:
            self.collapse_to_projects()
            return
        try:
            ids = set(json.loads(raw))
        except (TypeError, ValueError):
            ids = set()
        with self._no_anim():
            for idx in self._all_indexes():
                if idx.data(NodeRole).id in ids:
                    self.table.expand(idx)
        self.sync_expansion()

    # ---------- строка состояния, заголовок ----------
    def update_title(self) -> None:
        if self.ds is None:
            self.setWindowTitle(APP_NAME)
            return
        where = self.path.name if self.path else (f"сервер «{self.server.name}»" if self.server else "")
        self.setWindowTitle(" — ".join(x for x in (self.ds.title, where, APP_NAME) if x))

    def update_status(self) -> None:
        if self.ds is None:
            return
        c = self.ds.counts()
        parts = [f"Разделов: {c['group']}", f"проектов: {c['project']}", f"заданий: {c['task']}"]
        if self.proxy.c.active():
            parts.append("фильтр включён")
        if self.me_mode and self.settings.me:
            parts.append(f"режим «Исполнитель»: {self.settings.me}")
        if self.warnings:
            parts.append(f"замечаний при открытии: {len(self.warnings)}")
        self.status.setText(" · ".join(parts))
        self.status.setToolTip("\n".join(self.warnings))
        n = self.ds.edit_count
        self.edits_label.setText(f"Изменено: {n}" if n else "Изменений нет")

    def about(self) -> None:
        from ..install import current_exe, frozen, short_version

        where = str(current_exe().parent) if frozen() else "из исходников"
        source = self.update_source() or "не задан"
        QMessageBox.about(self, "О программе",
                          f"<b>{APP_NAME} {short_version()}</b> <span style='color:gray'>({__version__})</span><br>"
                          f"Диаграмма Ганта для управления проектами.<br>Подготовил {AUTHOR}.<br><br>"
                          f"Программа: {where}<br>Данные и правки: {data_dir()}<br>Обновления: {source}<br><br>"
                          "Правки хранятся на этом ПК и переживают перезапуск и новые выгрузки отчёта.<br>"
                          "Как пользоваться — «Справка → Руководство пользователя» (Ctrl+F1).")

    def header_menu(self, pos) -> None:
        """Меню заголовка: колонки и их группы одним списком — меню не закрывается, пока вы щёлкаете по нему."""
        m = QMenu(self)
        m.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        a = m.addAction("Порядок как в источнике")
        a.triggered.connect(self._source_order)
        m.addSeparator()
        self.column_list = ColumnList(self, m.close)
        wa = QWidgetAction(m)
        wa.setDefaultWidget(self.column_list)
        m.addAction(wa)
        m.exec(self.table.header().mapToGlobal(pos))

    def _source_order(self) -> None:
        self.table.header().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.proxy.sort(-1)

    def _toggle_column(self, key: str, on: bool) -> None:
        hidden = [k for k in self.settings.hidden_columns if k != key] + ([] if on else [key])
        groups = self.settings.column_groups
        if on:   # колонка в свёрнутой группе — группа разворачивается, иначе включённая колонка не появится
            groups = [dict(g, collapsed=False) if key in g.get("cols", []) else g for g in groups]
        self.apply_settings(replace(self.settings, hidden_columns=hidden, column_groups=groups))

    # ---------- окно ----------
    def _restore_window(self) -> None:
        geo = self.qs.value("window/geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        else:
            self.resize(1440, 860)
        state = self.qs.value("window/state3")
        if isinstance(state, QByteArray):
            self.restoreState(state)
        split = self.qs.value("window/splitter")
        self._fresh_split = not isinstance(split, QByteArray)
        if not self._fresh_split:
            self.splitter.restoreState(split)
        head = self.qs.value("window/header3")
        header = self.table.header()
        if isinstance(head, QByteArray):
            header.restoreState(head)
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.table.setSortingEnabled(True)
        self.table.setColumnHidden(COL["timeline"], True)
        self._apply_columns(self.settings)

    def closeEvent(self, event) -> None:
        self.card.hide()
        self._save_expanded()
        self.qs.setValue("window/geometry", self.saveGeometry())
        self.qs.setValue("window/state3", self.saveState())
        self.qs.setValue("window/splitter", self.splitter.saveState())
        self.qs.setValue("window/header3", self.table.header().saveState())
        save_view(self.settings)
        self.qs.sync()
        super().closeEvent(event)
