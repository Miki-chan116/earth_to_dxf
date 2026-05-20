const state = {
  app: window.__INITIAL_STATE__ || {},
  points: [],
  mode: "DRAW",
};

const els = {
  addressInput: document.getElementById("addressInput"),
  searchButton: document.getElementById("searchButton"),
  centerSelectButton: document.getElementById("centerSelectButton"),
  fetchButton: document.getElementById("fetchButton"),
  clearPointsButton: document.getElementById("clearPointsButton"),
  mapImage: document.getElementById("mapImage"),
  mapStage: document.getElementById("mapStage"),
  drawOverlay: document.getElementById("drawOverlay"),
  statusMessage: document.getElementById("statusMessage"),
  infoAddress: document.getElementById("infoAddress"),
  infoDisplayName: document.getElementById("infoDisplayName"),
  infoMode: document.getElementById("infoMode"),
  infoLatitude: document.getElementById("infoLatitude"),
  infoLongitude: document.getElementById("infoLongitude"),
  infoTileType: document.getElementById("infoTileType"),
  infoZoom: document.getElementById("infoZoom"),
  infoOutputDir: document.getElementById("infoOutputDir"),
  infoPointCount: document.getElementById("infoPointCount"),
  mapTypeButtons: Array.from(document.querySelectorAll(".map-type-button")),
};

function setStatus(message, isError = false) {
  els.statusMessage.textContent = message;
  els.statusMessage.style.color = isError ? "#b91c1c" : "#374151";
}

function refreshInfoPanel() {
  const appState = state.app;
  els.infoMode.textContent = state.mode;
  els.infoAddress.textContent = appState.address || "-";
  els.infoDisplayName.textContent = appState.display_name || "-";
  els.infoLatitude.textContent = typeof appState.latitude === "number" ? appState.latitude.toFixed(6) : "-";
  els.infoLongitude.textContent = typeof appState.longitude === "number" ? appState.longitude.toFixed(6) : "-";
  els.infoTileType.textContent = appState.tile_type_label || appState.tile_type || "-";
  els.infoZoom.textContent = appState.zoom ?? "-";
  els.infoOutputDir.textContent = appState.output_dir || "-";
  els.infoPointCount.textContent = String(state.points.length);
}

function refreshMapTypeButtons() {
  els.mapTypeButtons.forEach((button) => {
    const isActive = button.dataset.tileType === state.app.tile_type;
    button.classList.toggle("is-active", isActive);
  });
}

function refreshModeUi() {
  const centerSelecting = state.mode === "CENTER_SELECT";
  els.centerSelectButton.classList.toggle("is-mode-active", centerSelecting);
  refreshInfoPanel();
}

function updateMapImage() {
  const cacheBust = `t=${Date.now()}`;
  els.mapImage.src = `${state.app.image_url}?${cacheBust}`;
}

function syncOverlaySize() {
  els.drawOverlay.setAttribute("viewBox", `0 0 ${els.mapImage.width} ${els.mapImage.height}`);
  els.drawOverlay.style.width = `${els.mapImage.width}px`;
  els.drawOverlay.style.height = `${els.mapImage.height}px`;
}

function drawPoints() {
  syncOverlaySize();

  const polyline = state.points.map((point) => `${point.x},${point.y}`).join(" ");
  const circles = state.points
    .map(
      (point) =>
        `<circle cx="${point.x}" cy="${point.y}" r="5" fill="#ef4444" stroke="#ffffff" stroke-width="2"></circle>`
    )
    .join("");

  els.drawOverlay.innerHTML = `
    <polyline
      points="${polyline}"
      fill="none"
      stroke="#2563eb"
      stroke-width="3"
      stroke-linecap="round"
      stroke-linejoin="round"
    ></polyline>
    ${circles}
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

async function setCenterFromClick(pixelX, pixelY) {
  setStatus("中心位置を更新しています...");
  const data = await postJson("/api/set-center", {
    pixel_x: pixelX,
    pixel_y: pixelY,
  });

  if (!data.ok) {
    setStatus(data.error || "中心位置の更新に失敗しました", true);
    return false;
  }

  state.app = data.state;
  state.mode = "DRAW";
  updateMapImage();
  refreshInfoPanel();
  refreshMapTypeButtons();
  refreshModeUi();
  setStatus(data.message || "中心位置を更新しました");
  return true;
}

function addPointFromClick(event) {
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

  state.points.push({
    x: x * scaleX,
    y: y * scaleY,
  });
  drawPoints();
  setStatus(`点を追加しました: ${state.points.length}点`);
}

function clearPoints() {
  state.points = [];
  drawPoints();
  setStatus("点をクリアしました");
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
  state.mode = "DRAW";
  refreshModeUi();
  setStatus("中心指定をキャンセルしました");
}

els.searchButton.addEventListener("click", searchAddress);
els.centerSelectButton.addEventListener("click", () => {
  if (state.mode === "CENTER_SELECT") {
    cancelCenterSelectMode();
    return;
  }
  enterCenterSelectMode();
});
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
els.mapImage.addEventListener("click", addPointFromClick);
els.mapImage.addEventListener("load", drawPoints);
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    cancelCenterSelectMode();
  }
});
window.addEventListener("resize", drawPoints);

refreshInfoPanel();
refreshMapTypeButtons();
refreshModeUi();
drawPoints();
