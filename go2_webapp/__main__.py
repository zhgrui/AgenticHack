"""FastAPI web app: python -m go2_webapp"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import uvicorn
import zmq
import zmq.asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

BRIDGE_HOST = os.getenv("GO2_BRIDGE_HOST", "localhost")
CMD_PORT = int(os.getenv("GO2_ZMQ_CMD_PORT", "5555"))
PUB_PORT = int(os.getenv("GO2_ZMQ_PUB_PORT", "5556"))
WEBAPP_HOST = os.getenv("GO2_WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("GO2_WEBAPP_PORT", "8080"))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("go2_webapp")

app = FastAPI(title="Go2 Web Controller")

STATIC_DIR = Path(__file__).parent / "static"

# ── ZMQ helpers ───────────────────────────────────────────────────

_zmq_ctx: zmq.asyncio.Context | None = None


def get_zmq_ctx() -> zmq.asyncio.Context:
    global _zmq_ctx
    if _zmq_ctx is None:
        _zmq_ctx = zmq.asyncio.Context()
    return _zmq_ctx


async def bridge_command(cmd: str, params: dict | None = None) -> dict:
    """Send a command to the bridge and return the response."""
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


# ── REST endpoint ─────────────────────────────────────────────────

@app.post("/api/command")
async def api_command(request: Request):
    body = await request.json()
    cmd = body.get("cmd", "")
    params = body.get("params")
    resp = await bridge_command(cmd, params)
    return JSONResponse(content=resp)


# ── WebSocket: camera stream ─────────────────────────────────────

camera_clients: set[WebSocket] = set()


async def camera_relay():
    """Background task: subscribe to bridge PUB and relay to WebSocket clients."""
    ctx = get_zmq_ctx()
    sub = ctx.socket(zmq.SUB)
    sub.connect(f"tcp://{BRIDGE_HOST}:{PUB_PORT}")
    sub.subscribe(b"camera")
    log.info("Camera relay subscribed to tcp://%s:%d", BRIDGE_HOST, PUB_PORT)

    while True:
        try:
            parts = await sub.recv_multipart()
            if len(parts) < 2:
                continue
            jpeg_data = parts[1]
            dead: list[WebSocket] = []
            for ws in list(camera_clients):
                try:
                    await ws.send_bytes(jpeg_data)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                camera_clients.discard(ws)
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("Camera relay error")
            await asyncio.sleep(0.5)

    sub.close()


@app.websocket("/ws/camera")
async def ws_camera(websocket: WebSocket):
    await websocket.accept()
    camera_clients.add(websocket)
    log.info("Camera WebSocket client connected (%d total)", len(camera_clients))
    try:
        while True:
            # Keep connection alive; client doesn't send meaningful data
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        camera_clients.discard(websocket)
        log.info("Camera WebSocket client disconnected (%d total)", len(camera_clients))


# ── Startup / Shutdown ────────────────────────────────────────────

_camera_task: asyncio.Task | None = None


@app.on_event("startup")
async def on_startup():
    global _camera_task
    _camera_task = asyncio.create_task(camera_relay())
    log.info("Go2 Web App started")


@app.on_event("shutdown")
async def on_shutdown():
    if _camera_task:
        _camera_task.cancel()
        try:
            await _camera_task
        except asyncio.CancelledError:
            pass


# ── Static files (must be last so it doesn't shadow API routes) ──

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

# ── Run ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host=WEBAPP_HOST, port=WEBAPP_PORT)


def main():
    uvicorn.run(app, host=WEBAPP_HOST, port=WEBAPP_PORT)
