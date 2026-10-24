const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
let map, selectedFile, previewUrl, lastResult, activeCandidate, activePopup;
let mapLayers = [];
let navigationMarker, navigationResults = [], navigationTimer;
let streetInteractionEnabled = false;

const MAP_STYLES = {
  dark: "https://tiles.openfreemap.org/styles/dark",
  satellite: {
    version: 8,
    sources: { satellite: { type: "raster", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"], tileSize: 256, attribution: "Esri World Imagery" } },
    layers: [{ id: "satellite", type: "raster", source: "satellite" }]
  }
};

const STEP = {
  read: ["Preparing the image", 8], exif: ["Checking spatial signals", 18],
  ocr: ["Reading visible landmarks", 34], rev: ["Comparing visual references", 49],
  vision: ["Understanding the scene", 64], places: ["Searching candidate areas", 81],
  rank: ["Resolving the strongest match", 94],
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
  if (close || bounds.length === 1) map.flyTo({ center: bounds[0], zoom: close ? 17 : 15, pitch: 58, bearing: -10, duration: 1500 });
  else {
    const area = bounds.reduce((box, point) => box.extend(point), new maplibregl.LngLatBounds(bounds[0], bounds[0]));
    map.fitBounds(area, { padding: {top:80,bottom:80,left:80,right:340}, maxZoom: 14, duration: 1400 });
  }
}

function selectCandidate(candidate) {
  if (!candidate) return;
  activeCandidate = candidate;
  $("#result-name").textContent = candidate.name || "Location candidate";
  $("#result-address").textContent = candidate.address || "Mapped location";
  $("#result-tier").textContent = String(candidate.tier || "Place").replace(/^./, (c) => c.toUpperCase());
  $("#result-score").textContent = Number.isFinite(Number(candidate.score)) ? `${Math.min(100, Math.round(Number(candidate.score)))}%` : "Ranked";
  $("#result-radius").textContent = candidate.radius_m ? `${Math.round(candidate.radius_m)} m` : "Candidate pin";
  $$(".candidate").forEach((node) => node.classList.toggle("on", node.dataset.id === String(candidate.id)));
  drawCandidates(lastResult?.candidates || [candidate], candidate.id, true);
  if (!$("#street-view").hidden) showStreetContext();
}

function setView(view) {
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
  if (!activeCandidate) return;
  drawCandidates(lastResult?.candidates || [activeCandidate], activeCandidate.id, true);
  map.flyTo({ center: [activeCandidate.lon, activeCandidate.lat], zoom: 18.2, pitch: 67, bearing: -18, duration: 1450 });
  // Prefer a free, public street-imagery viewer when the shortlist has
  // coverage. Google remains only as the existing no-key fallback.
  const placeQuery = activeCandidate.street_view_query || [activeCandidate.name, activeCandidate.address].filter(Boolean).join(", ");
  const streetUrl = activeCandidate.kartaview || activeCandidate.street_view || `https://maps.google.com/maps?q=${encodeURIComponent(placeQuery)}&z=18&layer=c&output=embed`;
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
  const percent = Math.min(98, base + (status === "done" ? 4 : 0));
  $("#search-detail").textContent = label;
  $("#search-progress").style.width = `${percent}%`;
  $("#search-percent").textContent = `${String(percent).padStart(2, "0")}%`;
}

async function runSearch() {
  if (!selectedFile) return chooseImage();
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
    const response = await fetch("/api/analyze", { method: "POST", body: form });
    if (!response.ok) throw new Error(await response.text());
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        let message;
        try { message = JSON.parse(line); } catch { continue; }
        if (message.type === "step") updateStep(message.id, message.status);
        if (message.type === "result") showResult(message.data);
        if (message.type === "error") throw new Error(message.message || "Search failed");
      }
    }
  } catch (error) {
    document.body.classList.remove("busy");
    $("#search-title").textContent = "Search interrupted";
    $("#search-detail").textContent = error.message || "Please try again";
    $("#search-progress").style.width = "0";
    $("#source-dock").hidden = false;
  }
}

function showResult(data) {
  lastResult = data;
  document.body.classList.remove("busy");
  document.body.classList.add("result-mode");
  $("#search-card").hidden = true;
  $("#source-dock").hidden = true;
  $("#result-ui").hidden = false;
  const candidates = data.candidates || [];
  $("#candidate-strip").innerHTML = candidates.slice(0, 4).map((candidate, index) => `
    <div class="candidate ${data.primary?.id === candidate.id ? "on" : ""}" data-id="${esc(candidate.id)}">
      <b>${index + 1}. ${esc(candidate.name || "Location candidate")}</b>
      <small>${esc(candidate.address || "Mapped candidate")}</small>
    </div>`).join("");
  $$(".candidate").forEach((node) => node.addEventListener("click", () => {
    const found = candidates.find((candidate) => String(candidate.id) === node.dataset.id);
    if (found) selectCandidate(found);
  }));
  const first = data.primary || candidates[0];
  if (first) selectCandidate(first);
  else {
    activeCandidate = null;
    $("#result-name").textContent = "Location not resolved";
    $("#result-address").textContent = "Try a wider, clearer view of the scene.";
    clearLayers();
  }
  setView("street");
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
$("#compare-close").addEventListener("click", () => setView("street"));
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
