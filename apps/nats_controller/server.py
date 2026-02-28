"""NATS server that receives robot commands and forwards them to the Go2 ZMQ bridge."""

import argparse
import asyncio
import json
import logging
import os
import signal
from typing import Any

import zmq
from nats.aio.client import Client as NATS

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.go2.video.video_client import VideoClient

# Default configuration (can be overridden by env vars or CLI args)
DEFAULT_BRIDGE_HOST = os.getenv("GO2_BRIDGE_HOST", "localhost")
DEFAULT_CMD_PORT = int(os.getenv("GO2_ZMQ_CMD_PORT", "5555"))
DEFAULT_PUB_PORT = int(os.getenv("GO2_ZMQ_PUB_PORT", "5556"))
DEFAULT_NATS_URL = os.getenv("NATS_URL", "nats://127.0.0.1:4222")
DEFAULT_CMD_SUBJECT = os.getenv("NATS_CMD_SUBJECT", "go2.cmd")
DEFAULT_CAMERA_SUBJECT = os.getenv("NATS_CAMERA_SUBJECT", "go2.camera")
DEFAULT_INTERFACE = os.getenv("GO2_NETWORK_INTERFACE", "en8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("nats_controller")


class ZMQBridgeClient:
    """Client for communicating with the Go2 ZMQ bridge and SDK."""

    def __init__(
        self,
        host: str = DEFAULT_BRIDGE_HOST,
        cmd_port: int = DEFAULT_CMD_PORT,
        pub_port: int = DEFAULT_PUB_PORT,
        interface: str = DEFAULT_INTERFACE,
        init_video: bool = True,
    ):
        self.host = host
        self.cmd_port = cmd_port
        self.pub_port = pub_port
        self.ctx = zmq.Context()
        self.video_client = None

        if init_video:
            self._init_video_client(interface)

    def _init_video_client(self, interface: str):
        """Initialize the VideoClient for direct camera access."""
        try:
            log.info(f"Initializing DDS on interface {interface}")
            ChannelFactoryInitialize(0, interface)
            self.video_client = VideoClient()
            self.video_client.SetTimeout(3.0)
            self.video_client.Init()
            log.info("VideoClient initialized successfully")
        except Exception as e:
            log.warning(f"Failed to initialize VideoClient: {e}")
            self.video_client = None

    def send_command(self, cmd: str, params: dict | None = None) -> dict:
        """Send a command to the bridge and return the parsed response."""
        sock = self.ctx.socket(zmq.REQ)
        sock.setsockopt(zmq.RCVTIMEO, 5000)
        sock.connect(f"tcp://{self.host}:{self.cmd_port}")
        msg: dict = {"cmd": cmd}
        if params:
            msg["params"] = params
        try:
            sock.send_json(msg)
            resp = sock.recv_json()
        except zmq.Again:
            resp = {"ok": False, "msg": "Timeout waiting for bridge response"}
        except Exception as e:
            resp = {"ok": False, "msg": f"Error: {e}"}
        finally:
            sock.close()
        return resp

    def get_camera_frame(self, timeout_ms: int = 5000) -> bytes | None:
        """Get a single camera frame.

        Tries VideoClient (SDK) first, falls back to ZMQ bridge if needed.
        """
        # Try VideoClient first (direct SDK access)
        if self.video_client:
            try:
                code, data = self.video_client.GetImageSample()
                if code == 0 and data:
                    jpeg_bytes = bytes(data) if not isinstance(data, bytes) else data
                    log.debug(f"Got camera frame from VideoClient: {len(jpeg_bytes)} bytes")
                    return jpeg_bytes
            except Exception as e:
                log.warning(f"VideoClient GetImageSample failed: {e}")

        # Fall back to ZMQ bridge
        sub = self.ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.RCVTIMEO, timeout_ms)
        sub.connect(f"tcp://{self.host}:{self.pub_port}")
        sub.subscribe(b"camera")

        try:
            topic, data = sub.recv_multipart()
            jpeg_bytes = bytes(data) if not isinstance(data, bytes) else data
            log.debug(f"Got camera frame from ZMQ bridge: {len(jpeg_bytes)} bytes")
            return jpeg_bytes
        except zmq.Again:
            log.warning("Timeout waiting for camera frame from ZMQ bridge")
            return None
        finally:
            sub.close()

    def close(self):
        """Close the ZMQ context."""
        self.ctx.term()


async def handle_command(msg, bridge: ZMQBridgeClient, nc: NATS, camera_subject: str):
    """Handle an incoming command message."""
    try:
        data = json.loads(msg.data.decode())
        command = data.get("cmd")
        params = data.get("params")
        request_id = data.get("id")

        log.info(f"Received command: {command} with params: {params}, reply: {msg.reply}")

        if command == "get_camera_frame":
            # Special handling for camera frame
            # Use frame_subject from params if provided, otherwise generate one
            frame_subject = params.get("frame_subject") if params else None
            if not frame_subject:
                frame_subject = f"{camera_subject}.{request_id}"

            jpeg_bytes = bridge.get_camera_frame()
            if jpeg_bytes:
                # Publish the frame to the specified subject
                await nc.publish(frame_subject, jpeg_bytes)
                log.info(f"Published camera frame to {frame_subject}: {len(jpeg_bytes)} bytes")
                response = {
                    "id": request_id,
                    "ok": True,
                    "msg": f"Camera frame captured ({len(jpeg_bytes)} bytes)",
                    "frame_subject": frame_subject
                }
            else:
                response = {
                    "id": request_id,
                    "ok": False,
                    "msg": "Timeout waiting for camera frame"
                }
        else:
            # Regular command to bridge
            resp = bridge.send_command(command, params)
            response = {"id": request_id, **resp}
            log.info(f"Bridge response: {resp}")

        # Send response back
        reply_subject = msg.reply
        if reply_subject:
            await nc.publish(reply_subject, json.dumps(response).encode())
            log.info(f"Sent response to {reply_subject}: {response.get('ok', False)}")
        else:
            log.warning("No reply subject in message!")

    except json.JSONDecodeError as e:
        log.error(f"Failed to decode message: {e}")
        if msg.reply:
            error_resp = {"ok": False, "msg": f"Invalid JSON: {e}"}
            await nc.publish(msg.reply, json.dumps(error_resp).encode())
    except Exception as e:
        log.error(f"Error handling command: {e}")
        if msg.reply:
            error_resp = {"ok": False, "msg": f"Error: {e}"}
            await nc.publish(msg.reply, json.dumps(error_resp).encode())


async def main():
    """Main entry point for the NATS controller server."""
    parser = argparse.ArgumentParser(description="Go2 NATS Controller Server")
    parser.add_argument("--url", default=DEFAULT_NATS_URL, help="NATS server URL")
    parser.add_argument("--bridge-host", default=DEFAULT_BRIDGE_HOST, help="ZMQ bridge host")
    parser.add_argument("--cmd-port", type=int, default=DEFAULT_CMD_PORT, help="ZMQ command port")
    parser.add_argument("--pub-port", type=int, default=DEFAULT_PUB_PORT, help="ZMQ pub port")
    parser.add_argument("--interface", default=DEFAULT_INTERFACE, help="Network interface for SDK (e.g., eno1, eth0)")
    parser.add_argument("--cmd-subject", default=DEFAULT_CMD_SUBJECT, help="NATS command subject")
    parser.add_argument("--camera-subject", default=DEFAULT_CAMERA_SUBJECT, help="NATS camera frame subject")
    parser.add_argument("--no-video", action="store_true", help="Disable direct VideoClient (use ZMQ bridge only)")
    args = parser.parse_args()

    log.info("Starting Go2 NATS Controller Server...")

    # Initialize ZMQ bridge client with VideoClient
    bridge = ZMQBridgeClient(
        host=args.bridge_host,
        cmd_port=args.cmd_port,
        pub_port=args.pub_port,
        interface=args.interface,
        init_video=not args.no_video,
    )
    log.info(f"Connected to ZMQ bridge at {args.bridge_host}:{args.cmd_port}")

    # Connect to NATS
    nc = NATS()
    try:
        await nc.connect(args.url)
        log.info(f"Connected to NATS at {args.url}")
    except Exception as e:
        log.error(f"Failed to connect to NATS: {e}")
        log.error("Make sure NATS server is running or use --url to specify the correct address")
        return

    running = True

    def signal_handler():
        nonlocal running
        log.info("Shutting down...")
        running = False

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    # Create async callback wrapper with captured args
    async def command_handler(msg):
        await handle_command(msg, bridge, nc, args.camera_subject)

    # Subscribe to command subject
    sub = await nc.subscribe(args.cmd_subject, cb=command_handler)
    log.info(f"Subscribed to '{args.cmd_subject}' - waiting for commands...")

    try:
        while running:
            await asyncio.sleep(0.1)
    except Exception as e:
        log.error(f"Error in main loop: {e}")
    finally:
        await sub.unsubscribe()
        await nc.close()
        bridge.close()
        log.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
