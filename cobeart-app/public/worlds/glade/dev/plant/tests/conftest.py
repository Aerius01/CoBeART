"""Shared fixtures for the plant tests (run with the lpy interpreter): a fresh export of the real grass model."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PLANT_DIR = Path(__file__).resolve().parent.parent
LPY_DIR = PLANT_DIR.parent / "lpy"
if str(PLANT_DIR) not in sys.path:
    sys.path.insert(0, str(PLANT_DIR))

EXPORT_TIMEOUT_S = 120


@pytest.fixture(scope="session")
def organ_table_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The default export of lpy/grass.lpy (25 samples), written by export_organs.py in a subprocess.

    A subprocess keeps the Lsystem's lifetime out of the test process (a collected Lsystem crashes on iteration).
    """
    out = tmp_path_factory.mktemp("organs") / "grass.json"
    result = subprocess.run([sys.executable, str(LPY_DIR / "export_organs.py"), str(LPY_DIR / "grass.lpy"), str(out)],
                            capture_output=True, text=True, timeout=EXPORT_TIMEOUT_S)
    if result.returncode != 0:
        raise RuntimeError(f"export_organs.py failed ({result.returncode}): {result.stderr.strip()}")
    return out
