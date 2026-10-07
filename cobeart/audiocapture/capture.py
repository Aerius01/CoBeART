import numpy as np
import time
import threading
import logging
import sys
from typing import ContextManager, Optional, Protocol
from cobeart.audiocapture.beat import BeatDetector, PredictiveBeatLayer, StreamClock
from cobeart.audiocapture.utils import select_audio_device

logger: logging.Logger = logging.getLogger(__name__)

AUDIO_SCHEMA_VERSION: int = 1


class AudioRecorder(Protocol):
    """An open recording session that yields blocks of samples."""

    def record(self, numframes: int) -> Optional[np.ndarray]: ...


class AudioSource(Protocol):
    """What the capturer uses from a microphone (satisfied by soundcard.Microphone)."""

    def recorder(self, samplerate: float, channels: list[int], blocksize: int) -> ContextManager[AudioRecorder]: ...


class AudioCapturer:
    """A class to capture audio from a user-selected input device."""

    _BEAT_POLL_SECONDS: float = 0.02  # beat thread wake-up period (two 10 ms model frames)
    _BEAT_STATS_SECONDS: float = 5.0  # period of the beat-thread load log when debug is on

    def __init__(self, chunk_size: int = 1024, sample_rate: int = 48000, spectrum_bins: int = 128,
                 spectrum_history: int = 16, enable_beat_detection: bool = True, enable_emit: bool = False,
                 socketio_url: Optional[str] = None, socketio_namespace: str = "/audio", debug: bool = False,
                 mic: Optional[AudioSource] = None):
        """
        Initializes the AudioCapturer by selecting a device.
        A larger chunk_size (e.g., 1024) is better for frequency resolution of metrics.
        A smaller chunk_size (e.g., 512) is better for low-latency visualization.

        Args:
            chunk_size: Number of audio samples per chunk
            sample_rate: Audio sample rate in Hz, requested from the device (beat detection needs a multiple of 100)
            spectrum_bins: Number of frequency bins for spectrum analysis
            spectrum_history: Number of historical spectrum frames to keep
            enable_beat_detection: Run real-time beat detection (madmom) and fill the beat fields
            debug: Log beat-thread load statistics (frames, processing time, backlog) every few seconds
            mic: Capture source to use instead of prompting for a device (injected in tests)
        """
        self.mic = mic if mic is not None else select_audio_device()
        self.chunk_size = chunk_size
        self.sample_rate = sample_rate
        self.is_recording = False
        # Threaded capture state
        self._stop_event = threading.Event()
        self._ring = np.zeros(self.chunk_size, dtype=np.float32)
        self._ring_lock = threading.Lock()
        self._capture_thread = None
        self._capture_error: Optional[BaseException] = None

        # Spectrum analysis configuration
        self.spectrum_bins = spectrum_bins
        self.spectrum_history = spectrum_history
        self.freq_min = 20.0  # Hz
        self.freq_max = 20000.0  # Hz

        # Pre-compute Hanning window for FFT
        self._window = np.hanning(chunk_size)

        # Pre-compute FFT frequency bins (constant for given chunk_size and sample_rate)
        self._fft_freqs = np.fft.rfftfreq(self.chunk_size, 1.0 / self.sample_rate)

        # Pre-compute logarithmically-spaced frequency bins for spectrum (never changes)
        self._log_freq_bins = np.logspace(
            np.log10(max(self.freq_min, 1.0)),
            np.log10(min(self.freq_max, self.sample_rate / 2)),
            self.spectrum_bins + 1
        )

        # Pre-allocate workspace arrays for spectrum computation
        self._spectrum_workspace = np.zeros(self.spectrum_bins, dtype=np.float32)
        self._fft_freqs_spectrum = np.fft.rfftfreq(self.chunk_size, 1.0 / self.sample_rate)

        # Spectrum history buffer (ring buffer)
        self._spectrum_buffer = np.zeros((spectrum_history, spectrum_bins), dtype=np.float32)
        self._spectrum_index = 0
        self._spectrum_lock = threading.Lock()

        # Smoothing factor for spectrum (attack/decay)
        self._spectrum_smoothing = 0.3
        self._last_spectrum = np.zeros(spectrum_bins, dtype=np.float32)

        # Beat detection: the capture thread feeds samples, a separate thread runs the models
        self.enable_beat_detection = enable_beat_detection
        self.debug = debug
        self._beat_detector: Optional[BeatDetector] = None
        self._beat_predictor: Optional[PredictiveBeatLayer] = None
        self._stream_clock = StreamClock(self.sample_rate)
        self._beat_processing_thread: Optional[threading.Thread] = None
        self._beat_error: Optional[BaseException] = None

        # RMS envelope (dB-scaled with attack/decay smoothing)
        self._rms_db_envelope = 0.0
        self._envelope_attack = 0.7      # Fast attack coefficient (0.7 = ~230ms to 90%)
        self._envelope_release = 0.95    # Release coefficient (0.95 = ~1.5s to 10%)

        # Peak detection (historic RMS analysis)
        self._rms_history = []
        self._rms_history_size = 100     # ~3.3 seconds at 30Hz
        self._peak_threshold_multiplier = 2.0  # Increased from 1.5 for more selective peaks
        self._peak_minimum_rms = 0.1    # Absolute minimum RMS to register peaks (prevents noise)
        self._last_peak_time = 0
        self._peak_cooldown = 0.3        # 300ms minimum between peaks (was 0.1)

        # Onset detection (spectral flux with adaptive thresholding)
        self._last_spectrum_for_onset = None
        self._onset_flux_history = []
        self._onset_flux_history_size = 50      # ~1.7 seconds at 30Hz
        self._onset_threshold_multiplier = 2.5  # Must exceed 2.5x average flux
        self._onset_minimum_flux = 0.3          # Absolute minimum to prevent noise

        # Socket.IO emission (optional)
        self.enable_emit = enable_emit
        self._emitter = None

        if self.enable_emit:
            from cobeart.audiocapture.emitter import AudioEmitter
            self._emitter = AudioEmitter(
                socketio_url=socketio_url,
                namespace=socketio_namespace
            )
            logger.info("Emitter initialized for %s", socketio_namespace)

        if self.enable_beat_detection:
            self._beat_detector = BeatDetector(sample_rate=self.sample_rate)
            self._beat_predictor = PredictiveBeatLayer(self._beat_detector, self._stream_clock)
            logger.info("Beat detection on: %s", self._beat_detector.describe())
        else:
            logger.info("Beat detection off: beat stays false and tempo_bpm null")

    def start_stream(self):
        """Starts the audio recording stream."""
        if self.is_recording:
            logger.warning("Stream is already running.")
            return

        logger.info("Audio stream started at %d Hz.", self.sample_rate)
        # Capture in ~10 ms blocks for stability; maintain a rolling window of chunk_size
        base10 = int(round(self.sample_rate / 100))  # ~10 ms
        capture_frames = max(base10, 240)
        self._stop_event.clear()
        self._capture_error = None
        self._beat_error = None

        def _capture_loop():
            try:
                with self.mic.recorder(
                    samplerate=self.sample_rate, channels=[0], blocksize=capture_frames
                ) as recorder:
                    while not self._stop_event.is_set():
                        data = recorder.record(numframes=capture_frames)
                        if data is None:
                            continue
                        block = data.reshape(-1)
                        self._stream_clock.advance(len(block))
                        with self._ring_lock:
                            n = min(len(block), self.chunk_size)
                            if n < self.chunk_size:
                                self._ring[:-n] = self._ring[n:]
                                self._ring[-n:] = block[:n]
                            else:
                                self._ring[:] = block[-self.chunk_size:]

                        # Every sample goes to the beat detector; this only copies, the models run elsewhere
                        if self._beat_detector is not None:
                            self._beat_detector.add_samples(block)

                        # Compute metrics and push to emitter immediately (if enabled)
                        if self.enable_emit and self._emitter is not None:
                            frame_data = self._ring.copy()  # Get copy for metrics computation
                            payload = self.compute_metrics_payload(frame_data)
                            if payload is not None:
                                self._emitter.push_metrics(payload)
            except Exception as exc:
                logger.exception("Audio capture thread failed")
                self._capture_error = exc

        self._capture_thread = threading.Thread(target=_capture_loop, name="audio-capture", daemon=True)
        self._capture_thread.start()

        if self._beat_detector is not None:
            self._beat_processing_thread = threading.Thread(
                target=self._beat_processing_loop, args=(self._beat_detector,), name="beat-processing", daemon=True)
            self._beat_processing_thread.start()

        self.is_recording = True

    def stop_stream(self):
        """Stops the audio recording stream."""
        if not self.is_recording:
            logger.warning("Stream is not running.")
            return

        self._stop_event.set()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=1.0)
            self._capture_thread = None
        if self._beat_processing_thread is not None:
            self._beat_processing_thread.join(timeout=1.0)
            self._beat_processing_thread = None
        if self._emitter is not None:
            self._emitter.stop()
        self.is_recording = False
        logger.info("Audio stream stopped.")

    def _beat_processing_loop(self, detector: BeatDetector) -> None:
        """Beat thread: analyse pending audio every few ms; a failure is recorded and surfaced via capture_error."""
        busy = 0.0
        frames = 0
        stats_start = time.perf_counter()
        try:
            while not self._stop_event.wait(self._BEAT_POLL_SECONDS):
                started = time.perf_counter()
                frames += detector.process_pending()
                busy += time.perf_counter() - started
                elapsed = time.perf_counter() - stats_start
                if self.debug and elapsed >= self._BEAT_STATS_SECONDS:
                    logger.info(
                        "Beat thread: %d frames in %.1f s, %.2f ms/frame, busy %.1f%%, backlog %.3f s, tempo %s",
                        frames, elapsed, 1000 * busy / max(frames, 1), 100 * busy / elapsed,
                        detector.backlog_seconds, detector.tempo_bpm)
                    busy, frames, stats_start = 0.0, 0, time.perf_counter()
        except Exception as exc:
            logger.exception("Beat processing thread failed")
            self._beat_error = exc

    @property
    def capture_error(self) -> Optional[BaseException]:
        """The exception that terminated the capture or beat-processing thread, or None while both are healthy."""
        return self._capture_error if self._capture_error is not None else self._beat_error

    def read_chunk(self):
        """Reads a chunk of audio data from the stream."""
        if not self.is_recording:
            return None
        with self._ring_lock:
            return self._ring.copy()

    def get_rms(self, data):
        """Calculates the RMS of a chunk of audio data."""
        return np.sqrt(np.mean(data**2))

    def get_peak_amplitude(self, data):
        """Gets the peak amplitude of a chunk of audio data."""
        return np.max(np.abs(data))

    def get_zero_crossing_rate(self, data):
        """Calculates the zero-crossing rate of a chunk of audio data."""
        # Count sign changes; avoid division by zero on empty input
        if len(data) == 0:
            return 0.0
        return float(np.count_nonzero(np.diff(np.signbit(data)))) / float(len(data))

    def get_dominant_frequency(self, data):
        """Calculates the dominant frequency of a chunk of audio data using FFT."""
        n = len(data)
        if n == 0:
            return 0.0
        spectrum = np.fft.rfft(data * self._window)
        magnitudes = np.abs(spectrum)
        if magnitudes.size == 0:
            return 0.0
        peak_index = int(np.argmax(magnitudes))
        return float(self._fft_freqs[peak_index])

    def get_rms_db(self, data, min_db=-60.0, max_db=0.0):
        """
        Get RMS in decibel scale, mapped to [0.0, 1.0].

        Args:
            data: Audio samples
            min_db: Silence threshold in dB (default -60)
            max_db: Maximum level in dB (default 0, full scale)

        Returns:
            Float in range [0.0, 1.0] with perceptually linear scaling
        """
        rms = self.get_rms(data)

        # Convert to dB (20*log10 for amplitude)
        # Add small epsilon to avoid log(0)
        rms_db = 20 * np.log10(max(rms, 1e-10))

        # Map to [0.0, 1.0] range
        normalized = (rms_db - min_db) / (max_db - min_db)
        return float(np.clip(normalized, 0.0, 1.0))

    def get_rms_envelope(self, current_rms_db):
        """
        Get smoothed RMS envelope with fast attack and slow decay.

        This creates a smooth, visually-pleasing envelope that follows
        increases quickly but decays slowly, ideal for audio-reactive visuals.

        Args:
            current_rms_db: Current dB-scaled RMS value [0.0, 1.0]

        Returns:
            Float representing smoothed energy level [0.0, 1.0]
        """
        if current_rms_db > self._rms_db_envelope:
            # Attack: follow increases quickly
            self._rms_db_envelope = (self._envelope_attack * self._rms_db_envelope +
                                     (1.0 - self._envelope_attack) * current_rms_db)
        else:
            # Release: blend slowly toward current value (not multiplicative decay)
            self._rms_db_envelope = (self._envelope_release * self._rms_db_envelope +
                                     (1.0 - self._envelope_release) * current_rms_db)

        return float(self._rms_db_envelope)

    def detect_rms_peak(self, current_rms):
        """
        Detect if current RMS represents a significant peak above recent history.

        Uses a dynamic threshold based on recent RMS average. Peaks are detected
        when current RMS exceeds both the dynamic threshold AND an absolute minimum,
        and enough time has passed since the last peak (cooldown period).

        Args:
            current_rms: Current raw RMS value (not dB-scaled)

        Returns:
            Tuple of (is_peak, peak_intensity)
            - is_peak: Boolean indicating if this is a peak moment
            - peak_intensity: Float indicating how much above threshold (0.0+)
        """
        current_time = time.time()

        # Add to history
        self._rms_history.append(current_rms)
        if len(self._rms_history) > self._rms_history_size:
            self._rms_history.pop(0)

        # Need some history to establish baseline
        if len(self._rms_history) < 10:
            return False, 0.0

        # Calculate dynamic threshold from recent history
        avg_rms = np.mean(self._rms_history)
        threshold = avg_rms * self._peak_threshold_multiplier

        # Check if current RMS exceeds threshold, minimum absolute level, and cooldown
        is_peak = (current_rms > threshold and
                   current_rms > self._peak_minimum_rms and
                   current_time - self._last_peak_time > self._peak_cooldown)

        if is_peak:
            self._last_peak_time = current_time

        # Calculate intensity (how much above threshold)
        peak_intensity = max(0.0, (current_rms - threshold) / (threshold + 1e-6))

        return is_peak, float(peak_intensity)

    def detect_onset(self, data):
        """
        Detect audio onsets using adaptive spectral flux analysis.

        Onsets are detected by analyzing sudden increases in spectral energy
        relative to recent history. This adaptive approach works across different
        dynamics levels in music.

        Args:
            data: Audio samples (numpy array)

        Returns:
            Tuple of (is_onset, onset_strength)
            - is_onset: Boolean indicating if an onset was detected
            - onset_strength: Float indicating onset magnitude relative to threshold
        """
        # Get current spectrum and update history buffer
        current_spectrum = self.get_spectrum(data, update_history=True)

        # Need previous spectrum for comparison
        if self._last_spectrum_for_onset is None:
            self._last_spectrum_for_onset = current_spectrum
            return False, 0.0

        # Calculate spectral flux (half-wave rectified)
        # Only count increases in energy (positive differences)
        flux = np.sum(np.maximum(0, current_spectrum - self._last_spectrum_for_onset))

        # Update stored spectrum for next comparison
        self._last_spectrum_for_onset = current_spectrum.copy()

        # Add to history for adaptive threshold
        self._onset_flux_history.append(flux)
        if len(self._onset_flux_history) > self._onset_flux_history_size:
            self._onset_flux_history.pop(0)

        # Need some history to establish baseline
        if len(self._onset_flux_history) < 10:
            return False, 0.0

        # Calculate dynamic threshold from recent history
        avg_flux = np.mean(self._onset_flux_history)
        threshold = avg_flux * self._onset_threshold_multiplier

        # Detect onset: must exceed both dynamic threshold AND absolute minimum
        is_onset = (flux > threshold and flux > self._onset_minimum_flux)

        # Calculate strength relative to threshold
        onset_strength = min(flux / max(threshold, 0.1), 5.0)  # Cap at 5x

        return is_onset, float(onset_strength)

    def get_spectrum(self, data, update_history=True):
        """
        Compute FFT spectrum with logarithmically-spaced frequency bins.

        Args:
            data: Audio samples (numpy array)
            update_history: If True, adds this spectrum to history buffer

        Returns:
            numpy array of shape (spectrum_bins,) with normalized magnitudes [0.0, 1.0]
        """
        n = len(data)
        if n == 0:
            return np.zeros(self.spectrum_bins, dtype=np.float32)

        # Apply window and compute FFT
        windowed = data * self._window
        fft_result = np.fft.rfft(windowed)
        fft_magnitudes = np.abs(fft_result)

        # Use pre-allocated workspace
        spectrum = self._spectrum_workspace
        spectrum.fill(0.0)

        # Map FFT bins to logarithmic bins using binary search (faster than boolean masks)
        for i in range(self.spectrum_bins):
            freq_low = self._log_freq_bins[i]
            freq_high = self._log_freq_bins[i + 1]

            # Use searchsorted for O(log n) binary search instead of O(n) boolean mask
            idx_low = np.searchsorted(self._fft_freqs_spectrum, freq_low, side='left')
            idx_high = np.searchsorted(self._fft_freqs_spectrum, freq_high, side='right')

            if idx_high > idx_low:
                # Direct slice instead of boolean mask (faster, no allocation)
                spectrum[i] = np.mean(fft_magnitudes[idx_low:idx_high])

        # Normalize using log scale for better visual range (in-place operations)
        # Add small epsilon to avoid log(0)
        np.log10(spectrum + 1e-10, out=spectrum)
        # Map to [0, 1] range (assuming typical audio range)
        spectrum += 10.0
        spectrum /= 10.0
        np.clip(spectrum, 0.0, 1.0, out=spectrum)

        # Apply temporal smoothing (attack/decay) - in-place
        alpha = self._spectrum_smoothing
        spectrum *= alpha
        spectrum += (1.0 - alpha) * self._last_spectrum
        self._last_spectrum[:] = spectrum  # In-place copy

        # Update history buffer
        if update_history:
            with self._spectrum_lock:
                self._spectrum_buffer[self._spectrum_index] = spectrum.copy()
                self._spectrum_index = (self._spectrum_index + 1) % self.spectrum_history

        # Return copy since we reuse workspace
        return spectrum.copy()

    def get_spectrum_2d(self):
        """
        Get the 2D spectrum history buffer for use as a shader texture.

        Returns:
            numpy array of shape (spectrum_history, spectrum_bins) with values [0.0, 1.0]
            Rows are ordered from oldest (index 0) to newest (index -1)
        """
        with self._spectrum_lock:
            # Reorder buffer so oldest is first, newest is last
            # This creates the correct orientation for texture sampling
            buffer_copy = np.zeros_like(self._spectrum_buffer)
            for i in range(self.spectrum_history):
                src_idx = (self._spectrum_index + i) % self.spectrum_history
                buffer_copy[i] = self._spectrum_buffer[src_idx]
            return buffer_copy

    def has_beat(self):
        """
        Check for predicted beat and get current tempo.

        Uses predictive beat layer to provide low-latency beat detection by predicting
        future beats ahead of the detector. This method should be called at high frequency
        (e.g., every audio frame at ~50-100 Hz) in the consumer's main loop.

        The predictor internally polls the beat detector and generates predicted beat
        timestamps when tempo is stable. Returns True when a predicted beat should trigger.

        Returns:
            Tuple of (beat_detected, tempo_bpm, beat_timestamp)
            - beat_detected: True when current time has reached a predicted beat
            - tempo_bpm: Current tempo estimate (None if not yet determined)
            - beat_timestamp: Predicted beat timestamp when beat_detected=True (None otherwise)
        """
        if self._beat_predictor is None:
            return False, None, None

        return self._beat_predictor.get_next_beat()

    def compute_metrics_payload(self, data):
        """
        Compute all audio metrics from a data chunk.

        Args:
            data: Audio samples (numpy array)

        Returns:
            Dictionary with all computed metrics, or None if data is invalid
        """
        if data is None or data.size == 0:
            return None

        # Compute all metrics from this frame
        rms = self.get_rms(data)
        rms_db = self.get_rms_db(data)
        is_peak, peak_intensity = self.detect_rms_peak(rms)
        is_onset, onset_strength = self.detect_onset(data)
        beat, tempo_bpm, beat_timestamp = self.has_beat()

        # Note that is_peak, is_onset, and beat are explicitly cast to bools to ensure they are JSON serializable.
        return {
            "schemaVersion": AUDIO_SCHEMA_VERSION,
            "rms": float(rms),
            "peak": float(self.get_peak_amplitude(data)),
            "zcr": float(self.get_zero_crossing_rate(data)),
            "dominant_frequency": float(self.get_dominant_frequency(data)),
            "rms_db": float(rms_db),
            "rms_envelope": float(self.get_rms_envelope(rms_db)),
            "is_peak": bool(is_peak),
            "peak_intensity": float(peak_intensity),
            "is_onset": bool(is_onset),
            "onset_strength": float(onset_strength),
            "spectrum_2d": self.get_spectrum_2d().tolist(),
            "spectrum_config": {
                "width": self.spectrum_bins,
                "height": self.spectrum_history,
                "freq_min": self.freq_min,
                "freq_max": self.freq_max,
            },
            "beat": bool(beat),
            "tempo_bpm": tempo_bpm,
            "beat_timestamp": beat_timestamp,
        }


def meter_line(capturer: AudioCapturer, audio_data: np.ndarray, show_beat: bool) -> str:
    """One console metering line; with `show_beat` it calls `has_beat()`, which consumes the beat."""
    rms = capturer.get_rms(audio_data)
    peak = capturer.get_peak_amplitude(audio_data)
    zcr = capturer.get_zero_crossing_rate(audio_data)
    dom_freq = capturer.get_dominant_frequency(audio_data)
    if not show_beat:
        return f"RMS: {rms:.4f} | Peak: {peak:.4f} | ZCR: {zcr:.4f} | Dominant Freq: {dom_freq:.2f} Hz  "
    beat, tempo_bpm, beat_timestamp = capturer.has_beat()
    beat_indicator = "BEAT" if beat else "    "
    tempo_str = f"{tempo_bpm:.1f} BPM" if tempo_bpm is not None else "--- BPM"
    timestamp_str = f"@ {beat_timestamp:.3f}s" if beat and beat_timestamp else ""
    return (f"RMS: {rms:.4f} | Peak: {peak:.4f} | ZCR: {zcr:.4f} | "
            f"Freq: {dom_freq:.0f} Hz | {beat_indicator} {timestamp_str} | {tempo_str}  ")


def main() -> None:
    """Capture audio, meter it on the console, and emit metrics to the hub (config from config/cobeart.yaml)."""
    import argparse
    from cobeart.audiocapture.utils import NoAudioDeviceError
    from cobeart.settings.config import ConfigError, load_settings

    parser = argparse.ArgumentParser(
        description="Capture audio and emit its metrics to the CoBeART hub (settings come from config/cobeart.yaml)"
    )
    parser.add_argument("--no-emit", action="store_true", help="meter locally without sending to the hub")
    parser.add_argument("--debug", action="store_true", help="enable debug logging")
    parser.add_argument("--device", type=int, default=None, metavar="INDEX", help="input device index (else prompt)")
    args = parser.parse_args()
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    debug = args.debug or settings.debug
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)

    try:
        mic = select_audio_device(args.device)
    except NoAudioDeviceError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    beat_detection = settings.audio.beat_detection
    namespace = "/audio"
    capturer = AudioCapturer(
        chunk_size=settings.audio.chunk_size,
        sample_rate=settings.audio.sample_rate,
        enable_beat_detection=beat_detection,
        enable_emit=not args.no_emit,
        socketio_url=settings.hub_url,
        socketio_namespace=namespace,
        debug=debug,
        mic=mic,
    )
    capturer.start_stream()

    logger.info("Beat detection %s", "enabled" if beat_detection else "disabled")
    if args.no_emit:
        logger.info("Emission disabled (--no-emit)")
    else:
        logger.info("Emitting audio metrics to %s%s", settings.hub_url, namespace)
    logger.info("Reading audio metrics... Press Ctrl+C to stop.")

    try:
        while True:
            if capturer.capture_error is not None:
                logger.error("Audio capture thread died: %r", capturer.capture_error)
                sys.exit(1)
            audio_data = capturer.read_chunk()
            if audio_data is not None and audio_data.size > 0:
                # has_beat() consumes each beat, so only the display may call it when nothing is emitting.
                print(meter_line(capturer, audio_data, show_beat=beat_detection and args.no_emit), end='\r')

            # Sleep for a duration that is close to the chunk's duration
            # This prevents a busy-wait loop from consuming 100% CPU.
            # Chunk duration = chunk_size / sample_rate = 1024 / 48000 ~= 0.021s
            time.sleep(0.02)
    except KeyboardInterrupt:
        # Print a newline to move off the updating line of metrics
        print("\nStopping metric capture.")
    finally:
        capturer.stop_stream()


if __name__ == "__main__":
    main()
