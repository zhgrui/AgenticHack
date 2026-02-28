"""
apps/stt_stream/main.py
KISS real-time STT with Vosk + FastAPI WebSocket.

Layout:
    apps/stt_stream/
        main.py             <- this file
        requirements.txt
        models/             <- place vosk model dir here
            vosk-model-small-en-us-0.15/

Download model:
    wget https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
    unzip vosk-model-small-en-us-0.15.zip -d apps/stt_stream/models/

Install:
    pip install -r apps/stt_stream/requirements.txt

Run:
    cd apps/stt_stream
    uvicorn main:app --reload

Audio contract (browser → server):
    - 16 kHz, mono, PCM 16-bit little-endian, raw binary frames
    - Typical chunk: 20–40 ms ≈ 320–640 samples ≈ 640–1280 bytes
"""

import json
import os
import sys
import collections
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import nats
import webrtcvad
from faster_whisper import WhisperModel
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

# ── config ────────────────────────────────────────────────────────────
SAMPLE_RATE        = 16000
FRAME_MS           = 30
FRAME_BYTES        = int(SAMPLE_RATE * (FRAME_MS / 1000.0) * 2)  # 960 bytes for 30ms 16kHz 16-bit
NATS_URL           = os.getenv("NATS_URL",           "nats://192.33.91.115:4222")
NATS_SUBJECT       = os.getenv("NATS_STT_SUBJECT",   "stt.final")
WHISPER_MODEL      = os.getenv("WHISPER_MODEL",      "base")    # tiny|base|small|medium|large-v3

# ── faster-whisper model (auto-downloaded on first run) ────────────────
print(f"[STT] Loading faster-whisper '{WHISPER_MODEL}' (downloading if needed)…", flush=True)
model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
print(f"[STT] Model ready.", flush=True)

# ── NATS connection (shared across requests) ──────────────────────────
nc: nats.aio.client.Client | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global nc
    try:
        nc = await nats.connect(NATS_URL)
        print(f"[NATS] Connected to {NATS_URL}, publishing on '{NATS_SUBJECT}'", flush=True)
    except Exception as e:
        print(f"[NATS] Could not connect ({e}) — transcripts will only be printed", flush=True)
        nc = None
    yield
    if nc:
        await nc.close()

# ── app ───────────────────────────────────────────────────────────────
app = FastAPI(lifespan=lifespan)

# ── minimal HTML (served inline) ──────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>STT Stream (VAD)</title>
  <style>
    body { font-family: sans-serif; padding: 2rem; background: #111; color: #eee; }
    h2   { margin-bottom: .5rem; }
    #status { margin-bottom: 1.2rem; font-size: .9rem; opacity: .7; }
    .meter-wrap { width: 320px; height: 18px; background: #2a2a2a; border-radius: 4px; overflow: hidden; margin-bottom: 1rem; }
    .meter-fill { height: 100%; width: 0%; border-radius: 4px; transition: background .08s; }
  </style>
</head>
<body>
  <h2>🎙️ Real-time STT (VAD)</h2>
  <p>Server-side VAD chunks audio intelligently. Speak and view console.</p>
  <p id="status">Connecting…</p>
  <div class="meter-wrap"><div class="meter-fill" id="bar"></div></div>

  <script>
    const SAMPLE_RATE = 16000;
    let ws, audioCtx, analyser, dataArray;
    const bar = document.getElementById("bar");

    function drawMeter() {
      if (analyser) {
        analyser.getByteTimeDomainData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
          const v = (dataArray[i] - 128) / 128;
          sum += v * v;
        }
        const pct = Math.min(100, Math.sqrt(sum / dataArray.length) * 400);
        bar.style.width = pct + "%";
        bar.style.background = pct < 40 ? "#4ade80" : pct < 75 ? "#facc15" : "#f87171";
      }
      requestAnimationFrame(drawMeter);
    }

    async function init() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.binaryType = "arraybuffer";
      ws.onopen  = () => (document.getElementById("status").textContent = "🟢 Connected — listening");
      ws.onclose = () => { document.getElementById("status").textContent = "🔴 Disconnected — retrying…"; setTimeout(init, 2000); };
      ws.onerror = (e) => console.error("WS error", e);

      const stream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: SAMPLE_RATE, channelCount: 1 }, video: false });
      audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
      const source = audioCtx.createMediaStreamSource(stream);

      analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      dataArray = new Uint8Array(analyser.fftSize);
      source.connect(analyser);

      await audioCtx.audioWorklet.addModule(URL.createObjectURL(new Blob([`
        class PcmProcessor extends AudioWorkletProcessor {
          process(inputs) {
            const ch = inputs[0][0];
            if (!ch) return true;
            const buf = new Int16Array(ch.length);
            for (let i = 0; i < ch.length; i++)
              buf[i] = Math.max(-32768, Math.min(32767, ch[i] * 32768));
            this.port.postMessage(buf.buffer, [buf.buffer]);
            return true;
          }
        }
        registerProcessor("pcm-processor", PcmProcessor);
      `], { type: "application/javascript" })));

      const worklet = new AudioWorkletNode(audioCtx, "pcm-processor");
      worklet.port.onmessage = (e) => {
        if (ws.readyState === WebSocket.OPEN) ws.send(e.data);
      };
      source.connect(worklet);
    }

    drawMeter();
    init().catch(console.error);
  </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()

    vad = webrtcvad.Vad(3)                 # Aggressiveness 0 to 3
    pre_speech_ring = collections.deque(maxlen=10)  # 300ms pre-speech history
    speech_buffer = bytearray()
    in_speech = False
    silence_frames = 0
    raw_buffer = bytearray()

    def transcribe_and_publish(raw: bytes):
        """Convert raw PCM int16-LE bytes → float32, run whisper, publish."""
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        segments, _ = model.transcribe(audio, language="en", beam_size=5)
        text = " ".join(s.text.strip() for s in segments).strip()
        if text:
            print(f"FINAL: {text}", flush=True)
            if nc:
                import asyncio
                # run_in_threadpool runs in a thread, so create a new loop to fire the publish task
                loop = asyncio.new_event_loop()
                loop.run_until_complete(nc.publish(NATS_SUBJECT, text.encode()))

    print("\n[STT] Client connected", flush=True)
    try:
        while True:
            data: bytes = await ws.receive_bytes()
            raw_buffer.extend(data)

            # Process in perfect 30ms (960 byte) slices
            while len(raw_buffer) >= FRAME_BYTES:
                frame = bytes(raw_buffer[:FRAME_BYTES])
                del raw_buffer[:FRAME_BYTES]

                # Run VAD on 30ms frame
                is_speech = vad.is_speech(frame, SAMPLE_RATE)

                if not in_speech:
                    if is_speech:
                        # Speech started
                        in_speech = True
                        silence_frames = 0
                        # Prepend the ring buffer so we don't chop the first consonant
                        speech_buffer.clear()
                        for f in pre_speech_ring:
                            speech_buffer.extend(f)
                        speech_buffer.extend(frame)
                    else:
                        pre_speech_ring.append(frame)
                else:
                    speech_buffer.extend(frame)
                    if is_speech:
                        silence_frames = 0
                    else:
                        silence_frames += 1

                    # 15 frames of silence (~450ms) ends the utterance
                    if silence_frames > 15:
                        chunk = bytes(speech_buffer)
                        speech_buffer.clear()
                        in_speech = False
                        silence_frames = 0
                        
                        # Only transcribe if the speech segment is reasonably long (> ~500ms total buffer including padding)
                        if len(chunk) > FRAME_BYTES * 15:
                            # Fire and forget transcription tasks so we never block websocket reads!
                            import asyncio
                            loop = asyncio.get_running_loop()
                            loop.run_in_executor(None, transcribe_and_publish, chunk)

    except WebSocketDisconnect:
        print("\n[STT] Client disconnected", flush=True)
    except Exception as e:
        print(f"\n[STT] Error: {e}", flush=True)
