const state = {
  app: window.__INITIAL_STATE__ || {},
  currentPoints: [],
  entities: [],
  currentLayer: "ROAD",
  mode: "DRAW",
  drag: {
    active: false,
    moved: false,
    startClientX: 0,
    startClientY: 0,
    lastClientX: 0,
    lastClientY: 0,
    suppressClick: false,
  },
  mapUpdateInFlight: false,
  lastWheelZoomAt: 0,
};

const layerStyles = {
  ROAD: { label: "道路", color: "#dc2626", fill: "rgba(220, 38, 38, 0.16)" },
  SITE: { label: "敷地", color: "#16a34a", fill: "rgba(22, 163, 74, 0.16)" },
  SLOPE: { label: "法面", color: "#2563eb", fill: "rgba(37, 99, 235, 0.16)" },
  STRUCTURE: { label: "構造物", color: "#d97706", fill: "rgba(217, 119, 6, 0.18)" },
};

const els = {
  addressInput: document.getElementById("addressInput"),
  searchButton: document.getElementById("searchButton"),
  fetchButton: document.getElementById("fetchButton"),
  finishLineButton: document.getElementById("finishLineButton"),
  finishPolygonButton: document.getElementById("finishPolygonButton"),
  undoButton: document.getElementById("undoButton"),
  saveDxfButton: document.getElementById("saveDxfButton"),
  saveProjectButton: document.getElementById("saveProjectButton"),
  loadProjectButton: document.getElementById("loadProjectButton"),
  clearPointsButton: document.getElementById("clearPointsButton"),
  mapImage: document.getElementById("mapImage"),
  mapStage: document.getElementById("mapStage"),
  mapCanvas: document.querySelector(".map-canvas"),
  drawOverlay: document.getElementById("drawOverlay"),
  scaleBar: document.getElementById("scaleBar"),
  scaleBarLine: document.getElementById("scaleBarLine"),
  scaleBarLabel: document.getElementById("scaleBarLabel"),
  statusMessage: document.getElementById("statusMessage"),
  mapModeBadge: document.getElementById("mapModeBadge"),
  infoAddress: document.getElementById("infoAddress"),
  infoDisplayName: document.getElementById("infoDisplayName"),
  infoMode: document.getElementById("infoMode"),
  infoLatitude: document.getElementById("infoLatitude"),
  infoLongitude: document.getElementById("infoLongitude"),
  infoTileType: document.getElementById("infoTileType"),
  infoZoom: document.getElementById("infoZoom"),
  infoCurrentLayer: document.getElementById("infoCurrentLayer"),
  infoApproxScale: document.getElementById("infoApproxScale"),
  infoOutputDir: document.getElementById("infoOutputDir"),
  infoCurrentPointCount: document.getElementById("infoCurrentPointCount"),
  infoCurrentLength: document.getElementById("infoCurrentLength"),
  infoEntityCount: document.getElementById("infoEntityCount"),
  infoPolygonCount: document.getElementById("infoPolygonCount"),
  infoTotalLength: document.getElementById("infoTotalLength"),
  infoTotalArea: document.getElementById("infoTotalArea"),
  layerSummaryList: document.getElementById("layerSummaryList"),
  modeButtons: Array.from(document.querySelectorAll(".mode-switch-button")),
  mapTypeButtons: Array.from(document.querySelectorAll(".map-type-button")),
  mapActionButtons: Array.from(document.querySelectorAll(".map-action-button")),
  layerButtons: Array.from(document.querySelectorAll(".layer-button")),
};

function setStatus(message, isError = false) {
  els.statusMessage.textContent = message;
  els.statusMessage.style.color = isError ? "#b91c1c" : "#374151";
}

function updateDragCursor() {
  const isDragging = state.drag.active && state.drag.moved && state.mode === "PAN";
  els.mapStage.classList.toggle("is-dragging", isDragging);
}

function beginMapUpdate() {
  if (state.mapUpdateInFlight) {
    return false;
  }
  state.mapUpdateInFlight = true;
  return true;
}

function endMapUpdate() {
  state.mapUpdateInFlight = false;
}

function setTextIfPresent(element, value) {
  if (element) {
    element.textContent = value;
  }
}

function getLayerStyle(layerName) {
  return layerStyles[layerName] || layerStyles.ROAD;
}

function getModeLabel() {
  if (state.mode === "PAN") {
    return "移動モード";
  }
  if (state.mode === "CENTER_SELECT") {
    return "中心指定モード";
  }
  return "作図モード";
}

function refreshModeButtons() {
  els.modeButtons.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.mode === state.mode);
  });
}

function getEntityLength(entity) {
  const storedLength = Number(entity.length);
  if (Number.isFinite(storedLength)) {
    return storedLength;
  }
  return calculatePolylineLength(entity.points, false);
}

function getEntityArea(entity) {
  const storedArea = Number(entity.area);
  return Number.isFinite(storedArea) ? storedArea : 0;
}

function calculateLayerSummaries() {
  const summaries = {};

  Object.keys(layerStyles).forEach((layerName) => {
    summaries[layerName] = {
      label: getLayerStyle(layerName).label,
      length: 0,
      area: 0,
    };
  });

  state.entities.forEach((entity) => {
    const layerName = layerStyles[entity.layer] ? entity.layer : "ROAD";
    if (entity.type === "polyline" && entity.closed !== true) {
      summaries[layerName].length += getEntityLength(entity);
      return;
    }
    if (entity.closed === true) {
      summaries[layerName].area += getEntityArea(entity);
    }
  });

  return summaries;
}

function renderLayerSummaries() {
  const summaries = calculateLayerSummaries();
  els.layerSummaryList.innerHTML = Object.entries(summaries)
    .map(([layerName, summary]) => {
      const isActive = layerName === state.currentLayer;
      return `
        <div class="layer-summary-item${isActive ? " is-active" : ""}">
          <div class="layer-summary-head">
            <span>${summary.label}</span>
          </div>
          <div class="layer-summary-values">
            <span>延長: ${formatMeterValue(summary.length)}</span>
            <span>面積: ${summary.area.toFixed(2)} ㎡</span>
          </div>
        </div>
      `;
    })
    .join("");
}

function refreshInfoPanel() {
  const appState = state.app;
  const modeLabel = getModeLabel();
  setTextIfPresent(els.infoMode, modeLabel);
  setTextIfPresent(els.mapModeBadge, modeLabel);
  if (els.mapModeBadge) {
    els.mapModeBadge.classList.toggle("is-center-select", state.mode === "CENTER_SELECT");
  }
  setTextIfPresent(els.infoAddress, appState.address || "-");
  setTextIfPresent(els.infoDisplayName, appState.display_name || "-");
  setTextIfPresent(
    els.infoLatitude,
    typeof appState.latitude === "number" ? appState.latitude.toFixed(6) : "-"
  );
  setTextIfPresent(
    els.infoLongitude,
    typeof appState.longitude === "number" ? appState.longitude.toFixed(6) : "-"
  );
  setTextIfPresent(els.infoTileType, appState.tile_type_label || appState.tile_type || "-");
  setTextIfPresent(els.infoZoom, appState.zoom ?? "-");
  setTextIfPresent(els.infoCurrentLayer, getLayerStyle(state.currentLayer).label);
  setTextIfPresent(els.infoApproxScale, getApproxScaleText());
  setTextIfPresent(els.infoOutputDir, appState.output_dir || "-");
  setTextIfPresent(els.infoCurrentPointCount, String(state.currentPoints.length));
  setTextIfPresent(els.infoCurrentLength, formatMeterValue(calculatePolylineLength(state.currentPoints)));
  setTextIfPresent(els.infoEntityCount, String(state.entities.length));
  setTextIfPresent(
    els.infoPolygonCount,
    String(state.entities.filter((entity) => entity.closed === true).length)
  );
  setTextIfPresent(els.infoTotalLength, formatMeterValue(calculateTotalLength()));
  setTextIfPresent(els.infoTotalArea, `${calculateTotalArea().toFixed(2)} ㎡`);
  renderLayerSummaries();
}

function formatMeterValue(value) {
  const numericValue = Number(value);
  return `${(Number.isFinite(numericValue) ? numericValue : 0).toFixed(2)} m`;
}

function calculatePolylineLength(points, closed = false) {
  const metersPerPixel = getMetersPerImagePixel();
  if (!metersPerPixel || !Array.isArray(points) || points.length < 2) {
    return 0;
  }

  let totalPixels = 0;
  for (let index = 1; index < points.length; index += 1) {
    const dx = Number(points[index].x) - Number(points[index - 1].x);
    const dy = Number(points[index].y) - Number(points[index - 1].y);
    totalPixels += Math.hypot(dx, dy);
  }

  if (closed && points.length >= 3) {
    const firstPoint = points[0];
    const lastPoint = points[points.length - 1];
    totalPixels += Math.hypot(Number(firstPoint.x) - Number(lastPoint.x), Number(firstPoint.y) - Number(lastPoint.y));
  }

  return totalPixels * metersPerPixel;
}

function calculateTotalLength() {
  return state.entities.reduce((total, entity) => {
    if (entity.type !== "polyline" || entity.closed === true) {
      return total;
    }
    return total + getEntityLength(entity);
  }, 0);
}

function calculateTotalArea() {
  return state.entities.reduce((total, entity) => {
    if (entity.closed !== true) {
      return total;
    }
    return total + getEntityArea(entity);
  }, 0);
}

function getMetersPerImagePixel() {
  const value = Number(state.app.meters_per_pixel);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function getMetersPerDisplayPixel() {
  const metersPerImagePixel = getMetersPerImagePixel();
  const rect = els.mapImage.getBoundingClientRect();
  const naturalWidth = els.mapImage.naturalWidth;

  if (!metersPerImagePixel || !rect.width || !naturalWidth) {
    return null;
  }

  return metersPerImagePixel * (naturalWidth / rect.width);
}

function chooseScaleBarMeters(targetMeters) {
  const candidates = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000];
  return candidates.reduce((best, candidate) => {
    return Math.abs(candidate - targetMeters) < Math.abs(best - targetMeters) ? candidate : best;
  }, candidates[0]);
}

function formatDistanceLabel(meters) {
  if (meters >= 1000) {
    return `${meters / 1000}km`;
  }
  return `${meters}m`;
}

function roundScaleDenominator(value) {
  if (!Number.isFinite(value) || value <= 0) {
    return null;
  }
  if (value < 1000) {
    return Math.max(1, Math.round(value / 50) * 50);
  }
  if (value < 5000) {
    return Math.round(value / 100) * 100;
  }
  return Math.round(value / 500) * 500;
}

function getApproxScaleDenominator() {
  const metersPerDisplayPixel = getMetersPerDisplayPixel();
  if (!metersPerDisplayPixel) {
    return null;
  }

  const cssPixelMm = 25.4 / 96;
  return roundScaleDenominator((metersPerDisplayPixel * 1000) / cssPixelMm);
}

function getApproxScaleText() {
  const denominator = getApproxScaleDenominator();
  return denominator ? `1/${denominator}` : "-";
}

function updateScaleBar() {
  const metersPerDisplayPixel = getMetersPerDisplayPixel();
  if (!metersPerDisplayPixel) {
    els.scaleBar.hidden = true;
    return;
  }

  const targetWidthPx = 120;
  const distanceMeters = chooseScaleBarMeters(targetWidthPx * metersPerDisplayPixel);
  const barWidthPx = Math.max(48, Math.min(180, distanceMeters / metersPerDisplayPixel));

  els.scaleBar.hidden = false;
  els.scaleBarLine.style.width = `${barWidthPx}px`;
  els.scaleBarLabel.textContent = formatDistanceLabel(distanceMeters);
}

function refreshMapTypeButtons() {
  els.mapTypeButtons.forEach((button) => {
    const isActive = button.dataset.tileType === state.app.tile_type;
    button.classList.toggle("is-active", isActive);
  });
}

function refreshLayerButtons() {
  els.layerButtons.forEach((button) => {
    const isActive = button.dataset.layer === state.currentLayer;
    button.classList.toggle("is-active", isActive);
  });
}

function refreshModeUi() {
  els.mapStage.classList.toggle("is-draw-mode", state.mode === "DRAW");
  els.mapStage.classList.toggle("is-pan-mode", state.mode === "PAN");
  els.mapStage.classList.toggle("is-center-select", state.mode === "CENTER_SELECT");
  refreshModeButtons();
  updateDragCursor();
  refreshInfoPanel();
}

function updateMapImage() {
  const cacheBust = `t=${Date.now()}`;
  els.mapImage.src = `${state.app.image_url}?${cacheBust}`;
}

function hasDrawingData() {
  return state.currentPoints.length > 0 || state.entities.length > 0;
}

function confirmMapChangeClearsDrawing() {
  if (!hasDrawingData()) {
    return true;
  }
  return window.confirm("地図を移動すると現在の作図はクリアされます。続行しますか？");
}

function clearDrawingData() {
  state.currentPoints = [];
  state.entities = [];
}

function applyMapStateAfterRefetch(nextState, shouldClearDrawing = false) {
  state.app = nextState;
  if (shouldClearDrawing) {
    clearDrawingData();
  }
  updateMapImage();
  refreshInfoPanel();
  refreshMapTypeButtons();
  refreshModeUi();
  drawPoints();
}

function syncOverlaySize() {
  els.drawOverlay.setAttribute("viewBox", `0 0 ${els.mapImage.width} ${els.mapImage.height}`);
  els.drawOverlay.style.width = `${els.mapImage.width}px`;
  els.drawOverlay.style.height = `${els.mapImage.height}px`;
}

function drawPoints() {
  syncOverlaySize();
  updateScaleBar();

  const entityMarkup = state.entities
    .map((entity) => {
      const points = entity.points.map((point) => `${point.x},${point.y}`).join(" ");
      const isClosed = entity.closed === true;
      const layerStyle = getLayerStyle(entity.layer);
      const entityPoints = entity.points
        .map(
          (point) =>
            `<circle cx="${point.x}" cy="${point.y}" r="3.5" fill="${layerStyle.color}" stroke="#ffffff" stroke-width="1.5"></circle>`
        )
        .join("");
      const shapeMarkup = isClosed
        ? `<polygon
            points="${points}"
            fill="${layerStyle.fill}"
            stroke="${layerStyle.color}"
            stroke-width="3"
            stroke-linejoin="round"
            opacity="0.95"
          ></polygon>`
        : `<polyline
            points="${points}"
            fill="none"
            stroke="${layerStyle.color}"
            stroke-width="3"
            stroke-linecap="round"
            stroke-linejoin="round"
            opacity="0.95"
          ></polyline>`;
      return `
        ${shapeMarkup}
        ${entityPoints}
      `;
    })
    .join("");

  const currentPolyline = state.currentPoints.map((point) => `${point.x},${point.y}`).join(" ");
  const currentLayerStyle = getLayerStyle(state.currentLayer);
  const currentCircles = state.currentPoints
    .map(
      (point) =>
        `<circle cx="${point.x}" cy="${point.y}" r="5" fill="${currentLayerStyle.color}" stroke="#ffffff" stroke-width="2"></circle>`
    )
    .join("");

  els.drawOverlay.innerHTML = `
    ${entityMarkup}
    <polyline
      points="${currentPolyline}"
      fill="none"
      stroke="${currentLayerStyle.color}"
      stroke-width="3"
      stroke-linecap="round"
      stroke-linejoin="round"
      stroke-dasharray="8 6"
    ></polyline>
    ${currentCircles}
  `;

  refreshInfoPanel();
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  return response.json();
}

async function getJson(url) {
  const response = await fetch(url, { method: "GET" });
  return response.json();
}

async function searchAddress() {
  const address = els.addressInput.value.trim();
  if (!address) {
    setStatus("住所を入力してください", true);
    return;
  }

  setStatus("住所検索と地図取得を実行しています...");
  const data = await postJson("/api/address-search", { address });
  if (!data.ok) {
    setStatus(data.error || "住所検索に失敗しました", true);
    return;
  }

  state.app = data.state;
  updateMapImage();
  refreshInfoPanel();
  refreshMapTypeButtons();
  setStatus(data.message || "住所検索に成功しました");
}

async function changeMapType(tileType) {
  setStatus("地図を切り替えています...");
  const data = await postJson("/api/map-type", { tile_type: tileType });
  if (!data.ok) {
    setStatus(data.error || "地図切替に失敗しました", true);
    return;
  }

  state.app = data.state;
  updateMapImage();
  refreshInfoPanel();
  refreshMapTypeButtons();
  setStatus(data.message || "地図を更新しました");
}

async function fetchMap() {
  setStatus("地図を取得しています...");
  const data = await postJson("/api/fetch-map", {});
  if (!data.ok) {
    setStatus(data.error || "地図取得に失敗しました", true);
    return;
  }

  state.app = data.state;
  updateMapImage();
  refreshInfoPanel();
  refreshMapTypeButtons();
  setStatus(data.message || "地図を取得しました");
}

async function adjustMapView(action) {
  if (!beginMapUpdate()) {
    return;
  }

  const shouldClearDrawing = hasDrawingData();
  if (!confirmMapChangeClearsDrawing()) {
    endMapUpdate();
    setStatus("地図操作をキャンセルしました");
    return;
  }

  setStatus("地図を更新しています...");
  try {
    const data = await postJson("/api/adjust-view", { action });
    if (!data.ok) {
      setStatus(data.error || "地図操作に失敗しました", true);
      return;
    }

    applyMapStateAfterRefetch(data.state, shouldClearDrawing);
    setStatus(data.message || "地図を更新しました");
  } finally {
    endMapUpdate();
  }
}

async function panMapByPixels(deltaX, deltaY) {
  if (!beginMapUpdate()) {
    return false;
  }

  const shouldClearDrawing = hasDrawingData();
  if (!confirmMapChangeClearsDrawing()) {
    endMapUpdate();
    setStatus("地図移動をキャンセルしました");
    return false;
  }

  setStatus("地図をドラッグ移動しています...");
  try {
    const data = await postJson("/api/pan-by-pixels", {
      delta_x: deltaX,
      delta_y: deltaY,
      image_width: els.mapImage.naturalWidth,
      image_height: els.mapImage.naturalHeight,
    });

    if (!data.ok) {
      setStatus(data.error || "地図移動に失敗しました", true);
      alert(`地図移動に失敗しました: ${data.error || "unknown error"}`);
      return false;
    }

    applyMapStateAfterRefetch(data.state, shouldClearDrawing);
    setStatus(data.message || "地図を移動しました");
    return true;
  } finally {
    endMapUpdate();
  }
}

async function setCenterFromClick(pixelX, pixelY) {
  const shouldClearDrawing = hasDrawingData();
  if (!confirmMapChangeClearsDrawing()) {
    refreshModeUi();
    setStatus("中心指定をキャンセルしました");
    return false;
  }

  setStatus("中心位置を更新しています...");
  const data = await postJson("/api/set-center", {
    pixel_x: pixelX,
    pixel_y: pixelY,
  });

  if (!data.ok) {
    setStatus(data.error || "中心位置の更新に失敗しました", true);
    return false;
  }

  applyMapStateAfterRefetch(data.state, shouldClearDrawing);
  setStatus(data.message || "中心位置を更新しました");
  return true;
}

async function saveDxf() {
  if (state.entities.length === 0) {
    alert("DXF保存には確定済みの線が必要です");
    return;
  }

  const data = await postJson("/api/export-dxf", {
    entities: state.entities,
    image_width: els.mapImage.naturalWidth,
    image_height: els.mapImage.naturalHeight,
    approximate_scale_denominator: getApproxScaleDenominator(),
  });

  if (!data.success) {
    alert(`DXF保存に失敗しました: ${data.error || "unknown error"}`);
    return;
  }

  alert(`DXF保存完了\n${data.path}`);
  setStatus("DXF保存が完了しました");
}

async function saveProject() {
  setStatus("project.json を保存しています...");
  const data = await postJson("/api/save-project", {
    entities: state.entities,
    currentPoints: state.currentPoints,
    currentLayer: state.currentLayer,
    app: state.app,
  });

  if (!data.ok) {
    alert(`作業保存に失敗しました: ${data.error || "unknown error"}`);
    setStatus(data.error || "作業保存に失敗しました", true);
    return;
  }

  setStatus("作業を保存しました");
  alert(`作業保存完了\n${data.path}`);
}

async function loadProject() {
  setStatus("project.json を読み込んでいます...");
  const data = await getJson("/api/load-project");

  if (!data.ok) {
    alert(`作業読込に失敗しました: ${data.error || "unknown error"}`);
    setStatus(data.error || "作業読込に失敗しました", true);
    return;
  }

  const project = data.project || {};
  state.entities = Array.isArray(project.entities) ? project.entities : [];
  state.currentPoints = Array.isArray(project.currentPoints) ? project.currentPoints : [];
  state.currentLayer = layerStyles[project.currentLayer] ? project.currentLayer : "ROAD";

  if (project.webMapState && typeof project.webMapState === "object" && !data.warning) {
    state.app = { ...state.app, ...project.webMapState };
    if (typeof state.app.address === "string") {
      els.addressInput.value = state.app.address;
    }
  }

  refreshLayerButtons();
  refreshMapTypeButtons();
  refreshModeUi();
  drawPoints();

  if (data.warning) {
    setStatus(`作業を読み込みました: ${data.warning}`, true);
    alert(`作業読込完了\n${data.warning}`);
    return;
  }

  setStatus("作業を読み込みました");
}

async function calculateLineLength(points) {
  const data = await postJson("/api/calculate-length", { points });
  if (!data.success) {
    throw new Error(data.error || "延長計算に失敗しました");
  }
  return Number(data.length) || 0;
}

async function finishCurrentLine() {
  if (state.currentPoints.length < 2) {
    alert("線確定には2点以上必要です");
    return;
  }

  let length = 0;
  try {
    length = await calculateLineLength(state.currentPoints);
  } catch (error) {
    alert(`延長計算に失敗しました: ${error.message}`);
    return;
  }

  state.entities.push({
    type: "polyline",
    layer: state.currentLayer,
    closed: false,
    length,
    points: state.currentPoints.map((point) => ({ x: point.x, y: point.y })),
  });
  state.currentPoints = [];
  drawPoints();
  setStatus(`線を確定しました: ${length.toFixed(2)} m`);
}

async function calculatePolygonArea(points) {
  const data = await postJson("/api/calculate-area", { points });
  if (!data.success) {
    throw new Error(data.error || "面積計算に失敗しました");
  }
  return Number(data.area) || 0;
}

async function finishCurrentPolygon() {
  if (state.currentPoints.length < 3) {
    alert("閉面確定には3点以上必要です");
    return;
  }

  let area = 0;
  try {
    area = await calculatePolygonArea(state.currentPoints);
  } catch (error) {
    alert(`面積計算に失敗しました: ${error.message}`);
    return;
  }

  state.entities.push({
    type: "polygon",
    layer: state.currentLayer,
    closed: true,
    area,
    points: state.currentPoints.map((point) => ({ x: point.x, y: point.y })),
  });
  state.currentPoints = [];
  drawPoints();
  setStatus(`閉面を確定しました: ${area.toFixed(2)} ㎡`);
}

function addPointFromClick(event) {
  if (state.drag.suppressClick) {
    state.drag.suppressClick = false;
    return;
  }

  const rect = els.mapImage.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  if (x < 0 || y < 0 || x > rect.width || y > rect.height) {
    return;
  }

  const scaleX = els.mapImage.naturalWidth / rect.width;
  const scaleY = els.mapImage.naturalHeight / rect.height;

  if (state.mode === "CENTER_SELECT") {
    setCenterFromClick(x * scaleX, y * scaleY);
    return;
  }

  if (state.mode !== "DRAW") {
    return;
  }

  state.currentPoints.push({
    x: x * scaleX,
    y: y * scaleY,
  });
  drawPoints();
  setStatus(`点を追加しました: ${state.currentPoints.length}点`);
}

function handleMapMouseDown(event) {
  if (event.button !== 0 || state.mode !== "PAN") {
    return;
  }

  event.preventDefault();
  state.drag.active = true;
  state.drag.moved = false;
  state.drag.startClientX = event.clientX;
  state.drag.startClientY = event.clientY;
  state.drag.lastClientX = event.clientX;
  state.drag.lastClientY = event.clientY;
  updateDragCursor();
}

function handleMapMouseMove(event) {
  if (!state.drag.active || state.mode !== "PAN") {
    return;
  }

  event.preventDefault();
  state.drag.lastClientX = event.clientX;
  state.drag.lastClientY = event.clientY;
  const movedX = event.clientX - state.drag.startClientX;
  const movedY = event.clientY - state.drag.startClientY;
  if (Math.hypot(movedX, movedY) >= 5) {
    state.drag.moved = true;
    updateDragCursor();
  }
}

async function handleMapMouseUp(event) {
  if (!state.drag.active) {
    return;
  }

  event.preventDefault();
  const deltaX = event.clientX - state.drag.startClientX;
  const deltaY = event.clientY - state.drag.startClientY;
  const shouldPan = state.drag.moved && state.mode === "PAN";

  state.drag.active = false;
  state.drag.moved = false;
  updateDragCursor();

  if (!shouldPan) {
    return;
  }

  state.drag.suppressClick = true;
  await panMapByPixels(deltaX, deltaY);
}

function handleMapWheel(event) {
  if (state.mode === "CENTER_SELECT") {
    return;
  }

  if (!event.ctrlKey && !event.metaKey) {
    return;
  }

  event.preventDefault();
  const now = Date.now();
  if (state.mapUpdateInFlight || now - state.lastWheelZoomAt < 140) {
    return;
  }
  state.lastWheelZoomAt = now;

  if (event.deltaY < 0) {
    adjustMapView("zoom_in");
    return;
  }
  if (event.deltaY > 0) {
    adjustMapView("zoom_out");
  }
}

function preventNativeImageDrag(event) {
  event.preventDefault();
}

function clearPoints() {
  state.currentPoints = [];
  state.entities = [];
  drawPoints();
  setStatus("作図をクリアしました");
}

function undoDrawing() {
  if (state.currentPoints.length > 0) {
    state.currentPoints.pop();
    drawPoints();
    setStatus(`最後の点を戻しました: ${state.currentPoints.length}点`);
    return;
  }

  if (state.entities.length > 0) {
    state.entities.pop();
    drawPoints();
    setStatus(`最後の図形を戻しました: ${state.entities.length}図形`);
  }
}

function selectLayer(layerName) {
  if (!layerStyles[layerName]) {
    return;
  }

  state.currentLayer = layerName;
  refreshLayerButtons();
  drawPoints();
  setStatus(`レイヤを切り替えました: ${getLayerStyle(layerName).label}`);
}

function enterCenterSelectMode() {
  state.mode = "CENTER_SELECT";
  refreshModeUi();
  setStatus("中心にしたい位置をクリックしてください");
}

function cancelCenterSelectMode() {
  if (state.mode !== "CENTER_SELECT") {
    return;
  }
  setStatus("モードは維持されています。作図または移動を選ぶと切り替わります");
}

function changeMode(mode) {
  if (!["DRAW", "PAN", "CENTER_SELECT"].includes(mode)) {
    return;
  }

  state.mode = mode;
  state.drag.active = false;
  state.drag.moved = false;
  updateDragCursor();
  refreshModeUi();

  if (mode === "CENTER_SELECT") {
    setStatus("中心にしたい位置をクリックしてください");
    return;
  }

  if (mode === "PAN") {
    setStatus("移動モードです。ドラッグで地図を動かせます");
    return;
  }

  setStatus("作図モードです。クリックで点を追加できます");
}

els.searchButton.addEventListener("click", searchAddress);
els.finishLineButton.addEventListener("click", finishCurrentLine);
els.finishPolygonButton.addEventListener("click", finishCurrentPolygon);
els.undoButton.addEventListener("click", undoDrawing);
els.saveDxfButton.addEventListener("click", saveDxf);
els.saveProjectButton.addEventListener("click", saveProject);
els.loadProjectButton.addEventListener("click", loadProject);
els.fetchButton.addEventListener("click", fetchMap);
els.clearPointsButton.addEventListener("click", clearPoints);
els.addressInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    searchAddress();
  }
});
els.mapTypeButtons.forEach((button) => {
  button.addEventListener("click", () => changeMapType(button.dataset.tileType));
});
els.mapActionButtons.forEach((button) => {
  button.addEventListener("click", () => adjustMapView(button.dataset.mapAction));
});
els.layerButtons.forEach((button) => {
  button.addEventListener("click", () => selectLayer(button.dataset.layer));
});
els.modeButtons.forEach((button) => {
  button.addEventListener("click", () => changeMode(button.dataset.mode));
});
els.mapCanvas.addEventListener("mousedown", handleMapMouseDown);
window.addEventListener("mousemove", handleMapMouseMove);
window.addEventListener("mouseup", handleMapMouseUp);
els.mapCanvas.addEventListener("click", addPointFromClick);
els.mapImage.addEventListener("dragstart", preventNativeImageDrag);
els.mapStage.addEventListener("wheel", handleMapWheel, { passive: false });
els.mapImage.addEventListener("load", drawPoints);
window.addEventListener("resize", drawPoints);

refreshInfoPanel();
refreshMapTypeButtons();
refreshLayerButtons();
refreshModeUi();
drawPoints();
