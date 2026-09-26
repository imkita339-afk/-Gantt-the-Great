"""Файл обмена gantt-exchange: чтение, проверка (схема + смысл) и запись. Все сообщения — по-русски."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import Draft202012Validator

from .paths import resource_path

FIELD_TITLES = {
    "id": "Идентификатор", "parentId": "Родитель", "level": "Уровень", "order": "Порядок", "number": "Номер",
    "name": "Наименование", "description": "Описание", "executors": "Исполнители", "initiator": "Инициатор",
    "planStart": "План начала", "planEnd": "План окончания", "factStart": "Факт начала", "factEnd": "Факт окончания",
    "percent": "Процент", "status": "Статус", "priority": "Приоритет", "urgency": "Срочность", "impact": "Влияние",
    "category": "Категория", "color": "Цвет", "milestone": "Веха", "predecessors": "Зависимости", "url": "Ссылка",
    "custom": "Пользовательские поля", "version": "Версия", "editable": "Можно менять",
}
TYPE_TITLES = {"string": "строка", "number": "число", "integer": "целое число", "boolean": "да/нет",
               "array": "список", "object": "объект", "null": "пусто"}


class OpenError(Exception):
    """Файл не удалось открыть. message — для человека, errors — подробности по пунктам."""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.errors = errors or []


@dataclass
class CheckResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_validator: Draft202012Validator | None = None


def _get_validator() -> Draft202012Validator:
    global _validator
    if _validator is None:
        schema = json.loads(resource_path("schema", "gantt-exchange.schema.json").read_text(encoding="utf-8"))
        _validator = Draft202012Validator(schema)
    return _validator


def read_json(path: str | Path) -> dict:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")  # 1С может записать BOM
    except UnicodeDecodeError:
        raise OpenError("Файл не в кодировке UTF-8.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise OpenError(f"Это не JSON: строка {e.lineno}, позиция {e.colno} — {e.msg}.")


def write_json(doc: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def is_exchange(doc) -> bool:
    return isinstance(doc, dict) and doc.get("format") == "gantt-exchange"


def node_label(node) -> str:
    if not isinstance(node, dict):
        return "узел"
    number = f"№ {node['number']} " if node.get("number") else ""
    name = node.get("name") or "без названия"
    return f"Узел {number}«{name}» ({node.get('id', '?')})"


def _where(doc: dict, path) -> str:
    p = list(path)
    nodes = doc.get("nodes") if isinstance(doc, dict) else None
    if len(p) >= 2 and p[0] == "nodes" and isinstance(p[1], int) and isinstance(nodes, list) and p[1] < len(nodes):
        label = node_label(nodes[p[1]])
        rest = p[2:]
        return label + (f", поле «{FIELD_TITLES.get(rest[0], rest[0])}»" if rest else "")
    return ".".join(map(str, p)) or "Документ"


def _message(err) -> str:
    v, inst, val = err.validator, err.instance, err.validator_value
    if v == "required":
        missing = [k for k in val if not isinstance(inst, dict) or k not in inst]
        name = missing[0] if missing else "?"
        return f"нет обязательного поля «{FIELD_TITLES.get(name, name)}»"
    if v == "pattern":
        if "T[0-9]" in val:
            return f"ожидались дата и время ISO 8601, в файле «{inst}»"
        if "[0-9]{4}-[0-9]{2}-[0-9]{2}" in val:
            return f"ожидалась дата ГГГГ-ММ-ДД, в файле «{inst}»"
        if val.startswith("^1"):
            return f"версия схемы {inst} не поддерживается — программа понимает 1.x"
        if val.startswith("^#"):
            return f"ожидался цвет #RRGGBB, в файле «{inst}»"
        return f"значение «{inst}» не подходит по формату"
    if v == "type":
        types = val if isinstance(val, list) else [val]
        return f"ожидалось: {' или '.join(TYPE_TITLES.get(t, t) for t in types)}, в файле «{inst}»"
    if v == "enum":
        return f"недопустимое значение «{inst}»; допустимо: {', '.join(map(str, val))}"
    if v == "const":
        if list(err.path) == ["format"]:
            return "это не файл обмена Ганта (поле format не равно gantt-exchange)"
        return f"ожидалось «{val}», в файле «{inst}»"
    if v == "additionalProperties" and isinstance(inst, dict):
        known = set(err.schema.get("properties", {}))
        extra = sorted(k for k in inst if k not in known)
        return f"незнакомые поля: {', '.join(extra)}"
    if v in ("minimum", "maximum"):
        return f"значение {inst} вне допустимого диапазона"
    if v == "minLength":
        return "пустое значение"
    if v == "minItems":
        return "список не должен быть пустым"
    if v == "uniqueItems":
        return "значения в списке повторяются"
    if v is None or "False schema" in err.message:
        return "этот раздел не допускается для такого вида документа (meta.kind)"
    return err.message


def check(doc: dict) -> CheckResult:
    """Проверка по схеме и смысловая проверка. Возвращает все ошибки сразу."""
    res = CheckResult()
    for err in sorted(_get_validator().iter_errors(doc), key=lambda e: [str(x) for x in e.path]):
        res.errors.append(f"{_where(doc, err.path)}: {_message(err)}")
    if not isinstance(doc, dict):
        return res
    _check_meaning(doc, res)
    return res


def _check_meaning(doc: dict, res: CheckResult) -> None:
    """Ссылки, уникальность, циклы — то, что схема выразить не может. Пишем осторожно: документ может быть битым."""
    d = doc.get("dictionaries") if isinstance(doc.get("dictionaries"), dict) else {}
    kind = (doc.get("meta") or {}).get("kind") if isinstance(doc.get("meta"), dict) else None
    full = kind == "full"
    ids: dict[str, set] = {}
    for key, lst in d.items():
        if isinstance(lst, list):
            vals = [x.get("id") for x in lst if isinstance(x, dict)]
            ids[key] = set(vals)
            dup = [i for i, c in Counter(vals).items() if c > 1]
            if dup:
                res.errors.append(f"Справочник «{key}»: повторяются id {', '.join(map(str, dup[:5]))}")
    nodes = [n for n in doc.get("nodes") or [] if isinstance(n, dict)]
    by_id: dict[str, dict] = {}
    for n in nodes:
        if n.get("id") in by_id:
            res.errors.append(f"{node_label(n)}: id повторяется")
        by_id[n.get("id")] = n
    fields = {f.get("id"): f for f in d.get("customFields") or [] if isinstance(f, dict)}
    level_order = {x.get("id"): x.get("order") for x in d.get("levels") or [] if isinstance(x, dict)}
    refs = (("level", "levels"), ("status", "statuses"), ("priority", "priorities"), ("urgency", "urgencies"),
            ("impact", "impacts"), ("category", "categories"), ("initiator", "people"))
    for n in nodes:
        at = node_label(n)
        pairs = [(f, dic, n.get(f)) for f, dic in refs]
        pairs += [("executors", "people", p) for p in n.get("executors") or [] if isinstance(n.get("executors"), list)]
        for f, dic, v in pairs:
            if v is not None and (full or dic in ids) and v not in ids.get(dic, set()):
                res.errors.append(f"{at}, поле «{FIELD_TITLES.get(f, f)}»: значения «{v}» нет в справочнике {dic}")
        par = n.get("parentId")
        if par is not None and full and par not in by_id:
            res.errors.append(f"{at}: родителя {par} нет среди узлов")
        if par in by_id:
            lo, po = level_order.get(n.get("level")), level_order.get(by_id[par].get("level"))
            if isinstance(lo, int) and isinstance(po, int) and po >= lo:
                res.warnings.append(f"{at}: уровень не глубже уровня родителя")
        for k, v in (n.get("custom") or {}).items() if isinstance(n.get("custom"), dict) else []:
            f = fields.get(k)
            if f is None:
                if full or fields:
                    res.errors.append(f"{at}: поля custom.{k} нет в справочнике customFields")
            elif f.get("type") == "enum" and v is not None and v not in {x.get("id") for x in f.get("values") or []}:
                res.errors.append(f"{at}, поле «{f.get('name', k)}»: значения «{v}» нет среди допустимых")
            elif f.get("type") == "number" and v is not None and not isinstance(v, (int, float)):
                res.errors.append(f"{at}, поле «{f.get('name', k)}»: ожидалось число")
        for s, e in (("planStart", "planEnd"), ("factStart", "factEnd")):
            a, b = n.get(s), n.get(e)
            if isinstance(a, str) and isinstance(b, str) and a > b:
                res.warnings.append(f"{at}: {FIELD_TITLES[s].lower()} {a} позже, чем {FIELD_TITLES[e].lower()} {b}")
        for p in n.get("predecessors") or [] if isinstance(n.get("predecessors"), list) else []:
            if isinstance(p, dict) and full and p.get("id") not in by_id:
                res.errors.append(f"{at}: зависимость от несуществующего узла {p.get('id')}")
    for n in nodes:  # циклы в иерархии
        seen, cur = set(), n
        while isinstance(cur, dict) and cur.get("parentId") in by_id:
            if cur.get("id") in seen:
                res.errors.append(f"{node_label(n)}: цикл в иерархии")
                break
            seen.add(cur.get("id"))
            cur = by_id[cur["parentId"]]
    changes = [c for c in doc.get("changes") or [] if isinstance(c, dict)]
    dup = [i for i, c in Counter(c.get("changeId") for c in changes).items() if c > 1]
    if dup:
        res.errors.append(f"Правки: повторяются changeId {', '.join(map(str, dup))}")
    trunc = sum(1 for n in nodes if str(n.get("name", "")).endswith(("...", "…")))
    if trunc:
        res.warnings.append(f"Названий, обрезанных источником («...»): {trunc}")


def apply_incremental(base: dict, inc: dict) -> tuple[dict, dict, list[str]]:
    """Изменения источника (kind = incremental) поверх снимка того же набора.

    Узел из nodes заменяется целиком (это его текущее состояние), deleted удаляются вместе с потомками,
    которых нет в этом же документе. Возвращает новый снимок, счётчики и предупреждения.
    """
    import copy

    doc = copy.deepcopy(base)
    doc.pop("changes", None)                  # правки уже в хранилище — из снимка их не воскрешаем
    warnings: list[str] = []
    bm, im = doc["meta"], inc["meta"]
    if im.get("changedSince") and bm.get("cursor") and im["changedSince"] != bm["cursor"]:
        warnings.append("Изменения собраны не от той выгрузки, что открыта: часть изменений могла пропасть. "
                        "Надёжнее загрузить полный снимок.")
    for key, items in (inc.get("dictionaries") or {}).items():
        cur = doc["dictionaries"].setdefault(key, [])
        pos = {x["id"]: i for i, x in enumerate(cur)}
        for x in items:
            if x["id"] in pos:
                cur[pos[x["id"]]] = x
            else:
                cur.append(x)
    nodes = doc["nodes"]
    pos = {n["id"]: i for i, n in enumerate(nodes)}
    stats = {"updated": 0, "added": 0, "deleted": 0}
    incoming = {n["id"] for n in inc.get("nodes") or []}
    for n in inc.get("nodes") or []:
        if n["id"] in pos:
            nodes[pos[n["id"]]] = n
            stats["updated"] += 1
        else:
            pos[n["id"]] = len(nodes)
            nodes.append(n)
            stats["added"] += 1
    gone = set(inc.get("deleted") or [])
    children: dict[str, list[str]] = {}
    for n in nodes:
        children.setdefault(n.get("parentId"), []).append(n["id"])
    stack = list(gone)
    while stack:
        nid = stack.pop()
        for c in children.get(nid, []):
            if c not in gone and c not in incoming:
                gone.add(c)
                stack.append(c)
    before = len(nodes)
    doc["nodes"] = [n for n in nodes if n["id"] not in gone]
    stats["deleted"] = before - len(doc["nodes"])
    bm.update(kind="full", exportedAt=im.get("exportedAt", bm.get("exportedAt")))
    if im.get("cursor"):
        bm["cursor"] = im["cursor"]
    bm.pop("changedSince", None)
    return doc, stats, warnings
