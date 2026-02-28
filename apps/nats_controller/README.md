# Go2 NATS Controller

Control the Unitree Go2 quadruped robot via NATS messaging. This server acts as a bridge between NATS and the Go2 ZMQ bridge, allowing any NATS client to send robot commands.

## Architecture

```
NATS Client → NATS Server → NATS Controller → ZMQ Bridge → Go2 Robot
```

## Setup

1. Make sure the Go2 ZMQ bridge is running:
```bash
source go2_py311/bin/activate
GO2_NETWORK_INTERFACE=eno1 python -m go2_bridge
```

2. Start the NATS controller server:
```bash
source go2_py311/bin/activate
python -m apps.nats_controller.server
```

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `NATS_URL` | `nats://127.0.0.1:4222` | NATS server URL |
| `NATS_CMD_SUBJECT` | `go2.cmd` | Subject for commands |
| `NATS_CAMERA_SUBJECT` | `go2.camera` | Base subject for camera frames |
| `GO2_BRIDGE_HOST` | `localhost` | ZMQ bridge host |
| `GO2_ZMQ_CMD_PORT` | `5555` | ZMQ command port |
| `GO2_ZMQ_PUB_PORT` | `5556` | ZMQ pub port |

## Usage

### Python Client

```python
import asyncio
from apps.nats_controller import Go2NATSClient

async def main():
    client = Go2NATSClient()
    await client.connect()

    try:
        # Get robot status
        status = await client.get_status()
        print(status)

        # Stand up
        result = await client.execute_action("stand_up")
        print(result)

        # Move forward
        result = await client.move(vx=0.3, vy=0.0, vyaw=0.0)
        print(result)

        # Stop
        result = await client.stop()
        print(result)

        # Get camera frame
        frame = await client.get_camera_frame()
        if frame:
            with open("frame.jpg", "wb") as f:
                f.write(frame)

    finally:
        await client.close()

asyncio.run(main())
```

### Synchronous API

```python
from apps.nats_controller import Go2NATSClient

client = Go2NATSClient()
client.connect_sync()

try:
    status = client.get_status_sync()
    print(status)

    client.execute_action_sync("stand_up")
    client.move_sync(vx=0.3)
    client.stop_sync()
finally:
    client.close_sync()
```

### Raw NATS (any language)

Send JSON commands to the `go2.cmd` subject:

```json
{"cmd": "status", "id": "unique-request-id"}
{"cmd": "action", "params": {"name": "stand_up"}, "id": "unique-id"}
{"cmd": "move", "params": {"vx": 0.3, "vy": 0.0, "vyaw": 0.0}, "id": "unique-id"}
{"cmd": "stop", "id": "unique-id"}
{"cmd": "obstacle_avoidance", "params": {"enabled": true}, "id": "unique-id"}
{"cmd": "speed_level", "params": {"level": 2}, "id": "unique-id"}
{"cmd": "light", "params": {"on": true}, "id": "unique-id"}
{"cmd": "list_actions", "id": "unique-id"}
```

Responses will be sent to the reply subject:

```json
{"id": "unique-request-id", "ok": true, "msg": "OK", "data": {...}}
```

For camera frames, the response includes a `frame_subject` to subscribe to:

```json
{"id": "unique-id", "ok": true, "frame_subject": "go2.camera.unique-id"}
```

## Available Commands

| Command | Params | Description |
|---------|--------|-------------|
| `status` | none | Get robot status |
| `list_actions` | none | List available actions |
| `action` | `{"name": "action_name"}` | Execute an action |
| `move` | `{"vx": 0.3, "vy": 0.0, "vyaw": 0.0}` | Move robot |
| `stop` | none | Stop movement |
| `obstacle_avoidance` | `{"enabled": true}` | Toggle obstacle avoidance |
| `speed_level` | `{"level": 1-3}` | Set speed level |
| `light` | `{"on": true}` | Toggle head light |
| `get_camera_frame` | none | Capture camera frame |

## Running the Example

```bash
# Async example
python -m apps.nats_controller.client

# Sync example
python -m apps.nats_controller.client --sync
```
