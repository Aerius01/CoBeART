"""Builds contract `frame` messages from tracked arena poses and emits them on Socket.IO /ingest."""
import json
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from socketio.exceptions import ConnectionError as SocketIOConnectionError
from socketio.exceptions import SocketIOError

from cobeart.optitrackclient.transform import ArenaPose
from cobeart.packagesender.metrics import BodyMetrics, MetricsTracker

logger: logging.Logger = logging.getLogger(__name__)

SCHEMA_VERSION: int = 1
INGEST_NAMESPACE: str = "/ingest"
FRAME_EVENT: str = "frame"
DEFAULT_RECONNECT_INTERVAL_S: float = 2.0
CONNECT_WAIT_TIMEOUT_S: float = 2.0
STOP_JOIN_TIMEOUT_S: float = 3.0
# Tolerance when comparing frame times against the send schedule, so float rounding does not skip a due frame.
SCHEDULE_EPSILON_S: float = 1e-6

Payload = dict[str, Any]


class StrictJson:
    """JSON codec for socketio.Client(json=StrictJson): NaN or infinity raises instead of reaching the hub."""

    @staticmethod
    def dumps(obj: object, **kwargs: Any) -> str:
        return json.dumps(obj, allow_nan=False, **kwargs)

    @staticmethod
    def loads(s: str | bytes, **kwargs: Any) -> Any:
        return json.loads(s, **kwargs)


class SocketClient(Protocol):
    """The subset of socketio.Client used by PayloadSender."""

    connected: bool

    def on(self, event: str, handler: Callable[..., None], namespace: str) -> None: ...

    def connect(
        self, url: str, transports: list[str], namespaces: list[str], wait: bool, wait_timeout: float
    ) -> None: ...

    def emit(self, event: str, data: Payload, namespace: str) -> None: ...

    def disconnect(self) -> None: ...


def rigid_body_entry(pose: ArenaPose, metrics: BodyMetrics) -> Payload:
    """One contract rigid-body entry: integer mm position, arena quaternion and velocities."""
    x, y, z = pose.position
    qx, qy, qz, qw = pose.orientation
    return {
        "ID": metrics.id,
        "x": int(x),  # [mm]
        "y": int(y),
        "z": int(z),
        "qx": float(qx),  # unit quaternion, arena axes
        "qy": float(qy),
        "qz": float(qz),
        "qw": float(qw),
        "vx": float(metrics.velocity[0]),  # [mm/s]
        "vy": float(metrics.velocity[1]),
        "vz": float(metrics.velocity[2]),
        "wx": float(metrics.angular_velocity[0]),  # [deg/s], arena axes
        "wy": float(metrics.angular_velocity[1]),
        "wz": float(metrics.angular_velocity[2]),
        "abs_vel": float(metrics.abs_velocity),  # [mm/s]
        "norm_abs_vel": float(metrics.norm_abs_velocity),  # [0..1]
    }


def build_frame(rigidbodies: list[Payload], timestamp_ms: float) -> Payload:
    """A contract `frame` message for the given rigid-body entries."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "type": "optitrack",
        "timestamp": timestamp_ms,
        "rigidbodies": rigidbodies,
    }


def _all_finite(values: tuple[float, ...]) -> bool:
    return all(math.isfinite(v) for v in values)


class PayloadSender:
    """Updates per-body metrics on every mocap frame and emits frames on /ingest at most at `framerate`.

    `handle_frame` may be called from the motion source's receive thread; metric and schedule state are guarded
    by a lock. Connection state is driven by Socket.IO handler threads and a background reconnect loop. Inject a
    client created with `reconnection=False` and `json=StrictJson`: this class owns reconnection. An unexpected
    error in the reconnect loop is logged, sets `failure` and ends the loop; the owner must watch `failure`.
    """

    def __init__(
        self,
        client: SocketClient,
        tracker: MetricsTracker,
        url: str,
        framerate: float,
        failure: threading.Event,
        reconnect_interval_s: float = DEFAULT_RECONNECT_INTERVAL_S,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if framerate <= 0:
            raise ValueError(f"framerate must be positive, got {framerate}")
        self._client: SocketClient = client
        self._tracker: MetricsTracker = tracker
        self._url: str = url
        self._period_s: float = 1.0 / framerate
        self._reconnect_interval_s: float = reconnect_interval_s
        self._wall_clock: Callable[[], float] = wall_clock
        self._failure: threading.Event = failure

        self._frame_lock: threading.Lock = threading.Lock()
        self._next_send_s: float | None = None

        self._connected: threading.Event = threading.Event()
        self._stop: threading.Event = threading.Event()
        self._reconnect_thread: threading.Thread | None = None
        self._episode_lock: threading.Lock = threading.Lock()
        self._drop_logged: bool = False
        self._unreachable_logged: bool = False
        self._corrupt_logged: bool = False

        client.on("connect", self._on_connect, namespace=INGEST_NAMESPACE)
        client.on("disconnect", self._on_disconnect, namespace=INGEST_NAMESPACE)
        # No connect_error handler: it fires on every failed attempt, which connect() already raises and logs once.

    # Lifecycle

    def connect(self) -> None:
        """Start the background loop that connects to the hub and reconnects after drops."""
        if self._reconnect_thread is not None:
            raise RuntimeError("PayloadSender.connect() called twice")
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, name="payload-sender-reconnect", daemon=True
        )
        self._reconnect_thread.start()

    def stop(self) -> None:
        """Stop reconnecting and disconnect from the hub."""
        self._stop.set()
        if self._reconnect_thread is not None:
            self._reconnect_thread.join(timeout=STOP_JOIN_TIMEOUT_S)
            if self._reconnect_thread.is_alive():
                logger.warning("Reconnect thread still blocked in a connect attempt after %.0f s; abandoning it",
                               STOP_JOIN_TIMEOUT_S)
        if self._client.connected:
            self._client.disconnect()
        logger.info("Payload sender stopped")

    def is_connected(self) -> bool:
        """True while the /ingest namespace is connected."""
        return self._connected.is_set()

    def _reconnect_loop(self) -> None:
        try:
            while not self._stop.is_set():
                if not self._client.connected:
                    self._attempt_connect()
                self._stop.wait(self._reconnect_interval_s)
        except Exception:
            # Not a connection failure (e.g. an unusable URL): retrying cannot help, so surface it to the owner.
            logger.exception("Reconnect loop for %s failed; frames can no longer be sent", self._url)
            self._failure.set()

    def _attempt_connect(self) -> None:
        try:
            self._client.connect(
                self._url,
                transports=["websocket"],
                namespaces=[INGEST_NAMESPACE],
                wait=True,
                wait_timeout=CONNECT_WAIT_TIMEOUT_S,
            )
        except SocketIOConnectionError as exc:
            with self._episode_lock:
                first: bool = not self._unreachable_logged
                self._unreachable_logged = True
            if first:
                logger.warning(
                    "Hub at %s unreachable (%s); retrying every %.1f s", self._url, exc, self._reconnect_interval_s
                )
            else:
                logger.debug("Hub at %s still unreachable: %s", self._url, exc)

    def _on_connect(self) -> None:
        with self._episode_lock:
            self._drop_logged = False
            self._unreachable_logged = False
        self._connected.set()
        logger.info("Connected to %s%s", self._url, INGEST_NAMESPACE)

    def _on_disconnect(self, reason: object = None) -> None:
        self._connected.clear()
        logger.warning("Disconnected from %s%s (%s)", self._url, INGEST_NAMESPACE, reason)

    # Frames

    def handle_frame(self, frame_time_s: float, bodies: Mapping[int, ArenaPose]) -> Payload | None:
        """Update metrics for one mocap frame (`frame_time_s` from the mocap clock); emit it if a send is due.

        Returns the emitted frame, or None when the send-rate gate skipped it, the frame was corrupt (non-finite
        timestamp or value) or the hub is not connected.
        """
        if not math.isfinite(frame_time_s):
            self._log_corrupt_once(f"non-finite mocap timestamp {frame_time_s!r}")
            return None
        with self._frame_lock:
            entries: list[Payload] = []
            for body_id, pose in bodies.items():
                # Check the pose before the tracker sees it, so a NaN cannot poison the body's velocity state.
                if not _all_finite((*pose.position, *pose.orientation)):
                    self._log_corrupt_once(f"non-finite pose for body {body_id}")
                    return None
                metrics: BodyMetrics = self._tracker.update(body_id, frame_time_s, *pose.position, *pose.orientation)
                if not _all_finite((*metrics.velocity, *metrics.angular_velocity,
                                    metrics.abs_velocity, metrics.norm_abs_velocity)):
                    self._log_corrupt_once(f"non-finite velocity for body {body_id}")
                    return None
                entries.append(rigid_body_entry(pose, metrics))
            with self._episode_lock:
                self._corrupt_logged = False
            if not self._send_due(frame_time_s):
                return None
        if not self._connected.is_set():
            self._log_drop_once("not connected to the hub")
            return None
        frame: Payload = build_frame(entries, self._wall_clock() * 1000.0)
        try:
            self._client.emit(FRAME_EVENT, frame, namespace=INGEST_NAMESPACE)
        except SocketIOError as exc:
            self._log_drop_once(f"emit failed: {exc!r}")
            return None
        logger.debug("Sent frame with %d bodies", len(entries))
        return frame

    def _send_due(self, frame_time_s: float) -> bool:
        """Send-rate gate on the mocap clock. Resynchronizes on the first frame and when the clock jumps back."""
        next_send: float | None = self._next_send_s
        if next_send is not None and next_send - frame_time_s > self._period_s:
            next_send = None  # mocap clock restarted
        if next_send is not None and frame_time_s + SCHEDULE_EPSILON_S < next_send:
            return False
        next_send = frame_time_s if next_send is None else next_send
        next_send += self._period_s
        if next_send <= frame_time_s:
            next_send = frame_time_s + self._period_s  # fell behind (gap in frames): do not burst
        self._next_send_s = next_send
        return True

    def _log_corrupt_once(self, reason: str) -> None:
        with self._episode_lock:
            first: bool = not self._corrupt_logged
            self._corrupt_logged = True
        if first:
            logger.warning("Dropping corrupt mocap frames until a valid one arrives: %s", reason)

    def _log_drop_once(self, reason: str) -> None:
        with self._episode_lock:
            first: bool = not self._drop_logged
            self._drop_logged = True
        if first:
            logger.warning("Dropping frames until reconnected: %s", reason)
