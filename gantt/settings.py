"""Настройки внешнего вида: всё, что пользователь подстраивает под себя. Запоминаются между запусками."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields

from PySide6.QtCore import QSettings

from .paths import data_dir

COLUMN_KEYS = ["number", "executors", "status", "progress", "due", "start", "end", "fact_start", "fact_end",
               "priority", "urgency", "impact", "initiator", "custom"]
NEW_VISIBLE = {"due"}   # новые колонки, которые показываются и тем, у кого настройки уже сохранены


@dataclass
class ViewSettings:
    theme: str = "light"              # ключ из THEMES или system
    accent: str = "#2F5FD0"
    palette: str = "pastel"           # палитра проектов: pastel | warm | cool | muted
    pastel_lightness: int = 82        # светлота пастели, %
    task_colors: str = "status"       # цвет полос заданий: status | project
    font_size: int = 10
    row_height: int = 28
    bar_radius: int = 5
    labels: str = "progress"          # подписи у полос: none | progress | names
    show_fact: bool = True
    show_today: bool = True
    show_current_week: bool = True
    show_weekends: bool = True
    show_scale_marks: bool = True     # метки приоритета/срочности/влияния на полосе
    animations: bool = True           # плавный масштаб, прокрутка, раскрытие, смена темы, отклик кнопок
    potato: bool = False              # режим картошки: без анимаций, теней и сглаживания — для слабых ПК
    show_legend: bool = True          # строка обозначений под диаграммой
    zebra: bool = True                # строки через одну чуть темнее — глазу легче идти по строке
    hover_card: bool = True           # карточка задания при наведении
    hidden_columns: list[str] = field(default_factory=lambda: ["start", "end", "fact_start", "fact_end",
                                                               "priority", "urgency", "impact", "initiator",
                                                               "custom"])
    column_groups: list = field(default_factory=list)  # [{"cols": [ключи колонок], "collapsed": bool}]
    zoom_ppd: float = 5.0             # пикселей на день
    reopen_last: bool = True
    watch_file: bool = True           # перечитывать файл, когда он меняется
    me: str = ""                      # «кто я» — для режима «Исполнитель» (имя из справочника людей)

    @classmethod
    def defaults(cls) -> "ViewSettings":
        return cls()

    @property
    def smooth(self) -> bool:
        """Анимации включены и не выключены режимом картошки."""
        return self.animations and not self.potato


def qsettings() -> QSettings:
    return QSettings(str(data_dir() / "settings.ini"), QSettings.Format.IniFormat)


def load_view() -> ViewSettings:
    raw = qsettings().value("view/json", "")
    s = ViewSettings()
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    known = {f.name for f in fields(ViewSettings)}
    for k, v in data.items():
        if k not in known:
            continue
        cur = getattr(s, k)
        if isinstance(cur, float) and isinstance(v, (int, float)):
            setattr(s, k, float(v))
        elif isinstance(v, type(cur)):
            setattr(s, k, v)
    if data:  # колонки, которых не было в прежних настройках, по умолчанию скрыты
        known = data.get("known_columns", ["number", "executors", "status", "progress", "start", "end"])
        for key in COLUMN_KEYS:
            if key not in known and key not in s.hidden_columns and key not in NEW_VISIBLE:
                s.hidden_columns.append(key)
    return s


def save_view(s: ViewSettings) -> None:
    qs = qsettings()
    data = asdict(s)
    data["known_columns"] = COLUMN_KEYS
    qs.setValue("view/json", json.dumps(data, ensure_ascii=False))
    qs.sync()
