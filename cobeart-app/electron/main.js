// This file serves as the main entry point for the Electron application.
// It is responsible for two primary tasks:
// 1. Running a local web and WebSocket server to handle data from the OptiTrack system.
// 2. Creating a native desktop window (renderer process) that displays the front-end visualization.

const { app, BrowserWindow, dialog } = require('electron');
const path = require('path');

// The hub (Express + Socket.IO) runs directly within the main process (self-contained).
const { createHub } = require('./hub');

const PORT = 3000;
let hub;

// Starts the integrated web server via the shared hub.
async function startHttpServer() {
  hub = await createHub({ port: PORT, host: '127.0.0.1', publicDir: path.join(__dirname, '..', 'public') });
}

// Creates and configures the main application window.
function createWindow(shader, usePerfMode) {
  const win = new BrowserWindow({
    width: 1050,
    height: 1050,
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
  let url = `http://127.0.0.1:${PORT}/`;
  if (shader === 'molten') {
    url = `http://127.0.0.1:${PORT}/molten/`;
    if (usePerfMode) {
      url += '?performance=true';
    }
  } else if (shader === 'ink') {
    url = `http://127.0.0.1:${PORT}/ink/`;
  } else if (shader === 'mixed') {
    url = `http://127.0.0.1:${PORT}/composite/`;
  }
  // Wait for the window to be ready before opening DevTools and injecting variables
  win.webContents.on('did-finish-load', () => {
    win.webContents.executeJavaScript(`window.__SOCKET_PORT__=${PORT}`);

    // Show window once content is loaded to prevent GPU errors
    win.show();

    // Opening devtools breaks ink visualization
    // Only open devtools if shader is not 'ink', and do it after page load
    if (shader !== 'ink') {
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
  await startHttpServer();

  const choice = dialog.showMessageBoxSync({
    type: 'question',
    buttons: ['Splat', 'Molten', 'Ink', 'Mixed'],
    defaultId: 0,
    title: 'Choose Visualization',
    message: 'Which visualization would you like to use?',
    detail: 'Splat: fluid simulation. Molten: reflective shader. Ink: Dark fluid with washed contours. Mixed: spatial blend.',
    checkboxLabel: 'Performance Mode (Molten only)',
    checkboxChecked: false
  });

  const shader = ['splat', 'molten', 'ink', 'mixed'][choice];
  const usePerfMode = choice.checkboxChecked;

  createWindow(shader, usePerfMode);

  // Handle macOS-specific behavior for re-creating a window.
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow(shader, usePerfMode);
  });
});

// Defines the application's behavior when all windows are closed.
app.on('window-all-closed', () => {
  // On Windows and Linux, quit the app. On macOS, apps typically stay running.
  if (process.platform !== 'darwin') {
    hub?.close();
    app.quit();
  }
});