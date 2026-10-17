"""Fixtures for the cross-process e2e suite: contract validator, shared and fresh hubs, a recording /viewer."""
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from cobeart.settings.config import Settings, load_settings
from tests.e2e.harness import Hub, Viewer, hub_unavailable_reason, recording_viewer, running_hub

CONTRACT_DIR: Path = Path(__file__).resolve().parents[2] / "contract"
SCHEMA_NAMES: tuple[str, ...] = ("frame", "audio_metrics", "splat")


@pytest.fixture(scope="session")
def frame_validator() -> Draft202012Validator:
    """Draft 2020-12 validator for contract/frame.schema.json, resolving its refs through all three schemas."""
    schemas: dict[str, dict[str, object]] = {
        name: json.loads((CONTRACT_DIR / f"{name}.schema.json").read_text()) for name in SCHEMA_NAMES
    }
    registry: Registry = Registry().with_resources(
        (str(schema["$id"]), Resource.from_contents(schema, DRAFT202012)) for schema in schemas.values()
    )
    return Draft202012Validator(schemas["frame"], registry=registry)


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The config the hub also loads (COBEART_CONFIG or config/cobeart.yaml), so limits match on both sides."""
    return load_settings()


def _require_hub() -> None:
    reason: str | None = hub_unavailable_reason()
    if reason is not None:
        pytest.skip(reason)


@pytest.fixture(scope="session")
def shared_hub(settings: Settings, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Hub]:
    """One hub for tests that do not depend on hub state; they filter its replayed last frame by timestamp."""
    _require_hub()
    with running_hub(settings.network.socketio.host, tmp_path_factory.mktemp("shared-hub") / "hub.log") as hub:
        yield hub


@pytest.fixture
def fresh_hub(settings: Settings, tmp_path: Path) -> Iterator[Hub]:
    """A hub of this test's own, for checks on hub state (stored audio and last frame, rate-limited reject log)."""
    _require_hub()
    with running_hub(settings.network.socketio.host, tmp_path / "hub.log") as hub:
        yield hub


@pytest.fixture
def shared_viewer(shared_hub: Hub) -> Iterator[Viewer]:
    with recording_viewer(shared_hub.url) as viewer:
        yield viewer


@pytest.fixture
def fresh_viewer(fresh_hub: Hub) -> Iterator[Viewer]:
    with recording_viewer(fresh_hub.url) as viewer:
        yield viewer
