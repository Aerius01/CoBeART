(function () {
  const CONFIG = window.__COBEART_CONFIG__;
  const DEBUG = new URLSearchParams(location.search).has('debug') || CONFIG.debug === true;
  const BRIDGE_FRAMERATE_HZ = CONFIG.bridge_framerate;
  const AUDIO_MAX_AGE_MS = CONFIG.audio_max_age_ms;

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
    // The iframe in host/index.html that holds the embedded world; the only recipient of bridge messages.
    const frameEl = document.getElementById('worldFrame');

    // Debug only: overlay with the latest bodies (by role) and audio values
    const overlay = DEBUG ? (document.getElementById('textOverlay') ?? createOverlay(frameEl)) : null;

    // Connects to the hub's /viewer namespace (contract/CONTRACT.md: events 'frame' and 'audio')
    const socket = window.viewerSocket || io('/viewer', { transports: ['websocket'] });

    socket.on('connect', () => console.log('[bridge] socket connected', socket.id));
    socket.on('disconnect', () => console.log('[bridge] socket disconnected'));

    const postToWorld = (msg) => frameEl.contentWindow?.postMessage(msg, '*');

    let latestFrame = null; // Latest /viewer frame not yet posted
    let latestAudio = null; // Latest /viewer audio, posted or not
    let postedAudioTimestamp = null; // Hub timestamp of the audio last posted
    let staleAudioPosted = false; // Whether `audio: null` was posted for the current latestAudio

    socket.on('frame', (frame) => {
      if (DEBUG) console.log('[bridge] received frame', frame);
      latestFrame = frame;
    });

    socket.on('audio', (audio) => {
      if (DEBUG) console.log('[bridge] received audio', audio.timestamp);
      latestAudio = audio;
    });

    // Update the overlay with the given frame's bodies and the latest audio
    function showOverlay(frame) {
      let overlayText = '';
      if (latestAudio && !staleAudioPosted) {
        const { rms, peak, zcr, dominant_frequency: f0 } = latestAudio;
        overlayText += `Audio: RMS: ${rms.toFixed(4)} | Peak: ${peak.toFixed(4)} | ZCR: ${zcr.toFixed(4)} | f0: ${f0.toFixed(1)} Hz<br><br>`;
      }
      for (const [role, b] of Object.entries(frame.bodies)) {
        overlayText += `${role}<br>
Position: (${b.x.toFixed(2)}, ${b.y.toFixed(2)}, ${b.z.toFixed(2)})<br>
Velocity: (${b.vx.toFixed(2)}, ${b.vy.toFixed(2)}, ${b.vz.toFixed(2)})<br>
|V|: ${b.abs_vel.toFixed(2)} (norm: ${b.norm_abs_vel.toFixed(2)})<br>
Orientation q: (${b.qx.toFixed(3)}, ${b.qy.toFixed(3)}, ${b.qz.toFixed(3)}, ${b.qw.toFixed(3)})<br>
Angular Velocity: (wx: ${b.wx.toFixed(2)}, wy: ${b.wy.toFixed(2)}, wz: ${b.wz.toFixed(2)}) deg/s<br><br>`;
      }
      overlay.innerHTML = overlayText;
    }

    // Audio is large (spectrum) and updates at the chunk rate, so post it only when it changed.
    // Once the latest audio is older than audio_max_age_ms, post `audio: null` once; fresh audio re-arms it.
    function postAudio() {
      if (!latestAudio) return;
      if (latestAudio.timestamp !== postedAudioTimestamp) {
        postedAudioTimestamp = latestAudio.timestamp;
        staleAudioPosted = false;
        postToWorld({ type: 'audio', audio: latestAudio });
      }
      if (!staleAudioPosted && Date.now() - latestAudio.timestamp > AUDIO_MAX_AGE_MS) {
        staleAudioPosted = true;
        if (DEBUG) console.log('[bridge] audio stale, posting null');
        postToWorld({ type: 'audio', audio: null });
      }
    }

    // Post the latest frame, unchanged, at the bridge framerate
    setInterval(() => {
      postAudio();
      if (!latestFrame) return;
      const frame = latestFrame;
      latestFrame = null;
      if (DEBUG) console.log('[bridge] posting frame', Object.keys(frame.bodies));
      postToWorld({ type: 'frame', frame });
      if (DEBUG) showOverlay(frame);
    }, 1000 / BRIDGE_FRAMERATE_HZ);
  }

  // This block instructs the browser to call the init function only once the DOM is fully loaded,
  // such that the script does not reference elements that don't exist.
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
