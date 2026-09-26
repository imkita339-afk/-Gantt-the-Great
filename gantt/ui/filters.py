"""Панель фильтров: условия комбинируются, наборы сохраняются под именем."""
from __future__ import annotations

import json
from datetime import date, timedelta

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QSizePolicy, QStyledItemDelegate, QToolButton,
                               QVBoxLayout, QWidget)

from ..model import Dataset
from .dialogs import DateField
from .tree_model import Criteria

STATUS_KINDS = [("inProgress", "В работе"), ("overdue", "Просрочено"), ("done", "Выполнено"),
                ("notStarted", "Не начато"), ("onHold", "Приостановлено"), ("release", "В релизе"),
                ("cancelled", "Отменено")]


class _Elide(QStyledItemDelegate):
    """Строка списка — во всю ширину, длинный текст с многоточием: никакой прокрутки вбок."""

    def sizeHint(self, option, index):
        return QSize(10, super().sizeHint(option, index).height() + 6)   # строки не слипаются


class _List(QListWidget):
    """Список с флажками: высота — по числу строк (не больше 8), вбок не прокручивается."""

    def __init__(self, rows: int = 8):
        super().__init__()
        self.rows = rows
        self.setItemDelegate(_Elide(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        # не выше своих строк (пустоты не будет), но может сжаться до трёх строк, если окно низкое
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def _row_h(self) -> int:
        return self.sizeHintForRow(0) if self.count() else self.fontMetrics().height() + 6

    def sizeHint(self) -> QSize:
        return QSize(250, self._row_h() * max(1, min(self.rows, self.count())) + 4)

    def minimumSizeHint(self) -> QSize:
        return QSize(160, self._row_h() * max(1, min(3, self.count())) + 4)


class CheckList(QWidget):
    """Заголовок + список с флажками. Все отмечены (или ни одного) — фильтр не действует."""

    changed = Signal()

    def __init__(self, title: str, searchable: bool = False, rows: int = 6):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        head = QHBoxLayout()
        self.title = QLabel(f"<b>{title}</b>")
        head.addWidget(self.title)
        head.addStretch()
        self.all_btn = QToolButton()
        self.all_btn.setText("все")
        self.all_btn.setAutoRaise(True)
        self.all_btn.clicked.connect(lambda: self.set_all(True))
        self.none_btn = QToolButton()
        self.none_btn.setText("ни одного")
        self.none_btn.setAutoRaise(True)
        self.none_btn.clicked.connect(lambda: self.set_all(False))
        head.addWidget(self.all_btn)
        head.addWidget(self.none_btn)
        lay.addLayout(head)
        self.search = None
        if searchable:
            self.search = QLineEdit()
            self.search.setPlaceholderText("Найти…")
            self.search.setClearButtonEnabled(True)
            self.search.textChanged.connect(self._filter)
            lay.addWidget(self.search)
        self.list = _List(rows)
        self.list.itemChanged.connect(lambda _it: self.changed.emit())
        lay.addWidget(self.list)
        self.available = False

    def fill(self, items: list[tuple[str, str]], counts: dict[str, int] | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for key, text in items:
            n = (counts or {}).get(key)
            it = QListWidgetItem(f"{text}   {n}" if n else text)
            it.setToolTip(text)
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.available = bool(items)
        self.setVisible(self.available)
        self.list.updateGeometry()

    def set_all(self, on: bool) -> None:
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.list.blockSignals(False)
        self.changed.emit()

    def _filter(self, text: str) -> None:
        t = text.lower().strip()
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(bool(t) and t not in it.text().lower())

    def selected(self) -> set | None:
        keys = [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]
        if len(keys) == self.list.count():
            return None
        return set(keys)

    def set_selected(self, keys: set | None) -> None:
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            it = self.list.item(i)
            on = keys is None or it.data(Qt.ItemDataRole.UserRole) in keys
            it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.list.blockSignals(False)


class FilterPanel(QWidget):
    changed = Signal()

    def __init__(self, qs, parent=None):
        super().__init__(parent)
        self.qs = qs
        self._loading = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(10)

        sets = QHBoxLayout()
        self.sets = QComboBox()
        self.sets.setToolTip("Сохранённые наборы фильтров")
        self.sets.activated.connect(self._apply_set)
        self.sets.setMinimumWidth(170)
        save = QToolButton()
        save.setText("Сохранить…")
        save.clicked.connect(self._save_set)
        delete = QToolButton()
        delete.setText("Удалить")
        delete.clicked.connect(self._delete_set)
        reset = QPushButton("Сбросить все фильтры")
        reset.clicked.connect(self.reset)
        sets.addWidget(self.sets, 1)
        sets.addWidget(save)
        sets.addWidget(delete)
        sets.addSpacing(12)
        sets.addWidget(reset)
        outer.addLayout(sets)

        cols = QHBoxLayout()
        cols.setSpacing(18)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(10)
        right.setSpacing(10)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        outer.addLayout(cols, 1)

        flags = QVBoxLayout()
        flags.setSpacing(4)
        self.only_overdue = QCheckBox("Только просроченные")
        self.hide_done = QCheckBox("Скрыть выполненные")
        self.only_edited = QCheckBox("Только мои правки")
        self.hide_empty = QCheckBox("Скрыть пустые проекты")
        for cb in (self.only_overdue, self.hide_done, self.only_edited, self.hide_empty):
            cb.toggled.connect(self._emit)
            flags.addWidget(cb)
        left.addLayout(flags)

        self.sections = CheckList("Разделы", rows=8)
        self.statuses = CheckList("Статус", rows=7)
        self.executors = CheckList("Исполнители", searchable=True, rows=10)
        self.priorities = CheckList("Приоритет", rows=5)
        self.urgencies = CheckList("Срочность", rows=5)
        self.impacts = CheckList("Влияние", rows=5)
        self.initiators = CheckList("Инициатор", searchable=True, rows=6)
        for w in (self.statuses, self.priorities, self.urgencies, self.impacts):
            left.addWidget(w)
        for w in (self.sections, self.executors, self.initiators):
            right.addWidget(w)
        for w in (self.sections, self.statuses, self.executors, self.priorities, self.urgencies, self.impacts,
                  self.initiators):
            w.changed.connect(self._emit)

        per = QVBoxLayout()
        per.setSpacing(4)
        self.use_period = QCheckBox("Период — полоса пересекает даты")
        self.use_period.toggled.connect(self._emit)
        per.addWidget(self.use_period)
        row = QHBoxLayout()
        today = date.today()
        self.p_from = DateField(today.replace(day=1))
        self.p_to = DateField(today.replace(day=1) + timedelta(days=92))
        for f in (self.p_from, self.p_to):
            f.check.hide()
            f.edit.dateChanged.connect(self._emit)
        row.addWidget(QLabel("с"))
        row.addWidget(self.p_from, 1)
        row.addWidget(QLabel("по"))
        row.addWidget(self.p_to, 1)
        per.addLayout(row)
        left.addLayout(per)
        left.addStretch(1)
        right.addStretch(1)
        self._refresh_sets()

    # ---------- данные ----------
    def load_dataset(self, ds: Dataset) -> None:
        self._loading = True
        self.sections.fill([(n.id, n.name) for n in ds.roots if ds.role(n) == "group"] or [])
        kinds = {}
        for n in ds.nodes.values():
            if ds.role(n) == "task":
                k = ds.status_kind_for_color(n)
                kinds[k] = kinds.get(k, 0) + 1
        self.statuses.fill([(k, t) for k, t in STATUS_KINDS if k in kinds], kinds)
        used = {}
        for n in ds.nodes.values():
            for pid in n.raw.get("executors") or []:
                used[pid] = used.get(pid, 0) + 1
        people = sorted(((pid, ds.person_name(pid)) for pid in used), key=lambda x: x[1])
        self.executors.fill(people, used)
        for w, fld in ((self.priorities, "priority"), (self.urgencies, "urgency"), (self.impacts, "impact")):
            items = sorted(ds.scales[fld].values(), key=lambda x: x.get("order", 0))
            w.fill([(x["id"], x["name"]) for x in items])
        inits = sorted({n.raw.get("initiator") for n in ds.nodes.values() if n.raw.get("initiator")})
        self.initiators.fill([(pid, ds.person_name(pid)) for pid in inits])
        self._loading = False

    def criteria(self) -> Criteria:
        c = Criteria(hide_done=self.hide_done.isChecked(), only_overdue=self.only_overdue.isChecked(),
                     only_edited=self.only_edited.isChecked(), hide_empty=self.hide_empty.isChecked(),
                     sections=self.sections.selected() if self.sections.available else None,
                     statuses=self.statuses.selected() if self.statuses.available else None,
                     executors=self.executors.selected() if self.executors.available else None,
                     priorities=self.priorities.selected() if self.priorities.available else None,
                     urgencies=self.urgencies.selected() if self.urgencies.available else None,
                     impacts=self.impacts.selected() if self.impacts.available else None,
                     initiators=self.initiators.selected() if self.initiators.available else None)
        if self.use_period.isChecked():
            a, b = self.p_from.value(), self.p_to.value()
            if a and b:
                c.period = (min(a, b), max(a, b))
        return c

    def set_criteria(self, c: Criteria) -> None:
        self._loading = True
        self.hide_done.setChecked(c.hide_done)
        self.only_overdue.setChecked(c.only_overdue)
        self.only_edited.setChecked(c.only_edited)
        self.hide_empty.setChecked(c.hide_empty)
        for w, keys in ((self.sections, c.sections), (self.statuses, c.statuses), (self.executors, c.executors),
                        (self.priorities, c.priorities), (self.urgencies, c.urgencies), (self.impacts, c.impacts),
                        (self.initiators, c.initiators)):
            w.set_selected(keys)
        self.use_period.setChecked(bool(c.period))
        if c.period:
            self.p_from.set_value(c.period[0])
            self.p_to.set_value(c.period[1])
        self._loading = False
        self.changed.emit()

    def reset(self) -> None:
        self.set_criteria(Criteria())

    def _emit(self, *_):
        if not self._loading:
            QTimer.singleShot(0, self.changed.emit)

    # ---------- наборы ----------
    def _load_sets(self) -> dict:
        try:
            return json.loads(self.qs.value("filters/sets", "{}"))
        except (TypeError, ValueError):
            return {}

    def _refresh_sets(self) -> None:
        self.sets.clear()
        self.sets.addItem("Наборы фильтров…")
        for name in sorted(self._load_sets()):
            self.sets.addItem(name)

    def _save_set(self) -> None:
        name, ok = QInputDialog.getText(self, "Сохранить набор фильтров", "Название набора:")
        if not ok or not name.strip():
            return
        sets = self._load_sets()
        sets[name.strip()] = self.criteria().to_json()
        self.qs.setValue("filters/sets", json.dumps(sets, ensure_ascii=False))
        self._refresh_sets()
        self.sets.setCurrentText(name.strip())

    def _apply_set(self, i: int) -> None:
        if i <= 0:
            return
        data = self._load_sets().get(self.sets.itemText(i))
        if data:
            self.set_criteria(Criteria.from_json(data))

    def _delete_set(self) -> None:
        name = self.sets.currentText()
        sets = self._load_sets()
        if name in sets:
            del sets[name]
            self.qs.setValue("filters/sets", json.dumps(sets, ensure_ascii=False))
            self._refresh_sets()
