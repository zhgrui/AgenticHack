import asyncio
import os
import time
import argparse
import signal
from nats.aio.client import Client as NATS

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.go2.video.video_client import VideoClient

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.getenv("NATS_URL", "nats://192.33.91.115:4222"))
    parser.add_argument("--subject", default=os.getenv("NATS_CAMERA_SUBJECT", "camera.stream"))
    parser.add_argument("--interface", default=os.getenv("GO2_NETWORK_INTERFACE", "eno1"))
    parser.add_argument("--interval", type=float, default=0.5, help="Publish interval in seconds")
    args = parser.parse_args()

    # Initialize Robot SDK
    print(f"Initializing DDS on interface {args.interface}")
    ChannelFactoryInitialize(0, args.interface)
    
    video = VideoClient()
    video.SetTimeout(3.0)
    video.Init()
    print("VideoClient ready")

    # Connect to NATS
    nc = NATS()
    try:
        await nc.connect(args.url)
        print(f"Connected to NATS at {args.url}")
    except Exception as e:
        print(f"Failed to connect to NATS: {e}")
        return

    running = True

    def signal_handler():
        nonlocal running
        print("Shutting down...")
        running = False
        
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    print(f"Starting to publish frames to '{args.subject}' every {args.interval}s")
    
    try:
        while running:
            # Grab frame
            code, data = video.GetImageSample()
            
            if code == 0 and data:
                jpeg_bytes = bytes(data) if not isinstance(data, bytes) else data
                # Publish frame
                await nc.publish(args.subject, jpeg_bytes)
                print(f"Published frame of {len(jpeg_bytes)} bytes")
            else:
                print(f"Failed to grab frame, code: {code}")
            
            # Use sleep to maintain roughly the chosen interval
            await asyncio.sleep(args.interval)
    except Exception as e:
        print(f"Error in main loop: {e}")
    finally:
        await nc.close()

if __name__ == '__main__':
    asyncio.run(main())
