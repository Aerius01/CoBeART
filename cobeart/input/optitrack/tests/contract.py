"""Test helper: a recording Socket.IO stand-in. Contract validators live in tests/contract/schemas.py."""
from collections.abc import Callable
from typing import Any

from socketio.exceptions import ConnectionError as SocketIOConnectionError


class RecordingClient:
    """Socket.IO client stand-in: records emits, fails the first `connect_failures` connects, can fail emits."""

    def __init__(self, connect_failures: int = 0) -> None:
        self.connected: bool = False
        self.handlers: dict[str, Callable[..., None]] = {}
        self.emitted: list[tuple[str, dict[str, Any], str]] = []
        self.connect_calls: int = 0
        self.connect_failures: int = connect_failures
        self.emit_error: Exception | None = None

    def on(self, event: str, handler: Callable[..., None], namespace: str) -> None:
        self.handlers[event] = handler

    def connect(
        self, url: str, transports: list[str], namespaces: list[str], wait: bool, wait_timeout: float
    ) -> None:
        self.connect_calls += 1
        if self.connect_calls <= self.connect_failures:
            raise SocketIOConnectionError("Connection refused by the server")
        self.connected = True
        self.handlers["connect"]()

    def emit(self, event: str, data: dict[str, Any], namespace: str) -> None:
        if self.emit_error is not None:
            raise self.emit_error
        self.emitted.append((event, data, namespace))

    def disconnect(self) -> None:
        self.connected = False
        self.handlers["disconnect"]("client disconnect")

    def drop(self) -> None:
        """Simulate the server going away."""
        self.connected = False
        self.handlers["disconnect"]("transport close")
