"""Работа с сервером: подключения, обновление, отправка правок только после «Подтвердить», офлайн-режим."""
from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QMenu, QMessageBox, QPushButton, QSpinBox, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from ..exchange import FIELD_TITLES, OpenError, apply_incremental, check
from ..http_source import (HttpClient, HttpError, Profile, cache_path, forget_password, get_password,
                           load_profiles, remember_for_session, save_profiles, set_password)
from ..model import Dataset
from ..paths import data_dir
from .incoming import decode
from .panels import value_text
from .tasks import run_async

MOCK_NAME = "Тестовый сервер"
MAX_RETRY = 600


def _is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


class ConnectionDialog(QDialog):
    """Профили подключений: адрес, логин, пароль (в Windows), проверка связи, тестовый сервер."""

    def __init__(self, win, parent=None):
        super().__init__(parent or win)
        self.win = win
        self.setWindowTitle("Подключение к серверу")
        self.resize(820, 470)
        self.profiles = load_profiles()
        self.chosen: tuple[Profile, str | None] | None = None
        lay = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Подключения</b>"))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        left.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton("Новое")
        add.clicked.connect(self._new)
        self.delete = QPushButton("Удалить")
        self.delete.clicked.connect(self._delete)
        row.addWidget(add)
        row.addWidget(self.delete)
        left.addLayout(row)
        mock = QPushButton("Запустить тестовый сервер на этом ПК")
        mock.setToolTip("Сервер на демо-данных внутри программы: можно попробовать обновление, отправку правок, "
                        "конфликты — без 1С")
        mock.clicked.connect(self._mock)
        left.addWidget(mock)
        lay.addLayout(left, 2)

        right = QVBoxLayout()
        form = QFormLayout()
        self.name = QLineEdit()
        self.url = QLineEdit()
        self.url.setPlaceholderText("http://сервер/база/hs/gantt/v1")
        self.login = QLineEdit()
        self.login.setPlaceholderText("пусто — без авторизации")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox("Сохранить пароль в Windows (диспетчер учётных данных)")
        self.remember.setChecked(True)
        self.refresh = QSpinBox()
        self.refresh.setRange(0, 240)
        self.refresh.setSuffix(" мин")
        self.refresh.setSpecialValueText("только вручную")
        form.addRow("Название:", self.name)
        form.addRow("Адрес:", self.url)
        form.addRow("Логин:", self.login)
        form.addRow("Пароль:", self.password)
        form.addRow("", self.remember)
        form.addRow("Обновлять каждые:", self.refresh)
        right.addLayout(form)
        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet("color: #B26A00;")
        right.addWidget(self.warn)
        for w in (self.url, self.login):
            w.textChanged.connect(self._update_warn)
        test = QPushButton("Проверить соединение")
        test.clicked.connect(self._test)
        right.addWidget(test, 0, Qt.AlignmentFlag.AlignLeft)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(self.info, 1)
        bb = QDialogButtonBox()
        self.connect_btn = bb.addButton("Подключиться", QDialogButtonBox.ButtonRole.AcceptRole)
        self.connect_btn.setObjectName("primary")
        bb.addButton("Закрыть", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)
        lay.addLayout(right, 3)

        self._fill()
        current = win.server.name if win.server else None
        idx = next((i for i, p in enumerate(self.profiles) if p.name == current), 0)
        if self.profiles:
            self.list.setCurrentRow(idx)
        else:
            self._new()

    # ---------- профили ----------
    def _fill(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.profiles:
            self.list.addItem(p.name)
        self.list.blockSignals(False)
        self.delete.setEnabled(bool(self.profiles))

    def _select(self, row: int) -> None:
        if not 0 <= row < len(self.profiles):
            return
        p = self.profiles[row]
        self.name.setText(p.name)
        self.url.setText(p.url)
        self.login.setText(p.login)
        self.password.clear()
        saved = bool(p.login and get_password(p))
        self.password.setPlaceholderText("сохранён в Windows — можно не вводить" if saved else "")
        self.refresh.setValue(p.refresh_minutes)
        self.info.clear()

    def _new(self) -> None:
        self.list.clearSelection()
        self.list.setCurrentRow(-1)
        self.name.setText("Новое подключение")
        self.url.clear()
        self.login.clear()
        self.password.clear()
        self.password.setPlaceholderText("")
        self.refresh.setValue(5)
        self.info.clear()
        self.name.setFocus()
        self.name.selectAll()

    def _delete(self) -> None:
        row = self.list.currentRow()
        if not 0 <= row < len(self.profiles):
            return
        p = self.profiles.pop(row)
        if p.login:
            forget_password(p)
        save_profiles(self.profiles)
        self._fill()
        if self.profiles:
            self.list.setCurrentRow(0)
        else:
            self._new()

    def _form(self) -> Profile | None:
        url = self.url.text().strip()
        if not url.lower().startswith(("http://", "https://")):
            self.info.setText("<span style='color:#C2185B'>Адрес должен начинаться с http:// или https://</span>")
            return None
        return Profile(self.name.text().strip() or urlparse(url).hostname or "Сервер", url,
                       self.login.text().strip(), refresh_minutes=self.refresh.value())

    def _password(self, p: Profile) -> str | None:
        return self.password.text() or (get_password(p) if p.login else None)

    def _update_warn(self) -> None:
        url = self.url.text().strip().lower()
        risky = url.startswith("http://") and self.login.text().strip() and not _is_local(url)
        self.warn.setText("По http:// пароль передаётся почти открытым текстом. Если сервер умеет HTTPS — "
                          "лучше https://." if risky else "")

    # ---------- действия ----------
    def _test(self) -> None:
        p = self._form()
        if p is None:
            return
        self.info.setText("Проверяю…")
        client = HttpClient(p, self._password(p))

        def done(info):
            try:
                caps = info.get("capabilities") or {}
                srv = info.get("server") or {}
                self.info.setText(
                    "<span style='color:#2E8B57'><b>Связь есть</b></span><br>"
                    f"Сервер: {srv.get('name', '—')} {srv.get('version', '')}<br>"
                    f"Источник: {info.get('source', '—')} · «{info.get('sourceName') or '—'}»<br>"
                    f"Пользователь на сервере: {info.get('user') or '—'}<br>"
                    f"Изменения с прошлого раза: {'да' if caps.get('incremental') else 'нет, каждый раз всё'}<br>"
                    f"Приём правок: {'нет — только чтение' if info.get('readOnly') or not caps.get('import') else 'да'}")
            except RuntimeError:  # окно уже закрыли
                pass

        def failed(e):
            try:
                text = e.message if isinstance(e, HttpError) else str(e)
                self.info.setText(f"<span style='color:#C2185B'><b>{text}</b></span>")
            except RuntimeError:
                pass

        self.win.net(client.ping, done, failed)

    def _mock(self) -> None:
        url = self.win.start_mock()
        p = next((x for x in self.profiles if x.name == MOCK_NAME), None)
        if p is None:
            p = Profile(MOCK_NAME, url, refresh_minutes=1)
            self.profiles.append(p)
        p.url = url
        save_profiles(self.profiles)
        self._fill()
        self.list.setCurrentRow(self.profiles.index(p))
        self.info.setText("Тестовый сервер запущен на этом ПК (только для этой программы). Раз в минуту он сам "
                          "«меняет» что-нибудь в данных — видно, как приходят обновления. Нажмите «Подключиться».")

    def _accept(self) -> None:
        p = self._form()
        if p is None:
            return
        row = self.list.currentRow()
        same = next((i for i, x in enumerate(self.profiles) if x.name == p.name), None)
        if 0 <= row < len(self.profiles) and (same is None or same == row):
            self.profiles[row] = p
        elif same is not None:
            self.profiles[same] = p
        else:
            self.profiles.append(p)
        save_profiles(self.profiles)
        pw = self.password.text()
        if p.login and pw:
            if not (self.remember.isChecked() and set_password(p, pw)):
                remember_for_session(p, pw)
        self.chosen = (p, pw or None)
        self.accept()


class SendDialog(QDialog):
    """Что уйдёт в источник. Без нажатия «Подтвердить» ничего не отправляется."""

    def __init__(self, ds: Dataset, pack: dict, profile: Profile, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Отправить правки в источник")
        self.resize(900, 520)
        lay = QVBoxLayout(self)
        ch = pack["changes"]
        head = QLabel(f"<b>В «{profile.name}» будет записано правок: {len(ch)}</b><br>"
                      f"От имени: {pack['meta'].get('exportedBy') or '—'} · "
                      f"ПК: {ch[0].get('machine') or '—' if ch else '—'}<br>"
                      "<span style='color:gray'>Цвета — оформление программы, в источник не отправляются.</span>")
        head.setWordWrap(True)
        lay.addWidget(head)
        tree = QTreeWidget()
        tree.setHeaderLabels(["Что", "Поле", "Сейчас в источнике", "Станет"])
        tree.setRootIsDecorated(False)
        tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i, w in ((1, 140), (2, 150), (3, 150)):
            tree.setColumnWidth(i, w)
        for c in ch:
            node, fld = ds.nodes.get(c["nodeId"]), c["field"]
            tree.addTopLevelItem(QTreeWidgetItem([node.name if node else c["nodeId"], FIELD_TITLES.get(fld, fld),
                                                  value_text(ds, fld, decode(fld, c.get("oldValue"))),
                                                  value_text(ds, fld, decode(fld, c.get("newValue")))]))
        lay.addWidget(tree, 1)
        form = QFormLayout()
        self.comment = QLineEdit()
        self.comment.setPlaceholderText("необязательно — попадёт в журнал источника")
        form.addRow("Комментарий:", self.comment)
        lay.addLayout(form)
        bb = QDialogButtonBox()
        ok = bb.addButton("Подтвердить", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setObjectName("primary")
        bb.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class ServerMixin:
    """Часть главного окна, отвечающая за сервер. Данные с сервера — такой же набор, как файл: правки в очереди."""

    def _init_server(self) -> None:
        self.server: Profile | None = None
        self.client: HttpClient | None = None
        self.cursor: str | None = None
        self.capabilities: dict = {}
        self.read_only = False
        self.offline = False
        self.sync_network = False          # тесты: сеть без фона
        self.mock = None
        self.updated_at: datetime | None = None
        self.retry_delay = 15
        self.retry = QTimer(self)
        self.retry.setSingleShot(True)
        self.retry.timeout.connect(lambda: self.refresh_from_server(silent=True))
        self.auto = QTimer(self)
        self.auto.timeout.connect(lambda: self.refresh_from_server(silent=True))
        self.server_btn = QToolButton()
        self.server_btn.setAutoRaise(True)
        self.server_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.server_btn)
        menu.addAction(self.a_refresh)
        menu.addAction(self.a_send)
        menu.addSeparator()
        menu.addAction(self.a_connect)
        menu.addAction(self.a_disconnect)
        self.server_btn.setMenu(menu)
        self.server_btn.hide()
        self.statusBar().addPermanentWidget(self.server_btn)
        self._server_state()

    def net(self, fn, done, failed) -> None:
        if self.sync_network:
            try:
                value = fn()
            except Exception as e:  # noqa: BLE001
                failed(e)
            else:
                done(value)
        else:
            run_async(fn, done, failed)

    # ---------- подключение ----------
    def open_connections(self) -> None:
        dlg = ConnectionDialog(self)
        if dlg.exec() and dlg.chosen:
            self.connect_profile(*dlg.chosen)

    def start_mock(self) -> str:
        from ..mock_server import DEFAULT_PORT, MockState, start_in_thread

        if self.mock is None:
            state = MockState(churn=60, persist=data_dir() / "test-server.json")
            try:
                self.mock = start_in_thread(DEFAULT_PORT, state)
            except OSError:  # порт занят — берём любой свободный
                self.mock = start_in_thread(0, state)
        return f"http://127.0.0.1:{self.mock.server_address[1]}/v1"

    def connect_profile(self, profile: Profile, password: str | None = None, silent: bool = False) -> None:
        if profile.name == MOCK_NAME and _is_local(profile.url):
            profile.url = self.start_mock()     # тестовый сервер живёт в программе — поднимаем заново
        self.server, self.client = profile, HttpClient(profile, password)
        self.cursor, self.offline, self.retry_delay = None, False, 15
        self.retry.stop()
        cached = cache_path(profile)
        if cached.exists():  # сразу показываем последнее, что пришло с сервера, потом обновляем
            try:
                doc = json.loads(cached.read_text(encoding="utf-8"))
                if not check(doc).errors:
                    self.cursor = doc["meta"].get("cursor")
                    self._show_server_doc(doc, first=True, cached=True)
            except (OSError, ValueError, KeyError):
                pass
        self._server_state("подключение…")
        client = self.client

        def fetch():
            return client.ping(), client.export(self.cursor)

        self.net(fetch, lambda r: self._connected(client, *r), lambda e: self._net_failed(e, silent))

    def _connected(self, client, info: dict, doc: dict) -> None:
        if client is not self.client:   # за это время переключились на другое подключение
            return
        self.capabilities = info.get("capabilities") or {}
        self.read_only = bool(info.get("readOnly")) or not self.capabilities.get("import", False)
        self._apply_server_doc(doc, first=True)

    def refresh_from_server(self, silent: bool = False) -> None:
        if self.client is None:
            return
        client = self.client
        self.net(lambda: client.export(self.cursor), lambda d: client is self.client and self._apply_server_doc(d),
                 lambda e: self._net_failed(e, silent))

    def disconnect_server(self) -> None:
        self.server = self.client = None
        self.cursor = None
        self.auto.stop()
        self.retry.stop()
        self.qs.remove("lastServer")
        self._server_state()

    # ---------- данные ----------
    def _apply_server_doc(self, doc: dict, first: bool = False) -> None:
        res = check(doc)
        if res.errors:
            self._error("Сервер прислал данные, которые не прошли проверку. Показаны прежние данные.",
                        OpenError("Данные с сервера не приняты.", res.errors))
            return
        kind = doc["meta"]["kind"]
        if kind == "incremental":
            key = f"{doc['meta']['source']}|{doc['meta'].get('sourceName') or ''}"
            if self.ds is None or self.ds.key != key:   # не к чему применять — просим полный снимок
                self.cursor = None
                self.refresh_from_server(silent=True)
                return
            if not doc.get("nodes") and not doc.get("deleted"):
                self.cursor = doc["meta"].get("cursor") or self.cursor
                self._went_online()
                return
            merged, stats, warnings = apply_incremental(self.ds.doc, doc)
            if check(merged).errors:
                self.cursor = None
                self.refresh_from_server(silent=True)
                return
            self._show_server_doc(merged, warnings=warnings)
            n = stats["updated"] + stats["added"] + stats["deleted"]
            self.toast(f"С сервера: изменений {n}" + (" · есть расхождения с вашими правками"
                                                      if self.conflicts else ""),
                       "Разобрать" if self.conflicts else None, self.resolve_conflicts if self.conflicts else None)
        elif kind == "full":
            self._show_server_doc(doc, first=first, warnings=res.warnings)
            if first:
                c = self.ds.counts()
                self.toast(f"Сервер «{self.server.name}»: разделов {c['group']} · проектов {c['project']} · "
                           f"заданий {c['task']}")
        else:
            self.toast(f"Сервер прислал документ «{kind}» вместо данных — пропущено")
            return
        self.cursor = doc["meta"].get("cursor") or self.cursor
        try:
            cache_path(self.server).write_text(json.dumps(self.ds.doc, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
        self._went_online()

    def _show_server_doc(self, doc: dict, first: bool = False, cached: bool = False,
                         warnings: list[str] | None = None) -> None:
        same = self.ds is not None and self.ds.key == f"{doc['meta']['source']}|{doc['meta'].get('sourceName') or ''}"
        view = self._view_state() if same else None
        label = f"Сервер · {self.server.name}" + (" (сохранённая копия)" if cached else "")
        self.load_document(doc, None, label, warnings or [], view)
        self.qs.setValue("lastServer", self.server.name)
        self.qs.remove("lastFile")
        self.update_title()
        if self.conflicts and not first:
            QTimer.singleShot(300, self.resolve_conflicts)

    # ---------- связь ----------
    def _went_online(self) -> None:
        was_offline = self.offline
        self.offline, self.retry_delay = False, 15
        self.retry.stop()
        self.updated_at = datetime.now()
        minutes = self.server.refresh_minutes if self.server else 0
        if minutes:
            self.auto.start(minutes * 60_000)
        else:
            self.auto.stop()
        if was_offline:
            self.toast("Связь с сервером восстановлена")
        self._server_state()

    def _net_failed(self, e: Exception, silent: bool = False) -> None:
        if self.server is None:
            return
        if isinstance(e, HttpError) and e.offline:
            first = not self.offline
            self.offline = True
            self.auto.stop()
            self.retry.start(self.retry_delay * 1000)
            self._server_state()
            if first and not silent:
                self.toast(f"Нет связи с сервером — работа на последних данных, повтор через {self.retry_delay} с",
                           "Повторить", lambda: self.refresh_from_server())
            self.retry_delay = min(self.retry_delay * 2, MAX_RETRY)
            return
        self._server_state("ошибка")
        text = e.message if isinstance(e, HttpError) else f"Неожиданная ошибка: {e}"
        details = e.details if isinstance(e, HttpError) else []
        if silent:
            self.toast(f"Сервер: {text}")
        else:
            box = QMessageBox(QMessageBox.Icon.Warning, "Сервер", text, parent=self)
            if details:
                box.setDetailedText("\n".join(details))
            box.exec()

    def _server_state(self, note: str | None = None) -> None:
        connected = self.server is not None
        self.a_refresh.setEnabled(connected)
        self.a_disconnect.setEnabled(connected)
        self.a_send.setEnabled(connected and not self.read_only)
        self.changes.set_can_send(connected and not self.read_only)
        if not connected:
            self.server_btn.hide()
            return
        self.server_btn.show()
        if note:
            text = f"● {self.server.name} · {note}"
        elif self.offline:
            text = f"○ {self.server.name} · офлайн, повтор через {self.retry.remainingTime() // 1000 + 1} с"
        else:
            at = f" · {self.updated_at:%H:%M}" if self.updated_at else ""
            text = f"● {self.server.name}{at}" + (" · только чтение" if self.read_only else "")
        self.server_btn.setText(text)
        self.server_btn.setToolTip(f"{self.server.url}\nОбновить — F5, отправить правки — Ctrl+Shift+Enter")
        color = "#B26A00" if self.offline else "#2E8B57"
        self.server_btn.setStyleSheet(f"QToolButton {{ color: {color}; }}")

    # ---------- отправка правок ----------
    def send_changes(self) -> None:
        if self.client is None or self.ds is None:
            QMessageBox.information(self, "Отправить правки", "Сначала подключитесь к серверу: "
                                                              "«Файл → Подключение к серверу».")
            return
        if self.read_only:
            QMessageBox.information(self, "Отправить правки", "Этот сервер правки не принимает (только чтение).")
            return
        pack = self.changes_pack()
        if not pack["changes"]:
            self.toast("Правок данных нет — отправлять нечего")
            return
        dlg = SendDialog(self.ds, pack, self.server, self)
        if not dlg.exec():
            return
        comment = dlg.comment.text().strip()
        if comment:
            for c in pack["changes"]:
                c["comment"] = comment
        client = self.client
        self._server_state("отправка…")

        def done(results: dict):
            self._server_state()
            res = check(results)
            if res.errors or results.get("meta", {}).get("kind") != "results":
                self._error("Ответ сервера не распознан", OpenError("Правки остались в очереди.", res.errors))
                return
            self.import_results(results, ask=False)
            self.refresh_from_server(silent=True)

        def failed(e):
            self._net_failed(e)
            self.toast("Правки не отправлены и остались в очереди")

        self.net(lambda: client.send(pack), done, failed)
