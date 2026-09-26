"""Пришедшие документы обмена: пакет правок (changes) и ответ источника на правки (results)."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHeaderView, QLabel, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout)

from ..exchange import FIELD_TITLES
from ..model import DATE_FIELDS, EDIT_FIELDS, Dataset, Node
from .panels import value_text


def decode(fld: str, v):
    if fld in DATE_FIELDS:
        return date.fromisoformat(v) if isinstance(v, str) and v else None
    return v


def encode(fld: str, v):
    if fld in DATE_FIELDS:
        return v.isoformat() if v else None
    return v


class ChangesImportDialog(QDialog):
    """Правки из файла: список «было → станет»; в очередь попадают только отмеченные."""

    def __init__(self, ds: Dataset, doc: dict, parent=None):
        super().__init__(parent)
        self.ds = ds
        self.setWindowTitle("Правки из файла")
        self.resize(900, 520)
        lay = QVBoxLayout(self)
        meta = doc["meta"]
        same = f"{meta['source']}|{meta.get('sourceName') or ''}" == ds.key
        who = f" · автор: {meta['exportedBy']}" if meta.get("exportedBy") else ""
        head = QLabel(f"<b>Правок в файле: {len(doc['changes'])}</b>{who}<br>" +
                      ("Относятся к открытым данным." if same else
                       f"<span style='color:#B26A00'>Сделаны для других данных: «{meta.get('sourceName') or meta['source']}». "
                       "Применятся только к узлам с теми же id.</span>"))
        head.setWordWrap(True)
        lay.addWidget(head)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Что", "Поле", "Сейчас", "Станет", "Примечание"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i, w in ((1, 130), (2, 110), (3, 110), (4, 200)):
            self.tree.setColumnWidth(i, w)
        lay.addWidget(self.tree, 1)
        self.items: list[tuple[QTreeWidgetItem, Node | None, str, object]] = []
        for c in doc["changes"]:
            node, fld = ds.nodes.get(c["nodeId"]), c["field"]
            new = decode(fld, c.get("newValue"))
            note, ok = "", True
            if node is None:
                note, ok = "нет в открытых данных", False
            elif fld not in EDIT_FIELDS:
                note, ok = "это поле программа не меняет", False
            elif not ds.can_edit(node, fld):
                note, ok = "источник не разрешает менять это поле", False
            elif ds.get(node, fld) == new:
                note, ok = "уже так", False
            elif c.get("oldValue") is not None and decode(fld, c["oldValue"]) != node.source(fld):
                note = "в источнике уже другое значение"
            name = node.name if node else c["nodeId"]
            it = QTreeWidgetItem([name, FIELD_TITLES.get(fld, fld),
                                  value_text(ds, fld, ds.get(node, fld)) if node else "—",
                                  value_text(ds, fld, new), (c.get("comment") or note or "")])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(0, Qt.CheckState.Checked if ok else Qt.CheckState.Unchecked)
            if not ok:
                it.setDisabled(True)
            if note and ok:
                it.setToolTip(4, note)
            self.tree.addTopLevelItem(it)
            self.items.append((it, node, fld, new))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Добавить в мои правки")
        bb.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def chosen(self) -> list[tuple[Node, str, object]]:
        return [(node, fld, new) for it, node, fld, new in self.items
                if node is not None and not it.isDisabled() and it.checkState(0) == Qt.CheckState.Checked]


RESULT_TITLES = {"applied": "Записано в источник", "conflict": "Конфликт", "rejected": "Отказ"}


class ResultsDialog(QDialog):
    """Ответ источника: записанное уходит из очереди, конфликты и отказы — на выбор."""

    def __init__(self, ds: Dataset, doc: dict, parent=None):
        super().__init__(parent)
        self.ds = ds
        self.setWindowTitle("Ответ источника на правки")
        self.resize(960, 520)
        lay = QVBoxLayout(self)
        by_id = {}
        for (nid, fld), val in ds.overrides.items():
            by_id[ds.change_id(nid, fld, encode(fld, val))] = (ds.nodes.get(nid), fld)
        counts = {"applied": 0, "conflict": 0, "rejected": 0, "unknown": 0}
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Что", "Поле", "Моё значение", "Ответ", "Что сделать"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i, w in ((1, 120), (2, 110), (3, 250), (4, 230)):
            self.tree.setColumnWidth(i, w)
        self.rows: list[tuple[dict, Node, str, QComboBox | None]] = []
        for r in doc["results"]:
            hit = by_id.get(r["changeId"])
            if hit is None or hit[0] is None:
                counts["unknown"] += 1
                continue
            node, fld = hit
            res = r["result"]
            counts[res] += 1
            answer = RESULT_TITLES[res]
            if res == "conflict":
                answer += f": в источнике {value_text(ds, fld, decode(fld, r.get('currentValue')))}"
            if r.get("reason"):
                answer += f" — {r['reason']}"
            it = QTreeWidgetItem([node.name, FIELD_TITLES.get(fld, fld), value_text(ds, fld, ds.get(node, fld)),
                                  answer, ""])
            it.setToolTip(3, answer)
            self.tree.addTopLevelItem(it)
            combo = None
            if res == "applied":
                it.setText(4, "Убрать из очереди")
            else:
                combo = QComboBox()
                if res == "conflict":
                    combo.addItem("Взять из источника", "source")
                    combo.addItem("Оставить моё — отправить снова", "mine")
                else:
                    combo.addItem("Вернуть как в источнике", "source")
                    combo.addItem("Оставить правку у себя", "mine")
                self.tree.setItemWidget(it, 4, combo)
            self.rows.append((r, node, fld, combo))
        parts = [f"записано: {counts['applied']}", f"конфликтов: {counts['conflict']}",
                 f"отказов: {counts['rejected']}"]
        if counts["unknown"]:
            parts.append(f"не найдено в очереди: {counts['unknown']} (правку уже изменили или вернули)")
        head = QLabel("<b>Ответ по правкам</b> — " + " · ".join(parts))
        head.setWordWrap(True)
        lay.addWidget(head)
        lay.addWidget(self.tree, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Применить")
        bb.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.matched = len(self.rows)
        self.needs_choice = any(combo is not None for *_x, combo in self.rows)

    def decisions(self) -> list[tuple[Node, str, str, object]]:
        """(узел, поле, действие, новое значение источника): action — applied | source | mine."""
        out = []
        for r, node, fld, combo in self.rows:
            if r["result"] == "applied":
                src = self.ds.overrides.get((node.id, fld))
                out.append((node, fld, "applied", src))
            else:
                current = decode(fld, r.get("currentValue")) if r["result"] == "conflict" else node.source(fld)
                out.append((node, fld, combo.currentData(), current))
        return out
