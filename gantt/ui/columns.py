"""Список колонок (меню заголовка и «Настройки»): показать или скрыть колонку и собрать колонки в группу.

Строки — в порядке показа. Справа у колонок одной группы — скобка с кнопками «−»/«+» (свернуть, развернуть)
и «×» (разгруппировать). «Сгруппировать…» или Ctrl+щелчок по строке — режим отметки: отмеченные колонки
встанут рядом и станут группой.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..paths import resource_path
from ..settings import COLUMN_KEYS
from .theme import _alpha, mix
from .tree_model import COL, COLUMNS

TITLES = dict(COLUMNS)
FOLDED = "свёрнута"
HINT = "Ctrl+щелчок — отметить для группы"
HINT_PICK = "Отметьте колонки для новой группы"


class _Rows(QWidget):
    """Строки списка: флажок, название, справа — скобки групп."""

    BRACKET = 66                                  # место справа под скобку и кнопки группы

    def __init__(self, owner: "ColumnList"):
        super().__init__(owner)
        self.o = owner
        self.setMouseTracking(True)
        self._hot = None                          # ("row", ключ) | ("fold", группа) | ("drop", группа)
        self._press = None
        self._buttons: list[tuple[QRectF, str, int]] = []
        self._check = QPixmap(str(resource_path("gantt", "resources", "check.png")))

    def row_h(self) -> int:
        if self.o.compact:
            return max(22, self.fontMetrics().height() + 6)
        return max(26, self.fontMetrics().height() + 10)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        w = max(fm.horizontalAdvance(TITLES[k]) for k in COLUMN_KEYS) + fm.horizontalAdvance(f"  {FOLDED}")
        return QSize(30 + w + 16 + self.BRACKET, self.row_h() * len(COLUMN_KEYS))

    # ---------- раскладка ----------
    def _runs(self, keys: list[str]) -> list[tuple[int, list[int]]]:
        """Группы из настроек → (номер группы, строки подряд). Разорванная группа — несколько скобок."""
        pos = {k: i for i, k in enumerate(keys)}
        runs = []
        for g, grp in enumerate(self.o.win.settings.column_groups):
            rows = sorted(pos[k] for k in grp.get("cols", []) if k in pos)
            start = 0
            for i in range(1, len(rows) + 1):
                if i == len(rows) or rows[i] != rows[i - 1] + 1:
                    runs.append((g, rows[start:i]))
                    start = i
        return runs

    def _hit(self, pos: QPointF):
        for rect, kind, g in self._buttons:
            if rect.contains(pos):
                return kind, g
        keys = self.o.keys()
        i = int(pos.y() // self.row_h())
        if 0 <= i < len(keys) and 0 <= pos.x() < self.width():
            return "row", keys[i]
        return None

    # ---------- отрисовка ----------
    def paintEvent(self, _e) -> None:
        win = self.o.win
        t, s = win.tokens, win.settings
        if t is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(s.accent)
        ink, ink3, panel = QColor(t.ink), QColor(t.ink3), QColor(t.panel)
        keys, rh, W = self.o.keys(), self.row_h(), self.width()
        hidden = set(s.hidden_columns)
        folded = {k for g in s.column_groups if g.get("collapsed") for k in g.get("cols", [])}
        picking, marked = self.o.picking, self.o.marked
        small = QFont(self.font())
        small.setPointSizeF(small.pointSizeF() - 0.5)
        for i, key in enumerate(keys):
            y = i * rh
            row = QRectF(0, y, W, rh)
            if key in marked:
                p.fillRect(row, _alpha(accent, 46))
            elif self._hot == ("row", key) and not (picking and key in hidden):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(mix(panel, accent, 0.16))
                p.drawRoundedRect(row.adjusted(0, 1, 0, -1), 5, 5)
            # флажок: в обычном режиме — видна ли колонка, в режиме отметки — отмечена ли для группы
            box = QRectF(9.5, y + (rh - 14) / 2 + 0.5, 13, 13)   # как флажки пунктов меню
            off = picking and key in hidden
            on = (key in marked) if picking else (key not in hidden)
            if on:
                p.setPen(QPen(accent, 1))
                p.setBrush(accent)
                p.drawRoundedRect(box, 3, 3)
                p.drawPixmap(box.adjusted(1.5, 1.5, -1.5, -1.5).toRect(), self._check)
            else:
                p.setPen(QPen(QColor(t.line) if off else ink3, 1))
                p.setBrush(QColor(t.panel2) if off else panel)
                p.drawRoundedRect(box, 3, 3)
            dim = off or (not picking and key in folded and key not in hidden)
            p.setFont(self.font())
            p.setPen(ink3 if dim else ink)
            text_r = QRectF(30, y, W - 30 - self.BRACKET, rh)
            p.drawText(text_r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, TITLES[key])
            if key in folded and key not in hidden and not picking:
                x = 30 + self.fontMetrics().horizontalAdvance(TITLES[key] + "  ")
                p.setFont(small)
                p.setPen(ink3)
                p.drawText(QRectF(x, y, W - x - self.BRACKET, rh),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, FOLDED)
        self._paint_groups(p, keys, rh, W, accent, ink3, panel)
        p.end()

    def _paint_groups(self, p: QPainter, keys, rh: int, W: int, accent: QColor, ink3: QColor, panel: QColor) -> None:
        """Скобка справа у строк группы; посередине — «−»/«+» и «×», как над заголовком таблицы."""
        self._buttons = []
        groups = self.o.win.settings.column_groups
        seen: set[int] = set()
        x = W - self.BRACKET + 10
        for g, rows in self._runs(keys):
            y1, y2 = rows[0] * rh + rh / 2, rows[-1] * rh + rh / 2
            collapsed = bool(groups[g].get("collapsed"))
            p.setPen(QPen(accent if collapsed else ink3, 1.2))
            for r in rows:
                cy = r * rh + rh / 2
                p.drawLine(QPointF(x - 6, cy), QPointF(x, cy))
            p.drawLine(QPointF(x, y1), QPointF(x, y2))
            if g in seen:                            # кнопки — у первой части разорванной группы
                continue
            seen.add(g)
            mid = (y1 + y2) / 2
            for n, kind in enumerate(("fold", "drop")):
                box = QRectF(x + 8 + n * 22, mid - 6.5, 13, 13)
                hot = self._hot == (kind, g)
                color = accent if hot or (kind == "fold" and collapsed) else ink3
                p.setPen(QPen(color, 1))
                p.setBrush(panel)
                p.drawRoundedRect(box, 2.5, 2.5)
                c = box.center()
                p.setPen(QPen(color, 1.4))
                if kind == "fold":
                    p.drawLine(QPointF(c.x() - 2.8, c.y()), QPointF(c.x() + 2.8, c.y()))
                    if collapsed:
                        p.drawLine(QPointF(c.x(), c.y() - 2.8), QPointF(c.x(), c.y() + 2.8))
                else:
                    p.drawLine(QPointF(c.x() - 2.4, c.y() - 2.4), QPointF(c.x() + 2.4, c.y() + 2.4))
                    p.drawLine(QPointF(c.x() - 2.4, c.y() + 2.4), QPointF(c.x() + 2.4, c.y() - 2.4))
                self._buttons.append((box.adjusted(-4, -4, 4, 4), kind, g))

    # ---------- мышь ----------
    def _tip(self, hit) -> str:
        if hit is None or hit[0] == "row":
            return ""
        grp = self.o.win.settings.column_groups[hit[1]]
        names = ", ".join(TITLES.get(k, k) for k in grp.get("cols", []))
        if hit[0] == "drop":
            return f"Разгруппировать: {names}"
        return ("Развернуть: " if grp.get("collapsed") else "Свернуть: ") + names

    def mouseMoveEvent(self, e) -> None:
        hit = self._hit(e.position())
        if hit != self._hot:
            self._hot = hit
            self.setToolTip(self._tip(hit))
            self.setCursor(Qt.CursorShape.PointingHandCursor if hit and hit[0] != "row"
                           else Qt.CursorShape.ArrowCursor)
            self.update()
        e.accept()

    def leaveEvent(self, e) -> None:
        self._hot = None
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e) -> None:
        self._press = self._hit(e.position()) if e.button() == Qt.MouseButton.LeftButton else None
        e.accept()

    def mouseReleaseEvent(self, e) -> None:
        hit = self._hit(e.position())
        press, self._press = self._press, None
        e.accept()
        if e.button() != Qt.MouseButton.LeftButton or hit is None or hit != press:
            return
        kind, arg = hit
        ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if kind == "fold":
            self.o.win.toggle_group(arg)
        elif kind == "drop":
            self._hot = None
            self.o.win.ungroup(arg)
        elif ctrl or self.o.picking:
            self.o.mark(arg)
        else:
            self.o.toggle(arg)
        self.o.refresh()


class ColumnList(QWidget):
    """Список колонок, подсказка и кнопки групп — в меню заголовка (оно при щелчках не закрывается)
    и в «Настройках». Все изменения — сразу в окно: win.settings — единственный источник."""

    def __init__(self, win, close_menu=None, parent=None, compact: bool = False):
        super().__init__(parent)
        self.win = win
        self.close_menu = close_menu or (lambda: None)
        self.compact = compact                    # строки плотнее — в «Настройках» рядом с другими разделами
        self.picking = False                      # режим отметки колонок для новой группы
        self.marked: list[str] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 2)
        lay.setSpacing(6)
        self.rows = _Rows(self)
        lay.addWidget(self.rows)
        self.hint = QLabel()
        self.hint.setContentsMargins(10, 4, 10, 0)
        lay.addWidget(self.hint)
        foot = QHBoxLayout()
        foot.setContentsMargins(8, 0, 8, 0)
        foot.setSpacing(6)
        self.btn_group = QPushButton()
        self.btn_group.clicked.connect(self._group_clicked)
        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_all = QPushButton("Разгруппировать все")
        self.btn_all.clicked.connect(self._ungroup_all)
        foot.addWidget(self.btn_group)
        foot.addWidget(self.btn_cancel)
        foot.addStretch(1)
        foot.addWidget(self.btn_all)
        lay.addLayout(foot)
        # ширина — под самый широкий из двух режимов: меню и панель не меняют размер на ходу
        widths = []
        for text, hint, pick in (("Сгруппировать…", HINT, False), ("Сгруппировать (00)", HINT_PICK, True)):
            self.btn_group.setText(text)
            self.hint.setText(hint)
            self.btn_cancel.setVisible(pick)
            self.btn_all.setVisible(not pick)
            lay.invalidate()
            widths.append(self.sizeHint().width())
        self.setMinimumWidth(max(widths))
        self.refresh()

    def keys(self) -> list[str]:
        """Колонки в порядке показа — группа в списке идёт подряд, как в таблице."""
        h = self.win.table.header()
        return sorted(COLUMN_KEYS, key=lambda k: h.visualIndex(COL[k]))

    def refresh(self) -> None:
        n, t = len(self.marked), self.win.tokens
        self.btn_group.setText(f"Сгруппировать ({n})" if self.picking else "Сгруппировать…")
        self.btn_group.setToolTip("Отмеченные колонки встанут рядом и станут группой" if self.picking else
                                  "Отметить колонки для новой группы")
        self.btn_group.setEnabled(not self.picking or n > 0)
        primary = "primary" if self.picking and n else ""
        if self.btn_group.objectName() != primary:
            self.btn_group.setObjectName(primary)
            self.btn_group.style().unpolish(self.btn_group)
            self.btn_group.style().polish(self.btn_group)
        self.btn_cancel.setVisible(self.picking)
        self.btn_all.setVisible(not self.picking and len(self.win.settings.column_groups) > 1)
        if t is not None:
            color = self.win.settings.accent if self.picking else t.ink3
            self.hint.setText(f"<span style='color:{color}'>{HINT_PICK if self.picking else HINT}</span>")
        self.rows.update()

    def toggle(self, key: str) -> None:
        self.win._toggle_column(key, key in self.win.settings.hidden_columns)

    def mark(self, key: str) -> None:
        if key in self.win.settings.hidden_columns:   # скрытую колонку в группу не берём
            return
        self.picking = True
        if key in self.marked:
            self.marked.remove(key)
        else:
            self.marked.append(key)

    def cancel(self) -> None:
        self.picking, self.marked = False, []
        self.refresh()

    def _group_clicked(self) -> None:
        if not self.picking:
            self.picking = True
            self.refresh()
            return
        keys = list(self.marked)
        self.cancel()
        self.close_menu()
        self.win.make_group(keys)
        self.refresh()

    def _ungroup_all(self) -> None:
        self.close_menu()
        self.win.ungroup(None)
        self.refresh()
