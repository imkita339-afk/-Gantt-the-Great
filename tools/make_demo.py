"""Демо-данные для examples/: полностью выдуманные, ничего из реальных выгрузок.

Детерминированно (фиксированное зерно и «сегодня»), поэтому примеры одинаковы при каждом запуске.
Запуск: .venv\\Scripts\\python tools\\make_demo.py [папка]   (по умолчанию — examples/)
"""
from __future__ import annotations

import copy
import json
import random
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 9, 25)
NS = uuid.uuid5(uuid.NAMESPACE_URL, "urn:gantt-exchange:demo")
rnd = random.Random(20260925)

SECTIONS = ["Развитие ERP", "Склад и логистика", "Финансы и учёт", "Продажи и клиенты", "Инфраструктура"]
ARCHIVE = "Архив проектов"
# люди — обезличенные роли: в репозитории нет ни настоящих, ни выдуманных фамилий
PEOPLE = ["Аналитик 1", "Аналитик 2", "Аналитик 3", "Разработчик 1", "Разработчик 2", "Разработчик 3",
          "Разработчик 4", "Тестировщик 1", "Тестировщик 2", "Руководитель проекта", "Архитектор",
          "Администратор", "Методолог", "Инженер поддержки"]
SYSTEMS = ["ERP", "WMS", "ЗУП", "Бухгалтерия", "CRM", "Документооборот", "TMS", "Портал"]
ACTIONS = ["Доработка", "Интеграция", "Настройка", "Обновление", "Автоматизация", "Внедрение", "Исправление",
           "Разработка"]
OBJECTS = ["отчётов по продажам", "обмена с поставщиками", "маркировки товаров", "складских операций",
           "электронного документооборота", "расчёта бонусов", "учёта партий", "закрытия месяца",
           "ценообразования", "печатных форм", "прав доступа", "регламентных заданий", "обмена с сайтом",
           "учёта транспортных затрат", "сверки остатков", "заказов клиентов"]
KINDS = [("incident", "Инцидент", 5), ("changeRequest", "Запрос на изменение", 5), ("consultation", "Консультация", 1),
         ("serviceRequest", "Запрос на обслуживание", 1)]


def gid(*parts: str) -> str:
    return str(uuid.uuid5(NS, "|".join(parts)))


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def title(used: set) -> str:
    while True:
        t = f"{rnd.choice(SYSTEMS)}. {rnd.choice(ACTIONS)} {rnd.choice(OBJECTS)}"
        if t not in used:
            used.add(t)
            return t


def build() -> dict:
    people = [{"id": gid("person", f), "name": f} for f in PEOPLE]
    nodes, used, order, number = [], set(), 0, 5000
    special = {"no_plan": False, "empty": False}
    for si, sec in enumerate(SECTIONS + [ARCHIVE]):
        archive = sec == ARCHIVE
        order += 1
        sec_id = gid("section", sec)
        nodes.append({"id": sec_id, "parentId": None, "level": "section", "order": order,
                      "number": None if archive else str(si + 1), "name": sec})
        for _ in range(rnd.randint(3, 4) if archive else rnd.randint(4, 7)):
            order += 1
            name = title(used)
            dur = rnd.randint(45, 200)
            if archive:
                end = TODAY - timedelta(days=rnd.randint(20, 200))
                start = end - timedelta(days=dur)
            else:
                start = TODAY - timedelta(days=rnd.randint(-40, 260))
                end = start + timedelta(days=dur)
            proj = {"id": gid("project", name), "parentId": sec_id, "level": "project", "order": order, "name": name,
                    "planStart": iso(start), "planEnd": iso(end), "factStart": None, "factEnd": None}
            if start <= TODAY and rnd.random() < 0.85:
                proj["factStart"] = iso(start + timedelta(days=rnd.randint(-5, 12)))
            if archive:
                proj["factEnd"] = iso(end + timedelta(days=rnd.randint(-10, 15)))
            elif end < TODAY and rnd.random() < 0.4:
                proj["factEnd"] = iso(min(TODAY, end + timedelta(days=rnd.randint(0, 20))))
            n_tasks = rnd.randint(2, 6) if archive else rnd.choice([0] + list(range(2, 15)))
            if not archive and not special["no_plan"] and n_tasks >= 4:  # проект без плана — полоса по заданиям
                proj["planStart"] = proj["planEnd"] = None
                special["no_plan"] = True
            if not archive and not special["empty"] and n_tasks == 0:  # пустой проект — без дат и заданий
                for k in ("planStart", "planEnd", "factStart", "factEnd"):
                    proj[k] = None
                special["empty"] = True
            nodes.append(proj)
            for _t in range(n_tasks):
                order += 1
                number += rnd.randint(1, 9)
                lo = start - timedelta(days=20)
                hi = min(TODAY, end)
                created = lo + timedelta(days=rnd.randint(0, max(1, (hi - lo).days)))
                created = min(created, TODAY)
                done = archive or rnd.random() < 0.55
                completed = min(TODAY, created + timedelta(days=rnd.randint(1, 45))) if done else None
                deadline = created + timedelta(days=rnd.randint(5, 40)) if rnd.random() < 0.22 else None
                kind = rnd.choices(KINDS, weights=[k[2] for k in KINDS])[0]
                nodes.append({"id": gid("task", str(number)), "parentId": proj["id"], "level": "task", "order": order,
                              "number": str(number), "name": title(used) if rnd.random() < 0.6 else
                              f"{rnd.choice(SYSTEMS)}. {rnd.choice(ACTIONS)} {rnd.choice(OBJECTS)}",
                              "executors": [rnd.choice(people)["id"]],
                              "planStart": iso(created), "planEnd": iso(deadline),
                              "factStart": iso(created), "factEnd": iso(completed),
                              "status": "done" if done else "inProgress", "custom": {"taskKind": kind[0]}})
    return {
        "$schema": "../schema/gantt-exchange.schema.json",
        "format": "gantt-exchange",
        "schemaVersion": "1.0",
        "meta": {"source": "demo:gantt", "sourceName": "Демо-данные", "title": "Демо: проекты IT-отдела",
                 "exportedAt": "2026-09-25T09:00:00+07:00", "exportedBy": None, "kind": "full",
                 "changedSince": None, "cursor": "2026-09-25T09:00:00+07:00",
                 "generator": {"name": "tools/make_demo.py", "version": "1"}},
        "dictionaries": {
            "levels": [{"id": "section", "name": "Раздел", "order": 1},
                       {"id": "project", "name": "Проект", "order": 2},
                       {"id": "task", "name": "Задание", "order": 3}],
            "statuses": [{"id": "inProgress", "name": "В работе", "kind": "inProgress", "order": 1},
                         {"id": "done", "name": "Выполнено", "kind": "done", "order": 2}],
            "priorities": [{"id": f"p{i}", "name": n, "order": i} for i, n in
                           enumerate(["Очень низкий", "Низкий", "Средний", "Высокий"], 1)],
            "urgencies": [{"id": f"u{i}", "name": n, "order": i} for i, n in
                          enumerate(["Очень низкая", "Низкая", "Средняя", "Высокая"], 1)],
            "impacts": [{"id": f"i{i}", "name": n, "order": i} for i, n in
                        enumerate(["Очень низкое", "Низкое", "Среднее", "Высокое"], 1)],
            "people": sorted(people, key=lambda p: p["name"]),
            "customFields": [{"id": "taskKind", "name": "Вид задания", "type": "enum",
                              "values": [{"id": k, "name": n} for k, n, _w in KINDS]}],
        },
        "nodes": nodes,
    }


def derived(full: dict) -> dict[str, dict]:
    """Инкремент, пакет правок и результаты — на узлах демо-снимка."""
    tasks = [n for n in full["nodes"] if n["level"] == "task"]
    open_deadline = [n for n in tasks if n["planEnd"] and not n["factEnd"]]
    open_free = [n for n in tasks if not n["planEnd"] and not n["factEnd"]]
    done = [n for n in tasks if n["factEnd"]]
    a, b, c, d = open_free[0], open_deadline[0], open_free[1], done[-1]
    person = full["dictionaries"]["people"][0]
    ns = uuid.uuid5(uuid.NAMESPACE_URL, "urn:gantt-exchange:demo-examples")

    def plus(s: str, days: int) -> str:
        return (date.fromisoformat(s) + timedelta(days=days)).isoformat()

    def head(kind: str, at: str, **meta) -> dict:
        return {"$schema": "../schema/gantt-exchange.schema.json", "format": "gantt-exchange", "schemaVersion": "1.0",
                "meta": {"source": full["meta"]["source"], "sourceName": full["meta"]["sourceName"],
                         "exportedAt": at, "kind": kind, **meta}}

    a2 = copy.deepcopy(a)
    a2.update(factEnd="2026-09-26", status="done")
    b2 = copy.deepcopy(b)
    b2["planEnd"] = plus(b["planEnd"], 7)
    new_num = str(max(int(t["number"]) for t in tasks) + 1)
    new = {"id": str(uuid.uuid5(ns, f"task|{new_num}")), "parentId": a["parentId"], "level": "task",
           "order": a["order"] + 0.5, "number": new_num, "name": "ERP. Доработка печатных форм счёта",
           "executors": [person["id"]], "planStart": "2026-09-25", "planEnd": None, "factStart": "2026-09-25",
           "factEnd": None, "status": "inProgress", "custom": {"taskKind": "incident"}}
    inc = head("incremental", "2026-09-26T09:00:00+07:00", exportedBy=None,
               changedSince=full["meta"]["cursor"], cursor="2026-09-26T09:00:00+07:00")
    inc["nodes"], inc["deleted"] = [a2, b2, new], [d["id"]]

    author = "Руководитель проекта"
    ch = [
        {"changeId": str(uuid.uuid5(ns, "change|1")), "nodeId": b["id"], "field": "planEnd",
         "oldValue": b["planEnd"], "newValue": plus(b["planEnd"], 14), "baseVersion": None, "author": author,
         "machine": "IT-PC-07", "at": "2026-09-26T11:18:40+07:00", "comment": "Перенос по согласованию с отделом"},
        {"changeId": str(uuid.uuid5(ns, "change|2")), "nodeId": c["id"], "field": "status",
         "oldValue": "inProgress", "newValue": "done", "baseVersion": None, "author": author,
         "machine": "IT-PC-07", "at": "2026-09-26T11:19:05+07:00", "comment": None},
        {"changeId": str(uuid.uuid5(ns, "change|3")), "nodeId": c["id"], "field": "factEnd",
         "oldValue": None, "newValue": "2026-09-26", "baseVersion": None, "author": author,
         "machine": "IT-PC-07", "at": "2026-09-26T11:19:05+07:00", "comment": None},
    ]
    chd = head("changes", "2026-09-26T11:20:00+07:00", exportedBy=author)
    chd["changes"] = ch
    res = head("results", "2026-09-26T11:20:03+07:00", exportedBy="Служебный пользователь")
    res["results"] = [
        {"changeId": ch[0]["changeId"], "result": "conflict", "currentValue": plus(b["planEnd"], 7),
         "currentVersion": "v2", "reason": "Срок изменили в источнике после вашей загрузки"},
        {"changeId": ch[1]["changeId"], "result": "applied", "currentValue": None, "currentVersion": "v5",
         "reason": None},
        {"changeId": ch[2]["changeId"], "result": "rejected",
         "reason": "Источник не разрешает менять поле «Факт окончания»"},
    ]
    return {"incremental": inc, "changes": chd, "results": res}


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "examples"
    out.mkdir(parents=True, exist_ok=True)
    full = build()
    docs = {"full": full, **derived(full)}
    for name, doc in docs.items():
        indent = 1 if name == "full" else 2
        (out / f"{name}.gantt.json").write_text(json.dumps(doc, ensure_ascii=False, indent=indent) + "\n",
                                                encoding="utf-8")
    levels = {}
    for n in full["nodes"]:
        levels[n["level"]] = levels.get(n["level"], 0) + 1
    print("демо-данные:", levels, "→", out)


if __name__ == "__main__":
    main()
