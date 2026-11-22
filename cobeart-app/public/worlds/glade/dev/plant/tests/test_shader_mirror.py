"""The GLSL decline port (grass/shader.mjs) must list the same parameters and constant as decline.py."""
from __future__ import annotations

import dataclasses
import re
import sys

from conftest import PLANT_DIR

DEV_DIR = PLANT_DIR.parent
SHADER = DEV_DIR / "grass" / "shader.mjs"
sys.path.insert(0, str(DEV_DIR / "lpy"))
import decline  # noqa: E402


def shader_text() -> str:
    return SHADER.read_text(encoding="utf-8")


def test_decline_params_struct_lists_the_python_fields_in_order() -> None:
    match = re.search(r"struct\s+DeclineParams\s*\{(.*?)\};", shader_text(), re.DOTALL)
    assert match, f"{SHADER} has no 'struct DeclineParams {{ ... }};' block"
    names = re.findall(r"\bfloat\s+(\w+)\s*;", match.group(1))
    expected = [f.name for f in dataclasses.fields(decline.DeclineParams)]
    assert names == expected, f"{SHADER}: struct DeclineParams has fields {names}, decline.DeclineParams has {expected}"


def test_max_death_offset_matches_python() -> None:
    match = re.search(r"const\s+float\s+MAX_DEATH_OFFSET\s*=\s*([0-9.eE+-]+)\s*;", shader_text())
    assert match, f"{SHADER} has no 'const float MAX_DEATH_OFFSET = <x>;'"
    assert float(match.group(1)) == decline.MAX_DEATH_OFFSET, (
        f"{SHADER}: MAX_DEATH_OFFSET is {match.group(1)}, decline.py has {decline.MAX_DEATH_OFFSET}")
