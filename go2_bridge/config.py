"""Configuration from environment variables."""

import os


NETWORK_INTERFACE: str = os.getenv("GO2_NETWORK_INTERFACE", "eno1")

ZMQ_CMD_PORT: int = int(os.getenv("GO2_ZMQ_CMD_PORT", "5555"))
ZMQ_PUB_PORT: int = int(os.getenv("GO2_ZMQ_PUB_PORT", "5556"))

MOVE_HZ: int = int(os.getenv("GO2_MOVE_HZ", "20"))
MOVE_TIMEOUT_MS: int = int(os.getenv("GO2_MOVE_TIMEOUT_MS", "250"))

CAMERA_FPS: int = int(os.getenv("GO2_CAMERA_FPS", "10"))

OBSTACLE_AVOIDANCE: bool = os.getenv("GO2_OBSTACLE_AVOIDANCE", "1") == "1"

BRIDGE_HOST: str = os.getenv("GO2_BRIDGE_HOST", "localhost")
WEBAPP_HOST: str = os.getenv("GO2_WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT: int = int(os.getenv("GO2_WEBAPP_PORT", "8080"))
