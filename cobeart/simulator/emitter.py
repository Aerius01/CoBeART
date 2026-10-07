"""Emitter loop: drives the pure scenarios on a fixed schedule and sends them over Socket.IO."""
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from socketio.exceptions import SocketIOError

from cobeart.simulator.audio import AudioScenario
from cobeart.simulator.faults import apply_fault
from cobeart.simulator.scenarios import Scenario

logger: logging.Logger = logging.getLogger(__name__)

INGEST_NAMESPACE: str = "/ingest"
AUDIO_NAMESPACE: str = "/audio"
CONNECT_TIMEOUT_S: float = 10.0


class SocketClient(Protocol):
    """The subset of socketio.Client used here."""

    def connect(self, url: str, namespaces: list[str], wait_timeout: float) -> None: ...

    def emit(self, event: str, data: Any, namespace: str) -> None: ...

    def disconnect(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Timing, fault and stream-stop options for one simulator run."""
    url: str
    motion_rate_hz: float
    audio_rate_hz: float
    seed: int
    duration_s: float | None = None
    fault: str | None = None
    motion_stop_after_s: float | None = None
    audio_stop_after_s: float | None = None


@dataclass(slots=True)
class _Stream:
    """One scheduled message stream."""
    event: str
    namespace: str
    rate_hz: float
    stop_after_s: float | None
    produce: Callable[[float], dict[str, Any]]
    tick: int = 0

    @property
    def next_t(self) -> float:
        return self.tick / self.rate_hz


class Simulator:
    """Emits `frame` on /ingest and `audio_metrics` on /audio at their own rates until the duration ends."""

    def __init__(
        self,
        client: SocketClient,
        motion: Scenario | None,
        audio: AudioScenario | None,
        run: RunConfig,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client: SocketClient = client
        self._motion: Scenario | None = motion
        self._audio: AudioScenario | None = audio
        self._run: RunConfig = run
        self._clock: Callable[[], float] = clock
        self._sleep: Callable[[float], None] = sleep

    def _streams(self) -> list[_Stream]:
        cfg: RunConfig = self._run
        motion_rng: np.random.Generator = np.random.default_rng([cfg.seed, 0])
        audio_rng: np.random.Generator = np.random.default_rng([cfg.seed, 1])
        streams: list[_Stream] = []
        if self._motion is not None:
            motion: Scenario = self._motion
            streams.append(_Stream(
                "frame", INGEST_NAMESPACE, cfg.motion_rate_hz, cfg.motion_stop_after_s,
                lambda t: apply_fault(cfg.fault, motion(t, motion_rng).to_payload()),
            ))
        if self._audio is not None:
            audio: AudioScenario = self._audio
            streams.append(_Stream(
                "audio_metrics", AUDIO_NAMESPACE, cfg.audio_rate_hz, cfg.audio_stop_after_s,
                lambda t: apply_fault(cfg.fault, audio(t, audio_rng).to_payload()),
            ))
        return streams

    def run(self) -> dict[str, int]:
        """Connect, stream until the duration elapses, disconnect. Returns messages sent per event."""
        cfg: RunConfig = self._run
        active: list[_Stream] = self._streams()
        sent: dict[str, int] = {stream.event: 0 for stream in active}
        self._client.connect(cfg.url, namespaces=[INGEST_NAMESPACE, AUDIO_NAMESPACE], wait_timeout=CONNECT_TIMEOUT_S)
        logger.info("Connected to %s", cfg.url)
        start: float = self._clock()
        try:
            while active:
                stream: _Stream = min(active, key=lambda s: s.next_t)
                t: float = stream.next_t
                if cfg.duration_s is not None and t >= cfg.duration_s:
                    break
                if stream.stop_after_s is not None and t >= stream.stop_after_s:
                    logger.info("Stopped %s stream at t=%.2fs", stream.event, t)
                    active.remove(stream)
                    continue
                delay: float = start + t - self._clock()
                if delay > 0:
                    self._sleep(delay)
                try:
                    self._client.emit(stream.event, stream.produce(t), namespace=stream.namespace)
                except SocketIOError as exc:
                    if cfg.fault is None:
                        raise
                    # The hub drops a connection that sends an undecodable message (e.g. NaN): expected here.
                    logger.warning("Hub dropped the connection after a %r message (%r); stopping", cfg.fault, exc)
                    break
                stream.tick += 1
                sent[stream.event] += 1
            if not active and cfg.duration_s is not None:
                self._sleep(max(0.0, start + cfg.duration_s - self._clock()))
        finally:
            self._client.disconnect()
        logger.info("Done: %s", sent)
        return sent
