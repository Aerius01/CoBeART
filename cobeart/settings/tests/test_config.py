import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from cobeart.settings.config import (
    CONFIG_PATH_ENV,
    DEFAULT_CONFIG_PATH,
    SOCKETIO_URL_ENV,
    ConfigError,
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
    assert settings.tracking.max_num_objects == raw["tracking"]["max_num_objects"]
    assert settings.audio.beat_detection is True


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
    "dotted", ["network.socketio.port", "metrics.max_vel", "audio.beat_detection", "debug", "arena"]
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
        ("tracking.max_num_objects", True),
        ("arena.x", [5, -5]),
        ("frontend.splat_color", [1.0, 2.0, 0.0]),
        ("audio.chunk_size", "1024"),
    ],
)
def test_invalid_value_names_the_key(dotted: str, value: object) -> None:
    raw = copy.deepcopy(real_config())
    node, leaf = _node_and_leaf(raw, dotted)
    node[leaf] = value
    with pytest.raises(ConfigError, match=dotted):
        parse_settings(raw)


def test_unknown_key_is_rejected() -> None:
    raw = copy.deepcopy(real_config())
    raw["audio"]["chunk_sise"] = 1
    with pytest.raises(ConfigError, match="unknown config key 'audio.chunk_sise'"):
        parse_settings(raw)
