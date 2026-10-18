// Mouse source (D17): a transparent layer over the world iframe that turns a held mouse button into pose_frames.
// Loaded as a module by host/host.js for source=mouse only. The maths lives in mouse-math.mjs.
import { screenToArena, wheelNotch, applyWheel, buildHeldFrame, buildReleaseFrame } from '/sources/mouse-math.mjs';

const CONFIG = window.__COBEART_CONFIG__;
const DEBUG = new URLSearchParams(location.search).has('debug') || CONFIG.debug === true;
const { role, z_initial_mm: zInitialMm, z_step_mm: zStepMm, rate_hz: rateHz } = CONFIG.sources.mouse;
const { arena } = CONFIG;

const worldFrame = document.getElementById('worldFrame');
const socket = io('/ingest', { transports: ['websocket'] });
socket.on('connect', () => console.log('[mouse] connected to /ingest', socket.id));
socket.on('disconnect', () => console.log('[mouse] disconnected from /ingest'));

// Transparent layer exactly over the iframe, so the world never receives pointer or wheel events.
const layer = document.createElement('div');
layer.id = 'mouseLayer';
Object.assign(layer.style, {
  position: 'absolute', top: '0', left: '0', width: '100%', height: '100%', zIndex: '10',
  touchAction: 'none', cursor: 'crosshair',
});
worldFrame.insertAdjacentElement('afterend', layer);

// Always-visible readout of role and height, unaffected by ?debug.
const readout = document.createElement('div');
readout.id = 'mouseReadout';
Object.assign(readout.style, {
  position: 'fixed', left: '8px', bottom: '8px', zIndex: '10000', padding: '4px 8px', pointerEvents: 'none',
  font: '12px monospace', color: 'white', background: 'rgba(0, 0, 0, 0.6)', borderRadius: '4px',
});
document.body.appendChild(readout);

let z = zInitialMm;
let pointer = null; // Last client position while a button is held, else null
let sendTimer = null;

const showReadout = () => { readout.textContent = `mouse: ${role} | z ${z} mm`; };
showReadout();

const sourceTimeS = () => performance.now() / 1000;

function sendHeld() {
  const { x, y } = screenToArena(pointer.clientX, pointer.clientY, layer.getBoundingClientRect(), arena);
  const frame = buildHeldFrame(role, { x, y, z }, sourceTimeS());
  if (DEBUG) console.log('[mouse] pose_frame', frame);
  socket.emit('pose_frame', frame);
}

function press(e) {
  pointer = { clientX: e.clientX, clientY: e.clientY };
  if (sendTimer === null) {
    sendHeld();
    sendTimer = setInterval(sendHeld, 1000 / rateHz);
  }
}

// One empty frame, then silence: a vanished body reads as a release, not a dropout.
function release() {
  if (sendTimer === null) return;
  clearInterval(sendTimer);
  sendTimer = null;
  pointer = null;
  socket.emit('pose_frame', buildReleaseFrame(sourceTimeS()));
}

layer.addEventListener('pointerdown', (e) => {
  layer.setPointerCapture(e.pointerId);
  press(e);
});
layer.addEventListener('pointermove', (e) => {
  if (pointer !== null) pointer = { clientX: e.clientX, clientY: e.clientY };
});
// Any button still down keeps the body alive (e.buttons is the remaining mask)
layer.addEventListener('pointerup', (e) => { if (e.buttons === 0) release(); });
layer.addEventListener('pointercancel', release);
layer.addEventListener('contextmenu', (e) => e.preventDefault());
document.documentElement.addEventListener('mouseleave', release);
window.addEventListener('blur', release);

layer.addEventListener('wheel', (e) => {
  e.preventDefault();
  if (e.deltaY === 0) return; // horizontal scroll
  z = applyWheel(z, wheelNotch(e.deltaY), zStepMm, arena.z);
  showReadout();
}, { passive: false });
