# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CoBeART is an audio-visual art project using Spatial Augmented Reality. It combines real-time body tracking via OptiTrack with WebGL-based fluid/particle simulations rendered through Electron. The system captures motion data from OptiTrack cameras and audio from microphones, processes them in Python, and streams to a Node.js/Electron frontend for visualization.

## Architecture

### Three-Layer System

1. **Python Backend (`cobeart/`)**: Captures and processes motion/audio data
   - `optitrackclient/`: OptiTrack NatNet client for motion capture
   - `audiocapture/`: Audio capture and metrics extraction (RMS, peak, ZCR, dominant frequency)
   - `packagesender/`: Socket.IO client that sends data to Electron server
   - `settings/`: Configuration for OptiTrack IP addresses, tracking parameters

2. **Electron Main Process (`cobeart-app/electron/main.js`)**: Socket.IO hub
   - Runs Express server on port 3000
   - Three Socket.IO namespaces:
     - `/ingest`: Receives motion data from Python (OptiTrack frames drive emission rate)
     - `/audio`: Receives audio metrics from Python (additive background state)
     - `/viewer`: Broadcasts combined frames to frontend
   - Merges OptiTrack data with latest audio metrics into combined frames

3. **Frontend (`cobeart-app/public/`)**: Electron renderer with WebGL visualizations
   - `index.html` / `fluid-bridge.js`: Connects to `/viewer` namespace, receives frames
   - `fluid/`: WebGL fluid simulation (splat shader)
   - `molten/`: Alternative particle-based visualization
   - Bridge uses postMessage() to send data to iframe-contained simulations

### Data Flow

```
OptiTrack System → Python NatNetClient → PayloadSender (Socket.IO /ingest)
Audio Device → AudioCapturer → AudioMetricsEmitter (Socket.IO /audio)
                                        ↓
                            Electron main.js merges data
                                        ↓
                    Socket.IO /viewer → fluid-bridge.js → postMessage()
                                        ↓
                            WebGL Simulation (iframe)
```

## Commands

### Python Backend

**Python Environment:**

This project uses a conda environment named `splat-env` (Python 3.12). Call its interpreter directly; do not use `conda run`, which masks output:

```bash
~/miniconda3/envs/splat-env/bin/python -m pytest
~/miniconda3/envs/splat-env/bin/python -m py_compile file.py
```

Known traps (see `ROADMAP.md`, "Worktree caveats"):
- In a git worktree, run code with `python -m ...` from the worktree root. The installed `start-*` console scripts always import the main checkout.
- `soundcard` crashes under `python -c`, and under `python -m cobeart.audiocapture.<module>` with no arguments. Use script files, and pass at least one flag.

**Install Python dependencies** (builds madmom from a pinned commit):
```bash
~/miniconda3/envs/splat-env/bin/python -m pip install -e ".[test,viz]"
```

**Start OptiTrack motion capture client:**
```bash
start-optitrack-client
```
Connects to OptiTrack Motive software and streams rigid body tracking data. Requires OptiTrack SDK and Motive to be running.

**Start audio capture client:**
```bash
start-audio-client
```
Captures audio from selected input device and sends metrics to the Electron server.

### Electron Application

**Install Node.js dependencies:**
```bash
cd cobeart-app
npm install
```

**Start Electron app (production):**
```bash
npm start
```
Starts the full application with Electron window. Shows dialog to choose between "Splat" (fluid) or "Molten" (particle) visualization.

**Development mode (web server + Electron):**
```bash
npm run dev
```
Runs web server and Electron concurrently using `concurrently`.

**Web server only (for debugging):**
```bash
npm run web
```
Runs Express server without Electron window.

## Configuration

### OptiTrack Settings (`cobeart/settings/streaming.py`)

Key parameters:
- `client_address` / `server_address`: IP addresses for OptiTrack connection
- `use_multicast`: Boolean for multicast vs unicast streaming
- `tracking_framerate`: OptiTrack capture rate (typically 240 Hz)
- `package_framerate`: Rate for sending data to Electron (typically 240 Hz)
- `max_num_objects`: Maximum number of tracked rigid bodies (default: 6)
- `max_abs_coord_x/y/z`: Arena boundaries in millimeters

### Window Configuration (`cobeart-app/electron/main.js`)

- Default window size: 1050x1050
- Server port: 3000 (localhost only)
- Background throttling disabled for smooth visualization

## Key Implementation Details

### Motion Tracking Pipeline

- OptiTrack streams data via NatNet protocol (UDP multicast/unicast)
- Python `NatNetClient` receives frames and rigid body updates
- Coordinate transformation: OptiTrack (meters) → millimeters with axis remapping
- Quaternion rotations converted to Euler angles (roll, yaw, pitch in degrees)
- Velocity calculation: Position/rotation derivatives via Kalman filtering (`packagesender/metrics.py`)

### Audio Processing

- Uses `soundcard` library for cross-platform audio capture
- Threaded capture with ring buffer to prevent blocking
- Metrics calculated per chunk (default 1024 samples @ 48kHz):
  - RMS (root mean square)
  - Peak amplitude
  - Zero-crossing rate
  - Dominant frequency (FFT with Hanning window)
- Audio data merged with OptiTrack frames in main.js before emission to `/viewer`

### Socket.IO Communication

- Python clients connect to `/ingest` and `/audio` namespaces
- Electron main.js stores `lastFrame` and `lastAudioData` state
- OptiTrack frame events trigger emission to `/viewer` with merged audio data
- Frontend `fluid-bridge.js` maintains 240 Hz interval loop, forwarding latest frame to simulation

### WebGL Simulations

Both simulations run in iframes and receive `splat` messages via postMessage():
- **Fluid simulation**: Multi-pass WebGL2 shader pipeline for incompressible Navier-Stokes
- **Molten simulation**: Particle-based system with optional performance mode
- Tracked objects trigger splat/particle effects at their (x, y, z) positions
- Audio metrics can modulate visual parameters (color, intensity, radius)

### Background Shaders System

The `cobeart-app/public/backgrounds/` directory contains a repository of background shaders that can be used standalone or composited with primary shaders. Each background shader:
- Connects directly to Socket.IO `/viewer` namespace
- Receives OptiTrack rigid body data and audio metrics
- Renders using THREE.js (r128)
- Is designed to be modular and independently runnable

**Current Background Shaders:**

1. **Kaleidoscope (`backgrounds/kaleidoscope/`)**
   - Converted from ISF (Interactive Shader Format) to THREE.js
   - Multi-pass fragment shader with 6 persistent buffers + final composite
   - Features:
     - 8 generative color palettes (Stargate, Circuit Board, Nebula, Psychedelic Tunnels, Turing Spots, Labyrinth, Coral Growth, Cellular)
     - Kaleidoscope mirror effect with configurable segments
     - Animated distortion and rotation
     - Audio-reactive parameters: segments modulated by RMS, distortion by peak, line frequency by dominant frequency
     - Smooth parameter transitions using exponential smoothing
   - Architecture:
     - Pass 0: Time & speed buffer (tracks animation time and smoothed speed)
     - Pass 1: Geometry parameters (segments, distortion, line frequency, warp factor)
     - Pass 2: Pulsation time (for rhythmic line animation)
     - Pass 3: Rotation angle (for kaleidoscope rotation)
     - Pass 4: Color palette selection
     - Pass 5: Color controls (brightness, pattern scale, complexity)
     - Pass 6: Final composite render with all effects applied
   - Original source: `IM-KaleidoKnot.fs` by @dot2dot (bareimage)

2. **Particle Orbits (`backgrounds/particle-orbits/`)**
   - Vertex shader-based particle system
   - Features:
     - 60+ orbital rings of particles (2520 total particles)
     - Circular orbital motion with per-ring phase offsets
     - Audio-reactive particle size (peak), orbital radius (RMS), and color
     - OptiTrack body positions used for particle attraction/offset
     - HSV to RGB color cycling based on orbital parameters
     - Additive blending for glow effects
   - Architecture:
     - Vertex shader handles particle positioning and orbital calculations
     - Fragment shader renders circular point sprites with soft edges
     - Each particle's position calculated from vertexId using mathematical patterns
   - Inspired by vertex shader art techniques

**Adding New Background Shaders:**

To add a new background shader:
1. Create directory: `cobeart-app/public/backgrounds/{shader-name}/`
2. Add `index.html` with Socket.IO and THREE.js CDN links
3. Add `{shader-name}.js` with:
   - Socket.IO connection to `/viewer` namespace
   - OptiTrack data handling (`trackedEntities` object)
   - Audio metrics handling (`audioMetrics` object)
   - THREE.js scene, camera, renderer setup
   - Shader material with vertex/fragment shaders
   - Animation loop updating uniforms and rendering
4. Ensure shader receives and processes:
   - `payload.rigidbodies[]` - Array of tracked objects with x, y, z, velocities
   - `payload.audio` - Object with rms, peak, zcr, dominant_frequency

**Future Compositing Modes (Not Yet Implemented):**
- Background layer rendering with primary shader alpha compositing
- Post-process mix with custom blend modes (multiply, screen, overlay)
- Data sharing where background shader outputs become texture inputs to primary shaders

## Development Notes

- OptiTrack client requires OptiTrack SDK to be installed
- On Linux, audio capture uses `soundcard` with MediaFoundation backend compatibility patches
- NumPy 2.x compatibility: `frombuffer` replaces deprecated `fromstring`
- Electron uses `contextIsolation: true` for security
- All visualization rendering happens in renderer process (sandboxed browser environment)
- Git branches: `main` is primary, recent work in `molten-branch` and `music-branch`

## Testing

No automated test suite currently exists. Manual testing workflow:
1. Start OptiTrack Motive and ensure streaming is enabled
2. Run `start-optitrack-client` in terminal (should show connection info)
3. Run `start-audio-client` in another terminal (should show audio metrics)
4. Run `npm start` from `cobeart-app/` directory
5. Choose visualization type in dialog
6. Verify rigid bodies appear as splats/particles when tracked
7. Check debug overlay for position/velocity data
