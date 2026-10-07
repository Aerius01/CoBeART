#!/usr/bin/env python3
"""Watch live beats and tempo from AudioCapturer: one line per beat, plus a tempo line when it changes."""
import argparse
import logging
import time
from typing import Optional

from cobeart.audiocapture.capture import AudioCapturer
from cobeart.audiocapture.utils import select_audio_device

logger: logging.Logger = logging.getLogger(__name__)
POLL_SECONDS: float = 0.005


def watch_beats(capturer: AudioCapturer, seconds: Optional[float]) -> int:
    """Log beats until `seconds` elapse (forever if None) or Ctrl+C; returns the number of beats seen."""
    start = time.time()
    beats = 0
    last_tempo: Optional[float] = None
    while seconds is None or time.time() - start < seconds:
        if capturer.capture_error is not None:
            raise RuntimeError(f"Audio capture failed: {capturer.capture_error!r}") from capturer.capture_error
        beat, tempo, beat_timestamp = capturer.has_beat()
        rounded = None if tempo is None else round(tempo, 1)
        if rounded != last_tempo:
            logger.info("Tempo: %s", "not stable" if rounded is None else f"{rounded} BPM")
            last_tempo = rounded
        if beat and beat_timestamp is not None:
            beats += 1
            logger.info("BEAT %4d  t=%.3f  late %5.1f ms  %.1f BPM",
                        beats, beat_timestamp - start, 1000 * (time.time() - beat_timestamp), tempo)
        time.sleep(POLL_SECONDS)
    return beats


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch live beats and tempo from an audio input")
    parser.add_argument("--device", type=int, default=None, help="input device index (prompts when omitted)")
    parser.add_argument("--seconds", type=float, default=None, help="stop after this many seconds")
    parser.add_argument("--sample-rate", type=int, default=48000, help="capture sample rate in Hz")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    capturer = AudioCapturer(sample_rate=args.sample_rate, enable_beat_detection=True,
                             mic=select_audio_device(args.device))
    capturer.start_stream()
    try:
        beats = watch_beats(capturer, args.seconds)
        logger.info("Stopped after %d beats", beats)
    except KeyboardInterrupt:
        logger.info("Stopped")
    finally:
        capturer.stop_stream()


if __name__ == "__main__":
    main()
