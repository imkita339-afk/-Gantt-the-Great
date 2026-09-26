"""Панель изменений, отчёт об открытии, разбор расхождений, выбор «кто я»."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QListWidget, QListWidgetItem, QPushButton, QRadioButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ..exchange import FIELD_TITLES
from ..model import DATE_FIELDS, Dataset, fmt


def value_text(ds: Dataset, fld: str, v) -> str:
    if v is None:
        return "—"
    if fld in DATE_FIELDS:
        return fmt(v, False)
    if fld == "status":
        return ds.statuses.get(v, {}).get("name", str(v))
    if fld == "color":
        from PySide6.QtGui import QColor

        from .theme import color_name
        return color_name(QColor(v))
    return str(v)


class ChangesPanel(QWidget):
    """Все локальные правки: «было → стало», вернуть по одной или все, выгрузить."""

    revert = Signal(object, str)     # узел, поле
    revert_all = Signal()
    save_list = Signal()
    save_as = Signal()
    send = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: gray;")
        lay.addWidget(self.hint)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setSpacing(2)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.btn_revert = QPushButton("Вернуть выбранное")
        self.btn_revert.clicked.connect(self._revert_selected)
        self.btn_all = QPushButton("Вернуть все")
        self.btn_all.clicked.connect(self.revert_all)
        row.addWidget(self.btn_revert)
        row.addWidget(self.btn_all)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        b1 = QPushButton("Лист изменений…")
        b1.clicked.connect(self.save_list)
        b2 = QPushButton("Сохранить как…")
        b2.setObjectName("primary")
        b2.clicked.connect(self.save_as)
        row2.addWidget(b1)
        row2.addWidget(b2)
        lay.addLayout(row2)
        self.btn_send = QPushButton("Отправить в источник…")
        self.btn_send.setToolTip("Записать правки на сервер — после «Подтвердить»")
        self.btn_send.clicked.connect(self.send)
        self.btn_send.hide()
        lay.addWidget(self.btn_send)

    def set_can_send(self, on: bool) -> None:
        self.btn_send.setVisible(on)

    def refresh(self, ds: Dataset | None) -> None:
        self.list.clear()
        if ds is None:
            return
        n = 0
        for (nid, fld), val in sorted(ds.overrides.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            node = ds.nodes.get(nid)
            if node is None:
                continue
            n += 1
            it = QListWidgetItem(f"{node.name}\n{FIELD_TITLES.get(fld, fld)}:  "
                                 f"{value_text(ds, fld, node.source(fld))}  →  {value_text(ds, fld, val)}")
            it.setData(Qt.ItemDataRole.UserRole, (node, fld))
            self.list.addItem(it)
        self.hint.setText("Правки хранятся на этом ПК и переживают перезапуск и новые выгрузки. "
                          "Без сервера их можно сохранить в файл или выгрузить листом изменений."
                          if n else "Правок нет. Потяните полосу мышью или дважды щёлкните по ней, чтобы изменить.")
        self.btn_revert.setEnabled(bool(n))
        self.btn_all.setEnabled(bool(n))

    def _revert_selected(self) -> None:
        for it in self.list.selectedItems():
            node, fld = it.data(Qt.ItemDataRole.UserRole)
            self.revert.emit(node, fld)


class ImportReportDialog(QDialog):
    def __init__(self, ds: Dataset, source_label: str, file_name: str, warnings: list[str], conflicts: int,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Отчёт об открытии")
        self.setMinimumWidth(520)
        self.resolve = False
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b style='font-size:13pt'>Открыто</b><br><span style='color:gray'>{file_name} · "
                             f"{source_label}</span>"))
        c = ds.counts()
        grid = QGridLayout()
        titles = {"group": "разделов", "project": "проектов", "task": "заданий"}
        for i, key in enumerate(("group", "project", "task")):
            num = QLabel(f"<span style='font-size:20pt;font-weight:600'>{c[key]}</span>")
            grid.addWidget(num, 0, i, Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(QLabel(titles[key]), 1, i, Qt.AlignmentFlag.AlignCenter)
        lay.addLayout(grid)
        if warnings:
            lay.addWidget(QLabel(f"<b>Предупреждения: {len(warnings)}</b> — данные открыты, но стоит знать:"))
            lst = QListWidget()
            lst.setWordWrap(True)
            for w in warnings:
                lst.addItem(w)
            lst.setMaximumHeight(200)
            lay.addWidget(lst)
        else:
            lay.addWidget(QLabel("Замечаний нет."))
        if ds.edit_count:
            lay.addWidget(QLabel(f"Ваших правок сохранено: {ds.edit_count}."))
        buttons = QDialogButtonBox()
        if conflicts:
            lay.addWidget(QLabel(f"<b>Правки, которые расходятся с новыми данными: {conflicts}</b>"))
            b = buttons.addButton("Разобрать расхождения", QDialogButtonBox.ButtonRole.ActionRole)
            b.setObjectName("primary")
            b.clicked.connect(self._resolve)
        buttons.addButton("Готово", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)

    def _resolve(self) -> None:
        self.resolve = True
        self.accept()


class ConflictsDialog(QDialog):
    """Источник изменился после правки: для каждой — «оставить моё» или «взять из файла»."""

    def __init__(self, ds: Dataset, conflicts: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Разбор расхождений")
        self.setMinimumSize(640, 420)
        self.conflicts = conflicts
        self.groups: list[QButtonGroup] = []
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b style='font-size:12pt'>Данные обновились: {len(conflicts)} "
                             f"правок расходятся с новыми значениями</b>"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        bl = QVBoxLayout(body)
        for c in conflicts:
            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            fl = QVBoxLayout(frame)
            fld = c["field"]
            fl.addWidget(QLabel(f"<b>{c['name']}</b> — {FIELD_TITLES.get(fld, fld)}"))
            g = QButtonGroup(self)
            if c["deleted"]:
                fl.addWidget(QLabel("Узла нет в новых данных — удалён или переименован в источнике."))
                a, b = QRadioButton("Убрать правку"), QRadioButton("Оставить до следующей загрузки")
            else:
                fl.addWidget(QLabel(f"Было при правке: {value_text(ds, fld, c['old_source'])}   ·   "
                                    f"Ваша правка: <b>{value_text(ds, fld, c['mine'])}</b>   ·   "
                                    f"Сейчас в источнике: <b>{value_text(ds, fld, c['new_source'])}</b>"))
                a, b = QRadioButton("Оставить моё"), QRadioButton("Взять из источника")
            a.setChecked(True)
            g.addButton(a, 0)
            g.addButton(b, 1)
            row = QHBoxLayout()
            row.addWidget(a)
            row.addWidget(b)
            row.addStretch()
            fl.addLayout(row)
            bl.addWidget(frame)
            self.groups.append(g)
        bl.addStretch()
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        buttons = QDialogButtonBox()
        later = buttons.addButton("Решить позже", QDialogButtonBox.ButtonRole.RejectRole)
        ok = buttons.addButton("Применить", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        _ = later

    def decisions(self) -> list[tuple[dict, bool]]:
        """(расхождение, взять ли значение источника / убрать правку)."""
        return [(c, g.checkedId() == (1 if not c["deleted"] else 0)) for c, g in zip(self.conflicts, self.groups)]


class PersonDialog(QDialog):
    """«Кто я» — для режима «Исполнитель»."""

    def __init__(self, ds: Dataset, current: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Кто я")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Режим «Исполнитель» покажет ваши задания и их проекты. Выберите себя:"))
        self.combo = QComboBox()
        self.combo.setEditable(True)
        people = sorted(ds.people.values(), key=lambda p: p["name"])
        for p in people:
            self.combo.addItem(p["name"], p["id"])
        idx = next((i for i, p in enumerate(people) if current in (p["name"], p.get("fullName"))), -1)
        if idx >= 0:
            self.combo.setCurrentIndex(idx)
        lay.addWidget(self.combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def person(self) -> tuple[str | None, str]:
        return self.combo.currentData(), self.combo.currentText()
