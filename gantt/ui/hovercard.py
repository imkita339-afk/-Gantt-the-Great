"""Карточка при наведении: что это, план и факт, отклонение от срока, кто делает. Появляется и уходит плавно."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..exchange import FIELD_TITLES
from ..model import Dataset, Node, fmt
from .theme import _alpha, plan_colors, row_colors, status_color, tone

WIDTH = 372
PAD = 14
MARGIN = 14          # под тень
LABEL_W = 100


@dataclass
class Line:
    kind: str                       # title | sub | chips | sep | row | progress | note | hint
    label: str = ""
    text: str = ""
    color: QColor | None = None
    swatch: str = ""                # plan | fact | going | noend
    chips: list = field(default_factory=list)
    pct: int = 0


def _days(n: int) -> str:
    return f"{n} дн."


def card_lines(ds: Dataset, node: Node, s, t, project_color) -> list[Line]:
    """Содержимое карточки — отдельно от рисования, чтобы проверять тестами."""
    c = row_colors(t, QColor(s.accent))
    role = ds.role(node)
    today = ds.today
    lines = [Line("title", text=node.name)]
    sub = [ds.level_name(node)]
    if node.number:
        sub.append(f"№ {node.number}")
    if role == "task" and node.parent is not None:
        sub.append(node.parent.name)
    elif role != "task":
        done, total = ds.progress(node)
        if total:
            sub.append(f"заданий: {total}")
    lines.append(Line("sub", text=" · ".join(sub)))

    chips = []
    status = ds.status_label(node)
    if status:
        chips.append((status, status_color(ds.status_kind_for_color(node), s, t), QColor("#1C1E23")))
    kind, _d, due = ds.due_info(node)
    if due and kind in ("overdue", "today", "soon", "late", "ontime"):
        col = {"overdue": c["red"], "today": c["amber"], "soon": c["amber"], "late": c["amber"],
               "ontime": c["green"]}[kind]
        chips.append((due, _alpha(col, 40), col))
    if chips:
        lines.append(Line("chips", chips=chips))
    lines.append(Line("sep"))

    # план
    if role == "task":
        ps, pe, open_end = ds.task_span(node)
        plan_end = ds.get(node, "planEnd")
        if ps and plan_end:
            lines.append(Line("row", "План", f"{fmt(ps, False)} → {fmt(plan_end, False)} · "
                                            f"{_days((plan_end - ps).days + 1)}", swatch="plan"))
        elif ps:
            lines.append(Line("row", "План", f"с {fmt(ps, False)}, срок не задан", swatch="noend"))
        else:
            lines.append(Line("row", "План", "не задан", swatch="plan"))
    else:
        ps, pe, derived = ds.span(node)
        if ps:
            text = f"{fmt(ps, False)} → {fmt(pe, False)} · {_days((pe - ps).days + 1)}"
            lines.append(Line("row", "План" if role == "project" else "Период",
                              text + (" (по заданиям)" if derived and role == "project" else ""), swatch="plan"))
    # факт
    fs, fe = ds.get(node, "factStart"), ds.get(node, "factEnd")
    if role != "group":
        if fs and fe:
            lines.append(Line("row", "Факт", f"{fmt(fs, False)} → {fmt(fe, False)} · {_days((fe - fs).days + 1)}",
                              swatch="fact"))
        elif fs and not ds.is_done(node):
            lines.append(Line("row", "Факт", f"с {fmt(fs, False)}, идёт · {_days((today - fs).days + 1)}",
                              swatch="going"))
        elif fe:
            lines.append(Line("row", "Факт", f"закончено {fmt(fe, False)}", swatch="fact"))
        else:
            lines.append(Line("row", "Факт", "ещё не начато", swatch="fact", color=QColor(t.ink3)))
    # отклонение
    if role != "group" and ds.get(node, "planEnd"):
        end = ds.get(node, "planEnd")
        if fe:
            d = (fe - end).days
            if d > 0:
                lines.append(Line("row", "Отклонение", f"позже срока на {_days(d)}", color=c["red"]))
            elif d < 0:
                lines.append(Line("row", "Отклонение", f"раньше срока на {_days(-d)}", color=c["green"]))
            else:
                lines.append(Line("row", "Отклонение", "точно в срок", color=c["green"]))
        elif not ds.is_done(node):
            d = (end - today).days
            if d < 0:
                lines.append(Line("row", "До срока", f"срок прошёл {_days(-d)} назад", color=c["red"]))
            elif d == 0:
                lines.append(Line("row", "До срока", "срок сегодня", color=c["amber"]))
            else:
                lines.append(Line("row", "До срока", _days(d), color=c["amber"] if d <= 3 else None))
    # люди
    names = ds.executors_of(node)
    if names and role != "group":
        shown = ", ".join(names[:8]) + (f" и ещё {len(names) - 8}" if len(names) > 8 else "")
        lines.append(Line("row", "Исполнители" if len(names) > 1 else "Исполнитель", shown))
    if node.raw.get("initiator"):
        lines.append(Line("row", "Инициатор", ds.person_name(node.raw["initiator"])))
    # прогресс
    if role != "task":
        done, total = ds.progress(node)
        if total:
            lines.append(Line("progress", "Выполнено", f"{ds.percent(node)}% · {done} из {total}",
                              pct=ds.percent(node) or 0,
                              color=project_color(node) if role == "project" else QColor(t.group_bar)))
        n = ds.overdue_count(node)
        if n:
            lines.append(Line("row", "Просрочено", f"заданий: {n}", color=c["red"]))
    scales = [ds.scale_item(node, f)["name"] for f in ("priority", "urgency", "impact") if ds.scale_item(node, f)]
    if scales:
        lines.append(Line("row", "Приоритет", " · ".join(scales)))
    custom = ds.custom_label(node)
    if custom:
        lines.append(Line("row", "Ещё", custom))
    edited = ds.edited_fields(node)
    if edited:
        parts = []
        for f in edited:
            src = node.source(f)
            if f == "color":
                parts.append("цвет")
            else:
                was = fmt(src, False) if isinstance(src, date) else (
                    ds.statuses.get(src, {}).get("name", src) if f == "status" else src)
                parts.append(f"{FIELD_TITLES.get(f, f).lower()} (в источнике — {was or 'пусто'})")
        lines.append(Line("note", text="Изменено вами: " + "; ".join(parts), color=c["accent"]))
    if role != "group":
        lines.append(Line("hint", text="Двойной щелчок — изменить · правая кнопка — меню"))
    return lines


class HoverCard(QWidget):
    """Отдельное окошко поверх всего: не перехватывает мышь и фокус, появляется с задержкой и растворяется."""

    def __init__(self, win):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.NoDropShadowWindowHint)
        self.win = win
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.enabled = True
        self.node: Node | None = None
        self.lines: list[Line] = []
        self._layout: list[tuple] = []
        self.anchor = QPoint()
        self.pending: tuple[Node, QPoint] | None = None
        self.show_timer = QTimer(self)
        self.show_timer.setSingleShot(True)
        self.show_timer.timeout.connect(self._show_pending)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(160)
        self.hide_timer.timeout.connect(self.dismiss)
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.setDuration(150)
        self.fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade.finished.connect(self._fade_done)
        self.glide = QPropertyAnimation(self, b"pos", self)
        self.glide.setDuration(160)
        self.glide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fonts: dict = {}

    # ---------- когда показывать ----------
    def hover(self, node: Node | None, gpos: QPoint, delay: int = 420) -> None:
        if not self.enabled or self.win.ds is None:
            return
        if node is None:
            self.show_timer.stop()
            self.pending = None
            if self.isVisible():
                self.hide_timer.start()
            return
        self.hide_timer.stop()
        self.pending = (node, gpos)
        if self.isVisible() and self.fade.endValue() != 0.0:
            self._set(node)
            self._place(gpos, animate=True)
        else:
            self.show_timer.start(delay)

    def follow(self, gpos: QPoint) -> None:
        if self.pending is not None:
            self.pending = (self.pending[0], gpos)
        if self.isVisible() and (gpos - self.anchor).manhattanLength() > 60:
            self._place(gpos, animate=True)

    def dismiss(self) -> None:
        self.show_timer.stop()
        self.hide_timer.stop()
        self.pending = None
        if self.isVisible() and not self.win.settings.smooth:   # режим картошки — сразу, без растворения
            self.fade.stop()
            self.hide()
            self.node = None
        elif self.isVisible():
            self.fade.stop()
            self.fade.setStartValue(self.windowOpacity())
            self.fade.setEndValue(0.0)
            self.fade.start()

    def _fade_done(self) -> None:
        if self.fade.endValue() == 0.0:
            self.hide()
            self.node = None

    def _show_pending(self) -> None:
        if self.pending is None or self.win.ds is None:
            return
        node, gpos = self.pending
        self._set(node)
        self._place(gpos, animate=False)
        smooth = self.win.settings.smooth
        self.setWindowOpacity(0.0 if smooth else 1.0)
        self.show()
        self.raise_()
        if not smooth:
            return
        self.fade.stop()
        self.fade.setStartValue(0.0)
        self.fade.setEndValue(1.0)
        self.fade.start()

    # ---------- содержимое ----------
    def _font(self, kind: str) -> tuple[QFont, QFontMetrics]:
        f = self._fonts.get(kind)
        if f is None:
            font = QFont(self.win.font())
            size = font.pointSizeF()
            if kind == "title":
                font.setPointSizeF(size + 1.5)
                font.setWeight(QFont.Weight.DemiBold)
            elif kind in ("small", "hint"):
                font.setPointSizeF(size - 1)
            elif kind == "label":
                font.setPointSizeF(size - 0.5)
            elif kind == "chip":
                font.setPointSizeF(size - 1)
                font.setWeight(QFont.Weight.DemiBold)
            f = self._fonts[kind] = (font, QFontMetrics(font))
        return f

    def restyle(self) -> None:
        self._fonts.clear()
        if self.node is not None:
            self._set(self.node)

    def _set(self, node: Node) -> None:
        ds, s, t = self.win.ds, self.win.settings, self.win.tokens
        self.node = node
        self.lines = card_lines(ds, node, s, t, self.win.project_color)
        inner = WIDTH - 2 * PAD
        y, ops = PAD, []
        flags = int(Qt.TextFlag.TextWordWrap)
        for ln in self.lines:
            if ln.kind == "title":
                _f, fm = self._font("title")
                r = fm.boundingRect(QRect(0, 0, inner, 400), flags, ln.text)
                h = min(r.height(), fm.lineSpacing() * 3)
                ops.append((ln, QRect(PAD, y, inner, h)))
                y += h + 2
            elif ln.kind in ("sub", "hint", "note"):
                _f, fm = self._font("small" if ln.kind != "note" else "label")
                r = fm.boundingRect(QRect(0, 0, inner, 400), flags, ln.text)
                y += 6 if ln.kind in ("hint", "note") else 0
                ops.append((ln, QRect(PAD, y, inner, r.height())))
                y += r.height() + 2
            elif ln.kind == "chips":
                y += 6
                ops.append((ln, QRect(PAD, y, inner, 22)))
                y += 24
            elif ln.kind == "sep":
                y += 8
                ops.append((ln, QRect(PAD, y, inner, 1)))
                y += 9
            else:
                _f, fm = self._font("normal")
                vw = inner - LABEL_W
                r = fm.boundingRect(QRect(0, 0, vw, 400), flags, ln.text)
                h = max(r.height(), fm.height()) + (8 if ln.kind == "progress" else 0)
                ops.append((ln, QRect(PAD, y, inner, h)))
                y += h + 5
        self._layout = ops
        self.resize(QSize(WIDTH + 2 * MARGIN, y + PAD + 2 * MARGIN))
        self.update()

    def _place(self, gpos: QPoint, animate: bool) -> None:
        self.anchor = QPoint(gpos)
        w, h = self.width(), self.height()
        screen = QGuiApplication.screenAt(gpos) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        x, y = gpos.x() + 16 - MARGIN, gpos.y() + 22 - MARGIN
        if x + w > avail.right():
            x = gpos.x() - w - 10 + MARGIN
        if y + h > avail.bottom():
            y = gpos.y() - h - 10 + MARGIN
        target = QPoint(max(avail.left(), x), max(avail.top(), y))
        if animate and self.isVisible() and self.win.settings.smooth:
            self.glide.stop()
            self.glide.setStartValue(self.pos())
            self.glide.setEndValue(target)
            self.glide.start()
        else:
            self.move(target)

    # ---------- рисование ----------
    def paintEvent(self, event) -> None:
        t = self.win.tokens
        if t is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body = QRectF(MARGIN, MARGIN, self.width() - 2 * MARGIN, self.height() - 2 * MARGIN)
        for i in range(MARGIN, 0, -2) if not self.win.settings.potato else ():  # мягкая тень
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, int(26 * (1 - i / MARGIN) ** 2) + 2))
            p.drawRoundedRect(body.adjusted(-i, -i + 4, i, i + 4), 12 + i, 12 + i)
        p.setBrush(QColor(t.panel))
        p.setPen(QPen(QColor(t.line), 1))
        p.drawRoundedRect(body, 12, 12)
        p.translate(MARGIN, MARGIN)
        flags = int(Qt.TextFlag.TextWordWrap)
        for ln, r in self._layout:
            if ln.kind == "title":
                p.setFont(self._font("title")[0])
                p.setPen(QColor(t.ink))
                p.drawText(r, flags, ln.text)
            elif ln.kind in ("sub", "hint"):
                p.setFont(self._font("small")[0])
                p.setPen(QColor(t.ink3))
                p.drawText(r, flags, ln.text)
            elif ln.kind == "note":
                p.setFont(self._font("label")[0])
                p.setPen(ln.color or QColor(t.ink2))
                p.drawText(r, flags, ln.text)
            elif ln.kind == "sep":
                p.fillRect(r, QColor(t.line))
            elif ln.kind == "chips":
                font, fm = self._font("chip")
                p.setFont(font)
                x = r.left()
                for text, color, ink in ln.chips:
                    w = fm.horizontalAdvance(text) + 18
                    chip = QRectF(x, r.top(), w, 21)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(color)
                    p.drawRoundedRect(chip, 10.5, 10.5)
                    p.setPen(ink)
                    p.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)
                    x += w + 6
            else:
                font, fm = self._font("label")
                p.setFont(font)
                p.setPen(QColor(t.ink3))
                lx = r.left()
                if ln.swatch:
                    self._swatch(p, QRectF(lx, r.top() + fm.height() / 2 - 6, 20, 12), ln.swatch)
                    lx += 26
                p.drawText(QRect(lx, r.top(), LABEL_W - (lx - r.left()), fm.height() + 2),
                           int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), ln.label)
                font, fm = self._font("normal")
                p.setFont(font)
                p.setPen(ln.color or QColor(t.ink))
                vr = QRect(r.left() + LABEL_W, r.top(), r.width() - LABEL_W, r.height())
                if ln.kind == "progress":
                    bar = QRectF(vr.left(), vr.top() + fm.height() + 2, vr.width() - 4, 5)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(t.grid_strong))
                    p.drawRoundedRect(bar, 2.5, 2.5)
                    p.setBrush(tone(ln.color, -0.18))
                    p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * min(ln.pct, 100) / 100, 5), 2.5, 2.5)
                    p.setPen(QColor(t.ink))
                p.drawText(vr, flags, ln.text)
        p.end()

    def _swatch(self, p: QPainter, r: QRectF, kind: str) -> None:
        s, t = self.win.settings, self.win.tokens
        base = status_color("inProgress", s, t)
        if self.node is not None and self.win.ds is not None:
            base = status_color(self.win.ds.status_kind_for_color(self.node), s, t) \
                if self.win.ds.role(self.node) == "task" else self.win.project_color(self.node)
        fill, edge, fact = plan_colors(base, t)
        paint_mark(p, r, kind, fill, edge, fact, QColor(t.today))


def paint_mark(p: QPainter, r: QRectF, kind: str, fill: QColor, edge: QColor, fact: QColor, red: QColor) -> None:
    """Маленький образец обозначения — в карточке и в легенде."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    cy = r.center().y()
    if kind in ("plan", "noend"):
        p.setPen(QPen(edge, 1.2, Qt.PenStyle.DashLine if kind == "noend" else Qt.PenStyle.SolidLine))
        p.setBrush(fill)
        p.drawRoundedRect(r, 3, 3)
    elif kind in ("fact", "going"):
        h = max(4.0, r.height() * 0.42)
        w = r.width() - (6 if kind == "going" else 0)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fact)
        p.drawRoundedRect(QRectF(r.left(), cy - h / 2, w, h), h / 2, h / 2)
        if kind == "going":
            p.drawPolygon(QPolygonF([QPointF(r.left() + w - 1, cy - h / 2 - 2.5), QPointF(r.right(), cy),
                                     QPointF(r.left() + w - 1, cy + h / 2 + 2.5)]))
    p.restore()
