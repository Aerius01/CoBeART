# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project Overview

CoBeART is an audio-visual art project using Spatial Augmented Reality. OptiTrack body tracking and microphone audio are processed in Python and streamed to a Node.js/Electron hub, which feeds WebGL visualizations. Linux is the primary platform.

## Status

The architecture refactor is complete. `ROADMAP.md` is the backlog (the "Deferred" section) and the decision log (D1-D9 and the per-phase decisions); `ARCHITECTURE_REVIEW.md` is the historical review it came from. Read the relevant decision before reversing a design choice.

Source of truth when prose disagrees: the code, `contract/` (message schemas) and `config/cobeart.yaml` (settings).

## Architecture

```
OptiTrack/Motive -> NatNetClient -> FramePipeline -> PayloadSender --(/ingest, frame)--> hub
Microphone -> AudioCapturer -> AudioEmitter ----------(/audio, audio_metrics)---------> hub
hub merges, validates, broadcasts --(/viewer, frame)--> fluid-bridge.js --postMessage--> simulation iframes
```

### Python (`cobeart/`)

- `settings/config.py`: loads `config/cobeart.yaml` into a frozen `Settings` tree (`load_settings()`). Raises `ConfigError` naming the bad key.
- `optitrackclient/`: `start_client.py` (entry point, `FramePipeline`, `SyntheticNatNetSource` for `--simulate`), `NatNetClient.py` plus `DataDescriptions.py`/`MoCapData.py` (vendored NatNet SDK, excluded from flake8), `transform.py` (OptiTrack to arena axes), `tracked_bodies.py`.
- `packagesender/`: `sender.py` (`PayloadSender`, Socket.IO `/ingest` client), `metrics.py` (`MetricsTracker`: velocities and angular velocity from successive poses, windowed smoothing of normalized speed, dropout handling).
- `audiocapture/`: `capture.py` (`AudioCapturer`: threaded `soundcard` capture, RMS, peak, ZCR, dominant frequency, spectrum, onsets), `emitter.py` (`AudioEmitter`, `/audio` client), `utils.py` (device selection), `beat/` (beat and tempo detection), `viz/` and `tools/` (developer utilities).
- `simulator/`: offline stand-in for both producers (motion scenarios, audio scenarios, fault injection), talks to the hub over Socket.IO.

Beat detection runs madmom online in a spawned child process (`beat/process.py`, with `tracker.py`, `tempo.py`, `predictor.py`, `clock.py`) so it cannot stall capture; tempo range 55-215 BPM. `AudioCapturer.has_beat()` consumes each beat, so only one caller may use it. Any program that starts `AudioCapturer` must do so under `if __name__ == "__main__":`.

### Hub (`cobeart-app/`)

- `electron/hub.js`: `createHub(options)` runs Express and Socket.IO. Used by both `electron/main.js` (Electron window) and `server.js` (`npm run web`, same hub without a window). Nothing else may construct a Socket.IO `Server`.
- Namespaces: `/ingest` (motion frames from Python; each valid frame triggers one emission), `/audio` (audio metrics; stored as background state), `/viewer` (merged frames; a new viewer first gets the stored last frame).
- Every incoming payload is validated against `contract/*.schema.json` (ajv). Invalid payloads are dropped with a rate-limited log line. Frames with more than `tracking.max_num_objects` bodies or duplicate `ID`s are rejected.
- Stale-audio policy: audio is merged into a frame as `frame.audio` only if it is at most `hub.audio_max_age_ms` old; otherwise the frame carries no `audio`.
- HTTP: `/health` (client counts, last frame and audio age in ms), `/config.json`, and static files from `public/`. Every served HTML page gets `window.__COBEART_CONFIG__` injected as its first script (the frontend subset of the YAML: `debug`, `arena`, `bridge_framerate`, `splat_color`, `composite`).
- `electron/config.js`: loads and validates the YAML with `config/cobeart.schema.json`; `server.js` and `main.js` exit on an invalid file. The hub binds `network.socketio.host`.
- `electron/main.js`: asks which visualization to show (Splat, Molten, Ink, Mixed; Performance Mode is Molten only), then loads that page from the hub. DevTools open when `debug: true` in the YAML (never for Ink).

### Contract and config

- `contract/*.schema.json` define `frame`, `audio_metrics` and `splat`; `contract/CONTRACT.md` says who sends what and how to change it. All objects are closed and share `schemaVersion: 1`, so any field change is breaking and bumps both schemas. Read the schemas for field names and units; do not restate them elsewhere.
- Orientation is a unit quaternion `qx, qy, qz, qw` in arena axes plus angular velocity `wx, wy, wz` (deg/s), never Euler angles (D5). Bodies are identified by `ID` (0-based), never by array position. Body names map to IDs in `public/body_map.json`.
- `config/cobeart.yaml` is the single settings file (network and OptiTrack addresses, tracking, arena in mm, metrics, hub, window, frontend, debug, audio). Python reads it through `cobeart/settings/config.py`, Node through `electron/config.js`. Arena bounds and the port live only there.
- Environment variables: `COBEART_CONFIG` (path of an alternative YAML, Python and Node), `PORT` (overrides the hub port, Node), `COBEART_SOCKETIO_URL` (overrides the hub URL Python clients and the simulator use).

### Frontend (`cobeart-app/public/`)

- `fluid-bridge.js`: connects to `/viewer`, keeps the latest frame, and every `1000 / bridge_framerate` ms posts one `{type: 'splat', ...}` message per rigid body to the iframes and the window. It posts `{type: 'audio', audio}` only when the audio timestamp changes. Only the fluid simulation consumes `audio` messages so far.
- Simulations (each a page of its own, embedded as an iframe): `fluid/` (Splat, WebGL2 Navier-Stokes), `molten/` (reflective shader, `?performance=true` option), `ink/` (dark fluid with contours), `composite/` (Mixed: runs all three offscreen and blends between them with a spatial transition triggered by raised hands or a clap; thresholds in `frontend.composite`).
- Debug logging in the frontend is gated by `?debug` in the URL or `debug: true` in the YAML.

### Background shaders

`public/backgrounds/registry.js` is the only background model: the fluid simulation draws one registry entry behind the dye (off by default; the GUI's "show background" toggle and "background" list pick it). An entry is `{ name, fragmentShader, setUniforms(gl, program, ctx) }`. The fragment shader is compiled against the sim's base vertex shader (varying `vUv`); `setUniforms` sets every uniform the shader uses with the program already bound; `ctx` is `{ time, resolution, audio, rigidbodies }` (types documented in the registry). Current entries: Electric Clouds, Circles, Zephyr (each in `backgrounds/<name>/shader.js`, some with `fragment_shader.txt`/`vertex_shader.txt`). To add one, create `backgrounds/<name>/shader.js` exporting that object and list it in the registry.

## Commands

### Python environment

Conda environment `splat-env` (Python 3.12). Console scripts (`start-*`) need `conda activate splat-env`. Otherwise call the interpreter directly; never use `conda run`, which masks output:

```bash
~/miniconda3/envs/splat-env/bin/python -m pytest
```

Install (builds madmom from a pinned commit):
```bash
~/miniconda3/envs/splat-env/bin/python -m pip install -e ".[test,viz]"
```

Known traps:
- In a git worktree, run code with `python -m ...` from the worktree root. The installed `start-*` scripts always import the main checkout. A worktree also needs `npm ci` in `cobeart-app/` or the hub tests skip.
- `soundcard` crashes under `python -c`. Put ad-hoc Python in a script file. `python -m cobeart.audiocapture.capture` works with or without flags.
- `soundcard` talks to PulseAudio (or PipeWire's Pulse server) on Linux.

### Running the system

```bash
cd cobeart-app && npm ci          # once
cd cobeart-app && npm start       # Electron window + hub (port from the YAML, 3000)
cd cobeart-app && npm run web     # hub only, for a browser at http://127.0.0.1:3000
start-optitrack-client            # NatNet to hub; needs Motive streaming (addresses from the YAML or positional args)
start-optitrack-client --simulate # scripted synthetic bodies through the real pipeline, no hardware
start-audio-client                # capture, meter and emit to the hub by default
```

- `start-audio-client` flags: `--no-emit` (meter only, no hub), `--device INDEX` (skip the device prompt), `--debug`. Beat detection follows `audio.beat_detection` in the YAML.
- `start-optitrack-client [server_address [client_address [transport]]] [--simulate] [--url URL] [--log-level L]`.
- Viewers only receive frames when a motion source is running (frames drive emission), so audio checks need `start-optitrack-client --simulate` or `start-simulator` running too.
- Use `PORT=<port> npm run web` for a second hub; pass the matching `COBEART_SOCKETIO_URL` or `--url` to Python tools.

### Simulator (no hardware)

The hub must be running first (`npm run web` or `npm start`). With no hub, `start-simulator` logs one line and exits 1.

```bash
start-simulator --scenario hands --audio beat --duration 30
```

- `--scenario`: `orbit` (default), `hands`, `still`, `missing-body`, `shuffled`, `edge`, `none`. `--audio`: `silence` (default), `tone`, `beat`, `sweep`, `none`.
- `--fault {malformed,wrong-version,nan}` corrupts every message (the hub must reject it); `--audio-stop-after S` and `--motion-stop-after S` test the stale-audio and no-frame paths; `--bpm`, `--tempo-change BPM@S`, `--beat-jitter MS`, `--tone-hz`, `--bodies`, `--rate`, `--audio-rate`, `--seed`, `--url`, `--log-level`. See `--help`.
- Use `--audio none` when a real microphone is also feeding the hub.
- No scenario raises a hand above `z_threshold`, so composite hand-up transitions need a custom driver.

### Tests and checks

```bash
~/miniconda3/envs/splat-env/bin/python -m pytest        # repo root: cobeart/ unit tests and tests/e2e/ cross-process tests
~/miniconda3/envs/splat-env/bin/python -m flake8 cobeart
cd cobeart-app && npm test                              # node --test test/**/*.test.js (config and hub smoke tests)
node scripts/fitness.mjs                                # architecture fitness checks, from the repo root
node --test scripts/fitness.test.mjs
```

CI (`.github/workflows/ci.yml`) runs pytest, flake8, `npm test` and the fitness checks on every push. Cap long local test runs with `timeout`. Python tests live next to each package under `tests/`; hub tests start a real `node server.js`.

## Conventions

- Python: `logging.getLogger(__name__)`, no `print` in `packagesender/` or `transform.py`, per-frame output only at `DEBUG`. flake8 max line 120 (`.flake8`).
- JS: per-frame `console.log` only behind the `DEBUG` flag described above.
- Orientation, units and field names come from `contract/`; do not hardcode the arena or port, read them from the config.

## Testing and validation

### Offline workflow (default)

1. `cd cobeart-app && PORT=3400 npm run web` (or `npm start`, port 3000).
2. `COBEART_SOCKETIO_URL=http://127.0.0.1:3400 start-simulator --scenario orbit --audio beat`, or `start-optitrack-client --simulate` for the full Python pipeline.
3. Open `http://127.0.0.1:3400/` (Splat), `/molten/`, `/ink/` or `/composite/` in a browser; add `?debug` for the overlay and logs. In Splat, only hands, feet and tracked objects paint (`trackedBodyParts` from `body_map.json`); `--simulate` sends IDs 0-2.
4. `curl http://127.0.0.1:3400/health` shows connected clients and the age of the last frame and audio.

A real microphone can be checked live with `start-audio-client` against the same hub.

### On-site validation (needs OptiTrack and Motive)

1. Start Motive with streaming enabled; set `network.client_address`, `server_address` and `use_multicast` in the YAML.
2. `start-optitrack-client` should print the connection info; `start-audio-client` should show audio metrics.
3. `npm start` from `cobeart-app/`, choose a visualization.
4. Rigid bodies appear as splats or particles; the debug overlay (`debug: true` or `?debug`) shows position and velocity.
5. Tuning checklist (details in `ROADMAP.md`, Deferred): each rigid body created in Motive aligned to the global axes with the performer facing OptiTrack +Z (D5 orientation and lean sign); Motive Streaming IDs versus the 0-based IDs in `body_map.json`; `frontend.composite.z_threshold` is 2700 mm, above the 2500 mm arena ceiling, so check that a raised hand can trigger a hand-up transition; head-tilt lean clamp in `fluid/script.js`; dropout gap `max_gap_s` in `MetricsTracker`; NatNet multicast on the Linux machine; end-to-end latency with real bodies.

## Development Notes

- Electron runs with `contextIsolation: true`, `nodeIntegration: false` and background throttling disabled; all rendering happens in the sandboxed renderer.
- After `npm ci` in the main checkout, Electron's `chrome-sandbox` needs `sudo chown root:root` and `sudo chmod 4755` on `cobeart-app/node_modules/electron/dist/chrome-sandbox` (Ubuntu 24.04). `npm install` for an added dependency keeps it.
- OptiTrack needs Motive running; the NatNet client is vendored, no SDK install is required to run the Python code.
- Git: `main` is primary; work on a feature branch and fast-forward merge.
