"""Локальное хранилище (SQLite): правки пользователя по наборам данных. Переживают перезапуск и новые выгрузки."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .model import DATE_FIELDS
from .paths import data_dir


def _encode(fld: str, value) -> str:
    if fld in DATE_FIELDS:
        value = value.isoformat() if value else None
    return json.dumps(value, ensure_ascii=False)


def _decode(fld: str, text: str):
    value = json.loads(text)
    if fld in DATE_FIELDS:
        return date.fromisoformat(value) if value else None
    return value


class Store:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else data_dir() / "data.sqlite"
        self.db = sqlite3.connect(self.path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS overrides (
            dataset TEXT NOT NULL, node_id TEXT NOT NULL, field TEXT NOT NULL,
            value TEXT NOT NULL, source_value TEXT, updated_at TEXT NOT NULL,
            PRIMARY KEY (dataset, node_id, field))""")
        self.db.commit()

    def load(self, dataset: str) -> dict[tuple[str, str], object]:
        rows = self.db.execute("SELECT node_id, field, value FROM overrides WHERE dataset = ?", (dataset,))
        return {(nid, fld): _decode(fld, val) for nid, fld, val in rows}

    def load_sources(self, dataset: str) -> dict[tuple[str, str], object]:
        """Значения источника, от которых сделаны правки, — чтобы заметить, что источник с тех пор изменился."""
        rows = self.db.execute("SELECT node_id, field, source_value FROM overrides WHERE dataset = ?", (dataset,))
        return {(nid, fld): _decode(fld, src) if src is not None else None for nid, fld, src in rows}

    def save(self, dataset: str, node_id: str, fld: str, value, source_value) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO overrides VALUES (?, ?, ?, ?, ?, ?)",
            (dataset, node_id, fld, _encode(fld, value), _encode(fld, source_value),
             datetime.now().isoformat(timespec="seconds")))
        self.db.commit()

    def delete(self, dataset: str, node_id: str, fld: str) -> None:
        self.db.execute("DELETE FROM overrides WHERE dataset = ? AND node_id = ? AND field = ?",
                        (dataset, node_id, fld))
        self.db.commit()

    def close(self) -> None:
        self.db.close()
