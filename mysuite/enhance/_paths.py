from __future__ import annotations

import os
from pathlib import Path

_APP = "mysuite"


def models_dir() -> Path:
    """Where optional AI model weights live (RealESRGAN_x4plus.pth, GFPGANv1.4.pth).
    Override with MYSUITE_CACHE_DIR."""
    base = os.environ.get("MYSUITE_CACHE_DIR")
    return (Path(base) if base else Path.home() / ".cache" / _APP) / "models"


def history_db_path() -> Path:
    """Opt-in job history database (see mysuite.enhance.history). Override with
    MYSUITE_DATA_DIR."""
    base = os.environ.get("MYSUITE_DATA_DIR")
    return (Path(base) if base else Path.home() / ".local" / "share" / _APP) / "enhance-history.db"
