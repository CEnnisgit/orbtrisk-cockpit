/* global satellite */

importScripts("https://cdn.jsdelivr.net/npm/satellite.js@5.0.0/dist/satellite.min.js");

let entries = [];
let timer = null;
let intervalMs = 2000;

function buildEntries(objects) {
  entries = (objects || []).map((obj) => {
    try {
      const satrec = satellite.twoline2satrec(obj.tle_line1, obj.tle_line2);
      return { id: obj.id, satrec };
    } catch (err) {
      return { id: obj.id, satrec: null };
    }
  });
}

function computePositions() {
  if (!entries.length) return;
  const now = Date.now();
  const when = new Date(now);
  const gmst = satellite.gstime(when);
  const positions = new Array(entries.length);

  for (let i = 0; i < entries.length; i += 1) {
    const entry = entries[i];
    if (!entry.satrec) {
      positions[i] = { id: entry.id, ok: false };
      continue;
    }
    const pv = satellite.propagate(entry.satrec, when);
    if (!pv.position) {
      positions[i] = { id: entry.id, ok: false };
      continue;
    }
    const ecef = satellite.eciToEcf(pv.position, gmst);
    positions[i] = {
      id: entry.id,
      ok: true,
      x: ecef.x * 1000,
      y: ecef.y * 1000,
      z: ecef.z * 1000,
    };
  }

  postMessage({ type: "positions", positions, timestamp: now });
}

function startTimer() {
  if (timer) {
    clearInterval(timer);
  }
  timer = setInterval(computePositions, intervalMs);
  computePositions();
}

self.onmessage = (event) => {
  const data = event.data || {};
  if (data.type === "init") {
    intervalMs = data.intervalMs || intervalMs;
    buildEntries(data.objects || []);
    startTimer();
  }
  if (data.type === "stop" && timer) {
    clearInterval(timer);
    timer = null;
  }
};
