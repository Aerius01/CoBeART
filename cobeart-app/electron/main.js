// This file serves as the main entry point for the Electron application.
// It is responsible for two primary tasks:
// 1. Running a local web and WebSocket server to handle data from the OptiTrack system.
// 2. Creating a native desktop window (renderer process) that displays the front-end visualization.

const { app, BrowserWindow } = require('electron');
const path = require('path');

// The hub (Express + Socket.IO) runs directly within the main process (self-contained).
const { createHub } = require('./hub');
const { loadConfig, resolvePort, hubConfig, frontendConfig } = require('./config');

const PUBLIC_DIR = path.join(__dirname, '..', 'public');

let hub;

// Starts the integrated web server via the shared hub.
async function startHttpServer(config, port) {
  hub = await createHub({
    port,
    host: config.network.socketio.host,
    publicDir: PUBLIC_DIR,
    metrics: hubConfig(config).metrics,
    frontendConfig: frontendConfig(config)
  });
}

// Creates and configures the main application window.
function createWindow(config, url) {
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
  // Wait for the window to be ready before opening DevTools
  win.webContents.on('did-finish-load', () => {
    // Show window once content is loaded to prevent GPU errors
    win.show();

    if (config.debug) {
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

  // The launcher page picks the world and source and navigates this same window to the host page.
  const url = `${origin}/`;
  createWindow(config, url);

  // Handle macOS-specific behavior for re-creating a window (back on the launcher page).
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow(config, url);
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