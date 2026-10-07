"""
Streaming beat tracking with madmom's online beat models.

Audio is analysed once, frame by frame, as it arrives: madmom's online LSTM ensemble (BEATS_LSTM) turns each
10 ms frame into a beat activation, and the DBN beat tracker's forward algorithm decides causally whether that
frame is a beat. Both keep their state between calls, so each sample is processed exactly once.

All times here are stream seconds: seconds of audio received since the tracker was created. A frame is stamped
with the time of its newest sample (its right edge), which is the earliest moment its content is known.

Not thread-safe: in production it runs alone in the beat process (see process.py), so the models never hold the
capture process's GIL.
"""
from typing import Final

import numpy as np
from madmom.audio.signal import FramedSignal, Signal
from madmom.audio.spectrogram import (
    FilteredSpectrogramProcessor,
    LogarithmicSpectrogramProcessor,
    SpectrogramDifferenceProcessor,
)
from madmom.audio.stft import ShortTimeFourierTransformProcessor
from madmom.features.beats import DBNBeatTrackingProcessor
from madmom.ml.nn import NeuralNetworkEnsemble
from madmom.models import BEATS_LSTM
from madmom.processors import SequentialProcessor

FPS: Final[int] = 100
"""Frame rate of the beat models (frames per second); the hop is sample_rate / FPS samples."""
MODEL_SAMPLE_RATE: Final[int] = 44100
MODEL_FRAME_SIZE: Final[int] = 2048
"""madmom's online beat models use 2048-sample frames at 44.1 kHz (46.4 ms)."""
MODEL_COUNT: Final[int] = len(BEATS_LSTM)


class BeatBacklogError(RuntimeError):
    """Beat processing fell too far behind the incoming audio to stay real time."""


def frame_geometry(sample_rate: int) -> tuple[int, int]:
    """(frame_size, hop_size) in samples at `sample_rate`, matching the models' 2048 @ 44.1 kHz window."""
    if sample_rate % FPS != 0:
        raise ValueError(
            f"Beat detection needs a sample rate that is a multiple of {FPS} Hz (got {sample_rate} Hz); "
            "use 44100 or 48000"
        )
    # Same window duration, and therefore the same FFT bin frequencies, as the model's input.
    return round(MODEL_FRAME_SIZE * sample_rate / MODEL_SAMPLE_RATE), sample_rate // FPS


class BeatTracker:
    """Online beat tracker over a sample stream: add samples, then process them into beat times."""

    def __init__(self, sample_rate: int, min_bpm: float = 55.0, max_bpm: float = 215.0,
                 max_backlog_seconds: float = 5.0) -> None:
        """
        Args:
            sample_rate: Sample rate in Hz; must be a multiple of FPS so frames are exactly 10 ms apart.
            min_bpm, max_bpm: Tempo range of the DBN tracker.
            max_backlog_seconds: Unprocessed audio that add_samples accepts before raising BeatBacklogError.
        """
        self.sample_rate: int = sample_rate
        self.frame_size, self.hop_size = frame_geometry(sample_rate)
        # Compensates the larger window's higher FFT magnitudes, so features match the model's input scale.
        self._frame_gain: float = MODEL_FRAME_SIZE / self.frame_size
        self.max_backlog_seconds: float = max_backlog_seconds
        self._pending: np.ndarray = np.zeros(self.frame_size + int(max_backlog_seconds * sample_rate),
                                             dtype=np.float32)
        self._pending_count: int = 0

        # madmom online pipeline, mirroring RNNBeatProcessor(online=True) minus its framing, which is done here.
        self._spectrogram: SequentialProcessor = SequentialProcessor([
            ShortTimeFourierTransformProcessor(),
            FilteredSpectrogramProcessor(num_bands=12, fmin=30, fmax=17000, norm_filters=True),
            LogarithmicSpectrogramProcessor(mul=1, add=1),
        ])
        self._difference: SpectrogramDifferenceProcessor = SpectrogramDifferenceProcessor(
            diff_ratio=0.5, positive_diffs=True, stack_diffs=np.hstack)
        self._network: NeuralNetworkEnsemble = NeuralNetworkEnsemble.load(BEATS_LSTM)
        self._tracker: DBNBeatTrackingProcessor = DBNBeatTrackingProcessor(
            fps=FPS, online=True, min_bpm=min_bpm, max_bpm=max_bpm)
        self._tracker.reset()
        self._frames_processed: int = 0

    @property
    def free_samples(self) -> int:
        """How many more samples add_samples accepts."""
        return len(self._pending) - self._pending_count

    def add_samples(self, block: np.ndarray) -> None:
        """Queue mono samples for processing."""
        n = len(block)
        if n > self.free_samples:
            raise BeatBacklogError(
                f"Beat processing fell more than {self.max_backlog_seconds:g} s behind the audio; the CPU is too "
                "busy for real-time beat detection (close other load or disable beat detection)"
            )
        self._pending[self._pending_count:self._pending_count + n] = block
        self._pending_count += n

    @property
    def stream_seconds(self) -> float:
        """Stream time up to which audio has been analysed (right edge of the last processed frame)."""
        if self._frames_processed == 0:
            return 0.0
        return self._frame_time(self._frames_processed - 1)

    @property
    def frames_processed(self) -> int:
        return self._frames_processed

    def _frame_time(self, frame_index: int) -> float:
        return (frame_index * self.hop_size + self.frame_size) / self.sample_rate

    def process_pending(self) -> list[float]:
        """Run the models on every complete pending frame; returns the stream times of the beats found."""
        if self._pending_count < self.frame_size:
            return []
        num_frames = (self._pending_count - self.frame_size) // self.hop_size + 1
        span = self.frame_size + (num_frames - 1) * self.hop_size
        segment = self._pending[:span] * self._frame_gain

        frames = FramedSignal(Signal(segment, sample_rate=self.sample_rate), frame_size=self.frame_size,
                              hop_size=self.hop_size, origin='stream', num_frames=num_frames)
        first = self._frames_processed == 0
        spectrogram = self._spectrogram(frames)
        # The difference processor sizes its online buffer from its first call and then always returns that many
        # rows, so it must see the same number of frames on every call: one.
        features = np.vstack([self._difference(spectrogram[i:i + 1], reset=first and i == 0)
                              for i in range(num_frames)])
        activations = self._network(features, reset=first)
        beats = self._tracker.process_online(activations, reset=False)

        consumed = num_frames * self.hop_size
        remaining = self._pending_count - consumed
        self._pending[:remaining] = self._pending[consumed:self._pending_count]
        self._pending_count = remaining
        self._frames_processed += num_frames
        return [self._frame_time(int(round(beat_seconds * FPS))) for beat_seconds in beats]
