# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Project Overview

CoBeART is an audio-visual art project using Spatial Augmented Reality. Three independent modules meet at a Node.js/Electron hub: **input sources** (OptiTrack body tracking on site; the mouse or a simulator at home), **audio** (microphone analysis in Python) and **worlds** (WebGL visualizations). Every world receives the same contract and cannot tell which source produced it. One world runs at a time. Linux is the primary platform.

## Status

The original refactor (Phases 0-4) and the foundation phases (5-7: module separation, contract v2, swappable sources, Splat only) are done. `ROADMAP.md` is the backlog (the "Deferred" section) and the decision log (D1-D22 and the per-phase decisions); `ARCHITECTURE_REVIEW.md` is the historical review it came from. Read the relevant decision before reversing a design choice.

Source of truth when prose disagrees: the code, `contract/` (message schemas) and `config/cobeart.yaml` (settings).

## Architecture

```
OptiTrack/Motive -> NatNetClient -> FramePipeline -> PayloadSender --+
start-simulator (scenarios, faults) ---------------------------------+--(/ingest, pose_frame)--> hub
mouse layer on the host page (public/sources/mouse.js) --------------+
Microphone -> AudioCapturer -> AudioEmitter ------------------(/audio, audio_metrics)---------> hub
hub: validates, derives velocities per role (motion.js) --(/viewer, frame | audio)--> host/bridge.js
bridge --postMessage {type:'frame'} / {type:'audio'}--> world iframe (public/worlds/<name>/)
```

### Python (`cobeart/`)

Modules never import each other across these boundaries: `cobeart.audio` and `cobeart.input` are independent, and `cobeart.simulator` imports neither (enforced by the `module-boundaries` fitness rule). Shared code lives in `cobeart/common/` (`strict_json.py`: the Socket.IO JSON codec that rejects NaN).

- `settings/config.py`: loads `config/cobeart.yaml` into a frozen `Settings` tree (`load_settings()`). Raises `ConfigError` naming the bad key. Role names are read from the contract (`contract_roles()`).
- `input/optitrack/`: `start_client.py` (entry point, `FramePipeline`, `SyntheticNatNetSource` for `--simulate`), `sender.py` (`PayloadSender`, emits `pose_frame` on `/ingest`), `transform.py` (OptiTrack to arena axes), `tracked_bodies.py`, plus the vendored NatNet SDK (`NatNetClient.py`, `DataDescriptions.py`, `MoCapData.py`; excluded from flake8). `FramePipeline` maps Motive streaming IDs to roles with `tracking.roles`; unmapped IDs are dropped with one warning each.
- `audio/`: `capture.py` (`AudioCapturer`: threaded `soundcard` capture, RMS, peak, ZCR, dominant frequency, spectrum, onsets), `emitter.py` (`AudioEmitter`, `/audio` client), `utils.py` (device selection), `beat/` (beat and tempo detection), `viz/` and `tools/` (developer utilities).
- `simulator/`: offline stand-in for both producers (motion scenarios, audio scenarios, fault injection), talks to the hub over Socket.IO.

Velocities are not computed in Python: sources send poses only, and the hub derives everything else.

Beat detection runs madmom online in a spawned child process (`beat/process.py`, with `tracker.py`, `tempo.py`, `predictor.py`, `clock.py`) so it cannot stall capture; tempo range 55-215 BPM. `AudioCapturer.has_beat()` consumes each beat, so only one caller may use it. Any program that starts `AudioCapturer` must do so under `if __name__ == "__main__":`.

### Hub (`cobeart-app/electron/`)

- `hub.js`: `createHub({port, host, publicDir, metrics, frontendConfig, now})` runs Express and Socket.IO. Used by both `main.js` (Electron window) and `server.js` (`npm run web`, same hub without a window). Nothing else may construct a Socket.IO `Server`.
- Namespaces:
  - `/ingest`, event `pose_frame`: each valid pose_frame produces exactly one `frame` on `/viewer`. The v1 event `frame` is rejected with a log line naming the fix. One source drives the hub at a time (D20): the connection whose pose_frame was accepted last stays active while connected and sending within `metrics.max_gap_s`; other connections' frames are dropped with a log line, and a change of source resets the motion state.
  - `/audio`, event `audio_metrics`: each valid message is re-emitted on `/viewer` as `audio`, stamped with the hub `timestamp`.
  - `/viewer`, events `frame` and `audio`: two independent streams. Audio is never merged into frames, and a new viewer gets nothing until the next message (no replay).
- `motion.js`: the velocity stage. Per role it derives `vx..vz`, `wx..wz` (from successive quaternions), `abs_vel` and `norm_abs_vel` (windowed hold) from the source clock `source_time_s`, not receipt time. A role restarts at 0 on first sighting or after a gap above `metrics.max_gap_s`; a source clock going backwards resets every role.
- Every payload is validated with ajv against `contract/*.schema.json`; invalid ones are dropped with a rate-limited log line. An outgoing frame that does not validate (e.g. a non-finite velocity) is dropped too.
- HTTP:
  - `/health`: client counts, the active source's socket id (or null), and the last frame and audio age in ms.
  - `/config.json`.
  - `/worlds.json`: the subfolders of `public/worlds/` that contain an `index.html`, rescanned on every request.
  - Static files from `public/`. Every served HTML page gets `window.__COBEART_CONFIG__` injected as its first script: the frontend subset of the YAML (`debug`, `arena`, `bridge_framerate`, `audio_max_age_ms`, `sources`).
- `config.js`: loads and validates the YAML with `config/cobeart.schema.json`, whose role values reference the contract's role enum. Exports `loadConfig`, `resolvePort`, `trackingRoles`, `hubConfig` and `frontendConfig`. `server.js` and `main.js` exit on an invalid file. The hub binds `network.socketio.host`.
- `main.js`: opens the window on the launcher page `/` (no native dialog, D21); the page navigates the same window to `/host/?world=<name>&source=<live|mouse>`. DevTools open when `debug: true` in the YAML.

### Contract and config

- `contract/` holds one contract per side of the hub:
  - Sources send `pose_frame.schema.json`.
  - Worlds receive `frame.schema.json` and `audio.schema.json`, the same objects on `/viewer` and in the bridge's postMessages.
  - The audio producer sends `audio_metrics.schema.json`.
  - All are closed objects with `schemaVersion: 2`, so any field change is breaking and bumps all of them.
  - `contract/CONTRACT.md` says who sends what and how to change the contract. Read the schemas for field names and units; do not restate them elsewhere.
- Bodies are an object keyed by **role**: `left_hand`, `right_hand`, `head`, `chest_front`, `chest_back`, `left_foot`, `right_foot`, `stick`. The enum lives only in `frame.schema.json` `$defs/role`, and adding one is a version bump. There are no numeric IDs in the contract. A missing role means that body is not tracked in that frame.
- Positions are integer mm in arena axes. Orientation is a unit quaternion `qx, qy, qz, qw` plus angular velocity `wx, wy, wz` (deg/s), never Euler angles (D5).
- Canonical top-down view (D18): 2D worlds draw arena `x_max` at the left edge and `y_min` at the top. The mouse source applies the inverse, so the cursor lines up with the paint.
- `config/cobeart.yaml` is the single settings file:
  - Network and OptiTrack addresses.
  - `tracking` (rates, and `roles`: Motive ID to role).
  - `arena` (in mm).
  - `metrics` (hub velocity stage: `max_vel`, `history_window`, `max_gap_s`).
  - `window` and `frontend` (`bridge_framerate`, `audio_max_age_ms`).
  - `sources.mouse` (`role`, `z_initial_mm`, `z_step_mm`, `rate_hz`).
  - `debug` and `audio`.

  Python reads it through `cobeart/settings/config.py`, Node through `electron/config.js`. Arena bounds and the port live only there.
- Environment variables: `COBEART_CONFIG` (path of an alternative YAML, Python and Node), `PORT` (overrides the hub port, Node), `COBEART_SOCKETIO_URL` (overrides the hub URL Python clients and the simulator use).

### Frontend (`cobeart-app/public/`)

- `index.html`: browser launcher. Pick a world (from `/worlds.json`) and a source (Live or Mouse), then Start.
- `host/`: the page that hosts one world.
  - `host.js` validates `?world=` against `/worlds.json` and `?source=` (`live` or `mouse`), showing a visible error for a missing or unknown value. It then loads the world into the `#worldFrame` iframe, and `sources/mouse.js` for `source=mouse`.
  - `bridge.js` connects to `/viewer`. Every `1000 / bridge_framerate` ms it posts the latest unposted frame as `{type: 'frame', frame}`. It posts `{type: 'audio', audio}` when the audio timestamp changes, and `{type: 'audio', audio: null}` once the audio is older than `frontend.audio_max_age_ms`.
  - `keys.js` forwards operator keys into the world iframe.
  - `border-control.js` draws the border (`B`).
  - With `?debug`, the bridge shows an overlay listing bodies by role plus the audio line.
- `sources/`: browser input sources. `mouse.js` is a transparent layer over the world: while a button is held it sends `pose_frame`s at `rate_hz` for `sources.mouse.role`, with an identity quaternion and z from the wheel (persisting, clamped to `arena.z`, shown in a corner readout); on release it sends one empty frame. `M` toggles the layer off (so the world's own GUI is clickable) and on again, from the host or from inside the world. The maths is in `mouse-math.mjs` (pure, unit-tested).
- `worlds/<name>/`: one folder per world, each a standalone page that only listens for `frame` and `audio` postMessages. The `worlds-receive-only` fitness rule forbids sockets there. Adding a folder with an `index.html` adds a world to both launchers. There are two worlds:
  - `splat/` (WebGL2 Navier-Stokes fluid). It paints the hands, the feet and `stick` while held, and only while they move. Its gesture patterns key on roles, it has its own colour, and its keys are `P` (pause the fluid physics) and `G` (cycle backgrounds).
  - `glade/` (vegetation growing from one foot's energy field). The tracked body is the `foot_role` parameter (default `right_hand`, the mouse role; `left_foot` on site). Pure simulation in `sim/`, WebGL2 renderer in `render/`, parameters and validation in `params.mjs`, lil-gui panel in `panel.mjs`, wiring in `main.mjs`. Keys: `E` energy heatmap, `T` tuning panel (press `M` first so the panel is clickable), `D` dump the parameters as JSON to the console and clipboard, `R` reset. `BRIEF.md` is its design and `ROADMAP.md` its phases and decisions (roadmap ID `GL`).
- Debug logging in the frontend is gated by `?debug` in the URL or `debug: true` in the YAML.

### Background shaders

`worlds/splat/backgrounds/registry.js` is Splat's only background model. The fluid simulation draws one registry entry behind the dye. Backgrounds are off by default; the GUI's "show background" toggle and "background" list pick one.

An entry is `{ name, fragmentShader, setUniforms(gl, program, ctx) }`:
- The fragment shader is compiled against the sim's base vertex shader (varying `vUv`).
- `setUniforms` sets every uniform the shader uses, with the program already bound.
- `ctx` is `{ time, resolution, audio, bodies }`: `bodies` is the latest frame's role-keyed bodies, and `audio` is null when stale. Types are documented in the registry.

Current entries are Electric Clouds, Circles and Zephyr, each in `backgrounds/<name>/shader.js`, some with `fragment_shader.txt`/`vertex_shader.txt`. To add one, create `backgrounds/<name>/shader.js` exporting that object and list it in the registry.

## Commands

### Python environment

Conda environment `splat-env` (Python 3.12). Console scripts (`start-*`) need `conda activate splat-env`. Otherwise call the interpreter directly; never use `conda run`, which masks output:

```bash
~/miniconda3/envs/splat-env/bin/python -m pytest
```

Install (builds madmom from a pinned commit). Re-run it after moving a module that a console script points to:
```bash
~/miniconda3/envs/splat-env/bin/python -m pip install -e ".[test,viz]"
```

Known traps:
- In a git worktree, run code with `python -m ...` from the worktree root (`python -m cobeart.input.optitrack.start_client`, `python -m cobeart.audio.capture`, `python -m cobeart.simulator`). The installed `start-*` scripts always import the main checkout. A worktree also needs `npm ci` in `cobeart-app/` or the hub tests skip.
- `soundcard` crashes under `python -c`. Put ad-hoc Python in a script file. `python -m cobeart.audio.capture` works with or without flags.
- `soundcard` talks to PulseAudio (or PipeWire's Pulse server) on Linux.

### Running the system

```bash
cd cobeart-app && npm ci          # once
cd cobeart-app && npm start       # hub + Electron window on the launcher page (pick world and Live or Mouse)
cd cobeart-app && npm run web     # hub only; browser launcher at http://127.0.0.1:3000/
start-optitrack-client            # NatNet to hub; needs Motive streaming (addresses from the YAML or positional args)
start-optitrack-client --simulate # scripted synthetic bodies through the real pipeline, no hardware
start-audio-client                # capture, meter and emit to the hub by default
```

- `start-audio-client` flags: `--no-emit` (meter only, no hub), `--device INDEX` (skip the device prompt), `--debug`. Beat detection follows `audio.beat_detection` in the YAML.
- `start-optitrack-client [server_address [client_address [transport]]] [--simulate] [--url URL] [--log-level L]`. `--simulate` sends Motive IDs 0-2, which the shipped `tracking.roles` maps to `left_hand`, `right_hand`, `head`.
- Mouse input needs no Python process: choose Mouse in the launcher (or `/host/?world=splat&source=mouse`, `world=glade` for Glade), hold a button and move; the wheel sets z (in Glade, wheel up lifts the foot off the ground); `M` toggles the mouse layer. Run one source at a time: while the simulator or OptiTrack client is sending, the hub drops the mouse's frames (and vice versa).
- Audio reaches the world without any motion source; frames arrive only while a source sends pose_frames.
- Use `PORT=<port> npm run web` for a second hub; pass the matching `COBEART_SOCKETIO_URL` or `--url` to Python tools.

### Simulator (no hardware)

The hub must be running first (`npm run web` or `npm start`). With no hub, `start-simulator` logs one line and exits 1.

```bash
start-simulator --scenario hands --audio beat --duration 30
```

- `--scenario`: `orbit` (default), `hands`, `still`, `missing-body`, `edge`, `none`. `--audio`: `silence` (default), `tone`, `beat`, `sweep`, `none`.
- `--bodies N` (orbit, still, edge): the first N roles of `tracking.roles` in Motive-ID order. `hands` and `missing-body` always use `left_hand`, `right_hand`, `head`.
- `--fault {malformed,wrong-version,nan}` corrupts every message (the hub must reject it); `--audio-stop-after S` and `--motion-stop-after S` test the stale-audio and no-frame paths; `--bpm`, `--tempo-change BPM@S`, `--beat-jitter MS`, `--tone-hz`, `--rate`, `--audio-rate`, `--seed`, `--url`, `--log-level`. See `--help`.
- Use `--audio none` when a real microphone is also feeding the hub.

### Tests and checks

```bash
~/miniconda3/envs/splat-env/bin/python -m pytest        # repo root: package tests, tests/contract/ (schemas) and tests/e2e/ (cross-process)
~/miniconda3/envs/splat-env/bin/python -m flake8 cobeart tests
cd cobeart-app && npm test                              # node --test test/**/*.test.js (config, hub, motion stage, worlds, mouse maths)
node scripts/fitness.mjs                                # architecture fitness checks, from the repo root
node --test scripts/fitness.test.mjs
```

CI (`.github/workflows/ci.yml`) runs pytest, flake8, `npm test` and the fitness checks on every push. Cap long local test runs with `timeout`. Python tests live next to each package under `tests/`; `tests/contract/schemas.py` provides the contract validators and fixtures; hub tests start a real `node server.js`. Fitness rules: only `hub.js` constructs a Socket.IO server, no arena or port literals outside the config, no `print(` on the OptiTrack hot path, module boundaries, worlds receive only.

## Conventions

- Python: `logging.getLogger(__name__)`, no `print` in `input/optitrack/sender.py` or `transform.py`, per-frame output only at `DEBUG`. flake8 max line 120 (`.flake8`).
- JS: per-frame `console.log` only behind the `DEBUG` flag described above.
- Field names, units and roles come from `contract/`; do not hardcode the arena or port, read them from the config.
- A new world is a folder under `public/worlds/` that consumes `frame`/`audio` messages; a new source sends `pose_frame` to `/ingest`. Neither needs hub changes.

## Testing and validation

### Offline workflow (default)

1. `cd cobeart-app && PORT=3400 npm run web` (or `npm start`, port 3000).
2. Pick an input:
   - Mouse: open `http://127.0.0.1:3400/`, choose Splat or Glade and Mouse, then hold a button and move.
   - Simulator: `COBEART_SOCKETIO_URL=http://127.0.0.1:3400 start-simulator --scenario orbit --audio beat` with Live.
   - Full Python pipeline: `start-optitrack-client --simulate` with Live.
3. Add `?debug` to the page URL for the overlay and logs. Splat paints only moving hands, feet and a held `stick`; Glade grows plants where the `foot_role` body is near the floor, most where it holds still (`E` shows the ground energy).
4. `curl http://127.0.0.1:3400/health` shows connected clients and the age of the last frame and audio.

A real microphone can be checked live with `start-audio-client` against the same hub.

### On-site validation (needs OptiTrack and Motive)

1. Start Motive with streaming enabled; set `network.client_address`, `server_address` and `use_multicast` in the YAML.
2. `start-optitrack-client` should print the connection info; `start-audio-client` should show audio metrics.
3. `npm start` from `cobeart-app/`, choose Splat and Live in the launcher.
4. Bodies paint as splats; the debug overlay (`debug: true` or `?debug`) lists them by role with position and velocity.
5. Tuning checklist (details in `ROADMAP.md`, Deferred):
   - Create each rigid body in Motive aligned to the global axes, with the performer facing OptiTrack +Z (D5 orientation and lean sign).
   - Map the Motive streaming IDs to roles in `tracking.roles`; Motive IDs are often 1-based.
   - Head-tilt lean clamp in `worlds/splat/script.js`.
   - Dropout gap `metrics.max_gap_s`.
   - NatNet multicast on the Linux machine.
   - End-to-end latency with real bodies.

## Development Notes

- Electron runs with `contextIsolation: true`, `nodeIntegration: false` and background throttling disabled; all rendering happens in the sandboxed renderer.
- After `npm ci` in the main checkout, Electron's `chrome-sandbox` needs `sudo chown root:root` and `sudo chmod 4755` on `cobeart-app/node_modules/electron/dist/chrome-sandbox` (Ubuntu 24.04). `npm install` for an added dependency keeps it.
- OptiTrack needs Motive running; the NatNet client is vendored, no SDK install is required to run the Python code.
- Git: `main` is primary; work on a feature branch and fast-forward merge.
