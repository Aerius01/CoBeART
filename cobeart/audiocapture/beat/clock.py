"""Map audio stream time (samples received) to wall-clock time."""
import time
from typing import Callable


class StreamClock:
    """
    Estimates the wall-clock time of stream sample positions.

    The capture thread calls `advance` as soon as each block arrives. Every arrival gives an offset
    (arrival time minus stream time of the block's last sample); arrivals are only ever late, never early, so
    the smallest offset seen over the last one to two windows is the best estimate. Using recent windows only
    lets the estimate follow drift between the audio and system clocks.
    """

    def __init__(self, sample_rate: int, window_seconds: float = 2.0,
                 clock: Callable[[], float] = time.time) -> None:
        self.sample_rate: int = sample_rate
        self.window_seconds: float = window_seconds
        self._clock: Callable[[], float] = clock
        self._samples: int = 0
        self._window_start: float = float("-inf")
        self._previous_min: float = float("inf")
        self._current_min: float = float("inf")
        self._offset: float | None = None

    def advance(self, num_samples: int) -> None:
        """Record that num_samples more samples have just arrived (capture thread)."""
        now = self._clock()
        self._samples += num_samples
        offset = now - self._samples / self.sample_rate
        if now - self._window_start >= self.window_seconds:
            self._previous_min, self._current_min = self._current_min, offset
            self._window_start = now
        else:
            self._current_min = min(self._current_min, offset)
        # A single attribute store, so readers on other threads always see a complete value.
        self._offset = min(self._previous_min, self._current_min)

    @property
    def samples(self) -> int:
        """Samples received so far."""
        return self._samples

    @property
    def seconds(self) -> float:
        """Stream time of the newest sample received."""
        return self._samples / self.sample_rate

    def now(self) -> float:
        """Wall-clock time of the newest sample received: the present as far as the audio stream knows it."""
        return self.to_wall(self.seconds)

    def to_wall(self, stream_seconds: float) -> float:
        """Wall-clock time (Unix epoch seconds) of the given stream time."""
        offset = self._offset
        if offset is None:
            raise RuntimeError("StreamClock.to_wall called before any audio arrived (advance was never called)")
        return stream_seconds + offset
