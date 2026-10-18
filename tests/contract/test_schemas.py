"""Contract v2: the valid fixtures pass, and each listed malformation of them is rejected."""
from collections.abc import Callable
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from tests.contract.schemas import (
    CONTRACT_DIR,
    SCHEMA_NAMES,
    SchemaName,
    contract_validator,
    load_fixture,
    load_schema,
)

type Message = dict[str, Any]
type Mutation = Callable[[Message], None]

MOTION: tuple[SchemaName, ...] = ("pose_frame", "frame")
TIMESTAMPED: tuple[SchemaName, ...] = ("frame", "audio")
VELOCITY_FIELDS: tuple[str, ...] = ("vx", "vy", "vz", "wx", "wy", "wz", "abs_vel", "norm_abs_vel")


def _set(key: str, value: object) -> Mutation:
    def mutate(message: Message) -> None:
        message[key] = value
    return mutate


def _delete(key: str) -> Mutation:
    def mutate(message: Message) -> None:
        del message[key]
    return mutate


def _on_body(key: str, value: object) -> Mutation:
    def mutate(message: Message) -> None:
        message["bodies"]["right_hand"][key] = value
    return mutate


def _rename_body(new_role: str) -> Mutation:
    def mutate(message: Message) -> None:
        message["bodies"][new_role] = message["bodies"].pop("right_hand")
    return mutate


def _seconds_timestamp(message: Message) -> None:
    message["timestamp"] = message["timestamp"] / 1000.0


def _audio_in_frame(message: Message) -> None:
    audio: Message = load_fixture("audio")
    message["audio"] = audio


def _velocities_in_pose(message: Message) -> None:
    message["bodies"]["right_hand"].update(dict.fromkeys(VELOCITY_FIELDS, 0.0))


def _bodies_as_list(message: Message) -> None:
    message["bodies"] = [{"role": role, **body} for role, body in message["bodies"].items()]


INVALID: list[tuple[SchemaName, str, Mutation]] = [
    *((name, "schemaVersion 1", _set("schemaVersion", 1)) for name in SCHEMA_NAMES),
    *((name, "schemaVersion missing", _delete("schemaVersion")) for name in SCHEMA_NAMES),
    *((name, "unknown role", _rename_body("left_elbow")) for name in MOTION),
    *((name, "role as Motive ID", _rename_body("1")) for name in MOTION),
    *((name, "extra field on a body", _on_body("color", [1.0, 0.6, 0.2])) for name in MOTION),
    *((name, "ID on a body", _on_body("ID", 1)) for name in MOTION),
    *((name, "ID at top level", _set("ID", 1)) for name in MOTION),
    *((name, "quaternion component above 1", _on_body("qw", 1.5)) for name in MOTION),
    *((name, "quaternion component below -1", _on_body("qx", -1.01)) for name in MOTION),
    *((name, "non-integer position", _on_body("x", 450.5)) for name in MOTION),
    *((name, "bodies as a list", _bodies_as_list) for name in MOTION),
    *((name, "timestamp in seconds", _seconds_timestamp) for name in TIMESTAMPED),
    *((name, "timestamp missing", _delete("timestamp")) for name in TIMESTAMPED),
    ("pose_frame", "velocity fields on a body", _velocities_in_pose),
    ("pose_frame", "timestamp present", _set("timestamp", 1792310400123)),
    ("pose_frame", "source tag present", _set("type", "optitrack")),
    ("pose_frame", "negative source_time_s", _set("source_time_s", -0.1)),
    ("pose_frame", "source_time_s missing", _delete("source_time_s")),
    ("frame", "audio inside a frame", _audio_in_frame),
    ("frame", "source tag present", _set("type", "optitrack")),
    ("frame", "v1 rigidbodies array", _set("rigidbodies", [])),
    ("frame", "a derived field missing", lambda m: m["bodies"]["right_hand"].pop("norm_abs_vel")),
    ("frame", "norm_abs_vel above 1", _on_body("norm_abs_vel", 1.2)),
    ("audio_metrics", "producer sends timestamp", _set("timestamp", 1792310400150)),
    ("audio_metrics", "extra field", _set("loudness", 0.5)),
    ("audio_metrics", "beat without beat_timestamp", _set("beat_timestamp", None)),
    ("audio", "extra field", _set("loudness", 0.5)),
    ("audio", "beat_timestamp without beat", _set("beat_timestamp", 1792310400.1)),
]


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_schema_is_valid_draft_2020_12(name: SchemaName) -> None:
    try:
        Draft202012Validator.check_schema(load_schema(name))
    except SchemaError as exc:
        pytest.fail(f"{name}.schema.json is not a valid draft 2020-12 schema: {exc.message}")


def test_contract_dir_holds_exactly_the_v2_schemas() -> None:
    assert sorted(p.name for p in CONTRACT_DIR.glob("*.schema.json")) == sorted(
        f"{name}.schema.json" for name in SCHEMA_NAMES
    )


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_fixture_is_valid(name: SchemaName) -> None:
    errors: list[str] = [e.message for e in contract_validator(name).iter_errors(load_fixture(name))]
    assert errors == []


@pytest.mark.parametrize("name", MOTION)
def test_empty_bodies_are_valid(name: SchemaName) -> None:
    message: Message = load_fixture(name)
    message["bodies"] = {}
    assert contract_validator(name).is_valid(message)


@pytest.mark.parametrize("name", MOTION)
def test_every_role_is_accepted(name: SchemaName) -> None:
    roles: list[str] = load_schema("frame")["$defs"]["role"]["enum"]
    message: Message = load_fixture(name)
    body: Message = message["bodies"]["right_hand"]
    message["bodies"] = {role: dict(body) for role in roles}
    assert contract_validator(name).is_valid(message)


@pytest.mark.parametrize(
    ("name", "mutation"), [(name, mutation) for name, _, mutation in INVALID], ids=[f"{n}: {d}" for n, d, _ in INVALID]
)
def test_malformed_message_is_rejected(name: SchemaName, mutation: Mutation) -> None:
    message: Message = load_fixture(name)
    mutation(message)
    assert not contract_validator(name).is_valid(message)


def test_audio_is_audio_metrics_plus_timestamp() -> None:
    audio: Message = load_fixture("audio")
    assert contract_validator("audio").is_valid(audio)
    del audio["timestamp"]
    assert contract_validator("audio_metrics").is_valid(audio)
