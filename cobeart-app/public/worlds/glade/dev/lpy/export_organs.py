"""Sample an L-Py grass model into an organ table (JSON) validated against organ_table.schema.json.

Usage: export_organs.py <model.lpy> <out.json> [--samples N] [--seed S]
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence, get_type_hints

import jsonschema
from openalea.lpy import Lsystem

sys.path.insert(0, str(Path(__file__).resolve().parent))

import decline  # noqa: E402

LOG = logging.getLogger(__name__)

SCHEMA_VERSION = 2
SCHEMA_PATH = Path(__file__).with_name("organ_table.schema.json")
DEFAULT_SAMPLES = 25
TIME_DECIMALS = 9

Vec3 = tuple[float, float, float]
LeafState = Literal["growing", "mature", "senescent", "dead"]

LEAF_STATES: frozenset[str] = frozenset({"growing", "mature", "senescent", "dead"})


class OrganTableError(ValueError):
    """Raised when a model, its modules or the exported table violate the organ-table contract."""


@dataclass(frozen=True)
class LeafOrgan:
    id: str
    kind: Literal["leaf"]
    tiller: int
    rank: int
    birth_time: float
    age: float
    state: LeafState
    attach: Vec3
    direction: Vec3
    azimuth_rad: float
    angle_from_axis_rad: float
    sheath_length_m: float
    blade_length_m: float
    blade_width_m: float
    unroll: float
    arch_rad: float
    senescence: float
    fade: float
    decline_draw: float


@dataclass(frozen=True)
class TillerOrgan:
    id: str
    kind: Literal["tiller"]
    tiller: int
    rank: int
    birth_time: float
    age: float
    state: Literal["growing"]
    base: Vec3
    direction: Vec3
    azimuth_rad: float
    splay_rad: float


Organ = LeafOrgan | TillerOrgan

HEADER_FIELDS = frozenset({"id", "kind", "tiller", "rank", "birth_time", "age", "state"})


def _field_names(cls: type, vector: bool) -> tuple[str, ...]:
    """Non-header dataclass fields, the Vec3-typed ones when `vector` is true and the others otherwise."""
    hints = get_type_hints(cls)
    return tuple(f.name for f in fields(cls) if f.name not in HEADER_FIELDS and (hints[f.name] == Vec3) == vector)


LEAF_VECTOR_KEYS = _field_names(LeafOrgan, vector=True)
LEAF_SCALAR_KEYS = _field_names(LeafOrgan, vector=False)
TILLER_VECTOR_KEYS = _field_names(TillerOrgan, vector=True)
TILLER_SCALAR_KEYS = _field_names(TillerOrgan, vector=False)


@dataclass(frozen=True)
class Sample:
    time: float
    organs: tuple[Organ, ...]


@dataclass(frozen=True)
class ModelInfo:
    model: str
    seed: int
    params: dict[str, Any]
    dt: float
    t_end: float


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_positional(module_id: str, name: str, params: Sequence[Any], count: int) -> None:
    if len(params) != count:
        raise OrganTableError(f"module {name} {module_id}: expected {count} parameters, got {len(params)}")


def _number(module_id: str, field: str, value: object) -> float:
    if not _is_number(value):
        raise OrganTableError(f"module {module_id}: field {field!r} must be a finite number, got {value!r}")
    return float(value)  # type: ignore[arg-type]


def _vector(module_id: str, field: str, value: object) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise OrganTableError(f"module {module_id}: field {field!r} must be a list of 3 numbers, got {value!r}")
    x, y, z = (_number(module_id, f"{field}[{i}]", v) for i, v in enumerate(value))
    return (x, y, z)


def _check_keys(module_id: str, o: object, expected: Iterable[str]) -> dict[str, Any]:
    if not isinstance(o, dict):
        raise OrganTableError(f"module {module_id}: field 'o' must be a dict, got {type(o).__name__}")
    expected_set = set(expected)
    missing = sorted(expected_set - set(o))
    extra = sorted(set(o) - expected_set)
    if missing:
        raise OrganTableError(f"module {module_id}: field 'o' is missing key(s) {missing}")
    if extra:
        raise OrganTableError(f"module {module_id}: field 'o' has unexpected key(s) {extra}")
    return o


def leaf_from_module(params: Sequence[Any]) -> LeafOrgan:
    """Convert Leaf(id, tiller, rank, birth_time, age, state, o) into a LeafOrgan."""
    name = str(params[0]) if params else "?"
    module_id = f"Leaf {name}"
    _check_positional(name, "Leaf", params, 7)
    leaf_id, tiller, rank, birth_time, age, state, o = params
    if not isinstance(leaf_id, str):
        raise OrganTableError(f"module Leaf: field 'id' must be a string, got {leaf_id!r}")
    for field, value in (("tiller", tiller), ("rank", rank)):
        if not _is_int(value):
            raise OrganTableError(f"module {module_id}: field {field!r} must be an int, got {value!r}")
    birth_f = _number(module_id, "birth_time", birth_time)
    age_f = _number(module_id, "age", age)
    if state not in LEAF_STATES:
        raise OrganTableError(f"module {module_id}: field 'state' must be one of {sorted(LEAF_STATES)}, got {state!r}")
    o = _check_keys(module_id, o, LEAF_VECTOR_KEYS + LEAF_SCALAR_KEYS)
    vectors = {k: _vector(module_id, f"o.{k}", o[k]) for k in LEAF_VECTOR_KEYS}
    scalars = {k: _number(module_id, f"o.{k}", o[k]) for k in LEAF_SCALAR_KEYS}
    return LeafOrgan(
        id=leaf_id, kind="leaf", tiller=tiller, rank=rank, birth_time=round(birth_f, TIME_DECIMALS),
        age=round(age_f, TIME_DECIMALS), state=state, **vectors, **scalars,
    )


def tiller_from_module(params: Sequence[Any], time: float) -> TillerOrgan:
    """Convert Tiller(tiller, parent_rank, birth_time, o) into a TillerOrgan at the given sample time."""
    module_id = f"Tiller {params[0]}" if params else "Tiller ?"
    _check_positional(module_id, "Tiller", params, 4)
    tiller, parent_rank, birth_time, o = params
    for field, value in (("tiller", tiller), ("parent_rank", parent_rank)):
        if not _is_int(value):
            raise OrganTableError(f"module {module_id}: field {field!r} must be an int, got {value!r}")
    birth = _number(module_id, "birth_time", birth_time)
    o = _check_keys(module_id, o, TILLER_VECTOR_KEYS + TILLER_SCALAR_KEYS)
    vectors = {k: _vector(module_id, f"o.{k}", o[k]) for k in TILLER_VECTOR_KEYS}
    scalars = {k: _number(module_id, f"o.{k}", o[k]) for k in TILLER_SCALAR_KEYS}
    return TillerOrgan(
        id=f"T{tiller}", kind="tiller", tiller=tiller, rank=parent_rank, birth_time=round(birth, TIME_DECIMALS),
        age=round(time - birth, TIME_DECIMALS), state="growing", **vectors, **scalars,
    )


def organs_from_modules(modules: Iterable[Any], time: float) -> tuple[Organ, ...]:
    """Turn the Leaf and Tiller modules of a derived string into organs; other modules are ignored."""
    organs: list[Organ] = []
    for module in modules:
        params = list(module)
        if module.name == "Leaf":
            organs.append(leaf_from_module(params))
        elif module.name == "Tiller":
            organs.append(tiller_from_module(params, time))
    return tuple(sorted(organs, key=lambda o: (o.birth_time, o.id)))


def sample_steps(dt: float, t_end: float, samples: int) -> list[int]:
    """Step indices of N evenly spaced sample times over [0, t_end]; t_end is a multiple of dt."""
    last = round(t_end / dt)
    if samples < 2:
        raise OrganTableError(f"--samples must be at least 2, got {samples}")
    if samples > last + 1:
        raise OrganTableError(
            f"--samples {samples} exceeds the {last + 1} available steps (DT={dt}, T_END={t_end}); "
            f"maximum is {last + 1}"
        )
    return [round(i * last / (samples - 1)) for i in range(samples)]


def check_stable_ids(samples: Sequence[Sample]) -> None:
    """Raise if an organ id vanishes or changes kind, tiller, rank or birth_time in a later sample."""
    first_seen: dict[str, Organ] = {}
    previous_leaves: dict[str, LeafOrgan] = {}
    for sample in samples:
        by_id = {o.id: o for o in sample.organs}
        if len(by_id) != len(sample.organs):
            raise OrganTableError(f"duplicate organ id in sample at time {sample.time}")
        for organ_id, first in first_seen.items():
            later = by_id.get(organ_id)
            if later is None:
                raise OrganTableError(f"organ {organ_id} is missing from the sample at time {sample.time}")
            for field in ("kind", "tiller", "rank", "birth_time"):
                if getattr(first, field) != getattr(later, field):
                    raise OrganTableError(
                        f"organ {organ_id} changed {field} from {getattr(first, field)!r} "
                        f"to {getattr(later, field)!r} at time {sample.time}"
                    )
        check_leaf_invariants(sample, by_id, previous_leaves)
        for organ_id, organ in by_id.items():
            first_seen.setdefault(organ_id, organ)


SIZE_FIELDS = ("sheath_length_m", "blade_length_m", "blade_width_m")


def check_leaf_invariants(sample: Sample, by_id: dict[str, Organ], previous: dict[str, LeafOrgan]) -> None:
    """Check the life-cycle rules of every leaf in a sample and record it in `previous` for the next one.

    Rules: senescence is 1 exactly when dead; fade is positive only when dead and never decreases; at fade 1 the
    sizes are 0 and attach equals the tiller base, and a collapsed leaf stays collapsed. A dead leaf with fade < 1
    is a dry blade fading out and may have a nonzero size.
    """
    time = sample.time
    for organ_id, organ in by_id.items():
        if not isinstance(organ, LeafOrgan):
            continue
        before = previous.get(organ_id)
        if before is not None and before.state == "dead" and organ.state != "dead":
            raise OrganTableError(f"leaf {organ_id}: field 'state' left 'dead' for {organ.state!r} at time {time}")
        if (organ.senescence == 1) != (organ.state == "dead"):
            raise OrganTableError(
                f"leaf {organ_id}: field 'senescence' is {organ.senescence} but state is {organ.state!r} at time "
                f"{time}; senescence must be 1 if and only if the leaf is dead"
            )
        if organ.fade > 0 and organ.state != "dead":
            raise OrganTableError(
                f"leaf {organ_id}: field 'fade' is {organ.fade} but state is {organ.state!r} at time {time}; "
                "fade may be positive only when the leaf is dead"
            )
        if before is not None and organ.fade < before.fade:
            raise OrganTableError(
                f"leaf {organ_id}: field 'fade' decreased from {before.fade} to {organ.fade} at time {time}"
            )
        collapsed_before = before is not None and before.fade == 1
        if organ.fade == 1 or collapsed_before:
            _check_collapsed(organ, by_id, time, collapsed_before)
        previous[organ_id] = organ


def _check_collapsed(leaf: LeafOrgan, by_id: dict[str, Organ], time: float, collapsed_before: bool) -> None:
    """A collapsed leaf has zero sizes and sits at its tiller base."""
    for field in SIZE_FIELDS:
        if getattr(leaf, field) != 0:
            raise OrganTableError(
                f"leaf {leaf.id}: field {field!r} is {getattr(leaf, field)} at time {time}; "
                f"it must be 0 once fade is 1{' (collapsed earlier)' if collapsed_before else ''}"
            )
    tiller = by_id.get(f"T{leaf.tiller}")
    if not isinstance(tiller, TillerOrgan):
        raise OrganTableError(f"leaf {leaf.id}: tiller T{leaf.tiller} is missing at time {time}")
    if leaf.attach != tiller.base:
        raise OrganTableError(
            f"leaf {leaf.id}: field 'attach' {leaf.attach} must equal tiller base {tiller.base} at time {time} "
            "once fade is 1"
        )


def read_model_info(lsystem: Lsystem, model_path: Path) -> ModelInfo:
    """Read the externs, DT, T_END and SEED from the loaded model."""
    names = lsystem.context().globals()
    externs = names["__externs__"]
    for required in ("SEED", "DT", "T_END"):
        if required not in externs:
            raise OrganTableError(f"{model_path.name}: missing extern({required}=...) in the parameter block")
    params = {name: names[name] for name in sorted(externs)}
    for name, value in params.items():
        if not (_is_number(value) or isinstance(value, (str, bool))):
            raise OrganTableError(f"{model_path.name}: extern {name} must be a number, string or bool, got {value!r}")
    if not _is_int(params["SEED"]):
        raise OrganTableError(f"{model_path.name}: extern SEED must be an int, got {params['SEED']!r}")
    dt, t_end = _number("model", "DT", params["DT"]), _number("model", "T_END", params["T_END"])
    if dt <= 0 or t_end <= 0:
        raise OrganTableError(f"{model_path.name}: DT and T_END must be positive, got DT={dt}, T_END={t_end}")
    steps = t_end / dt
    if abs(steps - round(steps)) > 1e-9:
        raise OrganTableError(f"{model_path.name}: T_END={t_end} is not an integer multiple of DT={dt}")
    return ModelInfo(model=model_path.name, seed=params["SEED"], params=params, dt=dt, t_end=t_end)


def load_model(model_path: Path, seed: int | None) -> Lsystem:
    """Load the model, overriding the SEED extern when a seed is given."""
    if not model_path.is_file():
        raise OrganTableError(f"model file not found: {model_path}")
    lsystem = Lsystem(str(model_path), {} if seed is None else {"SEED": seed})
    return lsystem


def derive_samples(lsystem: Lsystem, info: ModelInfo, samples: int) -> list[Sample]:
    """Derive incrementally and capture the organs at each sample step."""
    tree = lsystem.axiom
    done = 0
    result: list[Sample] = []
    for step in sample_steps(info.dt, info.t_end, samples):
        if step > done:
            tree = lsystem.derive(tree, done, step - done)
            done = step
        time = round(step * info.dt, TIME_DECIMALS)
        result.append(Sample(time=time, organs=organs_from_modules(tree, time)))
    return result


def build_document(info: ModelInfo, samples: Sequence[Sample]) -> dict[str, Any]:
    """Assemble the JSON-ready organ-table document."""
    return {
        "schema_version": SCHEMA_VERSION,
        "model": info.model,
        "seed": info.seed,
        "params": info.params,
        "dt": info.dt,
        "t_end": info.t_end,
        "sample_times": [s.time for s in samples],
        "samples": [{"time": s.time, "organs": [_organ_dict(o) for o in s.organs]} for s in samples],
    }


def _organ_dict(organ: Organ) -> dict[str, Any]:
    data = asdict(organ)
    for key, value in data.items():
        if isinstance(value, tuple):
            data[key] = list(value)
    return data


def validate_document(document: dict[str, Any]) -> None:
    """Validate against organ_table.schema.json, raising OrganTableError with the failing path."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        path = "/".join(str(p) for p in first.absolute_path) or "<root>"
        raise OrganTableError(f"organ table invalid at {path}: {first.message} ({len(errors)} error(s) in total)")


def export_organs(
    model_path: Path, out_path: Path, samples: int = DEFAULT_SAMPLES, seed: int | None = None
) -> dict[str, Any]:
    """Derive, sample, check and validate the model, then write the JSON; nothing is written on error."""
    lsystem = load_model(model_path, seed)
    info = read_model_info(lsystem, model_path)
    if seed is not None and info.seed != seed:
        raise OrganTableError(
            f"{model_path.name}: --seed {seed} was ignored, SEED must be declared with extern(SEED=...)"
        )
    try:
        decline.DeclineParams.from_mapping(info.params)
    except ValueError as error:
        raise OrganTableError(f"{model_path.name}: {error}") from error
    sampled = derive_samples(lsystem, info, samples)
    check_stable_ids(sampled)
    document = build_document(info, sampled)
    validate_document(document)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return document


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model", type=Path, help="L-Py model file")
    parser.add_argument("out", type=Path, help="output JSON file")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES, help="number of sample times (default 25)")
    parser.add_argument("--seed", type=int, default=None, help="override the SEED extern")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    try:
        document = export_organs(args.model, args.out, args.samples, args.seed)
    except OrganTableError as error:
        LOG.error("%s", error)
        return 1
    last = document["samples"][-1]["organs"]
    print(f"wrote {args.out}: {len(document['samples'])} samples, {len(last)} organs at t={document['t_end']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
