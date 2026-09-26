"""Модель дерева для обеих половин окна: таблицы слева и шкалы справа. Фильтры и сортировка — в прокси."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

from ..model import Dataset, Node, fmt

COLUMNS = [("name", "Наименование"), ("number", "№"), ("executors", "Исполнители"), ("status", "Статус"),
           ("progress", "Прогресс"), ("due", "Срок"), ("start", "Начало"), ("end", "Окончание"), ("fact_start", "Факт начала"),
           ("fact_end", "Факт окончания"), ("priority", "Приоритет"), ("urgency", "Срочность"),
           ("impact", "Влияние"), ("initiator", "Инициатор"), ("custom", "Доп. поля"), ("timeline", "")]
COL = {key: i for i, (key, _t) in enumerate(COLUMNS)}
TIMELINE_COL = COL["timeline"]
NodeRole = Qt.ItemDataRole.UserRole + 1
SortRole = Qt.ItemDataRole.UserRole + 2


class GanttModel(QAbstractItemModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ds: Dataset | None = None
        self.colors = None          # функция node -> QColor | None (цвет проекта), задаёт окно
        self.section_bg = QColor("#F0EEE8")
        self._chips: dict[str, QPixmap] = {}
        self._texts: dict[tuple[str, str], str] = {}

    def set_dataset(self, ds: Dataset | None) -> None:
        self.beginResetModel()
        self.ds = ds
        self._texts.clear()
        self.endResetModel()

    def cell_text(self, node: Node, key: str) -> str:
        """Текст ячейки — считается один раз, пока данные не поменялись."""
        k = (node.id, key)
        t = self._texts.get(k)
        if t is None:
            t = self._texts[k] = self.text(node, key)
        return t

    # ---------- структура ----------
    def _children(self, parent: QModelIndex) -> list[Node]:
        if self.ds is None:
            return []
        if not parent.isValid():
            return self.ds.roots
        return parent.internalPointer().children

    def index(self, row, column, parent=QModelIndex()):
        children = self._children(parent)
        if 0 <= row < len(children) and 0 <= column < len(COLUMNS):
            return self.createIndex(row, column, children[row])
        return QModelIndex()

    def parent(self, index=QModelIndex()):
        if not index.isValid():
            return QModelIndex()
        p = index.internalPointer().parent
        return self.createIndex(p.row, 0, p) if p is not None else QModelIndex()

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid() and parent.column() > 0:
            return 0
        return len(self._children(parent))

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def index_for(self, node: Node, column: int = 0) -> QModelIndex:
        return self.createIndex(node.row, column, node)

    def node_changed(self, node: Node) -> None:
        """Правка узла меняет и сводные значения предков — обновляем всю цепочку."""
        self._texts.clear()
        n = node
        while n is not None:
            self.dataChanged.emit(self.index_for(n, 0), self.index_for(n, len(COLUMNS) - 1))
            n = n.parent

    def refresh_all(self) -> None:
        self._texts.clear()
        if self.ds is not None and self.ds.roots:
            self.layoutAboutToBeChanged.emit()
            self.layoutChanged.emit()

    # ---------- данные ----------
    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section][1]
        return None

    def flags(self, index):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable if index.isValid() else Qt.ItemFlag.NoItemFlags

    def _chip(self, color: QColor) -> QPixmap:
        key = color.name()
        if key not in self._chips:
            pm = QPixmap(12, 12)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(color.darker(125))
            p.setBrush(color)
            p.drawRoundedRect(0.5, 0.5, 11, 11, 3, 3)
            p.end()
            self._chips[key] = pm
        return self._chips[key]

    def text(self, node: Node, key: str) -> str:
        ds = self.ds
        if key == "name":
            return node.name
        if key == "number":
            return ds.number_label(node)
        if key == "executors":
            return ds.executors_label(node)
        if key == "status":
            return ds.status_label(node)
        if key == "progress":
            return ds.progress_label(node)
        if key == "due":
            return ds.due_info(node)[2]
        if key in ("start", "end"):
            s, e, _ = ds.span(node)
            return fmt(s if key == "start" else e) if s else ""
        if key in ("fact_start", "fact_end"):
            v = ds.get(node, "factStart" if key == "fact_start" else "factEnd")
            return fmt(v) if v else ""
        if key in ("priority", "urgency", "impact"):
            item = ds.scale_item(node, key)
            return item["name"] if item else ""
        if key == "initiator":
            return ds.person_name(node.raw.get("initiator")) if node.raw.get("initiator") else ""
        if key == "custom":
            return ds.custom_label(node)
        return ""

    def sort_value(self, node: Node, key: str):
        ds = self.ds
        if key in ("start", "end"):
            s, e, _ = ds.span(node)
            v = s if key == "start" else e
            return v or date.max
        if key in ("fact_start", "fact_end"):
            return ds.get(node, "factStart" if key == "fact_start" else "factEnd") or date.max
        if key == "progress":
            return ds.percent(node) if ds.percent(node) is not None else -1
        if key == "due":
            _kind, days, _t = ds.due_info(node)
            return days if days is not None else 10 ** 6
        if key == "number":
            t = ds.number_label(node)
            return (0, int(t), "") if t.isdigit() else (1, 0, t.casefold())
        if key in ("priority", "urgency", "impact"):
            item = ds.scale_item(node, key)
            return item.get("order", 0) if item else -1
        if key == "timeline":
            s, _e, _ = ds.span(node)
            return s or date.max
        return self.text(node, key).casefold()

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self.ds is None:
            return None
        node: Node = index.internalPointer()
        ds, key = self.ds, COLUMNS[index.column()][0]
        role_kind = ds.role(node)
        if role == NodeRole:
            return node
        if role == Qt.ItemDataRole.DisplayRole:
            return self.text(node, key) if key != "timeline" else None
        if role == SortRole:
            return self.sort_value(node, key)
        if role == Qt.ItemDataRole.DecorationRole and key == "name" and self.colors:
            c = self.colors(node)
            return self._chip(c) if c is not None else None
        if role == Qt.ItemDataRole.FontRole and key == "name" and role_kind != "task":
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ItemDataRole.BackgroundRole and role_kind == "group":
            return self.section_bg
        if role == Qt.ItemDataRole.TextAlignmentRole and key in ("number", "start", "end", "fact_start", "fact_end"):
            return int(Qt.AlignmentFlag.AlignCenter)
        return None


@dataclass
class Criteria:
    """Условия фильтра. None у множества — «любое значение»."""

    text: str = ""
    hide_done: bool = False
    only_overdue: bool = False
    only_edited: bool = False
    hide_empty: bool = False
    sections: set | None = None       # id узлов верхнего уровня
    executors: set | None = None      # id людей
    statuses: set | None = None       # вид статуса: inProgress, done, overdue…
    priorities: set | None = None
    urgencies: set | None = None
    impacts: set | None = None
    initiators: set | None = None
    period: tuple | None = None       # (с, по) — полоса пересекает период
    me: str | None = None             # режим «Исполнитель»: мои задания и их проекты

    def active(self) -> bool:
        return any([self.text, self.hide_done, self.only_overdue, self.only_edited, self.hide_empty, self.me,
                    self.period] + [x is not None for x in (self.sections, self.executors, self.statuses,
                                                            self.priorities, self.urgencies, self.impacts,
                                                            self.initiators)])

    def to_json(self) -> dict:
        d = {}
        for k, v in self.__dict__.items():
            if isinstance(v, set):
                v = sorted(v)
            elif isinstance(v, tuple):
                v = [x.isoformat() for x in v]
            d[k] = v
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Criteria":
        c = cls()
        for k, v in d.items():
            if not hasattr(c, k):
                continue
            if k == "period" and v:
                v = (date.fromisoformat(v[0]), date.fromisoformat(v[1]))
            elif isinstance(getattr(cls, k, None), type(None)) and isinstance(v, list) and k != "period":
                v = set(v)
            setattr(c, k, v)
        return c


class FilterProxy(QSortFilterProxyModel):
    """Фильтры (поиск, панель условий, режим «Исполнитель») и сортировка внутри уровня."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.c = Criteria()
        self.accepted: set[str] | None = None   # id видимых узлов; None — фильтра нет
        self.setSortRole(SortRole)

    def set_criteria(self, c: Criteria) -> None:
        """Видимые узлы считаются один раз обходом дерева, а не отдельным вопросом про каждую строку."""
        self.c = c
        self.accepted = self._compute(c)
        # одной сменой раскладки, а не сотнями «строки убраны/добавлены» — виды перестраиваются один раз
        self.invalidate()

    def _compute(self, c: Criteria) -> set[str] | None:
        model: GanttModel = self.sourceModel()
        ds = model.ds if model is not None else None
        if ds is None or not c.active():
            return None
        keep: set[str] = set()

        def take_subtree(n: Node) -> None:
            keep.add(n.id)
            for ch in n.children:
                take_subtree(ch)

        def walk(n: Node) -> bool:
            """True — узел или кто-то внутри него виден; родители видимых видны всегда."""
            if self._passes(ds, n):
                if c.text and ds.role(n) != "group":
                    take_subtree(n)          # нашли проект по имени — видно и всё внутри
                    return True
                keep.add(n.id)
                for ch in n.children:
                    walk(ch)
                return True
            found = False
            for ch in n.children:
                found = walk(ch) or found
            if found:
                keep.add(n.id)
            return found

        for r in ds.roots:
            walk(r)
        return keep

    def lessThan(self, left, right) -> bool:
        a, b = left.data(SortRole), right.data(SortRole)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)

    def _passes(self, ds: Dataset, node: Node) -> bool:
        c = self.c
        role = ds.role(node)
        if c.sections is not None and ds.top(node).id not in c.sections:
            return False
        if role == "group":
            return not c.active() or (c.sections is not None and not any(
                [c.text, c.hide_done, c.only_overdue, c.only_edited, c.hide_empty, c.me, c.period,
                 c.executors, c.statuses, c.priorities, c.urgencies, c.impacts, c.initiators]))
        if c.text:
            hay = " ".join([node.name, node.number or "", ", ".join(ds.executors_of(node))]).lower()
            if c.text not in hay:
                return False
        if c.hide_done and ds.is_done(node):
            return False
        if c.only_overdue and not ds.is_overdue(node):
            return False
        if c.only_edited and not ds.is_edited(node):
            return False
        if c.hide_empty and ds.is_empty(node):
            return False
        if c.me is not None and c.me not in ds.executor_ids(node):
            return False
        if c.executors is not None and not (ds.executor_ids(node) & c.executors):
            return False
        if c.statuses is not None and ds.status_kind_for_color(node) not in c.statuses:
            return False
        for fld, allowed in (("priority", c.priorities), ("urgency", c.urgencies), ("impact", c.impacts)):
            if allowed is not None and (node.raw.get(fld) or "") not in allowed:
                return False
        if c.initiators is not None and (node.raw.get("initiator") or "") not in c.initiators:
            return False
        if c.period:
            s, e, _ = ds.span(node)
            if not s or e < c.period[0] or s > c.period[1]:
                return False
        return True

    def filterAcceptsRow(self, row, parent):
        if self.accepted is None:
            return True
        model: GanttModel = self.sourceModel()
        children = parent.internalPointer().children if parent.isValid() else model.ds.roots
        return children[row].id in self.accepted
