# CoBeART message contract

The JSON Schemas in this directory (draft 2020-12) are the single source of truth for the data that crosses the Python / hub / frontend boundaries. Field units, ranges and meanings live in each schema's `description`s; this file only says who sends what, where, and how to change it.

| Message | Schema | Producer | Transport | Consumers |
|---|---|---|---|---|
| `frame` | `frame.schema.json` | Python motion producer (`cobeart/packagesender/sender.py`) | Socket.IO `/ingest`, event `frame` | Hub (`cobeart-app/electron/main.js`) |
| `frame` (merged) | `frame.schema.json` | Hub | Socket.IO `/viewer`, event `frame` | `public/fluid-bridge.js`, `public/composite/composite.js` |
| `audio_metrics` | `audio_metrics.schema.json` | Python audio producer (`cobeart/audiocapture/capture.py` via `emitter.py`) | Socket.IO `/audio`, event `audio_metrics` | Hub, which embeds it as `frame.audio` |
| `splat` | `splat.schema.json` | `public/fluid-bridge.js` | `window.postMessage`, `type: "splat"` | `public/fluid/script.js`, `public/molten/molten.js`, `public/ink/ink.js` |
| `audio` (bridge) | `audio_metrics.schema.json` (the `audio` field) | `public/fluid-bridge.js` | `window.postMessage`, `{ type: "audio", audio }` | `public/fluid/script.js` |

## Rules

- **One shape per hop.** The same `frame` schema validates the producer's message on `/ingest` and the hub's message on `/viewer`. The hub only overwrites `timestamp` with its receipt time and adds `audio`; producers never send `audio`.
- **`audio` inside a frame is a full `audio_metrics` message** (`$ref`, not a copy). It keeps its `schemaVersion` and must carry the hub's `timestamp`, which the hub adds when it stores the message.
- **`splat` is a rigid-body entry plus `type` and `color`.** It reuses the frame's rigid-body field definitions, so names and units match the frame exactly (`ID`, `abs_vel`, `norm_abs_vel`). It has no `schemaVersion` because it never leaves the frontend.
- **The bridge `audio` message is `{ type: "audio", audio }`,** where `audio` is the merged `frame.audio` object (an `audio_metrics` message plus the hub `timestamp`). The bridge posts it only when `audio.timestamp` differs from the last one posted, not on every frame. Listeners ignore any `type` they do not handle.
- **Orientation is a quaternion, never Euler angles.** `qx, qy, qz, qw` (scalar last) is a unit quaternion in arena axes, and `wx, wy, wz` is angular velocity in deg/s about the arena axes. Euler angles have a singularity (gimbal lock) and wraparound jumps; consumers that need a scalar such as forward lean derive it from the quaternion directly (see the `rigidBodyFields` description in `frame.schema.json`).
- **Rigid bodies are identified by `ID`** (upper case) and looked up by `ID`, never by position in `rigidbodies`.
- **Times:** `timestamp` fields are Unix epoch milliseconds. `audio_metrics.beat_timestamp` is Unix epoch seconds from the Python clock. The schema ranges reject either one sent in the other unit.
- **Strict:** every object is closed (`additionalProperties` or `unevaluatedProperties: false`), so an unknown or misspelled field is an error, not a silent no-op.

## Validating

The schemas reference each other by relative filename (`audio_metrics.schema.json`, `frame.schema.json#/$defs/rigidBodyFields`), resolved against their `$id`s under `https://cobeart.invalid/contract/`. Those `$id`s are identifiers, not URLs to fetch: load all three files into the validator first.

- Python: `jsonschema.Draft202012Validator` with a `referencing.Registry` holding all three resources.
- Node: `ajv/dist/2020` (the default `Ajv` class is draft-07 and does not support `unevaluatedProperties`), `addSchema` for all three, then `getSchema(<$id>)`. The schemas compile under ajv strict mode with `allowUnionTypes: true` (for the nullable beat fields).

## Changing the contract

There is one contract version, currently `schemaVersion: 1`, shared by `frame` and `audio_metrics` (and implicitly by `splat`). Because every object is closed, any change is breaking, including adding an optional field. To change it:

1. Edit the schema(s) and bump the `schemaVersion` `const` in **both** `frame.schema.json` and `audio_metrics.schema.json`.
2. In the same change, update every producer and consumer listed above, and the simulator, so they emit and accept the new version. Old and new versions are never accepted side by side.
3. Update this file if a producer, consumer or transport changed.
