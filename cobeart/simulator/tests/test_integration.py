"""End-to-end: simulator -> hub -> /viewer subscriber.

Uses the real hub (`node cobeart-app/server.js` on an ephemeral port); skipped when node or node_modules is missing.
"""
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import socketio
from jsonschema import Draft202012Validator

from cobeart.simulator.__main__ import main
from cobeart.simulator.audio import AudioConfig, build_audio_scenario
from cobeart.simulator.emitter import RunConfig, Simulator
from cobeart.simulator.scenarios import MotionConfig, build_motion_scenario, load_body_map

REPO_ROOT: Path = Path(__file__).resolve().parents[3]
APP_DIR: Path = REPO_ROOT / "cobeart-app"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_health(url: str, timeout_s: float = 15.0) -> None:
    deadline: float = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1.0):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Hub at {url} did not become healthy within {timeout_s}s")


@pytest.fixture
def hub_url() -> Iterator[str]:
    if shutil.which("node") is None or not (APP_DIR / "node_modules").is_dir():
        pytest.skip("real hub unavailable: install Node and run `npm ci` in cobeart-app/")
    port: int = _free_port()
    url: str = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        ["node", "server.js"], cwd=APP_DIR, env={**os.environ, "PORT": str(port)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_health(url)
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _collect_viewer(url: str, run: Any) -> list[dict[str, Any]]:
    received: list[dict[str, Any]] = []
    viewer = socketio.Client()
    viewer.on("frame", lambda data: received.append(data), namespace="/viewer")
    viewer.connect(url, namespaces=["/viewer"], wait_timeout=10)
    try:
        run()
        time.sleep(0.5)
    finally:
        viewer.disconnect()
    return received


def _assert_frames(received: list[dict[str, Any]], frame_validator: Draft202012Validator) -> None:
    assert len(received) > 50
    with_audio: list[dict[str, Any]] = [f for f in received if "audio" in f]
    assert with_audio, "no frame carried merged audio"
    frame_validator.validate(received[0])
    frame_validator.validate(with_audio[-1])
    assert {b["ID"] for b in received[-1]["rigidbodies"]} == {0, 1, 2, 3}


def test_simulator_frames_with_merged_audio_reach_viewer(hub_url: str, frame_validator: Draft202012Validator) -> None:
    motion_cfg = MotionConfig.from_body_map(
        load_body_map(), rate_hz=120.0, num_bodies=4, start_epoch_ms=time.time() * 1000
    )
    audio_cfg = AudioConfig(start_epoch_s=time.time(), lock_in_s=0.5)
    simulator = Simulator(
        client=socketio.Client(),
        motion=build_motion_scenario("orbit", motion_cfg),
        audio=build_audio_scenario("beat", audio_cfg),
        run=RunConfig(url=hub_url, motion_rate_hz=120.0, audio_rate_hz=100.0, seed=0, duration_s=2.0),
    )
    sent: dict[str, int] = {}

    def run() -> None:
        sent.update(simulator.run())

    received: list[dict[str, Any]] = _collect_viewer(hub_url, run)
    assert sent["frame"] > 200 and sent["audio_metrics"] > 150
    _assert_frames(received, frame_validator)


def test_cli_streams_to_hub(hub_url: str, frame_validator: Draft202012Validator) -> None:
    argv: list[str] = [
        "--scenario", "orbit", "--audio", "beat", "--url", hub_url, "--duration", "2", "--rate", "120",
    ]
    received: list[dict[str, Any]] = _collect_viewer(hub_url, lambda: main(argv))
    _assert_frames(received, frame_validator)
