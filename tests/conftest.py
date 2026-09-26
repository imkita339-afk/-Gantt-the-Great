import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Каждый тест — со своей папкой данных: реальные правки пользователя не трогаем."""
    monkeypatch.setenv("GANTT_DATA_DIR", str(tmp_path / "data"))
    from gantt.settings import qsettings
    qsettings().setValue("update/auto", False)     # без походов на GitHub при запуске окна


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_win(qapp):
    """Главное окно без экрана; сеть — синхронно, анимации выключены."""
    from gantt.settings import load_view
    from gantt.ui.main_window import MainWindow

    made = []

    def make():
        w = MainWindow(load_view())
        w.settings.animations = False
        w.sync_network = True
        made.append(w)
        return w

    yield make
    for w in made:
        w.auto.stop()
        w.retry.stop()
        if w.mock is not None:
            w.mock.shutdown()
            w.mock.server_close()
        w.store.close()
        w.deleteLater()
