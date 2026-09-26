"""Один экземпляр программы: второй запуск передаёт путь в уже открытое окно и завершается."""
from __future__ import annotations

import getpass
import hashlib
import json

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def server_name() -> str:
    """Имя канала включает пользователя: на терминальном сервере у каждого своё окно."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return "Gantt-" + hashlib.md5(user.encode("utf-8")).hexdigest()[:12]


def send_to_running(path: str, timeout: int = 700) -> bool:
    """True — программа уже открыта и получила путь (или просьбу выйти вперёд)."""
    return send_command("open", path, timeout)


def send_command(cmd: str, path: str = "", timeout: int = 700) -> bool:
    """Команда открытому окну: open — открыть путь и выйти вперёд, quit — закрыться (перед установкой)."""
    sock = QLocalSocket()
    sock.connectToServer(server_name())
    if not sock.waitForConnected(timeout):
        return False
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY: пусть открытое окно выйдет вперёд
    except (AttributeError, OSError):
        pass
    sock.write((json.dumps({"cmd": cmd, "path": path}, ensure_ascii=False) + "\n").encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(timeout)
    sock.disconnectFromServer()
    return True


def is_running(timeout: int = 300) -> bool:
    """Открыта ли программа у этого пользователя — без команды, просто стук в канал."""
    sock = QLocalSocket()
    sock.connectToServer(server_name())
    ok = sock.waitForConnected(timeout)
    if ok:
        sock.abort()
    return ok


def wait_until_closed(seconds: float = 10.0) -> bool:
    """Подождать, пока прежний экземпляр закроется (после обновления или команды quit)."""
    import time

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not is_running(200):
            return True
        time.sleep(0.2)
    return False


class InstanceServer(QObject):
    received = Signal(str)
    quitRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        QLocalServer.removeServer(server_name())
        self.server.listen(server_name())
        self.server.newConnection.connect(self._accept)

    def _accept(self) -> None:
        sock = self.server.nextPendingConnection()
        buf = bytearray()

        def read():
            buf.extend(bytes(sock.readAll()))
            if b"\n" in buf:
                line = bytes(buf).split(b"\n", 1)[0]
                try:
                    msg = json.loads(line.decode("utf-8"))
                except ValueError:
                    msg = {}
                if msg.get("cmd") == "quit":
                    self.quitRequested.emit()
                elif msg:
                    self.received.emit(msg.get("path") or "")
                buf.clear()

        sock.readyRead.connect(read)
        sock.disconnected.connect(sock.deleteLater)
