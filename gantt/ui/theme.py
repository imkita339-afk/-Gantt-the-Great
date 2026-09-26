"""Темы, палитры проектов, цвета статусов и названия цветов словами."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from ..paths import resource_path
from ..settings import ViewSettings


@dataclass
class Tokens:
    title: str
    dark: bool
    bg: str
    panel: str
    panel2: str
    section_row: str
    line: str
    grid: str
    grid_strong: str
    ink: str
    ink2: str
    ink3: str
    today: str
    weekend: str
    group_bar: str


THEMES = {
    "light": Tokens("Светлая", False, bg="#F1F0EC", panel="#FFFFFF", panel2="#F7F6F3", section_row="#F0EEE8",
                    line="#E1DFD8", grid="#EFEDE8", grid_strong="#E0DDD5", ink="#1C1E23", ink2="#4F545D",
                    ink3="#6A6F78", today="#C2185B", weekend="#F7F5F0", group_bar="#9AA0A8"),
    "sepia": Tokens("Сепия", False, bg="#F1E9D8", panel="#FBF6EC", panel2="#F4ECDC", section_row="#EFE4CF",
                    line="#DFD0B4", grid="#EFE6D4", grid_strong="#E2D4BA", ink="#3B2F22", ink2="#5E4E3C",
                    ink3="#7A6852", today="#B8336A", weekend="#F5ECDC", group_bar="#A38F74"),
    "nord": Tokens("Северная", False, bg="#E9EDF3", panel="#FFFFFF", panel2="#F2F4F8", section_row="#E7EBF2",
                   line="#D5DCE7", grid="#ECEFF4", grid_strong="#DAE0EA", ink="#2E3440", ink2="#434C5E",
                   ink3="#5C6780", today="#BF3F5E", weekend="#F1F3F7", group_bar="#8893A8"),
    "mint": Tokens("Мятная", False, bg="#E8F2EE", panel="#FFFFFF", panel2="#F1F8F5", section_row="#E2EEE8",
                   line="#CCE0D6", grid="#E8F1EC", grid_strong="#D3E4DA", ink="#1F2D27", ink2="#3E5249",
                   ink3="#56695F", today="#C2185B", weekend="#EEF5F1", group_bar="#8BA497"),
    "contrast": Tokens("Контрастная", False, bg="#FFFFFF", panel="#FFFFFF", panel2="#F0F0F0", section_row="#E9E9E9",
                       line="#8F8F8F", grid="#DEDEDE", grid_strong="#B5B5B5", ink="#000000", ink2="#141414",
                       ink3="#383838", today="#D00050", weekend="#F4F4F4", group_bar="#4D4D4D"),
    "dark": Tokens("Тёмная", True, bg="#121418", panel="#1A1D22", panel2="#20242A", section_row="#22262C",
                   line="#2C3139", grid="#23272D", grid_strong="#30353D", ink="#E7E9ED", ink2="#B4BAC4",
                   ink3="#8F96A1", today="#FF5C93", weekend="#1E2126", group_bar="#7C838D"),
    "graphite": Tokens("Графит", True, bg="#151515", panel="#1D1D1D", panel2="#242424", section_row="#292929",
                       line="#343434", grid="#262626", grid_strong="#373737", ink="#EDEDED", ink2="#BDBDBD",
                       ink3="#979797", today="#FF6B8B", weekend="#212121", group_bar="#7A7A7A"),
    "midnight": Tokens("Полночь", True, bg="#0D1322", panel="#121A2D", panel2="#172139", section_row="#1B2641",
                       line="#25314D", grid="#19233D", grid_strong="#27335A", ink="#E6EAF5", ink2="#B3BCD6",
                       ink3="#8E99BA", today="#FF6FA3", weekend="#141D33", group_bar="#6E7BA0"),
}

ACCENTS = [("#2F5FD0", "Синий"), ("#0F8A83", "Бирюзовый"), ("#7452C9", "Фиолетовый"),
           ("#C2563A", "Терракотовый"), ("#2E8B57", "Зелёный"), ("#3A4250", "Графит")]

# палитры проектов: (название, [(оттенок, название цвета)], насыщенность)
PALETTES = {
    "pastel": ("Пастель", [(210, "Синий"), (145, "Зелёный"), (30, "Персиковый"), (275, "Фиолетовый"),
                           (350, "Розовый"), (180, "Бирюзовый"), (50, "Песочный"), (240, "Лавандовый"),
                           (105, "Салатовый"), (320, "Сиреневый"), (12, "Коралловый"), (195, "Голубой")], 0.50),
    "warm": ("Тёплая пастель", [(8, "Красный"), (28, "Абрикосовый"), (42, "Песочный"), (55, "Жёлтый"),
                                (335, "Малиновый"), (350, "Розовый"), (18, "Коралловый"), (36, "Персиковый"),
                                (48, "Горчичный"), (320, "Пурпурный"), (300, "Сливовый"), (65, "Лимонный")], 0.55),
    "cool": ("Холодная пастель", [(205, "Голубой"), (220, "Синий"), (235, "Васильковый"), (255, "Лавандовый"),
                                  (270, "Фиолетовый"), (190, "Бирюзовый"), (175, "Морская волна"), (160, "Мятный"),
                                  (145, "Зелёный"), (285, "Сиреневый"), (197, "Небесный"), (228, "Индиго")], 0.45),
    "muted": ("Приглушённая", [(210, "Серо-синий"), (145, "Серо-зелёный"), (30, "Бежевый"), (275, "Серо-фиолетовый"),
                               (350, "Пыльно-розовый"), (180, "Серо-бирюзовый"), (50, "Оливковый"),
                               (240, "Серо-лавандовый"), (105, "Шалфейный"), (320, "Пыльно-сиреневый"),
                               (12, "Терракотовый"), (195, "Серо-голубой")], 0.24),
}

STATUS_HUES = {  # (оттенок, насыщенность)
    "done": (145, 0.42), "inProgress": (215, 0.55), "overdue": (20, 0.75), "notStarted": (215, 0.10),
    "onHold": (45, 0.55), "release": (265, 0.50), "cancelled": (0, 0.0),
}

HUE_NAMES = [(8, "Красный"), (20, "Коралловый"), (40, "Персиковый"), (52, "Песочный"), (68, "Жёлтый"),
             (110, "Салатовый"), (155, "Зелёный"), (172, "Мятный"), (190, "Бирюзовый"), (205, "Голубой"),
             (232, "Синий"), (252, "Лавандовый"), (290, "Фиолетовый"), (330, "Сиреневый"), (352, "Розовый"),
             (361, "Красный")]


def color_name(c: QColor) -> str:
    """Название цвета словами — примерно так, как его назовёт человек."""
    h, s, l, _a = c.getHslF()
    if s < 0.12 or h < 0:
        if l > 0.93:
            return "Белый"
        if l > 0.7:
            return "Светло-серый"
        if l > 0.4:
            return "Серый"
        return "Графитовый" if l > 0.15 else "Чёрный"
    deg = h * 360
    name = next(n for bound, n in HUE_NAMES if deg < bound)
    if l < 0.32:
        return "Тёмно-" + name.lower()
    return name


def palette_names(s: ViewSettings) -> list[str]:
    return [name for _h, name in PALETTES.get(s.palette, PALETTES["pastel"])[1]]


def resolve_theme(name: str) -> Tokens:
    if name == "system":
        hints = QGuiApplication.styleHints()
        name = "dark" if hints.colorScheme() == Qt.ColorScheme.Dark else "light"
    return THEMES.get(name, THEMES["light"])


def _lightness(s: ViewSettings, t: Tokens) -> float:
    base = s.pastel_lightness / 100
    return base - 0.2 if t.dark else base


def _sat(sat: float, t: Tokens) -> float:
    return sat * 0.72 if t.dark else sat  # на тёмном фоне пастель без «неона»


def project_palette(s: ViewSettings, t: Tokens) -> list[QColor]:
    _title, hues, sat = PALETTES.get(s.palette, PALETTES["pastel"])
    light = _lightness(s, t)
    return [QColor.fromHslF(h / 360, _sat(sat, t), light) for h, _name in hues]


def status_color(kind: str, s: ViewSettings, t: Tokens) -> QColor:
    h, sat = STATUS_HUES.get(kind, STATUS_HUES["inProgress"])
    return QColor.fromHslF(h / 360, _sat(sat, t), _lightness(s, t))


def scale_color(order: int, top: int, s: ViewSettings, t: Tokens) -> QColor:
    """Цвет значения шкалы (приоритет, срочность, влияние): от зелёного (низкое) к красному (высокое)."""
    frac = (order - 1) / max(1, top - 1)
    return QColor.fromHslF((130 - 125 * frac) / 360, 0.55, _lightness(s, t))


def deep(c: QColor) -> QColor:
    """Более насыщенный и тёмный тон того же цвета."""
    h, s, l, a = c.getHslF()
    return QColor.fromHslF(max(h, 0), min(1.0, s + 0.18), max(0.22, l - 0.30), a)


def tone(c: QColor, dl: float) -> QColor:
    """Тот же оттенок, светлее (dl > 0) или темнее (dl < 0) — остаётся пастельным."""
    h, s, l, a = c.getHslF()
    return QColor.fromHslF(max(h, 0), s, min(0.97, max(0.12, l + dl)), a)


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


def _alpha(c: QColor, a: int) -> QColor:
    c = QColor(c)
    c.setAlpha(a)
    return c


def row_colors(tokens: Tokens, accent: QColor) -> dict:
    """Цвета строк — общие для таблицы и шкалы, чтобы строка читалась как одна полоса через всё окно."""
    panel, ink = QColor(tokens.panel), QColor(tokens.ink)
    return {
        "panel": panel,
        "zebra": mix(panel, ink, 0.035 if tokens.dark else 0.024),
        "group": QColor(tokens.section_row),
        "select": mix(panel, accent, 0.24 if tokens.dark else 0.15),
        "select_overlay": _alpha(accent, 50 if tokens.dark else 36),
        "hover": _alpha(accent, 24 if tokens.dark else 15),
        "accent": QColor(accent),
        "red": QColor("#FF6B86" if tokens.dark else "#C62A4A"),
        "amber": QColor("#E6A74A" if tokens.dark else "#A35E00"),
        "green": QColor("#62C48E" if tokens.dark else "#23804E"),
    }


def plan_colors(base: QColor, t: Tokens) -> tuple[QColor, QColor, QColor]:
    """План — светлая дорожка с рамкой, факт — насыщенная полоса того же цвета: (заливка плана, рамка, факт)."""
    fill = _alpha(base, 92 if t.dark else 118)
    edge = tone(base, 0.10) if t.dark else tone(base, -0.30)
    fact = tone(base, 0.02) if t.dark else tone(base, -0.24)
    return fill, edge, fact


def apply_theme(app: QApplication, s: ViewSettings) -> Tokens:
    t = resolve_theme(s.theme)
    accent = QColor(s.accent)
    pal = QPalette()
    for role, color in ((QPalette.ColorRole.Window, t.bg), (QPalette.ColorRole.Base, t.panel),
                        (QPalette.ColorRole.AlternateBase, t.panel2), (QPalette.ColorRole.Button, t.panel2),
                        (QPalette.ColorRole.Text, t.ink), (QPalette.ColorRole.WindowText, t.ink),
                        (QPalette.ColorRole.ButtonText, t.ink), (QPalette.ColorRole.ToolTipBase, t.panel),
                        (QPalette.ColorRole.ToolTipText, t.ink), (QPalette.ColorRole.PlaceholderText, t.ink3),
                        (QPalette.ColorRole.Mid, t.line), (QPalette.ColorRole.Midlight, t.line),
                        (QPalette.ColorRole.Light, t.panel), (QPalette.ColorRole.Dark, t.grid_strong)):
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.ColorRole.Highlight, mix(QColor(t.panel), accent, 0.22 if t.dark else 0.16))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(t.ink))
    pal.setColor(QPalette.ColorRole.Link, accent)
    pal.setColor(QPalette.ColorRole.Accent, accent)
    app.setPalette(pal)
    app.setFont(QFont("Segoe UI", s.font_size))
    hover = mix(QColor(t.panel2), accent, 0.12).name()
    check = resource_path("gantt", "resources", "check.png").as_posix()
    checked = mix(QColor(t.panel2), accent, 0.22).name()
    app.setStyleSheet(f"""
        QToolBar {{ background: {t.panel2}; border: none; border-bottom: 1px solid {t.line}; spacing: 4px; padding: 4px 8px; }}
        QToolButton {{ padding: 4px 7px; border-radius: 6px; }}
        QToolButton:hover {{ background: {hover}; }}
        QToolButton:checked {{ background: {checked}; color: {t.ink}; font-weight: 600; }}
        QToolButton::menu-indicator {{ image: none; width: 0; }}
        QLineEdit, QComboBox, QSpinBox, QDateEdit {{ border: 1px solid {t.line}; border-radius: 6px; padding: 3px 6px;
            background: {t.panel}; }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus {{ border-color: {accent.name()}; }}
        QTreeView, QListWidget, QTableWidget {{ border: none; background: {t.panel}; }}
        QHeaderView::section {{ background: {t.panel2}; color: {t.ink3}; border: none;
            border-bottom: 1px solid {t.line}; border-right: 1px solid {t.grid}; padding: 0 8px; font-weight: 600; }}
        QSplitter::handle {{ background: {t.line}; }}
        QSplitter::handle:hover {{ background: {accent.name()}; }}
        QStatusBar {{ background: {t.panel2}; color: {t.ink3}; border-top: 1px solid {t.line}; }}
        QToolTip {{ background: {t.panel}; color: {t.ink}; border: 1px solid {t.line}; padding: 6px; }}
        QMenu {{ background: {t.panel}; border: 1px solid {t.line}; padding: 4px; }}
        QMenu::item {{ padding: 5px 22px 5px 30px; border-radius: 5px; }}
        QMenu::indicator {{ width: 13px; height: 13px; left: 9px; border-radius: 3px; }}
        QMenu::indicator:non-exclusive:unchecked, QMenu::indicator:exclusive:unchecked {{
            border: 1px solid {t.ink3}; background: {t.panel}; }}
        QMenu::indicator:non-exclusive:checked, QMenu::indicator:exclusive:checked {{
            border: 1px solid {accent.name()}; background: {accent.name()}; image: url("{check}"); }}
        QMenu::item:selected {{ background: {mix(QColor(t.panel), accent, 0.16).name()}; color: {t.ink}; }}
        QCheckBox::indicator, QListView::indicator, QTreeView::indicator {{ width: 14px; height: 14px;
            border-radius: 3px; }}
        QCheckBox::indicator:unchecked, QListView::indicator:unchecked, QTreeView::indicator:unchecked {{
            border: 1px solid {t.ink3}; background: {t.panel}; }}
        QCheckBox::indicator:checked, QListView::indicator:checked, QTreeView::indicator:checked {{
            border: 1px solid {accent.name()}; background: {accent.name()}; image: url("{check}"); }}
        QCheckBox::indicator:disabled, QListView::indicator:disabled, QTreeView::indicator:disabled {{
            border-color: {t.line}; background: {t.panel2}; }}
        QCheckBox::indicator:hover, QListView::indicator:hover {{ border-color: {accent.name()}; }}
        QMenu::separator {{ height: 1px; background: {t.line}; margin: 4px 8px; }}
        QPushButton {{ padding: 5px 14px; border: 1px solid {t.line}; border-radius: 7px; background: {t.panel}; }}
        QPushButton:hover {{ border-color: {accent.name()}; }}
        QPushButton#primary {{ background: {accent.name()}; color: white; border-color: {accent.name()}; font-weight: 600; }}
        QDockWidget::title {{ background: {t.panel2}; padding: 6px 10px; border-bottom: 1px solid {t.line}; }}
        QGroupBox {{ border: 1px solid {t.line}; border-radius: 8px; margin-top: 14px; padding-top: 6px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t.ink2}; font-weight: 600; }}
        QLabel#hint {{ color: {t.ink3}; }}
    """)
    return t
