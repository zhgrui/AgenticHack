"""NATS client for sending commands to the Go2 robot controller."""

import asyncio
import base64
import json
import logging
import os
import uuid
from typing import Any

from nats.aio.client import Client as NATS

# Configuration
NATS_URL = os.getenv("NATS_URL", "nats://127.0.0.1:4222")
CMD_SUBJECT = os.getenv("NATS_CMD_SUBJECT", "go2.cmd")
CAMERA_SUBJECT = os.getenv("NATS_CAMERA_SUBJECT", "go2.camera")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("nats_client")


class Go2NATSClient:
    """Client for sending Go2 robot commands via NATS.

    Example usage:
        client = Go2NATSClient()

        # Get status
        status = await client.get_status()
        print(status)

        # Execute an action
        result = await client.execute_action("stand_up")
        print(result)

        # Move the robot
        result = await client.move(vx=0.3, vy=0.0, vyaw=0.0)
        print(result)

        # Get camera frame
        frame = await client.get_camera_frame()
        if frame:
            with open("frame.jpg", "wb") as f:
                f.write(frame)

        await client.close()
    """

    def __init__(
        self,
        url: str = NATS_URL,
        cmd_subject: str = CMD_SUBJECT,
        camera_subject: str = CAMERA_SUBJECT,
    ):
        """Initialize the Go2 NATS client.

        Args:
            url: NATS server URL.
            cmd_subject: Subject for sending commands.
            camera_subject: Base subject for camera frames.
        """
        self.url = url
        self.cmd_subject = cmd_subject
        self.camera_subject = camera_subject
        self.nc: NATS | None = None
        self._connected = False

    async def connect(self):
        """Connect to the NATS server."""
        if self._connected:
            return

        self.nc = NATS()
        try:
            await self.nc.connect(self.url)
            self._connected = True
            log.info(f"Connected to NATS at {self.url}")
        except Exception as e:
            log.error(f"Failed to connect to NATS: {e}")
            raise

    async def close(self):
        """Close the NATS connection."""
        if self.nc and self._connected:
            await self.nc.close()
            self._connected = False
            log.info("NATS connection closed")

    async def _send_command(self, cmd: str, params: dict | None = None) -> dict:
        """Send a command and wait for the response."""
        if not self._connected:
            await self.connect()

        request_id = str(uuid.uuid4())
        msg = {"cmd": cmd, "id": request_id}
        if params:
            msg["params"] = params

        # Create unique inbox using request_id
        inbox = f"_INBOX.{request_id}"
        response_future = asyncio.Future()

        async def on_response(msg):
            try:
                data = json.loads(msg.data.decode())
                if data.get("id") == request_id:
                    response_future.set_result(data)
            except Exception as e:
                response_future.set_exception(e)

        # Subscribe returns a subscription object
        sub = await self.nc.subscribe(inbox, cb=on_response)

        try:
            # Publish with reply-to header (reply should be a string, not bytes)
            await self.nc.publish(
                self.cmd_subject,
                json.dumps(msg).encode(),
                reply=inbox,
            )

            # Wait for response with timeout
            response = await asyncio.wait_for(response_future, timeout=5.0)
            return response
        except asyncio.TimeoutError:
            return {"ok": False, "msg": "Timeout waiting for response"}
        except Exception as e:
            return {"ok": False, "msg": f"Error: {e}"}
        finally:
            await sub.unsubscribe()

    async def get_status(self) -> str:
        """Get the current status of the Go2 robot.

        Returns obstacle avoidance state, speed level, and light state.
        """
        resp = await self._send_command("status")
        return self._format_response(resp)

    async def list_actions(self) -> str:
        """List all available robot actions (e.g. stand_up, sit, dance1, hello)."""
        resp = await self._send_command("list_actions")
        return self._format_response(resp)

    async def execute_action(self, name: str) -> str:
        """Execute a named action on the robot.

        Args:
            name: Action name (e.g. stand_up, stand_down, sit, hello, stretch,
                  dance1, dance2, heart, front_flip, front_jump, back_flip,
                  left_flip, hand_stand, balance_stand, recovery_stand, damp, stop_move)
        """
        resp = await self._send_command("action", {"name": name})
        return self._format_response(resp)

    async def move(self, vx: float, vy: float = 0.0, vyaw: float = 0.0) -> str:
        """Move the robot with the given velocity.

        The robot must be standing first. Velocities are in m/s (linear) and rad/s (rotation).
        The movement continues at the given velocity until a stop command or new move command.
        The bridge has a 250ms safety timeout - if no new command arrives, the robot stops automatically.

        Args:
            vx: Forward/backward velocity (-1.0 to 1.0). Positive = forward.
            vy: Left/right velocity (-1.0 to 1.0). Positive = left.
            vyaw: Rotation velocity (-1.0 to 1.0). Positive = counter-clockwise.
        """
        resp = await self._send_command("move", {"vx": vx, "vy": vy, "vyaw": vyaw})
        return self._format_response(resp)

    async def stop(self) -> str:
        """Immediately stop all robot movement. Use this as an emergency stop or to halt motion."""
        resp = await self._send_command("stop")
        return self._format_response(resp)

    async def set_obstacle_avoidance(self, enabled: bool) -> str:
        """Enable or disable the robot's obstacle avoidance system.

        When enabled, the robot uses its sensors to avoid collisions during movement.
        Enabled by default at bridge startup.

        Args:
            enabled: True to enable, False to disable.
        """
        resp = await self._send_command("obstacle_avoidance", {"enabled": enabled})
        return self._format_response(resp)

    async def set_speed_level(self, level: int) -> str:
        """Set the robot's movement speed level.

        Args:
            level: Speed level from 1 (slow) to 3 (fast).
        """
        resp = await self._send_command("speed_level", {"level": level})
        return self._format_response(resp)

    async def set_light(self, on: bool) -> str:
        """Turn the robot's head light on or off.

        Args:
            on: True to turn on (max brightness), False to turn off.
        """
        resp = await self._send_command("light", {"on": on})
        return self._format_response(resp)

    async def get_camera_frame(self) -> bytes | None:
        """Capture a single camera frame from the robot and return it as JPEG bytes.

        The server must be running with camera publishing enabled.

        Returns:
            JPEG image bytes, or None if timeout/error occurs.
        """
        if not self._connected:
            await self.connect()

        # Generate unique frame subject and subscribe FIRST
        frame_request_id = str(uuid.uuid4())
        frame_subject = f"{self.camera_subject}.{frame_request_id}"
        frame_future: asyncio.Future[bytes] = asyncio.Future()

        async def on_frame(msg):
            frame_future.set_result(bytes(msg.data))

        # Subscribe before sending command so we don't miss the frame
        sub = await self.nc.subscribe(frame_subject, cb=on_frame)

        try:
            # Send command with the frame subject in params
            cmd_resp = await self._send_command("get_camera_frame", {"frame_subject": frame_subject})

            if not cmd_resp.get("ok"):
                log.error(f"Failed to get camera frame: {cmd_resp.get('msg')}")
                return None

            # Wait for the frame with timeout
            frame = await asyncio.wait_for(frame_future, timeout=5.0)
            log.info(f"Received camera frame: {len(frame)} bytes")
            return frame
        except asyncio.TimeoutError:
            log.error("Timeout waiting for camera frame")
            return None
        finally:
            await sub.unsubscribe()

    def _format_response(self, resp: dict) -> str:
        """Format a server response as readable text."""
        ok = resp.get("ok", False)
        msg = resp.get("msg", "")
        data = resp.get("data")
        parts = [f"{'OK' if ok else 'ERROR'}: {msg}"]
        if data:
            parts.append(json.dumps(data, indent=2))
        return "\n".join(parts)

    # Synchronous wrappers for convenience
    def connect_sync(self):
        """Synchronous wrapper for connect()."""
        asyncio.run(self.connect())

    def close_sync(self):
        """Synchronous wrapper for close()."""
        asyncio.run(self.close())

    def get_status_sync(self) -> str:
        """Synchronous wrapper for get_status()."""
        return asyncio.run(self.get_status())

    def list_actions_sync(self) -> str:
        """Synchronous wrapper for list_actions()."""
        return asyncio.run(self.list_actions())

    def execute_action_sync(self, name: str) -> str:
        """Synchronous wrapper for execute_action()."""
        return asyncio.run(self.execute_action(name))

    def move_sync(self, vx: float, vy: float = 0.0, vyaw: float = 0.0) -> str:
        """Synchronous wrapper for move()."""
        return asyncio.run(self.move(vx, vy, vyaw))

    def stop_sync(self) -> str:
        """Synchronous wrapper for stop()."""
        return asyncio.run(self.stop())

    def set_obstacle_avoidance_sync(self, enabled: bool) -> str:
        """Synchronous wrapper for set_obstacle_avoidance()."""
        return asyncio.run(self.set_obstacle_avoidance(enabled))

    def set_speed_level_sync(self, level: int) -> str:
        """Synchronous wrapper for set_speed_level()."""
        return asyncio.run(self.set_speed_level(level))

    def set_light_sync(self, on: bool) -> str:
        """Synchronous wrapper for set_light()."""
        return asyncio.run(self.set_light(on))

    def get_camera_frame_sync(self) -> bytes | None:
        """Synchronous wrapper for get_camera_frame()."""
        return asyncio.run(self.get_camera_frame())


# Example usage when run directly
async def example_async():
    """Example async usage."""
    client = Go2NATSClient()

    try:
        # Get status
        status = await client.get_status()
        print("Status:", status)

        # List available actions
        actions = await client.list_actions()
        print("Available actions:", actions)

        # Stand up
        result = await client.execute_action("stand_up")
        print("Stand up:", result)

        # Move forward
        result = await client.move(vx=0.3, vy=0.0, vyaw=0.0)
        print("Move:", result)

        # Stop
        result = await client.stop()
        print("Stop:", result)

        # Get camera frame
        frame = await client.get_camera_frame()
        if frame:
            print(f"Got camera frame: {len(frame)} bytes")

    finally:
        await client.close()


def example_sync():
    """Example synchronous usage."""
    client = Go2NATSClient()

    try:
        # Get status
        status = client.get_status_sync()
        print("Status:", status)

        # Execute action
        result = client.execute_action_sync("stand_up")
        print("Stand up:", result)

        # Move
        result = client.move_sync(vx=0.3)
        print("Move:", result)

        # Stop
        result = client.stop_sync()
        print("Stop:", result)

    finally:
        client.close_sync()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Go2 NATS Client Example")
    parser.add_argument("--url", default=NATS_URL, help="NATS server URL")
    parser.add_argument("--sync", action="store_true", help="Use synchronous API")
    args = parser.parse_args()

    if args.sync:
        example_sync()
    else:
        asyncio.run(example_async())
