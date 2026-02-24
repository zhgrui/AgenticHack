"""
Unitree GO2 Robot Controller
Sends commands to the Go2 ZMQ bridge (go2_bridge) instead of using the SDK directly.
"""

import os
import time
import threading
import logging

import zmq

BRIDGE_HOST = os.getenv('GO2_BRIDGE_HOST', 'localhost')
BRIDGE_CMD_PORT = int(os.getenv('GO2_ZMQ_CMD_PORT', '5555'))


class GO2Controller:
    def __init__(self):
        self.connected = False
        self._ctx = None
        self._sock = None
        self.last_command = None
        self.last_command_time = 0
        self.command_lock = threading.Lock()

        # Movement parameters
        self.default_forward_speed = 0.3
        self.default_turn_speed = 0.5
        self.logger = logging.getLogger('control')

        # Command mapping
        self.command_map = {
            'Forward': self.move_forward,
            'Right': self.turn_right,
            'Left': self.turn_left,
            'Rotate': self.rotate_in_place,
            'Idle': self.idle
        }

    def idle(self, *_args, **_kwargs):
        """Idle: stand still (no movement)."""
        self.logger.info("• Idle - standing still")
        self.stop()

    def connect(self):
        """Connect to the Go2 ZMQ bridge."""
        try:
            self._ctx = zmq.Context()
            self._sock = self._ctx.socket(zmq.REQ)
            self._sock.setsockopt(zmq.RCVTIMEO, 3000)
            self._sock.setsockopt(zmq.SNDTIMEO, 3000)
            self._sock.connect(f"tcp://{BRIDGE_HOST}:{BRIDGE_CMD_PORT}")

            # Test the connection
            resp = self._bridge_cmd("status")
            if resp and resp.get("ok"):
                self.connected = True
                self.logger.info("Connected to Go2 bridge at %s:%d", BRIDGE_HOST, BRIDGE_CMD_PORT)
            else:
                self.connected = True  # Still mark connected; bridge may be starting up
                self.logger.warning("Bridge responded but status was not ok: %s", resp)

            return True

        except Exception as e:
            self.logger.error("Failed to connect to Go2 bridge: %s", e)
            self.connected = False
            return False

    def _bridge_cmd(self, cmd, params=None):
        """Send a command to the bridge and return the JSON response."""
        msg = {"cmd": cmd}
        if params:
            msg["params"] = params
        try:
            self._sock.send_json(msg)
            return self._sock.recv_json()
        except zmq.ZMQError as e:
            self.logger.error("Bridge communication error: %s", e)
            # Re-create socket on error (REQ/REP state machine may be broken)
            self._reconnect()
            return None

    def _reconnect(self):
        """Re-create the ZMQ socket after a communication error."""
        try:
            if self._sock:
                self._sock.close(linger=0)
            self._sock = self._ctx.socket(zmq.REQ)
            self._sock.setsockopt(zmq.RCVTIMEO, 3000)
            self._sock.setsockopt(zmq.SNDTIMEO, 3000)
            self._sock.connect(f"tcp://{BRIDGE_HOST}:{BRIDGE_CMD_PORT}")
        except Exception as e:
            self.logger.error("Reconnect failed: %s", e)

    def _send_command(self, vx=0.0, vy=0.0, vyaw=0.0):
        """
        Send movement command to robot via bridge.

        Args:
            vx: Forward/backward velocity (m/s) - positive is forward
            vy: Left/right velocity (m/s) - positive is left
            vyaw: Yaw angular velocity (rad/s) - positive is counter-clockwise
        """
        with self.command_lock:
            if not self.connected:
                self.logger.warning("Robot not connected")
                return

            if vx == 0.0 and vy == 0.0 and vyaw == 0.0:
                self._bridge_cmd("stop")
            else:
                self._bridge_cmd("move", {"vx": vx, "vy": vy, "vyaw": vyaw})

            self.last_command = (vx, vy, vyaw)
            self.last_command_time = time.time()

    def move_forward(self, speed=None):
        """Move forward"""
        if speed is None:
            speed = self.default_forward_speed

        self.logger.info("→ Moving forward at %.2f m/s", speed)
        self._send_command(vx=speed, vy=0.0, vyaw=0.0)

    def turn_right(self, speed=None):
        """Turn right (rotate clockwise)"""
        if speed is None:
            speed = self.default_turn_speed

        self.logger.info("↻ Turning right at %.2f rad/s", speed)
        self._send_command(vx=0.0, vy=0.0, vyaw=-speed)

    def turn_left(self, speed=None):
        """Turn left (rotate counter-clockwise)"""
        if speed is None:
            speed = self.default_turn_speed

        self.logger.info("↺ Turning left at %.2f rad/s", speed)
        self._send_command(vx=0.0, vy=0.0, vyaw=speed)

    def rotate_in_place(self, speed=None):
        """Rotate in place (for down arrow - safer than backing up)"""
        if speed is None:
            speed = self.default_turn_speed

        self.logger.info("⟳ Rotating in place at %.2f rad/s", speed)
        self._send_command(vx=0.0, vy=0.0, vyaw=speed)

    def stop(self):
        """Stop all movement"""
        self.logger.info("■ Stopping")
        self._send_command(vx=0.0, vy=0.0, vyaw=0.0)

    def emergency_stop(self):
        """Emergency stop - immediately halt all movement"""
        self.logger.critical("!!! EMERGENCY STOP !!!")
        with self.command_lock:
            self._bridge_cmd("stop")

    def execute_command(self, command_name, speed=None):
        """
        Execute a named command

        Args:
            command_name: Name of the command (Forward, Right, Left, Rotate)
            speed: Optional speed parameter
        """
        if not self.connected:
            self.logger.warning("Robot not connected")
            return

        # Normalize command name
        command_name = command_name.strip().title()

        if command_name in self.command_map:
            self.command_map[command_name](speed)
        else:
            self.logger.warning("Unknown command: %s", command_name)

    def disconnect(self):
        """Disconnect from bridge"""
        if self.connected:
            self.stop()
            time.sleep(0.1)
            self.connected = False
            if self._sock:
                self._sock.close(linger=0)
            if self._ctx:
                self._ctx.term()
            self.logger.info("Disconnected from Go2 bridge")


# Test script
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('control')
    logger.info("GO2 Controller Test (via ZMQ bridge)")
    logger.info("%s", "=" * 40)

    controller = GO2Controller()

    if controller.connect():
        logger.info("\nTesting movements (2 seconds each)...")

        # Test forward
        logger.info("\n1. Testing FORWARD")
        controller.move_forward(0.2)
        time.sleep(2)
        controller.stop()
        time.sleep(1)

        # Test right turn
        logger.info("\n2. Testing RIGHT turn")
        controller.turn_right(0.3)
        time.sleep(2)
        controller.stop()
        time.sleep(1)

        # Test left turn
        logger.info("\n3. Testing LEFT turn")
        controller.turn_left(0.3)
        time.sleep(2)
        controller.stop()
        time.sleep(1)

        # Test rotate
        logger.info("\n4. Testing ROTATE in place")
        controller.rotate_in_place(0.4)
        time.sleep(2)
        controller.stop()

        logger.info("\n✓ Test complete")
        controller.disconnect()
    else:
        logger.error("Could not connect to bridge")
        sys.exit(1)
