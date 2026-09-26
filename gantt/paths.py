"""Где лежат ресурсы программы и её локальные данные."""
import os
import sys
from pathlib import Path


def resource_path(*parts: str) -> Path:
    """Файл из поставки: в собранном .exe — из распакованной папки, при разработке — из корня проекта."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


def data_dir() -> Path:
    """Локальная папка программы: кэш, правки, настройки. Всегда на этом ПК, даже если .exe на сетевом диске."""
    override = os.environ.get("GANTT_DATA_DIR")
    if override:
        path = Path(override)
    else:
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        path = Path(root) / "Гант"
    path.mkdir(parents=True, exist_ok=True)
    return path
