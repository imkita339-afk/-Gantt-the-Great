"""Правая половина окна: временная шкала с полосами. Та же модель, что у таблицы, — строки совпадают всегда."""
from __future__ import annotations

import math
from datetime import date, timedelta

from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QVariantAnimation,
                            Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyledItemDelegate, QToolTip, QTreeView

from ..model import Dataset, Node, fmt, monday
from .smooth import DragPan
from .theme import Tokens, plan_colors, row_colors, scale_color, status_color, tone
from .tree_model import COLUMNS, TIMELINE_COL, NodeRole

MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь",
          "Ноябрь", "Декабрь"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
ZOOM_PRESETS = {"day": 26.0, "week": 5.0, "month": 1.6, "quarter": 0.7, "year": 0.3}
MIN_PPD, MAX_PPD = 0.12, 60.0
HEADER_HEIGHT = 46


class TimeScale:
    def __init__(self, start: date, end: date, ppd: float):
        self.start = monday(start)
        self.end = end
        self.ppd = ppd

    @property
    def width(self) -> int:
        return int(math.ceil(((self.end - self.start).days + 1) * self.ppd))

    def x(self, d: date) -> float:
        return (d - self.start).days * self.ppd

    def date_at(self, x: float) -> date:
        return self.start + timedelta(days=math.floor(x / self.ppd))

    def tiers(self) -> tuple[str, str]:
        if self.ppd >= 14:
            return "month", "day"
        if self.ppd >= 3:
            return "month", "week"
        if self.ppd >= 1.0:
            return "year", "month"
        return "year", "quarter"


UNIT_MONTHS = {"month": 1, "quarter": 3, "year": 12}


def unit_start(unit: str, d: date) -> date:
    if unit == "day":
        return d
    if unit == "week":
        return monday(d)
    months = UNIT_MONTHS[unit]
    return date(d.year, (d.month - 1) // months * months + 1, 1)


def next_boundary(unit: str, d: date) -> date:
    if unit == "day":
        return d + timedelta(days=1)
    if unit == "week":
        return d + timedelta(days=7)
    m = d.month - 1 + UNIT_MONTHS[unit]
    return date(d.year + m // 12, m % 12 + 1, 1)


def boundaries(unit: str, d0: date, d1: date):
    """Начала единиц (день, неделя, месяц, квартал, год): от той, что содержит d0, до d1."""
    d = unit_start(unit, d0)
    while d <= d1:
        yield d
        d = next_boundary(unit, d)


def unit_label(unit: str, d: date, width: float) -> str:
    if unit == "day":
        return f"{d.day:02d}"
    if unit == "week":
        return f"{d.day:02d}" if width < 44 else f"{d.day:02d}.{d.month:02d}"
    if unit == "month":
        return MONTHS_SHORT[d.month - 1] if width < 110 else f"{MONTHS[d.month - 1]} {d.year}"
    if unit == "quarter":
        roman = ["I", "II", "III", "IV"][(d.month - 1) // 3]
        return roman if width < 40 else f"{roman} кв"
    return str(d.year)


class TimelineHeader(QHeaderView):
    def __init__(self, view: "TimelineView"):
        super().__init__(Qt.Orientation.Horizontal, view)
        self.view = view
        self.setSectionsClickable(False)
        self.setSectionsMovable(False)
        self.setStretchLastSection(False)
        self.setFixedHeight(HEADER_HEIGHT)

    def paintSection(self, painter: QPainter, rect: QRect, logical: int) -> None:
        if logical != TIMELINE_COL or self.view.scale is None:
            return
        t, sc = self.view.tokens, self.view.scale
        painter.save()
        painter.fillRect(rect, QColor(t.panel2))
        vis_l, vis_r = max(rect.left(), 0), min(rect.right(), self.viewport().width())
        d0, d1 = sc.date_at(vis_l - rect.left()), sc.date_at(vis_r - rect.left())
        top_unit, low_unit = sc.tiers()
        half = rect.height() // 2
        font = painter.font()
        # текущая неделя / день
        today = self.view.today
        if self.view.settings.show_current_week:
            xa = rect.left() + sc.x(monday(today))
            painter.fillRect(QRectF(xa, rect.top() + half, 7 * sc.ppd, half), _alpha(self.view.accent, 40))
        for unit, y, h, bold in ((top_unit, rect.top(), half, True), (low_unit, rect.top() + half, half, False)):
            font.setBold(bold)
            font.setPointSizeF(max(7.5, self.view.font().pointSizeF() - (0.5 if bold else 1)))
            painter.setFont(font)
            fm = QFontMetrics(font)
            for d in boundaries(unit, d0, d1):
                x = rect.left() + sc.x(d)
                w = sc.x(next_boundary(unit, d)) - sc.x(d)
                painter.setPen(QPen(QColor(t.grid_strong if bold else t.grid), 1))
                painter.drawLine(QPointF(x, y + (4 if not bold else 0)), QPointF(x, y + h))
                label = unit_label(unit, d, w)
                tw = fm.horizontalAdvance(label)
                if tw + 8 > w and not bold:
                    continue
                painter.setPen(QColor(t.ink if bold else t.ink3))
                if bold:  # подпись верхнего яруса прилипает к левому краю, пока месяц виден
                    lx = max(x + 6, 6) if x < 6 < x + w - tw - 6 else x + 6
                    painter.drawText(QRectF(lx, y, max(w - 8, tw + 4), h), Qt.AlignmentFlag.AlignVCenter, label)
                else:
                    painter.drawText(QRectF(x, y, w, h), Qt.AlignmentFlag.AlignCenter, label)
        painter.setPen(QPen(QColor(t.line), 1))
        painter.drawLine(rect.left(), rect.top() + half, rect.right(), rect.top() + half)
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        if self.view.settings.show_today:
            x = rect.left() + sc.x(today) + sc.ppd / 2
            c = QColor(t.today)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            f = QFont(self.view.font())
            f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
            f.setWeight(QFont.Weight.DemiBold)
            painter.setFont(f)
            label = f"Сегодня, {today.day} {MONTHS_SHORT[today.month - 1]}"
            w = QFontMetrics(f).horizontalAdvance(label) + 14
            pill = QRectF(x - w / 2, rect.top() + half + 3, w, half - 9)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c)
            painter.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, label)
            painter.setBrush(c)
            painter.drawPolygon(QPolygonF([QPointF(x - 4, rect.bottom() - 5), QPointF(x + 4, rect.bottom() - 5),
                                           QPointF(x, rect.bottom())]))
        painter.restore()


def _alpha(c: QColor, a: int) -> QColor:
    c = QColor(c)
    c.setAlpha(a)
    return c


def paint_grid(p: QPainter, v, rect: QRect, x0: float) -> None:
    """Выходные, текущая неделя и линии сетки в полосе rect (x0 — где на экране начинается шкала)."""
    sc, t, s = v.scale, v.tokens, v.settings
    d0, d1 = sc.date_at(rect.left() - x0), sc.date_at(rect.right() - x0)
    top, h = rect.top(), rect.height()
    if s.show_weekends and sc.ppd >= 6:
        wk = QColor(t.weekend)
        d = monday(d0) + timedelta(days=5)
        while d <= d1 + timedelta(days=1):
            p.fillRect(QRectF(x0 + sc.x(d), top, 2 * sc.ppd, h), wk)
            d += timedelta(days=7)
    if s.show_current_week:
        p.fillRect(QRectF(x0 + sc.x(monday(v.today)), top, 7 * sc.ppd, h), _alpha(v.accent, 20))
    top_unit, low_unit = sc.tiers()
    p.setPen(QPen(QColor(t.grid), 1))
    for d in boundaries(low_unit, d0, d1):
        x = round(x0 + sc.x(d)) + 0.5
        p.drawLine(QPointF(x, top), QPointF(x, top + h))
    p.setPen(QPen(QColor(t.grid_strong), 1))
    for d in boundaries(top_unit, d0, d1):
        x = round(x0 + sc.x(d)) + 0.5
        p.drawLine(QPointF(x, top), QPointF(x, top + h))


class TimelineDelegate(QStyledItemDelegate):
    """Полосы одной строки. На экране строку рисует вид (drawRow), при печати — paint() целиком."""

    def __init__(self, view: "TimelineView"):
        super().__init__(view)
        self.view = view
        self._fonts: dict = {}
        self._elided: dict = {}      # подписи у полос, обрезанные по ширине, — считаются один раз

    def sizeHint(self, option, index):
        return QSize(10, self.view.settings.row_height)

    def restyle(self) -> None:
        self._fonts.clear()
        self._elided.clear()

    def _font(self, bold: bool = False) -> tuple[QFont, QFontMetrics]:
        f = self._fonts.get(bold)
        if f is None:
            font = QFont(self.view.font())
            font.setPointSizeF(max(7.5, font.pointSizeF() - 1))
            if bold:
                font.setWeight(QFont.Weight.DemiBold)
            f = self._fonts[bold] = (font, QFontMetrics(font))
        return f

    def paint(self, painter: QPainter, option, index) -> None:
        """Строка целиком (для PDF/PNG): фон, сетка, полосы, «сегодня»."""
        v, node = self.view, index.data(NodeRole)
        if v.scale is None or v.ds is None or node is None:
            return
        ds, sc, t, s = v.ds, v.scale, v.tokens, v.settings
        r = option.rect
        vis_l, vis_r = max(r.left(), 0), min(r.right(), v.viewport().width())
        if vis_r <= vis_l:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QColor(t.section_row if ds.role(node) == "group" else t.panel)
        band = QRect(vis_l, r.top(), vis_r - vis_l + 1, r.height())
        painter.fillRect(band, bg)
        paint_grid(painter, v, band, r.left())
        self.bars(painter, r, node)
        if s.show_today:
            x = r.left() + sc.x(v.today) + sc.ppd / 2
            painter.setPen(QPen(QColor(t.today), 2 if sc.ppd >= 14 else 1.5))
            painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom() + 1))
        painter.setPen(QPen(QColor(t.grid), 1))
        painter.drawLine(QPointF(vis_l, r.bottom() + 0.5), QPointF(vis_r, r.bottom() + 0.5))
        painter.restore()

    # ---------- полосы ----------
    def bars(self, p: QPainter, r: QRect, node: Node) -> None:
        """План — светлая дорожка с рамкой, факт — насыщенная полоса внутри неё.

        Идёт сейчас — факт до сегодня со стрелкой; нет срока — дорожка растворяется; срок прошёл —
        красная штриховка до сегодня; сделали позже срока — красный хвост факта и риска срока.
        """
        v = self.view
        ds, sc, s, t = v.ds, v.scale, v.settings, v.tokens
        role = ds.role(node)
        H, top, x0 = r.height(), r.top(), r.left()
        cy = top + H / 2
        today = v.today
        day = timedelta(days=1)
        red = QColor(t.today)
        preview = v.drag_preview if v.drag_preview and v.drag_preview[0] is node else None

        def X(d: date) -> float:
            return x0 + sc.x(d)

        def span_x(a: date, b: date) -> tuple[float, float]:
            x1 = X(a)
            return x1, max(X(b + day), x1 + 3)

        if role == "group":
            start, end, _ = ds.span(node)
            if not start:
                return
            x1, x2 = span_x(start, end)
            c = QColor(t.group_bar)
            y, h = top + H * 0.42, max(4.0, H * 0.16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRect(QRectF(x1, y, x2 - x1, h))
            for x in (x1, x2):  # «скобки» сводной полосы
                dx = 5 if x == x1 else -5
                p.drawPolygon(QPolygonF([QPointF(x, y), QPointF(x, y + h + 5), QPointF(x + dx, y + h)]))
            n = ds.overdue_count(node)
            if n and s.labels != "none":
                self._label(p, f"просрочено: {n}", x2 + 8, cy, red, bold=True)
            return

        if role == "project":
            ps, pe, derived = ds.span(node)
            open_end = False
            base = v.project_color(node)
        else:
            ps, pe, open_end = ds.task_span(node)
            derived = False
            proj = v.project_of(node) if s.task_colors == "project" else None
            base = v.project_color(proj) if proj is not None else \
                status_color("overdue" if ds.is_overdue(node) else ds.status_kind(node), s, t)
        if preview:
            ps, pe, open_end, derived = preview[1], preview[2], False, False
        if not ps:
            return
        overdue = ds.is_overdue(node)
        fill, edge, fact = plan_colors(base, t)

        edited = ds.is_edited(node)
        if role == "task" and node.raw.get("milestone"):  # веха — ромб в дату начала
            cx, rr = X(ps) + sc.ppd / 2, H * 0.28
            p.setPen(QPen(edge, 1.3))
            p.setBrush(fact)
            p.drawPolygon(QPolygonF([QPointF(cx, cy - rr), QPointF(cx + rr, cy), QPointF(cx, cy + rr),
                                     QPointF(cx - rr, cy)]))
            lx = cx + rr + 6
            if edited:
                lx = self._edited_chip(p, lx, cy) + 6
            if s.labels == "names":
                self._label(p, node.name, lx, cy)
            return

        fast = getattr(v, "fast", False)   # режим картошки: без скруглений, градиентов и сглаживания
        track_h = H * (0.64 if role == "project" else 0.58)
        ty = cy - track_h / 2
        rad = 0 if fast else min(s.bar_radius, track_h / 2)
        x1, x2 = span_x(ps, pe)
        path = QPainterPath()
        if fast:
            path.addRect(QRectF(x1, ty, x2 - x1, track_h))
        else:
            path.addRoundedRect(QRectF(x1, ty, x2 - x1, track_h), rad, rad)
        # план
        p.setPen(Qt.PenStyle.NoPen)
        if open_end and fast:
            p.setBrush(_alpha(fill, 150))
            p.drawPath(path)
            pen = QPen(edge, 1.2, Qt.PenStyle.DashLine)
        elif open_end:  # срока нет — дорожка растворяется
            fade = max(x1, x2 - 90)
            g = QLinearGradient(fade, 0, x2, 0)
            g.setColorAt(0, fill)
            g.setColorAt(1, _alpha(fill, 0))
            p.setBrush(QBrush(g))
            p.drawPath(path)
            ge = QLinearGradient(fade, 0, x2, 0)
            ge.setColorAt(0, edge)
            ge.setColorAt(1, _alpha(edge, 0))
            pen = QPen(QBrush(ge), 1.2)
            pen.setStyle(Qt.PenStyle.DashLine)
        else:
            p.setBrush(fill)
            p.drawPath(path)
            pen = QPen(edge, 1.3)
            if derived:  # план не задан — собран по заданиям
                pen.setStyle(Qt.PenStyle.DashLine)
        if preview:
            pen = QPen(v.accent, 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(pen)
        p.drawPath(path)
        right = x2
        if edited and not preview:  # план до вашей правки — пунктиром поверх: видно, где был срок в источнике
            was = self._source_plan(node, role)
            if was is not None and was != (ps, pe):
                wx1, wx2 = span_x(*was)
                p.setPen(QPen(_alpha(QColor(t.ink3), 190), 1.2, Qt.PenStyle.DashLine))
                p.drawRoundedRect(QRectF(wx1, ty - 1.5, wx2 - wx1, track_h + 3), rad, rad)
                right = max(right, wx2)

        # факт
        fe_real = ds.get(node, "factEnd")
        if s.show_fact:
            fs = ds.get(node, "factStart")
            going = bool(fs) and not fe_real and not ds.is_done(node)
            fe = fe_real or (max(fs, today) if going else None)
            if fs and fe:
                fx1 = X(fs)
                fx2 = X(today) + sc.ppd / 2 if going else X(fe + day)
                fx2 = max(fx2, fx1 + 3)
                fh = max(5.0, track_h * 0.42)
                fy = cy - fh / 2
                fr = 0 if fast else fh / 2
                limit = x2 if pe and not open_end else None
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fact)
                p.drawRoundedRect(QRectF(fx1, fy, fx2 - fx1, fh), fr, fr)
                tip = fact
                if limit is not None and fx2 > limit + 1:  # всё, что после срока, — красным
                    p.setBrush(red)
                    p.drawRoundedRect(QRectF(max(fx1, limit), fy, fx2 - max(fx1, limit), fh), fr, fr)
                    tip = red
                if going:  # идёт сейчас — стрелка у «сегодня»
                    p.setBrush(tip)
                    p.drawPolygon(QPolygonF([QPointF(fx2 - 1, fy - 2.5), QPointF(fx2 + 6, cy),
                                             QPointF(fx2 - 1, fy + fh + 2.5)]))
                right = max(right, fx2 + (7 if going else 0))

        # срок прошёл, а задача не закрыта: штриховка от срока до сегодня
        if overdue and pe and not open_end and pe < today:
            ox2 = X(today + day)
            if ox2 > x2:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(_alpha(red, 170), Qt.BrushStyle.BDiagPattern))
                p.drawRect(QRectF(x2, ty + 1, ox2 - x2, track_h - 2))
                right = max(right, ox2)
        # риска срока — если срок сорван
        if role == "task" and pe and not open_end and (overdue or (fe_real and fe_real > pe)):
            p.setPen(QPen(red, 1.6))
            p.drawLine(QPointF(x2, ty - 3), QPointF(x2, ty + track_h + 3))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(red)
            p.drawPolygon(QPolygonF([QPointF(x2, ty - 4), QPointF(x2 + 6, ty - 1.5), QPointF(x2, ty + 1)]))

        lx = right + 6
        if edited:  # «изм.» — у строки есть ваши правки, в источнике их ещё нет
            lx = self._edited_chip(p, lx, cy) + 6
        if s.show_scale_marks and role == "task":  # цветные точки: приоритет, срочность, влияние
            for fld in ("priority", "urgency", "impact"):
                item = ds.scale_item(node, fld)
                if item:
                    c = QColor(item["color"]) if item.get("color") else scale_color(
                        item.get("order", 1), ds.scale_top(fld), s, t)
                    p.setPen(QPen(tone(c, -0.3), 1))
                    p.setBrush(c)
                    p.drawEllipse(QPointF(lx + 3, cy), 3.5, 3.5)
                    lx += 10
            if lx > right + 6:
                lx += 2
        if s.labels != "none":
            if role == "project":
                label = ds.progress_label(node)
                if s.labels == "names":
                    label = "  ·  ".join(x for x in (node.name, label) if x)
                if label:
                    lx = self._label(p, label, lx, cy)
            elif s.labels == "names":
                lx = self._label(p, node.name, lx, cy)
            if overdue and role == "task" and pe:
                self._label(p, f"+{(today - pe).days} дн.", lx + 2, cy, red, bold=True)

    @staticmethod
    def _source_plan(node: Node, role: str) -> tuple[date, date] | None:
        """План, как его прислал источник, — если его есть с чем сравнить."""
        start = node.source("planStart") or (node.source("factStart") if role == "task" else None)
        end = node.source("planEnd")
        return (start, max(start, end)) if start and end else None

    def _edited_chip(self, p: QPainter, x: float, cy: float) -> float:
        """Метка «изм.» акцентного цвета, как в макете. Возвращает правый край."""
        font, fm = self._font(True)
        accent = QColor(self.view.accent)
        text = "изм."
        w, h = fm.horizontalAdvance(text) + 10, fm.height()
        box = QRectF(x, cy - h / 2, w, h)
        border = tone(accent, 0.18) if self.view.tokens.dark else accent   # на тёмном фоне тёмный акцент — с каймой
        p.setPen(QPen(border, 1))
        p.setBrush(accent)
        r = 0 if getattr(self.view, "fast", False) else 4
        p.drawRoundedRect(box, r, r)
        p.setFont(font)
        p.setPen(QColor("#FFFFFF") if accent.lightnessF() < 0.62 else QColor("#1C1E23"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        return x + w

    def _label(self, p: QPainter, text: str, x: float, cy: float, color: QColor | None = None,
               bold: bool = False) -> float:
        """Подпись на светлой подложке — читается поверх сетки и соседних полос. Возвращает правый край."""
        if not text:
            return x
        font, fm = self._font(bold)
        key = (text, bold)
        cached = self._elided.get(key)
        if cached is None:
            short = fm.elidedText(text, Qt.TextElideMode.ElideRight, 360)
            cached = self._elided[key] = (short, fm.horizontalAdvance(short))
        text, tw = cached
        w, h = tw + 10, fm.height() + 2
        box = QRectF(x, cy - h / 2, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(QColor(self.view.tokens.panel), 215))
        r = 0 if getattr(self.view, "fast", False) else h / 2
        p.drawRoundedRect(box, r, r)
        p.setFont(font)
        p.setPen(color or QColor(self.view.tokens.ink2))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        return x + w


class TimelineView(QTreeView):
    """Шкала: та же модель, скрыты все колонки, кроме шкалы. Перетаскивание полос меняет даты."""

    datesEdited = Signal(object, object, object)       # узел, новое начало, новый конец
    editRequested = Signal(object)                      # узел
    contextRequested = Signal(object, QPoint)           # узел, глобальная точка
    zoomChanged = Signal(float)                         # после окончания масштабирования
    hovered = Signal(object)                            # узел под мышью или None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ds: Dataset | None = None
        self.scale: TimeScale | None = None
        self.settings = None
        self.tokens: Tokens | None = None
        self.accent = QColor("#2F5FD0")
        self.project_color = lambda node: QColor("#A9C4F5")
        self.today = date.today()
        self.drag_preview = None      # (узел, начало, конец) во время перетаскивания
        self._drag = None
        self.hover_node = None
        self.animations = True
        self.fast = False             # режим картошки: полосы без сглаживания, скруглений и градиентов
        self.smooth_v = None          # SmoothScroll общей вертикальной прокрутки (задаёт окно)
        self.smooth_h = None
        self.data_start = self.data_end = date.today()
        self._zoom_anchor = None
        self._zoom_target = None
        self._zoom_anim = QVariantAnimation(self)
        self._zoom_anim.setDuration(320)
        self._zoom_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._zoom_anim.valueChanged.connect(lambda v: self.zoom_to(math.exp(v), self._zoom_anchor))
        self._zoom_anim.finished.connect(self._zoom_finished)
        self.colors: dict = {}
        self._strip: QPixmap | None = None
        self._strip_key = None
        self._x0 = 0
        self.setHeader(TimelineHeader(self))
        self.delegate = TimelineDelegate(self)
        self.setItemDelegateForColumn(TIMELINE_COL, self.delegate)
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setItemsExpandable(False)
        self.setExpandsOnDoubleClick(False)
        # строки закрашивают всё сами — прокрутка сдвигает готовую картинку и дорисовывает только новую полоску
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setUniformRowHeights(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setMouseTracking(True)
        self.setAllColumnsShowFocus(True)
        self.pan = DragPan(self)       # зажатое колесо — тянуть диаграмму в любую сторону
        self.pan.started.connect(lambda: self.hovered.emit(None))

    def setModel(self, model) -> None:
        super().setModel(model)
        for c in range(len(COLUMNS)):
            self.setColumnHidden(c, c != TIMELINE_COL)
        self.header().setSectionResizeMode(TIMELINE_COL, QHeaderView.ResizeMode.Fixed)

    def _row_rect(self, node: Node):
        proxy = self.model()
        src = proxy.sourceModel()
        idx = proxy.mapFromSource(src.index_for(node, TIMELINE_COL))
        if not idx.isValid():
            return None
        r = self.visualRect(idx)
        return r if r.isValid() and r.height() > 0 else None

    def restyle(self) -> None:
        """Тема, акцент или шрифт поменялись."""
        if self.tokens is not None:
            self.colors = row_colors(self.tokens, self.accent)
        self._strip_key = None
        self.delegate.restyle()

    def _grid_strip(self, x0: int, h: int) -> QPixmap:
        """Выходные, неделя и сетка на высоту одной строки — рисуются один раз на кадр и копируются в каждую строку."""
        vw = self.viewport().width()
        dpr = self.devicePixelRatioF()
        key = (x0, vw, h, dpr, self.scale.ppd, self.scale.start, self.today, id(self.tokens), self.accent.rgba(),
               self.settings.show_weekends, self.settings.show_current_week)
        if key != self._strip_key:
            pm = QPixmap(int(vw * dpr) + 1, int(h * dpr) + 1)
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            paint_grid(p, self, QRect(0, 0, vw, h), x0)
            p.end()
            self._strip, self._strip_key = pm, key
        return self._strip

    def paintEvent(self, event) -> None:
        ready = self.ds is not None and self.scale is not None and self.tokens is not None
        if ready and not self.colors:
            self.restyle()
        self._x0 = self.header().sectionViewportPosition(TIMELINE_COL)
        if self.colors:  # поле непрозрачное: пустое место под строками закрашиваем сами
            p = QPainter(self.viewport())
            p.fillRect(event.rect(), self.colors["panel"])
            p.end()
        super().paintEvent(event)   # строки — в drawRow
        if not ready:
            return
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing, not self.fast)
        vh = self.viewport().height()
        if self.settings.show_today:
            x = self._x0 + self.scale.x(self.today) + self.scale.ppd / 2
            p.setPen(QPen(QColor(self.tokens.today), 2 if self.scale.ppd >= 14 else 1.5))
            p.drawLine(QPointF(x, 0), QPointF(x, vh))
        if getattr(self.ds, "has_links", False):
            self._links(p, vh)
        p.end()

    def drawRow(self, painter: QPainter, option, index) -> None:
        node = index.data(NodeRole)
        if self.ds is None or self.scale is None or node is None or not self.colors:
            return
        c = self.colors
        top, H = option.rect.top(), option.rect.height()
        vw = self.viewport().width()
        role = self.ds.role(node)
        row = QRect(0, top, vw, H)
        if role == "group":
            painter.fillRect(row, c["group"])
        elif self.settings.zebra and ((top + self.verticalOffset()) // max(1, H)) % 2:
            painter.fillRect(row, c["zebra"])
        else:
            painter.fillRect(row, c["panel"])
        painter.drawPixmap(0, top, self._grid_strip(self._x0, H))
        if self.selectionModel() is not None and self.selectionModel().isSelected(index):
            painter.fillRect(row, c["select_overlay"])
        elif node is self.hover_node:
            painter.fillRect(row, c["hover"])
        if role == "group":
            painter.setPen(QPen(QColor(self.tokens.line), 1))
            painter.drawLine(0, top + H - 1, vw, top + H - 1)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, not self.fast)
        self.delegate.bars(painter, QRect(self._x0, top, self.scale.width, H), node)
        painter.restore()

    def row_rect(self, node: Node) -> QRect | None:
        r = self._row_rect(node)
        return QRect(0, r.top(), self.viewport().width(), r.height()) if r is not None else None

    def _links(self, p: QPainter, vh: int) -> None:
        ink = QColor(self.tokens.ink3)
        p.setPen(QPen(ink, 1.3))
        for node in self.ds.nodes.values():
            links = node.raw.get("predecessors") or []
            if not links:
                continue
            r2 = self._row_rect(node)
            if r2 is None:
                continue
            s2, _e2, _o = self.ds.task_span(node) if self.ds.role(node) == "task" else self.ds.span(node)
            for link in links:
                pred = self.ds.nodes.get(link.get("id"))
                r1 = self._row_rect(pred) if pred is not None else None
                if r1 is None or not s2:
                    continue
                if (r1.bottom() < 0 and r2.bottom() < 0) or (r1.top() > vh and r2.top() > vh):
                    continue
                _s1, e1, _o1 = self.ds.task_span(pred) if self.ds.role(pred) == "task" else self.ds.span(pred)
                if not e1:
                    continue
                xa = r1.left() + self.scale.x(e1 + timedelta(days=1))
                xb = r2.left() + self.scale.x(s2)
                ya, yb = r1.center().y(), r2.center().y()
                path = QPainterPath(QPointF(xa, ya))
                mid = max(xa + 8, xb - 10) if xb - 10 > xa + 8 else xa + 8
                path.lineTo(mid, ya)
                path.lineTo(mid, yb)
                path.lineTo(xb - 1, yb)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
                p.setBrush(ink)
                p.drawPolygon(QPolygonF([QPointF(xb, yb), QPointF(xb - 6, yb - 3.5), QPointF(xb - 6, yb + 3.5)]))

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        """Вертикально — всегда сдвиг готовой картинки. Qt в попиксельном режиме при шаге больше числа
        видимых строк перерисовывает окно целиком — на быстрой прокрутке это заметно."""
        if dx == 0 and dy:
            self.viewport().scroll(0, dy)
        else:
            super().scrollContentsBy(dx, dy)
            if dx:  # подпись месяца у левого края шапки «липкая»: сдвиг готовой картинки оставил бы её копии
                self.header().viewport().update()

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:
        """Показать строку — только по вертикали: по горизонтали шкала остаётся там, где её оставил человек."""
        value = self.horizontalScrollBar().value()
        super().scrollTo(index, hint)
        self.horizontalScrollBar().setValue(value)

    def project_of(self, node: Node) -> Node | None:
        n = node.parent
        while n is not None and self.ds.role(n) != "project":
            n = n.parent
        return n

    def set_scale(self, scale: TimeScale) -> None:
        self.scale = scale
        self.header().resizeSection(TIMELINE_COL, scale.width)
        self.updateGeometries()  # диапазон прокрутки — сразу, а не при следующей отрисовке
        self.viewport().update()
        self.header().viewport().update()

    def scroll_to_date(self, d: date, fraction: float = 0.33, animate: bool = False) -> None:
        if self.scale:
            self.executeDelayedItemsLayout()  # иначе отложенная перекладка строк сбросит прокрутку в ноль
            self.updateGeometries()
            value = int(self.scale.x(d) - self.viewport().width() * fraction)
            if animate and self.smooth_h:
                self.smooth_h.scroll_to(value)
            else:
                self.horizontalScrollBar().setValue(value)

    def set_data_range(self, start: date, end: date) -> None:
        self.data_start, self.data_end = start, end

    def make_scale(self, ppd: float) -> TimeScale:
        """Шкала по данным, но не короче окна: иначе при мелком масштабе справа будет пустой «разрыв»."""
        min_days = int(self.viewport().width() / ppd) + 45
        end = max(self.data_end, self.data_start + timedelta(days=min_days))
        return TimeScale(self.data_start, end, ppd)

    def zoom_to(self, ppd: float, anchor_x: float | None = None) -> None:
        """Мгновенно: дата под anchor_x (или в центре) остаётся на месте."""
        if not self.scale:
            return
        ppd = max(MIN_PPD, min(MAX_PPD, ppd))
        ax = self.viewport().width() / 2 if anchor_x is None else anchor_x
        hs = self.horizontalScrollBar()
        anchor_days = (hs.value() + ax) / self.scale.ppd
        self.set_scale(self.make_scale(ppd))
        hs.setValue(int(anchor_days * ppd - ax))

    def animate_zoom(self, ppd: float, anchor_x: float | None = None) -> None:
        """Плавно к новому масштабу — по логарифму, чтобы и «день», и «год» менялись одинаково мягко."""
        if not self.scale:
            return
        ppd = max(MIN_PPD, min(MAX_PPD, ppd))
        if self.smooth_h:
            self.smooth_h.stop()
        if not self.animations:
            self.zoom_to(ppd, anchor_x)
            self.zoomChanged.emit(self.scale.ppd)
            return
        self._zoom_anchor, self._zoom_target = anchor_x, ppd
        self._zoom_anim.stop()
        self._zoom_anim.setStartValue(math.log(self.scale.ppd))
        self._zoom_anim.setEndValue(math.log(ppd))
        self._zoom_anim.start()

    def _zoom_finished(self) -> None:
        self._zoom_target = None
        self.zoomChanged.emit(self.scale.ppd)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.scale and self.make_scale(self.scale.ppd).end > self.scale.end:
            value = self.horizontalScrollBar().value()
            self.set_scale(self.make_scale(self.scale.ppd))
            self.horizontalScrollBar().setValue(value)

    # ---------- геометрия полос ----------
    def _node_at(self, pos: QPoint):
        idx = self.indexAt(pos)
        return (idx.data(NodeRole), self.visualRect(idx)) if idx.isValid() else (None, None)

    def _plan_bar(self, node: Node, rect: QRect):
        """(x1, x2, начало, конец) плановой полосы, которую можно тянуть, или None."""
        ds = self.ds
        role = ds.role(node)
        if role == "group":
            return None
        if role == "project":
            s, e, derived = ds.span(node)
        else:
            s, e, _open = ds.task_span(node)
        if not s or not e:
            return None
        x1 = rect.left() + self.scale.x(s)
        x2 = rect.left() + self.scale.x(e + timedelta(days=1))
        return x1, max(x2, x1 + 3), s, e

    def _hit(self, pos: QPoint):
        node, rect = self._node_at(pos)
        if node is None or self.scale is None:
            return None
        bar = self._plan_bar(node, rect)
        if not bar:
            return None
        x1, x2, s, e = bar
        x = pos.x()
        if abs(x - x1) <= 6:
            return node, "start", s, e
        if abs(x - x2) <= 6:
            return node, "end", s, e
        if x1 < x < x2:
            return node, "move", s, e
        return None

    # ---------- мышь ----------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.ds is not None:
            hit = self._hit(event.position().toPoint())
            if hit:
                node, mode, s, e = hit
                self.setCurrentIndex(self.indexAt(event.position().toPoint()))
                self._drag = {"node": node, "mode": mode, "s": s, "e": e, "x": event.position().x()}
                self.setCursor(Qt.CursorShape.ClosedHandCursor if mode == "move" else Qt.CursorShape.SizeHorCursor)
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._drag:
            d = self._drag
            days = round((event.position().x() - d["x"]) / self.scale.ppd)
            s, e = d["s"], d["e"]
            if d["mode"] == "move":
                s, e = s + timedelta(days=days), e + timedelta(days=days)
            elif d["mode"] == "start":
                s = min(s + timedelta(days=days), e)
            else:
                e = max(e + timedelta(days=days), s)
            self.drag_preview = (d["node"], s, e)
            self.viewport().update()
            shift = (s - d["s"]).days if d["mode"] != "end" else (e - d["e"]).days
            sign = f"{shift:+d} дн." if shift else "без изменений"
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"<b>{fmt(s, False)} → {fmt(e, False)}</b><br>{(e - s).days + 1} дн. · {sign}", self)
            return
        node, _rect = self._node_at(pos)
        if node is not self.hover_node:
            self.hovered.emit(node)
        hit = self._hit(pos) if self.ds is not None else None
        if hit is None:
            self.unsetCursor()
        elif hit[1] == "move":
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self.hovered.emit(None)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag:
            d, preview = self._drag, self.drag_preview
            self._drag, self.drag_preview = None, None
            self.unsetCursor()
            QToolTip.hideText()
            if preview and (preview[1] != d["s"] or preview[2] != d["e"]):
                self.datesEdited.emit(d["node"], preview[1], preview[2])
            self.viewport().update()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        node, _rect = self._node_at(event.position().toPoint())
        if node is not None and self.ds.role(node) != "group":
            self.editRequested.emit(node)
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        node, _rect = self._node_at(event.pos())
        if node is not None:
            self.setCurrentIndex(self.indexAt(event.pos()))
            self.contextRequested.emit(node, event.globalPos())

    def wheelEvent(self, event) -> None:
        mods = event.modifiers()
        dy, dx = event.angleDelta().y(), event.angleDelta().x()
        pixel = not event.pixelDelta().isNull()  # тачпад прокручивает плавно сам
        if mods & Qt.KeyboardModifier.ControlModifier:
            steps = dy / 120
            if steps and self.scale:
                base = self._zoom_target or self.scale.ppd
                self.animate_zoom(base * (1.25 ** steps), event.position().x())
            return
        if mods & Qt.KeyboardModifier.ShiftModifier or (dx and not dy):
            delta = dy or dx
            if self.smooth_h and not pixel:
                self.smooth_h.scroll_by(-delta / 120 * 160)
            else:
                hs = self.horizontalScrollBar()
                hs.setValue(hs.value() - int(delta))
            return
        if self.smooth_v and not pixel:
            self.smooth_v.scroll_by(-dy / 120 * 3 * self.settings.row_height)
            return
        super().wheelEvent(event)

    def viewportEvent(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:   # подсказки — карточкой при наведении
            return True
        return super().viewportEvent(event)
