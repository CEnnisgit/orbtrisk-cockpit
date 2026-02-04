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

const catalogCollection = new Cesium.PointPrimitiveCollection();
const operatorCollection = new Cesium.PointPrimitiveCollection();

let viewer;
let worker;
let objects = [];
let primitiveList = [];
let primitiveById = new Map();
let selectedPrimitive = null;
let searchIndex = new Map();
let noradIndex = new Map();

const COLOR_MAP = {
  PAYLOAD: Cesium.Color.fromCssColorString("#6ee7ff"),
  DEBRIS: Cesium.Color.fromCssColorString("#f97316"),
  "ROCKET BODY": Cesium.Color.fromCssColorString("#f43f5e"),
};

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
  Cesium.Ion.defaultAccessToken = "";
  const baseLayer = new Cesium.SingleTileImageryProvider({
    url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=",
  });
  viewer = new Cesium.Viewer("cesiumContainer", {
    imageryProvider: baseLayer,
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
  viewer.scene.globe.baseColor = Cesium.Color.fromCssColorString("#0a0c12");
  viewer.scene.backgroundColor = Cesium.Color.fromCssColorString("#0a0c12");
  viewer.scene.primitives.add(catalogCollection);
  viewer.scene.primitives.add(operatorCollection);
  viewer.scene.screenSpaceCameraController.inertiaSpin = 0.0;
  viewer.scene.screenSpaceCameraController.inertiaTranslate = 0.0;
  viewer.scene.screenSpaceCameraController.inertiaZoom = 0.0;
  viewer.scene.screenSpaceCameraController.minimumZoomDistance = 200000.0;
  viewer.scene.screenSpaceCameraController.maximumZoomDistance = 60000000.0;
  viewer.screenSpaceEventHandler.removeInputAction(Cesium.ScreenSpaceEventType.LEFT_DOUBLE_CLICK);
  resetCameraView();
  setupPicking();
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
    if (Cesium.defined(picked) && picked.id) {
      selectObject(picked.id, picked.primitive);
    }
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
}

function selectObject(obj, primitive) {
  if (!obj) return;
  if (selectedPrimitive) {
    selectedPrimitive.outlineWidth = 0;
  }
  if (primitive) {
    primitive.outlineWidth = 2;
    primitive.outlineColor = Cesium.Color.WHITE;
    selectedPrimitive = primitive;
  }
  renderInfo(obj);
}

function renderInfo(obj) {
  if (!infoEl) return;
  const displayType = obj.is_operator_asset ? "Operator Satellite" : (obj.object_type || "Unknown");
  infoEl.innerHTML = `
    <div class="info-card">
      <div class="info-title">${obj.name || "Unnamed Object"}</div>
      <div class="info-row"><span>NORAD ID</span><span>${obj.norad_cat_id || "—"}</span></div>
      <div class="info-row"><span>Type</span><span>${displayType}</span></div>
      <div class="info-row"><span>Intl Designator</span><span>${obj.international_designator || "—"}</span></div>
      <div class="info-row"><span>Operator Asset</span><span>${obj.is_operator_asset ? "Yes" : "No"}</span></div>
      <div class="info-row"><span>TLE Epoch</span><span>${obj.tle_epoch || "—"}</span></div>
    </div>
  `;
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
    setStatus("No match found.");
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

function setupControls() {
  if (toggleCatalog) {
    toggleCatalog.addEventListener("change", () => {
      catalogCollection.show = toggleCatalog.checked;
    });
  }
  if (toggleOperator) {
    toggleOperator.addEventListener("change", () => {
      operatorCollection.show = toggleOperator.checked;
    });
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

async function loadCatalog() {
  setStatus("Loading catalog objects…");
  const response = await fetch("/catalog/objects");
  if (!response.ok) {
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
    }
  };
}

buildViewer();
setupControls();
loadCatalog();
