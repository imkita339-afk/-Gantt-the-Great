"""Рисует иконку программы: синий квадрат с тремя пастельными полосами Ганта.

Каждый размер рисуется отдельно (а не уменьшается из большого), чтобы на 16×16 полосы оставались чёткими.
Результат: gantt/resources/gantt.ico (16–256) и gantt/resources/gantt.png (256);
заодно — галочка для меню gantt/resources/check.png.
Запуск: .venv\\Scripts\\python tools\\make_icon.py
"""
import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "gantt" / "resources"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
BARS = [  # (верх, начало, конец) в долях стороны, цвет
    ((0.235, 0.20, 0.60), "#FBD3AE"),
    ((0.435, 0.34, 0.82), "#BDE8CF"),
    ((0.635, 0.26, 0.58), "#D6C9F7"),
]


def draw(n: int) -> QImage:
    img = QImage(n, n, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    inset = 0 if n <= 24 else n * 0.04
    r = QRectF(inset, inset, n - 2 * inset, n - 2 * inset)
    g = QLinearGradient(0, r.top(), 0, r.bottom())
    g.setColorAt(0, QColor("#3B6CE0"))
    g.setColorAt(1, QColor("#2449A8"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawRoundedRect(r, n * 0.22, n * 0.22)
    h = max(2.0, round(n * 0.14)) if n <= 32 else n * 0.135
    for (top, x1, x2), color in BARS:
        y = round(n * top) if n <= 32 else n * top
        a, b = (round(n * x1), round(n * x2)) if n <= 32 else (n * x1, n * x2)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(a, y, b - a, h), h / 2, h / 2)
    if n >= 32:  # линия «сегодня» — видна только на крупных размерах
        p.setBrush(QColor("#FF8FB1"))
        w = max(1.5, n * 0.028)
        p.drawRoundedRect(QRectF(n * 0.70 - w / 2, n * 0.17, w, n * 0.66), w / 2, w / 2)
    p.end()
    return img


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def write_ico(path: Path, images: list[tuple[int, bytes]]) -> None:
    """ICO с PNG внутри: заголовок, каталог и сами картинки."""
    offset = 6 + 16 * len(images)
    head, body = struct.pack("<HHH", 0, 1, len(images)), b""
    entries = b""
    for size, data in images:
        side = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset + len(body))
        body += data
    path.write_bytes(head + entries + body)


def draw_check() -> QImage:
    """Белая галочка для отмеченных пунктов меню (рисуется поверх квадрата акцентного цвета)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainterPath, QPen

    img = QImage(26, 26, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("#FFFFFF"), 3.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                  Qt.PenJoinStyle.RoundJoin))
    path = QPainterPath(QPointF(5.5, 13.5))
    path.lineTo(10.8, 18.6)
    path.lineTo(20.6, 7.6)
    p.drawPath(path)
    p.end()
    return img


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841
    OUT.mkdir(parents=True, exist_ok=True)
    images = [(n, png_bytes(draw(n))) for n in SIZES]
    write_ico(OUT / "gantt.ico", images)
    draw(256).save(str(OUT / "gantt.png"))
    draw_check().save(str(OUT / "check.png"))
    print("иконка:", OUT / "gantt.ico")


if __name__ == "__main__":
    main()
