"""Шкала без экрана: шапка без следов при прокрутке, перетаскивание колесом, режим картошки, метка «изм.»."""
from datetime import timedelta

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication



@pytest.fixture
def win(root, make_win):
    w = make_win()
    w.resize(1400, 800)
    assert w.open_file(str(root / "examples" / "full.gantt.json"))
    w.show()
    QApplication.processEvents()
    return w


class PaintSpy(QObject):
    def __init__(self, widget):
        super().__init__()
        self.rects: list[QRect] = []
        widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Paint:
            self.rects.append(event.rect())
        return False


def test_header_repaints_whole_width_on_horizontal_scroll(win):
    """Подпись месяца у левого края «липкая»: после сдвига шапку нужно перерисовать целиком, иначе
    её прежние копии уезжают вправо вместе с картинкой («сесесе»)."""
    tl = win.timeline
    hs = tl.horizontalScrollBar()
    hs.setValue(hs.maximum() // 2)
    QApplication.processEvents()
    spy = PaintSpy(tl.header().viewport())
    for _ in range(3):
        hs.setValue(hs.value() - 23)
        QApplication.processEvents()
    width = tl.header().viewport().width()
    assert spy.rects and all(r.width() >= width for r in spy.rects)


def _mouse(kind, pos: QPointF, gpos: QPointF, button, buttons):
    return QMouseEvent(kind, pos, gpos, button, buttons, Qt.KeyboardModifier.NoModifier)


def drag(view, points, button=Qt.MouseButton.MiddleButton):
    vp = view.viewport()
    origin = QPointF(vp.mapToGlobal(QPoint(0, 0)))
    first = QPointF(*points[0])
    QApplication.sendEvent(vp, _mouse(QEvent.Type.MouseButtonPress, first, origin + first, button, button))
    for x, y in points[1:]:
        pt = QPointF(x, y)
        QApplication.sendEvent(vp, _mouse(QEvent.Type.MouseMove, pt, origin + pt, Qt.MouseButton.NoButton, button))
    last = QPointF(*points[-1])
    QApplication.sendEvent(vp, _mouse(QEvent.Type.MouseButtonRelease, last, origin + last, button,
                                      Qt.MouseButton.NoButton))


def test_middle_drag_pans_both_ways_like_a_map(win):
    tl = win.timeline
    win.expand_all()
    hs, vs = tl.horizontalScrollBar(), tl.verticalScrollBar()
    tl.resize(600, 200)
    QApplication.processEvents()
    hs.setValue(hs.maximum() // 2)
    vs.setValue(0)
    h0, v0 = hs.value(), vs.value()
    tl.pan.inertia = False
    drag(tl, [(300, 150), (260, 130), (200, 100)])    # тянем влево и вверх — вид едет вправо и вниз
    assert hs.value() == h0 + 100
    assert vs.value() == v0 + min(50, vs.maximum())
    assert win.table.verticalScrollBar().value() == vs.value()    # таблица едет вместе со шкалой
    assert not tl.pan.active and tl.pan.coasting() is False


def test_middle_drag_does_not_select_or_move_bars(win):
    tl = win.timeline
    tl.pan.inertia = False
    before = tl.currentIndex()
    drag(tl, [(300, 60), (320, 60)])
    assert tl.currentIndex() == before
    assert tl._drag is None and not win.undo.count()


def test_flick_keeps_rolling_with_inertia_and_stops(win):
    tl = win.timeline
    hs = tl.horizontalScrollBar()
    hs.setValue(hs.maximum() // 2)
    tl.pan.inertia = True
    pan = tl.pan
    pan._begin(QPointF(500, 100))
    t0 = pan._now()
    # быстрый рывок влево: 120 пикс. за ~30 мс — отпустили на ходу
    pan._samples.clear()
    pan._samples.extend([(t0 - 30, QPointF(620, 100)), (t0, QPointF(500, 100))])
    pan._end()
    assert pan.coasting()
    start = hs.value()
    for _ in range(5):
        pan._tick -= 16
        pan._coast()
    assert hs.value() > start          # катится дальше в ту же сторону
    pan.stop()
    assert not pan.coasting()


def test_potato_mode(win, tmp_path):
    from gantt.settings import load_view

    assert not win.timeline.fast
    win.settings.animations = True
    win.set_potato(True)
    s = load_view()
    assert s.potato and not s.smooth                   # запомнилось; анимаций нет
    assert win.timeline.fast and win.table.fast
    assert not win.vscroll.enabled and not win.timeline.animations and not win.timeline.pan.inertia
    assert all(pop.fast and not pop.animations for pop in win.pops)
    assert win.a_potato.isChecked() and not win.potato_btn.isHidden()
    win.settings_panel.reload(win.settings, win.tokens)
    assert not win.settings_panel.animations.isEnabled()
    assert win.settings_panel.animations.isChecked()   # своё значение флажок помнит
    img = win.timeline.viewport().grab()               # рисуется без ошибок
    assert not img.isNull()
    win.set_potato(False)
    assert win.settings.smooth and not win.timeline.fast and win.potato_btn.isHidden()


def _accent_pixels(win, node) -> int:
    tl = win.timeline
    img = QImage(tl.scale.width + 300, 34, QImage.Format.Format_ARGB32)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    tl.delegate.bars(p, QRect(0, 0, tl.scale.width, 34), node)
    p.end()
    accent = QColor(win.settings.accent).rgb()
    return sum(1 for y in range(img.height()) for x in range(img.width()) if img.pixel(x, y) == accent)


def test_edited_bar_gets_chip(win):
    task = next(n for n in win.ds.walk() if win.ds.role(n) == "task" and n.source("planEnd")
                and not n.raw.get("milestone"))
    assert _accent_pixels(win, task) == 0
    win.push(task, {"planEnd": task.source("planEnd") + timedelta(days=10)}, "тест")
    assert _accent_pixels(win, task) > 20                 # «изм.» акцентного цвета
    assert win.timeline.delegate._source_plan(task, "task")[1] == task.source("planEnd")
