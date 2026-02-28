"""
NATS camera stream viewer using OpenCV (no Tk/Qt dependency).
"""
import asyncio
import hashlib
import io
import os
import argparse
import queue
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")  # force X11; needed on Wayland hosts

import cv2
import numpy as np
from PIL import Image
from nats.aio.client import Client as NATS

frame_queue: queue.Queue = queue.Queue(maxsize=2)


async def nats_loop(url: str, subject: str) -> None:
    nc = NATS()
    try:
        await nc.connect(url)
        print(f"Connected to NATS at {url}")
    except Exception as e:
        print(f"Failed to connect: {e}")
        return

    async def on_message(msg):
        data = msg.data
        md5 = hashlib.md5(data).hexdigest()
        print(f"Received {len(data)} bytes | md5={md5}")
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
            # Drop oldest frame if viewer is slow
            if frame_queue.full():
                try:
                    frame_queue.get_nowait()
                except queue.Empty:
                    pass
            frame_queue.put_nowait(img)
        except Exception as e:
            print(f"Failed to decode frame: {e}")

    sub = await nc.subscribe(subject, cb=on_message)
    print(f"Subscribed to '{subject}' — waiting for frames…")

    try:
        while True:
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        pass
    finally:
        await sub.unsubscribe()
        await nc.close()


def start_nats_thread(url: str, subject: str) -> None:
    def run():
        asyncio.run(nats_loop(url, subject))
    t = threading.Thread(target=run, daemon=True, name="nats-sub")
    t.start()


def main():
    parser = argparse.ArgumentParser(description="Live NATS camera viewer")
    parser.add_argument("--url", default=os.getenv("NATS_URL", "nats://192.33.91.115:4222"))
    parser.add_argument("--subject", default=os.getenv("NATS_CAMERA_SUBJECT", "camera.stream"))
    args = parser.parse_args()

    start_nats_thread(args.url, args.subject)

    print("Waiting for first frame… (press 'q' in the window to quit)")

    cv2.namedWindow("Camera Stream — NATS", cv2.WINDOW_NORMAL)

    # Load Haar cascade classifier for face detection
    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')

    while True:
        try:
            img = frame_queue.get(timeout=0.05)
            # PIL RGB → NumPy BGR for OpenCV
            frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            
            # Convert to grayscale for face detection
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Detect faces
            faces = face_cascade.detectMultiScale(
                gray, 
                scaleFactor=1.1, 
                minNeighbors=5, 
                minSize=(30, 30)
            )
            
            # Annotate faces
            for (x, y, w, h) in faces:
                cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
                cv2.putText(frame, "Face", (x, y-10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
                
            cv2.imshow("Camera Stream — NATS", frame)
        except queue.Empty:
            pass

        # 30 ms wait; quit on 'q'
        if cv2.waitKey(30) & 0xFF == ord("q"):
            break

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
