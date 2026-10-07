// This file serves as the main entry point for the Electron application.
// It is responsible for two primary tasks:
// 1. Running a local web and WebSocket server to handle data from the OptiTrack system.
// 2. Creating a native desktop window (renderer process) that displays the front-end visualization.

const { app, BrowserWindow, dialog } = require('electron');
const path = require('path');

// The hub (Express + Socket.IO) runs directly within the main process (self-contained).
const { createHub } = require('./hub');
const { loadConfig, resolvePort, frontendConfig } = require('./config');

let hub;

// Starts the integrated web server via the shared hub.
async function startHttpServer(config, port) {
  hub = await createHub({
    port,
    host: config.network.socketio.host,
    publicDir: path.join(__dirname, '..', 'public'),
    audioMaxAgeMs: config.hub.audio_max_age_ms,
    maxBodies: config.tracking.max_num_objects,
    frontendConfig: frontendConfig(config)
  });
}

// Maps the dialog choice to the page the window loads.
function resolveViewUrl(origin, shader, usePerfMode) {
  switch (shader) {
    case 'molten':
      return `${origin}/molten/${usePerfMode ? '?performance=true' : ''}`;
    case 'ink':
      return `${origin}/ink/`;
    case 'mixed':
      return `${origin}/composite/`;
    default:
      return `${origin}/`;
  }
}

// Creates and configures the main application window.
function createWindow(config, origin, shader, usePerfMode) {
  const win = new BrowserWindow({
    width: config.window.width,
    height: config.window.height,
    useContentSize: true,
    backgroundColor: '#000000',
    autoHideMenuBar: true,
    show: false, // Don't show window until it's ready
    webPreferences: {
      // The preload script is a bridge between Electron's Node.js environment
      // and the sandboxed browser environment of the window, allowing for
      // secure, controlled communication.
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false
    }
  });

  // The window loads its content from the local server, just like a web browser.
  const url = resolveViewUrl(origin, shader, usePerfMode);
  // Wait for the window to be ready before opening DevTools
  win.webContents.on('did-finish-load', () => {
    // Show window once content is loaded to prevent GPU errors
    win.show();

    // Opening devtools breaks ink visualization, so never open it there.
    if (config.debug && shader !== 'ink') {
      // Small delay to ensure page is fully initialized
      setTimeout(() => {
        win.webContents.openDevTools();
      }, 100);
    }
  });

  // Handle the case where the window is ready before content loads
  win.once('ready-to-show', () => {
    // Window is ready to be shown, but we'll wait for did-finish-load
  });

  win.loadURL(url);
}

// Electron's initialization is asynchronous. This block executes once the app is ready.
app.whenReady().then(async () => {
  const config = loadConfig();
  const port = resolvePort(config);
  const origin = `http://${config.network.socketio.host}:${port}`;
  await startHttpServer(config, port);

  const { response, checkboxChecked } = await dialog.showMessageBox({
    type: 'question',
    buttons: ['Splat', 'Molten', 'Ink', 'Mixed'],
    defaultId: 0,
    title: 'Choose Visualization',
    message: 'Which visualization would you like to use?',
    detail: 'Splat: fluid simulation. Molten: reflective shader. Ink: Dark fluid with washed contours. Mixed: spatial blend.',
    checkboxLabel: 'Performance Mode (Molten only)',
    checkboxChecked: false
  });

  const shader = ['splat', 'molten', 'ink', 'mixed'][response];
  const usePerfMode = checkboxChecked;

  createWindow(config, origin, shader, usePerfMode);

  // Handle macOS-specific behavior for re-creating a window.
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow(config, origin, shader, usePerfMode);
  });
}).catch((err) => {
  console.error('Startup failed:', err);
  hub?.close();
  app.exit(1);
});

// Defines the application's behavior when all windows are closed.
app.on('window-all-closed', () => {
  // On Windows and Linux, quit the app. On macOS, apps typically stay running.
  if (process.platform !== 'darwin') {
    hub?.close();
    app.quit();
  }
});