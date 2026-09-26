"""Левая половина окна: дерево-таблица. Вложенность как в отчёте: раздел → проект → задания, раскрытие через «+».

Строка рисуется целиком за один проход (drawRow): без стандартного делегата Qt, который на каждую ячейку
по многу раз спрашивает модель, — так таблица на тысячу строк прокручивается плавно.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyle, QStyledItemDelegate, QTreeView

from .smooth import DragPan
from .theme import row_colors, scale_color, status_color, tone
from .timeline import HEADER_HEIGHT
from .tree_model import COL, COLUMNS, NodeRole

CENTERED = {"number", "start", "end", "fact_start", "fact_end"}
CHIP_INK = QColor("#1C1E23")


def _alpha(c: QColor, a: int) -> QColor:
    c = QColor(c)
    c.setAlpha(a)
    return c


class GroupHeader(QHeaderView):
    """Заголовок таблицы с группами колонок.

    Ctrl+щелчок по заголовкам отмечает колонки; когда Ctrl отпущен, отмеченные становятся группой.
    Над группой — скобка и кнопка «−» (свернуть) или «+» (развернуть).
    В groups — только группы, у которых есть колонки, включённые в меню; index — номер группы в настройках.
    """

    BAR = 16                                   # полоска групп над названиями колонок

    groupToggled = Signal(int)                 # номер группы в настройках
    picked = Signal(list)                      # колонки, отмеченные с Ctrl
    levelClicked = Signal(int)                 # кнопка уровня «1 2 3…» слева от «Наименование»

    def __init__(self, view: "TableView"):
        super().__init__(Qt.Orientation.Horizontal, view)
        self.view = view
        self.groups: list[dict] = []
        self.selection: list[str] = []
        self._buttons: list[tuple[QRectF, int]] = []
        self.levels = 0                        # сколько уровней вложенности в данных
        self.level_names: list[str] = []
        self.level_active: int | None = None
        self._level_btns: list[tuple[QRectF, int]] = []
        self._level_hot: int | None = None
        self._swallow = False
        self._poll = QTimer(self)
        self._poll.setInterval(70)
        self._poll.timeout.connect(self._check_ctrl)

    def _bar(self) -> int:
        return self.BAR if self.groups else 0

    def levels_width(self) -> int:
        return self.levels * 21 + 10 if self.levels > 1 else 0

    def paintSection(self, painter: QPainter, rect: QRect, logical: int) -> None:
        bar = self._bar()
        lw = self.levels_width() if logical == 0 else 0
        super().paintSection(painter, rect.adjusted(lw, bar, 0, 0), logical)
        if lw:
            self._paint_levels(painter, QRect(rect.left(), rect.top() + bar, lw, rect.height() - bar))
        if COLUMNS[logical][0] in self.selection:  # отмечено для группы
            painter.save()
            painter.fillRect(rect.adjusted(0, bar, 0, 0), _alpha(self.view.accent, 46))
            painter.setPen(QPen(self.view.accent, 2))
            painter.drawLine(rect.left() + 1, rect.bottom() - 1, rect.right() - 1, rect.bottom() - 1)
            painter.restore()

    def _paint_levels(self, p: QPainter, r: QRect) -> None:
        """Кнопки «1 2 3»: одним нажатием — показать дерево до нужного уровня, как в Excel."""
        t = self.view.tokens
        if t is None:
            return
        p.save()
        p.fillRect(r, QColor(t.panel2))
        p.setPen(QPen(QColor(t.line), 1))
        p.drawLine(r.left(), r.bottom(), r.right(), r.bottom())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        accent = self.view.accent
        self._level_btns = []
        for n in range(1, self.levels + 1):
            box = QRectF(r.left() + 6 + (n - 1) * 21, r.center().y() - 8.5, 18, 17)
            active, hot = n == self.level_active, n == self._level_hot
            p.setPen(QPen(accent if active or hot else QColor(t.ink3), 1))
            p.setBrush(accent if active else QColor(t.panel))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QColor("#FFFFFF") if active else (accent if hot else QColor(t.ink2)))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, str(n))
            self._level_btns.append((box.adjusted(-1, -3, 2, 3), n))
        p.restore()

    def level_tooltip(self, n: int) -> str:
        names = self.level_names
        if n >= self.levels:
            return "Развернуть всё" + (f" — до «{names[-1]}»" if names else "")
        if n == 1:
            return "Свернуть всё" + (f" — только «{names[0]}»" if names else "")
        return f"Показать до уровня «{names[n - 1]}»" if n - 1 < len(names) else f"До уровня {n}"

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        self._buttons = []
        t = self.view.tokens
        if not self.groups or t is None:
            return
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = self.BAR
        p.fillRect(QRect(0, 0, self.viewport().width(), bar), QColor(t.panel2))
        ink, accent = QColor(t.ink3), self.view.accent
        for g, grp in enumerate(self.groups):
            logicals = [COL[k] for k in grp["cols"] if k in COL]
            shown = [i for i in logicals if not self.isSectionHidden(i)]
            if shown and not grp.get("collapsed"):
                x1 = min(self.sectionViewportPosition(i) for i in shown)
                x2 = max(self.sectionViewportPosition(i) + self.sectionSize(i) for i in shown)
                p.setPen(QPen(ink, 1.2))
                y = bar / 2 + 0.5
                p.drawLine(QPointF(x1 + 16, y), QPointF(x2 - 5, y))
                p.drawLine(QPointF(x2 - 5, y), QPointF(x2 - 5, bar))
                box_x = x1 + 3
                sign = "−"
            else:  # свёрнута: «+» на месте группы — у правого края видимой колонки слева
                first = min((self.visualIndex(i) for i in logicals), default=0)
                left = [self.logicalIndex(v) for v in range(first) if not self.isSectionHidden(self.logicalIndex(v))]
                edge = (self.sectionViewportPosition(left[-1]) + self.sectionSize(left[-1])) if left else 0
                box_x = max(2, min(edge - 6, self.viewport().width() - 15))   # целиком внутри таблицы
                sign = "+"
            box = QRectF(box_x, bar / 2 - 5.5, 11, 11)
            hot = box.adjusted(-4, -3, 4, 3)
            p.setPen(QPen(accent if sign == "+" else ink, 1))
            p.setBrush(QColor(t.panel))
            p.drawRoundedRect(box, 2.5, 2.5)
            c = box.center()
            p.setPen(QPen(accent if sign == "+" else ink, 1.4))
            p.drawLine(QPointF(c.x() - 2.8, c.y()), QPointF(c.x() + 2.8, c.y()))
            if sign == "+":
                p.drawLine(QPointF(c.x(), c.y() - 2.8), QPointF(c.x(), c.y() + 2.8))
            self._buttons.append((hot, g))
        p.setPen(QPen(QColor(t.grid), 1))
        p.drawLine(0, bar, self.viewport().width(), bar)
        p.end()

    def group_tooltip(self, g: int) -> str:
        titles = dict(COLUMNS)
        grp = self.groups[g]
        names = ", ".join(titles.get(k, k) for k in grp["cols"])
        return ("Развернуть: " if grp.get("collapsed") else "Свернуть: ") + names

    def mousePressEvent(self, event) -> None:
        pos = event.position().toPoint()
        for hot, n in self._level_btns:
            if hot.contains(QPointF(pos)):
                self._swallow = True
                self.levelClicked.emit(n)
                return
        for hot, g in self._buttons:
            if hot.contains(QPointF(pos)):
                self._swallow = True
                self.groupToggled.emit(self.groups[g].get("index", g))
                return
        if event.button() == Qt.MouseButton.LeftButton and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            logical = self.logicalIndexAt(pos.x())
            key = COLUMNS[logical][0] if logical >= 0 else ""
            if key and key not in ("name", "timeline"):
                if key in self.selection:
                    self.selection.remove(key)
                else:
                    self.selection.append(key)
                self._swallow = True
                self._poll.start()
                self.viewport().update()
                return
        if self.groups and pos.y() < self.BAR:  # полоска групп — не сортировка
            self._swallow = True
            return
        self._swallow = False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._swallow:
            self._swallow = False
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        tip = next((self.group_tooltip(g) for hot, g in self._buttons if hot.contains(QPointF(pos))), "")
        level = next((n for hot, n in self._level_btns if hot.contains(QPointF(pos))), None)
        if level != self._level_hot:
            self._level_hot = level
            self.viewport().update()
        if level is not None:
            tip = self.level_tooltip(level)
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor if tip else Qt.CursorShape.ArrowCursor)
        if not self._swallow:
            super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._level_hot is not None:
            self._level_hot = None
            self.viewport().update()
        super().leaveEvent(event)

    def _check_ctrl(self) -> None:
        if QGuiApplication.queryKeyboardModifiers() & Qt.KeyboardModifier.ControlModifier:
            return
        self._poll.stop()
        keys, self.selection = self.selection, []
        self.viewport().update()
        if keys:
            self.picked.emit(keys)


class RowDelegate(QStyledItemDelegate):
    """Только высота строки — одинаковая в таблице и на шкале. Рисует сама таблица."""

    def __init__(self, view: "TableView"):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index):
        return QSize(40, self.view.settings.row_height)


class TableView(QTreeView):
    editRequested = Signal(object)
    contextRequested = Signal(object, QPoint)
    hovered = Signal(object)
    cellDoubleClicked = Signal(object, int)   # узел, колонка — например, двойной щелчок по статусу

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ds = None
        self.settings = None
        self.tokens = None
        self.accent = QColor("#2F5FD0")
        self.project_color = lambda node: QColor("#A9C4F5")
        self.hover_node = None
        self.smooth_v = None
        self.fast = False               # режим картошки: без сглаживания
        self.colors: dict = {}
        self._cols: list[tuple[str, int, int]] = []
        self._elided: dict = {}
        self._fonts: dict = {}
        # строки закрашивают всё сами — прокрутка сдвигает готовую картинку и дорисовывает только новую полоску
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setUniformRowHeights(True)
        self.setAnimated(True)
        self.setMouseTracking(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAllColumnsShowFocus(True)
        self.setExpandsOnDoubleClick(False)  # раскрытие — по «+», двойной щелчок — правка
        self.setIndentation(20)
        self.setHeader(GroupHeader(self))
        header = self.header()
        header.setFixedHeight(HEADER_HEIGHT)
        header.setSectionsMovable(True)
        header.setFirstSectionMovable(False)
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.pan = DragPan(self)         # зажатое колесо — тянуть список вверх-вниз, как и диаграмму
        self.pan.started.connect(lambda: self.hovered.emit(None))

    def setModel(self, model) -> None:
        super().setModel(model)
        self.setItemDelegate(RowDelegate(self))
        self.setColumnHidden(len(COLUMNS) - 1, True)   # шкала — в правой половине
        widths = {"name": 340, "number": 64, "executors": 150, "status": 112, "progress": 150, "due": 128,
                  "start": 84, "end": 84, "fact_start": 92, "fact_end": 104, "priority": 100, "urgency": 100,
                  "impact": 100, "initiator": 140, "custom": 180}
        for i, (key, _t) in enumerate(COLUMNS):
            if key in widths:
                self.setColumnWidth(i, widths[key])

    def restyle(self) -> None:
        """Тема, акцент или шрифт поменялись — пересчитать цвета и шрифты."""
        if self.tokens is not None:
            self.colors = row_colors(self.tokens, self.accent)
        self._fonts.clear()
        self._elided.clear()

    # ---------- отрисовка ----------
    def _font(self, kind: str) -> tuple[QFont, QFontMetrics]:
        f = self._fonts.get(kind)
        if f is None:
            font = QFont(self.font())
            if kind == "bold":
                font.setWeight(QFont.Weight.DemiBold)
            elif kind == "small":
                font.setPointSizeF(font.pointSizeF() - 0.5)
            elif kind == "smallbold":
                font.setPointSizeF(font.pointSizeF() - 0.5)
                font.setWeight(QFont.Weight.DemiBold)
            f = self._fonts[kind] = (font, QFontMetrics(font))
        return f

    def _elide(self, text: str, width: int, kind: str) -> str:
        key = (text, width, kind)
        t = self._elided.get(key)
        if t is None:
            if len(self._elided) > 20000:
                self._elided.clear()
            t = self._elided[key] = self._font(kind)[1].elidedText(text, Qt.TextElideMode.ElideRight, max(0, width))
        return t

    def paintEvent(self, event) -> None:
        # раскладка видимых колонок — один раз на кадр, а не для каждой строки
        h, vw = self.header(), self.viewport().width()
        cols = []
        for vis in range(h.count()):
            i = h.logicalIndex(vis)
            if h.isSectionHidden(i):
                continue
            x, w = h.sectionViewportPosition(i), h.sectionSize(i)
            if x + w > 0 and x < vw:
                cols.append((COLUMNS[i][0], x, w))
        self._cols = cols
        if not self.colors and self.tokens is not None:
            self.restyle()
        if self.colors:  # поле непрозрачное: пустое место под строками закрашиваем сами
            p = QPainter(self.viewport())
            p.fillRect(event.rect(), self.colors["panel"])
            p.end()
        super().paintEvent(event)

    def drawRow(self, painter: QPainter, option, index) -> None:
        ds, node = self.ds, index.data(NodeRole)
        if ds is None or node is None or not self.colors:
            super().drawRow(painter, option, index)
            return
        c, t = self.colors, self.tokens
        top, H = option.rect.top(), option.rect.height()
        width = self.viewport().width()
        role = ds.role(node)
        row_no = (top + self.verticalOffset()) // max(1, H)
        if role == "group":
            bg = c["group"]
        elif self.settings.zebra and row_no % 2:
            bg = c["zebra"]
        else:
            bg = c["panel"]
        painter.fillRect(QRect(0, top, width, H), bg)
        selected = self.selectionModel() is not None and self.selectionModel().isSelected(index)
        if selected:
            painter.fillRect(QRect(0, top, width, H), c["select"])
            painter.fillRect(QRect(0, top, 3, H), c["accent"])
        elif node is self.hover_node:
            painter.fillRect(QRect(0, top, width, H), c["hover"])
        has_children = bool(option.state & QStyle.StateFlag.State_Children)
        expanded = bool(option.state & QStyle.StateFlag.State_Open)
        for key, x, w in self._cols:
            cell = QRect(x, top, w, H)
            if key == "name":
                self._name(painter, cell, node, role, has_children, expanded)
            elif key == "status":
                self._status(painter, cell, node)
            elif key == "progress":
                self._progress(painter, cell, node, role)
            elif key == "due":
                self._due(painter, cell, node)
            elif key in ("priority", "urgency", "impact"):
                self._scale(painter, cell, node, key)
            else:
                text = self.model().sourceModel().cell_text(node, key)
                if text:
                    font, _fm = self._font("normal")
                    painter.setFont(font)
                    painter.setPen(QColor(t.ink2))
                    align = Qt.AlignmentFlag.AlignCenter if key in CENTERED else \
                        Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                    painter.drawText(cell.adjusted(8, 0, -8, 0), align, self._elide(text, w - 16, "normal"))
        # тонкая линия под разделом — разделы отделены друг от друга
        if role == "group":
            painter.setPen(QPen(QColor(t.line), 1))
            painter.drawLine(0, top + H - 1, width, top + H - 1)

    def _name(self, p: QPainter, cell: QRect, node, role: str, has_children: bool, expanded: bool) -> None:
        t, c, ds = self.tokens, self.colors, self.ds
        ind = self.indentation()
        x = cell.left() + (node.depth + 1) * ind
        cy = cell.center().y() + 0.5
        p.setRenderHint(QPainter.RenderHint.Antialiasing, not self.fast)
        if has_children:  # «+» / «−»
            s = 11
            box = QRectF(x - ind / 2 - s / 2 + 0.5, cy - s / 2, s, s)
            hot = node is self.hover_node
            ink = c["accent"] if hot else QColor(t.ink3)
            p.setPen(QPen(ink, 1))
            p.setBrush(QColor(t.panel))
            p.drawRoundedRect(box, 2.5, 2.5)
            p.setPen(QPen(ink, 1.4))
            bc = box.center()
            p.drawLine(QPointF(bc.x() - 2.8, bc.y()), QPointF(bc.x() + 2.8, bc.y()))
            if not expanded:
                p.drawLine(QPointF(bc.x(), bc.y() - 2.8), QPointF(bc.x(), bc.y() + 2.8))
        x += 4
        right = cell.right() - 6
        if ds.is_edited(node):  # точка «изменено вами»
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c["accent"])
            p.drawEllipse(QPointF(right - 3, cy), 3.5, 3.5)
            right -= 12
        if role != "task":  # сколько внутри просрочено — видно, не раскрывая
            n = ds.overdue_count(node)
            if n:
                font, fm = self._font("smallbold")
                label = str(n)
                bw = fm.horizontalAdvance(label) + 12
                pill = QRectF(right - bw, cy - 8, bw, 16)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_alpha(c["red"], 40 if t.dark else 30))
                p.drawRoundedRect(pill, 8, 8)
                p.setFont(font)
                p.setPen(c["red"])
                p.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)
                right -= bw + 6
        if role == "project" or ds.get(node, "color"):
            col = self.project_color(node)
            p.setPen(QPen(tone(col, -0.22), 1))
            p.setBrush(col)
            p.drawRoundedRect(QRectF(x, cy - 5.5, 11, 11), 3, 3)
            x += 17
        kind = "normal" if role == "task" else "bold"
        font, _fm = self._font(kind)
        p.setFont(font)
        p.setPen(QColor(t.ink))
        p.drawText(QRect(x, cell.top(), max(0, right - x), cell.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self._elide(node.name, right - x, kind))

    def _chip(self, p: QPainter, cell: QRect, text: str, color: QColor) -> None:
        font, fm = self._font("small")
        w = min(fm.horizontalAdvance(text) + 18, cell.width() - 12)
        h = min(20, cell.height() - 6)
        r = QRectF(cell.left() + 7, cell.center().y() - h / 2 + 0.5, w, h)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, not self.fast)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(r, h / 2, h / 2)
        p.setFont(font)
        p.setPen(CHIP_INK)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._elide(text, int(w) - 12, "small"))

    def _status(self, p: QPainter, cell: QRect, node) -> None:
        text = self.model().sourceModel().cell_text(node, "status")
        if text:
            self._chip(p, cell, text, status_color(self.ds.status_kind_for_color(node), self.settings, self.tokens))

    def _scale(self, p: QPainter, cell: QRect, node, fld: str) -> None:
        item = self.ds.scale_item(node, fld)
        if not item:
            return
        color = QColor(item["color"]) if item.get("color") else scale_color(
            item.get("order", 1), self.ds.scale_top(fld), self.settings, self.tokens)
        self._chip(p, cell, item["name"], color)

    def _progress(self, p: QPainter, cell: QRect, node, role: str) -> None:
        text = self.model().sourceModel().cell_text(node, "progress")
        if not text:
            return
        t = self.tokens
        pct = self.ds.percent(node) or 0
        base = self.project_color(node) if role == "project" else QColor(t.group_bar)
        r = cell.adjusted(8, 0, -8, 0)
        bar = QRectF(r.left(), r.center().y() - 3, 38, 6)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, not self.fast)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t.grid_strong))
        p.drawRoundedRect(bar, 3, 3)
        if pct:
            p.setBrush(tone(base, -0.18))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * min(pct, 100) / 100, bar.height()), 3, 3)
        font, _fm = self._font("small")
        p.setFont(font)
        p.setPen(QColor(t.ink2))
        p.drawText(QRect(int(bar.right()) + 7, r.top(), r.width() - 45, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self._elide(text, r.width() - 45, "small"))

    def _due(self, p: QPainter, cell: QRect, node) -> None:
        kind, _days, text = self.ds.due_info(node)
        if not text:
            return
        c, t = self.colors, self.tokens
        r = cell.adjusted(8, 0, -8, 0)
        if kind in ("overdue", "today"):  # то, что горит, — «таблеткой»
            font, fm = self._font("smallbold")
            color = c["red"] if kind == "overdue" else c["amber"]
            label = self._elide(text, r.width() - 14, "smallbold")
            w = fm.horizontalAdvance(label) + 14
            pill = QRectF(r.left() - 1, r.center().y() - 9.5, w, 19)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, not self.fast)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_alpha(color, 38 if t.dark else 26))
            p.drawRoundedRect(pill, 9.5, 9.5)
            p.setFont(font)
            p.setPen(color)
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)
            return
        color = {"soon": c["amber"], "late": c["amber"], "ontime": c["green"], "ok": QColor(t.ink2)}.get(
            kind, QColor(t.ink3))
        font, _fm = self._font("small")
        p.setFont(font)
        p.setPen(color)
        p.drawText(r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._elide(text, r.width(), "small"))

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        """Вертикально — всегда сдвиг готовой картинки. Qt в попиксельном режиме при шаге больше числа
        видимых строк перерисовывает окно целиком — на быстрой прокрутке это заметно."""
        if dx == 0 and dy:
            self.viewport().scroll(0, dy)
        else:
            super().scrollContentsBy(dx, dy)

    # ---------- мышь и клавиши ----------
    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:   # подсказки — карточкой при наведении
            return True
        return super().viewportEvent(event)

    def mouseMoveEvent(self, event) -> None:
        node = self.indexAt(event.position().toPoint()).data(NodeRole)
        if node is not self.hover_node:
            self.hovered.emit(node)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self.hovered.emit(None)
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        idx = self.indexAt(event.position().toPoint())
        if idx.isValid():
            self.cellDoubleClicked.emit(idx.data(NodeRole), idx.column())
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:
        mods = event.modifiers()
        if self.smooth_v and event.pixelDelta().isNull() and not mods & Qt.KeyboardModifier.ShiftModifier \
                and event.angleDelta().y():
            self.smooth_v.scroll_by(-event.angleDelta().y() / 120 * 3 * self.settings.row_height)
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_F2, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            node = self.currentIndex().data(NodeRole)
            if node is not None:
                self.editRequested.emit(node)
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
        idx = self.indexAt(event.pos())
        if idx.isValid():
            self.setCurrentIndex(idx)
            self.contextRequested.emit(idx.data(NodeRole), event.globalPos())

    def row_rect(self, node) -> QRect | None:
        """Прямоугольник строки узла во вьюпорте — чтобы перерисовать только её."""
        proxy = self.model()
        idx = proxy.mapFromSource(proxy.sourceModel().index_for(node))
        if not idx.isValid():
            return None
        r = self.visualRect(idx)
        return QRect(0, r.top(), self.viewport().width(), r.height()) if r.isValid() else None

    def content_width(self) -> int:
        """Ширина всех видимых колонок — дальше таблицу раздвигать нельзя, иначе она «рвётся»."""
        h = self.header()
        return sum(h.sectionSize(i) for i in range(h.count()) if not h.isSectionHidden(i)) + 2 * self.frameWidth()

    def column_edges(self) -> list[int]:
        """Правые края видимых колонок в порядке показа — к ним «прилипает» разделитель."""
        h = self.header()
        edges, x = [], 2 * self.frameWidth()
        for vis in range(h.count()):
            i = h.logicalIndex(vis)
            if not h.isSectionHidden(i):
                x += h.sectionSize(i)
                edges.append(x)
        return edges
