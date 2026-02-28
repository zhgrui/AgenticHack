"""
Control Logic — orchestrates face recognition and navigation via NATS.

Behaviour:
    1. Robot starts spinning (rotating around its own axis).
    2. Camera frames are received from NATS and checked for faces.
    3. When a face is detected the robot stops, performs the "hello"
       greeting action, and calls ``talkToHuman()``.
    4. When ``talkToHuman()`` returns the robot resumes spinning
       until the next person is found.

Usage:
    python control_logic.py
    python control_logic.py --url nats://192.33.91.115:4222

Requires:
    pip install nats-py opencv-python numpy Pillow
"""

from __future__ import annotations


import asyncio
import io
import json
import logging
import os
import signal
import argparse
from google import genai
import face_recognition

import cv2
import numpy as np
from PIL import Image
from nats.aio.client import Client as NATS

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("control_logic")

# ── Configuration ────────────────────────────────────────────────
DEFAULT_NATS_URL = os.getenv("NATS_URL", "nats://192.33.91.115:4222")
DEFAULT_CAMERA_SUBJECT = os.getenv("NATS_CAMERA_SUBJECT", "camera.stream")
DEFAULT_CMD_SUBJECT = os.getenv("NATS_ROBOT_CMD_SUBJECT", "robot.commands")
DEFAULT_FACE_SUBJECT = os.getenv("NATS_FACE_SUBJECT", "face.detected")

# Turning speed in rad/s (positive = counter-clockwise)
TURN_SPEED = float(os.getenv("TURN_SPEED", "0.5"))

# Minimum consecutive frames with a face before we react (debounce)
FACE_CONFIRM_FRAMES = int(os.getenv("FACE_CONFIRM_FRAMES", "3"))

# Cooldown (seconds) after talking before we look for the next person
POST_TALK_COOLDOWN = float(os.getenv("POST_TALK_COOLDOWN", "2.0"))


# ── Face detection setup ─────────────────────────────────────────
_cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
face_cascade = cv2.CascadeClassifier(_cascade_path)


def decode_frame(raw: bytes) -> np.ndarray | None:
    """Decode JPEG bytes into a BGR numpy array."""
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    except Exception as exc:
        log.warning("Failed to decode frame (%d bytes): %s", len(raw), exc)
        return None


def detect_faces(frame: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Return a list of (x, y, w, h) bounding boxes for detected faces."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = face_cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(30, 30),
    )
    # detectMultiScale returns an ndarray or an empty tuple
    if isinstance(faces, np.ndarray):
        return [tuple(f) for f in faces.tolist()]
    return []


# ── Placeholder interaction ──────────────────────────────────────


def get_face_embedding(face_img: np.ndarray) -> np.ndarray:
    """
    Convert a cropped face image (BGR numpy array) into a 128-dimensional embedding.
    Returns None if no face is detected.
    """
    # face_recognition expects RGB images
    rgb = cv2.cvtColor(face_img, cv2.COLOR_BGR2RGB)
    # Get embeddings (can return multiple if there are multiple faces)
    encodings = face_recognition.face_encodings(rgb)
    if encodings:
        return encodings[0]  # 128-d vector
    return None


def distance(face1, face2):

    return np.linalg.norm(face1 - face2)


async def talkToHuman(faces, frame) -> None:
    """
    Called when the robot has stopped in front of a person.

    Replace the body of this function with your conversation /
    interaction logic (e.g. speech synthesis, LLM chat, gesture
    control, etc.).
    """
    if faces is None or frame is None:
        log.warning("talkToHuman() called without face/frame data")
        return
    embeddings = []
    for x, y, w, h in faces:
        crop = frame[y : y + h, x : x + w]
        emb = get_face_embedding(crop)
        embeddings.append(emb)
    embedding = embeddings[0] if embeddings else None

    folder_path = "memory"
    recognized = False
    for filename in os.listdir(folder_path):
        file_path = os.path.join(folder_path, filename)
        if os.path.isfile(file_path):
            data = np.load("file_path")
            dist = distance(embedding, data)
            if dist <= 0.6:
                recognized = True
                break

    if not recognized:
        np.save(f"person-{len(os.listdir(folder_path))}.npy", embedding)
        prompt - "Say hello to this person and ask how they're doing today. This is the first time you've met them"
    else:
        prompt = "Say hello to this person again and ask how they're doing today. You have already met them"
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    response = client.models.generate_content(model="gemini-3-flash-preview", contents=prompt)

    log.info("talkToHuman() — interacting with person …")
    # TODO: implement real interaction here
    await asyncio.sleep(0)  # placeholder — returns immediately


# ── Robot command helpers (publish JSON to NATS) ─────────────────


class RobotCommander:
    """Thin wrapper that publishes robot commands to NATS."""

    def __init__(self, nc: NATS, cmd_subject: str) -> None:
        self._nc = nc
        self._subject = cmd_subject

    async def move(self, vx: float = 0.0, vy: float = 0.0, vyaw: float = 0.0) -> None:
        msg = {"cmd": "move", "params": {"vx": vx, "vy": vy, "vyaw": vyaw}}
        await self._nc.publish(self._subject, json.dumps(msg).encode())

    async def stop(self) -> None:
        msg = {"cmd": "stop"}
        await self._nc.publish(self._subject, json.dumps(msg).encode())

    async def action(self, name: str) -> None:
        msg = {"cmd": "action", "params": {"name": name}}
        await self._nc.publish(self._subject, json.dumps(msg).encode())

    async def spin(self, speed: float = TURN_SPEED) -> None:
        """Rotate in place around the robot's vertical axis."""
        await self.move(vx=0.0, vy=0.0, vyaw=speed)

    async def sit(self) -> None:
        await self.action("sit")

    async def stand_up(self) -> None:
        await self.action("stand_up")


# ── State machine ────────────────────────────────────────────────


class ControlStateMachine:
    """
    States
    ------
    SPINNING   — robot rotates looking for people
    STOPPING   — face detected, robot stops and greets (hello)
    TALKING    — interacting with the human (talkToHuman)
    RISING     — recovering stance after the interaction
    COOLDOWN   — brief pause before resuming the spin
    """

    SPINNING = "SPINNING"
    STOPPING = "STOPPING"
    TALKING = "TALKING"
    RISING = "RISING"
    COOLDOWN = "COOLDOWN"

    def __init__(self, robot: RobotCommander) -> None:
        self.robot = robot
        self.state = self.SPINNING
        self._face_count = 0  # consecutive frames with a face
        self.faces = None  # latest detected faces (list of bounding boxes)
        self.frame = None

    async def on_frame(self, frame: np.ndarray) -> None:
        """Process a single camera frame according to the current state."""

        if self.state == self.SPINNING:
            faces = detect_faces(frame)
            if faces:
                self.faces = faces
                self.frame = frame
                self._face_count += 1
                log.debug("Face detected (%d/%d)", self._face_count, FACE_CONFIRM_FRAMES)
                if self._face_count >= FACE_CONFIRM_FRAMES:
                    log.info("Face confirmed — stopping robot")
                    self._face_count = 0
                    await self._transition_to_stopping()
            else:
                self._face_count = 0

        # In all other states we ignore frames (the robot is busy).

    # ── state transitions ────────────────────────────────────────

    async def _transition_to_stopping(self) -> None:
        self.state = self.STOPPING
        await self.robot.stop()
        await asyncio.sleep(0.5)  # let the robot settle

        log.info("Greeting human with hello action")
        await self.robot.action("hello")
        await asyncio.sleep(3.0)  # wait for hello animation

        await self._transition_to_talking()

    async def _transition_to_talking(self) -> None:
        self.state = self.TALKING
        log.info("Starting interaction …")
        await talkToHuman(self.faces, self.frame)
        log.info("Interaction finished")
        await self._transition_to_rising()

    async def _transition_to_rising(self) -> None:
        self.state = self.RISING
        log.info("Recovering stance")
        await self.robot.stand_up()
        await asyncio.sleep(2.0)  # wait for stand animation

        await self._transition_to_cooldown()

    async def _transition_to_cooldown(self) -> None:
        self.state = self.COOLDOWN
        log.info("Cooldown %.1fs before resuming spin", POST_TALK_COOLDOWN)
        await asyncio.sleep(POST_TALK_COOLDOWN)

        await self._transition_to_spinning()

    async def _transition_to_spinning(self) -> None:
        self.state = self.SPINNING
        self._face_count = 0
        log.info("Resuming spin")
        await self.robot.spin()


# ── Main loop ────────────────────────────────────────────────────


async def run(args: argparse.Namespace) -> None:
    nc = NATS()
    try:
        await nc.connect(args.url)
        log.info("Connected to NATS at %s", args.url)
    except Exception as exc:
        log.error("NATS connection failed: %s", exc)
        return

    robot = RobotCommander(nc, args.cmd_subject)
    fsm = ControlStateMachine(robot)

    running = True

    def _stop() -> None:
        nonlocal running
        running = False

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass  # Windows

    # Start spinning immediately
    log.info("Starting initial spin …")
    await robot.spin()

    # ── camera frame callback ────────────────────────────────────
    frame_count = 0

    async def on_camera_frame(msg) -> None:
        nonlocal frame_count
        frame_count += 1
        frame = decode_frame(msg.data)
        if frame is None:
            return
        await fsm.on_frame(frame)

    camera_sub = await nc.subscribe(args.camera_subject, cb=on_camera_frame)
    log.info("Subscribed to '%s' — waiting for camera frames", args.camera_subject)
    log.info("Publishing commands to '%s'", args.cmd_subject)

    # Keep alive
    try:
        while running:
            # Re-send spin command periodically so the bridge doesn't
            # time out (250 ms default safety timeout).
            if fsm.state == ControlStateMachine.SPINNING:
                await robot.spin()
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        pass
    finally:
        log.info("Shutting down …")
        await robot.stop()
        await camera_sub.unsubscribe()
        await nc.close()
        log.info("Done.")


# ── CLI ──────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Control logic — spin, detect faces, interact, repeat",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_NATS_URL,
        help="NATS server URL (default: %(default)s)",
    )
    parser.add_argument(
        "--camera-subject",
        default=DEFAULT_CAMERA_SUBJECT,
        help="NATS subject for camera frames (default: %(default)s)",
    )
    parser.add_argument(
        "--cmd-subject",
        default=DEFAULT_CMD_SUBJECT,
        help="NATS subject for robot commands (default: %(default)s)",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
