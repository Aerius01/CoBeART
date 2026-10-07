"""CLI: python -m cobeart.simulator --scenario hands --audio beat --rate 240 --duration 30 --seed 0"""
import argparse
import logging
import time
from collections.abc import Sequence
from dataclasses import asdict

import socketio

from cobeart.settings.config import ConfigError, Settings, load_settings, validate_hub_url
from cobeart.simulator.audio import SIGNALS, AudioConfig, TempoChange, build_audio_scenario, parse_tempo_change
from cobeart.simulator.emitter import RunConfig, Simulator
from cobeart.simulator.faults import FAULTS
from cobeart.simulator.scenarios import MOTION_SCENARIOS, MotionConfig, build_motion_scenario, load_body_map

logger: logging.Logger = logging.getLogger("cobeart.simulator")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cobeart.simulator",
        description="Emit simulated OptiTrack frames (/ingest) and audio metrics (/audio) to a CoBeART hub.",
    )
    parser.add_argument(
        "--scenario", choices=[*sorted(MOTION_SCENARIOS), "none"], default="orbit", help="motion scenario"
    )
    parser.add_argument("--audio", choices=[*sorted(SIGNALS), "none"], default="silence", help="audio scenario")
    parser.add_argument("--rate", type=float, default=None, help="motion frame rate in Hz (default: package_framerate)")
    parser.add_argument("--audio-rate", type=float, default=100.0, help="audio message rate in Hz")
    parser.add_argument("--duration", type=float, default=None, help="seconds to run (default: until interrupted)")
    parser.add_argument("--seed", type=int, default=0, help="RNG seed; the same seed gives the same stream")
    parser.add_argument("--url", default=None, help="hub base URL (default: from config or $COBEART_SOCKETIO_URL)")
    parser.add_argument("--bodies", type=int, default=4, help="number of bodies for orbit, still, shuffled, edge")
    parser.add_argument("--bpm", type=float, default=120.0, help="tempo of the beat scenario")
    parser.add_argument(
        "--beat-jitter", type=float, default=0.0, metavar="MS", help="std dev of beat time jitter in ms"
    )
    parser.add_argument(
        "--tempo-change", action="append", default=[], metavar="BPM@SECONDS", help="tempo change (repeatable)"
    )
    parser.add_argument("--tone-hz", type=float, default=440.0, help="dominant frequency of the tone scenario")
    parser.add_argument("--fault", choices=sorted(FAULTS), default=None, help="corrupt every message")
    parser.add_argument("--audio-stop-after", type=float, default=None, metavar="SECONDS", help="stop audio messages")
    parser.add_argument("--motion-stop-after", type=float, default=None, metavar="SECONDS", help="stop frames")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args: argparse.Namespace = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings: Settings = load_settings()
        url: str = settings.hub_url if args.url is None else validate_hub_url(args.url)
    except ConfigError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc

    now: float = time.time()
    rate_hz: float = settings.tracking.package_framerate if args.rate is None else args.rate
    motion_cfg: MotionConfig = MotionConfig.from_settings(
        settings, load_body_map(), rate_hz=rate_hz, num_bodies=args.bodies, start_epoch_ms=now * 1000.0
    )
    tempo_changes: tuple[TempoChange, ...] = tuple(parse_tempo_change(text) for text in args.tempo_change)
    audio_cfg: AudioConfig = AudioConfig(
        sample_rate=settings.audio.sample_rate, chunk_size=settings.audio.chunk_size, rate_hz=args.audio_rate,
        tone_hz=args.tone_hz, bpm=args.bpm, beat_jitter_ms=args.beat_jitter, tempo_changes=tempo_changes,
        seed=args.seed, start_epoch_s=now,
    )
    run_cfg: RunConfig = RunConfig(
        url=url, motion_rate_hz=rate_hz, audio_rate_hz=args.audio_rate, seed=args.seed,
        duration_s=args.duration, fault=args.fault, motion_stop_after_s=args.motion_stop_after,
        audio_stop_after_s=args.audio_stop_after,
    )
    logger.info("scenario=%s audio=%s", args.scenario, args.audio)
    logger.info("run=%s", asdict(run_cfg))
    logger.info("motion=%s", asdict(motion_cfg))
    logger.info("audio=%s", asdict(audio_cfg))

    simulator: Simulator = Simulator(
        client=socketio.Client(),
        motion=None if args.scenario == "none" else build_motion_scenario(args.scenario, motion_cfg),
        audio=None if args.audio == "none" else build_audio_scenario(args.audio, audio_cfg),
        run=run_cfg,
    )
    try:
        simulator.run()
    except KeyboardInterrupt:
        logger.info("Interrupted")


if __name__ == "__main__":
    main()
