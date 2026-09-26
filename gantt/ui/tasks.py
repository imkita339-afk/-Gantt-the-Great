"""Сеть — в фоне: окно не замирает, пока сервер думает. Результат возвращается в главный поток."""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _Relay(QObject):
    """Живёт в главном потоке: сигнал из фонового потока приходит сюда очередью."""

    done = Signal(object)
    failed = Signal(object)

    def __init__(self, on_done, on_fail):
        super().__init__()
        self.on_done, self.on_fail = on_done, on_fail
        self.done.connect(self._done)
        self.failed.connect(self._failed)

    @Slot(object)
    def _done(self, value) -> None:
        _alive.discard(self)
        self.on_done(value)

    @Slot(object)
    def _failed(self, error) -> None:
        _alive.discard(self)
        self.on_fail(error)


class _Job(QRunnable):
    def __init__(self, fn, relay: _Relay):
        super().__init__()
        self.fn, self.relay = fn, relay

    def run(self) -> None:
        try:
            value = self.fn()
        except Exception as e:  # noqa: BLE001 — любую ошибку отдаём обработчику
            self.relay.failed.emit(e)
        else:
            self.relay.done.emit(value)


_alive: set[_Relay] = set()


def run_async(fn, on_done, on_fail) -> None:
    relay = _Relay(on_done, on_fail)
    _alive.add(relay)
    QThreadPool.globalInstance().start(_Job(fn, relay))
