"""Картинка диаграммы для PNG и PDF: таблица слева, шкала справа, на светлой «бумаге».

Полосы рисует тот же TimelineDelegate, что и на экране, — картинка совпадает с программой.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from PySide6.QtCore import QMarginsF, QModelIndex, QObject, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QImage, QPageLayout, QPageSize, QPainter, QPainterPath,
                           QPdfWriter, QPen, QPolygonF)
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

from ..model import Node, monday
from .theme import THEMES, project_palette
from .timeline import (MONTHS, TimeScale, TimelineDelegate, TimelineView, boundaries, next_boundary,
                       unit_label)
from .tree_model import COLUMNS, NodeRole

TITLES = dict(COLUMNS)
PRINT_COLUMNS = ("name", "number", "executors", "status", "progress", "due", "start", "end", "fact_start", "fact_end",
                 "priority", "urgency", "impact", "initiator")
WIDTHS = {"name": 340, "number": 70, "executors": 150, "status": 100, "progress": 88, "due": 116, "start": 74, "end": 74,
          "fact_start": 74, "fact_end": 74, "priority": 84, "urgency": 84, "impact": 84, "initiator": 120}
HEADER_H = 46
TITLE_H = 44
MAX_PNG_PIXELS = 120_000_000   # ~480 МБ памяти — больше не рисуем, предлагаем PDF


@dataclass
class RenderOptions:
    scope: str = "visible"   # visible — как на экране (фильтры, раскрытие); all — всё раскрыто, без фильтров
    period: str = "data"     # data — весь период строк; screen — участок шкалы, видимый на экране
    page: str = "A4"         # для PDF: A4 | A3
    title: str = ""


class _Index:
    """Делегату шкалы от индекса нужен только узел."""

    def __init__(self, node: Node):
        self.node = node

    def data(self, role=None):
        return self.node


class _Paper(QObject):
    """То, что TimelineDelegate берёт у вида шкалы, — без самого вида."""

    project_of = TimelineView.project_of

    def __init__(self, win, scale: TimeScale, width: int):
        super().__init__()
        self.ds, self.scale, self.settings = win.ds, scale, win.settings
        self.tokens = THEMES["light"]
        self.today = win.timeline.today
        self.accent = QColor(win.settings.accent)
        self.hover_node = self.drag_preview = None
        self._font = QFont(win.timeline.font())
        self._width = width
        palette = project_palette(win.settings, self.tokens)

        def color(node: Node) -> QColor:
            c = win.ds.get(node, "color")
            if c:
                return QColor(c)
            return palette[win.ds.project_index.get(node.id, 0) % len(palette)]

        self.project_color = color

    def font(self) -> QFont:
        return self._font

    def viewport(self):
        return self

    def width(self) -> int:
        return self._width


def collect_rows(win, scope: str) -> list[Node]:
    if scope == "all":
        return list(win.ds.walk())
    out: list[Node] = []

    def walk(parent: QModelIndex) -> None:
        for r in range(win.proxy.rowCount(parent)):
            idx = win.proxy.index(r, 0, parent)
            out.append(idx.data(NodeRole))
            if win.table.isExpanded(idx):
                walk(idx)

    walk(QModelIndex())
    return out


class Chart:
    def __init__(self, win, opts: RenderOptions, fit_width: float | None = None):
        self.win, self.opts, self.ds = win, opts, win.ds
        self.rows = collect_rows(win, opts.scope)
        self.t = THEMES["light"]
        self.row_h = max(20, win.settings.row_height)
        self.title_h = TITLE_H if opts.title else 0
        hidden = set(win.settings.hidden_columns)
        self.columns = [k for k in PRINT_COLUMNS if k == "name" or k not in hidden]
        self.left_w = sum(WIDTHS[k] for k in self.columns)
        tl = win.timeline
        if opts.period == "screen" and tl.scale is not None:
            x = tl.horizontalScrollBar().value()
            lo, hi = tl.scale.date_at(x), tl.scale.date_at(x + tl.viewport().width())
        else:
            lo, hi = self._data_range()
        days = (hi - monday(lo)).days + 1
        if fit_width:  # PDF: весь период по ширине страницы
            ppd = max(0.08, (fit_width - self.left_w) / days)
        else:
            ppd = tl.scale.ppd if tl.scale else 5.0
            ppd = min(ppd, 12000 / days)
        self.scale = TimeScale(lo, hi, ppd)
        self.width = self.left_w + self.scale.width
        self.paper = _Paper(win, self.scale, self.width)
        self.delegate = TimelineDelegate(self.paper)

    def _data_range(self) -> tuple[date, date]:
        ds, lo, hi = self.ds, None, None
        for n in self.rows:
            s, e, _ = ds.task_span(n) if ds.role(n) == "task" else ds.span(n)
            fs, fe = ds.fact_span(n)
            for d in (s, e, fs, fe):
                if d:
                    lo = d if lo is None or d < lo else lo
                    hi = d if hi is None or d > hi else hi
        today = self.win.timeline.today
        if lo is None:
            lo, hi = today - timedelta(days=30), today + timedelta(days=30)
        return lo - timedelta(days=7), hi + timedelta(days=14)

    def height_for(self, n_rows: int) -> int:
        return self.title_h + HEADER_H + n_rows * self.row_h

    # ---------- рисование ----------
    def paint(self, p: QPainter, i0: int, i1: int, page: tuple[int, int] | None = None) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.fillRect(QRect(0, 0, self.width, self.height_for(i1 - i0)), QColor("#FFFFFF"))
        y = 0
        if self.title_h:
            self._title(p, page)
            y += self.title_h
        self._table_header(p, y)
        self._time_header(p, y)
        y += HEADER_H
        top = y
        ys = {}
        for i in range(i0, i1):
            node = self.rows[i]
            ys[node.id] = y
            self._table_row(p, node, y)
            opt = QStyleOptionViewItem()
            opt.rect = QRect(self.left_w, y, self.scale.width, self.row_h)
            opt.state = QStyle.StateFlag.State_None
            self.delegate.paint(p, opt, _Index(node))
            y += self.row_h
        self._links(p, ys)
        p.setPen(QPen(QColor(self.t.line), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(self.left_w - 0.5, top - HEADER_H), QPointF(self.left_w - 0.5, y))
        p.drawRect(QRectF(0.5, top - HEADER_H + 0.5, self.width - 1, y - top + HEADER_H - 1))

    def _title(self, p: QPainter, page) -> None:
        f = QFont(self.paper.font())
        f.setPointSizeF(f.pointSizeF() + 4)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(self.t.ink))
        p.drawText(QRectF(4, 0, self.width * 0.7, TITLE_H - 10), Qt.AlignmentFlag.AlignVCenter, self.opts.title)
        f.setPointSizeF(self.paper.font().pointSizeF())
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(self.t.ink3))
        right = f"Сформировано {datetime.now():%d.%m.%Y %H:%M}"
        if page and page[1] > 1:
            right += f"  ·  лист {page[0]} из {page[1]}"
        p.drawText(QRectF(0, 0, self.width - 4, TITLE_H - 10),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, right)

    def _table_header(self, p: QPainter, y: int) -> None:
        t = self.t
        p.fillRect(QRect(0, y, self.left_w, HEADER_H), QColor(t.panel2))
        f = QFont(self.paper.font())
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() - 0.5)
        p.setFont(f)
        x = 0
        for k in self.columns:
            w = WIDTHS[k]
            p.setPen(QColor(t.ink2))
            p.drawText(QRectF(x + 8, y, w - 12, HEADER_H), Qt.AlignmentFlag.AlignVCenter, TITLES.get(k, k))
            x += w
            p.setPen(QPen(QColor(t.line), 1))
            p.drawLine(QPointF(x - 0.5, y + 8), QPointF(x - 0.5, y + HEADER_H - 8))
        p.setPen(QPen(QColor(t.line), 1))
        p.drawLine(QPointF(0, y + HEADER_H - 0.5), QPointF(self.left_w, y + HEADER_H - 0.5))

    def _time_header(self, p: QPainter, y: int) -> None:
        t, sc, x0 = self.t, self.scale, self.left_w
        rect = QRect(x0, y, sc.width, HEADER_H)
        p.fillRect(rect, QColor(t.panel2))
        top_unit, low_unit = sc.tiers()
        half = HEADER_H // 2
        font = QFont(self.paper.font())
        for unit, yy, h, bold in ((top_unit, y, half, True), (low_unit, y + half, half, False)):
            font.setBold(bold)
            font.setPointSizeF(max(7.0, self.paper.font().pointSizeF() - (0.5 if bold else 1)))
            p.setFont(font)
            fm = QFontMetrics(font)
            for d in boundaries(unit, sc.start, sc.end):
                x = x0 + sc.x(d)
                w = sc.x(next_boundary(unit, d)) - sc.x(d)
                if x + w < x0:
                    continue
                p.setPen(QPen(QColor(t.grid_strong if bold else t.grid), 1))
                if x >= x0:
                    p.drawLine(QPointF(x, yy + (0 if bold else 4)), QPointF(x, yy + h))
                label = unit_label(unit, d, w)
                if unit == "month" and bold and w >= 110:
                    label = f"{MONTHS[d.month - 1]} {d.year}"
                tw = fm.horizontalAdvance(label)
                if tw + 8 > w and not bold:
                    continue
                p.setPen(QColor(t.ink if bold else t.ink3))
                lx = max(x, x0)
                if bold and tw + 10 > x + w - lx:   # начало шкалы: от месяца виден хвостик — без подписи
                    continue
                if bold:
                    p.drawText(QRectF(lx + 6, yy, max(w - 8, tw + 4), h), Qt.AlignmentFlag.AlignVCenter, label)
                else:
                    p.drawText(QRectF(x, yy, w, h), Qt.AlignmentFlag.AlignCenter, label)
        p.setPen(QPen(QColor(t.line), 1))
        p.drawLine(QPointF(x0, y + half), QPointF(x0 + sc.width, y + half))
        p.drawLine(QPointF(x0, y + HEADER_H - 0.5), QPointF(x0 + sc.width, y + HEADER_H - 0.5))
        if self.win.settings.show_today and sc.start <= self.paper.today <= sc.end:
            x = x0 + sc.x(self.paper.today) + sc.ppd / 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(t.today))
            p.drawPolygon(QPolygonF([QPointF(x - 5, y + HEADER_H - 6), QPointF(x + 5, y + HEADER_H - 6),
                                     QPointF(x, y + HEADER_H)]))

    def _table_row(self, p: QPainter, node: Node, y: int) -> None:
        t, ds, H = self.t, self.ds, self.row_h
        role = ds.role(node)
        p.fillRect(QRect(0, y, self.left_w, H), QColor(t.section_row if role == "group" else "#FFFFFF"))
        base = QFont(self.paper.font())
        small = QFont(base)
        small.setPointSizeF(base.pointSizeF() - 0.5)
        x = 0
        for k in self.columns:
            w = WIDTHS[k]
            if k == "name":
                ind = 8 + node.depth * 16
                if role == "project":
                    c = self.paper.project_color(node)
                    p.setPen(QPen(c.darker(125), 1))
                    p.setBrush(c)
                    p.drawRoundedRect(QRectF(ind, y + H / 2 - 5, 10, 10), 3, 3)
                    ind += 16
                f = QFont(base)
                f.setBold(role != "task")
                p.setFont(f)
                p.setPen(QColor(t.ink))
                text = QFontMetrics(f).elidedText(node.name, Qt.TextElideMode.ElideRight, int(w - ind - 6))
                p.drawText(QRectF(ind, y, w - ind - 4, H), Qt.AlignmentFlag.AlignVCenter, text)
            else:
                p.setFont(small)
                p.setPen(QColor(t.ink2))
                text = self.win.model.text(node, k)
                text = QFontMetrics(small).elidedText(text, Qt.TextElideMode.ElideRight, w - 14)
                p.drawText(QRectF(x + 8, y, w - 12, H), Qt.AlignmentFlag.AlignVCenter, text)
            x += w
        p.setPen(QPen(QColor(t.grid), 1))
        p.drawLine(QPointF(0, y + H - 0.5), QPointF(self.left_w, y + H - 0.5))

    def _links(self, p: QPainter, ys: dict[str, int]) -> None:
        ds, sc, x0 = self.ds, self.scale, self.left_w
        if not ds.has_links:
            return
        ink = QColor(self.t.ink3)
        p.setPen(QPen(ink, 1.2))
        span = lambda n: ds.task_span(n) if ds.role(n) == "task" else ds.span(n)  # noqa: E731
        for nid, y2 in ys.items():
            node = ds.nodes[nid]
            s2 = span(node)[0]
            for link in node.raw.get("predecessors") or []:
                y1 = ys.get(link.get("id"))
                pred = ds.nodes.get(link.get("id"))
                if y1 is None or pred is None or not s2 or not span(pred)[1]:
                    continue
                xa, xb = x0 + sc.x(span(pred)[1] + timedelta(days=1)), x0 + sc.x(s2)
                ya, yb = y1 + self.row_h / 2, y2 + self.row_h / 2
                mid = max(xa + 8, xb - 10) if xb - 10 > xa + 8 else xa + 8
                path = QPainterPath(QPointF(xa, ya))
                path.lineTo(mid, ya)
                path.lineTo(mid, yb)
                path.lineTo(xb - 1, yb)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
                p.setBrush(ink)
                p.drawPolygon(QPolygonF([QPointF(xb, yb), QPointF(xb - 6, yb - 3.5), QPointF(xb - 6, yb + 3.5)]))


def save_png(win, path: str, opts: RenderOptions) -> int:
    chart = Chart(win, opts)
    h = chart.height_for(len(chart.rows))
    if chart.width * h > MAX_PNG_PIXELS:
        raise ValueError(f"Картинка получается слишком большой ({chart.width}×{h} точек). "
                         "Сохраните в PDF — он разобьёт диаграмму на листы — или сверните часть строк.")
    img = QImage(chart.width, h, QImage.Format.Format_ARGB32)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    chart.paint(p, 0, len(chart.rows))
    p.end()
    if not img.save(path, "PNG"):
        raise ValueError("Не удалось записать файл.")
    return len(chart.rows)


def save_pdf(win, path: str, opts: RenderOptions) -> int:
    """Альбомные листы A4/A3: весь период по ширине листа, строки переносятся на следующие листы."""
    writer = QPdfWriter(path)
    writer.setResolution(96)
    writer.setTitle(opts.title or "Диаграмма Ганта")
    writer.setCreator("Гант Великий")
    size = QPageSize(QPageSize.PageSizeId.A3 if opts.page == "A3" else QPageSize.PageSizeId.A4)
    writer.setPageLayout(QPageLayout(size, QPageLayout.Orientation.Landscape, QMarginsF(8, 8, 8, 8),
                                     QPageLayout.Unit.Millimeter))
    page_w, page_h = writer.width(), writer.height()
    zoom = 0.78                                   # мельче экрана, но читается
    chart = Chart(win, opts, fit_width=page_w / zoom)
    zoom = min(zoom, page_w / chart.width)
    per_page = max(1, int((page_h / zoom - chart.title_h - HEADER_H) // chart.row_h))
    total = max(1, -(-len(chart.rows) // per_page))
    p = QPainter(writer)
    for k in range(total):
        if k:
            writer.newPage()
        p.save()
        p.scale(zoom, zoom)
        chart.paint(p, k * per_page, min(len(chart.rows), (k + 1) * per_page), (k + 1, total))
        p.restore()
    p.end()
    return total
