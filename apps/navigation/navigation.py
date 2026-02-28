"""
Navigation module — subscribes to camera frames from NATS and
sends movement commands to the Go2 robot via NATS.

Usage:
    python navigation.py                       # defaults
    python navigation.py --url nats://192.33.91.115:4222
    NATS_URL=nats://192.33.91.115:4222 python navigation.py

Requires:
    pip install nats-py opencv-python numpy Pillow
"""

import asyncio
import io
import json
import logging
import os
import signal
import sys
import argparse

import cv2
import numpy as np
from PIL import Image
from nats.aio.client import Client as NATS

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("navigation")

# ── Configuration (env-var defaults match the rest of the project) ─
DEFAULT_NATS_URL = os.getenv("NATS_URL", "nats://192.33.91.115:4222")
DEFAULT_CAMERA_SUBJECT = os.getenv("NATS_CAMERA_SUBJECT", "camera.stream")
DEFAULT_NAV_SUBJECT = os.getenv("NATS_NAV_SUBJECT", "nav.commands")
DEFAULT_CMD_SUBJECT = os.getenv("NATS_ROBOT_CMD_SUBJECT", "robot.commands")


# ── Frame processing ──────────────────────────────────────────────

def decode_frame(raw: bytes) -> np.ndarray | None:
    """Decode JPEG bytes from NATS into a BGR numpy array (OpenCV format)."""
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        return frame
    except Exception as exc:
        log.warning("Failed to decode frame (%d bytes): %s", len(raw), exc)
        return None


def process_frame(frame: np.ndarray) -> dict:
    """
    Analyse a camera frame and decide what movement to make.

    Returns a dict like:
        {"vx": 0.3, "vy": 0.0, "vyaw": 0.0}   — move forward
        {"vx": 0.0, "vy": 0.0, "vyaw": 0.5}    — turn left
        {"stop": True}                           — stop

    *** Replace this stub with your real navigation logic ***
    (e.g. obstacle detection, lane following, SLAM, ML model, etc.)
    """
    h, w = frame.shape[:2]

    # --- Example: simple colour-blob avoidance stub ---
    # Divide the image into left / centre / right thirds
    third = w // 3
    left_region = frame[:, :third]
    centre_region = frame[:, third : 2 * third]
    right_region = frame[:, 2 * third :]

    # Compute mean brightness per region as a trivial "obstacle" proxy
    l_bright = np.mean(left_region)
    c_bright = np.mean(centre_region)
    r_bright = np.mean(right_region)

    log.debug("brightness  L=%.1f  C=%.1f  R=%.1f", l_bright, c_bright, r_bright)

    # Placeholder decision — go forward slowly
    return {"vx": 0.0, "vy": 0.0, "vyaw": 0.0}


# ── Main async loop ──────────────────────────────────────────────

async def run(args):
    # Connect to NATS
    nc = NATS()
    try:
        await nc.connect(args.url)
        log.info("Connected to NATS at %s", args.url)
    except Exception as exc:
        log.error("NATS connection failed: %s", exc)
        return

    cmd_subject = args.cmd_subject

    # ── Helper to send robot commands via NATS ──
    async def robot_move(vx: float = 0.0, vy: float = 0.0, vyaw: float = 0.0):
        msg = {"cmd": "move", "params": {"vx": vx, "vy": vy, "vyaw": vyaw}}
        await nc.publish(cmd_subject, json.dumps(msg).encode())

    async def robot_stop():
        msg = {"cmd": "stop"}
        await nc.publish(cmd_subject, json.dumps(msg).encode())

    async def robot_action(name: str):
        msg = {"cmd": "action", "params": {"name": name}}
        await nc.publish(cmd_subject, json.dumps(msg).encode())

    running = True

    # Graceful shutdown
    def _stop():
        nonlocal running
        running = False

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler — use fallback
            pass

    # --- Camera frame subscription ---
    frame_count = 0

    async def on_camera_frame(msg):
        nonlocal frame_count
        frame_count += 1
        frame = decode_frame(msg.data)
        if frame is None:
            return

        decision = process_frame(frame)

        if decision.get("stop"):
            await robot_stop()
            log.info("[frame %d] → STOP", frame_count)
        else:
            vx = decision.get("vx", 0.0)
            vy = decision.get("vy", 0.0)
            vyaw = decision.get("vyaw", 0.0)
            await robot_move(vx, vy, vyaw)
            if frame_count % 20 == 0:  # log every 20th frame to avoid spam
                log.info("[frame %d] → move vx=%.2f vy=%.2f vyaw=%.2f", frame_count, vx, vy, vyaw)

    camera_sub = await nc.subscribe(args.camera_subject, cb=on_camera_frame)
    log.info("Subscribed to '%s' — waiting for camera frames…", args.camera_subject)
    log.info("Publishing commands to '%s'", cmd_subject)

    # --- (Optional) navigation command subscription ---
    async def on_nav_command(msg):
        """Receive external navigation commands via NATS (JSON)."""
        try:
            cmd = json.loads(msg.data.decode())
            log.info("Nav command received: %s", cmd)

            if "action" in cmd:
                await robot_action(cmd["action"])
            elif "move" in cmd:
                m = cmd["move"]
                await robot_move(m.get("vx", 0), m.get("vy", 0), m.get("vyaw", 0))
            elif cmd.get("stop"):
                await robot_stop()
        except Exception as exc:
            log.warning("Bad nav command: %s", exc)

    nav_sub = await nc.subscribe(args.nav_subject, cb=on_nav_command)
    log.info("Subscribed to '%s' — listening for nav commands…", args.nav_subject)

    # Keep running until signalled
    try:
        while running:
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        pass
    finally:
        log.info("Shutting down…")
        await robot_stop()
        await camera_sub.unsubscribe()
        await nav_sub.unsubscribe()
        await nc.close()
        log.info("Done.")


# ── CLI ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Go2 navigation — NATS camera → decision → NATS commands",
    )
    parser.add_argument("--url", default=DEFAULT_NATS_URL,
                        help="NATS server URL (default: %(default)s)")
    parser.add_argument("--camera-subject", default=DEFAULT_CAMERA_SUBJECT,
                        help="NATS subject for camera frames (default: %(default)s)")
    parser.add_argument("--nav-subject", default=DEFAULT_NAV_SUBJECT,
                        help="NATS subject for nav commands (default: %(default)s)")
    parser.add_argument("--cmd-subject", default=DEFAULT_CMD_SUBJECT,
                        help="NATS subject to publish robot commands (default: %(default)s)")
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
