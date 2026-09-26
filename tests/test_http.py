"""HTTP-контракт на тестовом сервере: клиент и окно целиком (подключение, обновление, отправка, офлайн)."""
from datetime import date, timedelta

import pytest

from gantt.exchange import check
from gantt.http_source import HttpClient, HttpError, Profile
from gantt.mock_server import MockState, start_in_thread


@pytest.fixture
def server():
    srv = start_in_thread(0, MockState(seed=1))
    yield srv
    srv.shutdown()
    srv.server_close()


def url(srv) -> str:
    return f"http://127.0.0.1:{srv.server_address[1]}/v1"


def test_contract(server):
    c = HttpClient(Profile("t", url(server)))
    info = c.ping()
    assert info["api"] == "1.0" and info["capabilities"]["import"]
    full = c.export()
    assert full["meta"]["kind"] == "full" and check(full).errors == []
    server.state.tick(force=True)
    inc = c.export(full["meta"]["cursor"])
    assert inc["meta"]["kind"] == "incremental" and len(inc["nodes"]) == 1 and check(inc).errors == []
    assert c.export("чужая-метка")["meta"]["kind"] == "full"     # неизвестная метка — полный снимок

    node = next(n for n in full["nodes"] if n.get("planEnd") and n["id"] != inc["nodes"][0]["id"])
    new = (date.fromisoformat(node["planEnd"]) + timedelta(days=3)).isoformat()
    base = {"format": "gantt-exchange", "schemaVersion": "1.0",
            "meta": {"source": "http:mock", "sourceName": "Тестовый сервер", "exportedAt": "2026-09-25T10:00:00+07:00",
                     "kind": "changes"}}
    changes = [
        {"changeId": "c1", "nodeId": node["id"], "field": "planEnd", "oldValue": node["planEnd"], "newValue": new,
         "at": "2026-09-25T10:00:00+07:00"},
        {"changeId": "c2", "nodeId": node["id"], "field": "factEnd", "oldValue": "2000-01-01",
         "newValue": "2026-09-25", "at": "2026-09-25T10:00:00+07:00"},
        {"changeId": "c3", "nodeId": node["id"], "field": "name", "oldValue": node["name"], "newValue": "Другое",
         "at": "2026-09-25T10:00:00+07:00"},
    ]
    res = c.send(dict(base, changes=changes))
    assert [r["result"] for r in res["results"]] == ["applied", "conflict", "rejected"]
    again = c.send(dict(base, changes=changes[:1]))          # повтор — тот же ответ, второй раз не применяется
    assert again["results"][0]["result"] == "applied"
    with pytest.raises(HttpError) as e:
        c.send({"format": "gantt-exchange"})
    assert e.value.code == 400 and e.value.details


def test_auth_and_offline():
    srv = start_in_thread(0, MockState(auth="gantt:секрет"))
    try:
        with pytest.raises(HttpError) as e:
            HttpClient(Profile("t", url(srv), login="gantt"), "неверный").ping()
        assert e.value.code == 401 and not e.value.offline
        assert HttpClient(Profile("t", url(srv), login="gantt"), "секрет").ping()["api"] == "1.0"
    finally:
        srv.shutdown()
        srv.server_close()
    with pytest.raises(HttpError) as e:
        HttpClient(Profile("t", url(srv), timeout=2)).ping()
    assert e.value.offline


def test_window_connect_edit_send_refresh(server, make_win, monkeypatch):
    from gantt.ui import server as server_ui

    w = make_win()
    w.connect_profile(Profile("Проверка", url(server)))
    assert w.ds is not None and w.ds.key == "http:mock|Тестовый сервер" and not w.offline
    assert w.path is None and "Проверка" in w.windowTitle()
    task = next(n for n in w.ds.walk() if w.ds.role(n) == "task" and n.source("planEnd")
                and w.ds.status_kind(n) != "done")
    new_end = task.source("planEnd") + timedelta(days=5)
    w.push(task, {"planEnd": new_end, "color": "#F4B6C2"}, "тест")

    monkeypatch.setattr(server_ui.SendDialog, "exec", lambda self: 1)
    w.send_changes()
    assert w.server is not None
    assert [f for (_n, f) in w.ds.overrides] == ["color"]          # данные записаны, цвет остался у себя
    assert server.state.by_id[task.id]["planEnd"] == new_end.isoformat()
    assert w.ds.get(w.ds.nodes[task.id], "planEnd") == new_end

    server.state.tick(force=True)                                  # в источнике кто-то поменял задание
    changed = server.state.journal[-1][1]
    w.refresh_from_server()
    assert w.ds.nodes[changed].raw["version"] == server.state.by_id[changed]["version"]


def test_window_goes_offline_and_back(server, make_win):
    w = make_win()
    w.connect_profile(Profile("Проверка", url(server)))
    port = server.server_address[1]
    server.shutdown()
    server.server_close()
    w.client.p.timeout = 2
    w.refresh_from_server(silent=True)
    assert w.offline and w.retry.isActive() and w.ds is not None   # данные на месте
    back = start_in_thread(port, server.state)
    try:
        w.refresh_from_server(silent=True)
        assert not w.offline and not w.retry.isActive()
    finally:
        back.shutdown()
        back.server_close()


def test_conflict_from_server_is_offered(server, make_win, monkeypatch):
    from gantt.ui import incoming, server as server_ui

    w = make_win()
    w.connect_profile(Profile("Проверка", url(server)))
    task = next(n for n in w.ds.walk() if w.ds.role(n) == "task" and n.source("planEnd")
                and w.ds.status_kind(n) != "done")
    w.push(task, {"planEnd": task.source("planEnd") + timedelta(days=5)}, "тест")
    server.state.by_id[task.id]["planEnd"] = "2031-01-01"          # источник успел поменять то же поле
    shown = []
    monkeypatch.setattr(server_ui.SendDialog, "exec", lambda self: 1)
    monkeypatch.setattr(incoming.ResultsDialog, "exec", lambda self: shown.append(1) or 1)
    w.send_changes()
    assert shown                                                   # конфликт — спросили
    assert not w.ds.overrides                                      # по умолчанию «взять из источника»
    assert w.ds.get(w.ds.nodes[task.id], "planEnd") == date(2031, 1, 1)
