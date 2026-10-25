import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from cobeart.settings.config import (
    CONFIG_PATH_ENV,
    DEFAULT_CONFIG_PATH,
    FRAME_SCHEMA_PATH,
    SOCKETIO_URL_ENV,
    ConfigError,
    MouseSourceSettings,
    contract_roles,
    load_settings,
    parse_settings,
)


def real_config() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    return raw


def _node_and_leaf(raw: dict[str, Any], dotted: str) -> tuple[dict[str, Any], str]:
    *parents, leaf = dotted.split(".")
    node = raw
    for part in parents:
        node = node[part]
    return node, leaf


def test_real_file_parses() -> None:
    settings = load_settings({})
    raw = real_config()
    assert settings.network.socketio.port == raw["network"]["socketio"]["port"]
    assert settings.hub_url == f"http://{raw['network']['socketio']['host']}:{raw['network']['socketio']['port']}"
    assert settings.arena.x == tuple(float(v) for v in raw["arena"]["x"])
    assert settings.audio.beat_detection is True
    assert settings.metrics.max_gap_s == 0.25
    assert settings.frontend.audio_max_age_ms == 500
    assert settings.sources.mouse == MouseSourceSettings(
        role="right_hand", z_initial_mm=100, z_step_mm=20, rate_hz=60.0
    )


def test_tracking_roles_map_int_ids_to_roles() -> None:
    roles = load_settings({}).tracking.roles
    assert dict(roles) == {
        0: "left_hand", 1: "right_hand", 2: "head", 3: "chest_front",
        4: "chest_back", 5: "left_foot", 6: "right_foot", 7: "stick",
    }
    with pytest.raises(TypeError):
        roles[8] = "stick"  # type: ignore[index]


def test_contract_roles_come_from_the_frame_schema() -> None:
    schema = json.loads(FRAME_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert contract_roles() == frozenset(schema["$defs"]["role"]["enum"])
    assert set(load_settings({}).tracking.roles.values()) <= contract_roles()


def test_string_motive_ids_are_accepted_like_the_node_loader() -> None:
    raw = copy.deepcopy(real_config())
    raw["tracking"]["roles"] = {"0": "left_hand", 1: "right_hand"}
    assert dict(parse_settings(raw).tracking.roles) == {0: "left_hand", 1: "right_hand"}


def test_url_env_overrides_host_and_port() -> None:
    assert load_settings({SOCKETIO_URL_ENV: "http://127.0.0.1:3301"}).hub_url == "http://127.0.0.1:3301"


def test_bad_url_env_is_rejected() -> None:
    with pytest.raises(ConfigError, match="invalid hub URL 'localhost:3000'"):
        load_settings({SOCKETIO_URL_ENV: "localhost:3000"})


def test_config_path_env_selects_file(tmp_path: Path) -> None:
    raw = real_config()
    raw["network"]["socketio"]["port"] = 4242
    path = tmp_path / "custom.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert load_settings({CONFIG_PATH_ENV: str(path)}).hub_url == "http://127.0.0.1:4242"


def test_missing_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="nope.yaml"):
        load_settings({CONFIG_PATH_ENV: str(tmp_path / "nope.yaml")})


@pytest.mark.parametrize(
    "dotted",
    [
        "network.socketio.port", "metrics.max_vel", "metrics.max_gap_s", "audio.beat_detection", "debug", "arena",
        "tracking.roles", "frontend.audio_max_age_ms", "sources.mouse.role", "sources",
    ],
)
def test_missing_required_key_names_the_key(dotted: str) -> None:
    raw = copy.deepcopy(real_config())
    node, leaf = _node_and_leaf(raw, dotted)
    del node[leaf]
    with pytest.raises(ConfigError, match=f"missing required config key '{dotted}'"):
        parse_settings(raw)


@pytest.mark.parametrize(
    ("dotted", "value"),
    [
        ("network.socketio.port", 70000),
        ("network.client_address", "999.1.1.1"),
        ("arena.x", [5, -5]),
        ("audio.chunk_size", "1024"),
        ("metrics.max_gap_s", 0),
        ("frontend.audio_max_age_ms", 0.5),
        ("tracking.roles", {}),
        ("tracking.roles", ["left_hand"]),
        ("sources.mouse.z_initial_mm", 3000),
        ("sources.mouse.z_step_mm", 0),
        ("sources.mouse.rate_hz", -60),
    ],
)
def test_invalid_value_names_the_key(dotted: str, value: object) -> None:
    raw = copy.deepcopy(real_config())
    node, leaf = _node_and_leaf(raw, dotted)
    node[leaf] = value
    with pytest.raises(ConfigError, match=re.escape(f"'{dotted}'")):
        parse_settings(raw)


def test_unknown_role_in_tracking_roles_names_the_key() -> None:
    raw = copy.deepcopy(real_config())
    raw["tracking"]["roles"][1] = "right_elbow"
    with pytest.raises(ConfigError, match=r"config key 'tracking\.roles\.1' must be a contract role .*'right_elbow'"):
        parse_settings(raw)


def test_unknown_mouse_role_names_the_key() -> None:
    raw = copy.deepcopy(real_config())
    raw["sources"]["mouse"]["role"] = "mouse"
    with pytest.raises(ConfigError, match=r"config key 'sources\.mouse\.role' must be a contract role .*'mouse'"):
        parse_settings(raw)


@pytest.mark.parametrize("motive_id", [-1, "x", True, 1.5, "01"])
def test_bad_motive_id_names_the_key(motive_id: object) -> None:
    raw = copy.deepcopy(real_config())
    raw["tracking"]["roles"] = {motive_id: "stick"}
    with pytest.raises(ConfigError, match=re.escape(f"config key 'tracking.roles.{motive_id}'")):
        parse_settings(raw)


def test_role_mapped_twice_is_rejected() -> None:
    raw = copy.deepcopy(real_config())
    raw["tracking"]["roles"][8] = "stick"
    with pytest.raises(ConfigError, match=r"'tracking\.roles\.8' maps role 'stick', already mapped from ID 7"):
        parse_settings(raw)


def test_unknown_key_is_rejected() -> None:
    raw = copy.deepcopy(real_config())
    raw["audio"]["chunk_sise"] = 1
    with pytest.raises(ConfigError, match="unknown config key 'audio.chunk_sise'"):
        parse_settings(raw)


@pytest.mark.parametrize(
    ("dotted", "value"),
    [("tracking.max_num_objects", 10), ("frontend.splat_color", [1.0, 0.6, 0.2]), ("hub", {"audio_max_age_ms": 500})],
)
def test_removed_key_is_rejected_as_unknown(dotted: str, value: object) -> None:
    raw = copy.deepcopy(real_config())
    node, leaf = _node_and_leaf(raw, dotted)
    node[leaf] = value
    with pytest.raises(ConfigError, match=re.escape(f"unknown config key '{dotted}'")):
        parse_settings(raw)
