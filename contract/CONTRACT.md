# CoBeART message contract

The JSON Schemas in this directory (draft 2020-12) are the single source of truth for the data that crosses the source / hub / world boundaries. Field units, ranges and meanings live in each schema's `description`s; this file says who sends what, where, and how to change it.

## One shape per side of the hub

Sources send poses and raw audio metrics; the hub derives motion values and stamps times; worlds only ever see `frame` and `audio`, whatever the source (OptiTrack on site, the mouse or the simulator at home).

| Message | Schema (`$id` under `https://cobeart.invalid/contract/`) | Producer | Transport | Consumer |
|---|---|---|---|---|
| `pose_frame` | `pose_frame.schema.json` | OptiTrack sender (`cobeart/input/optitrack/sender.py`), simulator (`cobeart/simulator/`), mouse source (`cobeart-app/public/sources/mouse.js`) | Socket.IO `/ingest`, event `pose_frame` | Hub (`cobeart-app/electron/hub.js`) |
| `audio_metrics` | `audio_metrics.schema.json` | Audio producer (`cobeart/audio/capture.py` via `emitter.py`), simulator (`cobeart/simulator/`) | Socket.IO `/audio`, event `audio_metrics` | Hub |
| `frame` | `frame.schema.json` | Hub | Socket.IO `/viewer`, event `frame` | Bridge (`cobeart-app/public/host/bridge.js`) |
| `audio` | `audio.schema.json` | Hub | Socket.IO `/viewer`, event `audio` | Bridge |
| `frame` (posted) | `frame.schema.json` | Bridge | `postMessage` to the world iframe: `{ type: 'frame', frame }` | World (`cobeart-app/public/worlds/<name>/`) |
| `audio` (posted) | `audio.schema.json`, or `null` | Bridge | `postMessage` to the world iframe: `{ type: 'audio', audio }` | World |

## Rules

- **Hub.** Every incoming message is validated; an invalid one is dropped with a rate-limited log line and never forwarded. Each valid `pose_frame` yields exactly one `frame` on `/viewer`: same bodies and pose fields, plus the derived motion fields per body (`vx, vy, vz, wx, wy, wz, abs_vel, norm_abs_vel`, see `frame.schema.json#/$defs/body`), and `timestamp` = hub receipt time. Each valid `audio_metrics` yields exactly one `audio` on `/viewer`: the same fields plus the hub `timestamp`. Audio is never merged into frames, so audio reaches worlds with no motion source running and vice versa. The hub replays nothing to a newly connected viewer.
- **Derived motion uses the source clock.** The hub differentiates successive poses of the same role over `pose_frame.source_time_s`, never over receipt time. A role's motion state restarts (all derived fields 0) on its first sighting, after a source-time gap above `metrics.max_gap_s` (`config/cobeart.yaml`), or when the source clock goes backwards. `metrics.max_vel` and `metrics.history_window` scale and hold `norm_abs_vel`.
- **Bridge.** Every tick (`frontend.bridge_framerate`) it posts the latest frame not yet posted, unchanged, as `{ type: 'frame', frame }`. It posts `{ type: 'audio', audio }` when a new `audio` arrives (new `timestamp`), and `{ type: 'audio', audio: null }` once when the latest audio is older than `frontend.audio_max_age_ms` (the stale policy lives here, not in the hub); it re-arms when audio returns. It posts to the world iframe only. Worlds ignore any `type` they do not handle.
- **Bodies are keyed by role.** `bodies` is an object whose keys are roles from the closed enum `frame.schema.json#/$defs/role` (`left_hand`, `right_hand`, `head`, `chest_front`, `chest_back`, `left_foot`, `right_foot`, `stick`), so a role occurs at most once (one performer). A missing key means that body is not tracked in that message; `bodies` may be empty. There is no `ID` in the contract: each source maps its own identifiers to roles (OptiTrack: `tracking.roles` in `config/cobeart.yaml`, Motive streaming ID to role; unmapped IDs are dropped by the source). Adding a role is a contract change (below).
- **Orientation is a quaternion, never Euler angles (D5).** `qx, qy, qz, qw` (scalar last) is a unit quaternion in arena axes, and `wx, wy, wz` is angular velocity in deg/s about the arena axes. Euler angles have a singularity (gimbal lock) and wraparound jumps; consumers that need a scalar such as forward lean derive it from the quaternion directly (see `frame.schema.json#/$defs/poseFields`).
- **Times.** `frame.timestamp` and `audio.timestamp` are hub receipt times, Unix epoch milliseconds; producers never send them. `pose_frame.source_time_s` is seconds on the source's own monotonic clock with an arbitrary epoch. `audio_metrics.beat_timestamp` is Unix epoch seconds from the Python clock. The schema ranges reject a millisecond timestamp sent in seconds.
- **Strict.** Every object is closed (`additionalProperties` or `unevaluatedProperties: false`), so an unknown or misspelled field, a velocity field in a `pose_frame`, or `audio` inside a `frame` is an error, not a silent no-op.

## Canonical top-down view (D18)

Worlds that draw the arena in 2D use one projection, so screen-based sources line up with them. The viewer looks down on the arena; with `arena` from `config/cobeart.yaml` (`window.__COBEART_CONFIG__.arena`) and the viewport normalized to `u, v` in [0, 1] (`u` rightwards from the left edge, `v` downwards from the top edge):

```
u = (x_max - x) / (x_max - x_min)        x = x_max - u * (x_max - x_min)
v = (y - y_min) / (y_max - y_min)        y = y_min + v * (y_max - y_min)
```

So arena `x_max` is at the left edge and `y_min` at the top edge: the top-left corner is `(x_max, y_min)`, the bottom-right `(x_min, y_max)`. This is Splat's mapping; the mouse source applies the inverse (right column) over the host viewport. A world may ignore it, at the cost of the mouse not lining up.

## Validating

The schemas reference each other by relative filename (`pose_frame` uses `frame.schema.json#/$defs/role` and `#/$defs/poseFields`; `audio` uses `audio_metrics.schema.json#/$defs/fields`), resolved against their `$id`s under `https://cobeart.invalid/contract/`. Those `$id`s are identifiers, not URLs to fetch: load all four files into the validator first. `config/cobeart.schema.json` also takes the role enum from `frame.schema.json`.

- Python: `jsonschema.Draft202012Validator` with a `referencing.Registry` holding all four resources. `tests/contract/schemas.py` provides `contract_validator(name)` and valid example messages (`tests/contract/fixtures/<name>.json`).
- Node: `ajv/dist/2020` (the default `Ajv` class is draft-07 and does not support `unevaluatedProperties`), `addSchema` for all four, then `getSchema(<$id>)`. The schemas compile under ajv strict mode with `allowUnionTypes: true` (for the nullable beat fields).

## Changing the contract

There is one contract version, currently `schemaVersion: 2`. Because every object is closed, any change is breaking, including adding an optional field or a role. To change it:

1. Edit the schema(s) and bump the `schemaVersion` `const` in `pose_frame.schema.json`, `frame.schema.json` and `audio_metrics.schema.json` (`audio.schema.json` inherits it from `audio_metrics`).
2. In the same change, update every producer and consumer listed above, so they emit and accept the new version. Old and new versions are never accepted side by side.
3. Update this file if a producer, consumer or transport changed. A new role needs no config schema edit (the config references the enum), but `tracking.roles` must map it before OptiTrack can send it.
