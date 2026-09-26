"""Плавность: анимированная прокрутка и мягкая смена оформления."""
from __future__ import annotations

import math
from collections import deque

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QElapsedTimer, QEvent, QObject, QPointF,
                            QPropertyAnimation, Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtWidgets import QAbstractScrollArea, QGraphicsOpacityEffect, QLabel, QScrollBar, QWidget


class SmoothScroll(QObject):
    """Плавная прокрутка полосы: колесо, «Сегодня», переходы. Повторные вызовы продолжают движение к новой цели."""

    def __init__(self, bar: QScrollBar, duration: int = 220):
        super().__init__(bar)
        self.bar = bar
        self.enabled = True
        self.target: int | None = None
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(duration)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(lambda v: self.bar.setValue(round(v)))
        self.anim.finished.connect(self._finished)
        bar.sliderPressed.connect(self.stop)  # пользователь схватил ползунок — не спорим с ним

    def _finished(self) -> None:
        self.target = None

    def running(self) -> bool:
        return self.anim.state() == QAbstractAnimation.State.Running

    def stop(self) -> None:
        self.anim.stop()
        self.target = None

    def scroll_by(self, delta: float) -> None:
        base = self.target if self.running() and self.target is not None else self.bar.value()
        self.scroll_to(base + delta)

    def scroll_to(self, value: float) -> None:
        value = max(self.bar.minimum(), min(self.bar.maximum(), int(round(value))))
        if not self.enabled:
            self.bar.setValue(value)
            return
        self.anim.stop()
        self.target = value
        self.anim.setStartValue(float(self.bar.value()))
        self.anim.setEndValue(float(value))
        self.anim.start()


def crossfade(window: QWidget, apply, duration: int = 280) -> None:
    """Сменить оформление без рывка: снимок старого вида плавно растворяется поверх нового."""
    if not window.isVisible():
        apply()
        return
    overlay = QLabel(window)
    overlay.setPixmap(window.grab())
    overlay.setGeometry(window.rect())
    overlay.show()
    overlay.raise_()
    apply()
    effect = QGraphicsOpacityEffect(overlay)
    overlay.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", overlay)
    anim.setDuration(duration)
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.finished.connect(overlay.deleteLater)
    anim.start()


class DragPan(QObject):
    """Зажатое колесо мыши — тянуть вид, как камеру в стратегиях: картинка едет за курсором во все стороны.
    Отпустили на ходу — вид ещё немного катится по инерции и плавно останавливается."""

    started = Signal()

    FRICTION_MS = 325            # за столько миллисекунд скорость инерции падает в e раз
    MIN_FLICK = 0.25             # пикс./мс — медленнее отпустили, значит без инерции

    def __init__(self, view: QAbstractScrollArea, horizontal: bool = True):
        super().__init__(view)
        self.view = view
        self.h = view.horizontalScrollBar() if horizontal else None
        self.v = view.verticalScrollBar()
        self.inertia = True
        self.active = False
        self._last = QPointF()
        self._carry = QPointF()                     # дробные пиксели (масштаб экрана 125 % и т. п.)
        self._samples: deque = deque(maxlen=12)     # (мс, позиция) — скорость в момент отпускания
        self._vel = QPointF()                       # пикс./мс, в сторону прокрутки
        self._clock = QElapsedTimer()
        self._clock.start()
        self._tick = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._coast)
        view.viewport().installEventFilter(self)

    def _now(self) -> float:
        return self._clock.nsecsElapsed() / 1e6

    def coasting(self) -> bool:
        return self._timer.isActive()

    def stop(self) -> None:
        self._timer.stop()

    def eventFilter(self, obj, event) -> bool:
        et = event.type()
        if et in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            self.stop()
            if event.button() == Qt.MouseButton.MiddleButton:
                self._begin(event.globalPosition())
                return True
        elif et == QEvent.Type.MouseMove and self.active:
            if not event.buttons() & Qt.MouseButton.MiddleButton:   # отпустили где-то вне окна
                self._end()
                return False
            self._move(event.globalPosition())
            return True
        elif et == QEvent.Type.MouseButtonRelease and self.active and event.button() == Qt.MouseButton.MiddleButton:
            self._move(event.globalPosition())
            self._end()
            return True
        elif et == QEvent.Type.Wheel:
            self.stop()
        return False

    def _begin(self, pos: QPointF) -> None:
        self.active = True
        self._last, self._carry = QPointF(pos), QPointF()
        self._samples.clear()
        self._samples.append((self._now(), QPointF(pos)))
        for smooth in (getattr(self.view, "smooth_v", None), getattr(self.view, "smooth_h", None)):
            if smooth is not None:
                smooth.stop()     # колесо ещё докручивало — не спорим с рукой
        self.view.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        self.started.emit()

    def _move(self, pos: QPointF) -> None:
        d = pos - self._last
        self._last = QPointF(pos)
        self._samples.append((self._now(), QPointF(pos)))
        self._scroll(-d.x(), -d.y())

    def _end(self) -> None:
        self.active = False
        self.view.viewport().unsetCursor()
        now = self._now()
        recent = [(t, p) for t, p in self._samples if now - t <= 90]
        if not self.inertia or len(recent) < 2:
            return
        (t0, p0), (t1, p1) = recent[0], recent[-1]
        dt = max(t1 - t0, 8.0)
        vel = (p0 - p1) / dt                  # тянули вправо — вид едет влево
        if math.hypot(vel.x(), vel.y()) < self.MIN_FLICK or now - t1 > 60:
            return
        self._vel = vel
        self._tick = now
        self._timer.start()

    def _coast(self) -> None:
        now = self._now()
        dt = min(now - self._tick, 50.0)
        self._tick = now
        self._vel *= math.exp(-dt / self.FRICTION_MS)
        if math.hypot(self._vel.x(), self._vel.y()) < 0.03:
            self.stop()
            return
        if not self._scroll(self._vel.x() * dt, self._vel.y() * dt):
            self.stop()                        # упёрлись в край

    def _scroll(self, dx: float, dy: float) -> bool:
        """Сдвинуть полосы прокрутки; False — ни одна не сдвинулась (край)."""
        self._carry += QPointF(dx, dy)
        ix, iy = int(self._carry.x()), int(self._carry.y())
        self._carry -= QPointF(ix, iy)
        moved = False
        for bar, step in ((self.h, ix), (self.v, iy)):
            if bar is not None and step:
                before = bar.value()
                bar.setValue(before + step)
                moved = moved or bar.value() != before
        return moved or not (ix or iy)
