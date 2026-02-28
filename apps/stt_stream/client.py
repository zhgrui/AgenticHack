"""
apps/stt_stream/client.py
Subscribe to stt.final and print transcripts as they arrive.

Usage:
    python client.py
    python client.py --url nats://192.33.91.115:4222 --subject stt.final
"""
import asyncio
import os
import argparse
from nats.aio.client import Client as NATS


async def main():
    parser = argparse.ArgumentParser(description="NATS STT subscriber")
    parser.add_argument("--url",     default=os.getenv("NATS_URL",         "nats://192.33.91.115:4222"))
    parser.add_argument("--subject", default=os.getenv("NATS_STT_SUBJECT", "stt.final"))
    args = parser.parse_args()

    nc = NATS()
    await nc.connect(args.url)
    print(f"Connected to {args.url}")
    print(f"Listening on '{args.subject}' …\n")

    async def on_message(msg):
        text = msg.data.decode()
        print(f"[stt] {text}")

    await nc.subscribe(args.subject, cb=on_message)

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await nc.close()


if __name__ == "__main__":
    asyncio.run(main())
