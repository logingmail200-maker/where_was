const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
let map, selectedFile, previewUrl, lastResult, activeCandidate, activePopup;
let mapLayers = [];
let navigationMarker, navigationResults = [], navigationTimer;
let streetInteractionEnabled = false;
let activeSearch = null, progressValue = 8;

const MAP_STYLES = {
  dark: "https://tiles.openfreemap.org/styles/dark",
  satellite: {
    version: 8,
    sources: { satellite: { type: "raster", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], tileSize: 256, attribution: "Esri World Imagery" } },
    layers: [{ id: "satellite", type: "raster", source: "satellite" }]
  }
};

const STEP = {
  read: ["Preparing the image", 8],
  search: ["Searching places", 64],
  rank: ["Ranking matches", 94],
};

function initMap() {
  map = new maplibregl.Map({
    container: "map", style: MAP_STYLES.dark,
    center: [-122.34, 37.79], zoom: 10.8, pitch: 56, bearing: -13,
    attributionControl: true, antialias: true
  });
  map.on("style.load", () => {
    const labels = map.getStyle().layers.find((layer) => layer.type === "symbol")?.id;
    try {
      map.addLayer({
        id: "wherewas-3d-buildings", source: "openmaptiles", "source-layer": "building",
        type: "fill-extrusion", minzoom: 13.5,
        paint: {
          "fill-extrusion-color": "#bfc3c0",
          "fill-extrusion-height": ["coalesce", ["get", "render_height"], ["get", "height"], 8],
          "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
          "fill-extrusion-opacity": .42
        }
      }, labels);
    } catch (_) {}
  });
}

function size(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function esc(value) {
  return String(value || "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function radiusLabel(value) {
  const radius = Number(value);
  return radius > 0 ? (radius >= 1000 ? `${(radius / 1000).toFixed(radius < 10000 ? 1 : 0)} km` : `${Math.round(radius)} m`) : "Unknown";
}

function chooseImage() {
  $("#upload-panel").hidden = false;
  $("#upload-panel").classList.remove("leaving");
}

function browseImage() { $("#file").click(); }

function toast(message) {
  const node = $("#workspace-toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, 2200);
}

function loadFile(file) {
  if (!file) return;
  if (activeSearch || lastResult) resetSearch();
  selectedFile = file;
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(file);
  ["#dock-image", "#upload-preview", "#compare-image"].forEach((id) => $(id).src = previewUrl);
  $("#dock-name").textContent = file.name;
  $("#dock-size").textContent = size(file.size);
  $("#upload-name").textContent = file.name;
  $("#upload-size").textContent = size(file.size);
  $("#compare-file").textContent = file.name.toUpperCase();
  $("#upload-empty").hidden = true;
  $("#upload-ready").hidden = false;
  $("#upload-search").disabled = false;
  $("#upload-panel").hidden = false;
  $("#source-dock").hidden = true;
  $("#start-search").hidden = true;
}

function resetSearch() {
  activeSearch?.abort();
  activeSearch = null;
  activeCandidate = lastResult = null;
  document.body.classList.remove("busy", "result-mode");
  $("#result-ui").hidden = true;
  $("#search-card").hidden = true;
  $("#compare-drawer").hidden = true;
  $("#street-view").hidden = true;
  $("#street-frame").src = "about:blank";
  $("#source-dock").hidden = !selectedFile;
  $("#start-search").hidden = !!selectedFile;
  clearLayers();
  map.flyTo({ center: [-122.34, 37.79], zoom: 10.8, pitch: 56, bearing: -13, duration: 1200 });
}

function clearFile() {
  selectedFile = null;
  resetSearch();
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
  ["#dock-image", "#upload-preview", "#compare-image"].forEach((id) => $(id).removeAttribute("src"));
  $("#file").value = "";
  $("#source-dock").hidden = true;
  $("#upload-empty").hidden = false;
  $("#upload-ready").hidden = true;
  $("#upload-search").disabled = true;
  $("#upload-panel").hidden = true;
  $("#start-search").hidden = true;
}

function clearLayers() {
  mapLayers.forEach((layer) => { try { layer.remove(); } catch {} });
  mapLayers = [];
  if (activePopup) { activePopup.remove(); activePopup = null; }
}

function drawCandidates(candidates, selectedId, close = false) {
  clearLayers();
  const bounds = [];
  candidates.forEach((candidate, index) => {
    const on = candidate.id === selectedId;
    const element = document.createElement("div");
    element.className = "num-pin";
    element.innerHTML = `<span class="${on ? "on" : ""}">${index + 1}</span>`;
    element.addEventListener("click", () => selectCandidate(candidate));
    const marker = new maplibregl.Marker({ element }).setLngLat([candidate.lon, candidate.lat]).addTo(map);
    mapLayers.push(marker);
    bounds.push([candidate.lon, candidate.lat]);
  });
  if (!bounds.length) return;
  if (close || bounds.length === 1) map.flyTo({ center: (() => {const c=candidates.find(c=>c.id===selectedId);return c?[c.lon,c.lat]:bounds[0];})(), zoom: close ? 17 : 15, pitch: 58, bearing: -10, duration: 1500 });
  else {
    const area = bounds.reduce((box, point) => box.extend(point), new maplibregl.LngLatBounds(bounds[0], bounds[0]));
    const compact = window.innerWidth <= 640;
    const padding = compact
      ? { top: 64, bottom: Math.round(map.getContainer().clientHeight * 0.45), left: 28, right: 28 }
      : { top: 80, bottom: 80, left: 80, right: 340 };
    map.fitBounds(area, { padding, maxZoom: 14, duration: 1400 });
  }
}

function selectCandidate(candidate) {
  if (!candidate) return;
  activeCandidate = candidate;
  $("#map-external").hidden = false;
  const reference = $("#reference-image");
  let referenceUrl = null;
  try { const u = new URL(candidate.reference_image); if (u.protocol === "https:") referenceUrl = u.href; } catch {}
  reference.hidden = !referenceUrl;
  reference.removeAttribute("src");
  if (referenceUrl) reference.src = referenceUrl;
  reference.onerror = () => { reference.hidden = true; $("#reference-state").textContent = "Reference image unavailable"; };
  $("#reference-state").textContent = candidate.visual_verification === "geometric-support" ? "Geometric support found · review this reference" : "Not independently verified";
  $("#reference-credit").textContent = candidate.reference_attribution || "Nearby street imagery alone does not prove a match.";

  $("#result-name").textContent = candidate.name || "Location candidate";
  $("#result-address").textContent = candidate.address || "Mapped location";
  $("#result-tier").textContent = String(candidate.tier || "Place").replace(/^./, (c) => c.toUpperCase());
  $("#result-score").textContent = Number.isFinite(Number(candidate.score)) ? `${Math.min(100, Math.round(Number(candidate.score)))} / 100` : "Unscored";
  $("#result-radius").textContent = radiusLabel(candidate.radius_m);
  $("#open-street").disabled = ["city", "region"].includes(candidate.tier);
  $("#map-external").href = candidate.maps || `https://www.openstreetmap.org/?mlat=${encodeURIComponent(candidate.lat)}&mlon=${encodeURIComponent(candidate.lon)}#map=15/${encodeURIComponent(candidate.lat)}/${encodeURIComponent(candidate.lon)}`;
  $("#street-external").href = candidate.street_view || `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${encodeURIComponent(candidate.lat)},${encodeURIComponent(candidate.lon)}`;
  $("#street-external").hidden = ["city", "region"].includes(candidate.tier);
  $$(".candidate").forEach((node) => node.classList.toggle("on", node.dataset.id === String(candidate.id)));
  drawCandidates(lastResult?.candidates || [candidate], candidate.id, true);
  if (!$("#street-view").hidden) setView("street");
}

function setView(view) {
  if (view === "compare" && !activeCandidate) view = "map";
  if (view === "street" && (!activeCandidate || ["city", "region"].includes(activeCandidate.tier))) view = "map";
  $$(".view-toggle button").forEach((button) => button.classList.toggle("on", button.dataset.view === view));
  $("#compare-drawer").hidden = view !== "compare";
  $("#street-view").hidden = view !== "street";
  if (view !== "street") setStreetInteraction(false);
  if (!activeCandidate) return;
  if (view === "map") drawCandidates(lastResult?.candidates || [], activeCandidate.id, false);
  if (view === "street") showStreetContext();
  if (view === "compare") drawCandidates(lastResult?.candidates || [], activeCandidate.id, true);
}

function setStreetInteraction(enabled) {
  streetInteractionEnabled = enabled;
  $("#street-view").classList.toggle("is-interactive", enabled);
  $("#street-scroll-guard").hidden = enabled;
  const button = $("#street-interact");
  button.textContent = enabled ? "Done" : "Explore 360°";
  button.setAttribute("aria-pressed", String(enabled));
}

function showStreetContext() {
  if (!activeCandidate || ["city", "region"].includes(activeCandidate.tier)) return;
  drawCandidates(lastResult?.candidates || [activeCandidate], activeCandidate.id, true);
  map.flyTo({ center: [activeCandidate.lon, activeCandidate.lat], zoom: 18.2, pitch: 67, bearing: -18, duration: 1450 });
  const lat = Number(activeCandidate.lat), lon = Number(activeCandidate.lon);
  const streetUrl = `https://maps.google.com/maps?q=${encodeURIComponent(`${lat},${lon}`)}&layer=c&cbll=${encodeURIComponent(`${lat},${lon}`)}&output=svembed`;
  if ($("#street-frame").src !== streetUrl) $("#street-frame").src = streetUrl;
  $("#street-name").textContent = activeCandidate.name || "Selected place";
  $("#street-view").hidden = false;
  setStreetInteraction(false);
}

function renderPlaceResults(results) {
  navigationResults = results;
  const panel = $("#place-results");
  panel.innerHTML = results.length ? results.map((place, index) => `
    <button type="button" data-place="${index}"><b>${esc(place.name)}</b><small>${esc(place.address)}</small></button>`).join("") : `<div class="place-empty">No mapped places found</div>`;
  panel.hidden = false;
  panel.querySelectorAll("[data-place]").forEach((button) => button.addEventListener("click", () => navigateToPlace(navigationResults[Number(button.dataset.place)])));
}

async function searchPlaces(query) {
  try {
    const response = await fetch(`/api/geocode?q=${encodeURIComponent(query)}`);
    if (!response.ok) throw new Error();
    const data = await response.json();
    renderPlaceResults(data.results || []);
  } catch (_) {
    renderPlaceResults([]);
  }
}

function navigateToPlace(place) {
  if (!place) return;
  $("#navigate").value = place.name || place.address;
  $("#place-results").hidden = true;
  if (navigationMarker) navigationMarker.remove();
  navigationMarker = new maplibregl.Marker({ color: "#f5f6f2" }).setLngLat([place.lon, place.lat]).addTo(map);
  if (activePopup) activePopup.remove();
  activePopup = new maplibregl.Popup({ closeButton: false, offset: 25, maxWidth: "280px" })
    .setLngLat([place.lon, place.lat])
    .setHTML(`<div class="place-popup"><small>NAVIGATED PLACE</small><b>${esc(place.name)}</b><span>${esc(place.address)}</span></div>`)
    .addTo(map);
  map.flyTo({ center: [place.lon, place.lat], zoom: 15.6, pitch: 55, bearing: -10, duration: 1500 });
}

function setMapStyle(styleName) {
  const style = MAP_STYLES[styleName];
  if (!style) return;
  map.setStyle(style);
  $$(".map-modes button").forEach((button) => button.classList.toggle("on", button.dataset.style === styleName));
}

function updateStep(id, status) {
  if (status !== "run" && status !== "done") return;
  const [label, base] = STEP[id] || ["Searching visual terrain", 50];
  const percent = progressValue = Math.max(progressValue, Math.min(98, base + (status === "done" ? 4 : 0)));
  $("#search-detail").textContent = label;
  $("#search-progress").style.width = `${percent}%`;
  $("#search-percent").textContent = `${String(percent).padStart(2, "0")}%`;
}

async function runSearch() {
  if (activeSearch) return;
  if (!selectedFile) return chooseImage();
  const controller = new AbortController();
  activeSearch = controller;
  progressValue = 8;
  const timeout = setTimeout(() => controller.abort(), 60000);
  $("#upload-panel").hidden = true;
  $("#source-dock").hidden = false;
  $("#result-ui").hidden = true;
  $("#search-card").hidden = false;
  document.body.classList.remove("result-mode");
  document.body.classList.add("busy");
  $("#search-title").textContent = "Searching visual terrain…";
  $("#search-detail").textContent = "Preparing the image";
  $("#search-progress").style.width = "8%";
  $("#search-percent").textContent = "08%";
  map.flyTo({ center: [-122.34, 37.79], zoom: 11.8, pitch: 60, bearing: -13, duration: 1600 });

  const form = new FormData();
  form.append("file", selectedFile, selectedFile.name);
  try {
    const response = await fetch("/api/analyze", { method: "POST", body: form, signal: controller.signal });
    if (!response.ok) throw new Error(await response.text());
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "", receivedResult = false;
    function receive(line) {
      if (activeSearch !== controller || !line.trim()) return;
      const message = JSON.parse(line);
      if (message.type === "step") updateStep(message.id, message.status);
      if (message.type === "result") { receivedResult = true; showResult(message.data); }
      if (message.type === "error") throw new Error(message.message || "Search failed");
    }
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      lines.forEach(receive);
      if (done) { receive(buffer); break; }
    }
    if (!receivedResult) throw new Error("Search ended without a result. Please retry.");
  } catch (error) {
    if (activeSearch !== controller) return;
    document.body.classList.remove("busy");
    $("#search-title").textContent = "Search interrupted";
    $("#search-detail").textContent = error.name === "AbortError" ? "Search stopped. You can retry with another photo." : error.message || "Please try again";
    $("#search-progress").style.width = "0";
    $("#source-dock").hidden = false;
  } finally {
    clearTimeout(timeout);
    if (activeSearch === controller) activeSearch = null;
  }
}

$("#cancel-search").addEventListener("click", () => activeSearch?.abort());

function showResult(data) {
  lastResult = data;
  document.body.classList.remove("busy");
  document.body.classList.add("result-mode");
  $("#search-card").hidden = true;
  $("#source-dock").hidden = true;
  $("#result-ui").hidden = false;
  const candidates = data.candidates || [];
  $("#result-summary").textContent = data.summary || "Results are candidates to inspect, not a certain address.";
  $("#candidate-strip").innerHTML = candidates.slice(0, 10).map((candidate, index) => `
    <button type="button" class="candidate ${data.primary?.id === candidate.id ? "on" : ""}" data-id="${esc(candidate.id)}" aria-label="Inspect candidate ${index + 1}: ${esc(candidate.name || "Location candidate")}">
      <b>${index + 1}. ${esc(candidate.name || "Location candidate")}</b>
      <small>${esc(candidate.address || "Mapped candidate")}</small>
      <em>Inspect this candidate on the map</em>
      <span class="candidate-meta"><span>${esc(radiusLabel(candidate.radius_m))} radius</span><span>${esc(Math.round(Number(candidate.score) || 0))} ranking signal</span></span>
    </button>`).join("");
  $$(".candidate").forEach((node) => node.addEventListener("click", () => {
    const found = candidates.find((candidate) => String(candidate.id) === node.dataset.id);
    if (found) selectCandidate(found);
  }));
  const first = data.primary || candidates[0];
  if (first) selectCandidate(first);
  else {
    activeCandidate = null;
    $("#result-name").textContent = "Location not resolved";
    $("#result-address").textContent = "Try a clear place-only crop with distinctive fixed details.";
    $("#result-score").textContent = "—";
    $("#result-radius").textContent = "—";
    $("#result-tier").textContent = "Unresolved";
    $("#open-street").disabled = true;
    $("#map-external").hidden = true;
    $("#street-external").hidden = true;
    clearLayers();
  }
  setView("map");
}

$("#file").addEventListener("change", () => loadFile($("#file").files?.[0]));
[$("#upload-tool"), $("#start-search")].forEach((node) => node.addEventListener("click", chooseImage));
$("#remove-image").addEventListener("click", clearFile);
$("#upload-close").addEventListener("click", () => $("#upload-panel").hidden = true);
$("#upload-browse").addEventListener("click", browseImage);
$("#upload-change").addEventListener("click", browseImage);
$("#upload-search").addEventListener("click", runSearch);
$("#dock-search").addEventListener("click", chooseImage);
$("#back-results").addEventListener("click", resetSearch);
$("#panel-close").addEventListener("click", () => $(".result-panel").classList.toggle("collapsed"));
$("#compare-close").addEventListener("click", () => setView("map"));
$("#map-tool").addEventListener("click", () => activeCandidate ? setView("map") : map.flyTo({center:[-122.34,37.79],zoom:10.8,pitch:56,bearing:-13,duration:1200}));
$("#results-tool").addEventListener("click", () => {
  if (!lastResult) return toast("Run an image search to view candidates");
  $(".result-panel").classList.remove("collapsed");
  setView("map");
});
$("#compare-tool").addEventListener("click", () => lastResult ? setView("compare") : toast("Source comparison appears after a search"));
$("#navigate").addEventListener("input", (event) => {
  clearTimeout(navigationTimer);
  const query = event.target.value.trim();
  if (query.length < 2) { $("#place-results").hidden = true; return; }
  navigationTimer = setTimeout(() => searchPlaces(query), 320);
});
$("#navigate").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    if (navigationResults[0]) navigateToPlace(navigationResults[0]);
    else if (event.target.value.trim().length > 1) searchPlaces(event.target.value.trim());
  }
  if (event.key === "Escape") $("#place-results").hidden = true;
});
$("#navigate-clear").addEventListener("click", () => { $("#navigate").value = ""; $("#place-results").hidden = true; $("#navigate").focus(); });
$$(".map-modes button").forEach((button) => button.addEventListener("click", () => setMapStyle(button.dataset.style)));
$$(".view-toggle button").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
$("#open-street").addEventListener("click", () => setView("street"));
$("#show-all").addEventListener("click", () => setView("map"));
$("#street-close").addEventListener("click", () => setView("map"));
$("#street-interact").addEventListener("click", () => setStreetInteraction(!streetInteractionEnabled));
$("#street-scroll-guard").addEventListener("click", () => setStreetInteraction(true));
$("#confirm-result").addEventListener("click", async () => {
  if (!lastResult?.analysis_id || !activeCandidate) return;
  const button = $("#confirm-result");
  button.disabled = true; button.textContent = "Saving…";
  try {
    const response = await fetch("/api/feedback", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({analysis_id:lastResult.analysis_id,selected_id:activeCandidate.id,candidates:lastResult.candidates}) });
    if (!response.ok) throw new Error();
    button.textContent = "Confirmed ✓";
  } catch { button.disabled = false; button.textContent = "Try again"; }
});
document.addEventListener("paste", (event) => {
  const item = [...(event.clipboardData?.items || [])].find((entry) => entry.type.startsWith("image/"));
  if (item) loadFile(item.getAsFile());
});
document.addEventListener("dragover", (event) => { event.preventDefault(); $("#upload-drop").classList.add("dragging"); });
document.addEventListener("dragleave", (event) => { if (!event.relatedTarget) $("#upload-drop").classList.remove("dragging"); });
document.addEventListener("drop", (event) => {
  event.preventDefault();
  $("#upload-drop").classList.remove("dragging");
  const file = event.dataTransfer?.files?.[0];
  if (file?.type.startsWith("image/")) loadFile(file);
});

initMap();
