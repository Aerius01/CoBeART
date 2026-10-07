"""Loads the shared `config/cobeart.yaml` into a frozen, validated `Settings` tree."""
import ipaddress
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

CONFIG_PATH_ENV: str = "COBEART_CONFIG"
SOCKETIO_URL_ENV: str = "COBEART_SOCKETIO_URL"
DEFAULT_CONFIG_PATH: Path = Path(__file__).resolve().parents[2] / "config" / "cobeart.yaml"

Range = tuple[float, float]
Rgb = tuple[float, float, float]


class ConfigError(ValueError):
    """The configuration file is missing, unreadable or invalid; the message names the offending key."""


@dataclass(frozen=True, slots=True)
class SocketIOSettings:
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class NetworkSettings:
    client_address: str
    server_address: str
    use_multicast: bool
    socketio: SocketIOSettings


@dataclass(frozen=True, slots=True)
class TrackingSettings:
    use_optitrack_client: bool
    tracking_framerate: float
    package_framerate: float
    max_num_objects: int
    x_rescale: float
    y_rescale: float


@dataclass(frozen=True, slots=True)
class ArenaSettings:
    """Arena bounds in millimeters, [min, max] per axis."""
    x: Range
    y: Range
    z: Range


@dataclass(frozen=True, slots=True)
class MetricsSettings:
    max_vel: float
    history_window: int


@dataclass(frozen=True, slots=True)
class HubSettings:
    audio_max_age_ms: int


@dataclass(frozen=True, slots=True)
class WindowSettings:
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class CompositeSettings:
    z_threshold: float
    clap_distance: float
    show_interactive_elements: bool


@dataclass(frozen=True, slots=True)
class FrontendSettings:
    bridge_framerate: float
    splat_color: Rgb
    composite: CompositeSettings


@dataclass(frozen=True, slots=True)
class AudioSettings:
    chunk_size: int
    sample_rate: int
    beat_detection: bool


@dataclass(frozen=True, slots=True)
class Settings:
    """The whole shared configuration plus the resolved hub URL (COBEART_SOCKETIO_URL wins over host and port)."""
    network: NetworkSettings
    tracking: TrackingSettings
    arena: ArenaSettings
    metrics: MetricsSettings
    hub: HubSettings
    window: WindowSettings
    frontend: FrontendSettings
    debug: bool
    audio: AudioSettings
    hub_url: str


def validate_hub_url(value: str) -> str:
    """Return `value` if it is an http(s) URL with a host, e.g. http://127.0.0.1:3000, else raise ConfigError."""
    try:
        parts = urlsplit(value)
        host: str | None = parts.hostname
        parts.port  # raises ValueError on a malformed port
    except ValueError as exc:
        raise ConfigError(f"invalid hub URL {value!r}: {exc}") from exc
    if parts.scheme not in {"http", "https"} or not host:
        raise ConfigError(f"invalid hub URL {value!r}: expected http(s)://host[:port]")
    return value


class _Section:
    """Typed access to one mapping of the raw config; `done()` rejects keys nobody read."""

    def __init__(self, raw: object, path: str) -> None:
        if not isinstance(raw, Mapping):
            raise ConfigError(f"config key '{path or '<root>'}' must be a mapping, got {type(raw).__name__}")
        self._raw: Mapping[str, Any] = raw
        self._path: str = path
        self._read: set[str] = set()

    def _key(self, key: str) -> str:
        return f"{self._path}.{key}" if self._path else key

    def _get(self, key: str) -> Any:
        if key not in self._raw:
            raise ConfigError(f"missing required config key '{self._key(key)}'")
        self._read.add(key)
        return self._raw[key]

    def section(self, key: str) -> "_Section":
        return _Section(self._get(key), self._key(key))

    def boolean(self, key: str) -> bool:
        value: Any = self._get(key)
        if not isinstance(value, bool):
            raise ConfigError(f"config key '{self._key(key)}' must be true or false, got {value!r}")
        return value

    def integer(self, key: str, minimum: int | None = None, maximum: int | None = None) -> int:
        value: Any = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"config key '{self._key(key)}' must be an integer, got {value!r}")
        if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
            raise ConfigError(f"config key '{self._key(key)}' must be in [{minimum}, {maximum}], got {value}")
        return value

    def number(self, key: str, positive: bool = False) -> float:
        return self._as_number(self._get(key), self._key(key), positive)

    @staticmethod
    def _as_number(value: Any, name: str, positive: bool = False) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ConfigError(f"config key '{name}' must be a finite number, got {value!r}")
        if positive and value <= 0:
            raise ConfigError(f"config key '{name}' must be greater than 0, got {value}")
        return float(value)

    def string(self, key: str) -> str:
        value: Any = self._get(key)
        if not isinstance(value, str) or not value:
            raise ConfigError(f"config key '{self._key(key)}' must be a non-empty string, got {value!r}")
        return value

    def ipv4(self, key: str) -> str:
        value: str = self.string(key)
        try:
            ipaddress.IPv4Address(value)
        except ValueError as exc:
            raise ConfigError(f"config key '{self._key(key)}' must be an IPv4 address, got {value!r}") from exc
        return value

    def range(self, key: str) -> Range:
        name: str = self._key(key)
        value: Any = self._get(key)
        if not isinstance(value, list) or len(value) != 2:
            raise ConfigError(f"config key '{name}' must be a [min, max] list, got {value!r}")
        low, high = (self._as_number(v, name) for v in value)
        if low >= high:
            raise ConfigError(f"config key '{name}' must have min < max, got {value!r}")
        return (low, high)

    def rgb(self, key: str) -> Rgb:
        name: str = self._key(key)
        value: Any = self._get(key)
        if not isinstance(value, list) or len(value) != 3:
            raise ConfigError(f"config key '{name}' must be an [r, g, b] list, got {value!r}")
        r, g, b = (self._as_number(v, name) for v in value)
        if not all(0.0 <= c <= 1.0 for c in (r, g, b)):
            raise ConfigError(f"config key '{name}' channels must be in [0, 1], got {value!r}")
        return (r, g, b)

    def done(self) -> None:
        unknown: list[str] = sorted(set(self._raw) - self._read)
        if unknown:
            raise ConfigError(f"unknown config key '{self._key(unknown[0])}'")


def parse_settings(raw: object, hub_url_override: str | None = None) -> Settings:
    """Validate a parsed YAML document and build the `Settings` tree."""
    root: _Section = _Section(raw, "")

    net: _Section = root.section("network")
    sio: _Section = net.section("socketio")
    socketio_settings: SocketIOSettings = SocketIOSettings(sio.string("host"), sio.integer("port", 1, 65535))
    sio.done()
    network: NetworkSettings = NetworkSettings(
        client_address=net.ipv4("client_address"), server_address=net.ipv4("server_address"),
        use_multicast=net.boolean("use_multicast"), socketio=socketio_settings,
    )
    net.done()

    trk: _Section = root.section("tracking")
    tracking: TrackingSettings = TrackingSettings(
        use_optitrack_client=trk.boolean("use_optitrack_client"),
        tracking_framerate=trk.number("tracking_framerate", positive=True),
        package_framerate=trk.number("package_framerate", positive=True),
        max_num_objects=trk.integer("max_num_objects", 1),
        x_rescale=trk.number("x_rescale", positive=True),
        y_rescale=trk.number("y_rescale", positive=True),
    )
    trk.done()

    are: _Section = root.section("arena")
    arena: ArenaSettings = ArenaSettings(are.range("x"), are.range("y"), are.range("z"))
    are.done()

    met: _Section = root.section("metrics")
    metrics: MetricsSettings = MetricsSettings(met.number("max_vel", positive=True), met.integer("history_window", 1))
    met.done()

    hub_section: _Section = root.section("hub")
    hub: HubSettings = HubSettings(hub_section.integer("audio_max_age_ms", 0))
    hub_section.done()

    win: _Section = root.section("window")
    window: WindowSettings = WindowSettings(win.integer("width", 1), win.integer("height", 1))
    win.done()

    fro: _Section = root.section("frontend")
    com: _Section = fro.section("composite")
    composite: CompositeSettings = CompositeSettings(
        z_threshold=com.number("z_threshold"), clap_distance=com.number("clap_distance"),
        show_interactive_elements=com.boolean("show_interactive_elements"),
    )
    com.done()
    frontend: FrontendSettings = FrontendSettings(
        fro.number("bridge_framerate", positive=True), fro.rgb("splat_color"), composite
    )
    fro.done()

    debug: bool = root.boolean("debug")

    aud: _Section = root.section("audio")
    audio: AudioSettings = AudioSettings(
        aud.integer("chunk_size", 1), aud.integer("sample_rate", 1), aud.boolean("beat_detection")
    )
    aud.done()
    root.done()

    url: str = validate_hub_url(
        hub_url_override if hub_url_override else f"http://{socketio_settings.host}:{socketio_settings.port}"
    )
    return Settings(network, tracking, arena, metrics, hub, window, frontend, debug, audio, url)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load COBEART_CONFIG (default `config/cobeart.yaml`); COBEART_SOCKETIO_URL overrides the hub URL."""
    environment: Mapping[str, str] = os.environ if env is None else env
    path: Path = Path(environment.get(CONFIG_PATH_ENV, DEFAULT_CONFIG_PATH))
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file {path} is not valid YAML: {exc}") from exc
    try:
        return parse_settings(raw, environment.get(SOCKETIO_URL_ENV))
    except ConfigError as exc:
        raise ConfigError(f"{exc} (in {path})") from exc
