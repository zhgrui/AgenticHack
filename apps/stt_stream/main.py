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

import io
import json
import os
import sys
import collections
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import nats
import scipy.io.wavfile
import webrtcvad
from faster_whisper import WhisperModel
from pocket_tts import TTSModel
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

# ── config ────────────────────────────────────────────────────────────
SAMPLE_RATE        = 16000
FRAME_MS           = 30
FRAME_BYTES        = int(SAMPLE_RATE * (FRAME_MS / 1000.0) * 2)  # 960 bytes for 30ms 16kHz 16-bit
NATS_URL           = os.getenv("NATS_URL",           "nats://192.33.91.115:4222")
NATS_SUBJECT       = os.getenv("NATS_STT_SUBJECT",   "stt.final")
TTS_SUBJECT        = os.getenv("NATS_TTS_SUBJECT",   "tts.speak")
WHISPER_MODEL      = os.getenv("WHISPER_MODEL",      "base")

# ── faster-whisper model (auto-downloaded on first run) ────────────────
print(f"[STT] Loading faster-whisper '{WHISPER_MODEL}' …", flush=True)
stt_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
print(f"[STT] Model ready.", flush=True)

# ── pocket-tts model (auto-downloaded on first run) ─────────────────────
print("[TTS] Loading pocket-tts …", flush=True)
tts_model  = TTSModel.load_model()
tts_voice  = tts_model.get_state_for_audio_prompt("alba")  # built-in voice
print("[TTS] Model ready.", flush=True)

# ── NATS + TTS WS clients ────────────────────────────────────────
nc: nats.aio.client.Client | None = None
tts_clients: set[WebSocket] = set()   # browsers connected to /tts-ws

def _generate_wav(text: str) -> bytes:
    """Run pocket-tts synchronously and return raw WAV bytes."""
    audio = tts_model.generate_audio(tts_voice, text)       # torch tensor
    buf = io.BytesIO()
    scipy.io.wavfile.write(buf, tts_model.sample_rate, audio.numpy())
    return buf.getvalue()

async def _broadcast_tts(text: str):
    if not tts_clients:
        return
    print(f"[TTS] Generating for: {text!r}", flush=True)
    wav_bytes = await run_in_threadpool(_generate_wav, text)
    dead = set()
    for client in list(tts_clients):
        try:
            await client.send_bytes(wav_bytes)
        except Exception:
            dead.add(client)
    tts_clients.difference_update(dead)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global nc
    try:
        nc = await nats.connect(NATS_URL)
        print(f"[NATS] Connected to {NATS_URL}", flush=True)
        print(f"[NATS] STT publishing on '{NATS_SUBJECT}'", flush=True)
        print(f"[NATS] TTS subscribed on '{TTS_SUBJECT}'", flush=True)

        async def on_tts(msg):
            text = msg.data.decode().strip()
            if text:
                await _broadcast_tts(text)

        await nc.subscribe(TTS_SUBJECT, cb=on_tts)
    except Exception as e:
        print(f"[NATS] Could not connect ({e})", flush=True)
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
  <title>STT + TTS Stream</title>
  <style>
    body { font-family: sans-serif; padding: 2rem; background: #111; color: #eee; }
    h2   { margin-bottom: .5rem; }
    #status, #tts-status { font-size: .9rem; opacity: .7; margin-bottom: .5rem; }
    .meter-wrap { width: 320px; height: 18px; background: #2a2a2a; border-radius: 4px; overflow: hidden; margin-bottom: 1rem; }
    .meter-fill { height: 100%; width: 0%; border-radius: 4px; transition: background .08s; }
  </style>
</head>
<body>
  <h2>🎙️ STT + 🔊 TTS (VAD + pocket-tts)</h2>
  <p id="status">Mic: Connecting…</p>
  <div class="meter-wrap"><div class="meter-fill" id="bar"></div></div>
  <p id="tts-status">TTS: Connecting…</p>

  <script>
    const SAMPLE_RATE = 16000;
    let ws, audioCtx, analyser, dataArray;
    const bar = document.getElementById("bar");

    // ── Mic level meter ───────────────────────────────────────────────
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

    // ── STT mic → server ──────────────────────────────────────────────
    async function initMic() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws`);
      ws.binaryType = "arraybuffer";
      ws.onopen  = () => (document.getElementById("status").textContent = "Mic: 🟢 Connected");
      ws.onclose = () => { document.getElementById("status").textContent = "Mic: 🔴 Retrying…"; setTimeout(initMic, 2000); };
      ws.onerror = (e) => console.error("WS error", e);

      const stream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: SAMPLE_RATE, channelCount: 1 }, video: false });
      audioCtx = audioCtx || new AudioContext({ sampleRate: SAMPLE_RATE });
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
      worklet.port.onmessage = (e) => { if (ws.readyState === WebSocket.OPEN) ws.send(e.data); };
      source.connect(worklet);
    }

    // ── TTS playback from server ───────────────────────────────────────
    async function initTts() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ttsWs = new WebSocket(`${proto}://${location.host}/tts-ws`);
      ttsWs.binaryType = "arraybuffer";
      ttsWs.onopen  = () => (document.getElementById("tts-status").textContent = "TTS: 🟢 Ready");
      ttsWs.onclose = () => { document.getElementById("tts-status").textContent = "TTS: 🔴 Retrying…"; setTimeout(initTts, 2000); };

      ttsWs.onmessage = async (e) => {
        // Received WAV bytes from server — decode and play
        audioCtx = audioCtx || new AudioContext();
        const audioBuf = await audioCtx.decodeAudioData(e.data);
        const src = audioCtx.createBufferSource();
        src.buffer = audioBuf;
        src.connect(audioCtx.destination);
        src.start();
        document.getElementById("tts-status").textContent = "TTS: 🔊 Speaking…";
        src.onended = () => (document.getElementById("tts-status").textContent = "TTS: 🟢 Ready");
      };
    }

    drawMeter();
    initMic().catch(console.error);
    initTts().catch(console.error);
  </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


@app.websocket("/tts-ws")
async def tts_ws_endpoint(ws: WebSocket):
    """Browser connects here to receive TTS audio pushed from the server."""
    await ws.accept()
    tts_clients.add(ws)
    print("[TTS] Browser client connected", flush=True)
    try:
        while True:
            await ws.receive_text()   # keep connection alive; we only send, never receive
    except WebSocketDisconnect:
        pass
    finally:
        tts_clients.discard(ws)
        print("[TTS] Browser client disconnected", flush=True)


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
        segments, _ = stt_model.transcribe(audio, language="en", beam_size=5)
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
