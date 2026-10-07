"""
Predictive beat layer: fire beats on time by extrapolating the detector's stable tempo grid.

The detector only knows a beat once its audio has been captured and analysed. While the tempo is stable, this
layer projects the beat grid (last detected beat + k * interval) into wall-clock time and reports each grid point
the first time it is polled at or after that time. Each reported beat carries its predicted timestamp.
"""
import math
import threading
import time
from typing import Callable, Optional

from cobeart.audiocapture.beat.clock import StreamClock
from cobeart.audiocapture.beat.detector import BeatDetector


class PredictiveBeatLayer:
    """Turns a BeatDetector's tempo state into on-time beat events (thread-safe)."""

    def __init__(
        self,
        beat_detector: BeatDetector,
        stream_clock: StreamClock,
        poll_interval: float = 0.05,
        late_tolerance: float = 0.05,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """
        Args:
            beat_detector: Source of the stable tempo state.
            stream_clock: Maps the detector's stream seconds to wall-clock time.
            poll_interval: Seconds between refreshes of the detector state.
            late_tolerance: A grid point more than this many seconds in the past when first considered is
                skipped, so a slow consumer never gets a burst of stale beats.
            clock: Wall-clock source.
        """
        self._detector: BeatDetector = beat_detector
        self._stream_clock: StreamClock = stream_clock
        self._poll_interval: float = poll_interval
        self._late_tolerance: float = late_tolerance
        self._clock: Callable[[], float] = clock
        self._lock: threading.Lock = threading.Lock()
        self._last_poll: float = float("-inf")
        self._last_emitted: float = float("-inf")
        self._next_beat: Optional[float] = None
        self._interval: Optional[float] = None
        self._tempo: Optional[float] = None

    def get_next_beat(self) -> tuple[bool, Optional[float], Optional[float]]:
        """Return (beat, tempo_bpm, beat_timestamp); beat is True once per predicted beat, tempo None if unstable."""
        now = self._clock()
        with self._lock:
            if now - self._last_poll >= self._poll_interval:
                self._last_poll = now
                self._refresh(now)
            if self._next_beat is not None and self._interval is not None and now >= self._next_beat:
                beat_time = self._next_beat
                self._last_emitted = beat_time
                self._next_beat = beat_time + self._interval
                return True, self._tempo, beat_time
            return False, self._tempo, None

    def _refresh(self, now: float) -> None:
        state = self._detector.get_prediction_state()
        if state is None:
            self._next_beat = self._interval = self._tempo = None
            return
        anchor = self._stream_clock.to_wall(state.last_beat)
        earliest = max(now - self._late_tolerance,
                       self._last_emitted + 0.5 * state.interval,
                       anchor + 0.5 * state.interval)
        self._next_beat = anchor + math.ceil((earliest - anchor) / state.interval) * state.interval
        self._interval = state.interval
        self._tempo = state.bpm
