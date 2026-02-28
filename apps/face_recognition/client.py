"""
NATS camera stream viewer and Web Controller using FastAPI.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import argparse
import logging
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from nats.aio.client import Client as NATS

import uvicorn
import zmq
import zmq.asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("face_recognition")

app = FastAPI(title="Go2 Face Recognition App")
STATIC_DIR = Path(__file__).parent / "static"

# NATS setup
NATS_URL = os.getenv("NATS_URL", "nats://192.33.91.115:4222")
NATS_SUBJECT = os.getenv("NATS_CAMERA_SUBJECT", "camera.stream")

# ZMQ setup
BRIDGE_HOST = os.getenv("GO2_BRIDGE_HOST", "localhost")
CMD_PORT = int(os.getenv("GO2_ZMQ_CMD_PORT", "5555"))
WEBAPP_HOST = os.getenv("GO2_WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("GO2_WEBAPP_PORT", "8080"))

_zmq_ctx: zmq.asyncio.Context | None = None

def get_zmq_ctx() -> zmq.asyncio.Context:
    global _zmq_ctx
    if _zmq_ctx is None:
        _zmq_ctx = zmq.asyncio.Context()
    return _zmq_ctx

async def bridge_command(cmd: str, params: dict | None = None) -> dict:
    ctx = get_zmq_ctx()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 5000)
    sock.connect(f"tcp://{BRIDGE_HOST}:{CMD_PORT}")
    msg: dict = {"cmd": cmd}
    if params:
        msg["params"] = params
    await sock.send(json.dumps(msg).encode())
    raw = await sock.recv()
    sock.close()
    return json.loads(raw)

@app.post("/api/command")
async def api_command(request: Request):
    body = await request.json()
    cmd = body.get("cmd", "")
    params = body.get("params")
    try:
        resp = await bridge_command(cmd, params)
        return JSONResponse(content=resp)
    except Exception as e:
        log.error(f"Command error: {e}")
        return JSONResponse(content={"ok": False, "msg": str(e)}, status_code=500)

camera_clients: set[WebSocket] = set()

async def face_recognition_relay():
    nc = NATS()
    try:
        await nc.connect(NATS_URL)
        log.info(f"Connected to NATS at {NATS_URL}")
    except Exception as e:
        log.error(f"Failed to connect: {e}")
        return

    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

    last_speech_time = 0

    async def on_message(msg):
        nonlocal last_speech_time
        data = msg.data
        if not camera_clients:
            return  # skip if no clients watching
        
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
            
            if len(faces) > 0:
                # Check if there is at least one face that is large enough (e.g. >= 60x60 pixels)
                large_enough_face = any(w >= 200 and h >= 200 for (x, y, w, h) in faces)
                
                if large_enough_face:
                    current_time = asyncio.get_event_loop().time()
                    # 3-second cooldown so it doesn't overlap/spam the voice
                    if current_time - last_speech_time > 3.0:
                        last_speech_time = current_time
                        log.info("Speaking: hello human!")
                        # Use macOS built-in TTS without blocking the camera receive loop
                        await asyncio.create_subprocess_shell("say 'hello human'")

            for (x, y, w, h) in faces:
                cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
                cv2.putText(frame, "Face", (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
            
            # Encode to JPEG
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret: return
            jpeg_data = buffer.tobytes()
            
            dead = []
            for ws in list(camera_clients):
                try:
                    await ws.send_bytes(jpeg_data)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                camera_clients.discard(ws)
        except Exception as e:
            log.error(f"Failed to process frame: {e}")

    sub = await nc.subscribe(NATS_SUBJECT, cb=on_message)
    log.info(f"Subscribed to NATS '{NATS_SUBJECT}'")
    
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await sub.unsubscribe()
        await nc.close()

@app.websocket("/ws/camera")
async def ws_camera(websocket: WebSocket):
    await websocket.accept()
    camera_clients.add(websocket)
    log.info("Camera WebSocket client connected (%d total)", len(camera_clients))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        camera_clients.discard(websocket)
        log.info("Camera WebSocket client disconnected (%d total)", len(camera_clients))

_camera_task: asyncio.Task | None = None

@app.on_event("startup")
async def on_startup():
    global _camera_task
    _camera_task = asyncio.create_task(face_recognition_relay())

@app.on_event("shutdown")
async def on_shutdown():
    if _camera_task:
        _camera_task.cancel()
        try:
            await _camera_task
        except asyncio.CancelledError:
            pass

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

def main():
    global NATS_URL, NATS_SUBJECT
    parser = argparse.ArgumentParser(description="Live face recognition viewer and WebApp")
    parser.add_argument("--url", default=NATS_URL)
    parser.add_argument("--subject", default=NATS_SUBJECT)
    args = parser.parse_args()
    
    # Update global NATS settings from args if provided
    NATS_URL = args.url
    NATS_SUBJECT = args.subject

    uvicorn.run(app, host=WEBAPP_HOST, port=WEBAPP_PORT)

if __name__ == "__main__":
    main()
