"""MS Project XML (MSPDI): сохранить и открыть. Понимают MS Project, ProjectLibre, GanttProject."""
from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

NS = "http://schemas.microsoft.com/project"
UUID_NS = uuid.uuid5(uuid.NAMESPACE_URL, "urn:gantt-exchange:msproject")
# настраиваемые текстовые поля MS Project: (FieldID, FieldName, наше название)
EXTRA = [("188743731", "Text1", "Статус"), ("188743734", "Text2", "Приоритет"), ("188743737", "Text3", "Срочность"),
         ("188743740", "Text4", "Влияние"), ("188743743", "Text5", "Категория"), ("188743746", "Text6", "ID Ганта")]
LINK_TYPES = {"FF": "0", "FS": "1", "SF": "2", "SS": "3"}


def _sub(parent, tag, text=None):
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = str(text)
    return el


def write(doc: dict, path: str | Path) -> None:
    from .gantt_table import _names, _tree

    levels, statuses, people, pri, urg, imp, cat, _fields = _names(doc)
    ET.register_namespace("", NS)
    root = ET.Element(f"{{{NS}}}Project")
    q = lambda t: f"{{{NS}}}{t}"  # noqa: E731
    meta = doc.get("meta", {})
    _sub(root, q("Name"), meta.get("title") or meta.get("sourceName") or "Гант")
    _sub(root, q("Title"), meta.get("title") or "")
    _sub(root, q("CreationDate"), datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
    tree = _tree(doc)
    starts = [n.get("planStart") or n.get("factStart") for n, _ in tree if n.get("planStart") or n.get("factStart")]
    _sub(root, q("StartDate"), (min(starts) if starts else date.today().isoformat()) + "T08:00:00")
    ext = _sub(root, q("ExtendedAttributes"))
    for fid, fname, alias in EXTRA:
        ea = _sub(ext, q("ExtendedAttribute"))
        _sub(ea, q("FieldID"), fid)
        _sub(ea, q("FieldName"), fname)
        _sub(ea, q("Alias"), alias)
    tasks = _sub(root, q("Tasks"))
    uid = {n["id"]: i + 1 for i, (n, _d) in enumerate(tree)}
    has_children = {n.get("parentId") for n, _d in tree}
    top = max((x.get("order", 1) for x in pri.values()), default=1)
    for i, (n, depth) in enumerate(tree, start=1):
        t = _sub(tasks, q("Task"))
        _sub(t, q("UID"), i)
        _sub(t, q("ID"), i)
        _sub(t, q("Name"), n["name"])
        _sub(t, q("OutlineLevel"), depth + 1)
        if n.get("number"):
            _sub(t, q("WBS"), n["number"])
        _sub(t, q("Summary"), 1 if n["id"] in has_children else 0)
        s = n.get("planStart") or n.get("factStart")
        e = n.get("planEnd") or n.get("factEnd") or s
        if s:
            _sub(t, q("Start"), f"{s}T08:00:00")
            _sub(t, q("Finish"), f"{max(s, e)}T17:00:00")
        if n.get("factStart"):
            _sub(t, q("ActualStart"), f"{n['factStart']}T08:00:00")
        if n.get("factEnd"):
            _sub(t, q("ActualFinish"), f"{n['factEnd']}T17:00:00")
        pct = n.get("percent")
        kind = statuses.get(n.get("status") or "", {}).get("kind")
        if pct is None:
            pct = 100 if kind == "done" else 0
        _sub(t, q("PercentComplete"), int(round(pct)))
        _sub(t, q("Milestone"), 1 if n.get("milestone") else 0)
        if n.get("priority") in pri:
            _sub(t, q("Priority"), int(100 + 800 * (pri[n["priority"]].get("order", 1) - 1) / max(1, top - 1)))
        for p in n.get("predecessors") or []:
            if p.get("id") in uid:
                pl = _sub(t, q("PredecessorLink"))
                _sub(pl, q("PredecessorUID"), uid[p["id"]])
                _sub(pl, q("Type"), LINK_TYPES.get(p.get("type", "FS"), "1"))
                _sub(pl, q("LinkLag"), int((p.get("lagDays") or 0) * 4800))  # в десятых долях минуты
        for (fid, _fname, alias), val in zip(EXTRA, (
                statuses.get(n.get("status") or "", {}).get("name"), pri.get(n.get("priority") or "", {}).get("name"),
                urg.get(n.get("urgency") or "", {}).get("name"), imp.get(n.get("impact") or "", {}).get("name"),
                cat.get(n.get("category") or "", {}).get("name"), n["id"])):
            if val:
                ea = _sub(t, q("ExtendedAttribute"))
                _sub(ea, q("FieldID"), fid)
                _sub(ea, q("Value"), val)
        if n.get("description"):
            _sub(t, q("Notes"), n["description"])
    res = _sub(root, q("Resources"))
    rid = {}
    for i, p in enumerate(people.values(), start=1):
        r = _sub(res, q("Resource"))
        _sub(r, q("UID"), i)
        _sub(r, q("ID"), i)
        _sub(r, q("Name"), p.get("fullName") or p["name"])
        rid[p["id"]] = i
    asg = _sub(root, q("Assignments"))
    k = 1
    for n, _d in tree:
        for p in n.get("executors") or []:
            if p in rid:
                a = _sub(asg, q("Assignment"))
                _sub(a, q("UID"), k)
                _sub(a, q("TaskUID"), uid[n["id"]])
                _sub(a, q("ResourceUID"), rid[p])
                k += 1
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def is_msproject(path: str | Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(2048).decode("utf-8", errors="ignore")
        return "<Project" in head and "schemas.microsoft.com/project" in head
    except OSError:
        return False


def read(path: str | Path) -> tuple[dict, list[str]]:
    from .table import DEFAULT_LEVELS, guess_status_kind, scale_order, short_name

    root = ET.parse(path).getroot()
    ns = {"p": NS}
    txt = lambda el, tag: (el.findtext(f"p:{tag}", default="", namespaces=ns) or "").strip()  # noqa: E731
    alias = {}
    for ea in root.findall("p:ExtendedAttributes/p:ExtendedAttribute", ns):
        alias[txt(ea, "FieldID")] = txt(ea, "Alias") or txt(ea, "FieldName")
    people = {}
    for r in root.findall("p:Resources/p:Resource", ns):
        name = txt(r, "Name")
        if name:
            pid = str(uuid.uuid5(UUID_NS, "person|" + name))
            people[txt(r, "UID")] = {"id": pid, "name": short_name(name), "fullName": name}
    assign: dict[str, list] = {}
    for a in root.findall("p:Assignments/p:Assignment", ns):
        if txt(a, "ResourceUID") in people:
            assign.setdefault(txt(a, "TaskUID"), []).append(people[txt(a, "ResourceUID")]["id"])
    raw_tasks = []
    for t in root.findall("p:Tasks/p:Task", ns):
        if not txt(t, "Name") or txt(t, "OutlineLevel") == "0" or txt(t, "IsNull") == "1":
            continue
        raw_tasks.append(t)
    raw_tasks.sort(key=lambda t: int(txt(t, "ID") or 0))
    warnings, nodes, stack, uid2id = [], [], [], {}
    statuses, scales = {}, {"Приоритет": {}, "Срочность": {}, "Влияние": {}, "Категория": {}}
    max_depth = 1
    for order, t in enumerate(raw_tasks):
        depth = max(1, int(txt(t, "OutlineLevel") or 1))
        max_depth = max(max_depth, depth)
        extras = {alias.get(txt(ea, "FieldID"), ""): txt(ea, "Value")
                  for ea in t.findall("p:ExtendedAttribute", ns)}
        nid = extras.get("ID Ганта") or f"msp-{txt(t, 'UID')}"
        uid2id[txt(t, "UID")] = nid
        stack = stack[:depth - 1]
        n = {"id": nid, "parentId": stack[-1]["id"] if stack else None, "level": f"L{depth}", "order": order,
             "name": txt(t, "Name")}
        if txt(t, "WBS"):
            n["number"] = txt(t, "WBS")
        for tag, fld in (("Start", "planStart"), ("Finish", "planEnd"), ("ActualStart", "factStart"),
                         ("ActualFinish", "factEnd")):
            v = txt(t, tag)
            if v:
                n[fld] = v[:10]
        if txt(t, "PercentComplete"):
            n["percent"] = float(txt(t, "PercentComplete"))
        if txt(t, "Milestone") == "1":
            n["milestone"] = True
        if assign.get(txt(t, "UID")):
            n["executors"] = assign[txt(t, "UID")]
        st = extras.get("Статус")
        if st:
            statuses.setdefault(st, guess_status_kind(st))
            n["status"] = "st:" + st
        for title, fld in (("Приоритет", "priority"), ("Срочность", "urgency"), ("Влияние", "impact"),
                           ("Категория", "category")):
            v = extras.get(title)
            if v:
                scales[title].setdefault(v, len(scales[title]) + 1)
                n[fld] = f"{fld[:3]}:{v}"
        if txt(t, "Notes"):
            n["description"] = txt(t, "Notes")
        n["_links"] = [(txt(pl, "PredecessorUID"), txt(pl, "Type"), txt(pl, "LinkLag"))
                       for pl in t.findall("p:PredecessorLink", ns)]
        nodes.append(n)
        stack.append(n)
    rev_links = {v: k for k, v in LINK_TYPES.items()}
    for n in nodes:
        links = n.pop("_links")
        preds = [{"id": uid2id[u], "type": rev_links.get(tp, "FS"),
                  **({"lagDays": round(int(lag) / 4800, 2)} if lag and lag != "0" else {})}
                 for u, tp, lag in links if u in uid2id]
        if preds:
            n["predecessors"] = preds
    if not statuses:  # статусов нет — по проценту
        statuses = {"Не начато": "notStarted", "В работе": "inProgress", "Выполнено": "done"}
        leaf = {n.get("parentId") for n in nodes}
        for n in nodes:
            if n["id"] not in leaf:
                p = n.get("percent") or 0
                n["status"] = "st:" + ("Выполнено" if p >= 100 else "В работе" if p > 0 else "Не начато")
    names = DEFAULT_LEVELS.get(max_depth, [f"Уровень {k + 1}" for k in range(max_depth)])
    d = {"levels": [{"id": f"L{k + 1}", "name": names[k], "order": k + 1} for k in range(max_depth)],
         "statuses": [{"id": "st:" + s, "name": s, "kind": k, "order": i + 1}
                      for i, (s, k) in enumerate(statuses.items())],
         "people": list(people.values())}
    for title, key in (("Приоритет", "priorities"), ("Срочность", "urgencies"), ("Влияние", "impacts")):
        if scales[title]:
            d[key] = [{"id": f"{key[:3]}:{v}", "name": v, "order": scale_order(v, pos)}
                      for v, pos in scales[title].items()]
    if scales["Категория"]:
        d["categories"] = [{"id": f"cat:{v}", "name": v} for v in scales["Категория"]]
    name = txt(root, "Title") or txt(root, "Name") or Path(path).stem
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    doc = {"format": "gantt-exchange", "schemaVersion": "1.0",
           "meta": {"source": "msproject", "sourceName": name, "title": name, "exportedAt": stamp, "kind": "full",
                    "cursor": stamp},
           "dictionaries": d, "nodes": nodes}
    return doc, warnings
