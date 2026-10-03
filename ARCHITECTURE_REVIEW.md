# CoBeART Architecture Review

Date: 2026-10-03
Reviewer: architecture assessment prior to new development phase
Scope: full stack (Python backend, Electron hub, WebGL frontend)
Platform transition: Windows → Linux (Debian)

---

## 1. Executive Summary

CoBeART works and has produced genuinely impressive visual results. The core data-flow idea
(OptiTrack + audio → Python → Socket.IO hub → WebGL) is sound and worth preserving. However,
the codebase is at the stage where **feature velocity has outrun structural discipline**. It is
a research/demo prototype that has accreted features (beat detection, multiple shaders, compositing,
backgrounds) without the boundaries, contracts, and configuration hygiene needed to sustain another
development phase.

The single most important architectural law here (Richards & Ford, First Law): *everything is a
trade-off*. The project has, so far, consistently traded **long-term maintainability for short-term
visual results**. That was the correct trade for a demo. It is the wrong trade going forward, and
this review is about where to repay that debt before it compounds.

**Top findings, in priority order:**

1. **Duplicated, divergent server** — `cobeart-app/server.js` and `cobeart-app/electron/main.js`
   are two separate implementations of the same Socket.IO hub with *different behavior*. This is the
   most dangerous single issue: the data contract lives in two places and they already disagree.
2. **No message-contract definition anywhere** — the frame/audio payload shape is implicitly
   re-declared in Python, in `main.js`, in `fluid-bridge.js`, and in every shader. Any change ripples
   silently.
3. **Import-time side effects and global mutable state in the Python backend** — `sender.py`
   connects to Socket.IO at module import; `metrics.py` holds per-process globals (including a
   genuine correctness bug in velocity history shared across all bodies).
4. **Configuration is hardcoded everywhere** — IP addresses, ports, framerates, arena size, and
   magic thresholds are scattered as literals across Python and JS. There is no single config source.
5. **Two incompatible "background shader" architectures coexist** — the ES-module registry
   (`backgrounds/registry.js`) and the standalone-page pattern (`kaleidoscope/`, `particle-orbits/`)
   described in CLAUDE.md are different contracts that cannot be mixed.
6. **No automated tests, pervasive swallowed exceptions, and debug code in hot paths**
   (`print(...)` per frame, `console.log` per frame).
7. **Documentation drift** — CLAUDE.md describes commands, modules, and shaders that no longer match
   reality (`start-audio-client` is undocumented in `pyproject.toml`; the registry shaders are not the
   ones CLAUDE.md lists).

The good news: none of this requires a rewrite. The architecture is fundamentally fine. What is
needed is **consolidation, one config source, one message contract, and boundary enforcement** —
roughly 1–2 weeks of focused cleanup that will pay for itself immediately.

**Confidence in this assessment: High.** The issues are concrete and verifiable in source.

---

## 2. Current Architecture (as-built)

```
┌───────────────────────── PYTHON BACKEND (cobeart/) ─────────────────────────┐
│                                                                              │
│  OptiTrack Motive ──NatNet/UDP──> optitrackclient/start_client.py            │
│                                     │  receive_rigid_body_frame()            │
│                                     │  (axis remap, quat→euler tilt)         │
│                                     ▼                                        │
│                                   rigid_bodies{}  (module global)            │
│                                     │  receive_new_frame() per mocap frame   │
│                                     ▼                                        │
│                       packagesender/sender.py  PayloadSender                 │
│                         │  calculate_metrics()  (metrics.py, globals + bug)  │
│                         │  *** connects to Socket.IO AT IMPORT TIME ***      │
│                         ▼                                                    │
│                      emit "frame" ─────────────────────┐                    │
│                                                        │                     │
│  Microphone ──> audiocapture/capture.py AudioCapturer  │                     │
│                   │ threaded ring buffer, metrics,      │                     │
│                   │ beat detection (madmom, untracked)  │                     │
│                   ▼                                      │                    │
│                 audiocapture/emitter.py AudioEmitter    │                    │
│                   emit "audio_metrics" ─────────────┐   │                    │
│                                                     │   │                    │
└─────────────────────────────────────────────────── │ ─ │ ───────────────────┘
                                                      │   │
                          (Socket.IO /audio) ◄────────┘   │ (Socket.IO /ingest)
                                                      │   │
┌──────────── ELECTRON MAIN (electron/main.js) ────── ▼ ─ ▼ ──────────────────┐
│  Express static(public/) + Socket.IO Server :3000 (127.0.0.1)                │
│    /audio  : stores lastAudioData (validated)                                 │
│    /ingest : merges payload + lastAudioData → lastFrame, fan-out             │
│    /viewer : broadcast "frame", replay lastFrame on connect                  │
│    /health : namespace counts + staleness                                    │
│                                                                              │
│  >>> cobeart-app/server.js is a SECOND, DIVERGENT copy of this hub <<<       │
│      (no /audio, no merge, no validation, binds all interfaces)              │
│                                                                              │
│  createWindow(): dialog picks shader → loads /, /molten/, /ink/, /composite/ │
└───────────────────────────────── │ (Socket.IO /viewer) ─────────────────────┘
                                    ▼
┌──────────────────── FRONTEND (public/, Electron renderer) ──────────────────┐
│  index.html ─> fluid-bridge.js ─(postMessage to ALL iframes)─> fluid/        │
│                  (re-flattens payload into "splat" messages @120Hz)          │
│                                                                              │
│  Simulations (each its own iframe / page, each its own message contract):    │
│    fluid/script.js (2768 LOC)  ── imports backgrounds/registry.js            │
│    molten/molten.js (642)                                                     │
│    ink/ink.js (439)                                                           │
│    composite/composite.js ── stacks fluid+molten+ink iframes, GLSL blend     │
│                                                                              │
│  Backgrounds — TWO architectures:                                            │
│    A) registry.js  → {name, fragmentShader} compiled INTO fluid/script.js    │
│    B) standalone   → kaleidoscope/, particle-orbits/ connect to /viewer      │
│                       directly (own index.html, own THREE.js)                │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Layer-by-Layer Analysis

### 3.1 Python backend — `cobeart/`

**What is here:** `optitrackclient/` (vendored NatNet SDK + `start_client.py` driver),
`packagesender/` (sender + metrics), `audiocapture/` (capture, emitter, beat detection, viz),
`settings/` (one module of literals).

#### Critical issues

**A. Import-time Socket.IO connection — `packagesender/sender.py:6-8`**
```python
SIO_URL = "http://localhost:3000"
sio = socketio.Client(reconnection=True, reconnection_attempts=0)
sio.connect(SIO_URL, transports=["websocket"], namespaces=["/ingest"])
```
Connecting at module import is a serious anti-pattern. `import cobeart.packagesender.sender`
attempts a network connection as a side effect. This makes the module impossible to import for
testing, impossible to run unless the Electron server is already up, and it crashes the OptiTrack
client on startup ordering. Contrast with `audiocapture/emitter.py`, which does this *correctly*:
a class with a background reconnect loop and `wait=False`. The sender should adopt the emitter's
pattern (or share it). **Confidence: High.**

**B. Correctness bug — shared velocity history across all bodies — `packagesender/metrics.py:5,67-72`**
```python
normVel_hist = [1 for i in range(15)]   # module global, ONE list for ALL bodies
...
normVel_hist.append(body_history[id]['norm_abs_velocity'])
normVel_hist.pop(0)
```
There is a single `normVel_hist` shared across every tracked body. With multiple rigid bodies, body
2's velocity pollutes body 1's smoothing window. The history should be per-body (it already lives in
`body_history[id]` — the smoothing state belongs there too). This is a real bug, not a style issue.
**Confidence: High.**

**C. Debug `print` on every frame — `packagesender/metrics.py:74`**
```python
print(f"DEBUG metrics for ID {id}: {body_history[id]}")
```
At 240 Hz × N bodies this floods stdout and is a measurable throughput tax. Remove, or route through
a logger at DEBUG level. **Confidence: High.**

**D. `metrics.py` mutates via globals and copies a mutable template with `.copy()` (shallow)** —
`body_template.copy()` (line 36) is a shallow copy; the numpy arrays inside are shared references
until reassigned. It happens to work only because every field is reassigned immediately after. This
is fragile. The whole module should be a small class or dataclass per body with explicit state,
no module globals. Per your own CLAUDE.md: "Write pure functions — only modify return values, never
global state." This module violates that directly. **Confidence: High.**

**E. Dead experimental code — `optitrackclient/start_client.py:55-187`**
`head_tilt_from_quat`, `quaternion_to_rotation_matrix`, `quaternion_to_xaxis_yaxis`,
`heading_and_tilt_from_quat`, plus ten `tilt1..tilt5g` Euler-order experiments (lines 162-171),
duplicate `import math`/`import numpy` (88-89), and a block of commented-out code (173-181).
Only `tilt1, tilt2, tilt3` are actually used (line 184), and the semantics of packing three
*different Euler conventions of the same rotation* into `roll/yaw/pitch` fields is almost certainly
not what downstream consumers assume. This needs to be resolved to a single, correct orientation
extraction with documented axis conventions. **Confidence: High that it is dead/confusing; Medium on
the correct replacement — needs a decision from whoever understands the arena axis setup.**

#### Design-level observations

- **`settings/streaming.py` is the only config module and it is pure literals** (IPs, framerates,
  arena size, rescale factors). Audio config, Socket.IO URL, and ports live elsewhere (or nowhere).
  There is no single configuration surface. See §5.2.
- **`audiocapture/` is the best-engineered part of the backend.** `capture.py`, `emitter.py`,
  and the beat module show real care: threading with locks, pre-allocated FFT workspaces,
  reconnect loop, typed signatures, JSON-serialization discipline (explicit `bool()`/`float()`
  casts), argparse entry point. This is the quality bar the rest of the backend should meet.
  (It is also large — `capture.py` is 656 LOC and mixes capture, DSP, peak/onset detection, and
  emission. Worth splitting `AudioCapturer` into capture vs. analysis later, but not urgent.)
- **`madmom/` (88 MB) is vendored in-tree but untracked by git** (`git ls-files madmom` → 0 files)
  and required for beat detection per `pyproject.toml:59`. This is a reproducibility hole: a fresh
  clone cannot run beat detection, and the dependency is invisible to version control. Either vendor
  it properly (submodule/tracked) or pin a fork/wheel in `pyproject.toml`. **Confidence: High.**

### 3.2 Electron hub — `cobeart-app/electron/main.js` + `server.js`

**A. Duplicated, divergent server — THE priority fix.**
`server.js` (used by `npm run dev`/`npm run web`) and the `startHttpServer()` inside `main.js`
(used by `npm start`) are two hand-maintained copies of the same hub that have *already drifted*:

| Concern | `main.js` | `server.js` |
|---|---|---|
| `/audio` namespace | yes | **missing** |
| audio↔frame merge | yes | **no** |
| payload validation | yes (fields, NaN) | minimal |
| `/health` endpoint | yes | no |
| bind address | `127.0.0.1` (good) | all interfaces |
| timestamp injection | `Date.now()` | none |

This means development mode (`npm run web`) runs a *materially different data pipeline* than
production (`npm start`). A shader tuned under `web` can break under `start`. The hub logic must be
extracted into one module (e.g. `electron/hub.js` exporting `createHub(app, io)`) and imported by
both entry points. If `server.js` has no remaining purpose on Linux, delete it. **Confidence: High.**

**B. State management is acceptable but unprotected.** `lastFrame`/`lastAudioData` as
module-scoped `let` is fine for a single-window local app (KISS — do not over-engineer this into a
store). The real gap is that there is **no staleness policy**: if the audio client dies, `main.js`
keeps merging the last (now stale) `lastAudioData` into every frame forever. `/health` *reports*
age but nothing *acts* on it. Add a max-age so stale audio is dropped rather than frozen. This is a
resilience gap, not a structural one. **Confidence: High.**

**C. Latent bug — performance-mode checkbox — `main.js:197-198`**
```js
const shader = ['splat', 'molten', 'ink', 'mixed'][choice];
const usePerfMode = choice.checkboxChecked;   // choice is an int index, not the result object
```
`dialog.showMessageBoxSync` with a checkbox returns an **object** `{response, checkboxChecked}`
unless called in the integer-returning form. Here `choice` is used both as an array index (line 197,
implying it is an int) and as `.checkboxChecked` (line 198, implying it is an object). One of these
is wrong; `usePerfMode` is almost certainly always `undefined`. Performance mode likely never
engages from the dialog. **Confidence: High (contract mismatch is unambiguous in source).**

**D. `/ingest` comment says "back-compat" but there is no versioning.** The namespace design
(`/ingest`, `/audio`, `/viewer`) is actually good — clean producer/consumer split. Keep it. But the
"back-compat" note hints at undocumented contract evolution; formalize the contract (§4).

### 3.3 Frontend — `cobeart-app/public/`

**A. `fluid-bridge.js` re-flattens the payload and logs per frame.**
The bridge receives the merged frame, then at 120 Hz manually copies ~15 fields per rigid body into
a `splat` message (lines 127-144), with `color` hardcoded to `[1, 0.6, 0.2]` (line 141). It also
`console.log`s *every frame* (lines 69, 126) — in an Electron renderer with DevTools open this is a
real performance drain and was flagged in CLAUDE.md as breaking the ink visualization. The field-by-
field re-declaration is a third copy of the message contract (after Python and `main.js`). The bridge
should forward a typed object, not re-enumerate fields, and all per-frame logging must go behind a
debug flag. **Confidence: High.**

**B. Three different simulation message contracts.** `fluid/script.js` has *three* separate
`window.addEventListener('message', ...)` handlers (lines 1644, 1677, 1802); `molten` and `ink`
each have their own. There is no shared "splat event" schema. Each simulation parses the postMessage
payload independently. This is the frontend mirror of the backend's missing contract.

**C. `composite.js` — clever but fragile, and carries a known bug.**
- The cross-iframe canvas-texture capture (`tryBindSources`, `CanvasTexture` per frame) is an
  ingenious hack but depends on same-origin iframe canvas access and per-frame `needsUpdate`. It is
  the most GPU-expensive path in the app and the least robust.
- **Bug: `payload.rigidbodies[bodyPartsIndex['left_hand']]`** (lines 343-344). `bodyPartsIndex` is a
  name→? map loaded from `body_map.json`, used directly as an *array index* into `rigidbodies`. Unless
  `body_map.json` maps names to integer positions *and* bodies always arrive in that fixed order,
  `lh`/`rh` will be `undefined` and the subsequent `lh.x` throws. Rigid bodies should be looked up by
  `ID`, not array position.
- `showInteractiveElements = true` is committed (line 293) with a comment saying it must be `false`
  for production. Production config is a code literal.
- Large blocks of commented-out code (lines 408-422, 543-544).
**Confidence: High on all three.**

**D. Two background-shader architectures that cannot interoperate.**
- *Registry* (`backgrounds/registry.js`): exports `[{name, fragmentShader}]`, imported and compiled
  into `fluid/script.js:1008-1011` as GLSL fragment programs in the fluid render pass. Members:
  `electric-clouds`, `circles`, `zephyr`.
- *Standalone* (per CLAUDE.md): `kaleidoscope/`, `particle-orbits/` are full pages with their own
  `index.html`, their own THREE.js, connecting *directly* to `/viewer`. They are not loadable via the
  registry and are not composited with the fluid sim.

These are two incompatible contracts for the same conceptual feature ("background"). A new shader
author has to guess which pattern to follow, and CLAUDE.md documents the standalone pattern as *the*
way to add backgrounds while the live code imports the registry. Pick one. The registry (data-only
GLSL, composited in-engine) is the better abstraction; the standalone pages should either be migrated
into it or explicitly reclassified as "full-screen alternative visualizations," not "backgrounds."
**Confidence: High.**

**E. Untracked shader directories referenced-in-spirit.** `backgrounds/` contains untracked dirs
(`blackhole-sun/`, `conch/`, `metaballs/`, `retro-disco/`) plus `particle-orbits/original.txt`.
These are work-in-progress not in git and not in the registry. Decide: track them or remove them;
do not leave the shader catalog in a half-committed state entering a new phase.

---

## 4. Cross-Cutting Concerns

### 4.1 The missing message contract (highest-leverage fix)

The frame payload is implicitly defined in **at least four places**:
`sender.py:45-67` (producer) → `main.js:101-112` (merge/augment) → `fluid-bridge.js:91-144`
(flatten) → each shader (consume). Audio adds a fifth shape across `capture.py:541-562`,
`main.js:52-72`, `fluid-bridge.js:84-89`.

There is no schema, no version field on the frame (there is a `type: "optitrack"` tag but it is
unused downstream), and no validation beyond `main.js`'s ad-hoc field check. Any change to a field
name (`ID` vs `id` — the bridge already defensively handles both at line 92) is a silent, runtime-
only break.

**Recommendation:** define the contract **once** as a shared, documented schema:
- A single source-of-truth document (even a `CONTRACT.md` or a shared JSON Schema) for the `frame`
  and `audio_metrics` messages, including units and ranges (the field comments in `sender.py` are
  good raw material: `[mm]`, `[degrees]`, `[0..1]`).
- Add a `schemaVersion` to both messages so consumers can fail loudly on mismatch.
- Validate at the hub boundary (already partly done for audio) and reject rather than forward
  malformed frames.

This is a **Published Language** in DDD terms (Evans) — the stable contract between the Python
bounded context and the rendering bounded context. It is the correct boundary to invest in because
every current and future shader depends on it.

### 4.2 Configuration management

Hardcoded values inventory (non-exhaustive):
- `settings/streaming.py`: IPs `192.168.0.104/105`, framerates `240`, arena `±3000` mm, rescale `3.5`.
- `sender.py:6`: `SIO_URL = "http://localhost:3000"` (duplicate of the env-based `get_socketio_url`
  in `audiocapture/utils.py:11` — two different mechanisms for the same thing).
- `metrics.py`: `max_vel=13000`, history length `15`.
- `main.js:14`: `PORT = 3000`; window `1050×1050` (lines 128-129).
- `fluid-bridge.js`: `bridgeFramerate = 120`, `color = [1, 0.6, 0.2]`.
- `composite.js`: `arena = {x:3000, y:3000}` (duplicates the Python arena, can drift), z-thresholds
  `2700`, clap distance `200`, `showInteractiveElements`.
- `capture.py`: dozens of DSP magic numbers (reasonable as defaults, but undocumented as tunables).

The arena size existing independently in **both** `settings/streaming.py` and `composite.js` is the
clearest example of config that *will* drift. Your own CLAUDE.md mandates "configuration via
dataclasses or YAML — not hardcoded magic numbers."

**Recommendation:** one config source per layer, loaded not literal:
- Python: a single `Settings` dataclass (or YAML loaded into one) covering network, tracking, arena,
  audio. Collapse `SIO_URL`/`get_socketio_url` into it.
- Electron/frontend: port and arena injected once (the `window.__SOCKET_PORT__` injection at
  `main.js:159` is the right idea — extend it to arena size and framerate so the frontend stops
  hardcoding `3000`).
- Shared values that *must* agree across layers (arena bounds especially) should originate in one
  place and be passed to the other, not re-typed.

### 4.3 Error handling and resilience

Against your CLAUDE.md rule ("Never silently swallow errors"), the backend swallows aggressively:
- `capture.py:191-192`: the entire capture loop is wrapped in `except Exception: pass` — if the
  audio device fails, the thread dies silently and metrics simply stop with no diagnostic.
- `capture.py:9-13`, `emitter.py:49/61/106-108`: multiple bare `except Exception: pass`.
- `sender.py:72-73`: catches and prints but keeps looping on a broken socket.

Some of this is deliberate (high-frequency emit failures shouldn't spam) and that is defensible —
but it should be *logged once* with backoff, not silently dropped. The distinction between "expected
transient" (log-once) and "fatal" (raise/surface) is currently collapsed into blanket `pass`.

**Resilience gaps worth closing now:**
- Stale-data policy in the hub (§3.2.B).
- The capture loop should surface device failure, not vanish.
- Reconnect logic is good in `emitter.py`; `sender.py` has none (it relies on socketio client
  auto-reconnect but connects at import, so a failed initial connect is fatal).

### 4.4 Testing

`pyproject.toml` declares a `test` extra (pytest, flake8, bandit) and `audiocapture/tests/` exists
(`test_audio_features.py`, `test_predictive_beat.py`) — so the audio module *does* have tests, which
CLAUDE.md's "No automated test suite currently exists" no longer accurately reflects. Everything
else (optitrack transform, metrics, the hub, the bridge) is untested.

The highest-value tests to add, in order:
1. **Message contract tests** — once the schema exists, assert producer output and hub output match
   it. This is the cheapest insurance against the §4.1 problem.
2. **`metrics.py` velocity/normalization** — would have caught the shared-history bug (§3.1.B). Pure
   function, trivial to test once de-globalized.
3. **Coordinate transform in `start_client.py`** — pin the axis-remap and orientation extraction so
   the Euler-convention ambiguity can be resolved with a safety net.
Per your preference for integration/smoke tests: a single smoke test that boots the hub, emits a
synthetic frame on `/ingest` + metrics on `/audio`, and asserts the merged `/viewer` output would
cover the most critical seam.

---

## 5. Windows-Specific Code Inventory (Linux migration)

The codebase is **surprisingly clean** of Windows coupling — there is no `win32`, `.exe`, registry,
or path-separator abuse. The items below are the actual Windows-flavored concessions, all minor:

| Location | Item | Action on Linux |
|---|---|---|
| `audiocapture/capture.py:9-13` | Patches `soundcard.mediafoundation.numpy.fromstring` (MediaFoundation = Windows audio backend) | On Linux, soundcard uses PulseAudio, not MediaFoundation. The `try/except` already no-ops if the import fails, so it is harmless — but it is dead on Linux and should be removed or guarded by platform check for clarity. Verify PulseAudio capture works first. **Confidence: High it is Windows-only; Medium on whether the NumPy-2 `fromstring` issue recurs under PulseAudio — test.** |
| `audiocapture/capture.py:152-157` | `warnings.filterwarnings(... module="soundcard.mediafoundation")` | Same — filters a Windows-backend warning that won't fire on Linux. Remove. |
| `audiocapture/viz/waveform.py:11,33` | Same MediaFoundation patch + warning filter | Same as above. |
| `main.js:211` | `if (process.platform !== 'darwin')` quit logic | Correct and cross-platform already — keep. |
| `cobeart/settings/streaming.py:13-14` | LAN IPs for Windows "CoBe computer"/OptiTrack host | Not Windows-specific per se, but will need re-setting for the Debian machine's network. Move to config (§4.2). |

**Net:** Linux migration is low-risk from a code standpoint. The real migration work is
(a) confirming `soundcard` + PulseAudio capture + loopback behave on Debian (loopback device
enumeration via `sc.all_microphones(include_loopback=True)` differs between PulseAudio and
MediaFoundation — **test this early**), and (b) confirming OptiTrack NatNet multicast works from the
Linux box's network stack. Neither is a code refactor; both are validation spikes.

---

## 6. Recommended Structural Changes (Prioritized)

Priorities are P0 (do before any new feature work), P1 (do this phase), P2 (opportunistic).

### P0 — Consolidate and contract (the foundation)

1. **Unify the server.** Extract hub logic to one module imported by both `main.js` and the
   dev/web entry point; delete or reduce `server.js` to a thin wrapper. Removes the single biggest
   source of behavioral divergence. *(§3.2.A)*
2. **Define the message contract once.** Author the `frame` + `audio_metrics` schema with units,
   ranges, and a `schemaVersion`; validate at the hub; stop re-enumerating fields in `fluid-bridge.js`.
   *(§4.1)*
3. **Fix the `sender.py` import-time connection.** Convert to a class with a lazy/reconnecting
   client mirroring `emitter.py` (or extract a shared `SocketIOEmitter` base used by both). *(§3.1.A)*
4. **Fix the `metrics.py` shared-history bug and de-globalize.** Per-body smoothing state inside a
   small class/dataclass; no module globals; no per-frame `print`. *(§3.1.B/C/D)*
5. **Fix the perf-mode dialog bug** in `main.js:197-198`. *(§3.2.C)*
6. **Fix the `composite.js` body-lookup bug** (lookup by `ID`, not array index). *(§3.3.C)*

### P1 — Config, resilience, and shader-system coherence

7. **Single config source per layer** (Python dataclass/YAML; frontend injection). Eliminate the
   duplicated arena size and the dual Socket.IO-URL mechanisms. *(§4.2)*
8. **Stale-data policy in the hub** + surface (don't swallow) the audio capture-loop failure.
   *(§3.2.B, §4.3)*
9. **Pick one background-shader architecture.** Standardize on the registry; migrate or reclassify
   the standalone pages; update CLAUDE.md to match. Commit or remove the untracked shader dirs.
   *(§3.3.D/E)*
10. **Gate all per-frame logging** (`fluid-bridge.js`, `composite.js`, `sender.py`, `metrics.py`)
    behind a debug flag. *(§3.1.C, §3.3.A)*
11. **Resolve `madmom` vendoring** — git submodule, tracked vendor, or pinned wheel in
    `pyproject.toml`. *(§3.1)*
12. **Register `start-audio-client`** (and whatever audio entry point is intended) in
    `pyproject.toml [project.scripts]` — currently only `start-optitrack-client` exists, yet CLAUDE.md
    documents `start-audio-client`. *(doc/code drift)*

### P2 — Clean-up and longevity

13. **Remove dead experimental code** in `start_client.py` (unused quaternion helpers, tilt1..5g,
    commented blocks) and resolve the orientation-convention question with documented axes. *(§3.1.E)*
14. **Add the three high-value tests** (contract, metrics, transform). *(§4.4)*
15. **Split `AudioCapturer`** (capture vs. analysis vs. emission) — only if the audio module keeps
    growing. Not urgent; it is currently the best-structured code you have.
16. **Update CLAUDE.md** to reflect reality (shaders, commands, test status, Linux). It is currently
    a mix of accurate and stale, which misleads both humans and AI assistants.
17. **Introduce a lightweight fitness function**: a lint/CI check that the message schema is imported
    (not re-declared) and that `server.js`/`main.js` share the hub module — so this consolidation does
    not silently regress.

---

## 7. What to Keep (do not touch)

Resist the urge to rewrite these — they are working, well-shaped, or correctly simple:

- **The three-namespace Socket.IO design** (`/ingest`, `/audio`, `/viewer`). Clean producer/consumer
  separation; audio-as-additive-state while OptiTrack drives the rate is a genuinely good decision.
  Keep it; just put one implementation behind it.
- **`audiocapture/` engineering quality** — threaded ring buffer with locks, pre-allocated FFT
  workspaces, reconnecting emitter, typed signatures, JSON-safe casting. This is the reference quality
  for the rest of the codebase.
- **The beat-detection layering** (`detector` + `predictor` for low-latency prediction) is a
  thoughtful design and has tests. Keep.
- **Simple module-scoped `lastFrame`/`lastAudioData` state** in the hub. For a single-window local
  app this is correct (KISS/YAGNI) — do *not* add a state-management library. Only add the staleness
  guard.
- **The iframe-isolation model for simulations.** Treating each WebGL sim as a self-contained
  black box behind postMessage is a sound boundary (even if the *contract* across that boundary needs
  formalizing). It keeps the heavyweight fluid sim encapsulated and swappable.
- **`contextIsolation: true`, `nodeIntegration: false`, `127.0.0.1` binding** in `main.js` — correct
  Electron security posture. Keep. (Bring `server.js` up to the same binding when consolidating.)
- **The registry abstraction for backgrounds** (`{name, fragmentShader}` composited in-engine) — it
  is the right model; just make it the *only* model.
- **`.claudeignore` and `COBEART_SOCKETIO_URL` env override** — small signs of good hygiene already
  present; build on them.

---

## 8. Closing Note on Trade-offs

Everything above is a trade-off, stated explicitly:

- Consolidating the server trades a little short-term churn for the elimination of an entire class of
  "works in dev, breaks in prod" bugs.
- A formal message contract trades upfront schema effort for the ability to change any field without
  archaeology across four files.
- Config centralization trades a refactor for the end of silent cross-layer drift (the arena-size
  duplication will bite otherwise).
- *Not* doing these trades the appearance of speed now for compounding friction over the next phase —
  which is precisely the trade that got the codebase here.

The project does **not** need new architecture. It needs its existing, basically-sound architecture
made *explicit and singular*: one server, one contract, one config source, one background system.
That consolidation is the highest-return work available before the next development phase begins.
