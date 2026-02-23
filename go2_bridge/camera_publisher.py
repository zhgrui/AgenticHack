"""Camera frame ZMQ PUB publisher thread."""

from __future__ import annotations

import logging
import threading
import time

import zmq

from . import config

log = logging.getLogger(__name__)


class CameraPublisher:
    """Grabs frames from VideoClient and publishes them on a ZMQ PUB socket."""

    def __init__(self, robot, ctx: zmq.Context) -> None:
        self._robot = robot
        self._ctx = ctx
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="camera-pub")
        self._thread.start()
        log.info("Camera publisher started at %d FPS on port %d", config.CAMERA_FPS, config.ZMQ_PUB_PORT)

    def shutdown(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        pub = self._ctx.socket(zmq.PUB)
        pub.bind(f"tcp://*:{config.ZMQ_PUB_PORT}")
        interval = 1.0 / config.CAMERA_FPS

        while self._running:
            try:
                code, data = self._robot.get_image()
                if code == 0 and data:
                    jpeg_bytes = bytes(data) if not isinstance(data, bytes) else data
                    pub.send_multipart([b"camera", jpeg_bytes])
            except Exception:
                log.exception("Camera grab failed")

            time.sleep(interval)

        pub.close()
