/* global satellite */

importScripts("https://cdn.jsdelivr.net/npm/satellite.js@5.0.0/dist/satellite.min.js");

let entries = [];
let timer = null;
let intervalMs = 2000;
let virtualNowMs = null;
let selectedId = null;
let trailSpanMs = 90 * 60 * 1000;
let trailStepMs = 5 * 60 * 1000;

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
  const now = virtualNowMs || Date.now();
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
    positions[i] = {
      id: entry.id,
      ok: true,
      // TEME (ECI) in meters; we transform to Earth-fixed in the main thread using Cesium.
      x: pv.position.x * 1000,
      y: pv.position.y * 1000,
      z: pv.position.z * 1000,
    };
  }

  let trail = [];
  if (selectedId) {
    const entry = entries.find((item) => item.id === selectedId);
    if (entry && entry.satrec) {
      const halfSpan = trailSpanMs / 2;
      for (let offset = -halfSpan; offset <= halfSpan; offset += trailStepMs) {
        const t = new Date(now + offset);
        const pv = satellite.propagate(entry.satrec, t);
        if (!pv.position) {
          continue;
        }
        trail.push({
          t: t.getTime(),
          gmst: satellite.gstime(t),
          x: pv.position.x * 1000,
          y: pv.position.y * 1000,
          z: pv.position.z * 1000,
        });
      }
    }
  }

  postMessage({ type: "positions", positions, trail, gmst, timestamp: now });
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
  if (data.type === "time") {
    virtualNowMs = data.now || null;
  }
  if (data.type === "select") {
    selectedId = data.objectId || null;
  }
  if (data.type === "settings") {
    trailSpanMs = data.trailSpanMs || trailSpanMs;
    trailStepMs = data.trailStepMs || trailStepMs;
  }
  if (data.type === "stop" && timer) {
    clearInterval(timer);
    timer = null;
  }
};
