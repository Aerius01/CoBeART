import logging
import time
import threading
from typing import Optional, Dict, Any

import socketio
from socketio.exceptions import SocketIOError

from cobeart.audiocapture.utils import get_socketio_url

logger: logging.Logger = logging.getLogger(__name__)

RECONNECT_INTERVAL_S: float = 5.0
MAX_RECONNECT_INTERVAL_S: float = 10.0


class AudioEmitter:
    """
    Thin Socket.IO wrapper that immediately emits received metrics.

    No buffering, no rate limiting - pure pass-through from AudioCapturer to Socket.IO.
    """

    def __init__(
        self,
        socketio_url: Optional[str] = None,
        namespace: str = "/audio",
    ) -> None:
        self.socketio_url = socketio_url or get_socketio_url()
        self.namespace = namespace

        self._sio = socketio.Client(reconnection=True, reconnection_attempts=0)
        self._setup_handlers()

        self._connected = threading.Event()
        self._connect_failing = False
        self._emit_failing = False
        self._reconnect_thread = None
        self._stop_event = threading.Event()

        # Start connection
        self._connect()
        self._start_reconnect_loop()

    def _setup_handlers(self) -> None:
        """Setup Socket.IO event handlers."""
        @self._sio.on("connect", namespace=self.namespace)
        def _on_connect() -> None:
            self._connected.set()
            self._connect_failing = False
            logger.info("Connected to %s%s", self.socketio_url, self.namespace)

        @self._sio.on("disconnect", namespace=self.namespace)
        def _on_disconnect() -> None:
            self._connected.clear()
            logger.warning("Disconnected from %s%s", self.socketio_url, self.namespace)

        @self._sio.on("connect_error", namespace=self.namespace)
        def _on_connect_error(data: Any) -> None:
            self._log_connect_failure(data)

    def _connect(self) -> None:
        """Attempt to connect to Socket.IO server."""
        try:
            self._sio.connect(
                self.socketio_url,
                transports=["websocket"],
                namespaces=[self.namespace],
                wait=False
            )
        except SocketIOError as exc:
            self._log_connect_failure(exc)

    def _log_connect_failure(self, error: object) -> None:
        """Warn on the first failure of a streak; later ones go to DEBUG until reconnected."""
        if not self._connect_failing:
            self._connect_failing = True
            logger.warning(
                "Cannot reach %s%s (%s); retrying in the background", self.socketio_url, self.namespace, error)
        else:
            logger.debug("Still cannot reach %s%s: %s", self.socketio_url, self.namespace, error)

    def _start_reconnect_loop(self) -> None:
        """Start background thread to handle reconnection."""
        def _reconnect_loop():
            interval = RECONNECT_INTERVAL_S
            last_attempt = 0.0

            while not self._stop_event.is_set():
                now = time.monotonic()
                if self._connected.is_set():
                    interval = RECONNECT_INTERVAL_S
                elif not self._sio.connected and now - last_attempt >= interval:
                    self._connect()
                    last_attempt = now
                    interval = min(interval * 2, MAX_RECONNECT_INTERVAL_S)
                self._stop_event.wait(1.0)

        self._reconnect_thread = threading.Thread(
            target=_reconnect_loop,
            name="audio-emitter-reconnect",
            daemon=True
        )
        self._reconnect_thread.start()

    def is_connected(self) -> bool:
        """
        Check if the emitter is currently connected to the Socket.IO server.

        Returns:
            True if connected, False otherwise
        """
        return self._connected.is_set() and self._sio.connected

    def push_metrics(self, payload: Dict[str, Any]) -> None:
        """
        Immediately emit metrics to Socket.IO (no buffering, no rate limiting).

        Args:
            payload: Dictionary with all computed audio metrics
        """
        if not self._connected.is_set():
            return

        try:
            self._sio.emit("audio_metrics", payload, namespace=self.namespace)
            self._emit_failing = False
        except SocketIOError as exc:
            # Emission runs at ~100 Hz: warn once per failure streak only
            if not self._emit_failing:
                self._emit_failing = True
                logger.warning("Emit to %s failed (%s); dropping metrics until it recovers", self.namespace, exc)

    def stop(self) -> None:
        """Stop the emitter and disconnect from server."""
        self._stop_event.set()
        if self._reconnect_thread is not None:
            self._reconnect_thread.join(timeout=1.0)
        try:
            self._sio.disconnect()
        except SocketIOError as exc:
            logger.warning("Disconnect failed: %s", exc)
        logger.info("Emitter stopped")
