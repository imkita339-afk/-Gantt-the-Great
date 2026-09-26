"""Окна: правка узла (план, факт, статус, цвет проекта) и настройки внешнего вида."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from PySide6.QtCore import QDate, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDateEdit, QDialog,
                               QDialogButtonBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from ..model import Dataset, Node, fmt
from ..settings import ViewSettings
from .columns import ColumnList
from .theme import ACCENTS, PALETTES, THEMES, Tokens, color_name, palette_names, project_palette
from .widgets import potato_icon


def swatch_icon(color: QColor, size: int = 18) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(color.darker(130))
    p.setBrush(color)
    p.drawRoundedRect(1, 1, size - 2, size - 2, 4, 4)
    p.end()
    return QIcon(pm)


def theme_icon(t: Tokens, accent: str, w: int = 44, h: int = 28) -> QIcon:
    """Миниатюра темы: фон, панель и две полосы акцентного и пастельного цвета."""
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(t.line))
    p.setBrush(QColor(t.bg))
    p.drawRoundedRect(0.5, 0.5, w - 1, h - 1, 5, 5)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(t.panel))
    p.drawRoundedRect(4, 4, w - 8, h - 8, 3, 3)
    p.setBrush(QColor(accent))
    p.drawRoundedRect(8, 8, 18, 4, 2, 2)
    p.setBrush(QColor.fromHslF(145 / 360, 0.5, 0.68 if t.dark else 0.8))
    p.drawRoundedRect(16, 15, 20, 4, 2, 2)
    p.end()
    return QIcon(pm)


class DateField(QWidget):
    """Дата, которой может не быть: флажок + поле с календарём."""

    def __init__(self, value: date | None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.check = QCheckBox()
        self.check.setToolTip("Дата задана")
        self.edit = QDateEdit()
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("dd.MM.yyyy")
        self.edit.setMinimumWidth(130)
        lay.addWidget(self.check)
        lay.addWidget(self.edit, 1)
        self.check.toggled.connect(self.edit.setEnabled)
        self.set_value(value)

    def set_value(self, value: date | None) -> None:
        self.check.setChecked(value is not None)
        self.edit.setEnabled(value is not None)
        d = value or date.today()
        self.edit.setDate(QDate(d.year, d.month, d.day))

    def value(self) -> date | None:
        if not self.check.isChecked():
            return None
        q = self.edit.date()
        return date(q.year(), q.month(), q.day())


class NodeEditDialog(QDialog):
    """План, факт, статус (задания) и цвет (проекта). Возвращает только изменённые поля."""

    RESET = object()

    def __init__(self, ds: Dataset, node: Node, palette: list[QColor], names: list[str],
                 current_color: QColor | None, parent=None):
        super().__init__(parent)
        self.ds, self.node = ds, node
        self.setWindowTitle("Изменить")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        title = QLabel(f"<b style='font-size:13pt'>{node.name}</b><br>"
                       f"<span style='color:gray'>{ds.level_name(node)}"
                       f"{' № ' + node.number if node.number else ''}</span>")
        title.setWordWrap(True)
        lay.addWidget(title)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.fields: dict[str, DateField] = {}
        for fld, label in (("planStart", "План: начало"), ("planEnd", "План: окончание"),
                           ("factStart", "Факт: начало"), ("factEnd", "Факт: окончание")):
            f = DateField(ds.get(node, fld))
            if not ds.can_edit(node, fld):
                f.setEnabled(False)
                f.setToolTip("Источник не разрешает менять это поле")
            self.fields[fld] = f
            form.addRow(label, self._with_hint(f, fld, fmt(node.source(fld), False)))
        self.status = None
        if ds.role(node) == "task" and ds.statuses:
            self.status = QComboBox()
            for st in ds.status_choices():
                self.status.addItem(st["name"], st["id"])
            self.status.setCurrentIndex(max(0, self.status.findData(ds.get(node, "status"))))
            if not ds.can_edit(node, "status"):
                self.status.setEnabled(False)
                self.status.setToolTip("Источник не разрешает менять статус")
            src = ds.statuses.get(node.source("status") or "", {}).get("name", "—")
            form.addRow("Статус", self._with_hint(self.status, "status", src))
        lay.addLayout(form)

        self.color_choice = None  # None — не менять, RESET — авто, QColor — выбранный
        if ds.role(node) == "project":
            box = QGroupBox("Цвет проекта")
            gl = QGridLayout(box)
            self.color_group = QButtonGroup(self)
            self.color_group.setExclusive(True)
            for i, (c, name) in enumerate(zip(palette, names)):
                b = QToolButton()
                b.setIcon(swatch_icon(c, 22))
                b.setIconSize(QSize(22, 22))
                b.setCheckable(True)
                b.setAutoRaise(True)
                b.setToolTip(name)
                b.clicked.connect(lambda _=False, c=c, n=name: self._pick(c, n))
                self.color_group.addButton(b)
                if current_color is not None and c.name() == current_color.name():
                    b.setChecked(True)
                gl.addWidget(b, i // 6, i % 6)
            other = QPushButton("Другой…")
            other.clicked.connect(self._other)
            auto = QPushButton("Авто")
            auto.setToolTip("Цвет из палитры, как был по умолчанию")
            auto.clicked.connect(lambda: self._pick(self.RESET, "Авто — из палитры"))
            gl.addWidget(other, 0, 6)
            gl.addWidget(auto, 1, 6)
            self.preview = QLabel()
            gl.addWidget(self.preview, 2, 0, 1, 7)
            lay.addWidget(box)
            if current_color is not None:
                known = dict((c.name(), n) for c, n in zip(palette, names))
                self._show(current_color, known.get(current_color.name(), color_name(current_color)))

        self.error = QLabel()
        self.error.setStyleSheet("color: #C2185B;")
        lay.addWidget(self.error)
        buttons = QDialogButtonBox()
        reset = buttons.addButton("Вернуть как в источнике", QDialogButtonBox.ButtonRole.ResetRole)
        reset.setEnabled(ds.is_edited(node))
        reset.clicked.connect(self._reset_all)
        ok = buttons.addButton("Сохранить", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setObjectName("primary")
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.reset_requested = False

    def _with_hint(self, w: QWidget, fld: str, source_text: str) -> QWidget:
        row = QWidget()
        rl = QVBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(1)
        rl.addWidget(w)
        if (self.node.id, fld) in self.ds.overrides:
            hint = QLabel(f"в источнике: {source_text}")
            hint.setStyleSheet("color: gray; font-size: 8pt;")
            rl.addWidget(hint)
        return row

    def _show(self, c: QColor, name: str) -> None:
        self.preview.setText(f"<span style='background:{c.name()};'>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span>"
                             f"&nbsp; {name}")

    def _pick(self, c, name: str) -> None:
        self.color_choice = c
        if c is self.RESET:
            self.preview.setText(name)
        else:
            self._show(c, name)

    def _other(self) -> None:
        c = QColorDialog.getColor(QColor("#A9C4F5"), self, "Цвет проекта")
        if c.isValid():
            for b in self.color_group.buttons():
                b.setChecked(False)
            self._pick(c, color_name(c))

    def _reset_all(self) -> None:
        self.reset_requested = True
        self.accept()

    def _accept(self) -> None:
        v = {k: f.value() for k, f in self.fields.items()}
        for a, b, name in (("planStart", "planEnd", "план"), ("factStart", "factEnd", "факт")):
            if v[a] and v[b] and v[a] > v[b]:
                self.error.setText(f"Начало ({name}) позже окончания.")
                return
        self.accept()

    def changes(self) -> dict[str, object]:
        """Поле → новое значение (только то, что поменялось). Цвет None — вернуть автоматический."""
        res = {}
        for fld, f in self.fields.items():
            if f.value() != self.ds.get(self.node, fld):
                res[fld] = f.value()
        if self.status is not None and self.status.currentData() != self.ds.get(self.node, "status"):
            res["status"] = self.status.currentData()
        if self.color_choice is self.RESET:
            if self.ds.get(self.node, "color") is not None:
                res["color"] = None
        elif isinstance(self.color_choice, QColor):
            res["color"] = self.color_choice.name().upper()
        return res


class SettingsPanel(QWidget):
    """Настройки вида в три колонки: меняются сразу, запоминаются. Открывается панелью под кнопкой «Настройки».

    Колонки и их группы — тот же список, что в меню заголовка: он меняет настройки окна сразу, поэтому
    остальные поля собираются поверх текущих настроек окна, а не своей копии.
    """

    changed = Signal(object)

    def __init__(self, settings: ViewSettings, tokens: Tokens, win, parent=None):
        super().__init__(parent)
        self.s = replace(settings)
        self.tokens = tokens
        self.win = win
        self._building = True
        grid = QHBoxLayout(self)
        grid.setContentsMargins(12, 8, 12, 10)
        grid.setSpacing(14)
        c1, c2, c3 = QVBoxLayout(), QVBoxLayout(), QVBoxLayout()
        for c in (c1, c2, c3):
            grid.addLayout(c)

        look = QGroupBox("Оформление")
        f = QFormLayout(look)
        self.theme = QComboBox()
        self.theme.setIconSize(QSize(44, 28))
        for key, t in THEMES.items():
            self.theme.addItem(theme_icon(t, settings.accent), t.title, key)
        self.theme.addItem("Как в системе", "system")
        f.addRow("Тема", self.theme)
        acc = QHBoxLayout()
        self.accent_group = QButtonGroup(self)
        for color, title in ACCENTS:
            b = QToolButton()
            b.setIcon(swatch_icon(QColor(color), 20))
            b.setIconSize(QSize(20, 20))
            b.setCheckable(True)
            b.setAutoRaise(True)
            b.setToolTip(title)
            b.setProperty("color", color)
            self.accent_group.addButton(b)
            acc.addWidget(b)
        acc.addStretch()
        f.addRow("Акцентный цвет", acc)
        self.font = QSpinBox()
        self.font.setRange(8, 14)
        self.font.setSuffix(" пт")
        f.addRow("Размер шрифта", self.font)
        self.row = QSpinBox()
        self.row.setRange(20, 44)
        self.row.setSuffix(" пикс.")
        f.addRow("Высота строки", self.row)
        self.animations = QCheckBox("Плавные анимации")
        f.addRow("", self.animations)
        self.zebra = QCheckBox("Строки через одну темнее")
        self.show_legend = QCheckBox("Строка обозначений")
        self.hover_card = QCheckBox("Карточка при наведении")
        for w in (self.zebra, self.show_legend, self.hover_card):
            f.addRow("", w)
        tips = {self.animations: "Масштаб, прокрутка, раскрытие, смена темы, отклик кнопок",
                self.zebra: "Глазу легче идти по длинной строке от таблицы к шкале",
                self.show_legend: "Что значат полосы и цвета — строкой под диаграммой",
                self.hover_card: "План, факт, отклонение от срока, исполнители — при наведении на строку"}
        for w, tip in tips.items():
            w.setToolTip(tip)
        c1.addWidget(look)

        bars = QGroupBox("Полосы")
        g = QFormLayout(bars)
        self.palette = QComboBox()
        for key, (title, _h, _s) in PALETTES.items():
            self.palette.addItem(title, key)
        g.addRow("Палитра проектов", self.palette)
        self.strip = QLabel()
        g.addRow("", self.strip)
        self.light = QSlider(Qt.Orientation.Horizontal)
        self.light.setRange(70, 92)
        g.addRow("Светлота пастели", self.light)
        self.task_colors = QComboBox()
        self.task_colors.addItem("По статусу", "status")
        self.task_colors.addItem("Цветом проекта", "project")
        g.addRow("Цвет заданий", self.task_colors)
        self.radius = QSpinBox()
        self.radius.setRange(0, 12)
        self.radius.setSuffix(" пикс.")
        g.addRow("Скругление", self.radius)
        self.labels = QComboBox()
        self.labels.addItem("Нет", "none")
        self.labels.addItem("Прогресс проектов", "progress")
        self.labels.addItem("Названия и прогресс", "names")
        g.addRow("Подписи у полос", self.labels)
        self.show_fact = QCheckBox("Факт — полосой внутри плана")
        self.show_today = QCheckBox("Линия «сегодня»")
        self.show_week = QCheckBox("Текущая неделя")
        self.show_weekends = QCheckBox("Выходные дни")
        self.show_marks = QCheckBox("Метки приоритета у полос")
        for w in (self.show_fact, self.show_today, self.show_week, self.show_weekends, self.show_marks):
            g.addRow("", w)
        self.show_fact.setToolTip("Насыщенная полоса внутри светлой дорожки плана — когда делали на самом деле")
        self.show_weekends.setToolTip("В масштабах «день» и «неделя»")
        self.show_marks.setToolTip("Цветные точки приоритета, срочности и влияния справа от полосы")
        c2.addWidget(bars)
        from .update import UpdateBox   # здесь, а не наверху: update → main_window → dialogs
        upd = QGroupBox("Обновление программы")
        self.updates = UpdateBox(win)
        upd.setLayout(self.updates)
        c2.addWidget(upd)
        c2.addStretch()

        work = QGroupBox("Работа с файлами")
        wl = QVBoxLayout(work)
        self.reopen = QCheckBox("Открывать последний файл")
        self.watch = QCheckBox("Перечитывать файл при изменении")
        wl.addWidget(self.reopen)
        wl.addWidget(self.watch)
        c1.addWidget(work)

        perf = QGroupBox("Производительность")
        pl = QVBoxLayout(perf)
        self.potato = QCheckBox("Режим картошки")
        self.potato.setIcon(potato_icon(18))
        self.potato.setToolTip("Для слабых компьютеров и удалённого рабочего стола")
        pl.addWidget(self.potato)
        hint = QLabel("Без анимаций, теней и сглаживания: программа рисует проще и отзывается быстрее.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        pl.addWidget(hint)
        c1.addWidget(perf)
        c1.addStretch()

        cols = QGroupBox("Колонки таблицы")
        cl = QVBoxLayout(cols)
        cl.setContentsMargins(4, 6, 4, 6)
        self.columns = ColumnList(win, compact=True)
        cl.addWidget(self.columns)
        c3.addWidget(cols)
        c3.addStretch()
        reset = QPushButton("Сбросить к стандартным")
        reset.clicked.connect(self._defaults)
        c3.addWidget(reset, 0, Qt.AlignmentFlag.AlignRight)

        self._load(self.s)
        for w in (self.theme, self.palette, self.task_colors, self.labels):
            w.currentIndexChanged.connect(self._collect)
        for w in (self.font, self.row, self.radius):
            w.valueChanged.connect(self._collect)
        self.light.valueChanged.connect(self._collect)
        for w in (self.show_fact, self.show_today, self.show_week, self.show_weekends, self.show_marks,
                  self.reopen, self.watch, self.animations, self.zebra, self.show_legend, self.hover_card,
                  self.potato):
            w.toggled.connect(self._collect)
        self.accent_group.buttonClicked.connect(self._collect)
        self._building = False

    def reload(self, settings: ViewSettings, tokens: Tokens) -> None:
        """Настройки могли поменяться в другом месте (тема, колонки) — показать текущие."""
        self.s, self.tokens = replace(settings), tokens
        self._load(self.s)
        self.columns.cancel()
        self.updates.reload()

    def _load(self, s: ViewSettings) -> None:
        self._building = True
        self.theme.setCurrentIndex(max(0, self.theme.findData(s.theme)))
        for b in self.accent_group.buttons():
            b.setChecked(b.property("color").lower() == s.accent.lower())
        self.font.setValue(s.font_size)
        self.row.setValue(s.row_height)
        self.animations.setChecked(s.animations)
        self.potato.setChecked(s.potato)
        self._potato_lock(s.potato)
        self.zebra.setChecked(s.zebra)
        self.show_legend.setChecked(s.show_legend)
        self.hover_card.setChecked(s.hover_card)
        self.palette.setCurrentIndex(max(0, self.palette.findData(s.palette)))
        self.light.setValue(s.pastel_lightness)
        self.task_colors.setCurrentIndex(max(0, self.task_colors.findData(s.task_colors)))
        self.radius.setValue(s.bar_radius)
        self.labels.setCurrentIndex(max(0, self.labels.findData(s.labels)))
        self.show_fact.setChecked(s.show_fact)
        self.show_today.setChecked(s.show_today)
        self.show_week.setChecked(s.show_current_week)
        self.show_weekends.setChecked(s.show_weekends)
        self.show_marks.setChecked(s.show_scale_marks)
        self.reopen.setChecked(s.reopen_last)
        self.watch.setChecked(s.watch_file)
        self._strip()
        self._building = False

    def _strip(self) -> None:
        colors = project_palette(self.s, self.tokens)
        names = palette_names(self.s)
        pm = QPixmap(22 * len(colors), 18)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, c in enumerate(colors):
            p.setPen(c.darker(125))
            p.setBrush(c)
            p.drawRoundedRect(i * 22 + 1, 1, 19, 16, 4, 4)
        p.end()
        self.strip.setPixmap(pm)
        self.strip.setToolTip(", ".join(names))

    def _collect(self, *_):
        if self._building:
            return
        acc = next((b.property("color") for b in self.accent_group.buttons() if b.isChecked()), self.s.accent)
        self.s = replace(
            self.win.settings, theme=self.theme.currentData(), accent=acc, font_size=self.font.value(),
            row_height=self.row.value(), palette=self.palette.currentData(), pastel_lightness=self.light.value(),
            task_colors=self.task_colors.currentData(), bar_radius=self.radius.value(),
            labels=self.labels.currentData(), show_fact=self.show_fact.isChecked(),
            show_today=self.show_today.isChecked(), show_current_week=self.show_week.isChecked(),
            show_weekends=self.show_weekends.isChecked(), show_scale_marks=self.show_marks.isChecked(),
            reopen_last=self.reopen.isChecked(), watch_file=self.watch.isChecked(),
            animations=self.animations.isChecked(), zebra=self.zebra.isChecked(),
            show_legend=self.show_legend.isChecked(), hover_card=self.hover_card.isChecked(),
            potato=self.potato.isChecked())
        self._potato_lock(self.s.potato)
        self._strip()
        self.changed.emit(self.s)

    def _potato_lock(self, on: bool) -> None:
        """В режиме картошки анимаций нет — их флажок недоступен, но своё значение помнит."""
        self.animations.setEnabled(not on)
        self.animations.setToolTip("Выключены режимом картошки" if on else
                                   "Масштаб, прокрутка, раскрытие, смена темы, отклик кнопок")

    def _defaults(self) -> None:
        d = ViewSettings.defaults()
        d.zoom_ppd, d.me = self.s.zoom_ppd, self.s.me
        self.s = d
        self._load(d)
        self.changed.emit(self.s)
        self.columns.cancel()


class SettingsDialog(QDialog):
    """Те же настройки отдельным окном — если окно программы слишком узкое для панели."""

    changed = Signal(object)

    def __init__(self, settings: ViewSettings, tokens: Tokens, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки вида")
        lay = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.panel = SettingsPanel(settings, tokens, parent)
        self.panel.changed.connect(self.changed)
        scroll.setWidget(self.panel)
        lay.addWidget(scroll)
        close = QPushButton("Готово")
        close.setObjectName("primary")
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        self.resize(980, 640)


class ThemePanel(QWidget):
    """Тема, акцент и палитра проектов плитками — открывается панелью под кнопкой «Тема»."""

    changed = Signal(object)

    def __init__(self, settings: ViewSettings, parent=None):
        super().__init__(parent)
        self.s = replace(settings)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)
        lay.addWidget(QLabel("<b>Тема</b>"))
        grid = QGridLayout()
        grid.setSpacing(4)
        self.themes = QButtonGroup(self)
        for i, (key, t) in enumerate(list(THEMES.items()) + [("system", None)]):
            b = QToolButton()
            b.setCheckable(True)
            b.setAutoRaise(True)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(84, 52))
            b.setText(t.title if t else "Как в системе")
            b.setProperty("key", key)
            b.setMinimumWidth(100)
            self.themes.addButton(b)
            grid.addWidget(b, i // 3, i % 3)
        self.themes.buttonClicked.connect(lambda b: self._emit(theme=b.property("key")))
        lay.addLayout(grid)
        lay.addWidget(QLabel("<b>Акцентный цвет</b>"))
        row = QHBoxLayout()
        self.accents = QButtonGroup(self)
        for color, title in ACCENTS:
            b = QToolButton()
            b.setCheckable(True)
            b.setAutoRaise(True)
            b.setIcon(swatch_icon(QColor(color), 22))
            b.setIconSize(QSize(22, 22))
            b.setToolTip(title)
            b.setProperty("color", color)
            self.accents.addButton(b)
            row.addWidget(b)
        row.addStretch()
        self.accents.buttonClicked.connect(lambda b: self._emit(accent=b.property("color")))
        lay.addLayout(row)
        lay.addWidget(QLabel("<b>Палитра проектов</b>"))
        self.palettes = QButtonGroup(self)
        pal = QGridLayout()
        pal.setSpacing(4)
        for i, (key, (title, hues, sat)) in enumerate(PALETTES.items()):
            b = QToolButton()
            b.setCheckable(True)
            b.setAutoRaise(True)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setText(title)
            b.setIcon(_palette_icon([QColor.fromHslF(h / 360, sat, 0.8) for h, _n in hues[:6]]))
            b.setIconSize(QSize(66, 12))
            b.setProperty("key", key)
            self.palettes.addButton(b)
            pal.addWidget(b, i // 2, i % 2)
        self.palettes.buttonClicked.connect(lambda b: self._emit(palette=b.property("key")))
        lay.addLayout(pal)
        self.reload(settings)

    def reload(self, settings: ViewSettings) -> None:
        self.s = replace(settings)
        for b in self.themes.buttons():
            key = b.property("key")
            t = THEMES.get(key)
            b.setIcon(theme_icon(t, settings.accent, 84, 52) if t else _system_icon(settings.accent))
            b.setChecked(key == settings.theme)
        for b in self.accents.buttons():
            b.setChecked(b.property("color").lower() == settings.accent.lower())
        for b in self.palettes.buttons():
            b.setChecked(b.property("key") == settings.palette)

    def _emit(self, **kw) -> None:
        self.s = replace(self.s, **kw)
        self.changed.emit(self.s)
        self.reload(self.s)


def _palette_icon(colors: list[QColor]) -> QIcon:
    pm = QPixmap(66, 12)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    for i, c in enumerate(colors):
        p.setPen(c.darker(125))
        p.setBrush(c)
        p.drawRoundedRect(i * 11 + 0.5, 0.5, 10, 11, 3, 3)
    p.end()
    return QIcon(pm)


def _system_icon(accent: str) -> QIcon:
    """«Как в системе»: половина светлой темы, половина тёмной."""
    light, dark = theme_icon(THEMES["light"], accent, 84, 52), theme_icon(THEMES["dark"], accent, 84, 52)
    pm = QPixmap(84, 52)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.drawPixmap(0, 0, light.pixmap(84, 52))
    p.setClipRect(42, 0, 42, 52)
    p.drawPixmap(0, 0, dark.pixmap(84, 52))
    p.end()
    return QIcon(pm)
