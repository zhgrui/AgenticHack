"""
apps/stt_stream/tts_client.py
Interactive REPL: type a message, press Enter → publishes to tts.speak → browser plays it.

Usage:
    python tts_client.py
    python tts_client.py --url nats://192.33.91.115:4222 --subject tts.speak
"""
import asyncio
import os
import argparse
from nats.aio.client import Client as NATS


async def main():
    parser = argparse.ArgumentParser(description="TTS NATS publisher REPL")
    parser.add_argument("--url",     default=os.getenv("NATS_URL",        "nats://192.33.91.115:4222"))
    parser.add_argument("--subject", default=os.getenv("NATS_TTS_SUBJECT","tts.speak"))
    args = parser.parse_args()

    nc = NATS()
    await nc.connect(args.url)
    print(f"Connected to {args.url}")
    print(f"Publishing to '{args.subject}' — type a message and press Enter. Ctrl+C to quit.\n")

    loop = asyncio.get_running_loop()

    try:
        while True:
            # Read input without blocking the event loop
            text = await loop.run_in_executor(None, lambda: input("> "))
            text = text.strip()
            if text:
                await nc.publish(args.subject, text.encode())
                print(f"  → sent: {text!r}")
    except (KeyboardInterrupt, EOFError):
        print("\nBye!")
    finally:
        await nc.close()


if __name__ == "__main__":
    asyncio.run(main())
