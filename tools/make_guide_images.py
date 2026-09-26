"""Картинки для руководства (docs/img): снимки окон программы на выдуманных демо-данных.

Запуск: .venv\\Scripts\\python tools\\make_guide_images.py
Окна на пару секунд появятся на экране — без экрана Qt рисует текст квадратиками.
Данные и настройки — во временной папке: ваши правки и настройки не трогаются.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="gantt-guide-"))
os.environ["GANTT_DATA_DIR"] = str(TMP / "data")
os.environ["GANTT_MULTI"] = "1"
os.environ["GANTT_REG_KEY"] = r"Software\GanttGuideImages\None"   # «уже установлена» не показывать

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QApplication, QWidget  # noqa: E402

OUT = ROOT / "docs" / "img"
WIDTH = 1280
ACCENT = QColor("#2F5FD0")


def wait(ms: int = 250) -> None:
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        QApplication.processEvents()
        time.sleep(0.01)


def save(pm: QPixmap, name: str, max_w: int = WIDTH) -> None:
    """Сохранить в логических пикселях (не больше max_w): одинаково чётко при любом масштабе экрана."""
    dpr = pm.devicePixelRatio()
    w = round(pm.width() / dpr)
    img = pm.toImage()
    target = min(w, max_w)
    if img.width() != target:
        img = img.scaledToWidth(target, Qt.TransformationMode.SmoothTransformation)
    OUT.mkdir(parents=True, exist_ok=True)
    img.save(str(OUT / name), "PNG", 0)   # 0 — сильнейшее сжатие PNG (без потерь)
    print("  ", name, img.width(), "×", img.height())


def grab(w: QWidget) -> QPixmap:
    wait(350)
    return w.grab()


def _badge(p: QPainter, c: QPointF, n: int) -> None:
    p.setPen(QPen(QColor("#FFFFFF"), 2.5))
    p.setBrush(QColor("#E0457B"))
    p.drawEllipse(c, 12, 12)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRectF(c.x() - 12, c.y() - 12, 24, 24), Qt.AlignmentFlag.AlignCenter, str(n))


def mark(pm: QPixmap, inline: list[tuple[int, QPoint]], above: list[tuple[int, QPoint]]) -> QPixmap:
    """Номера-выноски. inline — прямо на снимке (в пустом месте); above — на полосе над окном
    с линией вниз к кнопке, чтобы не закрывать её подпись."""
    dpr = pm.devicePixelRatio()
    band = 34
    w, h = round(pm.width() / dpr), round(pm.height() / dpr)
    out = QPixmap(round(w * dpr), round((h + band) * dpr))
    out.setDevicePixelRatio(dpr)
    out.fill(QColor("#FFFFFF"))
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.drawPixmap(0, band, pm)
    f = QFont("Segoe UI", 10)
    f.setBold(True)
    p.setFont(f)
    for n, pt in above:
        c = QPointF(pt.x(), band / 2)
        p.setPen(QPen(QColor("#E0457B"), 2))
        p.drawLine(QPointF(pt.x(), band / 2 + 12), QPointF(pt.x(), band + pt.y()))
        _badge(p, c, n)
    for n, pt in inline:
        _badge(p, QPointF(pt.x(), pt.y() + band), n)
    p.end()
    return out


def at(w: QWidget, win: QWidget, dx: float = 0.5, dy: float = 0.5) -> QPoint:
    return w.mapTo(win, QPoint(int(w.width() * dx), int(w.height() * dy)))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from gantt.settings import load_view
    from gantt.ui.main_window import MainWindow
    from gantt.ui.theme import apply_theme

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    from gantt.settings import qsettings
    qsettings().setValue("update/auto", False)     # снимки — без проверки обновлений
    s = load_view()
    s.hover_card = False
    s.labels = "names"
    s.zoom_ppd = 1.6
    apply_theme(app, s)
    win = MainWindow(s)
    win.resize(WIDTH, 760)
    win.show()
    print("Картинки руководства:", OUT)

    save(grab(win), "start.png")

    win.open_demo()
    win.expand_to_level(2)
    ds = win.ds
    wait(300)
    # раскрыть один проект с заданиями — чтобы были видны задания разных видов
    proj = next(n for n in ds.walk() if ds.role(n) == "project" and len(n.children) >= 5)
    idx = win.proxy.mapFromSource(win.model.index_for(proj))
    win.table.expand(idx)
    tasks = [c for c in proj.children if c.source("planEnd") and not c.raw.get("milestone")]
    win.push(tasks[0], {"planEnd": tasks[0].source("planEnd") + timedelta(days=14)}, "демо")
    win.splitter.setSizes([520, WIDTH - 520])
    win.table.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtTop)
    win.timeline.scroll_to_date(win.timeline.today, 0.7)
    wait(400)
    pm = grab(win)
    header = win.table.header()
    from PySide6.QtWidgets import QToolButton
    from gantt.ui.widgets import Toast

    def button(action) -> QWidget:
        found = [b for b in win.findChildren(QToolButton) if b.defaultAction() is action and b.isVisible()]
        return found[0] if found else win._tool_widget(action)

    def top(w: QWidget, dx: float = 0.5) -> QPoint:
        return w.mapTo(win, QPoint(int(w.width() * dx), 4))

    if Toast.current is not None:
        Toast.current.hide()
    pm = grab(win)
    header = win.table.header()
    tv, lv = win.table.viewport(), win.timeline.viewport()
    groups = [win.table.row_rect(n) for n in ds.walk() if ds.role(n) == "group"]   # строка раздела: справа пусто
    group_y = next((r.center().y() for r in groups if r is not None and r.top() > tv.height() * 0.4),
                   int(tv.height() * 0.6))
    above = [
        (1, top(button(win.a_open))),
        (2, top(button(win.a_filters))),
        (3, top(button(win.a_mode_all), 1.0)),
        (4, top(button(win.zoom_actions["quarter"]))),
        (5, top(win.changes_btn)),
    ]
    inline = [
        (6, header.mapTo(win, QPoint(header.levels_width() + 12, header.height() - 12))),
        (7, tv.mapTo(win, QPoint(tv.width() - 60, group_y))),
        (8, lv.mapTo(win, QPoint(60, int(lv.height() * 0.72)))),
        (9, at(win.legend, win, 0.8, 0.5)),
        (10, at(win.statusBar(), win, 0.3, 0.5)),
    ]
    save(mark(pm, inline, above), "main.png")

    tl = win.timeline
    r = tl.viewport().rect()
    band = tl.viewport().grab(r.adjusted(0, 0, 0, -max(0, r.height() - 330)))
    save(band, "timeline.png", 900)

    win.show_legend()
    save(grab(win.legend_pop), "legend.png", 760)
    win.legend_pop.close_()
    wait(250)

    win.toggle_filters(True)
    save(grab(win.filters_pop), "filters.png", 900)
    win.toggle_filters(False)
    wait(250)

    win.open_settings()
    if win.settings_pop.is_open():
        save(grab(win.settings_pop), "settings.png", 1100)
        win.settings_pop.close_()
    wait(250)

    win.theme_btn.click()
    save(grab(win.theme_pop), "theme.png", 560)
    win.theme_pop.close_()
    wait(250)

    win.toggle_changes(True)
    save(grab(win.changes_pop), "changes.png", 620)
    win.toggle_changes(False)
    wait(250)

    from gantt.ui.dialogs import NodeEditDialog
    from gantt.ui.theme import palette_names
    dlg = NodeEditDialog(ds, tasks[0], win.palette_cache, palette_names(win.settings), None, win)
    dlg.show()
    save(grab(dlg), "edit.png", 620)
    dlg.close()

    from gantt.ui.export import SaveAsDialog
    dlg = SaveAsDialog(win.qs, ds.title, win)
    dlg.show()
    save(grab(dlg), "save-as.png", 820)
    dlg.close()

    from gantt.ui.server import ConnectionDialog
    dlg = ConnectionDialog(win)
    dlg.show()
    save(grab(dlg), "connect.png", 860)
    dlg.close()

    from gantt.ui import wizard
    csv = TMP / "Задачи отдела.csv"
    win.write_as("csv", str(csv), {})

    def shot_and_cancel(self):
        self.show()
        save(grab(self), "wizard.png", 1000)
        self.close()
        return 0

    wizard.TableWizard.exec = shot_and_cancel
    win.open_file(str(csv), wizard=True)

    from gantt.update import Manifest
    from gantt.ui.update import UpdateDialog
    notes = ("- Новая колонка «Трудозатраты».\n- Фильтр по инициатору запоминается.\n"
             "- Исправлено: подписи месяцев в шапке при прокрутке.")
    dlg = UpdateDialog(win, Manifest("1.1.0", "GanttSetup-1.1.0.exe", size=51_800_000, date="2026-10-15",
                                     notes=notes, base=str(TMP)))
    dlg.show()
    save(grab(dlg), "update.png", 620)
    dlg.close()

    from gantt.ui import setup
    setup.data_dir = lambda: Path(os.environ.get("LOCALAPPDATA", "C:/")) / "Гант"   # как у всех, а не временная
    dlg = setup.SetupDialog(Path("GanttSetup-1.0.0.exe"))
    dlg.folder.setText("C:\\Users\\Пользователь\\AppData\\Local\\Programs\\Гант")   # без имени того, кто собирал
    dlg.show()
    save(grab(dlg), "setup.png", 640)
    dlg.close()

    win.close()
    win.store.close()


if __name__ == "__main__":
    main()
