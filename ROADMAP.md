# CoBeART Refactor Roadmap

Source: `ARCHITECTURE_REVIEW.md` (2026-10-03). Section refs like (§3.2.A) point into that review.

**Status (2026-10-18):** Phases 0 to 4 (the original refactor) and 5 to 7 (module separation, one contract, swappable sources, Splat only; see "Foundation phases" below) are done. This file is the backlog (Deferred) and decision log. Earlier phases added **Carry-over from Phase N** notes to later WPs: pass them to the agent with the WP block.

## How this roadmap is meant to run

- **One merge per phase.** Each phase is merged into `main` (fast-forward, then pushed) as one series of commits, one per work package (WP) plus any follow-ups. Solo project: no PR (decided after Phase 1).
- **One agent per WP, one commit per agent.** Each agent works in its own worktree branched from the integration branch head *as it stood when the phase began*.
- **File ownership is exclusive within a phase.** No two WPs in the same phase touch the same file. This is what makes the commits merge cleanly. If an agent finds it must edit a file it does not own, it stops and reports to the master instead of editing.
- **Phases are strictly ordered.** Phase N+1 branches only after Phase N is merged and green.

### Master agent protocol (per phase)

1. Create integration branch `refactor/phase-N` from the previous phase's merged head, and **check it out in the main checkout before spawning**: with `worktree.baseRef: "head"` (see Worktree caveats), worktrees branch from the main checkout's current HEAD.
2. Spawn one agent per WP with `isolation: "worktree"` and the agent type and `model` named in the WP's **Model** line (agent types: `glsl-shader-expert` for 3.3, `code-documentation-auditor` for 4.2, a general agent otherwise) (the master itself runs on Opus; without an explicit `model`, agents inherit the master's model), passing: the WP block below (including any **Carry-over from Phase 1** note), the owned-file list, the relevant review sections, the exact expected base commit, the commit-trailer line naming **that agent's model** (e.g. `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` for a Sonnet agent, not the master's model), and a **unique port** (WP 1.1 → 3101, 1.2 → 3102, and so on). Agents run in parallel, so any hub they start for manual checks uses `PORT=<their port> node server.js` and the simulator's `--url http://127.0.0.1:<their port>`. Nobody but the master uses port 3000. Agents do not launch the Electron window (`main.js` hardcodes 3000 until Phase 3, and parallel windows are unreviewable); they verify visuals in a browser against their own `server.js` port using the Playwright tools, and attach screenshots to their report.
3. Each JS-touching agent runs `npm ci` in its worktree's `cobeart-app/` (the lockfile is committed, so every worktree gets identical versions). An agent that changes `package.json` commits the updated `package-lock.json` with it. npm 12 blocks install scripts unless listed in `package.json` `allowScripts`; Electron's postinstall (binary download) is approved for `electron@37.4.0` only, so an agent that changes the Electron version must re-approve it (`npm install-scripts approve electron`). After any `npm ci` in the **main** checkout, the human re-runs `sudo chown root:root` and `sudo chmod 4755` on `cobeart-app/node_modules/electron/dist/chrome-sandbox` (Ubuntu 24.04 blocks Electron's namespace sandbox otherwise).
4. When all agents finish, review each diff (do not just trust the report: run its tests, read the risky parts), then cherry-pick each WP commit into `refactor/phase-N` in WP order. Then check `ss -ltnp | grep -E ':3(0|1)[0-9]{2}'` and stop any hub an agent left running (Phase 1: two agents reported stopping hubs that were still up). Remove merged agent worktrees and branches after the merge.
5. **Gate review (read-only).** Before running the gate, spawn the reviewers named in the phase gate against the merged `refactor/phase-N` head (no worktree needed: they cannot edit). They report findings only. The master triages: real defects go back to the owning WP agent as a follow-up amend of that WP's commit; style nits are dropped. Reviewers use their own definitions' model (Sonnet).
6. Run the phase gate (listed per phase). If a WP fails the gate, send it back to that agent rather than patching it in the master.
7. The human runs the visual parts of the gate in the real Electron window on port 3000. **Before handing over, the master verifies every expectation against the code** and gives exact commands (with all required flags, e.g. `start-audio-client --emit`) plus what should and should not be visible. Phase 1 lesson: the human was told to expect audio-driven pulsing and clap transitions that the code could not produce. Use throwaway driver scripts in the scratchpad when no simulator scenario exercises a behaviour. Then fast-forward `main` and push (with the human's go-ahead).

### Worktree caveats (read before Phase 0)

- **Worktree base (verified, Phase 1):** by default Claude Code branches worktrees (subagent ones too) from the remote default branch `origin/HEAD`, not the local checkout. Each developer's `.claude/settings.local.json` must set `"worktree": {"baseRef": "head"}` so agents start from the checked-out phase branch. Every agent prompt starts with a **Step 0**: check `git rev-parse --short HEAD` equals the expected phase base and that `ROADMAP.md` exists; if not, stop and report (do not reset).
- Worktrees are created from committed state. **Untracked files do not exist in a worktree.** After Phase 0 the only untracked path is `licenses/`, which agents never need.
- `madmom` is installed once into the shared project environment in Phase 0 (D4, D8), so agents never rebuild it. Worktrees share that environment.
- **Editable-install trap (verified):** the shared env has `cobeart` installed in editable mode from the main checkout. Running `python -m ...` or a script from the worktree root picks up the worktree's `cobeart/` (verified). The installed console scripts (`start-audio-client`, `start-optitrack-client`) do **not**: they always import the main checkout. In worktrees, agents use `python -m cobeart.audiocapture.capture` and `python -m cobeart.optitrackclient.start_client` instead; the `start-*` names in this roadmap mean those module invocations when run from a worktree.
- **`soundcard` argv crash (verified, `soundcard` 0.4.6 is latest):** importing `soundcard` reads `sys.argv[1]` when `sys.argv[0]` is `-c` or `-m`. So it crashes under `python -c`, and under `python -m cobeart.audiocapture.<module>` **with no arguments** (the package `__init__` eagerly imports `capture` while `sys.argv` is still `['-m']`). WP 1.6 removed the eager package import, so `python -m cobeart.audiocapture.<module>` with no arguments now works. `python -c` that imports `soundcard` still crashes: put ad-hoc Python in a scratch script file.
- **JS hub in worktrees:** a worktree has no `node_modules`. Run `npm ci` in its `cobeart-app/` before anything that boots the hub, including `pytest cobeart` (the simulator integration tests skip without it).
- **Playwright:** the MCP browser (Firefox) is installed in `~/.cache/ms-playwright/`. Agents share one browser, so console errors from another agent's page can appear. Screenshots land in `.playwright-mcp/` (gitignored).
- Agents call the interpreter directly, `~/miniconda3/envs/splat-env/bin/python`, never `conda run` (it masks output). The project CLAUDE.md says the same as of Phase 0.
- **Phase 2 lessons (master and agents):**
  - Never `pkill -f "<pattern>"` with a pattern that also appears in the running shell command (it kills its own shell) or that matches other agents' hubs (`node server.js`). Stop a hub by the PID from `ss -ltnp | grep ':<port> '`.
  - The hub's reject log is rate-limited per kind (5 s), so run each `--fault` check against a fresh hub.
  - Merged `/viewer` frames with audio are about 48 KB (the 16x128 spectrum). A Python `/viewer` watcher cannot decode 240 of them per second and falls behind, so time-based checks must use the hub's `timestamp` fields, not receipt time. A newly connected viewer also gets the hub's stored `lastFrame` first, however old.
  - Playwright screenshots must be saved under the repo (`.playwright-mcp/`); the scratchpad is outside its allowed roots.
  - Parallel agents share one scratchpad directory: each uses its own subfolder.
  - The Splat view only paints hands, feet and tracked objects (`trackedBodyParts` in `fluid/script.js`, from `body_map.json`); the head and other IDs drive patterns but draw nothing. `--simulate` sends IDs 0-2, so Splat shows two bodies and Ink shows three.
- **Phase 3 lessons (master and agents):**
  - A queued `SendMessage` reaches an agent only between tool calls. An agent blocked in one long command (a test loop, or a poll waiting for a background job) never sees it. Agents must cap every test run with `timeout`, and never poll a background job with an unbounded `until` loop. If an agent stalls, inspect its processes (`ps`, `/proc/<pid>/task/*/wchan`) and stop the blocking command by PID; `SIGINT` to a hung pytest prints where it was stuck.
  - A worktree re-based onto a newer phase head (`git checkout --detach refactor/phase-N`) needs `npm ci` again when that head changed `package.json`, or the hub integration tests fail.
  - Use `npm install` (not `npm ci`) in the main checkout when only a dependency was added: it keeps `chrome-sandbox` root-owned, so the human need not redo the `sudo` steps.
  - `/viewer` frames are emitted only when a motion frame arrives. Any audio or beat check that watches `/viewer` needs a motion source too (`start-simulator --scenario orbit --audio none`; `--audio none` so simulated audio does not overwrite the real mic).
  - `start-simulator` fails at once if no hub is up; start Electron (or `node server.js`) first. Console scripts (`start-*`) need `conda activate splat-env`.
  - No simulator scenario raises a hand above `z_threshold`, so composite transitions need a driver (Phase 3 used a throwaway scratchpad script). A simulator `transitions` scenario would make this a regular gate step.
  - Report distributions (median, percentiles), not a sorted head, when checking tempo: the first gate readout showed only the lowest values and looked like a 3-5 BPM error.
  - WP 3.4 runs the beat models in a spawned child process (`beat/process.py`), because madmom's Python steps held the GIL and stalled capture by up to 0.94 s under load. Any program that starts `AudioCapturer` must do so under `if __name__ == "__main__":`.

### Shared conventions (every agent follows these)

- **Python logging:** `logging.getLogger(__name__)`, no `print` in hot paths. Per-frame output only at `DEBUG`.
- **JS debug gating:** per-frame `console.log` only behind `const DEBUG = new URLSearchParams(location.search).has('debug') || window.__COBEART_CONFIG__?.debug === true;`. Phase 3 wires `__COBEART_CONFIG__`; before that, the query param alone works.
- **Contract version:** `schemaVersion: 1` on `frame` and `audio_metrics`.
- **Tests:** Python tests live next to the package under `tests/` and run with pytest. JS tests use `node --test` under `cobeart-app/test/` (`npm test` runs `node --test "test/**/*.test.js"`; Node 22 rejects a bare directory).
- **Lint:** `flake8 cobeart` is clean as of Phase 1 (repo `.flake8`: max line 120, vendored NatNet SDK files excluded). Every WP keeps the files it touches clean.
- **Contract is the source of truth.** `contract/*.schema.json` is merged. Read it, not the review, for field names and units (e.g. `ID`, ms `timestamp`, quaternion `qx..qw`, angular velocity `wx..wz`). `contract/CONTRACT.md` explains validation (Python `referencing.Registry`; Node `ajv/dist/2020` with `allowUnionTypes`).
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
- **Carry-over from Phase 1:** use `ajv/dist/2020` (the default `Ajv` is draft-07 and lacks `unevaluatedProperties`) with `allowUnionTypes: true`, and load all three schemas (`frame` `$ref`s `audio_metrics`). The hub currently checks only four audio fields. The simulator's `--fault malformed|wrong-version|nan` produces payloads the schemas reject; `nan` on audio is caught only as invalid JSON (JSON Schema `number` accepts NaN), so decide how the hub treats it.
- **Scope:** Validate `frame` and `audio_metrics` with `ajv` against `contract/*.schema.json` at the `/ingest` and `/audio` boundaries; reject and log-once (rate-limited) on failure; fail loudly on `schemaVersion` mismatch. Drop `lastAudioData` from merged frames when older than `audioMaxAgeMs` (a `createHub` option, default 500; Phase 3 sources it from config). Remove the "back-compat" comment.
- **Test:** extend the smoke test: malformed frame is not forwarded; stale audio is omitted; wrong `schemaVersion` is rejected.
- **Accept:** `npm test` passes.

### WP 2.2: Python frame producer without import side effects (§3.1.A, §4.3, P0 #3)
- **Model:** Opus. Lifecycle, threading, dependency injection, and a synthetic NatNet source that must produce data consistent with the D5 transform.
- **Owns:** `cobeart/packagesender/sender.py`, `cobeart/optitrackclient/start_client.py` (sender wiring, frame callback and `start()`), `cobeart/packagesender/metrics.py`, `cobeart/packagesender/tests/`
- **Carry-over from Phase 1:** `start_client.py` now has `receive_rigid_body_frame(id, position, rotation, tracking_valid)` delegating to `tracked_bodies.apply_rigid_body` (dropouts remove the body from the frame). `MetricsTracker.update(id, x, y, z, qx, qy, qz, qw)` returns quaternion-based `BodyMetrics` with a 0.25 s dropout gap and a zero-seeded window. `sender.py` still: connects at import, has no `schemaVersion`, sends `timestamp` in seconds, uses `int()` on positions (keep, contract wants integer mm), and has a broad `except` that drops the whole frame. NatNet's per-frame callback receives `data_dict["timestamp"]` (seconds since Motive start): use it as the metrics clock.
- **Scope:** No connection at import. `PayloadSender` takes injected `socketio.Client`, `MetricsTracker`, URL, and framerate; owns a background reconnect loop mirroring `audiocapture/emitter.py`; `connect()`/`stop()` lifecycle. Use `get_socketio_url()` instead of the hardcoded `SIO_URL`. Add `schemaVersion: 1`. Replace prints with logging; log emit failures once per disconnect episode. `start_client.py` builds the sender inside `start()`, not at module level. Payload `timestamp` in epoch milliseconds per the contract.
- **Velocity timing (Phase 1 gate review findings #2, #3):** today metrics are computed when a payload is *sent*, with `time.time` at send time, behind a `>= 1/framerate` gate that aliases against 240 Hz mocap jitter (dt alternates ~4/8 ms; an unchanged pose can be resent, giving a zero then a ~2x velocity spike). Instead, update `MetricsTracker` once per mocap frame from the NatNet frame callback, using the NatNet frame `timestamp` (`data_dict["timestamp"]`, seconds since Motive start) as the clock, so dt is mocap time; the send-rate gate only decides which computed state is emitted. Add a minimum dt floor (configurable, default 0.5 ms): below it, skip the velocity update rather than divide. The synthetic source supplies its own frame timestamps.
- **Offline pipeline seam:** add a `MotionSource` protocol to `start_client.py`. `NatNetClient` is one implementation. `SyntheticNatNetSource` is the other: it calls the same `receive_rigid_body_frame(id, position, rotation, tracking_valid)` / `receive_new_frame` callbacks with OptiTrack-native data (meters, quaternions, OptiTrack axes) from a simple scripted path. `start-optitrack-client --simulate` uses it. This exercises the real transform (1.7), metrics (1.3) and sender path with no hardware, which the socket-level simulator (1.9) skips.
- **Test:** `import cobeart.packagesender.sender` and `import cobeart.optitrackclient.start_client` with no server running; built payload validates against `contract/frame.schema.json`; a synthetic body moving at a known speed produces `abs_vel` within tolerance after passing through transform and metrics; with jittered frame arrival and a send rate below the mocap rate, velocity stays constant for constant motion (no 0/2x alternation); a dt below the floor does not produce a velocity spike.
- **Accept:** pytest passes; `start-optitrack-client --simulate` starts before Electron, connects once Electron is up, and drives the splat view.

### WP 2.3: Python audio producer: contract and error surfacing (§4.3, P1 #8)
- **Model:** Sonnet. Error surfacing in an existing, well-structured module.
- **Owns:** `cobeart/audiocapture/capture.py` (payload construction and capture loop only; leave `__init__` defaults and the beat path to WP 3.4), `cobeart/audiocapture/emitter.py`, `cobeart/audiocapture/tests/`
- **Carry-over from Phase 1:** the audio payload has no `schemaVersion` yet. The audio schema requires the beat fields on every message; `capture.py` always sends them (`has_beat()` returns `(False, None, None)` when detection is off). `rms_db` is a normalized 0..1 value, not decibels; `spectrum_config.freq_max` reports 20000 even when `sample_rate / 2` is lower (note only).
- **Scope:** Add `schemaVersion: 1` to the metrics payload. Replace the capture-loop `except Exception: pass` with logged failure plus a surfaced error state (the thread must not die silently; the main loop should exit non-zero or report). Replace the bare `pass` blocks in `emitter.py` with log-once-with-backoff for transient emit/connect errors.
- **Test:** audio payload validates against `contract/audio_metrics.schema.json`; a capture source that raises surfaces the error.
- **Accept:** pytest passes.

### WP 2.4: Bridge forwards the contract, not a re-enumeration (§3.3.A/B, P0 #2, P1 #10)
- **Model:** Opus. Consolidates three message handlers in the 2,768-line `fluid/script.js`; behavior regressions are easy to miss.
- **Owns:** `cobeart-app/public/fluid-bridge.js`, `cobeart-app/public/fluid/script.js` (message-handler regions only, lines ~1644, ~1677, ~1802)
- **Carry-over from Phase 1:** the bridge already forwards `qx..qw` and `wx..wz` (WP 1.7) but still emits `id`, `absVel`, `normVel` and a constant `color`; the splat schema wants `ID`, `abs_vel`, `norm_abs_vel`, `color` ([0,1] RGB). `fluid/script.js` reads `m.id` about 41 times and `m.normVel`; the comment near line 1678 wrongly says x and y are 0..1 (they are arena mm). The head-tilt block (~2595) has an ungated per-frame `console.log` and the Pattern 9 header comment (~2272) still says "roll". The bridge logs every received frame and tick ungated, which makes the composite page laggy with DevTools open.
- **Scope:** Bridge builds `splat` messages per `contract/splat.schema.json` with a single mapping function instead of copying 15 fields inline; drop the `ID`/`id` defensive fallback (contract settles `ID`). Gate all per-frame logs behind `DEBUG`. Consolidate the three `message` listeners in `fluid/script.js` into one dispatcher keyed on `type`.
- **Accept:** splat visualization behaves as before; no per-frame logs without `?debug`.

### WP 2.5: Molten and ink conform to the splat contract (§3.3.B)
- **Model:** Sonnet. Follows the pattern WP 2.4 sets, against a fixed schema.
- **Owns:** `cobeart-app/public/molten/molten.js`, `cobeart-app/public/ink/ink.js`
- **Carry-over from Phase 1:** molten and ink read `m.id`; switch to `ID` per `splat.schema.json`.
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
- **Carry-over from Phase 2:**
  - `start_client.py` resolves the hub URL from `--url` / `COBEART_SOCKETIO_URL` (validated by `hub_url` at parse time) and passes it to `PayloadSender`; `get_socketio_url()` in `audiocapture/utils.py` is still used by the audio emitter. Replace both with `Settings`. `PayloadSender(reconnect_interval_s)` and `MetricsTracker(min_dt_s)` are constructor args ready for config.
  - `sender.py` builds the client with `json=StrictJson` (`allow_nan=False`). python-socketio applies a `json=` codec process-wide, not per client.
  - The simulator dies with `BadNamespaceError` after `--fault nan` (the hub drops the connection on the NaN literal). Make it log and exit cleanly, or reconnect.
  - `capture.py` `main()` still uses `print`, the interactive device prompt (no `--device` flag; the gate pipes a number into stdin), and `select_audio_device` calls bare `exit()` when no microphone exists. `main()` already exits 1 when `capturer.capture_error` is set (WP 2.3).
  - Audio producers should also serialize with `allow_nan=False`, like the motion sender.
- **Scope:** Frozen `Settings` dataclass tree loaded once from `config/cobeart.yaml` (path overridable by `COBEART_CONFIG`), `COBEART_SOCKETIO_URL` still overrides the URL. Delete `streaming.py` and `get_socketio_url`; inject settings into sender, tracker, emitter, client, and simulator (which uses the arena bounds, body map IDs and rates instead of its own defaults). Add `pyyaml`. Register `start-simulator = "cobeart.simulator.__main__:main"`. In `capture.py` `main()`, make emission the default (the Phase 3 gate runs `start-audio-client` with no flags; keep a `--no-emit` for local metering) and build `AudioCapturer` from the `audio` settings section (including `beat_detection`); leave the class itself and its DSP defaults to WP 3.4 (not in scope per review).
- **Test:** loader parses the real file; missing required key raises a specific error naming the key.
- **Accept:** pytest passes; both clients start using only the config file.

### WP 3.2: Electron and frontend read the shared config (§4.2, §3.3.C, P1 #7, #10)
- **Model:** Sonnet. Same pattern as 3.1 on the JS side.
- **Owns:** `cobeart-app/electron/main.js`, `cobeart-app/electron/hub.js`, `cobeart-app/server.js`, `cobeart-app/package.json`, `cobeart-app/public/fluid-bridge.js`, `cobeart-app/public/composite/composite.js`, `cobeart-app/public/molten/molten.js`, `cobeart-app/public/ink/ink.js`, `cobeart-app/public/fluid/script.js` (arena normalization in `handleSplatMessage` only, ~1812-1817; WP 3.3 owns the registry regions), `cobeart-app/test/`
- **Carry-over from Phase 2:**
  - `main.js` has a pure `resolveViewUrl(shader, usePerfMode)`, a module-level `PORT`, and `DEBUG = process.env.COBEART_DEBUG === '1'` gating DevTools: move `PORT` and `DEBUG` to config. Startup failures exit 1 via a `.catch` on `app.whenReady()`.
  - `createHub` takes `audioMaxAgeMs` and `now`; it reads `contract/*.schema.json` at runtime from `../../contract`, which any future Electron packaging must include. The hub caps messages at 256 KiB (`MAX_PAYLOAD_BYTES`), rejects producer-sent `audio` and duplicate IDs, and has no CORS. There is no body-count cap: add `maxItems` from `max_num_objects` (contract or config).
  - `fluid-bridge.js` has `SPLAT_COLOR` as a module constant and creates its overlay only when `DEBUG`. In debug mode it appends `?debug` to every iframe `src` (one reload) so embedded sims see the flag; when `__COBEART_CONFIG__` lands, iframes must get it too (the sims read only their own `window`).
  - `composite.js:~668` posts old-shape splats (`id`, x/y as 0..1, no `norm_abs_vel`) inside `try{}catch(_){}`: conform them to `splat.schema.json`.
- **Scope:** Load `config/cobeart.yaml` once in the main process (`js-yaml`), pass port and `audioMaxAgeMs` to `createHub`, window size to `BrowserWindow`, inject the frontend subset as `window.__COBEART_CONFIG__` (replacing `__SOCKET_PORT__`; also serve it at `/config.json` so `npm run web` pages get it). `composite.js` uses injected arena, z-threshold, clap distance, and `show_interactive_elements` (config sets it `false`, code today has `true`: an intended visible change); trim `rightHandHistory` like `leftHandHistory`; fix the debug splats (~667) to match `splat.schema.json`; delete its commented-out blocks; gate per-frame logs. `fluid-bridge.js` uses injected framerate and color.
- **Arena in the sims (added at Phase 3 start):** `molten.js:~80`, `ink.js:~343` and `fluid/script.js:~1813` hardcode `arena_x = arena_y = 3000`. Read the arena from the injected config instead. Config gives `[min, max]` per axis, so normalize with min/max (x flipped) rather than assuming a symmetric arena. The sims run in iframes and read only their own `window`, so pick one way for every page (iframe or standalone) to get the config.
- **Accept:** changing arena in the YAML changes composite, Splat, Molten and Ink normalization with no JS edit; `npm test` passes.

### WP 3.3: One background-shader architecture (§3.3.D/E, P1 #9)
- **Model:** Sonnet, spawned as the `glsl-shader-expert` agent type (`.claude/agents/glsl-shader-expert.md`, which sets `model: sonnet`).
- **Owns:** `cobeart-app/public/backgrounds/**`, `cobeart-app/public/fluid/script.js` (registry compile region ~1008-1011 and `drawBackground` ~1410-1438; WP 3.2 owns the arena lines in `handleSplatMessage`)
- **Scope:** Apply D2:
  - Delete `backgrounds/kaleidoscope/` and `backgrounds/particle-orbits/`.
  - Registry entry contract becomes `{ name, fragmentShader, setUniforms(gl, program, ctx) }`, documented with JSDoc in `registry.js`. `ctx` is `{ time, resolution, audio, rigidbodies }`, where `audio` and `rigidbodies` follow `contract/` (null/empty when absent). Move each shader's current uniform values out of the `if (bgDef.name === ...)` chain into its own `setUniforms`; delete the chain. Visual output of the three existing backgrounds must not change.
  - `fluid/script.js` keeps the latest audio and rigid-body state from the message dispatcher built in WP 2.4 and passes it as `ctx`.
- **Accept:** each background looks identical to before (side-by-side screenshot against the pre-phase build); a test background that maps `ctx.audio.rms` to brightness visibly reacts to `python -m cobeart.simulator --audio beat` (then remove it before committing).
- **Accept:** every registry entry renders inside the fluid sim; each standalone visualization loads at its new path.

### WP 3.4: Beat detection on by default (D4)
- **Model:** Opus. Real-time threading, madmom latency, and a convergence test that must be robust rather than tuned to pass.
- **Owns:** `cobeart/audiocapture/capture.py` (`AudioCapturer` class only, not `main()`), `cobeart/audiocapture/beat/`, `cobeart/audiocapture/tests/`
- **Carry-over from Phase 1:** with beat detection off (today's default) `tempo_bpm` stays null forever, which the schema cannot tell apart from "not yet stable"; turning detection on by default resolves it.
- **Carry-over from Phase 2:** the capture thread now records failures in `capture_error` (WP 2.3); the beat processing thread has no equivalent and should surface errors the same way. `AudioCapturer.__init__` still silently falls back to the requested `sample_rate` when `sc.default_samplerate()` raises. `AudioCapturer(mic=...)` accepts an injected `AudioSource` Protocol, so the click-track test needs no device.
- **Scope:**
  - `AudioCapturer(enable_beat_detection=True)` by default. Remove the code that silently turns beat detection off when madmom is missing (`capture.py:~143`), so a missing madmom fails at startup with a clear error.
  - In `beat/detector.py`, replace the lazy "optional" import with a normal top-level import, since madmom is now required.
  - Make sure the detector/predictor work never blocks the capture loop (no dropped chunks), and that the payload always carries `beat`, `tempo_bpm` and `beat_timestamp` per the contract.
  - Log the beat configuration at startup.
- **Test:** a new test feeds a synthetic click track (generated in the test at a known BPM) through `AudioCapturer` without a device, and asserts that the detected tempo converges within ±2 BPM and that beat timestamps line up within a stated tolerance.
- **Accept:** `start-audio-client` with a real mic and music playing emits `beat: true` pulses and a stable `tempo_bpm` (report the BPM, the actual track tempo, and the lock-in time); the capture thread keeps up (no backlog growth over 5 minutes); CPU use is reported.
- **Not in scope:** visuals reacting to beats. The beat fields reach every simulation and background `ctx` through the contract, so wiring them into visuals is later feature work.

**Phase 3 gate review:** `code-optimizer-reviewer` on WP 3.4 (beat detection inside the capture loop: no blocking, no dropped chunks).

**Phase 3 gate:** Phase 2 gate plus `grep -rn "3000" cobeart-app/public cobeart --include=*.js --include=*.py --include=*.html --exclude-dir=tests --exclude-dir=node_modules` shows no arena or port literals outside `config/` (unrelated values such as the `headDepth` default are fine); `start-audio-client` with no extra flags emits live beat fields from a real mic and music; every `/viewer` frame's `audio` carries the beat fields.

---

## Phase 4: Guardrails and documentation (4 parallel WPs)

Depends on everything above.

**Phase 4 decisions (2026-10-17, agreed with the human before branching):**
- **Baseline at `c8ffe3a`:** pytest 155 passed (none skipped), `flake8 cobeart` clean, `npm test` 13/13. Phase base for agents is the `refactor/phase-4` commit that records these decisions.
- **Roles:** the master writes all roadmap documentation and is the gatekeeper (diff review, gate, `ci.yml` security read; no separate reviewer agents). Every WP, including the CLAUDE.md WP, is implemented by an agent on the model its WP names. Ports: 4.1 → 3401, 4.2 → 3402, 4.3 → 3403, 4.4 → 3404.
- **Field-list fitness rule dropped.** The payload field set legitimately appears in 14 producer, consumer and test files, so a grep heuristic is all noise. Shape drift is already caught by the tests that validate real payloads against `contract/*.schema.json` (sender, simulator, capture, hub smoke).
- **CI and `soundcard`:** importing `soundcard` connects to PulseAudio (`soundcard/pulseaudio.py`, module-level `_PulseAudio()`), and `capture.py` imports it via `audiocapture/utils.py`. CI installs `pulseaudio` and starts it with a null sink before pytest; no code change.
- **`print(` rule** keeps the original scope (`packagesender/`, `transform.py`). Prints elsewhere are intentional console output (NatNet menu, meter, device prompt, `viz/`, `tools/`).
- **`still` through the real metrics path:** `--simulate` only walks circles, and `SyntheticNatNetSource.step()` is hard-wired to `CirclePath`. The e2e test defines its own `MotionSource` (move, then stop) and wires it to the real `FramePipeline`, `MetricsTracker`, `PayloadSender` and hub. No production edit.
- **One home for cross-process tests:** WP 4.3 moves `cobeart/simulator/tests/test_integration.py` into `tests/e2e/` and owns `pyproject.toml` `testpaths`, so plain `pytest` runs everything, as CI does.
- **CI on the public fork:** decided at merge time to fast-forward `main` locally and push it, letting the push trigger CI (a CI failure would be fixed by a follow-up commit on `main`); no remote phase branch.
- **Pulled from Deferred:** the `start-simulator` no-hub traceback (fails fast with one line, not retry: a dev and test tool, and a mistyped `--url` must not hang) and the stale `frame.schema.json` `audio` description (WP 4.4). The rest of Deferred stays deferred.
- **After Phase 4:** `ROADMAP.md` stays as the backlog and decision log; `ARCHITECTURE_REVIEW.md` stays as a historical record; CLAUDE.md's "Refactor in progress" section shrinks to a pointer to the Deferred list.

### WP 4.1: Fitness functions and CI (P2 #17)
- **Model:** Sonnet. CI config and a grep-based checker.
- **Owns:** `.github/workflows/ci.yml` (new), `scripts/fitness.mjs` (new), `scripts/fitness.test.mjs` (new)
- **Carry-over from Phase 3:** the arena/port literal check must allow `fluid/script.js` `headDepth` (default 3000 mm, not an arena literal) and the `http://127.0.0.1:3000` examples in docstrings (`settings/config.py`, `start_client.py`). Arena literals in `fluid/script.js` patterns (~2008-2517) are now derived from config as half-extents. CI needs madmom (pinned git commit, builds from source) and `npm ci` before pytest, or the hub integration tests skip or fail. The beat tests spawn a child process and use shared memory; the audiocapture `conftest.py` has a 120 s per-test hang guard.
- **Scope:**
  - CI: triggers `push` (all branches), `pull_request`, `workflow_dispatch`. One job, `ubuntu-latest`, Python 3.12, Node 22. Top-level `permissions: contents: read`; actions pinned to a version; no secrets. Cache pip (including the madmom build). Install `pulseaudio` and start it with a null sink before pytest. `ELECTRON_SKIP_BINARY_DOWNLOAD=1` for `npm ci` (tests boot `node server.js`, never Electron). Steps: `pip install -e ".[test]"`, `npm ci` in `cobeart-app/`, `pytest` (after WP 4.3 this includes `tests/e2e/` via `testpaths`), `flake8 cobeart`, `npm test`, `node scripts/fitness.mjs`, `node --test scripts/fitness.test.mjs`. Set a job `timeout-minutes`.
  - Fitness script fails (exit 1, one line per violation with file:line and rule) if: anything other than `cobeart-app/electron/hub.js` constructs `new Server(`; arena or port literals appear outside `config/` (the Phase 3 gate grep, with the allowlist above); `print(` appears in `cobeart/packagesender` or `cobeart/optitrackclient/transform.py`. Each rule is a pure function over file text, exported for the test file; the CLI walks the repo.
- **Test:** `scripts/fitness.test.mjs` feeds each rule a violating and a clean snippet, including the allowlisted `headDepth` line and the docstring URLs.
- **Accept:** fitness script passes on the phase base; the test file passes; deliberately reintroducing one violation locally makes the script fail; CI green on the pushed phase branch (master).

### WP 4.2: Bring CLAUDE.md in line with reality (P2 #16)
- **Model:** Sonnet, spawned as the `code-documentation-auditor` agent type (sets `model: sonnet`). Its default instructions encourage editing docstrings and comments anywhere; the prompt must state that it may edit `CLAUDE.md` only and should report any stale docstrings it finds instead of fixing them.
- **Owns:** `CLAUDE.md`
- **Carry-over from Phase 1:** CLAUDE.md says `max_num_objects` defaults to 6; code and `config/cobeart.yaml` say 10. The composite's hand-up transitions need z > 2700 mm (see the on-site checklist in Deferred).
- **Carry-over from Phase 3:**
  - Commands: `start-simulator` exists; `start-audio-client` emits by default (`--no-emit`, `--device INDEX`; `--emit`, `--enable-beat-detection`, `--socketio-url` are gone). `COBEART_DEBUG` is gone: DevTools follow `debug` in the YAML. `COBEART_CONFIG` selects the config file for Python and Electron alike; `PORT` overrides the hub port; `COBEART_SOCKETIO_URL` overrides the Python hub URL. Console scripts need `conda activate splat-env`.
  - Architecture: `cobeart/settings/config.py` (frozen `Settings`) replaced `streaming.py`; `electron/config.js` loads and validates the YAML and the hub injects `window.__COBEART_CONFIG__` into every served HTML page (and `/config.json`). The bridge posts `{type: 'audio'}` to sims on change (`contract/CONTRACT.md`). Beat detection runs madmom online in a spawned child process (`beat/process.py`, `tracker.py`, `tempo.py`, `predictor.py`, `clock.py`); tempo range 55-215 BPM; `has_beat()` consumes each beat, so only one caller may use it.
  - Testing: `/viewer` checks need a motion source (see Phase 3 lessons); the simulator needs the hub up first.
- **Carry-over from Phase 4 decisions** (these land in parallel, so document them by these names): CI in `.github/workflows/ci.yml`; `node scripts/fitness.mjs` (architecture fitness checks); cross-process tests in `tests/e2e/` collected by plain `pytest`; `start-simulator` with no hub logs one line and exits 1. Stale content seen at the phase base: `npm run dev`, `streaming.py`, `max_num_objects` 6, the Kaleidoscope and Particle Orbits sections, MediaFoundation patches, `molten-branch`/`music-branch`, the hardware-only Testing section.
- **Scope:** Update commands (`start-audio-client`, `start-simulator`, `npm test`, fitness script), architecture (hub module, contract, config file), background system (registry only, `setUniforms` contract; remove the Kaleidoscope and Particle Orbits sections), test status and CI, Linux as the primary platform, and the conda invocation guidance. Shrink "Refactor in progress" to a short pointer to `ROADMAP.md` (backlog: Deferred; decision log: D1-D9 and phase decisions).
- **Accept:** every command in CLAUDE.md runs as written (master re-checks the Phase 4 ones after merge). Replace the hardware-only "Testing" section with the offline workflow (simulator, `--simulate`, e2e), and keep the hardware checklist as a separate "on-site validation" list.

### WP 4.3: End-to-end offline integration test (§4.4)
- **Model:** Opus. Orchestrating Node and Python subprocesses with timing assertions; flakiness is the main risk.
- **Owns:** `tests/e2e/` (new, repo root), `cobeart/simulator/tests/test_integration.py` (moved into `tests/e2e/`), `pyproject.toml` (`testpaths` only: `["cobeart", "tests"]`)
- **Scope:** A pytest suite with one shared fixture that boots the real hub (`node server.js` as a subprocess on an ephemeral port, with the config path overridden where needed), drives both simulation paths (socket-level `cobeart.simulator`, and the full Python pipeline via `start-optitrack-client --simulate`), subscribes to `/viewer` with a `python-socketio` client, and asserts:
  - every received frame validates against `contract/frame.schema.json`
  - merged audio is present and then disappears after `--audio-stop-after`
  - fault-injected payloads never reach `/viewer`
  - IDs from `shuffled` and `missing-body` arrive intact
  - a body that moves and then stops drives `norm_abs_vel` to 0 through the real metrics path: a test-defined `MotionSource` (OptiTrack-native poses) wired to the real `FramePipeline`, `MetricsTracker`, `PayloadSender` and hub (see Phase 4 decisions)
  - the existing `test_integration.py` checks keep passing from their new home
- **Carry-over from Phase 3:** `server.js` loads and validates `config/cobeart.yaml` (or `COBEART_CONFIG`) at startup and exits 1 on an invalid file; `PORT` overrides the configured port. The simulator raises `ConnectionError` if the hub is not up yet (WP 4.4 turns this into a clean exit 1), while `start-optitrack-client --simulate` retries; wait for `/health` before starting the simulator. The hub rejects frames with more than `tracking.max_num_objects` bodies. Validated Phase 3 gate driver scripts (`/viewer` watcher with schema validation and tempo percentiles; composite transition driver) can serve as starting points; they lived in the master scratchpad and are not in the repo.
- **Robustness:** every wait is bounded; time checks use hub `timestamp` fields, not receipt time (see Phase 2 lessons); every subprocess is cleaned up on failure.
- **Accept:** `pytest tests/e2e` passes 5 times in a row locally, and under `taskset -c 0`; plain `pytest` collects it; no hardware, browser, or Electron window needed; green in CI.

### WP 4.4: Simulator no-hub exit and schema wording (Deferred, Phase 2/3 findings)
- **Model:** Sonnet. Two small fixes.
- **Owns:** `cobeart/simulator/__main__.py`, `contract/frame.schema.json` (the `audio` description only; no `schemaVersion` bump), `cobeart/simulator/tests/test_cli.py` (new)
- **Scope:** `start-simulator` with no hub at the URL logs one line (`no hub at <url>, start Electron or npm run web first`) and exits 1, with no traceback. It fails fast; it does not retry. Remove "once implemented" from the `audio` description (staleness is implemented, WP 2.1).
- **Test:** `main()` against an unused port raises `SystemExit` with code 1 within a few seconds and logs the one line.
- **Accept:** pytest passes; flake8 clean.

**Phase 4 outcome (2026-10-17):** WP commits 4.1 `75705f1`, 4.2 `b065fcc`, 4.3 `21c171d`, 4.4 `ea1366b`, each reviewed and re-run by the master; no follow-ups needed. Local gate: `pytest` 165 passed (none skipped), `flake8 cobeart tests` clean, `npm test` 13/13, fitness 0 violations and its tests 10/10, `tests/e2e` 11 passed 5/5 normally (21-24 s) and 5/5 under `taskset -c 0` (20 s), no hub left running; first CI run (39057368181, 2 m 46 s) green with 165 passed, none skipped, including the PulseAudio step and the clean-runner madmom build; the CLAUDE.md Phase 4 commands run as written (`start-simulator` with no hub exits 1 with one ERROR line after its INFO startup lines).
- **Lessons:** WP 4.3 first saw 229/240 frames arrive: `python-engineio` `Client.disconnect()` closes the websocket without draining its send queue, so a producer's last few messages are lost. Cross-process tests end a stream on a marker frame sent over a new connection rather than counting sent frames. Schema-validating a ~48 KB audio frame with `jsonschema` costs about 15 ms, which is where most e2e time goes. Agents that stay inside their owned files and get explicit done-when commands needed no follow-up round this phase.

**Phase 4 gate review:** the master (diff review of every WP, security read of `ci.yml`).

**Phase 4 gate:** Phase 3 gate plus `pytest` (now including `tests/e2e`), `flake8 cobeart`, `npm test`, `node scripts/fitness.mjs` and `node --test scripts/fitness.test.mjs`; `pytest tests/e2e` 5 times in a row, normally and under `taskset -c 0`; CI green on the pushed `refactor/phase-4`; every command in CLAUDE.md runs as written.

---

## Foundation phases (5 to 7): module separation, one contract, swappable sources

Agreed with the human on 2026-10-18 in a design interview. Goal: audio, input and visuals are independent modules; every world receives the same contract and cannot tell where input came from (OptiTrack on site, the mouse or the simulator at home); worlds never mix; Splat is the only world kept.

### Decisions D10-D19

| ID | Decision |
|---|---|
| D10 | **Splat is the only world.** Molten, Ink and Composite are deleted with everything that exists only for them: the visualization dialog, `frontend.composite`, the `cursor` message path, the `moltenFrame`/`inkFrame` bridge lookups. Composite's "mixing worlds" is not wanted: one world runs at a time. |
| D11 | **Module layout follows the architecture.** Python: `cobeart/input/optitrack/` (NatNet, transform, role mapping, sender; was `optitrackclient/` + `packagesender/`), `cobeart/audio/` (was `audiocapture/`), `cobeart/simulator/`, `cobeart/settings/`, and `cobeart/common/` for code both producers share (`StrictJson`). `cobeart.audio` and `cobeart.input` never import each other; the simulator imports neither. Console script names do not change. Frontend: `public/host/` (host page, bridge, border toggle), `public/sources/` (browser input sources), `public/worlds/<name>/` (one folder per world, Splat's backgrounds inside it). |
| D12 | **One contract per side of the hub.** Sources send `pose_frame` (pose only: no velocities, no audio, no source tag) on `/ingest`. The hub sends `frame` and `audio` on `/viewer`, and the bridge posts the very same objects to the world (`{type: 'frame', frame}`, `{type: 'audio', audio}`). Worlds only ever see `frame` and `audio`, whatever the source. `splat.schema.json` is deleted; `type: "optitrack"` is removed. All schemas move to `schemaVersion: 2`. |
| D13 | **Bodies are keyed by role, not ID.** `bodies` is an object keyed by role, so a role occurs at most once by construction (one performer). Roles are a closed enum in the schema: `left_hand`, `right_hand`, `head`, `chest_front`, `chest_back`, `left_foot`, `right_foot`, `stick`. Objects get names, not a generic `object` role. Adding a role is a version bump. `ID` is removed from the contract. A missing role key means that body is not tracked in that frame. |
| D14 | **The hub computes derived motion values.** Velocities, angular velocity, `abs_vel` and `norm_abs_vel` are computed in the hub (a Node port of `MetricsTracker`, per role) from the **source clock**: `pose_frame.source_time_s` (seconds, monotonic, arbitrary epoch per source: mocap frame time, `performance.now()/1000`, simulator time). State for a role resets on first sighting, a gap above `metrics.max_gap_s`, or a clock going backwards (as today). The outgoing `frame.timestamp` is hub receipt time (epoch ms). The Python metrics code and the simulator's own velocity code are deleted. |
| D15 | **Audio is its own stream end to end.** The hub validates `audio_metrics`, stamps its receipt `timestamp`, and emits it on `/viewer` as event `audio`; it never merges audio into frames, so audio reaches worlds with no motion source running. The hub replays nothing to a new viewer (no last frame, no last audio). The bridge posts audio on change, and posts `{type: 'audio', audio: null}` once when the latest audio is older than `frontend.audio_max_age_ms` (stale policy moves from hub to bridge). |
| D16 | **Sources.** OptiTrack maps Motive streaming IDs to roles with `tracking.roles` in the YAML (unmapped IDs are dropped, warned once; `max_num_objects` and `body_map.json` go). Mouse (D17) and both simulators (`start-optitrack-client --simulate` exercises the NatNet adapter; `start-simulator` is the headless scenario/fault/audio source) send `pose_frame`. The `shuffled` scenario is dropped (order no longer exists). |
| D17 | **Mouse source** (`public/sources/mouse.js`, a layer in the host page above the world, enabled by `source=mouse`). A body exists only while a mouse button is held; it acts as `sources.mouse.role` (default `right_hand`) with a fixed identity quaternion. z starts at `sources.mouse.z_initial_mm`, each wheel notch moves it by `z_step_mm`, clamped to `arena.z`, and it stays until the wheel moves again. While held it sends `pose_frame` at `sources.mouse.rate_hz` (continuous like mocap, so a still mouse reads as still, not as a dropout); on release it sends one frame with no bodies. An always-on readout (mouse mode only) shows role and z. Operator keys keep working: the host forwards `keydown` into the world iframe (same origin). |
| D18 | **Canonical top-down view.** Worlds that draw the arena in 2D use one projection, so screen-based sources line up: the viewer looks down on the arena with arena **x_max at the left edge** and **y_min at the top edge** (Splat's existing mapping in `handleSplatMessage`). The mouse source applies its inverse over the host viewport. Recorded in `contract/CONTRACT.md`. A world may ignore it, at the cost of the mouse not lining up. |
| D19 | **Worlds and launcher.** A world is any `public/worlds/<name>/index.html` that listens for `frame`/`audio` messages; the hub discovers them and serves `/worlds.json`. The host page `/host/?world=<name>&source=live|mouse` embeds that world in an iframe and runs the bridge (and the mouse layer for `source=mouse`). The Electron launcher and the browser index page (`/`) both choose world and source. Worlds own their look: Splat's colour moves into Splat (`frontend.splat_color` goes). Splat drops its own mouse, touch, spacebar and `cursor` input and keeps operator keys (`P` pause, `G` backgrounds; `B` border is the host's). |

Later option, not scheduled: move Splat's gesture patterns (clap, jump, hands-up...) out of the world into a gesture module that emits higher-level events as their own stream.

### Execution model (Phases 5-7)

- **Roles:** the master (Opus, holds the design context) writes all roadmap and WP documentation, briefs and supervises agents, and is the gatekeeper (diff review, re-running every success test, the phase gate). Every WP is implemented by one agent on the model its WP names, with `isolation: "worktree"`. Agents never edit `ROADMAP.md` or `CLAUDE.md`; the master updates both at each phase end.
- The master protocol, worktree caveats and shared conventions above still apply, with these updates: integration branches are `refactor/phase-5` etc.; ports are 3500 + WP number (5.1 → 3501, 6.0 → 3600, ...); the console-script names in a worktree mean `python -m <module>` with the **new** module paths once Phase 5 is merged; after Phase 5 merges, the master re-runs `pip install -e ".[test,viz]"` in the main checkout (entry points moved).
- **Success tests:** each WP lists **Must pass** checks. They are the agent's definition of done; the agent reports each one's literal result, and the master re-runs them before cherry-picking.
- **Intermediate breakage:** Phase 6 changes the contract on both sides at once (the contract rule: old and new versions are never accepted side by side). WP 6.0 lands the schemas and config first; 6.1-6.4 then run in parallel against it. Individual 6.x commits may fail cross-WP tests; the Phase 6 gate is what must be green.

---

## Phase 5: Cleanup and layout (2 parallel WPs)

Pure deletion and moves; no behaviour change for Splat, the hub or the producers. The contract is untouched (still v1).

**Baseline at `bab2e43` (main):** pytest 165 passed, `npm test` 13/13, fitness clean.

### WP 5.1: Remove the other worlds and move the frontend into host/worlds
- **Model:** Sonnet. Deletion and file moves; the risk is a dangling reference.
- **Owns:** `cobeart-app/public/**` except `body_map.json` (stays at `public/body_map.json` until Phase 6; the simulator reads that path), `cobeart-app/electron/main.js`, `cobeart-app/electron/config.js`, `cobeart-app/test/**`, `config/cobeart.yaml`, `config/cobeart.schema.json`, `contract/CONTRACT.md` (consumer rows only), `contract/splat.schema.json` (description wording only).
- **Scope:**
  - Delete `public/molten/`, `public/ink/`, `public/composite/`. Remove `frontend.composite` from the YAML, its schema and `frontendConfig`; remove the `cursor` handler (`handleCursorMessage` and its dispatch) from Splat; drop `moltenFrame`/`inkFrame` and the "no iframe found, create one" fallback from the bridge.
  - Move with `git mv`: `public/fluid/` → `public/worlds/splat/`; `public/backgrounds/` → `public/worlds/splat/backgrounds/`; `public/index.html` → `public/host/index.html` (iframe `src="/worlds/splat/index.html"`; drop the leftover "Live Tracking Debug" heading, badges and `<pre id="out">` if nothing reads them); `fluid-bridge.js` → `public/host/bridge.js`; `border-control.js` and `shared-border.css` → `public/host/`. Fix every path that referenced the old locations (script and import paths in Splat, the registry, the host page).
  - New `public/index.html`: a minimal page that redirects to `/host/` (Phase 7 replaces it with the launcher).
  - `electron/main.js`: no dialog; load `/host/` directly. DevTools follow `debug` (the ink exception goes).
  - Do not touch `fluid/script.js` beyond the `cursor` removal and path fixes (Phase 6 rewrites its input).
- **Must pass:**
  1. `npm test` green (update any test that referenced removed config keys or pages).
  2. `node scripts/fitness.mjs` 0 violations (the `headDepth` allowlist line matches on content, so the move is fine).
  3. `grep -rniE "molten|ink/|inkFrame|composite|cursor|fluid-bridge|/fluid/" cobeart-app/public cobeart-app/electron cobeart-app/server.js config contract` returns nothing (word "ink" in unrelated prose excepted; report any hit you keep and why).
  4. Playwright against `PORT=3501 node server.js` plus `start-simulator --scenario orbit --audio beat --url http://127.0.0.1:3501 --duration 20`: `/` lands on `/host/`, Splat paints the orbiting hands, background toggle (`G`) still cycles, no console errors. Screenshot attached.
  5. `pytest tests/e2e` green (the hub still serves the same contract).

### WP 5.2: Python packages follow the module boundaries
- **Model:** Sonnet. Mechanical moves plus one new fitness rule.
- **Owns:** `cobeart/**`, `tests/**`, `pyproject.toml`, `.flake8`, `.github/workflows/ci.yml`, `scripts/fitness.mjs`, `scripts/fitness.test.mjs`.
- **Scope:**
  - `git mv`: `cobeart/optitrackclient/` and `cobeart/packagesender/` → `cobeart/input/optitrack/` (one package; tests merge into its `tests/`; `contract.py` test helper moves with them); `cobeart/audiocapture/` → `cobeart/audio/`. `StrictJson` moves to `cobeart/common/strict_json.py` and both the sender and the audio emitter import it from there. Keep the vendored NatNet files byte-identical (only their internal import lines change).
  - Update every import, logger name in tests (`caplog` logger strings), `monkeypatch.setattr` paths, `[project.scripts]` (same script names, new module paths), `.flake8` excludes, the CI comment path, `tests/e2e` imports and `python -m` invocations.
  - `fitness.mjs`: retarget `no-print-hot-path` to `cobeart/input/optitrack/sender.py`, `metrics.py` and `transform.py`; add rule **`module-boundaries`**: a file under `cobeart/audio/` must not import `cobeart.input`; under `cobeart/input/` must not import `cobeart.audio`; under `cobeart/simulator/` must import neither. Add violating and clean cases to `fitness.test.mjs`.
  - Do not change behaviour; delete nothing but the emptied packages.
- **Must pass:**
  1. `pytest` (whole repo) green with the same count as baseline (165), none skipped.
  2. `flake8 cobeart tests` clean.
  3. `node scripts/fitness.mjs` 0 violations; `node --test scripts/fitness.test.mjs` green, including the new rule's cases.
  4. `python -m cobeart.input.optitrack.start_client --simulate --url http://127.0.0.1:3502` streams to a hub on 3502 (`curl /health` shows ingest 1 and a fresh `lastFrameAge`), and Ctrl+C exits 0.
  5. `python -m cobeart.audio.capture --no-emit` starts (device prompt or meter) without import errors; `grep -rn "optitrackclient\|packagesender\|audiocapture" --include=*.py --include=*.toml --include=*.mjs --include=*.yml --include=.flake8 .` returns nothing.

**Phase 5 outcome (2026-10-18):** WP commits 5.1 `c3eaa69`, 5.2 `3804f81` (cherry-picked onto `refactor/phase-5`), each diff reviewed by the master. Merged-head gate: pytest 165 passed (none skipped), `flake8 cobeart tests` clean, `npm test` 13/13, fitness 4 rules / 0 violations, fitness tests 14/14, no old module path left; after `pip install -e . --no-deps` the `start-*` scripts import the new paths, `start-optitrack-client --simulate` streams (`/health` ingest 1) and `start-audio-client --no-emit` meters.
- **Lessons:** the ownership split missed that `cobeart/settings/config.py` (5.2) parses the `frontend.composite` section 5.1 deleted from the YAML; the strict loader rejects both a missing and an unknown key, so neither WP alone could be green. The master moved the three removals into 5.2 mid-flight. When a WP deletes a config key, the owner of every loader of that key belongs in the same WP or gets an explicit carry-over. The auto-mode classifier refused 5.2's `git commit` in its worktree ("Modify Shared Resources") although 5.1's went through; the human approved the master committing the staged, reviewed work.
- **Carry-over to WP 6.3:** `worlds/splat/index.html` and `host/index.html` both load `host/border-control.js` (the border is created twice; it belongs to the host, D19). Operator keys reach Splat only when its iframe has focus.

**Phase 5 gate:** all Must-pass checks re-run on the merged head; pytest 165, `flake8 cobeart tests`, `npm test`, fitness and its tests; `pip install -e ".[test,viz]"` in the main checkout, then `start-optitrack-client --simulate` and `npm start` show Splat painting (human, port 3000).

---

## Phase 6: Contract v2 (WP 6.0 first, then 4 parallel WPs)

Depends on Phase 5. Implements D12-D16 and the Splat side of D19.

### WP 6.0: Contract v2 schemas and config (runs alone, before 6.1-6.4)
- **Model:** Opus. Every other Phase 6 WP codes against this; precision matters more than speed.
- **Owns:** `contract/**`, `config/cobeart.yaml`, `config/cobeart.schema.json`, `cobeart/settings/**`, `cobeart-app/electron/config.js`, `cobeart-app/test/config.test.js`, `tests/contract/` (new).
- **Scope:**
  - `pose_frame.schema.json` (new): `{schemaVersion: 2, source_time_s: number >= 0, bodies: {<role>: {x, y, z, qx, qy, qz, qw}}}`; positions integer mm as today; `bodies` may be empty; closed everywhere.
  - `frame.schema.json`: `{schemaVersion: 2, timestamp: epoch ms (hub receipt), bodies: {<role>: {x, y, z, qx..qw, vx, vy, vz, wx, wy, wz, abs_vel, norm_abs_vel}}}`; no `type`, no `audio`, no `ID`. The role enum is defined once (`$defs`) and referenced by `pose_frame`; the pose fields are defined once and extended for `frame` (as `splat` extends `rigidBodyFields` today). Keep the existing field descriptions (units, D5 orientation), minus ID and merge wording.
  - `audio_metrics.schema.json`: `schemaVersion` 2; document that on `/viewer` it carries the hub `timestamp` (required there; express it as the `audio` message shape however is cleanest, e.g. a `viewer_audio` `$def`).
  - Delete `splat.schema.json`. Rewrite `CONTRACT.md`: one shape per side of the hub, the producer/consumer table (sources: OptiTrack sender, simulator, mouse source; hub; bridge; worlds), the bridge messages `{type:'frame', frame}` and `{type:'audio', audio | null}`, the role rule, and D18 (canonical top-down view).
  - Config, final shape: `tracking.roles` (Motive ID → role, default: the current `body_map.json` mapping), remove `tracking.max_num_objects`; `metrics.max_gap_s: 0.25` added (hub reads `metrics.*` from now on); `hub.audio_max_age_ms` → `frontend.audio_max_age_ms` (drop the `hub` section if empty); remove `frontend.splat_color`; add `sources.mouse: {role: right_hand, z_initial_mm: 1200, z_step_mm: 50, rate_hz: 60}`. Role values validated against the enum in both loaders. Python `Settings` and Node `frontendConfig` reflect this (frontend gets `arena`, `bridge_framerate`, `audio_max_age_ms`, `sources`, `debug`); Node exports the hub-side values (`metrics`) for WP 6.1.
- **Must pass:**
  1. `tests/contract/test_schemas.py` (new): for each schema, valid fixtures pass and these fail: wrong `schemaVersion`, unknown role, extra field on a body, `ID` present, `audio` inside a frame, velocity fields in a `pose_frame`, `timestamp` in seconds, non-unit-range quaternion component.
  2. Config tests on both sides: the shipped YAML loads; an unknown role in `tracking.roles` or `sources.mouse.role` fails with an error naming the key; a removed key (`max_num_objects`) present fails as unknown.
  3. `flake8` clean on owned files. (Other suites are expected to fail until 6.1-6.4 land; list which ones fail and why in the report.)

**WP 6.0 outcome (2026-10-18):** commit `5dea9a8` on `refactor/phase-6`, reviewed by the master; `tests/contract` 59 passed, settings + contract 100 passed, `config.test.js` 13/13. This is the base for 6.1-6.4. Settled while implementing (binding for 6.1-6.4 and 7.2):
- **Events:** sources emit event **`pose_frame`** on `/ingest` (not `frame`); `/audio` keeps `audio_metrics`; `/viewer` emits `frame` and `audio`.
- **Schemas** (`$id` prefix `https://cobeart.invalid/contract/`, register all four): `pose_frame.schema.json`; `frame.schema.json` (also holds `$defs/role`, the only copy of the enum, `$defs/poseFields`, `$defs/body`); `audio_metrics.schema.json` (producer side; v2; fields in `$defs/fields`; **forbids** `timestamp`); `audio.schema.json` (new: the `/viewer` audio = fields + required hub `timestamp`). Positions are **integer** mm in both directions (the mouse source must round). `audio: null` on the bridge message is defined in `CONTRACT.md`, not a schema.
- **Python test helpers:** `tests/contract/schemas.py` exports `contract_validator(name)`, `contract_registry()`, `load_schema`, `load_fixture`, `SCHEMA_NAMES`; valid fixtures in `tests/contract/fixtures/{pose_frame,frame,audio_metrics,audio}.json`. Reuse them instead of new validators.
- **Node config** (`electron/config.js`): `loadConfig`, `resolvePort`, `trackingRoles(config) -> Map<number, role>`, `hubConfig(config) -> {metrics: {max_vel, history_window, max_gap_s}}`, `frontendConfig(config) -> {debug, arena, bridge_framerate, audio_max_age_ms, sources: {mouse: {role, z_initial_mm, z_step_mm, rate_hz}}}`. `min_dt_s` (0.0005 s) stays a hub constant.
- **Python `Settings`:** `tracking.roles: Mapping[int, Role]`; `metrics.max_gap_s`; `frontend.audio_max_age_ms`; `sources.mouse`; `hub` and `tracking.max_num_objects` and `frontend.splat_color` are gone; `contract_roles()` reads the enum from the contract.
- **Known breakage at the base, by owner:** 6.1: `server.js:12-13`, `main.js:21-22` (`config.hub...`, `max_num_objects`), `hub.js` (v1 schemas, merge, `maxBodies`), `hub.smoke` tests 14, 15, 21. 6.2: `start_client.py` and `FramePipeline` (`max_num_objects`), `simulator/scenarios.py` and `__main__.py` (`BODY_MAP_PATH`, `load_body_map`, `max_num_objects`), `simulator/tests/helpers.py`, `input/optitrack/tests/test_synthetic_source.py`, and three conftests that load the deleted `splat.schema.json` (`input/optitrack/tests/contract.py:21`, `audio/tests/conftest.py:31`, `simulator/tests/conftest.py:15`). 6.3: `host/bridge.js:5` (`CONFIG.splat_color` throws), `worlds/splat/script.js` (`/body_map.json` fetch), `backgrounds/registry.js` comments. 6.4: `tests/e2e/conftest.py:15` (`splat`), `test_simulator_streams.py:34` (`settings.hub`), `test_motion_pipeline.py:96` (`max_num_objects`).

### WP 6.1: Hub velocity stage and separate streams
- **Model:** Opus. Numerical port plus the hub's core loop.
- **Owns:** `cobeart-app/electron/hub.js`, `cobeart-app/electron/motion.js` (new: the velocity stage), `cobeart-app/server.js`, `cobeart-app/electron/main.js` (createHub arguments only), `cobeart-app/test/**` except `config.test.js`.
- **Scope:** `/ingest` validates `pose_frame` (v2) and emits exactly one `/viewer` `frame` per valid pose_frame, with derived fields from `motion.js` (port of `MetricsTracker`: per-role state, source-clock dt, `max_gap_s` reset, clock-backwards reset, `min_dt_s` hold, history-window max hold, `max_vel` cap, quaternion angular velocity; read the Python at the phase base including `cobeart/input/optitrack/tests/test_metrics.py`). `/audio` validates and emits `audio` on `/viewer` with the hub `timestamp`. No merge, no replay to new viewers, no duplicate-ID or max-bodies checks (the schema covers both). `/health` keeps client counts and ages. Non-finite derived values must not be emitted (drop the frame with a rate-limited log).
- **Must pass (node tests):**
  1. Every case of the Python `test_metrics.py` ported and passing against `motion.js`.
  2. Two pose_frames for `right_hand` with `source_time_s` 0.1 s apart and x 100 mm apart, sent back to back: the second `/viewer` frame has `vx` ≈ 1000 mm/s (proves source clock, not receipt time).
  3. A body moving then still for a full history window reaches `norm_abs_vel` 0; a gap > `max_gap_s` resets velocity to 0.
  4. Rejected and never forwarded: v1 payload, unknown role, velocity fields in a pose_frame, NaN packet.
  5. Audio arrives on `/viewer` as `audio` with no `/ingest` client connected; frames carry no `audio` key.
  6. A viewer connecting after frames and audio were sent receives nothing until the next ingest/audio message.
  7. `npm test` green; fitness clean.

### WP 6.2: Python producers send pose_frame
- **Model:** Sonnet. Mostly deletion and reshaping along a fixed spec.
- **Owns:** `cobeart/**` except `cobeart/settings/**`; `cobeart-app/public/body_map.json` (delete it).
- **Scope:** `PayloadSender` builds `pose_frame` (roles from `tracking.roles`, `source_time_s` = mocap frame time; keeps send-rate throttling and the finite check). `FramePipeline` drops unmapped IDs with one warning each (replacing `max_num_objects`). Delete `metrics.py` and its tests (6.1 ports them first; read-only for 6.1). `SyntheticNatNetSource` default paths use Motive IDs that `tracking.roles` maps (hands and head as today). Simulator: scenarios produce role-keyed pose_frames with `source_time_s`; delete its velocity code and `load_body_map`/`BODY_MAP_PATH` (roles come from settings); drop `shuffled`; `faults` still produce malformed / wrong-version (now v1) / NaN payloads. Audio: `schemaVersion` 2 in `capture.py` and the simulator's audio messages.
- **Must pass:**
  1. pytest under `cobeart/` green; every payload a test produces validates against `pose_frame` (motion) or `audio_metrics` v2 (audio).
  2. A test that a Motive ID missing from `tracking.roles` is dropped and logged once; a mapped ID arrives under its role.
  3. `grep -rnE "\bvx\b|abs_vel|norm_abs_vel|MetricsTracker|body_map|max_num_objects" cobeart` returns nothing outside comments that explain the hub owns them.
  4. `flake8 cobeart` clean; fitness clean.

### WP 6.3: Bridge posts whole frames; Splat reads roles
- **Model:** Opus. `worlds/splat/script.js` is ~2700 lines with a dozen gesture patterns keyed on body IDs; the rewrite must keep every pattern's behaviour.
- **Owns:** `cobeart-app/public/host/**`, `cobeart-app/public/worlds/**`.
- **Scope:**
  - Bridge: listens for `frame` and `audio` on `/viewer`; every tick posts the latest unposted frame as `{type: 'frame', frame}`; posts `{type: 'audio', audio}` on change and `{type: 'audio', audio: null}` once when the latest audio is older than `frontend.audio_max_age_ms` (re-arms when audio returns). Posts to the world iframe only (no self-post: nothing at host level consumes it). The debug overlay lists bodies by role.
  - Splat: one `frame` handler that iterates `frame.bodies` by role; every `bodyPartsIndex[...]`/`m.ID` lookup becomes a role key; the object logic uses `stick`; per-body pointers keyed by role; colour from a constant in Splat (current `[1.0, 0.6, 0.2]`); `latestRigidBodies` becomes the latest `frame.bodies` (backgrounds' `ctx.rigidbodies` → `ctx.bodies` keyed by role; update the registry doc comment and any background that reads it). `audio: null` clears `latestAudio`. Delete the mouse, touch, spacebar-splat handlers; keep `P` and `G`. Single-body globals (`latestNormVel`, `latestAngVel`, `computeVelocityDissipation` reading `zdict[1]`, i.e. Motive ID 1 = `right_hand`) keep their current meaning by role.
- **Must pass:**
  1. `grep -nE "\.ID\b|bodyPartsIndex|body_map|mousedown|touchstart|'splat'|\"splat\"" public/worlds/splat/script.js public/host/*.js` returns nothing.
  2. A table in the report: each old ID/name reference (file:line at the phase base) → its role-key replacement.
  3. Playwright against `PORT=3603 node server.js` (WP 6.1 is not merged in this worktree, so drive the page with a scratch script that `postMessage`s v2 `frame`/`audio` messages into the world iframe, built from the 6.0 schemas): hands paint; a `stick` near `right_hand` turns yellow (pattern 10); `audio: null` causes no error; no console errors. Screenshots attached.
  4. A scratch Node or browser check that a captured bridge message validates against `frame.schema.json` (and the audio one against the viewer audio shape).

### WP 6.4: End-to-end suite and frontend fitness rule
- **Model:** Sonnet. The harness exists; this re-targets it to v2.
- **Owns:** `tests/e2e/**`, `scripts/fitness.mjs`, `scripts/fitness.test.mjs`.
- **Scope:** Update the harness to watch `/viewer` `frame` and `audio` events separately, and rewrite the tests to v2. Add fitness rule **`worlds-receive-only`**: files under `cobeart-app/public/worlds/` must not contain `io(`, `socket.io`, `/viewer` or `/ingest` (worlds only receive postMessages).
- **Must pass** (against the merged Phase 6 head; in the worktree, write them against the 6.0 schemas and expect them to fail until 6.1/6.2 land; the master re-runs them after merge):
  1. Every `/viewer` frame validates against `frame` v2 and every `audio` against the viewer audio shape.
  2. `--scenario none --audio beat`: audio arrives on `/viewer`, no frames. `--scenario orbit --audio none`: frames arrive without any `audio` key.
  3. Every `--fault` and a v1 payload never reach `/viewer`.
  4. `missing-body`: the missing role is absent from `bodies` in the affected frames.
  5. Orbit speed check: a simulated orbiting body's `abs_vel` matches the scenario's analytic speed (2πr/T) within 5 % (proves the hub port end to end).
  6. A body that moves then stops reaches `norm_abs_vel` 0 through the real `FramePipeline`, `PayloadSender` and hub.
  7. `--simulate` CLI streams frames whose role keys are the `tracking.roles` values of the synthetic IDs.
  8. A viewer connecting mid-stream gets no frame older than its connect time.
  9. `pytest tests/e2e` 5 times in a row green, and under `taskset -c 0`; fitness and its tests green (new rule has violating and clean cases).

**Phase 6 outcome (2026-10-18):** commits on `refactor/phase-6`: 6.0 `5dea9a8`, 6.1 `52f01bc`, 6.2 `5f002f0`, 6.4 `d1b568a`, 6.3 `9e2a92d`, 6.4 follow-up `10a791b`; every diff reviewed by the master (6.1's `motion.js` line by line against the Python). Merged-head gate: pytest 243 passed (none skipped), `flake8 cobeart tests` clean, `npm test` 42/42, fitness 5 rules / 0 violations, fitness tests 16/16, `tests/e2e` 15 passed 5/5 by the agent plus 3/3 by the master (about 36 s each) and 6/6 under `taskset -c 0`; master browser check of simulator -> hub -> bridge -> Splat (`hands` + `beat`): overlay lists roles and live audio, both hands paint, no console errors.
- **Settled by the WPs:** `/ingest` also rejects the v1 event `frame` with an actionable log line. A source clock going backwards resets every role. `--bodies N` takes the first N roles of `tracking.roles` in Motive-ID order (orbit, still, edge); `hands` and `missing-body` always use `left_hand`, `right_hand`, `head`. `PayloadSender(client, url, framerate, failure, reconnect_interval_s)` and `FramePipeline(sender, roles, failure)`. Splat handles bodies in the fixed enum order (the old ID order), so pattern state ordering is unchanged; its centred-arena maths (patterns 6, 9, 11, 12) now use the arena bounds. The host forwards operator keys into the world (`host/keys.js`) and owns the border alone.
- **Lessons:** WP 6.4 coded against interfaces landing in parallel with every assumption isolated in one adapter section; the one mismatch (simulator `MotionConfig`) was a three-line follow-up. Asking each WP to report the decisions it made and the exact interfaces it exposes let the master check cross-WP fit before merging.
- **Carry-over to Phase 7:** `createHub({port, host, publicDir, metrics, frontendConfig, now})` (7.1 adds the `/worlds.json` route inside it). `host/keys.js` already forwards keydown into `#worldFrame` (7.2 keeps it; D17 needs no new forwarding). The host page currently hardcodes `/worlds/splat/index.html` and loads `bridge.js`, `keys.js`, `border-control.js`. The mouse source must send integer mm and event `pose_frame`; `frontendConfig.sources.mouse` carries `role`, `z_initial_mm`, `z_step_mm`, `rate_hz`.

**Phase 6 gate:** whole-repo `pytest`, `flake8 cobeart tests`, `npm test`, fitness + tests, `tests/e2e` 5x; CI green on push. Human (port 3000): `npm start` with `start-optitrack-client --simulate`, then with `start-simulator --scenario hands --audio beat`; Splat paints; the debug overlay (`?debug`) shows roles.

---

## Phase 7: Launcher and mouse source (2 parallel WPs)

Depends on Phase 6. Implements D17-D19.

### WP 7.1: World discovery and launcher
- **Model:** Sonnet.
- **Owns:** `cobeart-app/electron/hub.js` (the `/worlds.json` route only), `cobeart-app/electron/main.js`, `cobeart-app/public/index.html`, `cobeart-app/test/worlds.test.js` (new).
- **Scope:** `/worlds.json` lists the subfolders of `public/worlds/` that contain an `index.html`, sorted, as `{worlds: [{name}]}`. The browser `/` page fetches it and lets you pick a world and a source (Live / Mouse), then opens `/host/?world=<name>&source=<live|mouse>`. Electron: a dialog with one button per world plus a "Mouse input" checkbox, then loads the same URL.
- **Must pass:** node test: `/worlds.json` with a temp public dir containing two worlds and one folder without `index.html` lists exactly the two; `npm test` green; Playwright: `/` lists `splat` and navigates to the right URL for both sources; screenshot.

### WP 7.2: Host page selects the world; mouse source
- **Model:** Sonnet.
- **Owns:** `cobeart-app/public/host/**`, `cobeart-app/public/sources/**` (new), `cobeart-app/test/mouse.test.js` (new).
- **Scope:** The host page reads `world` (validated against `/worlds.json`; unknown or missing shows a visible error listing the valid names, no silent default) and `source`. For `source=mouse` it loads `sources/mouse.js` (D17): a transparent layer over the iframe capturing pointer and wheel; screen → arena per D18 (inverse of Splat's mapping, read from `worlds/splat/script.js`); its own Socket.IO client on `/ingest` sending `pose_frame` v2; the readout; `keydown` forwarded into the iframe window. Keep the screen → arena mapping and the z update as pure exported functions so they are testable.
- **Must pass:**
  1. Node test: mapping corners (top-left → `(x_max, y_min)`, bottom-right → `(x_min, y_max)`), z clamped at both arena ends, wheel steps accumulate; built pose_frames validate against the schema (held and released forms).
  2. Playwright on `PORT=3702 node server.js`, `/host/?world=splat&source=mouse`: a drag paints under the cursor (screenshot with cursor position noted); releasing stops painting; wheel changes the readout z; `G` cycles backgrounds while the mouse layer is active; `/health` shows one ingest client.
  3. `/host/?world=nope` shows the error; `npm test` green; fitness clean.

**Phase 7 outcome (2026-10-18):** commits on `refactor/phase-7`: 7.1 `b62d962`, 7.2 `f113b5a`, each diff reviewed by the master. Merged-head gate: pytest 243 passed, `flake8 cobeart tests` clean, `npm test` 51/51, fitness 5 rules / 0 violations, fitness tests 16/16, `tests/e2e` 15 passed 2/2. Master browser check: launcher -> Splat + Mouse -> `/host/?world=splat&source=mouse`; small circles around (200, 600) paint a splat centred at about (205, 585) (D18 mapping holds); six wheel notches up move the readout from 1200 to 1500 mm. CLAUDE.md rewritten by the master for the new layout, contract, commands and workflow; stale `__pycache__`-only folders of the pre-Phase-5 packages removed from the main checkout.
- **Settled by the WPs:** `listWorlds(publicDir)` is exported from `hub.js` and shared by the route and the Electron dialog; a missing `world` or `source` is a visible error (no default); the launcher keeps every query param (so `?debug` arrives as `debug=`, which `has('debug')` accepts); the mouse maths is an `.mjs` module so the browser and the CommonJS node tests share it; release fires on `pointerup` with no buttons left, `pointercancel`, document `mouseleave` or window `blur`.
- **Observed:** Splat paints only while a body moves (`pointer.moved`), so a held but still mouse paints nothing, exactly like a still tracked hand. The mouse layer covers the world, so Splat's own GUI ("Open Controls") is not clickable in mouse mode.

**Human gate findings (2026-10-18), handled by WP 7.3:**
- Running `start-simulator --scenario orbit` while painting with the mouse made splats jet along a line through the cursor: both sources sent `right_hand`, so Splat's pointer jumped between them each frame, and the two source clocks (simulator from 0, mouse from `performance.now()`) reset the hub's motion state on every other frame. The contract makes roles unique per frame but nothing stopped two sources at once. **Decision D20:** one source drives the hub at a time.
- After the native Electron dialog (`dialog.showMessageBox` with no parent window) closed, the window did not appear or load until another desktop window event happened (GTK `GLib-GObject ... has no handler` error at that moment; root cause not reproduced, agents cannot drive Electron). Phases 5 and 6, which opened the window directly, did not show this. **Decision D21:** no native dialog; the Electron window opens on the launcher page `/` (one launcher for browser and Electron).
- In mouse mode the layer blocks Splat's own GUI. **Decision D22:** `M` toggles the mouse layer.

### WP 7.3: One active source, launcher in the window, mouse layer toggle
- **Model:** Sonnet.
- **Owns:** `cobeart-app/electron/hub.js` (the `/ingest` handling), `cobeart-app/electron/main.js`, `cobeart-app/public/sources/**`, `cobeart-app/test/**` except `config.test.js`, `tests/e2e/**`.
- **Scope:**
  - **D20, hub:** the active source is the `/ingest` connection whose `pose_frame` was accepted last. A valid `pose_frame` from another connection is dropped (rate-limited log naming both socket ids) while the active one has sent within the last `metrics.max_gap_s` (hub receipt time) and is still connected; otherwise the sender becomes the active source. On every change of active source the motion state is reset (new tracker), so two clocks never mix. `/health` additionally reports which socket id is active (or `null`).
  - **D21, Electron:** remove the dialog and `listWorlds` use from `main.js`; the window loads `${origin}/` (the launcher page) and keeps its options, DevTools-on-debug and the macOS `activate` handler. Closing the window quits as before.
  - **D22, mouse:** `M` toggles the layer. Off: `pointer-events: none` on the layer, any held body is released (one empty `pose_frame`), the readout shows the mouse is off and how to turn it on. On: back to painting. `M` must work whichever document has focus: the host window, and the world iframe after the user clicked Splat's GUI (listen in the iframe window too, same origin, trusted events only so `keys.js` forwarding does not toggle twice; `border-control.js` does the same for `B`).
- **Must pass:**
  1. Node tests: with two `/ingest` clients, B's frames never reach `/viewer` while A is active; B takes over once A has been silent longer than `max_gap_s` (use a small value) and immediately when A disconnects; B's first forwarded frame has all-zero derived values; `/health` names the active socket.
  2. `tests/e2e` green 3 runs in a row (adapt the harness if its end-of-stream marker on a second connection now waits on the rule; keep the fix in one place and explain it).
  3. `main.js` no longer imports `dialog` or `listWorlds`; `node --check` passes.
  4. Playwright: `/` -> Splat + Mouse; drag paints; `M` turns the layer off (readout says so), "Open Controls" opens Splat's GUI and a GUI click works; with focus inside the iframe, `M` turns the layer back on once (not twice) and a drag paints again. Screenshots.
  5. `npm test`, fitness, `pytest` (whole repo) green.

**Phase 7 gate:** Phase 6 gate plus: human runs `npm start`, picks Splat + Mouse, paints, scrolls z (hand-drop radius pattern visibly changes below 1800 mm), and picks Splat + Live with `start-simulator`. CLAUDE.md updated by the master (layout, commands, contract, testing workflow) and every command in it re-run.

---

## Deferred (not scheduled)

Items that Phases 5-7 resolve or make moot (prune when the phase merges): duplicate molten/ink handlers and the triple bridge in composite (5.1); composite `z_threshold` and `tryBindSources` empty catches (5.1); `/viewer` audio bandwidth and the stale `lastFrame` replay (6.1); `ctx.rigidbodies` never pruned (6.3); stale CONTRACT.md hub consumer and `fluid-bridge.js` header (5.1/6.0); `max_num_objects`-related checks (6.0/6.2).

- **Splat pause semantics** (seen at the Phase 6 gate): `P` skips only `step()` (fluid physics); `applyInputs()` still paints, so bodies keep drawing into a frozen fluid. Inherited from the upstream sim. The human sees no use for pause yet; rework it if it becomes relevant (e.g. also skip `applyInputs()` for a full freeze).
- **Record and replay real sessions.** At the next session with the real system, record raw NatNet rigid-body callbacks (ID, position, quaternion, timestamp) to a file, and add a `ReplaySource` that plays them back through the `MotionSource` seam from WP 2.2. That replaces synthetic motion with real movement for regression tests, and is the only offline way to check that the D5 orientation convention matches real performers.
- **On-site validation spikes** (cannot be done offline, per §5): NatNet multicast from the Debian box, and an end-to-end latency check with real bodies.
- **On-site tuning checklist** (found during Phase 1):
  - D5 identity orientation and lean sign: each rigid body must be created in Motive aligned to the global axes with the performer facing OptiTrack +Z.
  - Rigid-body IDs vs `body_map.json` (0-based): Motive Streaming IDs are often 1-based.
  - Composite `frontend.composite.z_threshold` (2700 mm): above the 2500 mm arena ceiling and likely above a raised hand, so hand-up transitions may never fire.
  - Head-tilt palette lean clamp (-40..30 deg in `fluid/script.js`).
  - Dropout gap `max_gap_s` (0.25 s) in `MetricsTracker`.
- **Audio-reactive visuals.** Nothing consumes audio yet. Since Phase 3 the bridge forwards audio to the sims on change, and background shaders receive it as `ctx.audio` in `setUniforms`, so the plumbing is in place. Candidate: optional `audio` subset (`rms_envelope`, `beat`, `onset_strength`) on `splat`, splat radius/brightness scaled by envelope with a decaying boost on `beat`, one tunable strength constant.
- **Split `AudioCapturer`** into capture, analysis, emission (P2 #15). Only if the audio module keeps growing.
- **Shared `SocketIOEmitter` base** for `sender.py` and `emitter.py`. Worth doing only if a third producer appears; until then the two mirror the same pattern.
- **Composite canvas-capture robustness** (§3.3.C first bullet). No concrete fix proposed in the review.
- **Found in Phase 2 (no owner yet):**
  - `molten/index.html` and `ink/index.html` each load `fluid-bridge.js`, so the composite page runs three bridges and three `/viewer` sockets, and molten and ink receive splats twice.
  - Molten and ink duplicate the same splat handler; its `seenIds` set is rebuilt per message and so does nothing.
  - `/viewer` bandwidth: each merged frame carries the full audio spectrum (~48 KB) at 240 Hz, about 11 MB/s per viewer. Consider sending the spectrum at the audio rate only, or a slimmer audio subset on frames.
  - The hub replays its stored `lastFrame` to new viewers regardless of age.
  - A body that disappears from the NatNet stream entirely (rather than arriving with `tracking_valid=False`) is never pruned from `FramePipeline`.
- **Found in Phase 3 (no owner yet):**
  - Beat tempo range is madmom's 55-215 BPM; one song briefly locked to double time (~210 BPM) in its intro. Narrowing it is a product decision (depends on the show's music).
  - With a real mic, `beat_timestamp` is late by roughly the device input latency (`StreamClock` assumes zero).
  - `AudioCapturer.stop_stream()` then `start_stream()` is not restart-safe (beat process, clock and history are not reset). `main()` starts once, so it is unused today.
  - `compute_metrics_payload` runs on the capture thread (avg 3 ms, max ~11 ms per 10 ms block). Measured fine; moving it to an emitter-side worker would make "never blocks capture" structural.
  - `fluid/script.js` Splat patterns (~2008-2517) treat the arena as centred (`arena_x/y` are half-extents); an asymmetric arena would be wrong there.
  - `PayloadSender(reconnect_interval_s)`, `MetricsTracker(min_dt_s)`, `tracking.use_optitrack_client`, `x_rescale`/`y_rescale` have no YAML key or no consumer.
  - `composite.js` `tryBindSources` and `attachKeysToIframe` still have empty `catch (e) {}` blocks.
  - The background registry's `ctx.rigidbodies` keeps the last splat per ID forever (never pruned when a body leaves).
  - Composite transitions were broken since WP 1.4 (seed ring buffer evicted the largest seeds, so the reveal shrank mid-transition); fixed at the Phase 3 gate by spacing seeds over `uAnimSeconds` and never evicting.
- **Found in Phase 4 (no owner yet):**
  - `Simulator.run()` (and `PayloadSender.stop()`) disconnect right after the last emit, losing the final queued messages (engineio does not drain on close); the simulator's `Done:` counts overstate what the hub received. Drain before disconnecting, or document it.
  - Stale comments: `contract/CONTRACT.md` names `electron/main.js` as the hub consumer (it is `electron/hub.js`); `config/cobeart.yaml:1` says "not yet wired up"; `fluid-bridge.js` header says the server is hosted in `main.js`; `capture.py` `main()` comment treats chunk duration 1024/48000 as fixed (both come from the YAML). `README.md` is stale (Python 3.8, Windows install, `main.js` as server).

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
| `public/fluid/script.js` | 1.7 (head-tilt block, angular-velocity reads) | 2.4 (handlers) | 3.2 (arena in `handleSplatMessage`), 3.3 (registry, `drawBackground`) | n/a |
| `public/composite/composite.js` | 1.4 | n/a | 3.2 | n/a |
| `public/molten/molten.js`, `public/ink/ink.js` | n/a | 2.5 | 3.2 (arena) | n/a |
| `contract/CONTRACT.md` | 1.2 | n/a | 3.2 (bridge `audio` message) | n/a |
| `contract/frame.schema.json` | 1.2 | n/a | n/a | 4.4 (`audio` description) |
| `packagesender/sender.py` | 1.3 (call site), then 1.7 (orientation fields) | 2.2 | 3.1 | n/a |
| `packagesender/metrics.py` | 1.3, then 1.7 (angular velocity, dropouts) | 2.2 (frame-time clock, dt floor) | 3.1 | n/a |
| `optitrackclient/start_client.py` | 1.7 | 2.2 | 3.1 | n/a |
| `cobeart/simulator/` | 1.9 | n/a | 3.1 | 4.3 (moves `tests/test_integration.py`), 4.4 (`__main__.py`, `tests/test_cli.py`) |
| `audiocapture/capture.py` | 1.6 | 2.3 | 3.1 (`main()`), 3.4 (`AudioCapturer`) | n/a |
| `audiocapture/beat/` | n/a | n/a | 3.4 | n/a |
| `audiocapture/emitter.py` | n/a | 2.3 | 3.1 | n/a |
| `pyproject.toml` | 0.1 | n/a | 3.1 | 4.3 (`testpaths`) |
| `CLAUDE.md` | 0.1 | n/a | n/a | 4.2 |

Critical path: D5/D6 → Phase 1 → 2.2 → 3.1 → 4.3. Total: 22 agent commits across 4 phases, plus the Phase 0 master commit.
