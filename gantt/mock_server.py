"""Тестовый сервер по HTTP-контракту (docs/api.md) на демо-данных — чтобы проверить работу с сервером без 1С.

Запуск отдельно:  python -m gantt.mock_server --port 8765 [--auth логин:пароль] [--churn 60] [--fail 0.2]
Из программы:     «Файл → Подключение к серверу → Запустить тестовый сервер».
Слушает только 127.0.0.1 — с других компьютеров недоступен.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import random
import threading
import time
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .exchange import check
from .paths import resource_path

EDITABLE = ["planStart", "planEnd", "factStart", "factEnd", "status"]
DEFAULT_PORT = 8765


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class MockState:
    """Данные сервера, журнал изменений для changedSince и помнящиеся результаты правок."""

    def __init__(self, doc: dict | None = None, churn: float = 0, fail_rate: float = 0, delay: float = 0,
                 readonly: bool = False, auth: str | None = None, seed: int | None = None,
                 persist: Path | None = None):
        self.persist = persist
        saved = None
        if persist is not None and persist.exists():  # данные «сервера» переживают перезапуск программы
            try:
                saved = json.loads(persist.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                saved = None
        if saved:
            doc = saved["doc"]
        if doc is None:
            doc = json.loads(resource_path("examples", "full.gantt.json").read_text(encoding="utf-8"))
        self.doc = copy.deepcopy(doc)
        self.doc.pop("$schema", None)
        self.doc.pop("changes", None)
        self.doc["meta"].update(source="http:mock", sourceName="Тестовый сервер",
                                title="Тестовый сервер · демо-данные", exportedBy="Служебный пользователь",
                                defaultEditable=EDITABLE)
        for n in self.doc["nodes"]:
            n.setdefault("version", "v1")
        self.by_id = {n["id"]: n for n in self.doc["nodes"]}
        self.seq = 0
        self.journal: list[tuple[int, str, bool]] = []     # (номер, id узла, удалён)
        self.results: dict[str, dict] = {}
        self.churn, self.fail_rate, self.delay, self.readonly, self.auth = churn, fail_rate, delay, readonly, auth
        self.last_churn = time.monotonic()
        self.rng = random.Random(seed)
        self.lock = threading.Lock()
        self.log: list[str] = []
        if saved:
            self.seq = saved["seq"]
            self.journal = [tuple(j) for j in saved["journal"]]
            self.results = saved["results"]

    def save(self) -> None:
        if self.persist is None:
            return
        data = {"doc": self.doc, "seq": self.seq, "journal": self.journal[-5000:], "results": self.results}
        tmp = self.persist.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.persist)

    @property
    def cursor(self) -> str:
        return f"mock-{self.seq}"

    def _touch(self, node: dict, deleted: bool = False) -> None:
        self.seq += 1
        if not deleted:
            node["version"] = f"v{int(node.get('version', 'v1')[1:]) + 1}"
        self.journal.append((self.seq, node["id"], deleted))

    # ---------- «жизнь» источника ----------
    def tick(self, force: bool = False) -> None:
        """Раз в churn секунд кто-то «в источнике» сдвигает срок или закрывает задание."""
        if not force and (not self.churn or time.monotonic() - self.last_churn < self.churn):
            return
        self.last_churn = time.monotonic()
        leaf = max(x["order"] for x in self.doc["dictionaries"]["levels"])
        leaf_ids = {x["id"] for x in self.doc["dictionaries"]["levels"] if x["order"] == leaf}
        done = {s["id"] for s in self.doc["dictionaries"]["statuses"] if s["kind"] == "done"}
        tasks = [n for n in self.doc["nodes"] if n["level"] in leaf_ids and n.get("status") not in done
                 and n.get("planEnd")]
        if not tasks:
            return
        n = self.rng.choice(tasks)
        if self.rng.random() < 0.6:
            d = date.fromisoformat(n["planEnd"]) + timedelta(days=self.rng.randint(2, 10))
            n["planEnd"] = d.isoformat()
            self.log.append(f"{n['name']}: срок перенесён на {d:%d.%m.%Y}")
        elif done:
            n["status"] = sorted(done)[0]
            n["factEnd"] = date.today().isoformat()
            self.log.append(f"{n['name']}: выполнено")
        self._touch(n)
        self.save()

    # ---------- контракт ----------
    def ping(self) -> dict:
        m = self.doc["meta"]
        return {"api": "1.0", "schemaVersions": ["1.0"], "server": {"name": "Тестовый сервер Ганта", "version": "1.0"},
                "source": m["source"], "sourceName": m["sourceName"], "user": "Служебный пользователь",
                "readOnly": self.readonly, "capabilities": {"incremental": True, "import": not self.readonly}}

    def export(self, changed_since: str | None) -> dict:
        self.tick()
        base = None
        if changed_since and changed_since.startswith("mock-"):
            try:
                base = int(changed_since[5:])
            except ValueError:
                base = None
        if base is None or base > self.seq:   # метка неизвестна — отдаём полный снимок
            doc = copy.deepcopy(self.doc)
            doc["meta"].update(exportedAt=_now(), kind="full", cursor=self.cursor)
            return doc
        changed, deleted = [], []
        for _s, nid, gone in (j for j in self.journal if j[0] > base):
            if gone:
                deleted.append(nid)
            elif nid in self.by_id and nid not in changed:
                changed.append(nid)
        meta = {k: v for k, v in self.doc["meta"].items() if k not in ("cursor", "changedSince")}
        meta.update(exportedAt=_now(), kind="incremental", changedSince=changed_since, cursor=self.cursor)
        return {"format": "gantt-exchange", "schemaVersion": "1.0", "meta": meta,
                "nodes": [copy.deepcopy(self.by_id[i]) for i in changed], "deleted": deleted}

    def import_changes(self, doc: dict) -> dict:
        results = []
        for c in doc["changes"]:
            if c["changeId"] in self.results:     # повтор после обрыва — тот же ответ
                results.append(self.results[c["changeId"]])
                continue
            node, fld = self.by_id.get(c["nodeId"]), c["field"]
            r = {"changeId": c["changeId"]}
            if self.readonly:
                r.update(result="rejected", reason="сервер только для чтения")
            elif node is None:
                r.update(result="rejected", reason="узел удалён")
            elif fld not in EDITABLE:
                r.update(result="rejected", reason=f"поле «{fld}» не разрешено")
            elif fld == "status" and c.get("newValue") not in {s["id"] for s in
                                                               self.doc["dictionaries"]["statuses"]}:
                r.update(result="rejected", reason="недопустимый статус")
            elif node.get(fld) != c.get("oldValue"):
                r.update(result="conflict", currentValue=node.get(fld), currentVersion=node["version"],
                         reason="значение в источнике изменилось после вашей загрузки")
            else:
                if c.get("newValue") is None:
                    node.pop(fld, None)
                else:
                    node[fld] = c["newValue"]
                self._touch(node)
                r.update(result="applied", currentVersion=node["version"])
                who = c.get("author") or "кто-то"
                self.log.append(f"{node['name']}: {fld} → {c.get('newValue')} ({who})")
            self.results[c["changeId"]] = r
            results.append(r)
        self.save()
        meta = {k: self.doc["meta"][k] for k in ("source", "sourceName")}
        meta.update(exportedAt=_now(), kind="results")
        return {"format": "gantt-exchange", "schemaVersion": "1.0", "meta": meta, "results": results}


class Handler(BaseHTTPRequestHandler):
    server_version = "GanttMock/1.0"
    state: MockState

    def log_message(self, fmt, *args):  # тихо: журнал — в state.log
        pass

    def _send(self, code: int, body: dict | None = None, headers: dict | None = None) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _guard(self) -> bool:
        st = self.server.state
        if st.delay:
            time.sleep(st.delay)
        if st.auth:
            want = "Basic " + base64.b64encode(st.auth.encode("utf-8")).decode("ascii")
            if self.headers.get("Authorization") != want:
                self._send(401, {"error": "Нужен логин и пароль"}, {"WWW-Authenticate": 'Basic realm="gantt"'})
                return False
        if st.fail_rate and st.rng.random() < st.fail_rate:
            self._send(503, {"error": "База недоступна (имитация сбоя)"})
            return False
        return True

    def _route(self) -> str:
        path = urlparse(self.path).path.rstrip("/")
        return path[path.find("/v1") + 3:] if "/v1" in path else path

    def do_GET(self):  # noqa: N802
        if not self._guard():
            return
        st, route = self.server.state, self._route()
        with st.lock:
            if route == "/ping":
                self._send(200, st.ping())
            elif route == "/export":
                since = parse_qs(urlparse(self.path).query).get("changedSince", [None])[0]
                self._send(200, st.export(since))
            else:
                self._send(404, {"error": f"Нет такого адреса: {route}"})

    def do_POST(self):  # noqa: N802
        if not self._guard():
            return
        st, route = self.server.state, self._route()
        if route != "/import":
            self._send(404, {"error": f"Нет такого адреса: {route}"})
            return
        size = int(self.headers.get("Content-Length") or 0)
        if size > 20_000_000:
            self._send(413, {"error": "Слишком большой запрос"})
            return
        try:
            doc = json.loads(self.rfile.read(size).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self._send(400, {"error": "Тело — не JSON", "details": [str(e)]})
            return
        res = check(doc)
        if res.errors or doc.get("meta", {}).get("kind") != "changes":
            self._send(400, {"error": "Документ не прошёл проверку",
                             "details": res.errors or ["Ожидался документ kind = changes"]})
            return
        with st.lock:
            self._send(200, st.import_changes(doc))


def make_server(port: int = DEFAULT_PORT, state: MockState | None = None) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    srv.state = state or MockState()
    return srv


def start_in_thread(port: int = DEFAULT_PORT, state: MockState | None = None) -> ThreadingHTTPServer:
    srv = make_server(port, state)
    threading.Thread(target=srv.serve_forever, name="gantt-mock", daemon=True).start()
    return srv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Тестовый сервер Ганта (HTTP-контракт v1) на демо-данных")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--auth", help="логин:пароль — включить Basic-авторизацию")
    ap.add_argument("--churn", type=float, default=60, help="раз в N секунд менять что-то в данных (0 — нет)")
    ap.add_argument("--fail", type=float, default=0, help="доля запросов, отвечающих 503 (0…1)")
    ap.add_argument("--delay", type=float, default=0, help="задержка ответа, секунд")
    ap.add_argument("--readonly", action="store_true", help="не принимать правки")
    ap.add_argument("--data", help="свой файл обмена вместо демо-данных")
    a = ap.parse_args(argv)
    doc = json.loads(Path(a.data).read_text(encoding="utf-8")) if a.data else None
    state = MockState(doc, a.churn, a.fail, a.delay, a.readonly, a.auth)
    srv = make_server(a.port, state)
    print(f"Тестовый сервер: http://127.0.0.1:{a.port}/v1  (Ctrl+C — остановить)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
