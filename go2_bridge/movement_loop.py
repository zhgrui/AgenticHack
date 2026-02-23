"""20 Hz movement heartbeat thread with timeout safety."""

from __future__ import annotations

import logging
import threading
import time

from . import config

log = logging.getLogger(__name__)


class MovementLoop:
    """Continuously relays the last commanded velocity to the robot at MOVE_HZ.

    If no new command arrives within MOVE_TIMEOUT_MS, velocity resets to zero
    and one final Move(0,0,0) is sent.
    """

    def __init__(self, robot) -> None:
        self._robot = robot
        self._lock = threading.Lock()
        self._vx: float = 0.0
        self._vy: float = 0.0
        self._vyaw: float = 0.0
        self._last_cmd_time: float = 0.0
        self._running = False
        self._thread: threading.Thread | None = None

    def set_velocity(self, vx: float, vy: float, vyaw: float) -> None:
        with self._lock:
            self._vx = vx
            self._vy = vy
            self._vyaw = vyaw
            self._last_cmd_time = time.monotonic()

    def stop(self) -> None:
        """Zero velocity immediately."""
        with self._lock:
            self._vx = 0.0
            self._vy = 0.0
            self._vyaw = 0.0
            self._last_cmd_time = 0.0

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="move-loop")
        self._thread.start()
        log.info("Movement loop started at %d Hz", config.MOVE_HZ)

    def shutdown(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        interval = 1.0 / config.MOVE_HZ
        timeout_s = config.MOVE_TIMEOUT_MS / 1000.0
        sent_zero = False

        while self._running:
            with self._lock:
                vx, vy, vyaw = self._vx, self._vy, self._vyaw
                last_t = self._last_cmd_time

            now = time.monotonic()

            # Timeout check
            if last_t > 0 and (now - last_t) > timeout_s:
                if not sent_zero:
                    log.debug("Movement timeout — zeroing velocity")
                    self._robot.move(0.0, 0.0, 0.0)
                    with self._lock:
                        self._vx = 0.0
                        self._vy = 0.0
                        self._vyaw = 0.0
                        self._last_cmd_time = 0.0
                    sent_zero = True
            elif last_t > 0:
                # Active command — relay velocity
                try:
                    self._robot.move(vx, vy, vyaw)
                    log.debug("Move sent: vx=%.2f vy=%.2f vyaw=%.2f", vx, vy, vyaw)
                except Exception:
                    log.exception("Move command failed")
                sent_zero = False
            # else: no command ever received — do nothing

            time.sleep(interval)
