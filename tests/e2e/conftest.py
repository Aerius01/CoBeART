"""Fixtures for the cross-process e2e suite: contract validators, shared and fresh hubs, a recording /viewer."""
from collections.abc import Iterator
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from cobeart.settings.config import Settings, load_settings
from tests.contract.schemas import contract_validator
from tests.e2e.harness import Hub, Viewer, hub_unavailable_reason, recording_viewer, running_hub


@pytest.fixture(scope="session")
def frame_validator() -> Draft202012Validator:
    """Validator for contract/frame.schema.json, the shape of every /viewer `frame`."""
    return contract_validator("frame")


@pytest.fixture(scope="session")
def audio_validator() -> Draft202012Validator:
    """Validator for contract/audio.schema.json, the shape of every /viewer `audio`."""
    return contract_validator("audio")


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
    """One hub for tests that do not depend on hub state; their viewers drop messages stamped before they start."""
    _require_hub()
    with running_hub(settings.network.socketio.host, tmp_path_factory.mktemp("shared-hub") / "hub.log") as hub:
        yield hub


@pytest.fixture
def fresh_hub(settings: Settings, tmp_path: Path) -> Iterator[Hub]:
    """A hub of this test's own, for checks on hub state (per-role velocity state, health ages, reject log)."""
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
