"""Cross-process test harness: the real hub (`node server.js`) as a subprocess, a /viewer recorder, bounded waits."""
import ctypes
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

import numpy as np
import socketio
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from cobeart.common.strict_json import StrictJson
from cobeart.input.optitrack.sender import PayloadSender
from cobeart.input.optitrack.start_client import FramePipeline
from cobeart.settings.config import Settings
from cobeart.simulator.scenarios import Scenario, build_motion_scenario
from cobeart.simulator.tests.helpers import motion_config

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
APP_DIR: Path = REPO_ROOT / "cobeart-app"

HUB_START_TIMEOUT_S: float = 20.0
HUB_STOP_TIMEOUT_S: float = 5.0
VIEWER_CONNECT_TIMEOUT_S: float = 10.0
POLL_INTERVAL_S: float = 0.02
LOG_TAIL_CHARS: int = 2000
_PR_SET_PDEATHSIG: int = 1


class BodyMessage(TypedDict):
    """The body fields the e2e assertions read (see contract/frame.schema.json); the hub derives the velocities."""
    x: int
    y: int
    z: int
    wx: float
    wy: float
    wz: float
    abs_vel: float
    norm_abs_vel: float


class FrameMessage(TypedDict):
    """A /viewer `frame`; `timestamp` is hub receipt time (epoch ms), `bodies` is keyed by role."""
    schemaVersion: int
    timestamp: float
    bodies: dict[str, BodyMessage]


class AudioMessage(TypedDict):
    """A /viewer `audio`; `timestamp` is hub receipt time (epoch ms)."""
    schemaVersion: int
    timestamp: float


def hub_unavailable_reason() -> str | None:
    """Why the real hub cannot be booted here, or None when it can."""
    if shutil.which("node") is None:
        return "real hub unavailable: Node.js is not installed"
    if not (APP_DIR / "node_modules").is_dir():
        return "real hub unavailable: run `npm ci` in cobeart-app/"
    return None


def _die_with_parent() -> None:
    """preexec_fn: the child gets SIGTERM when pytest dies, so a killed test run leaves no orphan process."""
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    if libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) failed")


def wait_until(condition: Callable[[], bool], timeout_s: float, what: str) -> None:
    """Poll `condition` until true; raise TimeoutError naming `what` after `timeout_s`."""
    deadline: float = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out after {timeout_s:.1f} s waiting for {what}")
        time.sleep(POLL_INTERVAL_S)


def _free_port(host: str) -> int:
    with socket.socket() as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


@dataclass(frozen=True, slots=True)
class Hub:
    """A running hub subprocess and where its console output goes."""
    url: str
    log_path: Path
    process: subprocess.Popen[bytes]

    def log_text(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def log_tail(self) -> str:
        return self.log_text()[-LOG_TAIL_CHARS:]

    def health(self) -> dict[str, Any]:
        """The hub's /health JSON."""
        with urllib.request.urlopen(f"{self.url}/health", timeout=2.0) as response:
            body: dict[str, Any] = json.loads(response.read())
        return body


def _wait_for_hub(hub: Hub) -> None:
    listening: str = f"HTTP server on {hub.url}"
    deadline: float = time.monotonic() + HUB_START_TIMEOUT_S
    while True:
        if hub.process.poll() is not None:
            raise RuntimeError(f"hub exited with code {hub.process.returncode} during startup:\n{hub.log_tail()}")
        if listening in hub.log_text():  # our process logged its own listen line, not some other hub on the port
            try:
                hub.health()
                return
            except OSError:
                pass
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timed out after {HUB_START_TIMEOUT_S:.0f} s waiting for the hub at {hub.url}/health:\n"
                               f"{hub.log_tail()}")
        time.sleep(POLL_INTERVAL_S)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate the process, then kill it if it has not exited within HUB_STOP_TIMEOUT_S."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=HUB_STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=HUB_STOP_TIMEOUT_S)


@contextmanager
def running_hub(host: str, log_path: Path) -> Iterator[Hub]:
    """Boot `node server.js` on an ephemeral port and stop it on exit, also when the body raises.

    The hub inherits the environment, so it loads the same config (COBEART_CONFIG or the default) as the tests.
    """
    port: int = _free_port(host)
    env: dict[str, str] = {**os.environ, "PORT": str(port)}
    with log_path.open("wb") as log:
        process: subprocess.Popen[bytes] = subprocess.Popen(
            ["node", "server.js"], cwd=APP_DIR, env=env, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, preexec_fn=_die_with_parent,
        )
    hub: Hub = Hub(url=f"http://{host}:{port}", log_path=log_path, process=process)
    try:
        _wait_for_hub(hub)
        yield hub
    finally:
        _stop_process(process)


def start_python(args: Sequence[str]) -> subprocess.Popen[str]:
    """Start `python -m ...` from the repo root (so a worktree runs its own code), capturing its output."""
    return subprocess.Popen(
        [sys.executable, *args], cwd=REPO_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, preexec_fn=_die_with_parent,
    )


def run_python(args: Sequence[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
    """Run `python -m ...` to completion within `timeout_s`; raise with its output if it fails."""
    process: subprocess.Popen[str] = start_python(args)
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise TimeoutError(f"{' '.join(args)} did not finish within {timeout_s:.0f} s:\n{stderr[-LOG_TAIL_CHARS:]}")
    if process.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} exited with {process.returncode}:\n{stderr[-LOG_TAIL_CHARS:]}")
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


class Viewer:
    """A /viewer subscriber recording `frame` and `audio` events separately, in hub order.

    Messages stamped before `since_ms` are dropped, so a viewer can discard anything the hub sent before its
    producer started (the hub no longer replays old messages, but in-flight ones from an earlier test can still
    arrive on a shared hub). A viewer that never calls `start_recording` keeps everything it receives.
    """

    def __init__(self, url: str) -> None:
        self._url: str = url
        self._client: socketio.Client = socketio.Client(reconnection=False)
        self._frames: list[FrameMessage] = []
        self._audios: list[AudioMessage] = []
        self._changed: threading.Condition = threading.Condition()
        self._since_ms: float = 0.0
        self._client.on("frame", self._on_frame, namespace="/viewer")
        self._client.on("audio", self._on_audio, namespace="/viewer")

    def _on_frame(self, data: FrameMessage) -> None:
        with self._changed:
            if data["timestamp"] >= self._since_ms:
                self._frames.append(data)
                self._changed.notify_all()

    def _on_audio(self, data: AudioMessage) -> None:
        with self._changed:
            if data["timestamp"] >= self._since_ms:
                self._audios.append(data)
                self._changed.notify_all()

    def connect(self) -> None:
        self._client.connect(self._url, namespaces=["/viewer"], transports=["websocket"],
                             wait_timeout=VIEWER_CONNECT_TIMEOUT_S)

    def disconnect(self) -> None:
        self._client.disconnect()

    def start_recording(self) -> None:
        """Forget everything so far and keep only messages the hub stamps from now on."""
        with self._changed:
            self._since_ms = float(int(time.time() * 1000.0))  # hub stamps whole ms (Date.now())
            self._frames = []
            self._audios = []

    def frames(self) -> list[FrameMessage]:
        with self._changed:
            return list(self._frames)

    def audios(self) -> list[AudioMessage]:
        with self._changed:
            return list(self._audios)

    def wait_for(self, condition: Callable[[list[FrameMessage]], bool], timeout_s: float,
                 what: str) -> list[FrameMessage]:
        """Block until `condition(frames)` holds; raise TimeoutError naming `what` (and the frame count)."""
        with self._changed:
            if not self._changed.wait_for(lambda: condition(self._frames), timeout=timeout_s):
                raise TimeoutError(f"timed out after {timeout_s:.1f} s waiting for {what} on /viewer "
                                   f"({len(self._frames)} frames received)")
            return list(self._frames)

    def wait_for_count(self, count: int, timeout_s: float) -> list[FrameMessage]:
        return self.wait_for(lambda frames: len(frames) >= count, timeout_s, f"{count} frames")

    def wait_for_audio_count(self, count: int, timeout_s: float) -> list[AudioMessage]:
        """Block until at least `count` audio messages arrived; raise TimeoutError otherwise."""
        with self._changed:
            if not self._changed.wait_for(lambda: len(self._audios) >= count, timeout=timeout_s):
                raise TimeoutError(f"timed out after {timeout_s:.1f} s waiting for {count} audio messages on "
                                   f"/viewer ({len(self._audios)} received)")
            return list(self._audios)


@contextmanager
def recording_viewer(url: str) -> Iterator[Viewer]:
    """A connected Viewer that has started recording; disconnected on exit."""
    viewer: Viewer = Viewer(url)
    viewer.connect()
    try:
        viewer.start_recording()
        yield viewer
    finally:
        viewer.disconnect()


def _is_marker(frame: FrameMessage) -> bool:
    return not frame["bodies"]


def marker_pose_frame() -> dict[str, Any]:
    """A valid pose_frame with no bodies. No scenario or pipeline here sends one, so its frame marks a stream end."""
    return {"schemaVersion": 2, "source_time_s": 0.0, "bodies": {}}


def emit_once(url: str, event: str, payload: object, namespace: str = "/ingest") -> None:
    """Connect, emit one message and disconnect. For hand-built payloads (a v1 frame) and stream markers."""
    client: socketio.Client = socketio.Client(reconnection=False)
    client.connect(url, namespaces=[namespace], transports=["websocket"], wait_timeout=VIEWER_CONNECT_TIMEOUT_S)
    try:
        client.emit(event, payload, namespace=namespace)
        time.sleep(POLL_INTERVAL_S)  # let the write flush before the disconnect (engineio does not drain)
    finally:
        client.disconnect()


def frames_until_marker(url: str, viewer: Viewer, timeout_s: float) -> list[FrameMessage]:
    """The frames the viewer got before an end-of-stream marker sent on a new /ingest connection.

    Call it after the producer has disconnected. A producer's last few frames can be lost when it disconnects
    (python-engineio closes the websocket without draining its send queue), so a count of sent frames is no end
    condition. The hub handles messages in arrival order, and the marker's connection opens after the producer's
    closed, so everything the hub accepted from the producer reaches the viewer before the marker.
    """
    client: socketio.Client = socketio.Client(reconnection=False)
    client.connect(url, namespaces=["/ingest"], transports=["websocket"], wait_timeout=VIEWER_CONNECT_TIMEOUT_S)
    try:
        client.emit("pose_frame", marker_pose_frame(), namespace="/ingest")
        frames: list[FrameMessage] = viewer.wait_for(
            lambda received: any(_is_marker(frame) for frame in received), timeout_s, "the end-of-stream marker"
        )
    finally:
        client.disconnect()
    end: int = next(index for index, frame in enumerate(frames) if _is_marker(frame))
    return frames[:end]


def assert_all_valid(messages: Sequence[Any], validator: Draft202012Validator, kind: str = "frame") -> None:
    """Every message validates against its contract schema; the failure names the first offender."""
    for index, message in enumerate(messages):
        error = best_match(validator.iter_errors(message))
        if error is not None:
            raise AssertionError(f"{kind} {index} of {len(messages)} violates the contract at "
                                 f"{error.json_path}: {error.message}")


def roles_of(frame: FrameMessage) -> set[str]:
    return set(frame["bodies"])


# ---------------------------------------------------------------------------------------------------------------
# Adapters to interfaces owned by the Python producers (WP 6.2). Everything the suite assumes about their
# construction lives below, so a signature change there is a change in this section only.
# ---------------------------------------------------------------------------------------------------------------

def make_scenario(name: str) -> Scenario:
    """A named simulator motion scenario (a pure function of t) with the config's roles and arena."""
    return build_motion_scenario(name, motion_config())


def scenario_payload(scenario: Scenario, t: float) -> dict[str, Any]:
    """The pose_frame payload the scenario produces at time `t` (the simulator sends exactly this)."""
    payload: dict[str, Any] = scenario(t, np.random.default_rng(0)).to_payload()
    return payload


def build_motion_pipeline(
    settings: Settings, url: str, failure: threading.Event
) -> tuple[PayloadSender, FramePipeline]:
    """The real PayloadSender and FramePipeline, wired as start_client.start() wires them."""
    sender: PayloadSender = PayloadSender(
        client=socketio.Client(reconnection=False, json=StrictJson),
        url=url,
        framerate=settings.tracking.package_framerate,
        failure=failure,
    )
    pipeline: FramePipeline = FramePipeline(sender, settings.tracking.roles, failure)
    return sender, pipeline
