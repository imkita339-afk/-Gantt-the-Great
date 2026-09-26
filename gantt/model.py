"""Модель данных: дерево узлов из документа gantt-exchange + слой правок пользователя + вычисляемое."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

DATE_FIELDS = ("planStart", "planEnd", "factStart", "factEnd")
EDIT_FIELDS = DATE_FIELDS + ("status", "color")
SCALES = {"priority": "priorities", "urgency": "urgencies", "impact": "impacts"}
DONE_KINDS = ("done", "cancelled")


def to_date(s) -> date | None:
    return date.fromisoformat(s) if isinstance(s, str) and s else None


def fmt(d: date | None, short: bool = True) -> str:
    if d is None:
        return "—"
    return d.strftime("%d.%m.%y" if short else "%d.%m.%Y")


@dataclass(eq=False)
class Node:
    id: str
    parent_id: str | None
    level: str
    order: float
    number: str | None
    name: str
    raw: dict
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = None
    row: int = 0
    depth: int = 0
    _src: dict = field(default_factory=dict, repr=False)

    def source(self, fld: str):
        """Значение поля, как его прислал источник (даты — объектами date). Разбор дат — один раз."""
        try:
            return self._src[fld]
        except KeyError:
            v = self.raw.get(fld)
            v = to_date(v) if fld in DATE_FIELDS else v
            self._src[fld] = v
            return v

    def set_source(self, fld: str, value) -> None:
        """Новое значение источника (например, после ответа сервера); value — как в файле обмена."""
        if value is None:
            self.raw.pop(fld, None)
        else:
            self.raw[fld] = value
        self._src.pop(fld, None)


class Dataset:
    """Набор данных = источник + его экземпляр (meta.source + meta.sourceName)."""

    def __init__(self, doc: dict, overrides: dict | None = None, today: date | None = None):
        self.doc = doc
        meta = doc["meta"]
        self.key = f"{meta['source']}|{meta.get('sourceName') or ''}"
        self.title = meta.get("title") or meta.get("sourceName") or meta["source"]
        self.today = today or date.today()
        d = doc.get("dictionaries", {})
        self.levels = {x["id"]: x for x in d.get("levels", [])}
        self.statuses = {x["id"]: x for x in d.get("statuses", [])}
        self.people = {x["id"]: x for x in d.get("people", [])}
        self.scales = {f: {x["id"]: x for x in d.get(key, [])} for f, key in SCALES.items()}
        self.custom_fields = {x["id"]: x for x in d.get("customFields", [])}
        self.nodes: dict[str, Node] = {}
        for i, n in enumerate(doc.get("nodes", [])):
            self.nodes[n["id"]] = Node(n["id"], n.get("parentId"), n["level"], n.get("order", i),
                                       n.get("number"), n["name"], n)
        self.roots: list[Node] = []
        for node in self.nodes.values():
            parent = self.nodes.get(node.parent_id) if node.parent_id else None
            node.parent = parent
            (parent.children if parent else self.roots).append(node)
        self._sort(self.roots, 0)
        orders = sorted({x.get("order", 1) for x in self.levels.values()})
        # «проект» — уровень над самым нижним: у него свой цвет и сводная полоса
        self.project_order = orders[-2] if len(orders) >= 2 else None
        self.leaf_order = orders[-1] if orders else 1
        self.project_index: dict[str, int] = {}
        for n in self.walk():
            if self.role(n) == "project":
                self.project_index[n.id] = len(self.project_index)
        self.overrides: dict[tuple[str, str], object] = dict(overrides or {})
        self.has_links = any(n.raw.get("predecessors") for n in self.nodes.values())
        self._cache: dict = {}

    def _sort(self, nodes: list[Node], depth: int) -> None:
        nodes.sort(key=lambda n: n.order)
        for i, n in enumerate(nodes):
            n.row, n.depth = i, depth
            self._sort(n.children, depth + 1)

    def walk(self, nodes: list[Node] | None = None):
        for n in self.roots if nodes is None else nodes:
            yield n
            yield from self.walk(n.children)

    # ---------- уровни и роли ----------
    def level_order(self, node: Node) -> int:
        return self.levels.get(node.level, {}).get("order", self.leaf_order)

    def level_name(self, node: Node) -> str:
        return self.levels.get(node.level, {}).get("name", node.level)

    def role(self, node: Node) -> str:
        """group — раздел и выше, project — уровень над заданиями, task — нижний уровень."""
        o = self.level_order(node)
        if self.project_order is None or o > self.project_order:
            return "task"
        return "project" if o == self.project_order else "group"

    # ---------- значения с учётом правок ----------
    def get(self, node: Node, fld: str):
        key = (node.id, fld)
        if key in self.overrides:
            return self.overrides[key]
        return node.source(fld)

    def set_value(self, node: Node, fld: str, value) -> None:
        if value == node.source(fld):
            self.overrides.pop((node.id, fld), None)
        else:
            self.overrides[(node.id, fld)] = value
        self._cache.clear()

    def can_edit(self, node: Node, fld: str) -> bool:
        """Разрешает ли источник менять поле: editable узла, иначе meta.defaultEditable, иначе всё. Цвет — всегда."""
        if fld == "color":
            return True
        allowed = node.raw.get("editable")
        if allowed is None:
            allowed = self.doc["meta"].get("defaultEditable")
        return allowed is None or fld in allowed

    def edited_fields(self, node: Node) -> list[str]:
        return [f for (nid, f) in self.overrides if nid == node.id]

    def is_edited(self, node: Node) -> bool:
        ids = self._cache.get("edited")
        if ids is None:
            ids = self._cache["edited"] = {nid for (nid, _f) in self.overrides}
        return node.id in ids

    def invalidate(self) -> None:
        """Сбросить вычисленное (после изменения значений источника)."""
        self._cache.clear()

    @property
    def edit_count(self) -> int:
        return len(self.overrides)

    # ---------- статус и прогресс ----------
    def status_kind(self, node: Node) -> str:
        st = self.statuses.get(self.get(node, "status") or "")
        if st:
            return st["kind"]
        return "done" if self.get(node, "factEnd") else "inProgress"

    def is_done(self, node: Node) -> bool:
        if self.role(node) == "task":
            return self.status_kind(node) in DONE_KINDS
        if self.get(node, "factEnd"):
            return True
        done, total = self.progress(node)
        return total > 0 and done == total and not self.get(node, "status")

    def is_overdue(self, node: Node) -> bool:
        key = ("od", node.id)
        res = self._cache.get(key)
        if res is None:
            if self.role(node) == "group" or self.is_done(node):
                res = False
            else:
                end = self.get(node, "planEnd")
                res = bool(end and end < self.today)
            self._cache[key] = res
        return res

    def overdue_count(self, node: Node) -> int:
        """Сколько просроченных заданий внутри раздела или проекта."""
        key = ("odc", node.id)
        if key not in self._cache:
            if self.role(node) == "task" and not node.children:
                n = 1 if self.is_overdue(node) else 0
            else:
                n = sum(self.overdue_count(c) for c in node.children)
            self._cache[key] = n
        return self._cache[key]

    def due_info(self, node: Node) -> tuple[str, int | None, str]:
        """Колонка «Срок»: (вид, дни, текст). Вид: overdue | today | soon | ok | late | ontime | done | none | ''."""
        key = ("due", node.id)
        if key in self._cache:
            return self._cache[key]
        res = ("", None, "")
        if self.role(node) != "group":
            end = self.get(node, "planEnd")
            if self.role(node) == "project" and not end:
                end = self.span(node)[1] if not self.span(node)[2] else None
            if self.is_done(node):
                fe = self.get(node, "factEnd")
                if end and fe:
                    late = (fe - end).days
                    res = ("late", late, f"позже на {late} дн.") if late > 0 else ("ontime", late, "в срок")
                else:
                    res = ("done", None, "выполнено")
            elif not end:
                res = ("none", None, "без срока")
            else:
                d = (end - self.today).days
                if d < 0:
                    res = ("overdue", d, f"просрочено {-d} дн.")
                elif d == 0:
                    res = ("today", 0, "сегодня")
                elif d <= 3:
                    res = ("soon", d, f"через {d} дн.")
                elif d < 60:
                    res = ("ok", d, f"через {d} дн.")
                else:
                    res = ("ok", d, f"через {d // 30} мес.")
        self._cache[key] = res
        return res

    def progress(self, node: Node) -> tuple[int, int]:
        """Выполнено / всего по заданиям внутри узла (для задания — само задание)."""
        key = ("progress", node.id)
        if key not in self._cache:
            if self.role(node) == "task" and not node.children:
                res = (1 if self.status_kind(node) in DONE_KINDS else 0, 1)
            else:
                done = total = 0
                for c in node.children:
                    d, t = self.progress(c)
                    done, total = done + d, total + t
                res = (done, total)
            self._cache[key] = res
        return self._cache[key]

    def percent(self, node: Node) -> int | None:
        p = node.raw.get("percent")
        if isinstance(p, (int, float)):
            return round(p)
        done, total = self.progress(node)
        return round(done * 100 / total) if total else None

    # ---------- даты и полосы ----------
    def task_span(self, node: Node) -> tuple[date | None, date | None, bool]:
        """(начало, конец, открытый конец) плановой полосы задания."""
        start = self.get(node, "planStart") or self.get(node, "factStart")
        end = self.get(node, "planEnd")
        if start is None:
            return None, None, False
        if end is None:
            fact_end = self.get(node, "factEnd")
            if fact_end:
                return start, fact_end, False
            return start, max(start, self.today), True  # в работе без срока — до сегодня
        return start, max(start, end), False

    def span(self, node: Node) -> tuple[date | None, date | None, bool]:
        """(начало, конец, вычислено по детям) — для проекта и раздела."""
        key = ("span", node.id)
        if key in self._cache:
            return self._cache[key]
        if self.role(node) == "task" and not node.children:
            s, e, _ = self.task_span(node)
            res = (s, e, False)
        else:
            s, e = self.get(node, "planStart"), self.get(node, "planEnd")
            if s and e:
                res = (s, max(s, e), False)
            else:
                starts, ends = [], []
                for c in node.children:
                    cs, ce, _ = self.span(c)
                    if cs:
                        starts.append(cs)
                    if ce:
                        ends.append(ce)
                fs, fe = self.get(node, "factStart"), self.get(node, "factEnd")
                s = s or (min(starts) if starts else fs)
                e = e or (max(ends) if ends else fe)
                if s and not e:
                    e = max(s, self.today)
                res = (s, max(s, e) if s and e else e, True) if s else (None, None, True)
            # у раздела своя полоса всегда сводная
            if self.role(node) == "group":
                starts = [x for x in (self.span(c)[0] for c in node.children) if x]
                ends = [x for x in (self.span(c)[1] for c in node.children) if x]
                res = (min(starts), max(ends), True) if starts and ends else (None, None, True)
        self._cache[key] = res
        return res

    def fact_span(self, node: Node) -> tuple[date | None, date | None]:
        s, e = self.get(node, "factStart"), self.get(node, "factEnd")
        if s and not e and not self.is_done(node):
            e = max(s, self.today)
        return s, e

    def date_range(self) -> tuple[date, date]:
        lo, hi = self.today, self.today
        for n in self.nodes.values():
            for f in DATE_FIELDS:
                v = self.get(n, f)
                if v:
                    lo, hi = min(lo, v), max(hi, v)
        return lo, hi

    # ---------- подписи для таблицы ----------
    def executors_of(self, node: Node) -> list[str]:
        key = ("ex", node.id)
        if key not in self._cache:
            names = [self.people.get(p, {}).get("name", p) for p in node.raw.get("executors") or []]
            if not names and node.children:
                seen = []
                for c in node.children:
                    for nm in self.executors_of(c):
                        if nm not in seen:
                            seen.append(nm)
                names = seen
            self._cache[key] = names
        return self._cache[key]

    def executors_label(self, node: Node) -> str:
        if self.role(node) == "group":
            return ""
        names = self.executors_of(node)
        if not names:
            return ""
        if node.raw.get("executors") or len(names) == 1:
            return ", ".join(names)
        return f"{names[0]} +{len(names) - 1}"

    def number_label(self, node: Node) -> str:
        if node.number:
            return str(node.number)
        if self.role(node) == "project":
            return str(node.row + 1)
        return ""

    def status_label(self, node: Node) -> str:
        role = self.role(node)
        if role == "group":
            return ""
        if self.is_overdue(node):
            return "Просрочено" if role == "task" else "Просрочен"
        if role == "task":
            st = self.statuses.get(self.get(node, "status") or "")
            if st:
                return st["name"]
            return "Выполнено" if self.status_kind(node) == "done" else "В работе"
        if self.get(node, "factEnd"):
            return "Завершён"
        done, total = self.progress(node)
        if total and done == total:
            return "Выполнен"
        s, _e, _d = self.span(node)
        return "В работе" if s and s <= self.today else ("Не начат" if s else "")

    def status_kind_for_color(self, node: Node) -> str:
        if self.is_overdue(node):
            return "overdue"
        if self.role(node) == "task":
            return self.status_kind(node)
        return "done" if self.is_done(node) else "inProgress"

    def progress_label(self, node: Node) -> str:
        if self.role(node) == "task":
            return ""
        done, total = self.progress(node)
        if not total:
            return ""
        return f"{self.percent(node)}% · {done}/{total}"

    def top(self, node: Node) -> Node:
        while node.parent is not None:
            node = node.parent
        return node

    def scale_item(self, node: Node, fld: str) -> dict | None:
        return self.scales[fld].get(node.raw.get(fld) or "")

    def scale_top(self, fld: str) -> int:
        return max((x.get("order", 1) for x in self.scales[fld].values()), default=1)

    def person_name(self, pid: str | None) -> str:
        return self.people.get(pid or "", {}).get("name", pid or "")

    def executor_ids(self, node: Node) -> set[str]:
        key = ("exid", node.id)
        if key not in self._cache:
            ids = set(node.raw.get("executors") or [])
            for c in node.children:
                ids |= self.executor_ids(c)
            self._cache[key] = ids
        return self._cache[key]

    def custom_label(self, node: Node) -> str:
        parts = []
        for k, v in (node.raw.get("custom") or {}).items():
            f = self.custom_fields.get(k)
            if f is None or v is None:
                continue
            if f.get("type") == "enum":
                v = next((x["name"] for x in f.get("values", []) if x["id"] == v), v)
            parts.append(f"{f['name']}: {v}")
        return " · ".join(parts)

    def is_empty(self, node: Node) -> bool:
        """Проект без заданий и без единой даты — рисовать нечего."""
        return self.role(node) == "project" and not node.children and not any(
            self.get(node, f) for f in DATE_FIELDS)

    def status_choices(self) -> list[dict]:
        return sorted(self.statuses.values(), key=lambda x: x.get("order", 0))

    def counts(self) -> dict[str, int]:
        res = {"group": 0, "project": 0, "task": 0}
        for n in self.nodes.values():
            res[self.role(n)] += 1
        return res

    # ---------- сохранение ----------
    def change_id(self, node_id: str, fld: str, value) -> str:
        """Стабильный id правки: по нему источник присылает результат (results)."""
        import uuid

        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.key}|{node_id}|{fld}|{value}"))

    def change_records(self, at: str, data_only: bool = False, author: str | None = None,
                       machine: str | None = None) -> list[dict]:
        """Правки в виде записей changes. data_only — без цвета (оформление в источник не отправляется)."""
        out = []
        for node in self.walk():
            for fld in sorted(self.edited_fields(node)):
                if data_only and fld == "color":
                    continue
                val, old = self.overrides[(node.id, fld)], node.source(fld)
                if fld in DATE_FIELDS:
                    val = val.isoformat() if val else None
                    old = old.isoformat() if old else None
                rec = {"changeId": self.change_id(node.id, fld, val), "nodeId": node.id, "field": fld,
                       "oldValue": old, "newValue": val, "at": at}
                if node.raw.get("version") is not None:
                    rec["baseVersion"] = node.raw["version"]
                if author:
                    rec["author"] = author
                if machine:
                    rec["machine"] = machine
                out.append(rec)
        return out

    def to_document(self, generator: dict, exported_at: str, bake: bool = False) -> dict:
        """Снимок набора (kind = full).

        bake=False — без потерь: в узлах значения источника, все правки (и цвет) — в changes;
        при открытии программа снова показывает их как правки этого же набора.
        bake=True — для Excel/CSV/MS Project: в узлах видимые значения, changes нет.
        """
        import copy

        doc = copy.deepcopy(self.doc)
        doc.pop("$schema", None)
        doc.pop("changes", None)
        doc["meta"].update(exportedAt=exported_at, generator=generator, kind="full")
        if bake:
            for raw in doc["nodes"]:
                node = self.nodes[raw["id"]]
                for fld in self.edited_fields(node):
                    val = self.overrides[(node.id, fld)]
                    if fld in DATE_FIELDS:
                        val = val.isoformat() if val else None
                    if val is None:
                        raw.pop(fld, None)
                    else:
                        raw[fld] = val
            return doc
        changes = self.change_records(exported_at)
        if changes:
            doc["changes"] = changes
        return doc

    def changes_document(self, generator: dict, exported_at: str, author: str | None,
                         machine: str | None) -> dict:
        """Пакет правок на отправку в источник (kind = changes): только данные, без цвета."""
        meta = {k: v for k, v in self.doc["meta"].items() if k in ("source", "sourceName", "title")}
        meta.update(exportedAt=exported_at, generator=generator, kind="changes")
        if author:
            meta["exportedBy"] = author
        return {"format": "gantt-exchange", "schemaVersion": "1.0", "meta": meta,
                "changes": self.change_records(exported_at, data_only=True, author=author, machine=machine)}


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())
