"""Мастер открытия таблицы: какая колонка что значит, как устроена вложенность, какие статусы. Запоминается шаблоном."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..formats import delete_template, load_templates
from ..formats import table as tb
from ..model import Dataset
from .filters import STATUS_KINDS

HIERARCHY = [("outline", "Группировка строк Excel (+/− слева)"), ("indent", "Отступы в колонке названия"),
             ("numbering", "Нумерация 1 / 1.1 / 1.1.1"), ("level", "Колонка «Уровень»"),
             ("parent", "Колонка «Родитель» (ссылка на ключ)"), ("flat", "Без вложенности — плоский список")]
DATE_ORDERS = [("dmy", "ДД.ММ.ГГГГ — как в России"), ("mdy", "ММ/ДД/ГГГГ — американский"),
               ("ymd", "ГГГГ-ММ-ДД — ISO")]
PREVIEW_ROWS = 60


def _letter(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell_text(v) -> str:
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%d.%m.%Y")
    return str(v)


class TableWizard(QDialog):
    """Показывает таблицу и догадки программы; всё можно поправить. Итог — Mapping."""

    def __init__(self, table: tb.TableData, parent=None, clipboard: bool = False):
        super().__init__(parent)
        self.table = table
        self.mapping = tb.guess_mapping(table)
        self._loading = False
        self.setWindowTitle(f"Открыть таблицу — {table.name}")
        self.resize(1180, 760)
        lay = QVBoxLayout(self)

        intro = QLabel("Программа прочитала таблицу и угадала, что в какой колонке. Проверьте и поправьте, "
                       "если нужно, — справа снизу видно, что получится.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        top = QHBoxLayout()
        self.sheet = QComboBox()
        self.sheet.addItems(table.sheets or [table.sheet or "—"])
        if table.sheet:
            self.sheet.setCurrentText(table.sheet)
        self.sheet.currentTextChanged.connect(self._sheet_changed)
        self.sheet_label = QLabel("Лист:")
        for w in (self.sheet_label, self.sheet):
            w.setVisible(len(table.sheets) > 1)
            top.addWidget(w)
        top.addWidget(QLabel("Заголовки в строке:"))
        self.header_row = QSpinBox()
        self.header_row.setRange(1, max(1, min(len(table.rows), 50)))
        self.header_row.valueChanged.connect(self._header_changed)
        top.addWidget(self.header_row)
        top.addSpacing(18)
        top.addWidget(QLabel("Шаблон:"))
        self.templates = QComboBox()
        self.templates.setMinimumWidth(220)
        self.templates.activated.connect(self._template_chosen)
        top.addWidget(self.templates)
        self.del_template = QPushButton("Удалить шаблон")
        self.del_template.clicked.connect(self._delete_template)
        top.addWidget(self.del_template)
        top.addStretch()
        lay.addLayout(top)

        split = QSplitter()
        self.preview = QTableWidget()
        self.preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview.setAlternatingRowColors(True)
        self.preview.verticalHeader().setDefaultSectionSize(22)
        split.addWidget(self.preview)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        self.cols = QTableWidget(0, 3)
        self.cols.setHorizontalHeaderLabels(["Колонка", "Пример", "Что это"])
        self.cols.verticalHeader().hide()
        self.cols.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.cols.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.cols.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.cols.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.cols.setColumnWidth(0, 150)
        self.cols.setColumnWidth(2, 190)
        rl.addWidget(self.cols, 3)

        box = QGroupBox("Вложенность и даты")
        form = QFormLayout(box)
        self.hierarchy = QComboBox()
        for key, title in HIERARCHY:
            self.hierarchy.addItem(title, key)
        self.hierarchy.currentIndexChanged.connect(self._changed)
        form.addRow("Вложенность:", self.hierarchy)
        self.levels = QLineEdit()
        self.levels.setPlaceholderText("например: Раздел, Проект, Задача")
        self.levels.textChanged.connect(self._changed)
        form.addRow("Уровни сверху вниз:", self.levels)
        self.date_order = QComboBox()
        for key, title in DATE_ORDERS:
            self.date_order.addItem(title, key)
        self.date_order.currentIndexChanged.connect(self._changed)
        form.addRow("Даты записаны как:", self.date_order)
        rl.addWidget(box)

        self.status_box = QGroupBox("Статусы")
        sl = QVBoxLayout(self.status_box)
        self.status_hint = QLabel()
        self.status_hint.setWordWrap(True)
        sl.addWidget(self.status_hint)
        self.status_table = QTableWidget(0, 2)
        self.status_table.setHorizontalHeaderLabels(["Значение в таблице", "Это значит"])
        self.status_table.verticalHeader().hide()
        self.status_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.status_table.setColumnWidth(1, 170)
        self.status_table.setMaximumHeight(170)
        sl.addWidget(self.status_table)
        rl.addWidget(self.status_box, 2)
        split.addWidget(right)
        split.setSizes([640, 540])
        lay.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.remember = QCheckBox("Запомнить как шаблон:")
        self.remember.setChecked(not clipboard)
        self.remember.setToolTip("Таблица с такими же заголовками дальше откроется сразу, без мастера")
        self.template_name = QLineEdit(Path(table.name).stem if not clipboard else "Таблица из буфера")
        self.template_name.setMaximumWidth(260)
        self.remember.toggled.connect(self.template_name.setEnabled)
        self.template_name.setEnabled(self.remember.isChecked())
        bottom.addWidget(self.remember)
        bottom.addWidget(self.template_name)
        bottom.addSpacing(16)
        self.result = QLabel()
        self.result.setWordWrap(True)
        bottom.addWidget(self.result, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Открыть")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        bottom.addWidget(self.buttons)
        lay.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._update_result)
        self._fill_templates()
        self._load_mapping()

    # ---------- заполнение ----------
    def _fill_templates(self) -> None:
        self.templates.clear()
        self.templates.addItem("— угадать заново —", None)
        for name in load_templates():
            self.templates.addItem(name, name)
        self.del_template.setEnabled(self.templates.count() > 1)

    def _headers(self) -> list:
        r = self.mapping.header_row
        row = self.table.rows[r] if r < len(self.table.rows) else []
        return list(row) + [None] * (self.table.width - len(row))

    def _load_mapping(self) -> None:
        self._loading = True
        m = self.mapping
        self.header_row.setValue(m.header_row + 1)
        self._fill_preview()
        self._fill_columns()
        self.hierarchy.setCurrentIndex(max(0, [k for k, _t in HIERARCHY].index(m.hierarchy)))
        self.levels.setText(", ".join(m.level_names))
        self.date_order.setCurrentIndex([k for k, _t in DATE_ORDERS].index(m.date_order))
        self._fill_statuses()
        self._loading = False
        self._update_result()

    def _fill_preview(self) -> None:
        t, m = self.table, self.mapping
        heads = self._headers()
        rows = t.rows[m.header_row + 1:m.header_row + 1 + PREVIEW_ROWS]
        self.preview.clear()
        self.preview.setColumnCount(t.width)
        self.preview.setRowCount(len(rows))
        titles = {c: f for f, c in m.columns.items() if f != "key" or c not in m.columns.values()}
        custom = {c: n for n, c in m.custom.items()}
        labels = []
        for i in range(t.width):
            h = _cell_text(heads[i]) or f"Колонка {_letter(i)}"
            if i in custom:
                h += f"\n→ своё поле «{custom[i]}»"
            elif i in titles:
                h += f"\n→ {tb.FIELD_TITLES[titles[i]]}"
            labels.append(h)
        self.preview.setHorizontalHeaderLabels(labels)
        self.preview.setVerticalHeaderLabels([str(m.header_row + 2 + k) for k in range(len(rows))])
        bold = QFont(self.preview.font())
        bold.setBold(True)
        for r, row in enumerate(rows):
            idx = m.header_row + 1 + r
            level = t.levels[idx] if idx < len(t.levels) else 0
            indent = t.indents[idx] if idx < len(t.indents) else 0
            for c in range(t.width):
                v = row[c] if c < len(row) else None
                text = _cell_text(v)
                if c == m.columns.get("name") and (level or indent) and m.hierarchy in ("outline", "indent"):
                    text = "    " * (level if m.hierarchy == "outline" else indent) + text
                it = QTableWidgetItem(text)
                if c in titles or c in custom:
                    it.setBackground(QColor(47, 95, 208, 22))
                self.preview.setItem(r, c, it)
        self.preview.resizeColumnsToContents()
        for c in range(t.width):
            self.preview.setColumnWidth(c, min(self.preview.columnWidth(c), 260))

    def _fill_columns(self) -> None:
        t, m = self.table, self.mapping
        heads = self._headers()
        by_col = {c: f for f, c in m.columns.items() if f != "key"}
        keys = {c for f, c in m.columns.items() if f == "key"}
        custom = {c: n for n, c in m.custom.items()}
        self.cols.setRowCount(t.width)
        for i in range(t.width):
            title = _cell_text(heads[i]) or "(без заголовка)"
            self.cols.setItem(i, 0, QTableWidgetItem(f"{_letter(i)} · {title}"))
            sample = next((_cell_text(r[i]) for r in t.rows[m.header_row + 1:] if i < len(r) and r[i] not in
                           (None, "")), "")
            self.cols.setItem(i, 1, QTableWidgetItem(sample[:60]))
            combo = QComboBox()
            combo.addItem("— не загружать —", None)
            for f, name in tb.FIELDS:
                combo.addItem(name, f)
            combo.addItem("Своё поле (как есть)", "custom")
            if i in custom:
                combo.setCurrentIndex(combo.findData("custom"))
            elif i in by_col:
                combo.setCurrentIndex(combo.findData(by_col[i]))
            elif i in keys:
                combo.setCurrentIndex(combo.findData("key"))
            combo.currentIndexChanged.connect(lambda _=0, col=i: self._column_changed(col))
            self.cols.setCellWidget(i, 2, combo)

    def _fill_statuses(self) -> None:
        m = self.mapping
        col = m.columns.get("status")
        self.status_table.setRowCount(0)
        if col is None:
            self.status_hint.setText("Колонки статуса нет — статус заданий считается по датам: есть «Факт окончания» "
                                     "— выполнено; прошёл срок — просрочено; иначе — в работе.")
            self.status_table.hide()
            return
        self.status_hint.setText("Что значит каждое значение статуса — от этого зависят цвет полос и фильтры.")
        self.status_table.show()
        values = tb.distinct(self.table, m, col)
        for v in values:
            m.status_map.setdefault(v, tb.guess_status_kind(v))
        self.status_table.setRowCount(len(values))
        for r, v in enumerate(values):
            self.status_table.setItem(r, 0, QTableWidgetItem(v))
            combo = QComboBox()
            for kind, title in STATUS_KINDS:
                combo.addItem(title, kind)
            combo.setCurrentIndex(max(0, combo.findData(m.status_map.get(v))))
            combo.currentIndexChanged.connect(lambda _=0, v=v, c=combo: self._status_changed(v, c))
            self.status_table.setCellWidget(r, 1, combo)

    # ---------- реакции ----------
    def _sheet_changed(self, name: str) -> None:
        if self._loading or not self.table.path or name == self.table.sheet:
            return
        try:
            self.table = tb.read_xlsx(self.table.path, name)
        except Exception as e:  # noqa: BLE001
            self.result.setText(f"<span style='color:#C2185B'>Лист не прочитался: {e}</span>")
            return
        self.mapping = tb.guess_mapping(self.table)
        self.header_row.setRange(1, max(1, min(len(self.table.rows), 50)))
        self._load_mapping()

    def _header_changed(self, value: int) -> None:
        if self._loading:
            return
        # другая строка заголовков — другие колонки: угадываем заново от неё
        headers = self.table.rows[value - 1] if value - 1 < len(self.table.rows) else []
        m = tb.guess_mapping(self.table)
        m.header_row = value - 1
        m.columns = tb.guess_columns(headers)
        m.signature = tb.signature(self.table, m.header_row)
        m.status_map = {}
        m.status_mode = "column" if "status" in m.columns else "dates"
        m.custom = {}
        self.mapping = m
        self._load_mapping()

    def _template_chosen(self, index: int) -> None:
        name = self.templates.itemData(index)
        if name is None:
            m = tb.guess_mapping(self.table)
        else:
            m = tb.Mapping.from_json(load_templates()[name])
            if any(c >= self.table.width for c in list(m.columns.values()) + list(m.custom.values())):
                self.result.setText("<span style='color:#C2185B'>Шаблон не подходит: в таблице меньше колонок.</span>")
                return
            self.template_name.setText(name)
            self.remember.setChecked(True)
        self.mapping = m
        self._load_mapping()

    def _delete_template(self) -> None:
        name = self.templates.currentData()
        if name:
            delete_template(name)
            self._fill_templates()

    def _column_changed(self, col: int) -> None:
        if self._loading:
            return
        m = self.mapping
        combo = self.cols.cellWidget(col, 2)
        fld = combo.currentData()
        for f, c in list(m.columns.items()):
            if c == col:
                del m.columns[f]
        for n, c in list(m.custom.items()):
            if c == col:
                del m.custom[n]
        if fld == "custom":
            name = _cell_text(self._headers()[col]) or f"Колонка {_letter(col)}"
            m.custom[name] = col
        elif fld:
            old = m.columns.get(fld)
            m.columns[fld] = col
            if old is not None and old != col:  # поле было у другой колонки — та освобождается
                other = self.cols.cellWidget(old, 2)
                self._loading = True
                other.setCurrentIndex(0)
                self._loading = False
        if fld == "status" or "status" not in m.columns:
            m.status_mode = "column" if "status" in m.columns else "dates"
            m.status_map = {}
            self._fill_statuses()
        self._loading = True
        self._fill_preview()
        self._loading = False
        self._changed()

    def _status_changed(self, value: str, combo: QComboBox) -> None:
        self.mapping.status_map[value] = combo.currentData()
        self._changed()

    def _changed(self, *_):
        if self._loading:
            return
        m = self.mapping
        m.hierarchy = self.hierarchy.currentData()
        m.level_names = [x.strip() for x in self.levels.text().split(",") if x.strip()]
        m.date_order = self.date_order.currentData()
        self._timer.start()

    def _update_result(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        m = self.mapping
        if "name" not in m.columns:
            self.result.setText("<span style='color:#C2185B'>Укажите колонку «Наименование» — без неё строк нет.</span>")
            ok.setEnabled(False)
            return
        try:
            doc, warnings = tb.convert(self.table, self._final_mapping())
            ds = Dataset(doc)
        except Exception as e:  # noqa: BLE001 — показываем, что не так, и ждём правки
            self.result.setText(f"<span style='color:#C2185B'>{e}</span>")
            ok.setEnabled(False)
            return
        c = ds.counts()
        names = {x["order"]: x["name"] for x in doc["dictionaries"]["levels"]}
        parts = []
        for order in sorted(names):
            n = sum(1 for node in ds.nodes.values() if ds.level_order(node) == order)
            parts.append(f"{names[order].lower()}: {n}")
        text = "Получится — " + " · ".join(parts)
        if warnings:
            text += f" · <span style='color:#B26A00'>замечаний: {len(warnings)}</span>"
            self.result.setToolTip("\n".join(warnings[:30]))
        else:
            self.result.setToolTip("")
        self.result.setText(text if sum(c.values()) else "Строк не найдено")
        ok.setEnabled(bool(sum(c.values())))

    def _final_mapping(self) -> tb.Mapping:
        m = self.mapping
        m.sheet = self.table.sheet
        m.signature = tb.signature(self.table, m.header_row)
        m.template = self.template_name.text().strip() if self.remember.isChecked() else ""
        if m.status_mode == "column" and "status" not in m.columns:
            m.status_mode = "dates"
        return m

    def result_mapping(self) -> tuple[tb.TableData, tb.Mapping, bool]:
        """Таблица (лист мог смениться), сопоставление и «запомнить шаблон»."""
        m = self._final_mapping()
        return self.table, m, bool(self.remember.isChecked() and m.template)
