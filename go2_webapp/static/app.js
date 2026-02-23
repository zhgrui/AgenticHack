/* Go2 Web Controller — vanilla JS */

const SPEED_MULT = [0.3, 0.6, 1.0]; // speed levels 1-3

// ── DOM refs ─────────────────────────────────────────────────────

const cameraImg   = document.getElementById("camera-img");
const noFeed      = document.getElementById("no-feed");
const btnStop     = document.getElementById("btn-stop");
const btnMoveStop = document.getElementById("btn-move-stop");
const actionsGrid = document.getElementById("actions-grid");
const logEl       = document.getElementById("log");
const oaToggle    = document.getElementById("oa-toggle");
const speedSelect = document.getElementById("speed-select");
const connStatus  = document.getElementById("conn-status");

// ── Logging ──────────────────────────────────────────────────────

function log(msg) {
  const line = document.createElement("div");
  line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

// ── API helper ───────────────────────────────────────────────────

async function sendCmd(cmd, params) {
  try {
    const body = { cmd };
    if (params) body.params = params;
    const resp = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    log(`${cmd}: ${data.msg}`);
    return data;
  } catch (err) {
    log(`ERROR: ${err.message}`);
    return null;
  }
}

// ── Camera WebSocket ─────────────────────────────────────────────

let cameraWs = null;
let blobUrl = null;

function connectCamera() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  cameraWs = new WebSocket(`${proto}//${location.host}/ws/camera`);
  cameraWs.binaryType = "arraybuffer";

  cameraWs.onopen = () => {
    connStatus.textContent = "Connected";
    connStatus.style.color = "#4caf50";
    log("Camera connected");
  };

  cameraWs.onmessage = (ev) => {
    if (blobUrl) URL.revokeObjectURL(blobUrl);
    const blob = new Blob([ev.data], { type: "image/jpeg" });
    blobUrl = URL.createObjectURL(blob);
    cameraImg.src = blobUrl;
    cameraImg.style.display = "block";
    noFeed.style.display = "none";
  };

  cameraWs.onclose = () => {
    connStatus.textContent = "Disconnected";
    connStatus.style.color = "#e53935";
    cameraImg.style.display = "none";
    noFeed.style.display = "flex";
    log("Camera disconnected — reconnecting in 2s");
    setTimeout(connectCamera, 2000);
  };

  cameraWs.onerror = () => cameraWs.close();
}

connectCamera();

// ── Stop button ──────────────────────────────────────────────────

btnStop.addEventListener("click", () => sendCmd("stop"));
btnMoveStop.addEventListener("click", () => sendCmd("stop"));

// ── Obstacle avoidance toggle ────────────────────────────────────

oaToggle.addEventListener("change", () => {
  sendCmd("obstacle_avoidance", { enabled: oaToggle.checked });
});

// ── Speed level ──────────────────────────────────────────────────

speedSelect.addEventListener("change", () => {
  sendCmd("speed_level", { level: parseInt(speedSelect.value) });
});

// ── Actions grid ─────────────────────────────────────────────────

const ACTIONS = [
  "stand_up", "stand_down", "balance_stand", "recovery_stand",
  "sit", "hello", "stretch", "dance1", "dance2", "heart",
  "front_flip", "front_jump", "back_flip", "left_flip",
  "hand_stand", "damp", "stop_move",
];

ACTIONS.forEach((name) => {
  const btn = document.createElement("button");
  btn.textContent = name.replace(/_/g, " ");
  btn.addEventListener("click", () => sendCmd("action", { name }));
  actionsGrid.appendChild(btn);
});

// ── Movement: on-screen buttons ──────────────────────────────────

let moveInterval = null;
let activeVx = 0, activeVy = 0, activeVyaw = 0;

function getSpeedMult() {
  return SPEED_MULT[parseInt(speedSelect.value) - 1] || 0.3;
}

function startMove(vx, vy, vyaw) {
  stopMove();
  const m = getSpeedMult();
  activeVx = vx * m;
  activeVy = vy * m;
  activeVyaw = vyaw * m;
  sendCmd("move", { vx: activeVx, vy: activeVy, vyaw: activeVyaw });
  moveInterval = setInterval(() => {
    sendCmd("move", { vx: activeVx, vy: activeVy, vyaw: activeVyaw });
  }, 100); // 10 Hz
}

function stopMove() {
  if (moveInterval) {
    clearInterval(moveInterval);
    moveInterval = null;
  }
  activeVx = activeVy = activeVyaw = 0;
}

// Bind move-pad and rotate-row buttons
document.querySelectorAll(".move-pad button[data-vx], .rotate-row button[data-vx]").forEach((btn) => {
  const vx = parseFloat(btn.dataset.vx);
  const vy = parseFloat(btn.dataset.vy);
  const vyaw = parseFloat(btn.dataset.vyaw);

  function down(e) { e.preventDefault(); btn.classList.add("active"); startMove(vx, vy, vyaw); }
  function up(e)   { e.preventDefault(); btn.classList.remove("active"); stopMove(); sendCmd("stop"); }

  btn.addEventListener("mousedown", down);
  btn.addEventListener("mouseup", up);
  btn.addEventListener("mouseleave", up);
  btn.addEventListener("touchstart", down);
  btn.addEventListener("touchend", up);
  btn.addEventListener("touchcancel", up);
});

// ── Movement: keyboard ───────────────────────────────────────────

const KEY_MAP = {
  w: { vx: 1, vy: 0, vyaw: 0 },
  s: { vx: -1, vy: 0, vyaw: 0 },
  a: { vx: 0, vy: 1, vyaw: 0 },
  d: { vx: 0, vy: -1, vyaw: 0 },
  q: { vx: 0, vy: 0, vyaw: 1 },
  e: { vx: 0, vy: 0, vyaw: -1 },
};

const pressedKeys = new Set();

document.addEventListener("keydown", (ev) => {
  const key = ev.key.toLowerCase();
  if (key === " ") { sendCmd("stop"); return; }
  if (!KEY_MAP[key] || pressedKeys.has(key)) return;
  pressedKeys.add(key);
  updateKeyboardMove();
});

document.addEventListener("keyup", (ev) => {
  const key = ev.key.toLowerCase();
  if (!KEY_MAP[key]) return;
  pressedKeys.delete(key);
  updateKeyboardMove();
});

let keyMoveInterval = null;

function updateKeyboardMove() {
  if (pressedKeys.size === 0) {
    if (keyMoveInterval) {
      clearInterval(keyMoveInterval);
      keyMoveInterval = null;
    }
    sendCmd("stop");
    return;
  }

  let vx = 0, vy = 0, vyaw = 0;
  for (const key of pressedKeys) {
    const m = KEY_MAP[key];
    vx += m.vx;
    vy += m.vy;
    vyaw += m.vyaw;
  }
  // Clamp
  vx = Math.max(-1, Math.min(1, vx));
  vy = Math.max(-1, Math.min(1, vy));
  vyaw = Math.max(-1, Math.min(1, vyaw));

  const mult = getSpeedMult();
  const fvx = vx * mult, fvy = vy * mult, fvyaw = vyaw * mult;

  // Send immediately then at 10 Hz
  sendCmd("move", { vx: fvx, vy: fvy, vyaw: fvyaw });
  if (keyMoveInterval) clearInterval(keyMoveInterval);
  keyMoveInterval = setInterval(() => {
    sendCmd("move", { vx: fvx, vy: fvy, vyaw: fvyaw });
  }, 100);
}

// ── Load initial status ──────────────────────────────────────────

(async () => {
  const resp = await sendCmd("status");
  if (resp && resp.data) {
    oaToggle.checked = resp.data.obstacle_avoidance;
    speedSelect.value = String(resp.data.speed_level || 1);
  }
})();
