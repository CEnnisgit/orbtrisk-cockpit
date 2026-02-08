/* global Cesium */

const statusEl = document.getElementById("globeStatus");
const countsEl = document.getElementById("globeCounts");
const infoEl = document.getElementById("globeInfo");
const searchInput = document.getElementById("globeSearch");
const searchButton = document.getElementById("globeSearchBtn");
const resetButton = document.getElementById("globeResetBtn");
const zoomInButton = document.getElementById("globeZoomInBtn");
const zoomOutButton = document.getElementById("globeZoomOutBtn");
const toggleCatalog = document.getElementById("toggleCatalog");
const toggleOperator = document.getElementById("toggleOperator");

const modeEarthBtn = document.getElementById("modeEarth");
const modeSolarBtn = document.getElementById("modeSolar");
const timeScrubber = document.getElementById("timeScrubber");
const timeLabel = document.getElementById("timeLabel");
const timePlayBtn = document.getElementById("timePlayBtn");
const timeNowBtn = document.getElementById("timeNowBtn");
const rateButtons = Array.from(document.querySelectorAll(".rate-btn"));
const toggleFollow = document.getElementById("toggleFollow");
const toggleRide = document.getElementById("toggleRide");
const toggleTrails = document.getElementById("toggleTrails");
const toggleGroundTrack = document.getElementById("toggleGroundTrack");
const toggleFootprint = document.getElementById("toggleFootprint");
const panelEl = document.getElementById("viewerPanel");
const panelToggleBtn = document.getElementById("panelToggleBtn");

const catalogCollection = new Cesium.PointPrimitiveCollection();
const operatorCollection = new Cesium.PointPrimitiveCollection();

let viewer;
let worker;
let objects = [];
let primitiveList = [];
let primitiveById = new Map();
let selectedPrimitive = null;
let selectedObject = null;
let searchIndex = new Map();
let noradIndex = new Map();
let trailEntity = null;
let groundTrackEntity = null;
let footprintEntity = null;
let solarEntities = [];
let solarLastUpdate = 0;
let cloudCollection = null;
let mode = "earth";
let followEnabled = false;
let rideEnabled = false;

let isPlaying = false;
let playbackRate = 1;
let timeOffsetMinutes = 0;
let virtualNowMs = Date.now();
let playStartWallMs = Date.now();
let playStartVirtualMs = virtualNowMs;
let clockTimer = null;

const SOLAR_DISTANCE_SCALE = 1e-6;
const SOLAR_RADIUS_SCALE = 1e-4;
const SOLAR_UPDATE_MS = 10000;

const COLOR_MAP = {
  PAYLOAD: Cesium.Color.fromCssColorString("#6ee7ff"),
  DEBRIS: Cesium.Color.fromCssColorString("#f97316"),
  "ROCKET BODY": Cesium.Color.fromCssColorString("#f43f5e"),
};

const SOLAR_COLORS = {
  Sun: Cesium.Color.fromCssColorString("#ffd166"),
  Mercury: Cesium.Color.fromCssColorString("#9ca3af"),
  Venus: Cesium.Color.fromCssColorString("#f6bd60"),
  Earth: Cesium.Color.fromCssColorString("#6ee7ff"),
  Moon: Cesium.Color.fromCssColorString("#d1d5db"),
  Mars: Cesium.Color.fromCssColorString("#ef4444"),
  Jupiter: Cesium.Color.fromCssColorString("#f59e0b"),
  Saturn: Cesium.Color.fromCssColorString("#fcd34d"),
  Uranus: Cesium.Color.fromCssColorString("#5eead4"),
  Neptune: Cesium.Color.fromCssColorString("#60a5fa"),
  // Moons
  Io: Cesium.Color.fromCssColorString("#fbbf24"),
  Europa: Cesium.Color.fromCssColorString("#e0e7ff"),
  Ganymede: Cesium.Color.fromCssColorString("#a8a29e"),
  Callisto: Cesium.Color.fromCssColorString("#78716c"),
  Titan: Cesium.Color.fromCssColorString("#fb923c"),
  Enceladus: Cesium.Color.fromCssColorString("#e2e8f0"),
  Triton: Cesium.Color.fromCssColorString("#93c5fd"),
  // Dwarf planets
  Pluto: Cesium.Color.fromCssColorString("#d4a574"),
  Eris: Cesium.Color.fromCssColorString("#c4b5a0"),
  Makemake: Cesium.Color.fromCssColorString("#d97706"),
  Haumea: Cesium.Color.fromCssColorString("#a3a3a3"),
  Sedna: Cesium.Color.fromCssColorString("#dc2626"),
};

/* Category rendering config */
const CATEGORY_STYLE = {
  star:         { radiusScale: 1,    labelSize: "14px", alpha: 1.0,  maxLabelDist: 2e9 },
  planet:       { radiusScale: 1,    labelSize: "13px", alpha: 0.9,  maxLabelDist: 1e9 },
  dwarf_planet: { radiusScale: 6,    labelSize: "11px", alpha: 0.8,  maxLabelDist: 6e7 },
  moon:         { radiusScale: 8,    labelSize: "10px", alpha: 0.75, maxLabelDist: 4e7 },
  small_body:   { radiusScale: 40,   labelSize: "10px", alpha: 0.7,  maxLabelDist: 4e7 },
};

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function setStatus(message) {
  if (statusEl) {
    statusEl.textContent = message;
  }
}

function setCounts(total, missing) {
  if (!countsEl) return;
  const missingText = missing ? `, ${missing} missing TLE` : "";
  countsEl.textContent = `${total} objects loaded${missingText}`;
}

function buildViewer() {
  const token = window.CESIUM_ION_TOKEN;
  const nightAssetId = window.CESIUM_NIGHT_ASSET_ID;
  const useIon = token && token.trim().length > 0;
  if (useIon) {
    Cesium.Ion.defaultAccessToken = token;
  }
  const baseLayer = new Cesium.SingleTileImageryProvider({
    url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=",
  });
  const imageryProvider = useIon
    ? Cesium.createWorldImagery({
        style: Cesium.IonWorldImageryStyle.AERIAL,
      })
    : baseLayer;

  const terrainProvider = useIon && Cesium.createWorldTerrain ? Cesium.createWorldTerrain() : undefined;
  viewer = new Cesium.Viewer("cesiumContainer", {
    imageryProvider,
    terrainProvider,
    baseLayerPicker: false,
    geocoder: false,
    homeButton: false,
    sceneModePicker: false,
    navigationHelpButton: false,
    animation: false,
    timeline: false,
    fullscreenButton: false,
    infoBox: false,
    selectionIndicator: false,
  });
  viewer.clock.shouldAnimate = false;
  viewer.clock.multiplier = 0;
  viewer.scene.globe.enableLighting = true;
  viewer.scene.globe.depthTestAgainstTerrain = true;
  viewer.scene.globe.showGroundAtmosphere = true;
  viewer.scene.skyAtmosphere.show = true;
  viewer.scene.sun.show = true;
  viewer.scene.moon.show = true;
  viewer.scene.fog.enabled = true;
  viewer.scene.fog.density = 0.00025;
  viewer.scene.backgroundColor = Cesium.Color.fromCssColorString("#05070d");
  viewer.scene.primitives.add(catalogCollection);
  viewer.scene.primitives.add(operatorCollection);
  viewer.scene.screenSpaceCameraController.inertiaSpin = 0.0;
  viewer.scene.screenSpaceCameraController.inertiaTranslate = 0.0;
  viewer.scene.screenSpaceCameraController.inertiaZoom = 0.0;
  viewer.scene.screenSpaceCameraController.minimumZoomDistance = 200000.0;
  viewer.scene.screenSpaceCameraController.maximumZoomDistance = 90000000.0;
  viewer.screenSpaceEventHandler.removeInputAction(Cesium.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);
  addNightLayer(nightAssetId);
  addClouds();
  resetCameraView();
  setupPicking();
}

function addNightLayer(assetIdRaw) {
  if (!assetIdRaw) return;
  const assetId = Number(assetIdRaw);
  if (!assetId || !Cesium.IonImageryProvider) return;
  try {
    const nightLayer = new Cesium.IonImageryProvider({ assetId });
    const layer = viewer.imageryLayers.addImageryProvider(nightLayer);
    layer.alpha = 0.35;
    layer.brightness = 0.3;
  } catch (err) {
    // Ignore if asset unavailable.
  }
}

function addClouds() {
  if (!Cesium.CloudCollection) return;
  cloudCollection = new Cesium.CloudCollection();
  const cloudCount = 30;
  for (let i = 0; i < cloudCount; i += 1) {
    const lon = Cesium.Math.toRadians(-180 + Math.random() * 360);
    const lat = Cesium.Math.toRadians(-60 + Math.random() * 120);
    const height = 6000 + Math.random() * 4000;
    const position = Cesium.Cartesian3.fromRadians(lon, lat, height);
    cloudCollection.add({
      position,
      scale: new Cesium.Cartesian2(120000 + Math.random() * 120000, 60000 + Math.random() * 90000),
      slice: 0.3 + Math.random() * 0.4,
      brightness: 1.0,
    });
  }
  viewer.scene.primitives.add(cloudCollection);
}

function colorForObject(obj) {
  if (obj.is_operator_asset) {
    return Cesium.Color.fromCssColorString("#ffb347");
  }
  const key = obj.object_type ? obj.object_type.toUpperCase() : "PAYLOAD";
  return COLOR_MAP[key] || Cesium.Color.fromCssColorString("#94a3b8");
}

function buildPrimitives(items) {
  catalogCollection.removeAll();
  operatorCollection.removeAll();
  primitiveList = [];
  primitiveById.clear();
  searchIndex.clear();
  noradIndex.clear();

  items.forEach((obj) => {
    const collection = obj.is_operator_asset ? operatorCollection : catalogCollection;
    const primitive = collection.add({
      position: new Cesium.Cartesian3(0, 0, 0),
      color: colorForObject(obj),
      pixelSize: obj.is_operator_asset ? 6 : 3,
      outlineColor: Cesium.Color.BLACK,
      outlineWidth: 0,
      id: obj,
    });
    primitiveList.push(primitive);
    primitiveById.set(obj.id, primitive);
    if (obj.name) {
      searchIndex.set(obj.name.toLowerCase(), obj.id);
    }
    if (obj.norad_cat_id) {
      noradIndex.set(String(obj.norad_cat_id), obj.id);
    }
  });
}

function setupPicking() {
  const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
  handler.setInputAction((movement) => {
    const picked = viewer.scene.pick(movement.position);
    if (!Cesium.defined(picked)) return;
    if (picked.id && picked.id.isSolarBody) {
      selectSolarBody(picked.id.solarData);
      return;
    }
    if (picked.id) {
      selectObject(picked.id, picked.primitive);
    }
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
}

function selectObject(obj, primitive) {
  if (!obj) return;
  selectedObject = obj;
  if (selectedPrimitive) {
    selectedPrimitive.outlineWidth = 0;
    selectedPrimitive.pixelSize = selectedPrimitive._origPixelSize || selectedPrimitive.pixelSize;
  }
  if (primitive) {
    primitive._origPixelSize = primitive._origPixelSize || primitive.pixelSize;
    primitive.pixelSize = primitive._origPixelSize + 2;
    primitive.outlineWidth = 2;
    primitive.outlineColor = Cesium.Color.fromCssColorString("#6ee7ff");
    selectedPrimitive = primitive;
  }
  if (worker) {
    worker.postMessage({ type: "select", objectId: obj.id });
  }
  renderInfo(obj);
}

function selectSolarBody(data) {
  selectedObject = null;
  selectedPrimitive = null;
  if (worker) {
    worker.postMessage({ type: "select", objectId: null });
  }
  renderInfo(data);
}

function formatAgeHours(hours) {
  if (hours === null || hours === undefined) return "\u2014";
  if (hours < 1) {
    const mins = Math.round(hours * 60);
    return `${mins}m`;
  }
  return `${hours.toFixed(1)}h`;
}

function categoryLabel(cat) {
  const labels = {
    star: "Star",
    planet: "Planet",
    dwarf_planet: "Dwarf Planet",
    moon: "Moon",
    small_body: "Small Body / Asteroid",
  };
  return labels[cat] || cat || "Unknown";
}

function renderInfo(obj) {
  if (!infoEl) return;
  if (!obj) {
    infoEl.innerHTML = '<div class="empty-state">Click a satellite or use search to view details.</div>';
    return;
  }
  if (obj.is_solar_body) {
    const parentRow = obj.parent
      ? `<div class="info-row"><span>Parent</span><span>${escapeHtml(obj.parent)}</span></div>`
      : "";
    infoEl.innerHTML = `
      <div class="info-card">
        <div class="info-header">
          <div class="info-title">${escapeHtml(obj.name)}</div>
          <span class="quality-badge tier-b">Solar Ephemeris</span>
        </div>
        <div class="info-row"><span>Type</span><span>${escapeHtml(categoryLabel(obj.category))}</span></div>
        <div class="info-row"><span>Radius</span><span>${obj.radius_km.toLocaleString()} km</span></div>
        ${parentRow}
        <div class="info-row"><span>Frame</span><span>J2000 / Heliocentric</span></div>
      </div>
    `;
    return;
  }

  const tier = (obj.quality_tier || "D").toUpperCase();
  const tierClass = `tier-${tier.toLowerCase()}`;
  const displayType = obj.is_operator_asset ? "Operator Satellite" : (obj.object_type || "Unknown");
  const rows = [
    ["NORAD ID", obj.norad_cat_id || "\u2014"],
    ["Type", displayType],
    ["Intl Designator", obj.international_designator || "\u2014"],
    ["Operator Asset", obj.is_operator_asset ? "Yes" : "No"],
    ["TLE Epoch", obj.tle_epoch || "\u2014"],
    ["TLE Source", obj.tle_source || "\u2014"],
    ["TLE Age", formatAgeHours(obj.tle_age_hours)],
  ];

  const metaRows = [];
  const pushMeta = (label, value, suffix = "") => {
    if (value === undefined || value === null || value === "") return;
    metaRows.push([label, `${value}${suffix}`]);
  };
  pushMeta("Owner", obj.owner);
  pushMeta("Ops Status", obj.ops_status_code);
  pushMeta("Launch", obj.launch_date);
  pushMeta("Decay", obj.decay_date);
  pushMeta("Apogee", obj.apogee_km, " km");
  pushMeta("Perigee", obj.perigee_km, " km");
  pushMeta("Inclination", obj.inclination_deg, "\u00b0");
  pushMeta("Period", obj.period_min, " min");
  pushMeta("RCS", obj.rcs_size);
  pushMeta("Orbit Center", obj.orbit_center);
  pushMeta("Orbit Type", obj.orbit_type);

  const infoRowsHtml = rows
    .map((row) => `<div class="info-row"><span>${escapeHtml(row[0])}</span><span>${escapeHtml(row[1])}</span></div>`)
    .join("");
  const metaHtml = metaRows
    .map((row) => `<div class="info-row"><span>${escapeHtml(row[0])}</span><span>${escapeHtml(row[1])}</span></div>`)
    .join("");

  infoEl.innerHTML = `
    <div class="info-card">
      <div class="info-header">
        <div class="info-title">${escapeHtml(obj.name || "Unnamed Object")}</div>
        <span class="quality-badge ${tierClass}">Tier ${tier}</span>
      </div>
      ${infoRowsHtml}
      ${metaRows.length ? `<div class="info-section"><div class="info-section-title">Catalog</div>${metaHtml}</div>` : ""}
    </div>
    <div class="ai-card">
      <div class="ai-header">
        <span>AI Briefing</span>
        <button id="aiSummaryBtn" class="secondary" type="button">Generate</button>
      </div>
      <div id="aiSummary" class="ai-summary">Ask for an AI summary or details on this object.</div>
      <ul id="aiFacts" class="ai-facts"></ul>
      <div class="ai-thread" id="aiThread"></div>
      <div class="ai-input">
        <input id="aiInput" type="text" placeholder="Ask a question" />
        <button id="aiSendBtn" class="btn-primary" type="button">Ask</button>
      </div>
      <div id="aiCitations" class="panel-sub"></div>
    </div>
  `;
  bindAiControls(obj);
}

function bindAiControls(obj) {
  const summaryBtn = document.getElementById("aiSummaryBtn");
  const sendBtn = document.getElementById("aiSendBtn");
  const aiInput = document.getElementById("aiInput");
  const aiSummary = document.getElementById("aiSummary");
  const aiFacts = document.getElementById("aiFacts");
  const aiThread = document.getElementById("aiThread");
  const aiCitations = document.getElementById("aiCitations");
  if (!summaryBtn || !sendBtn || !aiInput || !aiSummary || !aiThread || !aiFacts || !aiCitations) return;

  aiThread.innerHTML = "";
  aiFacts.innerHTML = "";
  aiCitations.textContent = "";
  const messages = [];

  const appendMessage = (role, content, className) => {
    const container = document.createElement("div");
    const roleClass = className || (role === "You" ? "user-message" : "assistant-message");
    container.className = `ai-message ${roleClass}`;
    container.innerHTML = `<span>${role}</span><div>${escapeHtml(content)}</div>`;
    aiThread.appendChild(container);
    aiThread.scrollTop = aiThread.scrollHeight;
    return container;
  };

  summaryBtn.addEventListener("click", async () => {
    summaryBtn.disabled = true;
    summaryBtn.textContent = "Generating...";
    aiSummary.textContent = "Generating summary...";
    aiFacts.innerHTML = "";
    aiCitations.textContent = "";
    try {
      const resp = await fetch("/ai/object-summary", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ object_id: obj.id, style: "concise" }),
      });
      if (!resp.ok) {
        throw new Error("AI summary failed");
      }
      const data = await resp.json();
      aiSummary.textContent = data.summary || "No summary available.";
      if (Array.isArray(data.key_facts)) {
        data.key_facts.forEach((fact) => {
          const li = document.createElement("li");
          li.textContent = fact;
          aiFacts.appendChild(li);
        });
      }
      if (Array.isArray(data.citations)) {
        aiCitations.textContent = `Sources: ${data.citations.join(", ")}`;
      }
    } catch (err) {
      aiSummary.className = "ai-summary ai-error";
      aiSummary.textContent = "AI is unavailable. Make sure Ollama is running.";
    } finally {
      summaryBtn.disabled = false;
      summaryBtn.textContent = "Generate";
    }
  });

  const sendMessage = async () => {
    const question = aiInput.value.trim();
    if (!question) return;
    aiInput.value = "";
    sendBtn.disabled = true;
    appendMessage("You", question, "user-message");
    messages.push({ role: "user", content: question });
    const thinkingEl = document.createElement("div");
    thinkingEl.className = "ai-thinking";
    thinkingEl.textContent = "Thinking...";
    aiThread.appendChild(thinkingEl);
    aiThread.scrollTop = aiThread.scrollHeight;
    try {
      const resp = await fetch("/ai/object-chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ object_id: obj.id, messages }),
      });
      if (!resp.ok) {
        throw new Error("AI chat failed");
      }
      const data = await resp.json();
      thinkingEl.remove();
      appendMessage("Assistant", data.reply || "No response.", "assistant-message");
      messages.push({ role: "assistant", content: data.reply || "" });
      if (Array.isArray(data.citations)) {
        aiCitations.textContent = `Sources: ${data.citations.join(", ")}`;
      }
    } catch (err) {
      thinkingEl.remove();
      const errMsg = appendMessage("Assistant", "AI is unavailable. Make sure Ollama is running.", "assistant-message");
      errMsg.querySelector("div").classList.add("ai-error");
    } finally {
      sendBtn.disabled = false;
    }
  };

  sendBtn.addEventListener("click", sendMessage);
  aiInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      sendMessage();
    }
  });
}

function searchAndFocus() {
  const query = searchInput.value.trim().toLowerCase();
  if (!query) return;

  let matchId = null;
  if (noradIndex.has(query)) {
    matchId = noradIndex.get(query);
  } else {
    for (const [name, id] of searchIndex.entries()) {
      if (name.includes(query)) {
        matchId = id;
        break;
      }
    }
  }

  if (!matchId) {
    if (statusEl) {
      statusEl.style.color = "#ff6b6b";
      statusEl.textContent = "No match found.";
      setTimeout(() => { statusEl.style.color = ""; }, 3000);
    }
    return;
  }

  const primitive = primitiveById.get(matchId);
  const obj = objects.find((item) => item.id === matchId);
  if (!primitive || !obj) return;
  const position = primitive.position;
  if (Cesium.defined(position)) {
    const carto = Cesium.Cartographic.fromCartesian(position);
    viewer.camera.flyTo({
      destination: Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, carto.height + 800000),
      duration: 1.2,
    });
  }
  selectObject(obj, primitive);
}

function togglePanel() {
  if (!panelEl) return;
  panelEl.classList.toggle("collapsed");
  if (panelToggleBtn) {
    panelToggleBtn.innerHTML = panelEl.classList.contains("collapsed") ? "&lsaquo;" : "&rsaquo;";
  }
}

function setupControls() {
  if (toggleCatalog) {
    toggleCatalog.addEventListener("change", applyLayerVisibility);
  }
  if (toggleOperator) {
    toggleOperator.addEventListener("change", applyLayerVisibility);
  }
  if (searchButton) {
    searchButton.addEventListener("click", searchAndFocus);
  }
  if (resetButton) {
    resetButton.addEventListener("click", resetCameraView);
  }
  if (zoomInButton) {
    zoomInButton.addEventListener("click", () => zoomCamera(0.6));
  }
  if (zoomOutButton) {
    zoomOutButton.addEventListener("click", () => zoomCamera(1.4));
  }
  if (searchInput) {
    searchInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        searchAndFocus();
      }
    });
  }
  if (modeEarthBtn) {
    modeEarthBtn.addEventListener("click", () => setMode("earth"));
  }
  if (modeSolarBtn) {
    modeSolarBtn.addEventListener("click", () => setMode("solar"));
  }
  if (timeScrubber) {
    timeScrubber.addEventListener("input", () => {
      timeOffsetMinutes = Number(timeScrubber.value || 0);
      updateVirtualTime(true);
    });
  }
  if (timePlayBtn) {
    timePlayBtn.addEventListener("click", togglePlay);
  }
  if (timeNowBtn) {
    timeNowBtn.addEventListener("click", resetTime);
  }
  rateButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      rateButtons.forEach((el) => el.classList.remove("active"));
      btn.classList.add("active");
      playbackRate = Number(btn.dataset.rate || 1);
      updateVirtualTime(true);
    });
  });
  if (toggleFollow) {
    toggleFollow.addEventListener("change", () => {
      followEnabled = toggleFollow.checked;
      if (!followEnabled) {
        viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
      }
    });
  }
  if (toggleRide) {
    toggleRide.addEventListener("change", () => {
      rideEnabled = toggleRide.checked;
    });
  }
  if (panelToggleBtn) {
    panelToggleBtn.addEventListener("click", togglePanel);
  }
}

function resetCameraView() {
  if (!viewer) return;
  const destination = Cesium.Cartesian3.fromDegrees(-20, 20, 18000000);
  viewer.camera.setView({
    destination,
  });
}

function zoomCamera(factor) {
  if (!viewer) return;
  const camera = viewer.camera;
  const carto = Cesium.Cartographic.fromCartesian(camera.position);
  const height = Math.max(carto.height * factor, viewer.scene.screenSpaceCameraController.minimumZoomDistance);
  const destination = Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, height);
  camera.setView({ destination });
}

function applyLayerVisibility() {
  if (mode === "solar") {
    catalogCollection.show = false;
    operatorCollection.show = false;
    return;
  }
  if (toggleCatalog) {
    catalogCollection.show = toggleCatalog.checked;
  }
  if (toggleOperator) {
    operatorCollection.show = toggleOperator.checked;
  }
}

function updateVirtualTime(resetPlayBase = false) {
  if (resetPlayBase) {
    playStartWallMs = Date.now();
  }
  if (isPlaying) {
    if (resetPlayBase) {
      playStartVirtualMs = Date.now() + timeOffsetMinutes * 60 * 1000;
    }
    virtualNowMs = playStartVirtualMs + (Date.now() - playStartWallMs) * playbackRate;
  } else {
    virtualNowMs = Date.now() + timeOffsetMinutes * 60 * 1000;
  }
  updateTimeLabel();
}

function updateTimeLabel() {
  if (!timeLabel) return;
  const label = new Date(virtualNowMs).toISOString().replace("T", " ").replace("Z", " UTC");
  if (timeLabel.textContent === label) return;
  timeLabel.textContent = label;
  if (timeScrubber && !isPlaying) {
    timeScrubber.value = timeOffsetMinutes;
  }
}

function togglePlay() {
  isPlaying = !isPlaying;
  if (timePlayBtn) {
    timePlayBtn.textContent = isPlaying ? "Pause" : "Play";
  }
  playStartWallMs = Date.now();
  playStartVirtualMs = virtualNowMs;
}

function resetTime() {
  timeOffsetMinutes = 0;
  if (timeScrubber) {
    timeScrubber.value = 0;
  }
  isPlaying = false;
  if (timePlayBtn) {
    timePlayBtn.textContent = "Play";
  }
  updateVirtualTime(true);
}

function startClock() {
  if (clockTimer) {
    clearInterval(clockTimer);
  }
  clockTimer = setInterval(() => {
    if (isPlaying) {
      updateVirtualTime(false);
    } else {
      virtualNowMs = Date.now() + timeOffsetMinutes * 60 * 1000;
      updateTimeLabel();
    }
    if (worker) {
      worker.postMessage({ type: "time", now: virtualNowMs });
    }
    if (mode === "solar") {
      updateSolarPositions();
    }
  }, 1000);
}

function updateTrail(trailPositions, groundTrackPositions) {
  if (!trailEntity) {
    trailEntity = viewer.entities.add({
      polyline: {
        positions: [],
        width: 2,
        material: Cesium.Color.fromCssColorString("#6ee7ff"),
      },
    });
  }
  if (!groundTrackEntity) {
    groundTrackEntity = viewer.entities.add({
      polyline: {
        positions: [],
        width: 1,
        material: Cesium.Color.fromCssColorString("#ffb347"),
      },
    });
  }
  if (Array.isArray(trailPositions) && trailPositions.length) {
    const positions = trailPositions.map((p) => new Cesium.Cartesian3(p.x, p.y, p.z));
    trailEntity.polyline.positions = positions;
    trailEntity.show = toggleTrails ? toggleTrails.checked : true;
  } else if (trailEntity) {
    trailEntity.show = false;
  }
  if (Array.isArray(groundTrackPositions) && groundTrackPositions.length) {
    const positions = groundTrackPositions.map((p) => new Cesium.Cartesian3(p.x, p.y, p.z));
    groundTrackEntity.polyline.positions = positions;
    groundTrackEntity.show = toggleGroundTrack ? toggleGroundTrack.checked : false;
  } else if (groundTrackEntity) {
    groundTrackEntity.show = false;
  }
}

function updateFootprint() {
  if (!toggleFootprint || !toggleFootprint.checked) {
    if (footprintEntity) {
      footprintEntity.show = false;
    }
    return;
  }
  if (!selectedPrimitive || !Cesium.defined(selectedPrimitive.position)) {
    if (footprintEntity) {
      footprintEntity.show = false;
    }
    return;
  }
  const carto = Cesium.Cartographic.fromCartesian(selectedPrimitive.position);
  const height = carto.height;
  const earthRadius = viewer.scene.globe.ellipsoid.maximumRadius;
  const theta = Math.acos(earthRadius / (earthRadius + height));
  const radius = earthRadius * theta;
  const position = Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, 0);
  if (!footprintEntity) {
    footprintEntity = viewer.entities.add({
      position,
      ellipse: {
        semiMajorAxis: radius,
        semiMinorAxis: radius,
        material: Cesium.Color.fromCssColorString("#6ee7ff").withAlpha(0.2),
        outline: true,
        outlineColor: Cesium.Color.fromCssColorString("#6ee7ff").withAlpha(0.6),
      },
    });
  } else {
    footprintEntity.position = position;
    footprintEntity.ellipse.semiMajorAxis = radius;
    footprintEntity.ellipse.semiMinorAxis = radius;
  }
  footprintEntity.show = true;
}

function applyFollowCamera() {
  if (!followEnabled || !selectedPrimitive || !Cesium.defined(selectedPrimitive.position)) {
    return;
  }
  const offset = rideEnabled
    ? new Cesium.Cartesian3(0, -1200000, 600000)
    : new Cesium.Cartesian3(0, -3500000, 1800000);
  viewer.camera.lookAt(selectedPrimitive.position, offset);
}

async function loadCatalog() {
  setStatus("Loading catalog objects\u2026");
  const statusContainer = document.querySelector(".viewer-status");
  if (statusContainer) statusContainer.classList.add("loading");
  const response = await fetch("/catalog/objects");
  if (statusContainer) statusContainer.classList.remove("loading");
  if (!response.ok) {
    if (statusEl) statusEl.style.color = "#ff6b6b";
    setStatus("Failed to load catalog objects.");
    return;
  }
  const payload = await response.json();
  objects = payload.items || [];
  buildPrimitives(objects);
  setCounts(payload.total || objects.length, payload.missing_tle || 0);
  setStatus(`Tracking ${objects.length} objects.`);

  worker = new Worker("/static/globe-worker.js");
  worker.postMessage({
    type: "init",
    objects,
    intervalMs: 2000,
  });
  worker.onmessage = (event) => {
    if (event.data.type === "positions") {
      const positions = event.data.positions || [];
      positions.forEach((item, index) => {
        const primitive = primitiveList[index];
        if (!primitive) return;
        if (item.ok) {
          primitive.show = true;
          primitive.position = new Cesium.Cartesian3(item.x, item.y, item.z);
        } else {
          primitive.show = false;
        }
      });
      updateTrail(event.data.trail || [], event.data.groundTrack || []);
      updateFootprint();
      applyFollowCamera();
    }
  };
}

function setMode(nextMode) {
  if (mode === nextMode) return;
  mode = nextMode;
  if (modeEarthBtn && modeSolarBtn) {
    modeEarthBtn.classList.toggle("active", mode === "earth");
    modeSolarBtn.classList.toggle("active", mode === "solar");
  }
  if (mode === "solar") {
    viewer.scene.globe.show = false;
    viewer.scene.skyAtmosphere.show = false;
    catalogCollection.show = false;
    operatorCollection.show = false;
    if (searchInput) searchInput.disabled = true;
    if (searchButton) searchButton.disabled = true;
    viewer.scene.screenSpaceCameraController.maximumZoomDistance = 5e9;
    setStatus("Solar system mode active.");
    loadSolarScene();
  } else {
    viewer.scene.globe.show = true;
    viewer.scene.skyAtmosphere.show = true;
    applyLayerVisibility();
    if (searchInput) searchInput.disabled = false;
    if (searchButton) searchButton.disabled = false;
    viewer.scene.screenSpaceCameraController.maximumZoomDistance = 90000000.0;
    setStatus(`Tracking ${objects.length} objects.`);
    clearSolarScene();
    resetCameraView();
  }
}

function clearSolarScene() {
  solarLastUpdate = 0;
  solarEntities.forEach((entity) => viewer.entities.remove(entity));
  solarEntities = [];
}

async function loadSolarScene() {
  clearSolarScene();
  await updateSolarPositions(true);
  const destination = new Cesium.Cartesian3(0, -6000000, 3000000);
  viewer.camera.setView({ destination });
}

async function updateSolarPositions(force = false) {
  if (mode !== "solar") return;
  const now = Date.now();
  if (!force && solarLastUpdate && now - solarLastUpdate < SOLAR_UPDATE_MS) {
    return;
  }
  solarLastUpdate = now;
  try {
    const epoch = new Date(virtualNowMs).toISOString();
    const response = await fetch(
      `/solar/positions?epoch=${encodeURIComponent(epoch)}&include_small_bodies=1`
    );
    if (!response.ok) {
      return;
    }
    const data = await response.json();
    if (!data || !Array.isArray(data.bodies)) return;
    if (solarEntities.length !== data.bodies.length) {
      clearSolarScene();
      data.bodies.forEach((body) => {
        const cat = body.category || "small_body";
        const style = CATEGORY_STYLE[cat] || CATEGORY_STYLE.small_body;
        const radius = Math.max(1, body.radius_km) * 1000 * SOLAR_RADIUS_SCALE * style.radiusScale;
        const color = SOLAR_COLORS[body.name]
          || (cat === "dwarf_planet" ? Cesium.Color.fromCssColorString("#d4a574")
          : Cesium.Color.fromCssColorString("#f97316"));
        const entity = viewer.entities.add({
          position: new Cesium.Cartesian3(0, 0, 0),
          ellipsoid: {
            radii: new Cesium.Cartesian3(radius, radius, radius),
            material: color.withAlpha(style.alpha),
          },
          label: {
            text: body.name,
            font: `${style.labelSize} Space Grotesk, sans-serif`,
            fillColor: Cesium.Color.WHITE,
            pixelOffset: new Cesium.Cartesian2(0, -18),
            showBackground: true,
            backgroundColor: Cesium.Color.fromCssColorString("#0a0c12").withAlpha(0.6),
            distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, style.maxLabelDist),
          },
        });
        entity.isSolarBody = true;
        entity.solarData = {
          is_solar_body: true,
          name: body.name,
          radius_km: body.radius_km,
          category: cat,
          parent: body.parent || null,
        };
        solarEntities.push(entity);
      });
    }
    data.bodies.forEach((body, idx) => {
      const position = body.position_km || [0, 0, 0];
      const scaled = new Cesium.Cartesian3(
        position[0] * 1000 * SOLAR_DISTANCE_SCALE,
        position[1] * 1000 * SOLAR_DISTANCE_SCALE,
        position[2] * 1000 * SOLAR_DISTANCE_SCALE
      );
      const entity = solarEntities[idx];
      if (entity) {
        entity.position = scaled;
      }
    });
  } catch (err) {
    setStatus("Failed to load solar positions.");
  }
}

buildViewer();
setupControls();
applyLayerVisibility();
loadCatalog();
startClock();
