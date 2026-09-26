"""Адаптеры форматов: каждый переводит свой файл в документ gantt-exchange. Ядро знает только этот документ."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..exchange import OpenError
from ..paths import data_dir


@dataclass
class ReadResult:
    document: dict
    warnings: list[str] = field(default_factory=list)
    source_label: str = ""
    kind: str = "full"          # full | changes | results — что за документ


class NeedsWizard(Exception):
    """Таблица неизвестного вида — нужен мастер сопоставления колонок."""

    def __init__(self, table):
        super().__init__("Нужен мастер")
        self.table = table


OPEN_FILTER = ("Все поддерживаемые (*.json *.xlsx *.xlsm *.csv *.tsv *.txt *.xml);;Файл обмена (*.json);;"
               "Excel (*.xlsx *.xlsm);;CSV (*.csv *.tsv *.txt);;MS Project XML (*.xml)")

# «Сохранить как»: ключ, название, расширение, описание, что не сохранится (None — без потерь)
WRITERS = [
    ("json", "Файл обмена", ".gantt.json",
     "Всё без потерь: данные, справочники, правки. Откроется в программе на любом ПК.", None),
    ("xlsx", "Excel — Гант-таблица", ".xlsx",
     "Данные с группировкой по уровням и справочники; по желанию — лист «Диаграмма». Откроется обратно.",
     "Правки войдут в сами значения — отдельного списка правок не будет. Не сохранятся версии узлов."),
    ("csv", "CSV", ".csv", "Одна таблица для других программ (разделитель «;», UTF-8).",
     "Правки войдут в значения. Не сохранятся: цвета справочников, версии, зависимости, типы своих полей."),
    ("msproject", "MS Project XML", ".xml", "Для MS Project, ProjectLibre, GanttProject.",
     "Правки войдут в значения. Статус, приоритет, срочность, влияние — в текстовые поля; цвета не сохранятся."),
    ("pdf", "PDF", ".pdf", "Картинка диаграммы для печати и писем.", "Это картинка — данные из неё не читаются."),
    ("png", "PNG", ".png", "Картинка диаграммы.", "Это картинка — данные из неё не читаются."),
]


# ---------- шаблоны таблиц ----------
def _templates_path() -> Path:
    return data_dir() / "templates.json"


def load_templates() -> dict:
    try:
        return json.loads(_templates_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_template(mapping) -> None:
    data = load_templates()
    data[mapping.template] = mapping.to_json()
    _templates_path().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def delete_template(name: str) -> None:
    data = load_templates()
    data.pop(name, None)
    _templates_path().write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def find_template(table):
    """Шаблон, у которого те же заголовки, — таблица откроется без мастера."""
    from .table import Mapping, guess_header_row, signature

    hdr = guess_header_row(table)
    for i in {hdr, *range(0, min(10, len(table.rows)))}:
        sig = signature(table, i)
        if not sig:
            continue
        for name, d in load_templates().items():
            m = Mapping.from_json(d)
            if m.signature == sig and m.header_row == i:
                return m
    return None


def table_result(table, mapping) -> ReadResult:
    from ..exchange import check
    from .table import convert

    try:
        doc, warnings = convert(table, mapping)
    except ValueError as e:
        raise OpenError(str(e))
    res = check(doc)
    if res.errors:
        raise OpenError("Таблица прочитана, но результат не прошёл проверку.", res.errors[:50])
    if len(warnings) > 20:
        warnings = warnings[:20] + [f"…и ещё {len(warnings) - 20} похожих замечаний"]
    label = f"Таблица · шаблон «{mapping.template}»" if mapping.template else "Таблица"
    return ReadResult(doc, warnings + res.warnings, label)


# ---------- открыть что угодно ----------
def open_path(path: str | Path, wizard: bool = False) -> ReadResult:
    from . import gantt_table, json_format, msproject, report_1c, table

    p = Path(path)
    if not p.exists():
        raise OpenError(f"Файл не найден:\n{p}")
    ext = p.suffix.lower()
    if ext == ".json":
        return json_format.read(p)
    if ext == ".xml":
        if not msproject.is_msproject(p):
            raise OpenError("Это XML, но не MS Project.", ["Открываются файлы MS Project XML (MSPDI)."])
        try:
            doc, warnings = msproject.read(p)
        except Exception as e:  # noqa: BLE001
            raise OpenError("Не удалось прочитать файл MS Project.", [str(e)])
        return _checked(doc, warnings, "MS Project XML")
    if ext in (".xlsx", ".xlsm"):
        if not wizard:
            try:
                doc = gantt_table.read_xlsx(p)
            except Exception:  # noqa: BLE001 — не наш формат — пробуем дальше
                doc = None
            if doc is not None:
                return _checked(doc, [], "Excel · Гант-таблица")
            try:
                return report_1c.read(p)
            except OpenError as e:
                if "не похожа" not in e.message:
                    raise
        try:
            data = table.read_xlsx(p)
        except Exception as e:  # noqa: BLE001
            raise OpenError("Не удалось прочитать Excel-файл.", [str(e)])
        if not wizard and find_template(data) is None:  # шаблон мог быть сделан для другого листа
            for d in load_templates().values():
                sheet = d.get("sheet")
                if sheet and sheet != data.sheet and sheet in data.sheets:
                    other = table.read_xlsx(p, sheet)
                    if find_template(other) is not None:
                        data = other
                        break
        return _table_or_wizard(data, wizard)
    if ext in (".csv", ".tsv", ".txt"):
        data = table.read_csv(p)
        if not wizard and data.rows and gantt_table.is_gantt_table(data.rows[0]):
            return _checked(gantt_table.read_rows(data.rows, p.name), [], "CSV · Гант-таблица")
        return _table_or_wizard(data, wizard)
    raise OpenError("Этот тип файла не поддерживается.",
                    ["Открываются: файл обмена (.json), Excel (.xlsx), CSV (.csv), MS Project XML (.xml)."])


def _table_or_wizard(data, wizard: bool) -> ReadResult:
    if not wizard:
        m = find_template(data)
        if m is not None:
            return table_result(data, m)
    raise NeedsWizard(data)


def _checked(doc: dict, warnings: list[str], label: str) -> ReadResult:
    from ..exchange import check

    res = check(doc)
    if res.errors:
        raise OpenError("Файл прочитан, но результат не прошёл проверку.", res.errors[:50])
    return ReadResult(doc, warnings + res.warnings, label)
