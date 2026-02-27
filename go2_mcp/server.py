"""MCP server that proxies commands to the Go2 ZMQ bridge."""

from __future__ import annotations

import base64
import json
import logging
import os
import sys

import zmq
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent

# Logging must go to stderr — stdout is reserved for MCP JSON-RPC protocol
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
log = logging.getLogger("go2_mcp")

BRIDGE_HOST = os.getenv("GO2_BRIDGE_HOST", "localhost")
CMD_PORT = int(os.getenv("GO2_ZMQ_CMD_PORT", "5555"))
PUB_PORT = int(os.getenv("GO2_ZMQ_PUB_PORT", "5556"))

mcp = FastMCP("go2-robot")

# ── ZMQ helpers ───────────────────────────────────────────────────

_zmq_ctx: zmq.Context | None = None


def _get_ctx() -> zmq.Context:
    global _zmq_ctx
    if _zmq_ctx is None:
        _zmq_ctx = zmq.Context()
    return _zmq_ctx


def _send_command(cmd: str, params: dict | None = None) -> dict:
    """Send a command to the bridge and return the parsed response."""
    ctx = _get_ctx()
    sock = ctx.socket(zmq.REQ)
    sock.setsockopt(zmq.RCVTIMEO, 5000)
    sock.connect(f"tcp://{BRIDGE_HOST}:{CMD_PORT}")
    msg: dict = {"cmd": cmd}
    if params:
        msg["params"] = params
    sock.send_json(msg)
    resp = sock.recv_json()
    sock.close()
    return resp


def _format_response(resp: dict) -> str:
    """Format a bridge response as readable text."""
    ok = resp.get("ok", False)
    msg = resp.get("msg", "")
    data = resp.get("data")
    parts = [f"{'OK' if ok else 'ERROR'}: {msg}"]
    if data:
        parts.append(json.dumps(data, indent=2))
    return "\n".join(parts)


# ── Tools ─────────────────────────────────────────────────────────


@mcp.tool()
def get_status() -> str:
    """Get the current status of the Go2 robot.

    Returns obstacle avoidance state, speed level, and light state.
    """
    resp = _send_command("status")
    return _format_response(resp)


@mcp.tool()
def list_actions() -> str:
    """List all available robot actions (e.g. stand_up, sit, dance1, hello)."""
    resp = _send_command("list_actions")
    return _format_response(resp)


@mcp.tool()
def execute_action(name: str) -> str:
    """Execute a named action on the robot.

    Args:
        name: Action name (e.g. stand_up, stand_down, sit, hello, stretch,
              dance1, dance2, heart, front_flip, front_jump, back_flip,
              left_flip, hand_stand, balance_stand, recovery_stand, damp, stop_move)
    """
    resp = _send_command("action", {"name": name})
    return _format_response(resp)


@mcp.tool()
def move(vx: float, vy: float = 0.0, vyaw: float = 0.0) -> str:
    """Move the robot with the given velocity.

    The robot must be standing first. Velocities are in m/s (linear) and rad/s (rotation).
    The movement continues at the given velocity until a stop command or new move command.
    The bridge has a 250ms safety timeout — if no new command arrives, the robot stops automatically.

    Args:
        vx: Forward/backward velocity (-1.0 to 1.0). Positive = forward.
        vy: Left/right velocity (-1.0 to 1.0). Positive = left.
        vyaw: Rotation velocity (-1.0 to 1.0). Positive = counter-clockwise.
    """
    resp = _send_command("move", {"vx": vx, "vy": vy, "vyaw": vyaw})
    return _format_response(resp)


@mcp.tool()
def stop() -> str:
    """Immediately stop all robot movement. Use this as an emergency stop or to halt motion."""
    resp = _send_command("stop")
    return _format_response(resp)


@mcp.tool()
def set_obstacle_avoidance(enabled: bool) -> str:
    """Enable or disable the robot's obstacle avoidance system.

    When enabled, the robot uses its sensors to avoid collisions during movement.
    Enabled by default at bridge startup.

    Args:
        enabled: True to enable, False to disable.
    """
    resp = _send_command("obstacle_avoidance", {"enabled": enabled})
    return _format_response(resp)


@mcp.tool()
def set_speed_level(level: int) -> str:
    """Set the robot's movement speed level.

    Args:
        level: Speed level from 1 (slow) to 3 (fast).
    """
    resp = _send_command("speed_level", {"level": level})
    return _format_response(resp)


@mcp.tool()
def set_light(on: bool) -> str:
    """Turn the robot's head light on or off.

    Args:
        on: True to turn on (max brightness), False to turn off.
    """
    resp = _send_command("light", {"on": on})
    return _format_response(resp)


@mcp.tool()
def get_camera_frame() -> CallToolResult:
    """Capture a single camera frame from the robot and return it as a JPEG image.

    The bridge must be running with camera publishing enabled.
    """
    ctx = _get_ctx()
    sub = ctx.socket(zmq.SUB)
    sub.setsockopt(zmq.RCVTIMEO, 5000)
    sub.connect(f"tcp://{BRIDGE_HOST}:{PUB_PORT}")
    sub.subscribe(b"camera")

    try:
        topic, data = sub.recv_multipart()
        jpeg_bytes = bytes(data) if not isinstance(data, bytes) else data
        b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
        return CallToolResult(
            content=[
                TextContent(type="text", text=f"Camera frame captured ({len(jpeg_bytes)} bytes)"),
                ImageContent(type="image", data=b64, mimeType="image/jpeg"),
            ]
        )
    except zmq.Again:
        return CallToolResult(
            content=[TextContent(type="text", text="ERROR: Timed out waiting for camera frame")]
        )
    finally:
        sub.close()


if __name__ == "__main__":
    mcp.run(transport="stdio")
