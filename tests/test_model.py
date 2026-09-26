from datetime import date

from gantt.formats import open_path
from gantt.model import Dataset
from gantt.store import Store


def demo(root, **kw):
    return Dataset(open_path(root / "examples" / "full.gantt.json").document, **kw)


def test_roles_follow_levels(root):
    ds = demo(root)
    assert ds.counts() == {"group": 6, "project": 33, "task": 221}
    flat = Dataset(open_path(root / "examples" / "other-system-flat.gantt.json").document)
    assert set(flat.role(n) for n in flat.walk()) == {"task"}


def test_override_and_reset(root):
    ds = demo(root)
    proj = next(n for n in ds.walk() if ds.role(n) == "project" and n.source("planEnd"))
    src = proj.source("planEnd")
    ds.set_value(proj, "planEnd", date(2030, 1, 1))
    assert ds.get(proj, "planEnd") == date(2030, 1, 1) and ds.is_edited(proj)
    ds.set_value(proj, "planEnd", src)          # вернули как было — правка исчезает
    assert not ds.is_edited(proj)


def test_store_roundtrip(root, tmp_path):
    store = Store(tmp_path / "s.sqlite")
    ds = demo(root)
    proj = next(n for n in ds.walk() if ds.role(n) == "project")
    store.save(ds.key, proj.id, "color", "#F4B6C2", None)
    store.save(ds.key, proj.id, "factStart", date(2026, 1, 2), None)
    ds2 = demo(root, overrides=store.load(ds.key))
    p2 = ds2.nodes[proj.id]
    assert ds2.get(p2, "color") == "#F4B6C2"
    assert ds2.get(p2, "factStart") == date(2026, 1, 2)


def test_group_span_covers_children(root):
    ds = demo(root, today=date(2026, 9, 24))
    sec = ds.roots[0]
    s, e, derived = ds.span(sec)
    assert derived and s and e
    for p in sec.children:
        ps, pe, _ = ds.span(p)
        if ps:
            assert s <= ps and pe <= e


def test_snapshot_keeps_edits(root):
    from gantt.exchange import check
    ds = demo(root)
    proj = next(n for n in ds.walk() if ds.role(n) == "project" and n.source("planEnd"))
    ds.set_value(proj, "planEnd", date(2030, 1, 1))
    ds.set_value(proj, "color", "#F4B6C2")
    doc = ds.to_document({"name": "Гант", "version": "test"}, "2026-09-25T12:00:00+07:00")
    assert check(doc).errors == []
    raw = next(n for n in doc["nodes"] if n["id"] == proj.id)
    assert raw.get("planEnd") == proj.raw.get("planEnd")           # в узлах — значения источника
    assert sorted(c["field"] for c in doc["changes"]) == ["color", "planEnd"]
    baked = ds.to_document({"name": "Гант", "version": "test"}, "2026-09-25T12:00:00+07:00", bake=True)
    raw = next(n for n in baked["nodes"] if n["id"] == proj.id)
    assert raw["planEnd"] == "2030-01-01" and raw["color"] == "#F4B6C2" and "changes" not in baked
    pack = ds.changes_document({"name": "Гант"}, "2026-09-25T12:00:00+07:00", "Руководитель проекта", "PC")
    assert check(pack).errors == [] and [c["field"] for c in pack["changes"]] == ["planEnd"]  # цвет не уходит


def test_due_column_and_overdue_counts(root):
    ds = demo(root, today=date(2026, 9, 24))
    task = next(n for n in ds.walk() if ds.role(n) == "task" and not ds.is_done(n))
    ds.set_value(task, "planEnd", date(2026, 9, 20))
    assert ds.due_info(task) == ("overdue", -4, "просрочено 4 дн.")
    ds.set_value(task, "planEnd", date(2026, 9, 24))
    assert ds.due_info(task)[:2] == ("today", 0)
    ds.set_value(task, "planEnd", date(2026, 9, 26))
    assert ds.due_info(task) == ("soon", 2, "через 2 дн.")
    ds.set_value(task, "planEnd", None)
    assert ds.due_info(task)[0] == "none"
    ds.set_value(task, "planEnd", date(2026, 9, 1))
    ds.set_value(task, "factEnd", date(2026, 9, 4))
    ds.set_value(task, "status", next(s["id"] for s in ds.status_choices() if s["kind"] == "done"))
    assert ds.due_info(task) == ("late", 3, "позже на 3 дн.")
    # счётчик просрочек у проекта и раздела совпадает с заданиями внутри
    for n in ds.walk():
        if ds.role(n) != "task":
            inside = [t for t in _tasks(n) if ds.is_overdue(t)]
            assert ds.overdue_count(n) == len(inside)


def _tasks(n):
    for c in n.children:
        if not c.children:
            yield c
        yield from _tasks(c)


def test_source_dates_are_parsed_once_and_can_be_replaced(root):
    ds = demo(root)
    task = next(n for n in ds.walk() if ds.role(n) == "task" and n.source("planEnd"))
    assert task.source("planEnd") is task.source("planEnd")        # из кэша
    task.set_source("planEnd", "2031-02-03")
    assert task.source("planEnd") == date(2031, 2, 3) and task.raw["planEnd"] == "2031-02-03"
