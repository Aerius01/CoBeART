import subprocess
import sys
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[3]

# Any socketio.Client construction or connect at import time fails the check; so does pulling in soundcard.
IMPORT_CHECK: str = """
import sys
import socketio

def _refuse(*args, **kwargs):
    raise SystemExit("socketio.Client used at import time")

socketio.Client.__init__ = _refuse
socketio.Client.connect = _refuse
import cobeart.packagesender.sender
import cobeart.optitrackclient.start_client
assert "soundcard" not in sys.modules, "motion pipeline imported soundcard"
print("ok")
"""


def test_importing_producer_modules_has_no_side_effects() -> None:
    result = subprocess.run(
        [sys.executable, "-c", IMPORT_CHECK], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
