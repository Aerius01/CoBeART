"""CLI behavior when no hub is listening."""
import logging
import socket
import time

import pytest

from cobeart.simulator.__main__ import main

MAX_FAIL_FAST_S: float = 5.0


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_no_hub_logs_one_line_and_exits_1(caplog: pytest.LogCaptureFixture) -> None:
    url: str = f"http://127.0.0.1:{_unused_port()}"
    start: float = time.monotonic()
    with caplog.at_level(logging.INFO), pytest.raises(SystemExit) as exc_info:
        main(["--url", url, "--duration", "1"])
    elapsed: float = time.monotonic() - start

    assert exc_info.value.code == 1
    assert elapsed < MAX_FAIL_FAST_S
    errors: list[logging.LogRecord] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.getMessage() for r in errors] == [f"no hub at {url}, start Electron or npm run web first"]
    assert all(r.exc_info is None for r in caplog.records)
    assert "Traceback" not in caplog.text
