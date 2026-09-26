"""Обозначения: строка под диаграммой и полная легенда во всплывающей панели. Образцы рисуются теми же цветами."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QToolButton, QVBoxLayout, QWidget

from .filters import STATUS_KINDS
from .theme import _alpha, plan_colors, project_palette, row_colors, status_color

ITEMS = [  # (образец, коротко, подробно)
    ("plan", "План", "Светлая дорожка с рамкой — когда задание должно быть сделано: от начала до срока."),
    ("fact", "Факт", "Насыщенная полоса внутри дорожки — когда делали на самом деле."),
    ("going", "Идёт", "Факт до сегодняшнего дня со стрелкой — задание сейчас в работе."),
    ("noend", "Без срока", "Дорожка растворяется вправо — срок не задан."),
    ("overdue", "Просрочено", "Красная штриховка от срока до сегодня и «+N дн.» — срок прошёл, а задание не закрыто."),
    ("late", "Позже срока", "Красный хвост факта за красной риской — закончили позже плана."),
    ("derived", "План по заданиям", "Пунктирная рамка у проекта — своих дат нет, период собран по его заданиям."),
    ("group", "Раздел", "Серая скобка — общий период всех проектов раздела."),
    ("milestone", "Веха", "Ромб — событие одного дня."),
    ("today", "Сегодня", "Красная линия; подсвеченная полоса — текущая неделя."),
    ("edited", "Изменено вами", "Метка «изм.» у полосы (в таблице — точка) — у строки есть ваши правки, "
                                "в источнике их ещё нет. Пунктирная рамка — план до правки."),
    ("badge", "Просрочено внутри", "Красное число у проекта или раздела в таблице — сколько заданий внутри просрочено."),
]
SHORT = ["plan", "fact", "going", "noend", "overdue", "late", "milestone", "today", "edited"]


def paint_sample(p: QPainter, r: QRectF, kind: str, s, t, accent: QColor) -> None:
    """Образец обозначения в прямоугольнике r (примерно 34×16)."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    red = QColor(t.today)
    cy = r.center().y()
    th = min(r.height(), 13.0)
    track = QRectF(r.left(), cy - th / 2, r.width(), th)
    fh = max(4.0, th * 0.42)

    def plan(rect: QRectF, base: QColor, dashed: bool = False) -> None:
        fill, edge, _fact = plan_colors(base, t)
        p.setBrush(fill)
        p.setPen(QPen(edge, 1.2, Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine))
        p.drawRoundedRect(rect, 3.5, 3.5)

    def fact(x1: float, x2: float, base: QColor, color: QColor | None = None) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color or plan_colors(base, t)[2])
        p.drawRoundedRect(QRectF(x1, cy - fh / 2, x2 - x1, fh), fh / 2, fh / 2)

    work, done = status_color("inProgress", s, t), status_color("done", s, t)
    if kind == "plan":
        plan(track, work)
    elif kind == "fact":
        plan(track, done)
        fact(r.left() + 3, r.right() - 5, done)
    elif kind == "going":
        plan(track, work)
        x2 = r.left() + r.width() * 0.68
        fact(r.left() + 3, x2, work)
        p.setBrush(plan_colors(work, t)[2])
        p.drawPolygon(QPolygonF([QPointF(x2 - 1, cy - fh / 2 - 2.5), QPointF(x2 + 6, cy),
                                 QPointF(x2 - 1, cy + fh / 2 + 2.5)]))
    elif kind == "noend":
        fill, edge, _f = plan_colors(work, t)
        g = QLinearGradient(r.left(), 0, r.right(), 0)
        g.setColorAt(0.3, fill)
        g.setColorAt(1, _alpha(fill, 0))
        p.setBrush(QBrush(g))
        ge = QLinearGradient(r.left(), 0, r.right(), 0)
        ge.setColorAt(0.3, edge)
        ge.setColorAt(1, _alpha(edge, 0))
        p.setPen(QPen(QBrush(ge), 1.2, Qt.PenStyle.DashLine))
        p.drawRoundedRect(track, 3.5, 3.5)
    elif kind == "overdue":
        od = status_color("overdue", s, t)
        mid = r.left() + r.width() * 0.5
        plan(QRectF(r.left(), track.top(), mid - r.left(), th), od)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_alpha(red, 170), Qt.BrushStyle.BDiagPattern))
        p.drawRect(QRectF(mid, track.top() + 1, r.right() - mid, th - 2))
        p.setPen(QPen(red, 1.6))
        p.drawLine(QPointF(mid, track.top() - 2), QPointF(mid, track.bottom() + 2))
    elif kind == "late":
        mid = r.left() + r.width() * 0.55
        plan(QRectF(r.left(), track.top(), mid - r.left(), th), done)
        fact(r.left() + 3, r.right(), done)
        fact(mid, r.right(), done, red)
        p.setPen(QPen(red, 1.6))
        p.drawLine(QPointF(mid, track.top() - 2), QPointF(mid, track.bottom() + 2))
    elif kind == "derived":
        plan(track, project_palette(s, t)[0], dashed=True)
    elif kind == "group":
        c = QColor(t.group_bar)
        y, h = cy - 2, 4
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawRect(QRectF(r.left(), y, r.width(), h))
        for x, dx in ((r.left(), 5), (r.right(), -5)):
            p.drawPolygon(QPolygonF([QPointF(x, y), QPointF(x, y + h + 5), QPointF(x + dx, y + h)]))
    elif kind == "milestone":
        _fill, edge, fc = plan_colors(work, t)
        c, rr = r.center(), th * 0.55
        p.setPen(QPen(edge, 1.2))
        p.setBrush(fc)
        p.drawPolygon(QPolygonF([QPointF(c.x(), cy - rr), QPointF(c.x() + rr, cy), QPointF(c.x(), cy + rr),
                                 QPointF(c.x() - rr, cy)]))
    elif kind == "today":
        p.fillRect(QRectF(r.left() + 4, r.top(), r.width() - 8, r.height()), _alpha(accent, 26))
        p.setPen(QPen(red, 2))
        p.drawLine(QPointF(r.center().x(), r.top()), QPointF(r.center().x(), r.bottom()))
    elif kind == "edited":
        f = QFont(p.font())
        f.setPointSizeF(max(6.5, f.pointSizeF() - 2))
        f.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetrics(f)
        w, h = min(r.width(), fm.horizontalAdvance("изм.") + 8), min(r.height(), fm.height())
        chip = QRectF(r.center().x() - w / 2, cy - h / 2, w, h)
        p.setPen(QPen(accent.lighter(140) if t.dark else accent, 1))
        p.setBrush(accent)
        p.drawRoundedRect(chip, 3.5, 3.5)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF") if accent.lightnessF() < 0.62 else QColor("#1C1E23"))
        p.drawText(chip, Qt.AlignmentFlag.AlignCenter, "изм.")
    elif kind == "badge":
        c = row_colors(t, accent)["red"]
        pill = QRectF(r.center().x() - 10, cy - 8, 20, 16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(c, 34))
        p.drawRoundedRect(pill, 8, 8)
        f = QFont(p.font())
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(c)
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, "3")
    p.restore()


class Sample(QWidget):
    def __init__(self, kind: str, win, parent=None):
        super().__init__(parent)
        self.kind, self.win = kind, win
        self.setFixedSize(44, 22)

    def paintEvent(self, _e) -> None:
        if self.win.tokens is None:
            return
        p = QPainter(self)
        paint_sample(p, QRectF(4, 3, 36, 16), self.kind, self.win.settings, self.win.tokens,
                     QColor(self.win.settings.accent))
        p.end()


class LegendBar(QWidget):
    """Строка обозначений под диаграммой: самое нужное, остальное — «Все обозначения»."""

    more = Signal()

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.btn = QToolButton(self)
        self.btn.setText("Все обозначения…")
        self.btn.setAutoRaise(True)
        self.btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn.clicked.connect(self.more)
        lay = QHBoxLayout(self)       # кнопка — по размеру надписи при любом шрифте, без многоточия
        lay.setContentsMargins(0, 0, 8, 0)
        lay.addStretch(1)
        lay.addWidget(self.btn)
        self._fit()

    def _fit(self) -> None:
        self.setFixedHeight(max(30, QFontMetrics(self.font()).height() + 14))

    def changeEvent(self, e) -> None:
        if e.type() == QEvent.Type.FontChange:
            self._fit()
        super().changeEvent(e)

    def paintEvent(self, _e) -> None:
        t, s = self.win.tokens, self.win.settings
        if t is None:
            return
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(t.panel2))
        p.setPen(QColor(t.line))
        p.drawLine(0, 0, self.width(), 0)
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() - 0.5)
        fm = QFontMetrics(font)
        p.setFont(font)
        x, limit = 12, self.btn.x() - 12
        top = (self.height() - 16) / 2
        titles = {k: title for k, title, _d in ITEMS}
        for kind in SHORT:
            w = 34 + 6 + fm.horizontalAdvance(titles[kind]) + 18
            if x + w > limit:
                break
            paint_sample(p, QRectF(x, top, 34, 16), kind, s, t, QColor(s.accent))
            p.setPen(QColor(t.ink2))
            p.drawText(QRectF(x + 40, 0, w, self.height()), int(Qt.AlignmentFlag.AlignVCenter), titles[kind])
            x += w
        p.end()


class _Line(QWidget):
    """Тонкая черта между обозначениями — видно, где кончается одно и начинается другое."""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.setFixedHeight(1)

    def paintEvent(self, _e) -> None:
        if self.win.tokens is not None:
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(self.win.tokens.line))
            p.end()


class LegendPanel(QWidget):
    """Полная легенда: полосы, цвета статусов, колонка «Срок».

    Каждое обозначение — отдельный блок: образец, под названием — описание, между блоками — черта.
    """

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(6)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        lay.addWidget(self._head("Полосы на шкале", first=True))
        items = QVBoxLayout()
        items.setSpacing(0)
        self.descs: list[tuple[QLabel, str]] = []
        for i, (kind, title, text) in enumerate(ITEMS):
            if i:
                items.addWidget(_Line(win))
            row = QHBoxLayout()
            row.setContentsMargins(0, 8, 0, 8)
            row.setSpacing(12)
            row.addWidget(Sample(kind, win), 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(QLabel(f"<b>{title}</b>"))
            desc = QLabel(text)
            desc.setWordWrap(True)
            col.addWidget(desc)
            self.descs.append((desc, text))
            row.addLayout(col, 1)
            items.addLayout(row)
        lay.addLayout(items)
        lay.addWidget(self._head("Цвет заданий — статус"))
        self.status_row = QLabel()
        self.status_row.setWordWrap(True)
        lay.addWidget(self.status_row)
        lay.addWidget(self._head("Колонка «Срок»"))
        self.due_row = QLabel()
        self.due_row.setWordWrap(True)
        lay.addWidget(self.due_row)
        hint = QLabel()
        hint.setWordWrap(True)
        hint.setContentsMargins(0, 8, 0, 0)
        lay.addWidget(hint)
        lay.addStretch()
        self.hint = hint

    @staticmethod
    def _head(text: str, first: bool = False) -> QLabel:
        """Заголовок раздела — крупнее названий обозначений, чтобы не путать одно с другим."""
        lb = QLabel(f"<big><b>{text}</b></big>")
        lb.setContentsMargins(0, 0 if first else 12, 0, 2)
        return lb

    def refresh(self) -> None:
        t, s = self.win.tokens, self.win.settings
        if t is None:
            return
        for desc, text in self.descs:   # описание — приглушённее названия
            desc.setText(f"<span style='color:{t.ink2}'>{text}</span>")
        chips = []
        for kind, title in STATUS_KINDS:
            c = status_color(kind, s, t)
            name = title.replace(' ', '&nbsp;')
            chips.append(f"<span style='background:{c.name()}; color:#1C1E23'>&nbsp;&nbsp;{name}&nbsp;&nbsp;</span>")
        self.status_row.setText("&nbsp; ".join(chips))
        c = row_colors(t, QColor(s.accent))
        red, amber, green = c["red"].name(), c["amber"].name(), c["green"].name()
        self.due_row.setText(
            f"<span style='color:{t.ink2}'>через 12 дн.</span> — время есть; "
            f"<span style='color:{amber}'>через 2 дн.</span> — скоро срок; "
            f"<b style='color:{amber}'>сегодня</b>; <b style='color:{red}'>просрочено 5 дн.</b>; "
            f"<span style='color:{green}'>в срок</span> и <span style='color:{amber}'>позже на 3 дн.</span> — "
            f"для выполненных; <span style='color:{t.ink3}'>без срока</span>.")
        self.hint.setText(f"<span style='color:{t.ink3}'>Наведите мышь на строку — появится карточка с планом, "
                          f"фактом и отклонением от срока.</span>")
        self.update()

