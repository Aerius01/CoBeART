# CoBeART Refactor Roadmap

Source: `ARCHITECTURE_REVIEW.md` (2026-10-03). Section refs like (§3.2.A) point into that review.

## How this roadmap is meant to run

- **One PR per phase.** Each phase is a single PR into the integration branch, made of one commit per work package (WP).
- **One agent per WP, one commit per agent.** Each agent works in its own worktree branched from the integration branch head *as it stood when the phase began*.
- **File ownership is exclusive within a phase.** No two WPs in the same phase touch the same file. This is what makes the commits merge cleanly. If an agent finds it must edit a file it does not own, it stops and reports to the master instead of editing.
- **Phases are strictly ordered.** Phase N+1 branches only after Phase N is merged and green.

### Master agent protocol (per phase)

1. Create integration branch `refactor/phase-N` from the previous phase's merged head, and **check it out in the main checkout before spawning**: with `worktree.baseRef: "head"` (see Worktree caveats), worktrees branch from the main checkout's current HEAD.
2. Spawn one agent per WP with `isolation: "worktree"` and the agent type and `model` named in the WP's **Model** line (agent types: `glsl-shader-expert` for 3.3, `code-documentation-auditor` for 4.2, a general agent otherwise) (the master itself runs on Opus; without an explicit `model`, agents inherit the master's model), passing: the WP block below, the owned-file list, the relevant review sections, and a **unique port** (WP 1.1 → 3101, 1.2 → 3102, and so on). Agents run in parallel, so any hub they start for manual checks uses `PORT=<their port> node server.js` and the simulator's `--url http://127.0.0.1:<their port>`. Nobody but the master uses port 3000. Agents do not launch the Electron window (`main.js` hardcodes 3000 until Phase 3, and parallel windows are unreviewable); they verify visuals in a browser against their own `server.js` port using the Playwright tools, and attach screenshots to their report.
3. Each JS-touching agent runs `npm ci` in its worktree's `cobeart-app/` (the lockfile is committed, so every worktree gets identical versions). An agent that changes `package.json` commits the updated `package-lock.json` with it. npm 12 blocks install scripts unless listed in `package.json` `allowScripts`; Electron's postinstall (binary download) is approved for `electron@37.4.0` only, so an agent that changes the Electron version must re-approve it (`npm install-scripts approve electron`). After any `npm ci` in the **main** checkout, the human re-runs `sudo chown root:root` and `sudo chmod 4755` on `cobeart-app/node_modules/electron/dist/chrome-sandbox` (Ubuntu 24.04 blocks Electron's namespace sandbox otherwise).
4. When all agents finish, cherry-pick or merge each WP commit into `refactor/phase-N` in WP order.
5. **Gate review (read-only).** Before running the gate, spawn the reviewers named in the phase gate against the merged `refactor/phase-N` head (no worktree needed: they cannot edit). They report findings only. The master triages: real defects go back to the owning WP agent as a follow-up amend of that WP's commit; style nits are dropped. Reviewers use their own definitions' model (Sonnet).
6. Run the phase gate (listed per phase). If a WP fails the gate, send it back to that agent rather than patching it in the master.
7. Open the phase PR. The master and the human run the visual parts of each gate in the real Electron window on port 3000. Merge before starting the next phase.

### Worktree caveats (read before Phase 0)

- **Worktree base (verified):** by default Claude Code branches worktrees (subagent ones too) from the remote default branch `origin/HEAD`, not the local checkout. Each developer's `.claude/settings.local.json` must set `"worktree": {"baseRef": "head"}` so agents start from the checked-out phase branch. Agents still confirm `ROADMAP.md` exists and HEAD is the phase base before working.
- Worktrees are created from committed state. **Untracked files do not exist in a worktree.** After Phase 0 the only untracked path is `licenses/`, which agents never need.
- `madmom` is installed once into the shared project environment in Phase 0 (D4, D8), so agents never rebuild it. Worktrees share that environment.
- **Editable-install trap (verified):** the shared env has `cobeart` installed in editable mode from the main checkout. Running `python -m ...` or a script from the worktree root picks up the worktree's `cobeart/` (verified). The installed console scripts (`start-audio-client`, `start-optitrack-client`) do **not**: they always import the main checkout. In worktrees, agents use `python -m cobeart.audiocapture.capture` and `python -m cobeart.optitrackclient.start_client` instead; the `start-*` names in this roadmap mean those module invocations when run from a worktree.
- **`soundcard` argv crash (verified, `soundcard` 0.4.6 is latest):** importing `soundcard` reads `sys.argv[1]` when `sys.argv[0]` is `-c` or `-m`. So it crashes under `python -c`, and under `python -m cobeart.audiocapture.<module>` **with no arguments** (the package `__init__` eagerly imports `capture` while `sys.argv` is still `['-m']`). Until WP 1.6 lands: put ad-hoc Python in a scratch script file, and always pass at least one flag to audio modules run with `-m`.
- Agents call the interpreter directly, `~/miniconda3/envs/splat-env/bin/python`, never `conda run` (it masks output). The project CLAUDE.md says the same as of Phase 0.

### Shared conventions (every agent follows these)

- **Python logging:** `logging.getLogger(__name__)`, no `print` in hot paths. Per-frame output only at `DEBUG`.
- **JS debug gating:** per-frame `console.log` only behind `const DEBUG = new URLSearchParams(location.search).has('debug') || window.__COBEART_CONFIG__?.debug === true;`. Phase 3 wires `__COBEART_CONFIG__`; before that, the query param alone works.
- **Contract version:** `schemaVersion: 1` on `frame` and `audio_metrics`.
- **Tests:** Python tests live next to the package under `tests/` and run with pytest. JS tests use `node --test` under `cobeart-app/test/`.
- **Offline development.** There is no OptiTrack system available during this work. Every acceptance check and phase gate must be runnable with the simulator (WP 1.9, and the `--simulate` path from WP 2.2). A real microphone is available, so audio can still be checked live.
- **No backward-compat shims.** When an interface changes, the WP that changes it updates all call sites it owns; call sites it does not own are listed as dependencies for a later phase.

---

## Phase 0: Decisions and housekeeping (master or human, not parallel)

Not agent work. These are decisions the review explicitly leaves open, plus making the repo worktree-ready.

| ID | Decision | Review ref | Recommendation |
|---|---|---|---|
| D1 | Fate of `server.js` | §3.2.A | **Decided:** keep as a thin wrapper over `createHub()` so `npm run web` runs the identical hub without Electron (browser debugging, e2e tests). Drop `npm run dev` entirely: on Linux it starts two hubs on port 3000 and one crashes with `EADDRINUSE` |
| D2 | Background architecture | §3.3.D | **Decided:** the registry is the only background model. Delete `kaleidoscope/` and `particle-orbits/` (never used, no intent to use). Fix the registry contract so each entry owns its uniform setup via `setUniforms(gl, program, ctx)`, where `ctx` always carries time, resolution, latest audio metrics and rigid bodies, even if a shader ignores them |
| D3 | Untracked shader dirs (`blackhole-sun`, `conch`, `metaballs`, `retro-disco`, `particle-orbits/original.txt`) | §3.3.E | **Done:** all deleted (2026-10-03). The registry stays at Electric Clouds, Circles, Zephyr |
| D4 | `madmom` vendoring | §3.1 | **Decided:** beat detection is a required, default-on signal, so madmom is a required dependency. Pin upstream by commit in the `cobeart` extra: `madmom @ git+https://github.com/CPJKU/madmom.git@27f032e8947204902c675e5e341a3faf5dc86dae` (unmodified upstream, 0.17.dev0, works with NumPy 2). Delete the local `madmom/` clone once the pinned install is verified (needs explicit go-ahead) |
| D5 | Orientation convention for the arena | §3.1.E | **Decided (2026-10-04, replaces the earlier Z-Y-X Euler decision; provisional pending on-site check):** no Euler angles in the contract. Each rigid body carries a unit quaternion `qx, qy, qz, qw` (scalar last) in arena axes, obtained by applying the position axis remap to the rotation. The remap x = -X, y = Z, z = Y is itself a proper rotation (det +1, 180 deg about OptiTrack (0,1,1)/sqrt2), so the quaternion maps as (qx, qy, qz, qw) -> (-qx, qz, qy, qw). At identity the body faces arena +y with up = +z (right = +x). Angular velocity is the vector `wx, wy, wz` (deg/s, arena axes) from successive quaternions. Reason: every Euler decomposition has one angle limited to ±90° with gimbal lock at its ends and wraparound jumps at ±180°, which show up as angular-velocity spikes; the quaternion has neither. Consumers derive scalars directly, e.g. forward lean = asin(-forward.z) with forward = q·(0, 1, 0). Verify sign and identity orientation against real performers via record/replay (see Deferred) |
| D6 | Shared config format | §4.2 | **Decided:** one `config/cobeart.yaml` at repo root, read by Python (`pyyaml`) and Node (`js-yaml`) |
| D7 | `ink.zip`, `licenses.zip`, `licenses/` | n/a | **Done:** both zips deleted (verified identical copies of `public/ink/` and `licenses/`). `licenses/` stays untracked and must never be committed: the repo is public and it contains private correspondence. No attribution file for now |
| D8 | Project environment | n/a | **Done (2026-10-03):** conda `splat-env`, Python 3.12, interpreter `~/miniconda3/envs/splat-env/bin/python`. `pyproject.toml` pruned to the actually-imported deps (numpy>=2, scipy, python-socketio[client], soundcard, pinned madmom; `viz` extra = matplotlib; `test` extra + jsonschema), `requires-python >=3.12`, `start-audio-client` registered, pytest `testpaths = ["cobeart"]`. Installed with `pip install -e ".[test,viz]"`. Verified: madmom 0.17.dev0 builds on NumPy 2.5 and tracks a synthetic 120 BPM click at 120.0 BPM; PulseAudio (PipeWire) lists 5 inputs incl. loopback monitors. Target show machine is assumed Debian (migration out of scope) |
| D9 | Manual audio scripts in `audiocapture/tests/` | §4.4 | **Done:** they were interactive scripts (device prompt), not automated tests, and broke `pytest`. Moved to `cobeart/audiocapture/tools/live_features.py` and `tools/live_predictive_beat.py` (git mv). `pytest` now collects nothing until Phase 1 adds real tests. Local `madmom/` clone deleted |

**Commit 0.1 (master):** commit the D8 `pyproject.toml` changes and the D9 move; track `CLAUDE.md`, `ARCHITECTURE_REVIEW.md`, `ROADMAP.md`, `.claudeignore`, and `cobeart-app/.claude/agents/glsl-shader-expert.md`. Never stage `licenses/` (use explicit paths, not `git add -A`). Before the commit, set up the environment per D8 and verify `from madmom.features.beats import RNNBeatProcessor` imports.

**Gate:** `git status` shows only `licenses/` as untracked. All seven decisions recorded (append them to this file).

---

## Phase 1: Foundations (8 parallel WPs)

Everything here is either a new file or a file nobody else touches in this phase. Nothing depends on anything else in the phase.

### WP 1.1: Unify the Socket.IO hub (§3.2.A, P0 #1)
- **Model:** Sonnet. Mechanical extraction with a clear target and a smoke test.
- **Owns:** `cobeart-app/electron/hub.js` (new), `cobeart-app/electron/main.js` (the `startHttpServer` region only), `cobeart-app/server.js`, `cobeart-app/package.json`, `cobeart-app/test/hub.smoke.test.js` (new)
- **Scope:** Move all hub logic from `main.js` into `createHub({ port, host, publicDir }) -> { server, io, close }`. `main.js` and `server.js` both call it. Bind `127.0.0.1` in both. Behavior must equal current `main.js` exactly (no validation or staleness changes yet). Per D1, reduce `server.js` to a thin wrapper that calls `createHub()`, and remove the `dev` script (and the `concurrently` devDependency) from `package.json`.
- **Test:** smoke test boots the hub on an ephemeral port, emits a frame on `/ingest` and metrics on `/audio`, asserts the merged frame on `/viewer` and the `/health` response. Add `socket.io-client` as a devDependency and a `"test": "node --test test/"` script.
- **Accept:** `npm test` passes; `npm run web` and `npm start` both serve the same pipeline.

### WP 1.2: Define the message contract (§4.1, P0 #2)
- **Model:** Opus. Defines the schema every later phase builds on; mistakes propagate.
- **Owns:** `contract/frame.schema.json`, `contract/audio_metrics.schema.json`, `contract/splat.schema.json`, `contract/CONTRACT.md` (all new)
- **Scope:** JSON Schema (draft 2020-12) for the `frame` message (as emitted by `sender.py:45-67`, plus hub-injected `timestamp` and optional `audio`), the `audio_metrics` message (as built in `capture.py:541-562`), and the bridge-to-sim `splat` postMessage. Include units and ranges in `description`/`minimum`/`maximum`; describe orientation per D5 (arena-axes quaternion `qx, qy, qz, qw` and angular velocity vector `wx, wy, wz`; amended after the first commit, which used Euler angles); `schemaVersion: {"const": 1}`, `additionalProperties: false`. Settle `ID` as the canonical field name. The audio schema must cover every field `capture.py` emits, not just the four the review lists: `rms_db`, `rms_envelope`, `is_peak`, `peak_intensity`, `is_onset`, `onset_strength`, `spectrum_2d`, `spectrum_config`, `beat`, `tempo_bpm` (nullable only while tempo is not yet stable), `beat_timestamp` (nullable likewise). Beat fields are **required** on every message: beat detection is part of the default signal (D4). `CONTRACT.md` is short prose: who produces, who consumes, how to bump the version.
- **Accept:** schemas validate against the metaschema; a sample payload captured from current code validates (except for the new `schemaVersion` field).

### WP 1.3: De-globalize metrics and fix shared velocity history (§3.1.B/C/D, P0 #4)
- **Model:** Sonnet. Small, well specified, test-first.
- **Owns:** `cobeart/packagesender/metrics.py`, `cobeart/packagesender/sender.py` (the single `calculate_metrics` call site only), `cobeart/packagesender/tests/` (new)
- **Scope:** Replace module globals with a `MetricsTracker` class holding per-body state (`BodyState` dataclass, including its own velocity-history deque). Return a frozen `BodyMetrics` dataclass. Preserve current semantics (norm velocity zeroes only after a full window of zeros, else window max). `max_vel` and window length become constructor args with today's values as defaults. Remove the debug `print`. Inject a clock callable for testability. Update the one call site in `sender.py` (a module-level tracker instance is acceptable here; WP 2.2 moves it into DI).
- **Test:** two interleaved bodies do not affect each other's smoothing (the regression test for the bug); first-sighting returns zeros; velocity math with an injected clock.
- **Accept:** pytest passes; `sender.py` diff is limited to the call site.

### WP 1.4: Fix composite body lookup (§3.3.C, P0 #6)
- **Model:** Sonnet. Small bug fix.
- **Owns:** `cobeart-app/public/composite/composite.js`
- **Scope:** Resolve `left_hand`/`right_hand` by finding the rigid body whose `ID` equals the `body_map.json` value, not by array index. Handle a missing body explicitly (skip that hand's input) instead of throwing on `lh.x`. Confirm whether OptiTrack IDs match `body_map.json` values (0-based) and note the finding in the commit message if they do not.
- **Accept:** composite view runs with one hand missing and with bodies arriving out of order, no console errors. Until WP 1.9 merges, check this with a throwaway inline `socket.io-client` emit script; at the phase gate, re-check it with the simulator's `missing-body` and `shuffled` scenarios.

### WP 1.5: Python packaging hygiene
- **Done in Phase 0 (D8).** No agent. Number kept so references stay stable.

### WP 1.6: Remove Windows audio shims (§5)
- **Model:** Sonnet. Small, plus a live-audio check.
- **Owns:** `cobeart/audiocapture/__init__.py`, `cobeart/audiocapture/capture.py` (lines ~9-13 and ~152-157 only), `cobeart/audiocapture/viz/waveform.py`
- **Scope:** Remove the eager `AudioCapturer`/`AudioEmitter` re-exports from `audiocapture/__init__.py` (nothing imports them) so `python -m cobeart.audiocapture.<module>` with no arguments no longer crashes in `soundcard`; verify with `python -m cobeart.audiocapture.tools.live_features </dev/null`. Delete the MediaFoundation `fromstring` patch and warning filters. Validation spike: confirm capture and loopback enumeration under PulseAudio/PipeWire on Debian; if `fromstring` breaks under the Linux backend, fix the root cause and report it.
- **Accept:** pytest passes; `start-audio-client` captures from a real device (manual check, report device list output in the summary).

### WP 1.7: Extract and pin the OptiTrack transform (§3.1.E, P2 #13, #14.3)
- **Model:** Opus. Quaternion remap through a reflection is easy to get subtly wrong, and synthetic tests can pass on a wrong convention.
- **Blocked by:** D5
- **Owns:** `cobeart/optitrackclient/transform.py` (new), `cobeart/optitrackclient/start_client.py` (transform and dead-code regions; do not touch the `sender` import or `payload_sender` wiring), `cobeart/optitrackclient/tests/` (new), `cobeart/packagesender/metrics.py` and `cobeart/packagesender/tests/` (angular velocity only; WP 1.3 is merged first), `cobeart/packagesender/sender.py` (the per-body tuple unpack and orientation/angular-velocity payload fields only), `cobeart-app/public/fluid-bridge.js` (the orientation fields it copies into `splat` and its debug overlay only), `cobeart-app/public/fluid/script.js` (head-tilt palette block around line 2595, and the angular-velocity fields read into `latestAngVel` around line 1809 and `computeCurl` around line 1233 only)
- **Scope:** Move axis remap and orientation extraction into pure functions in `transform.py` that do not import `sender` (so they are importable without a live server). Implement D5: apply the same axis remap to the rotation as to the position (x = -X, y = Z, z = Y; this is a proper rotation P, det +1, so R_arena = P R P^T) and emit it as a unit quaternion `qx, qy, qz, qw` in arena axes. No Euler decomposition anywhere. Put the convention and the identity-orientation assumption in the `transform.py` module docstring. In `metrics.py`, replace the Euler-difference angular velocity with the rotation vector of `q_now * q_prev^-1` divided by the time step (deg/s, arena axes), so `BodyMetrics` carries the quaternion and `angular_velocity` as `wx, wy, wz`. In `sender.py`, emit `qx, qy, qz, qw, wx, wy, wz` instead of `roll, yaw, pitch, vroll, vyaw, vpitch` (this also removes the old `vyaw`/`vpitch` swap). In `fluid-bridge.js`, forward the new fields in the `splat` message and overlay instead of the Euler ones (WP 2.4 later rewrites the bridge per `splat.schema.json`). In `fluid/script.js`, the head-tilt palette computes forward lean = asin(-forward.z) from the quaternion, keeping the -90..60 clamp (note in the commit message that it needs on-site retuning), and `latestAngVel`/`computeCurl` take `wx, wy, wz`. Delete `head_tilt_from_quat`, `quaternion_to_rotation_matrix`, `quaternion_to_xaxis_yaxis`, `heading_and_tilt_from_quat`, `tilt1..tilt5g`, duplicate imports, and commented blocks. `start_client.py` calls `transform.py`.
- **Test:** known OptiTrack quaternions and positions produce documented arena outputs: identity maps to the identity quaternion; a forward lean gives positive forward lean and leaves heading unchanged; turning in place about the vertical rotates forward within the horizontal plane; a side lean tilts up toward ±x with no forward lean; axis remap of a known point; the remapped rotation is proper (determinant +1) and the quaternion is unit length. Metrics: constant rotation at a known rate about arena z gives `wz` equal to that rate and `wx = wy = 0`, including across the 180° heading wrap.
- **Accept:** pytest passes; `python -m py_compile` on `start_client.py`.

### WP 1.8: Shared configuration file (§4.2, P1 #7 part 1)
- **Model:** Sonnet. Collecting existing literals into one file.
- **Blocked by:** D6
- **Owns:** `config/cobeart.yaml` (new), `config/cobeart.schema.json` (new)
- **Scope:** One file holding every cross-layer and tunable value from the review's §4.2 inventory: network (OptiTrack IPs, multicast, Socket.IO host/port), tracking (framerates, max objects, rescale), arena bounds, metrics (`max_vel`, history window), hub (`audio_max_age_ms`, default 500), window (1050×1050), frontend (bridge framerate, splat color, composite z-threshold 2700, clap distance 200, `show_interactive_elements: false`), `debug: false`, and an `audio` section (`chunk_size`, `sample_rate`, `beat_detection: true`). Values equal today's literals, except `beat_detection`, which is on per D4. Schema validates it. Nothing reads it yet.
- **Accept:** YAML validates against its schema.

### WP 1.9: Socket-level data simulator (offline testing)
- **Model:** Sonnet. Largest new code in the phase, but fully specified.
- **Owns:** `cobeart/simulator/` (new: `__init__.py`, `__main__.py`, `scenarios.py`, `audio.py`, `faults.py`, `tests/`)
- **Scope:** A standalone Python tool that stands in for both Python clients. It emits `frame` on `/ingest` and `audio_metrics` on `/audio`, shaped exactly per `contract/` (WP 1.2 runs in parallel, so both agents get the payload shape from the review, `sender.py:45-67` and `capture.py:541-562`, plus `schemaVersion: 1`). It must not import `packagesender` or `optitrackclient`, because those are being rewritten.
  - **Motion scenarios**, produced directly in contract space (mm, quaternions, deg/s): `orbit` (N bodies circling the arena), `hands` (left and right hand IDs from `body_map.json`, with a periodic clap within 200 mm), `still` (bodies stop, so norm velocity should go to zero), `missing-body` (one hand drops out at intervals), `shuffled` (rigid bodies in random order each frame), `edge` (bodies sweep to the arena bounds and slightly past them). Orientation follows D5: every body carries an arena-axes quaternion and an angular velocity vector (e.g. `hands` and `orbit` include a nodding head body whose quaternion oscillates in forward lean). Velocities come from simple finite differences computed in the simulator, deliberately separate from `metrics.py` so they can act as an independent reference.
  - **Audio scenarios:** `silence`, `tone` (fixed dominant frequency), `beat` (RMS and peak pulses at a given BPM), `sweep`. Every scenario emits the beat fields. `beat` drives them realistically: `beat: true` on the frame nearest each beat, `beat_timestamp` of that beat, `tempo_bpm` null for the first few seconds (lock-in) and then stable. Options `--bpm`, `--beat-jitter MS`, and `--tempo-change BPM@SECONDS` exercise consumers and the predictor's assumptions. This is the continuous fake beat signal for development; real beat detection is checked with a real mic and music.
  - **Fault injection:** `--fault malformed|wrong-version|nan`, `--audio-stop-after SECONDS` (to test stale audio), `--motion-stop-after SECONDS`.
  - **CLI:** `python -m cobeart.simulator --scenario hands --audio beat --rate 240 --duration 30 --seed 0 --url http://127.0.0.1:3000`. Same seed, same stream. Log the parameters at startup. No `pyproject.toml` entry for now; WP 3.1 adds the `start-simulator` script.
  - The scenario generators are pure functions `(t: float, rng) -> Frame`, separate from the emitter loop, so tests and the e2e harness (WP 4.3) can call them without a socket.
- **Test:** pure scenario tests (`hands` produces a clap within 200 mm at the expected time; `shuffled` keeps the ID set intact; all generated frames match the payload shape from the review sections above). An integration test boots the WP 1.1-equivalent hub if it is present, otherwise a minimal `python-socketio` server fixture, and asserts that frames arrive.
- **Accept:** with `npm run web` running, `python -m cobeart.simulator --scenario orbit --audio beat` drives the splat visualization visibly, and the merged audio values reach the viewer (debug overlay). No visual reacts to audio yet; that is feature work (see Deferred).

**Phase 1 gate review:** `code-optimizer-reviewer` on WP 1.3 (`metrics.py` runs at 240 Hz × bodies).

**Phase 1 gate:** `pytest`, `flake8 cobeart`, `npm test` in `cobeart-app`. Simulated smoke run: `npm start`, then `python -m cobeart.simulator --scenario hands --audio beat`. Splat and composite both respond. Re-check WP 1.4 with `--scenario missing-body` and `--scenario shuffled`. Audio check: `start-audio-client --emit` with a real mic (emission is off by default until WP 3.1), in parallel with the motion-only simulator (`--audio none`).

---

## Phase 2: Wire the contract and fix boundaries (6 parallel WPs)

Depends on: hub module (1.1), schemas (1.2), `MetricsTracker` (1.3), `jsonschema` dep (Phase 0).

### WP 2.1: Hub validation and stale-audio policy (§3.2.B, §4.1, P0 #2, P1 #8)
- **Model:** Sonnet. Schema validation and a staleness timer, both well specified.
- **Owns:** `cobeart-app/electron/hub.js`, `cobeart-app/package.json`, `cobeart-app/test/`
- **Scope:** Validate `frame` and `audio_metrics` with `ajv` against `contract/*.schema.json` at the `/ingest` and `/audio` boundaries; reject and log-once (rate-limited) on failure; fail loudly on `schemaVersion` mismatch. Drop `lastAudioData` from merged frames when older than `audioMaxAgeMs` (a `createHub` option, default 500; Phase 3 sources it from config). Remove the "back-compat" comment.
- **Test:** extend the smoke test: malformed frame is not forwarded; stale audio is omitted; wrong `schemaVersion` is rejected.
- **Accept:** `npm test` passes.

### WP 2.2: Python frame producer without import side effects (§3.1.A, §4.3, P0 #3)
- **Model:** Opus. Lifecycle, threading, dependency injection, and a synthetic NatNet source that must produce data consistent with the D5 transform.
- **Owns:** `cobeart/packagesender/sender.py`, `cobeart/optitrackclient/start_client.py` (sender wiring, frame callback and `start()`), `cobeart/packagesender/metrics.py`, `cobeart/packagesender/tests/`
- **Scope:** No connection at import. `PayloadSender` takes injected `socketio.Client`, `MetricsTracker`, URL, and framerate; owns a background reconnect loop mirroring `audiocapture/emitter.py`; `connect()`/`stop()` lifecycle. Use `get_socketio_url()` instead of the hardcoded `SIO_URL`. Add `schemaVersion: 1`. Replace prints with logging; log emit failures once per disconnect episode. `start_client.py` builds the sender inside `start()`, not at module level. Payload `timestamp` in epoch milliseconds per the contract.
- **Velocity timing (Phase 1 gate review findings #2, #3):** today metrics are computed when a payload is *sent*, with `time.time` at send time, behind a `>= 1/framerate` gate that aliases against 240 Hz mocap jitter (dt alternates ~4/8 ms; an unchanged pose can be resent, giving a zero then a ~2x velocity spike). Instead, update `MetricsTracker` once per mocap frame from the NatNet frame callback, using the NatNet frame `timestamp` (`data_dict["timestamp"]`, seconds since Motive start) as the clock, so dt is mocap time; the send-rate gate only decides which computed state is emitted. Add a minimum dt floor (configurable, default 0.5 ms): below it, skip the velocity update rather than divide. The synthetic source supplies its own frame timestamps.
- **Offline pipeline seam:** add a `MotionSource` protocol to `start_client.py`. `NatNetClient` is one implementation. `SyntheticNatNetSource` is the other: it calls the same `receive_rigid_body_frame(id, position, rotation, tracking_valid)` / `receive_new_frame` callbacks with OptiTrack-native data (meters, quaternions, OptiTrack axes) from a simple scripted path. `start-optitrack-client --simulate` uses it. This exercises the real transform (1.7), metrics (1.3) and sender path with no hardware, which the socket-level simulator (1.9) skips.
- **Test:** `import cobeart.packagesender.sender` and `import cobeart.optitrackclient.start_client` with no server running; built payload validates against `contract/frame.schema.json`; a synthetic body moving at a known speed produces `abs_vel` within tolerance after passing through transform and metrics; with jittered frame arrival and a send rate below the mocap rate, velocity stays constant for constant motion (no 0/2x alternation); a dt below the floor does not produce a velocity spike.
- **Accept:** pytest passes; `start-optitrack-client --simulate` starts before Electron, connects once Electron is up, and drives the splat view.

### WP 2.3: Python audio producer: contract and error surfacing (§4.3, P1 #8)
- **Model:** Sonnet. Error surfacing in an existing, well-structured module.
- **Owns:** `cobeart/audiocapture/capture.py` (payload construction and capture loop only; leave `__init__` defaults and the beat path to WP 3.4), `cobeart/audiocapture/emitter.py`, `cobeart/audiocapture/tests/`
- **Scope:** Add `schemaVersion: 1` to the metrics payload. Replace the capture-loop `except Exception: pass` with logged failure plus a surfaced error state (the thread must not die silently; the main loop should exit non-zero or report). Replace the bare `pass` blocks in `emitter.py` with log-once-with-backoff for transient emit/connect errors.
- **Test:** audio payload validates against `contract/audio_metrics.schema.json`; a capture source that raises surfaces the error.
- **Accept:** pytest passes.

### WP 2.4: Bridge forwards the contract, not a re-enumeration (§3.3.A/B, P0 #2, P1 #10)
- **Model:** Opus. Consolidates three message handlers in the 2,768-line `fluid/script.js`; behavior regressions are easy to miss.
- **Owns:** `cobeart-app/public/fluid-bridge.js`, `cobeart-app/public/fluid/script.js` (message-handler regions only, lines ~1644, ~1677, ~1802)
- **Scope:** Bridge builds `splat` messages per `contract/splat.schema.json` with a single mapping function instead of copying 15 fields inline; drop the `ID`/`id` defensive fallback (contract settles `ID`). Gate all per-frame logs behind `DEBUG`. Consolidate the three `message` listeners in `fluid/script.js` into one dispatcher keyed on `type`.
- **Accept:** splat visualization behaves as before; no per-frame logs without `?debug`.

### WP 2.5: Molten and ink conform to the splat contract (§3.3.B)
- **Model:** Sonnet. Follows the pattern WP 2.4 sets, against a fixed schema.
- **Owns:** `cobeart-app/public/molten/molten.js`, `cobeart-app/public/ink/ink.js`
- **Scope:** Message handlers consume the `splat` schema fields (same names as WP 2.4 emits) via a single listener each. Gate per-frame logs behind `DEBUG`.
- **Coordinate with 2.4:** both agents receive `contract/splat.schema.json` as the source of truth; neither invents field names.
- **Accept:** Molten (with and without performance mode via `?performance=true`) and Ink both render and react.

### WP 2.6: Fix the performance-mode dialog bug (§3.2.C, P0 #5)
- **Model:** Sonnet. Small bug fix (Haiku would likely manage, but the dialog API detail is easy to misread).
- **Owns:** `cobeart-app/electron/main.js`
- **Scope:** Use the object returned by `showMessageBoxSync` correctly (or switch to `showMessageBox` and await `{ response, checkboxChecked }`). Gate `openDevTools()` behind a debug flag (env var for now, config in Phase 3).
- **Accept:** choosing Molten with the checkbox loads `/molten/?performance=true`.

**Phase 2 gate review:** `code-security-auditor` on WP 2.1 (hub input-validation boundary, bind address) and WP 2.2 (reconnect logic, untrusted URL from env/config); `code-optimizer-reviewer` on WP 2.4 (bridge loop at 120 Hz, message dispatch in `fluid/script.js`).

**Phase 2 gate:** Phase 1 gate plus:
- `start-optitrack-client --simulate` started *before* Electron must not crash, and connects once Electron is up.
- Stale audio: `python -m cobeart.simulator --audio beat --audio-stop-after 5` makes `audio` disappear from `/viewer` frames within the max age.
- `--fault malformed`, `--fault wrong-version` and `--fault nan` are each rejected at the hub, logged once, and not forwarded.
- Molten (with and without `?performance=true`) and Ink both respond to `--scenario orbit`.

---

## Phase 3: Configuration, beat detection, and shader-system coherence (4 parallel WPs)

Depends on: `config/cobeart.yaml` (1.8), the rewritten sender/emitter (2.2, 2.3), hub options (2.1), bridge (2.4).

### WP 3.1: Python reads the shared config (§4.2, P1 #7)
- **Model:** Sonnet. Broad but mechanical: one loader, many injection sites.
- **Owns:** `cobeart/settings/` (replace `streaming.py`), `cobeart/audiocapture/utils.py`, `cobeart/packagesender/sender.py`, `cobeart/packagesender/metrics.py`, `cobeart/audiocapture/emitter.py`, `cobeart/optitrackclient/start_client.py`, `cobeart/simulator/`, `cobeart/audiocapture/capture.py` (`main()` only), `pyproject.toml`
- **Scope:** Frozen `Settings` dataclass tree loaded once from `config/cobeart.yaml` (path overridable by `COBEART_CONFIG`), `COBEART_SOCKETIO_URL` still overrides the URL. Delete `streaming.py` and `get_socketio_url`; inject settings into sender, tracker, emitter, client, and simulator (which uses the arena bounds, body map IDs and rates instead of its own defaults). Add `pyyaml`. Register `start-simulator = "cobeart.simulator.__main__:main"`. In `capture.py` `main()`, make emission the default (the Phase 3 gate runs `start-audio-client` with no flags; keep a `--no-emit` for local metering) and build `AudioCapturer` from the `audio` settings section (including `beat_detection`); leave the class itself and its DSP defaults to WP 3.4 (not in scope per review).
- **Test:** loader parses the real file; missing required key raises a specific error naming the key.
- **Accept:** pytest passes; both clients start using only the config file.

### WP 3.2: Electron and frontend read the shared config (§4.2, §3.3.C, P1 #7, #10)
- **Model:** Sonnet. Same pattern as 3.1 on the JS side.
- **Owns:** `cobeart-app/electron/main.js`, `cobeart-app/electron/hub.js`, `cobeart-app/server.js`, `cobeart-app/package.json`, `cobeart-app/public/fluid-bridge.js`, `cobeart-app/public/composite/composite.js`, `cobeart-app/test/`
- **Scope:** Load `config/cobeart.yaml` once in the main process (`js-yaml`), pass port and `audioMaxAgeMs` to `createHub`, window size to `BrowserWindow`, inject the frontend subset as `window.__COBEART_CONFIG__` (replacing `__SOCKET_PORT__`; also serve it at `/config.json` so `npm run web` pages get it). `composite.js` uses injected arena, z-threshold, clap distance, and `show_interactive_elements` (config sets it `false`, code today has `true`: an intended visible change); trim `rightHandHistory` like `leftHandHistory`; fix the debug splats (~667) to match `splat.schema.json`; delete its commented-out blocks; gate per-frame logs. `fluid-bridge.js` uses injected framerate and color.
- **Accept:** changing arena in the YAML changes composite normalization with no JS edit; `npm test` passes.

### WP 3.3: One background-shader architecture (§3.3.D/E, P1 #9)
- **Model:** Sonnet, spawned as the `glsl-shader-expert` agent type (`.claude/agents/glsl-shader-expert.md`, which sets `model: sonnet`).
- **Owns:** `cobeart-app/public/backgrounds/**`, `cobeart-app/public/fluid/script.js` (registry compile region ~1008-1011 and `drawBackground` ~1410-1438)
- **Scope:** Apply D2:
  - Delete `backgrounds/kaleidoscope/` and `backgrounds/particle-orbits/`.
  - Registry entry contract becomes `{ name, fragmentShader, setUniforms(gl, program, ctx) }`, documented with JSDoc in `registry.js`. `ctx` is `{ time, resolution, audio, rigidbodies }`, where `audio` and `rigidbodies` follow `contract/` (null/empty when absent). Move each shader's current uniform values out of the `if (bgDef.name === ...)` chain into its own `setUniforms`; delete the chain. Visual output of the three existing backgrounds must not change.
  - `fluid/script.js` keeps the latest audio and rigid-body state from the message dispatcher built in WP 2.4 and passes it as `ctx`.
- **Accept:** each background looks identical to before (side-by-side screenshot against the pre-phase build); a test background that maps `ctx.audio.rms` to brightness visibly reacts to `python -m cobeart.simulator --audio beat` (then remove it before committing).
- **Accept:** every registry entry renders inside the fluid sim; each standalone visualization loads at its new path.

### WP 3.4: Beat detection on by default (D4)
- **Model:** Opus. Real-time threading, madmom latency, and a convergence test that must be robust rather than tuned to pass.
- **Owns:** `cobeart/audiocapture/capture.py` (`AudioCapturer` class only, not `main()`), `cobeart/audiocapture/beat/`, `cobeart/audiocapture/tests/`
- **Scope:**
  - `AudioCapturer(enable_beat_detection=True)` by default. Remove the code that silently turns beat detection off when madmom is missing (`capture.py:~143`), so a missing madmom fails at startup with a clear error.
  - In `beat/detector.py`, replace the lazy "optional" import with a normal top-level import, since madmom is now required.
  - Make sure the detector/predictor work never blocks the capture loop (no dropped chunks), and that the payload always carries `beat`, `tempo_bpm` and `beat_timestamp` per the contract.
  - Log the beat configuration at startup.
- **Test:** a new test feeds a synthetic click track (generated in the test at a known BPM) through `AudioCapturer` without a device, and asserts that the detected tempo converges within ±2 BPM and that beat timestamps line up within a stated tolerance.
- **Accept:** `start-audio-client` with a real mic and music playing emits `beat: true` pulses and a stable `tempo_bpm` (report the BPM, the actual track tempo, and the lock-in time); the capture thread keeps up (no backlog growth over 5 minutes); CPU use is reported.
- **Not in scope:** visuals reacting to beats. The beat fields reach every simulation and background `ctx` through the contract, so wiring them into visuals is later feature work.

**Phase 3 gate review:** `code-optimizer-reviewer` on WP 3.4 (beat detection inside the capture loop: no blocking, no dropped chunks).

**Phase 3 gate:** Phase 2 gate plus `grep -rn "3000" cobeart-app/public cobeart` shows no arena or port literals outside `config/`; `start-audio-client` with no extra flags emits live beat fields from a real mic and music; every `/viewer` frame's `audio` carries the beat fields.

---

## Phase 4: Guardrails and documentation (3 parallel WPs)

Depends on everything above.

### WP 4.1: Fitness functions and CI (P2 #17)
- **Model:** Sonnet. CI config and a grep-based checker.
- **Owns:** `.github/workflows/ci.yml` (new), `scripts/fitness.mjs` (new)
- **Scope:** CI runs `pytest` (including `tests/e2e/` from WP 4.3, using the agreed path even before 4.3 merges), `flake8`, `npm test`. Fitness script fails if: `server.js` or `main.js` construct `new Server(` directly instead of using `createHub`; any file outside `contract/` and the hub re-declares the payload field list (heuristic grep for the field set); arena or port literals appear outside `config/`; `print(` appears in `cobeart/packagesender` or `cobeart/optitrackclient/transform.py`.
- **Accept:** CI green on the phase branch; deliberately reintroducing one violation locally makes the fitness script fail.

### WP 4.2: Bring CLAUDE.md in line with reality (P2 #16)
- **Model:** Sonnet, spawned as the `code-documentation-auditor` agent type (sets `model: sonnet`). Its default instructions encourage editing docstrings and comments anywhere; the prompt must state that it may edit `CLAUDE.md` only and should report any stale docstrings it finds instead of fixing them.
- **Owns:** `CLAUDE.md`
- **Scope:** Update commands (`start-audio-client`, `npm test`), architecture (hub module, contract, config file), background system (registry only, `setUniforms` contract; remove the Kaleidoscope and Particle Orbits sections), test status, Linux as the primary platform, and the conda invocation guidance.
- **Accept:** every command in CLAUDE.md runs as written. Replace the hardware-only "Testing" section with the offline workflow (simulator, `--simulate`, e2e), and keep the hardware checklist as a separate "on-site validation" list.

### WP 4.3: End-to-end offline integration test (§4.4)
- **Model:** Opus. Orchestrating Node and Python subprocesses with timing assertions; flakiness is the main risk.
- **Owns:** `tests/e2e/` (new, repo root)
- **Scope:** A pytest suite that boots the real hub (`node server.js` as a subprocess on an ephemeral port, with the config path overridden), runs scenarios through both simulation paths (socket-level `cobeart.simulator`, and the full Python pipeline via `start-optitrack-client --simulate`), subscribes to `/viewer` with a `python-socketio` client, and asserts:
  - every received frame validates against `contract/frame.schema.json`
  - merged audio is present and then disappears after `--audio-stop-after`
  - fault-injected payloads never reach `/viewer`
  - IDs from `shuffled` and `missing-body` arrive intact
  - the `still` scenario drives `norm_abs_vel` to 0 through the real metrics path
- **Accept:** `pytest tests/e2e` passes locally and in CI; no hardware, browser, or Electron window needed.

---

## Deferred (not scheduled)

- **Record and replay real sessions.** At the next session with the real system, record raw NatNet rigid-body callbacks (ID, position, quaternion, timestamp) to a file, and add a `ReplaySource` that plays them back through the `MotionSource` seam from WP 2.2. That replaces synthetic motion with real movement for regression tests, and is the only offline way to check that the D5 orientation convention matches real performers.
- **On-site validation spikes** (cannot be done offline, per §5): NatNet multicast from the Debian box, and an end-to-end latency check with real bodies.
- **On-site tuning checklist** (found during Phase 1):
  - D5 identity orientation and lean sign: each rigid body must be created in Motive aligned to the global axes with the performer facing OptiTrack +Z.
  - Rigid-body IDs vs `body_map.json` (0-based): Motive Streaming IDs are often 1-based.
  - Composite `frontend.composite.z_threshold` (2700 mm): above the 2500 mm arena ceiling and likely above a raised hand, so hand-up transitions may never fire.
  - Head-tilt palette lean clamp (-40..30 deg in `fluid/script.js`).
  - Dropout gap `max_gap_s` (0.25 s) in `MetricsTracker`.
- **Audio-reactive visuals.** Nothing consumes audio yet (the bridge only shows it in the overlay). Candidate: optional `audio` subset (`rms_envelope`, `beat`, `onset_strength`) on `splat`, splat radius/brightness scaled by envelope with a decaying boost on `beat`, one tunable strength constant.
- **Split `AudioCapturer`** into capture, analysis, emission (P2 #15). Only if the audio module keeps growing.
- **Shared `SocketIOEmitter` base** for `sender.py` and `emitter.py`. Worth doing only if a third producer appears; until then the two mirror the same pattern.
- **Composite canvas-capture robustness** (§3.3.C first bullet). No concrete fix proposed in the review.

---

## File ownership matrix

Shows which WP owns each contested file per phase (a dash means untouched). Use this to check for collisions if you reshuffle WPs.

| File | Ph 1 | Ph 2 | Ph 3 | Ph 4 |
|---|---|---|---|---|
| `electron/main.js` | 1.1 | 2.6 | 3.2 | n/a |
| `electron/hub.js` | 1.1 | 2.1 | 3.2 | n/a |
| `server.js` | 1.1 | n/a | 3.2 | n/a |
| `cobeart-app/package.json` | 1.1 | 2.1 | 3.2 | n/a |
| `public/fluid-bridge.js` | 1.7 (orientation fields) | 2.4 | 3.2 | n/a |
| `public/fluid/script.js` | 1.7 (head-tilt block, angular-velocity reads) | 2.4 (handlers) | 3.3 (registry, `drawBackground`) | n/a |
| `public/composite/composite.js` | 1.4 | n/a | 3.2 | n/a |
| `packagesender/sender.py` | 1.3 (call site), then 1.7 (orientation fields) | 2.2 | 3.1 | n/a |
| `packagesender/metrics.py` | 1.3, then 1.7 (angular velocity, dropouts) | 2.2 (frame-time clock, dt floor) | 3.1 | n/a |
| `optitrackclient/start_client.py` | 1.7 | 2.2 | 3.1 | n/a |
| `cobeart/simulator/` | 1.9 | n/a | 3.1 | n/a |
| `audiocapture/capture.py` | 1.6 | 2.3 | 3.1 (`main()`), 3.4 (`AudioCapturer`) | n/a |
| `audiocapture/beat/` | n/a | n/a | 3.4 | n/a |
| `audiocapture/emitter.py` | n/a | 2.3 | 3.1 | n/a |
| `pyproject.toml` | 0.1 | n/a | 3.1 | n/a |
| `CLAUDE.md` | 0.1 | n/a | n/a | 4.2 |

Critical path: D5/D6 → Phase 1 → 2.2 → 3.1 → 4.3. Total: 21 agent commits across 4 phases, plus the Phase 0 master commit.
