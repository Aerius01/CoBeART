"""
Predictive beat layer: fire beats on time by extrapolating the stable tempo grid.

The beat tracker only knows a beat once its audio has been captured and analysed. While the tempo is stable, this
layer projects the beat grid (last detected beat + k * interval) into wall-clock time and reports each grid point
the first time it is polled once the audio stream has reached it. Time is taken from the stream (the wall-clock
time of the newest captured sample), not from the system clock: a beat is reported in the first capture block
that contains it even when the capture thread runs late, and no beat is reported for audio that never arrived.
"""
import math
import threading
from typing import Optional

from cobeart.audiocapture.beat.clock import StreamClock
from cobeart.audiocapture.beat.tempo import TempoSource


class PredictiveBeatLayer:
    """Turns a tempo source's stable tempo into on-time beat events (thread-safe)."""

    def __init__(
        self,
        tempo_source: TempoSource,
        stream_clock: StreamClock,
        poll_interval: float = 0.05,
        late_tolerance: float = 0.05,
    ) -> None:
        """
        Args:
            tempo_source: Source of the stable tempo state.
            stream_clock: Maps stream seconds to wall-clock time and tells how far the stream has got.
            poll_interval: Stream seconds between refreshes of the tempo state.
            late_tolerance: A grid point more than this many seconds before the stream position when first
                considered is skipped, so a consumer that polls rarely never gets a burst of stale beats.
        """
        self._tempo_source: TempoSource = tempo_source
        self._stream_clock: StreamClock = stream_clock
        self._poll_interval: float = poll_interval
        self._late_tolerance: float = late_tolerance
        self._lock: threading.Lock = threading.Lock()
        self._last_poll: float = float("-inf")
        self._last_emitted: float = float("-inf")
        self._next_beat: Optional[float] = None
        self._interval: Optional[float] = None
        self._tempo: Optional[float] = None

    def get_next_beat(self) -> tuple[bool, Optional[float], Optional[float]]:
        """Return (beat, tempo_bpm, beat_timestamp); beat is True once per predicted beat, tempo None if unstable."""
        with self._lock:
            if self._stream_clock.samples == 0:
                return False, None, None
            now = self._stream_clock.now()
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
        state = self._tempo_source.get_prediction_state(self._stream_clock.seconds)
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
