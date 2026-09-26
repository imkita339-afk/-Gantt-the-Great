"""Мелкие элементы «отклика»: волна от нажатия, пульс разделителя, всплывающие уведомления, прилипающий разделитель."""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QApplication, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLayout,
                               QPushButton, QSplitter, QSplitterHandle, QWidget)


class _Ripple(QWidget):
    """Волна от точки нажатия: расходится и гаснет. Мышь пропускает насквозь."""

    def __init__(self, parent: QWidget, center: QPoint, color: QColor):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())
        self.center, self.color, self.t = QPointF(center), QColor(color), 0.0
        r = parent.rect()
        self.max_r = max((QPointF(x, y) - self.center).manhattanLength() for x, y in
                         ((r.left(), r.top()), (r.right(), r.top()), (r.left(), r.bottom()), (r.right(), r.bottom())))
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(420)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self.deleteLater)
        self.show()
        self.anim.start()

    def _step(self, v) -> None:
        self.t = v
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.color)
        c.setAlphaF(0.28 * (1 - self.t))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        radius = 6 + self.max_r * self.t
        p.drawEllipse(self.center, radius, radius)


class RippleFilter(QObject):
    """Для всей программы: у любой кнопки при нажатии — лёгкая волна."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = True
        self.color = QColor("#2F5FD0")

    def eventFilter(self, obj, event) -> bool:
        if self.enabled and event.type() == QEvent.Type.MouseButtonPress and isinstance(obj, QAbstractButton) \
                and obj.isEnabled() and obj.width() > 8:
            _Ripple(obj, event.position().toPoint(), self.color)
        return False


class Pulse(QWidget):
    """Пульс на вертикальной линии: мягкое свечение расходится в стороны и гаснет."""

    def __init__(self, parent: QWidget, x: int, color: QColor):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(parent.rect())
        self.x, self.color, self.t = x, QColor(color), 0.0
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(520)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self.deleteLater)
        self.show()
        self.raise_()
        self.anim.start()

    def _step(self, v) -> None:
        self.t = v
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        half = 6 + 46 * self.t
        g = QLinearGradient(self.x - half, 0, self.x + half, 0)
        edge = QColor(self.color)
        edge.setAlpha(0)
        mid = QColor(self.color)
        mid.setAlphaF(0.42 * (1 - self.t))
        g.setColorAt(0, edge)
        g.setColorAt(0.5, mid)
        g.setColorAt(1, edge)
        p.fillRect(QRectF(self.x - half, 0, 2 * half, self.height()), g)
        core = QColor(self.color)
        core.setAlphaF(0.9 * (1 - self.t))
        p.fillRect(QRectF(self.x - 1, 0, 2, self.height()), core)


class _SnapHandle(QSplitterHandle):
    released = Signal()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self.released.emit()


class SnapSplitter(QSplitter):
    """Разделитель, который сообщает об отпускании — окно «прилепляет» его к краю колонки."""

    handleReleased = Signal()

    def createHandle(self) -> QSplitterHandle:
        h = _SnapHandle(self.orientation(), self)
        h.released.connect(self.handleReleased)
        return h


class Toast(QWidget):
    """Короткое уведомление внизу окна, по желанию — с кнопкой действия («Отменить»)."""

    current: "Toast | None" = None

    def __init__(self, parent: QWidget, text: str, action: str | None = None, callback=None, ms: int = 4200,
                 dark: bool = False):
        super().__init__(parent)
        if Toast.current is not None:
            try:
                Toast.current.deleteLater()
            except RuntimeError:
                pass
        Toast.current = self
        bg, fg = ("#E7E9ED", "#1C1E23") if dark else ("#1C1E23", "#FFFFFF")
        self.setStyleSheet(f"QWidget#toast {{ background: {bg}; border-radius: 10px; }}"
                           f"QLabel {{ color: {fg}; }}"
                           f"QPushButton {{ color: {fg}; background: transparent; border: 1px solid {fg}55;"
                           f" border-radius: 6px; padding: 3px 10px; font-weight: 600; }}")
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 9, 10 if action else 14, 9)
        lay.setSpacing(12)
        lay.addWidget(QLabel(text))
        if action:
            btn = QPushButton(action)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda: (callback() if callback else None, self._hide()))
            lay.addWidget(btn)
        self.adjustSize()
        self._place()
        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(0.0)
        self.setGraphicsEffect(self.effect)
        self.show()
        self.raise_()
        self._fade(0.0, 1.0, 160)
        QTimer.singleShot(ms, self._hide)

    def _place(self) -> None:
        par = self.parentWidget()
        bottom = getattr(par, "toast_bottom", lambda: par.height() - 36)()
        self.move(par.width() - self.width() - 22, bottom - self.height())

    def _fade(self, a: float, b: float, ms: int, done=None) -> None:
        anim = QPropertyAnimation(self.effect, b"opacity", self)
        anim.setDuration(ms)
        anim.setStartValue(a)
        anim.setEndValue(b)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if done:
            anim.finished.connect(done)
        anim.start()

    def _hide(self) -> None:
        try:
            self._fade(self.effect.opacity(), 0.0, 260, self.deleteLater)
        except RuntimeError:
            pass
        if Toast.current is self:
            Toast.current = None


class Popover(QWidget):
    """Всплывающая панель поверх окна (фильтры, обозначения): плавно выезжает, закрывается щелчком мимо или Esc.

    Это дочерний виджет окна, а не отдельное окно: диалоги и выпадающие списки внутри работают как обычно.
    """

    MARGIN = 16                        # под тень

    closed = Signal()

    def __init__(self, win: QWidget, content: QWidget, width: int = 380):
        super().__init__(win)
        self.win = win
        self.content = content
        self.panel_width = width
        self.anchor: QWidget | None = None
        self.preferred_height: int | None = None     # желаемая высота панели (иначе — по содержимому)
        self.tokens = None
        self.animations = True
        self.fast = False                  # режим картошки: без тени
        lay = QHBoxLayout(self)
        m = self.MARGIN
        lay.setContentsMargins(m + 1, m + 1, m + 1, m + 5)
        lay.addWidget(content)
        self._t = 0.0
        self._closing = False
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(170)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self._done)
        self.effect = None
        self.hide()

    def is_open(self) -> bool:
        return self.isVisible() and not self._closing

    def open(self, anchor: QWidget) -> None:
        self.anchor = anchor
        self._closing = False
        # шрифт или тема могли смениться, пока панель была скрыта: вложенные раскладки скрытых виджетов
        # помнят прежние размеры — без пересчёта подписи обрезаются
        for lay in self.findChildren(QLayout):
            lay.invalidate()
        self.place()
        self.show()
        self.raise_()
        QApplication.instance().installEventFilter(self)
        self._animate(1.0)

    def close_(self) -> None:
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        QApplication.instance().removeEventFilter(self)
        self._animate(0.0)
        self.closed.emit()

    def place(self) -> None:
        if self.anchor is None:
            return
        m = self.MARGIN
        a = self.anchor.mapTo(self.win, QPoint(0, self.anchor.height()))
        hint = self.content.sizeHint()
        want = self.preferred_height or hint.height()
        w = max(self.panel_width, hint.width()) + 2 * m + 2   # по содержимому: ничего не обрезано
        x = max(0, min(a.x() - m, self.win.width() - w))
        top = self.anchor.mapTo(self.win, QPoint(0, 0)).y()
        below, above = self.win.height() - a.y() - 12, top - 12
        if below >= min(want, 320) or below >= above:   # вниз от кнопки
            h = max(160, min(want + 2 * m + 6, below))
            y = a.y() - m + 4
        else:                                            # внизу окна места нет — вверх
            h = max(160, min(want + 2 * m + 6, above))
            y = top - h + m - 4
            x = max(0, min(self.anchor.mapTo(self.win, QPoint(self.anchor.width(), 0)).x() - w + m, self.win.width() - w))
        self._base = QPoint(x, y)
        self.setGeometry(x, y, w, h)

    def _animate(self, to: float) -> None:
        if not self.animations:
            self._step(to)
            self._done()
            return
        if self.effect is None:
            self.effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(self.effect)
        self.anim.stop()
        self.anim.setStartValue(self._t)
        self.anim.setEndValue(to)
        self.anim.start()

    def _step(self, v) -> None:
        self._t = float(v)
        if self.effect is not None:
            self.effect.setOpacity(self._t)
        if hasattr(self, "_base"):
            self.move(self._base.x(), self._base.y() - round(10 * (1 - self._t)))

    def _done(self) -> None:
        if self.effect is not None:        # после появления — без эффекта: содержимое рисуется напрямую
            self.setGraphicsEffect(None)
            self.effect = None
        if self._closing:
            self._closing = False
            self.hide()

    def eventFilter(self, obj, event) -> bool:
        et = event.type()
        if et == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape and QApplication.activeModalWidget() is None:
            self.close_()
            return True
        if et == QEvent.Type.MouseButtonPress and isinstance(obj, QWidget) and \
                QApplication.activeModalWidget() is None:
            w = obj
            while w is not None:
                if w is self or w is self.anchor:
                    return False
                w = w.parentWidget()
            self.close_()
        return False

    def paintEvent(self, _event) -> None:
        t = self.tokens
        if t is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        m = self.MARGIN
        body = QRectF(self.rect()).adjusted(m, m, -m, -m + 4)
        for i in range(m, 0, -2) if not self.fast else ():  # мягкая тень
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, int(30 * (1 - i / m) ** 2) + 2))
            p.drawRoundedRect(body.adjusted(-i, -i + 5, i, i + 5), 12 + i, 12 + i)
        p.setBrush(QColor(t.panel))
        p.setPen(QColor(t.line))
        p.drawRoundedRect(body, 12, 12)


def potato_icon(size: int = 18) -> QIcon:
    """Картошка — значок режима высокой производительности: неровный клубень с «глазками»."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 18
    body = QPainterPath()
    body.moveTo(3 * s, 9 * s)
    body.cubicTo(2 * s, 4 * s, 8 * s, 2.5 * s, 11.5 * s, 3.5 * s)
    body.cubicTo(16 * s, 4.5 * s, 17 * s, 9 * s, 15 * s, 12.5 * s)
    body.cubicTo(13 * s, 16 * s, 6 * s, 16.5 * s, 4 * s, 13.5 * s)
    body.cubicTo(3.2 * s, 12.2 * s, 3.2 * s, 10.5 * s, 3 * s, 9 * s)
    p.setPen(QColor("#8A5A2B"))
    p.setBrush(QColor("#D9A55B"))
    p.drawPath(body)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#A8733A"))
    for x, y, r in ((6.5, 8, 1.1), (11, 6.5, 0.9), (12.5, 11, 1.0), (8, 12.2, 0.8)):
        p.drawEllipse(QPointF(x * s, y * s), r * s, r * s)
    p.end()
    return QIcon(pm)
