"""Simulator behavior when the hub drops the connection."""
from typing import Any

import pytest
from socketio.exceptions import BadNamespaceError

from cobeart.simulator.emitter import RunConfig, Simulator
from cobeart.simulator.scenarios import build_motion_scenario
from cobeart.simulator.tests.helpers import motion_config


class DroppingClient:
    """Accepts connect, then fails every emit like a client the hub has disconnected."""

    def __init__(self) -> None:
        self.disconnected: bool = False

    def connect(self, url: str, namespaces: list[str], wait_timeout: float) -> None:
        pass

    def emit(self, event: str, data: Any, namespace: str) -> None:
        raise BadNamespaceError(f"{namespace} is not a connected namespace.")

    def disconnect(self) -> None:
        self.disconnected = True


def _simulator(client: DroppingClient, fault: str | None) -> Simulator:
    return Simulator(
        client=client,
        motion=build_motion_scenario("orbit", motion_config()),
        audio=None,
        run=RunConfig(url="http://127.0.0.1:1", motion_rate_hz=240.0, audio_rate_hz=100.0, seed=0,
                      duration_s=1.0, fault=fault),
        sleep=lambda _: None,
    )


def test_fault_run_ends_cleanly_when_the_hub_drops_the_connection(caplog: pytest.LogCaptureFixture) -> None:
    client = DroppingClient()
    assert _simulator(client, "nan").run() == {"frame": 0}
    assert client.disconnected
    assert any("Hub dropped the connection" in r.getMessage() for r in caplog.records)


def test_dropped_connection_without_fault_is_an_error() -> None:
    client = DroppingClient()
    with pytest.raises(BadNamespaceError):
        _simulator(client, None).run()
    assert client.disconnected
