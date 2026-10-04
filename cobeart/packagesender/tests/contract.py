"""Test helpers: a contract validator built from contract/*.schema.json, and a recording Socket.IO stand-in."""
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from socketio.exceptions import ConnectionError as SocketIOConnectionError

CONTRACT_DIR: Path = Path(__file__).resolve().parents[3] / "contract"


def frame_validator() -> Draft202012Validator:
    """Validator for frame.schema.json with all contract schemas registered."""
    registry: Registry = Registry()
    schemas: dict[str, dict[str, Any]] = {}
    for name in ("frame", "audio_metrics", "splat"):
        schemas[name] = json.loads((CONTRACT_DIR / f"{name}.schema.json").read_text())
        registry = registry.with_resource(schemas[name]["$id"], Resource.from_contents(schemas[name], DRAFT202012))
    return Draft202012Validator(schemas["frame"], registry=registry)


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
