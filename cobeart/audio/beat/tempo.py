"""Tempo estimation from a stream of beat times (stream seconds)."""
import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Final, Optional, Protocol

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_ANALYSIS_LAG_SECONDS: Final[float] = 0.5
"""How far beat analysis may trail the live audio before it is warned about and the tempo counts as stale."""


@dataclass(frozen=True)
class TempoState:
    """A stable tempo: the latest beat (stream seconds), the beat interval (seconds) and the tempo (BPM)."""

    last_beat: float
    interval: float
    bpm: float


class TempoSource(Protocol):
    """Anything that reports the current stable tempo given the live stream time."""

    def get_prediction_state(self, live_seconds: float) -> Optional[TempoState]: ...


class TempoEstimator:
    """Turns beat times into a stable tempo (thread-safe: beats added on one thread, state read on others)."""

    def __init__(self, tempo_tolerance: float = 0.10, stable_beats: int = 4, history_size: int = 8,
                 max_missed_beats: float = 2.0, analysis_lag_seconds: float = DEFAULT_ANALYSIS_LAG_SECONDS) -> None:
        """
        Args:
            tempo_tolerance: A beat interval more than this fraction away from the running mean restarts the
                estimate from that beat.
            stable_beats: Consecutive consistent beats needed before the tempo counts as stable.
            history_size: Beats kept for the tempo estimate (the BPM is the mean of their intervals).
            max_missed_beats: The tempo stops being stable when no beat was found for this many intervals.
            analysis_lag_seconds: Tolerated lag of the analysed audio behind the live audio (see
                get_prediction_state).
        """
        if stable_beats < 3 or history_size < stable_beats:
            raise ValueError(
                f"Need 3 <= stable_beats <= history_size (got stable_beats={stable_beats}, "
                f"history_size={history_size})"
            )
        self.tempo_tolerance: float = tempo_tolerance
        self.stable_beats: int = stable_beats
        self.max_missed_beats: float = max_missed_beats
        self.analysis_lag_seconds: float = analysis_lag_seconds
        self._history: deque[float] = deque(maxlen=history_size)
        self._analysed_seconds: float = 0.0
        self._lock: threading.Lock = threading.Lock()

    def update(self, beats: list[float], analysed_seconds: float) -> None:
        """Add newly found beats and record how far the audio has been analysed."""
        with self._lock:
            for beat_time in beats:
                if len(self._history) >= 2:
                    mean_interval = (self._history[-1] - self._history[0]) / (len(self._history) - 1)
                    interval = beat_time - self._history[-1]
                    if abs(interval - mean_interval) > self.tempo_tolerance * mean_interval:
                        logger.debug("Beat interval %.3f s breaks tempo (mean %.3f s); restarting estimate",
                                     interval, mean_interval)
                        self._history.clear()
                self._history.append(beat_time)
                logger.debug("Beat at stream %.3f s (history %d)", beat_time, len(self._history))
            self._analysed_seconds = analysed_seconds

    def get_prediction_state(self, live_seconds: float) -> Optional[TempoState]:
        """
        The current stable tempo, or None while there is none.

        None when there are too few consistent beats, or when the last beat is stale: more than max_missed_beats
        intervals before the analysed audio (the music stopped), or before the live stream time `live_seconds`
        plus the tolerated analysis lag. The second check means a stalled or dead analysis cannot keep a tempo
        alive while live audio moves on.
        """
        with self._lock:
            if len(self._history) < self.stable_beats:
                return None
            last = self._history[-1]
            interval = (last - self._history[0]) / (len(self._history) - 1)
            analysed = self._analysed_seconds
        max_gap = self.max_missed_beats * interval
        if analysed - last > max_gap or live_seconds - last > max_gap + self.analysis_lag_seconds:
            return None
        return TempoState(last_beat=last, interval=interval, bpm=60.0 / interval)

    @property
    def tempo_bpm(self) -> Optional[float]:
        """Stable tempo in BPM as of the analysed audio, or None."""
        state = self.get_prediction_state(self._analysed_seconds)
        return None if state is None else state.bpm
