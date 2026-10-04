(function () {
  const DEBUG = new URLSearchParams(location.search).has('debug') || window.__COBEART_CONFIG__?.debug === true;
  const BRIDGE_FRAMERATE_HZ = 120;
  const SPLAT_COLOR = Object.freeze([1, 0.6, 0.2]);

  // A splat is the frame's rigid-body entry unchanged plus type and color (contract/splat.schema.json).
  const toSplat = (rigidBody) => ({ type: 'splat', ...rigidBody, color: SPLAT_COLOR });

  function createOverlay(frameEl) {
    const overlay = document.createElement('div');
    overlay.id = 'textOverlay';
    Object.assign(overlay.style, {
      position: 'absolute',
      top: `${frameEl.offsetTop}px`,
      left: `${frameEl.offsetLeft}px`,
      width: `${frameEl.offsetWidth}px`,
      height: `${frameEl.offsetHeight}px`,
      pointerEvents: 'none',
      color: 'white',
      backgroundColor: 'rgba(0, 0, 0, 0.0)',
      fontFamily: 'monospace',
      fontSize: '12px',
      overflowY: 'auto',
      padding: '10px',
    });
    document.body.appendChild(overlay);
    return overlay;
  }

  function init() {
    // Finds the id=fluidFrame iframe element that was created in the index.html file,
    // and that holds the embedded fluid simulation.
    let frameEl = document.getElementById('fluidFrame')
            || document.getElementById('moltenFrame')
            || document.getElementById('inkFrame');
     if (!frameEl) {
       frameEl = document.createElement('iframe');
       frameEl.id = 'fluidFrame';
       frameEl.src = '/fluid/index.html';
       frameEl.width = 960;
       frameEl.height = 540;
       frameEl.style.border = '0';
       frameEl.style.maxWidth = '100%';
       const host = document.getElementById('out')?.parentElement || document.body;
       host.appendChild(document.createElement('h2')).textContent = 'Fluid Simulation';
       host.appendChild(frameEl);
     }

    // Debug only: give embedded sims ?debug too (their own URL has no query string; one reload at startup)
    if (DEBUG) {
      for (const frame of document.querySelectorAll('iframe[src]')) {
        const url = new URL(frame.src, location.href);
        if (!url.searchParams.has('debug')) {
          url.searchParams.set('debug', '');
          frame.src = url.href;
        }
      }
    }

    // Debug only: overlay with the latest rigid-body and audio values
    const overlay = DEBUG ? (document.getElementById('textOverlay') ?? createOverlay(frameEl)) : null;

    // Connects to the /viewer namespace on the server hosted in electron/main.js
    const socket = window.viewerSocket || io('/viewer', { transports: ['websocket'] });

    socket.on('connect', () => console.log('[fluid-bridge] socket connected', socket.id));
    socket.on('disconnect', () => console.log('[fluid-bridge] socket disconnected'));

    // Post a message to the given iframes, and to the window itself (for top-level scripts)
    function postToWindows(msg, frames) {
      for (const frame of frames) {
        frame.contentWindow?.postMessage(msg, '*');
      }
      window.postMessage(msg, '*');
    }

    let latestFrame = null; // Latest /viewer frame not yet forwarded
    let latestAudio = null; // Latest audio metrics seen in any frame

    // Receive frames emitted by the hub and keep only the latest one
    socket.on('frame', (payload) => {
      if (DEBUG) console.log('[fluid-bridge] received frame', payload);
      latestFrame = payload;
      if (payload.audio) latestAudio = payload.audio;
    });

    // Update the overlay with the given frame's data
    function showOverlay(frame) {
      let overlayText = '';
      if (latestAudio) {
        const { rms, peak, zcr, dominant_frequency: f0 } = latestAudio;
        overlayText += `Audio: RMS: ${rms.toFixed(4)} | Peak: ${peak.toFixed(4)} | ZCR: ${zcr.toFixed(4)} | f0: ${f0.toFixed(1)} Hz<br><br>`;
      }
      for (const rb of frame.rigidbodies) {
        overlayText += `Splat ID: ${rb.ID}<br>
Position: (${rb.x.toFixed(2)}, ${rb.y.toFixed(2)}, ${rb.z.toFixed(2)})<br>
Velocity: (${rb.vx.toFixed(2)}, ${rb.vy.toFixed(2)}, ${rb.vz.toFixed(2)})<br>
|V|: ${rb.abs_vel.toFixed(2)} (norm: ${rb.norm_abs_vel.toFixed(2)})<br>
Orientation q: (${rb.qx.toFixed(3)}, ${rb.qy.toFixed(3)}, ${rb.qz.toFixed(3)}, ${rb.qw.toFixed(3)})<br>
Angular Velocity: (wx: ${rb.wx.toFixed(2)}, wy: ${rb.wy.toFixed(2)}, wz: ${rb.wz.toFixed(2)}) deg/s<br><br>`;
      }
      overlay.innerHTML = overlayText;
    }

    // Forward the latest frame at the bridge framerate, one splat per rigid body
    setInterval(() => {
      if (!latestFrame) return;
      const frame = latestFrame;
      latestFrame = null;
      if (DEBUG) console.log('[fluid-bridge] sending splats', frame.rigidbodies);
      const frames = document.querySelectorAll('iframe');
      for (const rb of frame.rigidbodies) {
        postToWindows(toSplat(rb), frames);
      }
      if (DEBUG) showOverlay(frame);
    }, 1000 / BRIDGE_FRAMERATE_HZ);
  }

  // This block instructs the browser to call the init function only once the DOM is fully loaded,
  // such that the script does not reference elements that don't exist.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
