"""Loads the shared `config/cobeart.yaml` into a frozen, validated `Settings` tree."""
import ipaddress
import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

import yaml

CONFIG_PATH_ENV: str = "COBEART_CONFIG"
SOCKETIO_URL_ENV: str = "COBEART_SOCKETIO_URL"
REPO_ROOT: Path = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH: Path = REPO_ROOT / "config" / "cobeart.yaml"
# The role enum is defined once, in the contract; config role values are checked against it.
FRAME_SCHEMA_PATH: Path = REPO_ROOT / "contract" / "frame.schema.json"
# A tracking.roles key: a non-negative integer, or its decimal string (what config/cobeart.schema.json accepts).
_MOTIVE_ID_PATTERN: re.Pattern[str] = re.compile(r"0|[1-9][0-9]*")

Range = tuple[float, float]
# A contract role name (frame.schema.json $defs/role), validated against `contract_roles()` on load.
type Role = str


class ConfigError(ValueError):
    """The configuration file is missing, unreadable or invalid; the message names the offending key."""


@cache
def contract_roles() -> frozenset[Role]:
    """The closed role enum from contract/frame.schema.json ($defs/role)."""
    try:
        roles: list[str] = json.loads(FRAME_SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]["role"]["enum"]
    except (OSError, ValueError, KeyError) as exc:
        raise ConfigError(f"cannot read the role enum ($defs/role) from {FRAME_SCHEMA_PATH}: {exc!r}") from exc
    return frozenset(roles)


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
    """`roles`: read-only map from Motive streaming ID (int) to contract role, each role at most once."""
    use_optitrack_client: bool
    tracking_framerate: float
    package_framerate: float
    x_rescale: float
    y_rescale: float
    roles: Mapping[int, Role]


@dataclass(frozen=True, slots=True)
class ArenaSettings:
    """Arena bounds in millimeters, [min, max] per axis."""
    x: Range
    y: Range
    z: Range


@dataclass(frozen=True, slots=True)
class MetricsSettings:
    """The hub's velocity stage: norm_abs_vel scale and hold window, and the source-time gap that restarts a role."""
    max_vel: float
    history_window: int
    max_gap_s: float


@dataclass(frozen=True, slots=True)
class WindowSettings:
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class FrontendSettings:
    bridge_framerate: float
    audio_max_age_ms: int


@dataclass(frozen=True, slots=True)
class MouseSourceSettings:
    """Browser mouse source (D17); z values in millimeters, z_initial_mm inside arena.z."""
    role: Role
    z_initial_mm: int
    z_step_mm: int
    rate_hz: float


@dataclass(frozen=True, slots=True)
class SourcesSettings:
    mouse: MouseSourceSettings


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
    window: WindowSettings
    frontend: FrontendSettings
    sources: SourcesSettings
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

    def role(self, key: str, roles: frozenset[Role]) -> Role:
        return self._as_role(self._get(key), self._key(key), roles)

    @staticmethod
    def _as_role(value: Any, name: str, roles: frozenset[Role]) -> Role:
        if not isinstance(value, str) or value not in roles:
            allowed: str = ", ".join(sorted(roles))
            raise ConfigError(f"config key '{name}' must be a contract role ({allowed}), got {value!r}")
        return value

    def role_map(self, key: str, roles: frozenset[Role]) -> Mapping[int, Role]:
        """A non-empty {Motive ID: role} mapping; keys may be ints or their decimal strings, each role at most once."""
        name: str = self._key(key)
        value: Any = self._get(key)
        if not isinstance(value, Mapping) or not value:
            raise ConfigError(f"config key '{name}' must be a non-empty mapping of Motive ID to role, got {value!r}")
        by_id: dict[int, Role] = {}
        id_of_role: dict[Role, int] = {}
        for raw_id, raw_role in value.items():
            entry: str = f"{name}.{raw_id}"
            motive_id: int = self._as_motive_id(raw_id, entry)
            role: Role = self._as_role(raw_role, entry, roles)
            if motive_id in by_id:
                raise ConfigError(f"config key '{entry}' repeats Motive ID {motive_id}")
            if role in id_of_role:
                raise ConfigError(
                    f"config key '{entry}' maps role '{role}', already mapped from ID {id_of_role[role]}"
                )
            by_id[motive_id] = role
            id_of_role[role] = motive_id
        return MappingProxyType(by_id)

    @staticmethod
    def _as_motive_id(value: Any, name: str) -> int:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        if isinstance(value, str) and _MOTIVE_ID_PATTERN.fullmatch(value):
            return int(value)
        raise ConfigError(f"config key '{name}' must have a non-negative integer Motive ID as key, got {value!r}")

    def done(self) -> None:
        unknown: list[str] = sorted(set(self._raw) - self._read)
        if unknown:
            raise ConfigError(f"unknown config key '{self._key(unknown[0])}'")


def parse_settings(
    raw: object, hub_url_override: str | None = None, roles: frozenset[Role] | None = None
) -> Settings:
    """Validate a parsed YAML document and build the `Settings` tree; `roles` defaults to `contract_roles()`."""
    valid_roles: frozenset[Role] = contract_roles() if roles is None else roles
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
        x_rescale=trk.number("x_rescale", positive=True),
        y_rescale=trk.number("y_rescale", positive=True),
        roles=trk.role_map("roles", valid_roles),
    )
    trk.done()

    are: _Section = root.section("arena")
    arena: ArenaSettings = ArenaSettings(are.range("x"), are.range("y"), are.range("z"))
    are.done()

    met: _Section = root.section("metrics")
    metrics: MetricsSettings = MetricsSettings(
        met.number("max_vel", positive=True), met.integer("history_window", 1), met.number("max_gap_s", positive=True)
    )
    met.done()

    win: _Section = root.section("window")
    window: WindowSettings = WindowSettings(win.integer("width", 1), win.integer("height", 1))
    win.done()

    fro: _Section = root.section("frontend")
    frontend: FrontendSettings = FrontendSettings(
        fro.number("bridge_framerate", positive=True), fro.integer("audio_max_age_ms", 0)
    )
    fro.done()

    src: _Section = root.section("sources")
    mou: _Section = src.section("mouse")
    z_min, z_max = arena.z
    mouse: MouseSourceSettings = MouseSourceSettings(
        role=mou.role("role", valid_roles),
        z_initial_mm=mou.integer("z_initial_mm", math.ceil(z_min), math.floor(z_max)),
        z_step_mm=mou.integer("z_step_mm", 1),
        rate_hz=mou.number("rate_hz", positive=True),
    )
    mou.done()
    src.done()
    sources: SourcesSettings = SourcesSettings(mouse)

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
    return Settings(network, tracking, arena, metrics, window, frontend, sources, debug, audio, url)


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
