"""Test script for Go2 NATS client.

Tests camera frame retrieval and executes a sequence of actions:
stand_down -> stand_up -> stand_down
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from apps.nats_controller import Go2NATSClient


async def test_camera_and_actions(url: str):
    """Test camera frame and action sequence."""
    print("=== Go2 NATS Client Test ===\n")

    client = Go2NATSClient(url=url)

    try:
        print("Connecting to NATS...")
        await client.connect()
        print("Connected!\n")

        # Test 1: Get camera frame
        print("--- Test 1: Camera Frame ---")
        print("Requesting camera frame...")
        frame = await client.get_camera_frame()
        if frame:
            print(f"SUCCESS: Got camera frame ({len(frame)} bytes)")
            # Save frame to file
            frame_path = Path(__file__).parent / "test_frame.jpg"
            with open(frame_path, "wb") as f:
                f.write(frame)
            print(f"Saved frame to: {frame_path}\n")
        else:
            print("FAILED: No camera frame received\n")

        # Test 2: Action sequence
        print("--- Test 2: Action Sequence ---")

        print("1. Executing stand_down...")
        result = await client.execute_action("stand_down")
        print(f"Result: {result}\n")

        print("Waiting 2 seconds...")
        await asyncio.sleep(2)

        print("2. Executing stand_up...")
        result = await client.execute_action("stand_up")
        print(f"Result: {result}\n")

        print("Waiting 2 seconds...")
        await asyncio.sleep(2)

        print("3. Executing stand_down...")
        result = await client.execute_action("stand_down")
        print(f"Result: {result}\n")

        print("=== Test Complete ===")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\nClosing connection...")
        await client.close()
        print("Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Go2 NATS Client Test")
    parser.add_argument("--url", default="nats://127.0.0.1:4222", help="NATS server URL")
    args = parser.parse_args()

    asyncio.run(test_camera_and_actions(args.url))
