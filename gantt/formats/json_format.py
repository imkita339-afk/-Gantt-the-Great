"""Файл обмена (.json): снимок, а также пакет правок (changes) и результаты (results)."""
from __future__ import annotations

from pathlib import Path

from ..exchange import OpenError, check, is_exchange, read_json
from . import ReadResult

LABELS = {"full": "Файл обмена", "changes": "Файл обмена · правки", "results": "Файл обмена · результаты",
          "incremental": "Файл обмена · изменения источника"}


def read(path: Path) -> ReadResult:
    doc = read_json(path)
    if not is_exchange(doc):
        raise OpenError("Это не файл обмена Ганта.", ["В файле нет признака \"format\": \"gantt-exchange\"."])
    res = check(doc)
    if res.errors:
        raise OpenError("Файл не прошёл проверку. Текущие данные не изменены.", res.errors)
    kind = doc["meta"]["kind"]
    return ReadResult(doc, res.warnings, LABELS.get(kind, "Файл обмена"), kind)
