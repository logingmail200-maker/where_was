const root = document.documentElement;
const header = document.querySelector("#site-header");
const hero = document.querySelector("#hero");
const product = document.querySelector(".product-stage");
const editorial = document.querySelector(".editorial-bg");
const story = document.querySelector(".geo-story");
const storyCanvas = document.querySelector("#story-map");
const storyGlobe = document.querySelector("#story-globe");
const storySteps = [...document.querySelectorAll(".story-steps li")];
let storyContext = storyCanvas?.getContext("2d");
let globeContext = storyGlobe?.getContext("2d");
const targetLabel = document.querySelector(".target-chip span");
const targetValue = document.querySelector(".target-chip b");
const heroPixels = document.querySelector("#hero-pixels");
const liveMapElement = document.querySelector("#story-live-map");
const sourcePixelCanvas = document.querySelector("#source-pixels");
const sourceImage = document.querySelector(".source-frame > img");
const storyStreetView = document.querySelector(".story-street-view");
const streetViewControl = document.querySelector(".street-view-control");
const streetViewButton = streetViewControl?.querySelector("button");
const themeToggle = document.querySelector(".theme-toggle");
let liveStoryMap = null;
let heroParticlePoints = [];
let sourceParticlePoints = [];
let lastStoryProgress = 0;
let storyAutoStarted = false;
let storyAutoScene = 0;
let storyAutoProgress = 0;
let storyAutoFrame = 0;
let streetViewTimer = 0;

const clamp = (value, min = 0, max = 1) => Math.min(max, Math.max(min, value));
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
let ticking = false;

function applyTheme(theme) {
  const light = theme === "light";
  document.documentElement.dataset.theme = light ? "light" : "dark";
  themeToggle?.setAttribute("aria-pressed", String(light));
  themeToggle?.setAttribute("aria-label", light ? "Switch to dark mode" : "Switch to light mode");
  const icon = themeToggle?.querySelector("span");
  if (icon) icon.textContent = light ? "☾" : "☀";
}

try {
  applyTheme(localStorage.getItem("wherewas-theme") || "dark");
} catch (_) {
  applyTheme("dark");
}
themeToggle?.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(next);
  try { localStorage.setItem("wherewas-theme", next); } catch (_) {}
});

function setStreetViewInteractive(enabled) {
  if (!storyStreetView || !streetViewButton || !streetViewControl) return;
  clearTimeout(streetViewTimer);
  storyStreetView.classList.toggle("is-interactive", enabled);
  streetViewControl.classList.toggle("is-active", enabled);
  streetViewButton.setAttribute("aria-pressed", String(enabled));
  streetViewButton.textContent = enabled ? "Return to page scroll" : "Explore 360°";
  if (enabled && matchMedia("(pointer: coarse)").matches) {
    streetViewTimer = window.setTimeout(() => setStreetViewInteractive(false), 7000);
  }
}

streetViewButton?.addEventListener("click", () => {
  setStreetViewInteractive(!storyStreetView?.classList.contains("is-interactive"));
});
storyStreetView?.addEventListener("mouseleave", () => setStreetViewInteractive(false));
addEventListener("keydown", (event) => {
  if (event.key === "Escape") setStreetViewInteractive(false);
});

function easeInOut(value) {
  const t = clamp(value);
  return t * t * (3 - 2 * t);
}

function initialiseLiveStoryMap() {
  if (!liveMapElement || !window.maplibregl || liveStoryMap) return;
  liveStoryMap = new maplibregl.Map({
    container: liveMapElement,
    interactive: false,
    attributionControl: false,
    center: [4, 23],
    zoom: 1.12,
    bearing: -12,
    pitch: 0,
    fadeDuration: 0,
    renderWorldCopies: false,
    style: {
      version: 8,
      sources: {
        dark: {
          type: "raster",
          tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
          tileSize: 256,
          attribution: "© OpenStreetMap contributors",
        },
      },
      layers: [
        { id: "night", type: "background", paint: { "background-color": "#030408" } },
        { id: "dark-map", type: "raster", source: "dark", paint: { "raster-opacity": 0.91, "raster-saturation": -1, "raster-contrast": 0.34, "raster-brightness-min": 0.08, "raster-brightness-max": 0.76 } },
      ],
    },
  });
  liveStoryMap.on("load", () => {
    try {
      liveStoryMap.setProjection({ type: "globe" });
      liveStoryMap.setFog({ color: "#030408", "high-color": "#151127", "space-color": "#020205", "horizon-blend": 0.16 });
      liveStoryMap.addSource("dubai-target", {
        type: "geojson",
        data: { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [55.2722, 25.1924] } },
      });
      liveStoryMap.addLayer({
        id: "dubai-halo", type: "circle", source: "dubai-target", minzoom: 8,
        paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 8, 15, 18], "circle-color": "rgba(255,255,255,0)", "circle-stroke-color": "rgba(255,255,255,0)", "circle-stroke-width": 0, "circle-opacity": 0 },
      });
      liveStoryMap.addLayer({
        id: "dubai-core", type: "circle", source: "dubai-target", minzoom: 8,
        paint: { "circle-radius": 4, "circle-color": "rgba(255,255,255,0)", "circle-stroke-color": "rgba(255,255,255,0)", "circle-stroke-width": 0, "circle-opacity": 0 },
      });
      liveStoryMap.addSource("boulevard-route", {
        type: "geojson",
        data: { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: [[55.2669,25.1958],[55.2692,25.1947],[55.2714,25.1936],[55.2735,25.1923],[55.2756,25.1912],[55.2781,25.1904]] } },
      });
      liveStoryMap.addLayer({
        id: "boulevard-glow", type: "line", source: "boulevard-route", minzoom: 8,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#caff62", "line-width": ["interpolate", ["linear"], ["zoom"], 8, 7, 15, 25], "line-blur": 7, "line-opacity": 0 },
      });
      liveStoryMap.addLayer({
        id: "boulevard-core", type: "line", source: "boulevard-route", minzoom: 8,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#eaffae", "line-width": ["interpolate", ["linear"], ["zoom"], 8, 1.5, 15, 4], "line-opacity": 0 },
      });
    } catch (_) {}
  });
}

function updateLiveStoryMap(progress) {
  if (!liveStoryMap) return;
  const globePhase = easeInOut((progress - 0.34) / 0.18);
  const zoomPhase = easeInOut((progress - 0.52) / 0.24);
  const startLng = -34;
  const centerLng = startLng + (55.2722 - startLng) * globePhase;
  const centerLat = 18 + (25.1924 - 18) * globePhase;
  const zoom = 1.1 + globePhase * 3.2 + zoomPhase * 8.7;
  liveStoryMap.jumpTo({
    center: [centerLng, centerLat],
    zoom,
    bearing: -28 + globePhase * 28,
    pitch: zoomPhase * 38,
  });
}

function prepareHeroParticles() {
  if (!heroPixels) return;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  heroPixels.width = Math.round(innerWidth * dpr);
  heroPixels.height = Math.round(innerHeight * dpr);
  heroPixels.style.width = `${innerWidth}px`;
  heroPixels.style.height = `${innerHeight}px`;
  const sample = document.createElement("canvas");
  sample.width = Math.max(1, Math.round(innerWidth / 3));
  sample.height = Math.max(1, Math.round(innerHeight / 3));
  const ctx = sample.getContext("2d", { willReadFrequently: true });
  const fontSize = Math.max(25, Math.min(64, innerWidth * 0.052)) / 3;
  ctx.fillStyle = "#fff";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.font = `500 ${fontSize}px Inter, sans-serif`;
  const x = sample.width / 2;
  const y = sample.height * 0.30;
  ctx.fillText("Find the place", x, y);
  ctx.fillText("inside the picture.", x, y + fontSize * 1.05);
  const pixels = ctx.getImageData(0, 0, sample.width, sample.height).data;
  heroParticlePoints = [];
  for (let py = 0; py < sample.height; py += 2) {
    for (let px = 0; px < sample.width; px += 2) {
      if (pixels[(py * sample.width + px) * 4 + 3] > 90 && hash(px * 9 + py * 17) > 0.36) {
        heroParticlePoints.push({ x: px * 3, y: py * 3, drift: hash(px + py * 2) - 0.5, lift: hash(px * 3 + py) });
      }
    }
  }
}

function drawHeroParticles(dissolve) {
  if (!heroPixels) return;
  const ctx = heroPixels.getContext("2d");
  const dpr = Math.min(devicePixelRatio || 1, 2);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, innerWidth, innerHeight);
  if (dissolve <= 0 || dissolve >= 1) return;
  const scatter = easeInOut(dissolve) * 48;
  ctx.fillStyle = `rgba(246,247,244,${Math.sin(dissolve * Math.PI) * 0.88})`;
  heroParticlePoints.forEach((point, index) => {
    if (hash(index * 4.7) < dissolve * 0.24) return;
    const x = point.x + point.drift * scatter;
    const y = point.y - point.lift * scatter * 0.55;
    const size = 1 + hash(index * 8.3) * 1.5;
    ctx.fillRect(x, y, size, size);
  });
}

function hash(value) {
  const x = Math.sin(value * 91.73) * 43758.5453;
  return x - Math.floor(x);
}

function prepareSourceParticles() {
  if (!sourcePixelCanvas || !sourceImage || !sourceImage.complete) return;
  const sample = document.createElement("canvas");
  sample.width = 180;
  sample.height = 102;
  const ctx = sample.getContext("2d", { willReadFrequently: true });
  try {
    ctx.drawImage(sourceImage, 0, 0, sample.width, sample.height);
    const pixels = ctx.getImageData(0, 0, sample.width, sample.height).data;
    sourceParticlePoints = [];
    for (let y = 1; y < sample.height; y += 2) {
      for (let x = 1; x < sample.width; x += 2) {
        const index = (y * sample.width + x) * 4;
        if (pixels[index + 3] < 80 || hash(x * 13 + y * 7) < 0.08) continue;
        sourceParticlePoints.push({
          nx: x / sample.width,
          ny: y / sample.height,
          r: pixels[index],
          g: pixels[index + 1],
          b: pixels[index + 2],
          drift: hash(index * 0.17) - 0.5,
          delay: hash(index * 0.31) * 0.18,
        });
      }
    }
  } catch (_) {
    sourceParticlePoints = [];
  }
}

function drawSourcePixelFlow(progress) {
  if (!sourcePixelCanvas) return;
  const rect = sourcePixelCanvas.getBoundingClientRect();
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  if (sourcePixelCanvas.width !== Math.round(width * dpr) || sourcePixelCanvas.height !== Math.round(height * dpr)) {
    sourcePixelCanvas.width = Math.round(width * dpr);
    sourcePixelCanvas.height = Math.round(height * dpr);
  }
  const ctx = sourcePixelCanvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const flow = clamp((progress - 0.27) / 0.25);
  if (flow <= 0 || flow >= 1 || !sourceParticlePoints.length) return;

  const frame = sourceImage?.parentElement?.getBoundingClientRect();
  if (!frame) return;
  const canvasRect = sourcePixelCanvas.getBoundingClientRect();
  let targetX = width * 0.5;
  let targetY = height * 0.44;
  try {
    const uaePoint = liveStoryMap?.project([55.2722, 25.1924]);
    if (progress >= 0.34 && uaePoint && Number.isFinite(uaePoint.x) && Number.isFinite(uaePoint.y)) {
      targetX = clamp(uaePoint.x, width * 0.16, width * 0.84);
      targetY = clamp(uaePoint.y, height * 0.18, height * 0.72);
    }
  } catch (_) {}
  const wave = Math.sin(flow * Math.PI);
  ctx.globalCompositeOperation = "screen";
  sourceParticlePoints.forEach((point, index) => {
    const local = easeInOut(clamp((flow - point.delay) / (1 - point.delay)));
    const sx = frame.left - canvasRect.left + point.nx * frame.width;
    const sy = frame.top - canvasRect.top + point.ny * frame.height;
    const inverse = 1 - local;
    const controlX = sx + (targetX - sx) * 0.54;
    const controlY = Math.min(sy, targetY) - 72 - hash(index * 0.41) * 24;
    const streamWidth = point.drift * 16 * wave;
    const x = inverse * inverse * sx + 2 * inverse * local * controlX + local * local * targetX + streamWidth;
    const y = inverse * inverse * sy + 2 * inverse * local * controlY + local * local * targetY + streamWidth * 0.28;
    const alpha = Math.sin(local * Math.PI) * 0.9;
    const size = Math.max(0.45, (1.85 - local * 1.1) * (0.72 + hash(index * 2.1)));
    ctx.fillStyle = `rgba(${point.r},${point.g},${point.b},${alpha})`;
    ctx.fillRect(x, y, size, size);
  });
  ctx.globalCompositeOperation = "source-over";

}

const continentShapes = [
  [[-168,72],[-140,70],[-120,58],[-125,40],[-105,18],[-82,24],[-66,46],[-78,61],[-55,72]],
  [[-81,13],[-67,8],[-50,-6],[-35,-24],[-50,-55],[-69,-46],[-78,-15]],
  [[-12,36],[-8,58],[10,71],[34,68],[48,52],[42,38],[25,34],[8,36]],
  [[-18,34],[12,37],[38,30],[51,10],[40,-35],[18,-35],[-4,-15],[-15,8]],
  [[32,72],[88,77],[145,63],[177,50],[160,20],[118,4],[96,7],[75,24],[50,38]],
  [[110,-10],[145,-9],[156,-28],[144,-44],[116,-39]],
  [[-55,84],[-20,80],[-18,61],[-44,59]],
  [[35,31],[56,27],[60,17],[50,12],[40,18]],
];

function pointInsideShape(lon, lat, shape) {
  let inside = false;
  for (let i = 0, j = shape.length - 1; i < shape.length; j = i++) {
    const [xi, yi] = shape[i];
    const [xj, yj] = shape[j];
    const crosses = ((yi > lat) !== (yj > lat)) && (lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi);
    if (crosses) inside = !inside;
  }
  return inside;
}

const isLand = (lon, lat) => continentShapes.some((shape) => pointInsideShape(lon, lat, shape));

function drawStoryMap(progress = 0) {
  if (!storyCanvas || !storyContext) return;
  const rect = storyCanvas.getBoundingClientRect();
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  if (storyCanvas.width !== Math.round(width * dpr) || storyCanvas.height !== Math.round(height * dpr)) {
    storyCanvas.width = Math.round(width * dpr);
    storyCanvas.height = Math.round(height * dpr);
  }
  const ctx = storyContext;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#06080c";
  ctx.fillRect(0, 0, width, height);

  const reveal = clamp((progress - 0.23) / 0.34);
  const zoom = 0.94 + clamp((progress - 0.48) / 0.34) * 0.3;
  ctx.save();
  ctx.translate(width / 2, height / 2);
  ctx.scale(zoom, zoom);
  ctx.translate(-width / 2, -height / 2);

  ctx.globalAlpha = 0.16 * reveal;
  ctx.fillStyle = "#727782";
  for (let i = 0; i < 92; i += 1) {
    const x = hash(i + 2) * width;
    const y = hash(i + 31) * height;
    const w = 18 + hash(i + 77) * 74;
    const h = 12 + hash(i + 113) * 50;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate((hash(i + 55) - 0.5) * 0.5);
    ctx.strokeRect(-w / 2, -h / 2, w, h);
    ctx.restore();
  }
  ctx.globalAlpha = 1;

  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (let i = 0; i < 20; i += 1) {
    const y = (i / 19) * height + (hash(i) - 0.5) * 38;
    ctx.beginPath();
    ctx.moveTo(-60, y);
    for (let x = 0; x <= width + 80; x += width / 6) {
      ctx.lineTo(x, y + Math.sin(x * 0.009 + i) * (13 + hash(i + 40) * 24));
    }
    ctx.strokeStyle = `rgba(126,132,145,${0.08 * reveal})`;
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  for (let i = 0; i < 14; i += 1) {
    const x = (i / 13) * width + (hash(i + 90) - 0.5) * 50;
    ctx.beginPath();
    ctx.moveTo(x, -40);
    for (let y = 0; y <= height + 60; y += height / 6) {
      ctx.lineTo(x + Math.sin(y * 0.008 + i * 2) * (16 + hash(i + 15) * 25), y);
    }
    ctx.strokeStyle = `rgba(126,132,145,${0.07 * reveal})`;
    ctx.stroke();
  }

  const boulevard = () => {
    ctx.beginPath();
    ctx.moveTo(-50, height * 0.77);
    ctx.bezierCurveTo(width * 0.2, height * 0.5, width * 0.5, height * 0.73, width * 0.72, height * 0.43);
    ctx.bezierCurveTo(width * 0.84, height * 0.28, width * 0.93, height * 0.3, width + 60, height * 0.17);
  };
  boulevard();
  ctx.strokeStyle = `rgba(183,188,198,${0.34 * reveal})`;
  ctx.lineWidth = 14;
  ctx.stroke();
  boulevard();
  ctx.strokeStyle = `rgba(10,13,18,${0.9 * reveal})`;
  ctx.lineWidth = 10;
  ctx.stroke();
  boulevard();
  ctx.setLineDash([9, 13]);
  ctx.strokeStyle = `rgba(202,255,98,${0.35 * reveal})`;
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.beginPath();
  ctx.arc(width * 0.43, height * 0.54, 72 + progress * 34, 0, Math.PI * 2);
  ctx.strokeStyle = `rgba(202,255,98,${0.12 * reveal})`;
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(width * 0.43, height * 0.54, 130 + progress * 52, 0, Math.PI * 2);
  ctx.strokeStyle = `rgba(202,255,98,${0.07 * reveal})`;
  ctx.stroke();
  ctx.restore();
}

function drawStoryGlobe(time = 0) {
  if (!storyGlobe || !globeContext) return;
  const rect = storyGlobe.getBoundingClientRect();
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  if (storyGlobe.width !== Math.round(width * dpr) || storyGlobe.height !== Math.round(height * dpr)) {
    storyGlobe.width = Math.round(width * dpr);
    storyGlobe.height = Math.round(height * dpr);
  }
  const ctx = globeContext;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const cx = width * 0.5;
  const cy = height * 0.45;
  const radius = Math.min(width, height) * (width < 700 ? 0.29 : 0.31);
  const rotation = time * 0.00018;
  const glow = ctx.createRadialGradient(cx, cy, radius * 0.08, cx, cy, radius * 1.25);
  glow.addColorStop(0, "rgba(127,101,255,.14)");
  glow.addColorStop(0.62, "rgba(50,39,100,.08)");
  glow.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = glow;
  ctx.beginPath();
  ctx.arc(cx, cy, radius * 1.32, 0, Math.PI * 2);
  ctx.fill();

  ctx.strokeStyle = "rgba(151,127,255,.18)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.arc(cx, cy, radius, 0, Math.PI * 2);
  ctx.stroke();
  for (let ring = 1; ring < 4; ring += 1) {
    ctx.beginPath();
    ctx.ellipse(cx, cy, radius * (ring / 4), radius, 0, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(151,127,255,.07)";
    ctx.stroke();
  }
  for (let ring = -2; ring <= 2; ring += 1) {
    const y = cy + ring * radius * 0.29;
    const rx = Math.sqrt(Math.max(0, radius * radius - Math.pow(y - cy, 2)));
    ctx.beginPath();
    ctx.ellipse(cx, y, rx, rx * 0.16, 0, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(151,127,255,.065)";
    ctx.stroke();
  }

  const globeRotation = (-55 * Math.PI) / 180 + time * 0.000075;
  const step = width < 700 ? 4.6 : 3.2;
  for (let lat = -78; lat <= 78; lat += step) {
    for (let lon = -180; lon < 180; lon += step) {
      const latRad = (lat * Math.PI) / 180;
      const lonRad = (lon * Math.PI) / 180 + globeRotation;
      const x3 = Math.cos(latRad) * Math.sin(lonRad);
      const y3 = Math.sin(latRad);
      const z3 = Math.cos(latRad) * Math.cos(lonRad);
      if (z3 < -0.04) continue;
      const land = isLand(lon, lat);
      const alpha = land ? 0.32 + z3 * 0.62 : 0.035 + z3 * 0.055;
      ctx.fillStyle = land ? `rgba(195,182,255,${alpha})` : `rgba(111,119,141,${alpha})`;
      const size = land ? 1.15 + z3 * 1.05 : 0.6;
      ctx.fillRect(cx + x3 * radius, cy - y3 * radius, size, size);
    }
  }

  const dubaiLat = (25.1924 * Math.PI) / 180;
  const dubaiLon = (55.2722 * Math.PI) / 180 + globeRotation;
  const dx = cx + Math.cos(dubaiLat) * Math.sin(dubaiLon) * radius;
  const dy = cy - Math.sin(dubaiLat) * radius;
  ctx.strokeStyle = "rgba(202,255,98,.36)";
  ctx.beginPath();
  ctx.arc(dx, dy, 11 + Math.sin(time * 0.004) * 3, 0, Math.PI * 2);
  ctx.stroke();
  ctx.fillStyle = "#caff62";
  ctx.beginPath();
  ctx.arc(dx, dy, 3.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "rgba(231,255,187,.9)";
  ctx.font = "500 9px Inter, sans-serif";
  ctx.fillText("DUBAI", dx + 17, dy + 3);
}

function animateStoryGlobe(time) {
  if (!storyGlobe) return;
  const scene = Number(story?.dataset.scene || 0);
  if (!liveStoryMap && (scene === 2 || scene === 3)) drawStoryGlobe(time);
  if (liveStoryMap?.getLayer("boulevard-glow")) {
    const roadReveal = easeInOut((lastStoryProgress - 0.69) / 0.07);
    const pulse = scene === 3 ? 0.62 + Math.sin(time * 0.018) * 0.2 + Math.sin(time * 0.047) * 0.1 : 0;
    try {
      liveStoryMap.setPaintProperty("boulevard-glow", "line-opacity", 0);
      liveStoryMap.setPaintProperty("boulevard-core", "line-opacity", 0);
    } catch (_) {}
  }
  requestAnimationFrame(animateStoryGlobe);
}

function applyStoryState(progress, scene) {
  if (!story) return;
  if (scene !== 7 && storyStreetView?.classList.contains("is-interactive")) {
    setStreetViewInteractive(false);
  }
  lastStoryProgress = progress;
  const intro = easeInOut(progress / 0.14);
  const sourceCollapse = easeInOut((progress - 0.27) / 0.07);
  const globeReveal = easeInOut((progress - 0.34) / 0.045);
  const roadReveal = easeInOut((progress - 0.69) / 0.07);
  story.dataset.scene = String(scene);
  story.style.setProperty("--story-p", progress.toFixed(4));
  story.style.setProperty("--intro-scale", (0.4 + intro * 0.6).toFixed(4));
  story.style.setProperty("--source-opacity", (1 - sourceCollapse).toFixed(4));
  story.style.setProperty("--source-shrink", (1 - sourceCollapse * 0.86).toFixed(4));
  story.style.setProperty("--globe-opacity", (globeReveal * 0.96).toFixed(4));
  story.style.setProperty("--road-reveal", roadReveal.toFixed(4));
  // Keep the source photograph close to the copy, then lift it into the scan stage.
  const introStart = innerWidth < 760 ? 71 : 74;
  story.style.setProperty("--intro-top", `${introStart - intro * (introStart - 48)}%`);
  root.style.setProperty("--story-intro", intro.toFixed(4));
  root.style.setProperty("--title-dissolve", "0");
  storySteps.forEach((step) => step.classList.toggle("active", Number(step.dataset.step) === scene));
  if (targetLabel && targetValue) {
    targetLabel.textContent = scene === 2 ? "Searching" : "Street targeting";
    targetValue.textContent = scene === 2 ? "Global index" : "Downtown Dubai";
  }
  if (scene === 7 && storyStreetView && !storyStreetView.getAttribute("src")) {
    storyStreetView.src = storyStreetView.dataset.src;
  }
  drawStoryMap(progress);
  if (innerWidth >= 760) drawSourcePixelFlow(progress);
  updateLiveStoryMap(progress);
}

function beginAutomaticStory() {
  if (storyAutoStarted || reducedMotion) return;
  storyAutoStarted = true;
  const started = performance.now();
  const scanHold = 1450;
  const transitionDuration = 5750;
  const duration = scanHold + transitionDuration;
  const animate = (now) => {
    const elapsed = now - started;
    if (elapsed < scanHold) {
      // The photo stays whole and stationary while the grid performs one scan.
      storyAutoProgress = 0.18;
      storyAutoScene = 1;
    } else {
      const transitionElapsed = elapsed - scanHold;
      const phase = clamp(transitionElapsed / transitionDuration);
      storyAutoProgress = 0.27 + easeInOut(phase) * 0.57;
      storyAutoScene = transitionElapsed < 2700 ? 2 : transitionElapsed < 5000 ? 3 : 7;
    }
    applyStoryState(storyAutoProgress, storyAutoScene);
    if (elapsed < duration) storyAutoFrame = requestAnimationFrame(animate);
  };
  storyAutoFrame = requestAnimationFrame(animate);
}

function updateScrollScene() {
  const y = window.scrollY;
  if (header) {
    header.classList.toggle("scrolled", y > 18);
  }
  if (!reducedMotion && hero) {
    const travel = Math.max(1, hero.offsetHeight - innerHeight);
    const progress = clamp(y / travel);
    root.style.setProperty("--hero-p", progress.toFixed(4));
    if (product) product.classList.toggle("result", progress > 0.48);
  }
  if (!reducedMotion && editorial) {
    const rect = editorial.parentElement.getBoundingClientRect();
    const progress = clamp((innerHeight - rect.top) / (innerHeight + rect.height));
    editorial.style.setProperty("--editorial-y", `${(progress - 0.5) * 54}px`);
  }
  if (story) {
    const rect = story.getBoundingClientRect();
    const travel = Math.max(1, story.offsetHeight - innerHeight);
    const progress = clamp(-rect.top / travel);
    let scene = 0;
    if (progress >= 0.14) scene = 1;
    if (reducedMotion) scene = 7;
    if (!storyAutoStarted) applyStoryState(progress, scene);
    if (progress >= 0.18 && !storyAutoStarted && !reducedMotion) beginAutomaticStory();
    if (reducedMotion) applyStoryState(0.84, 7);
  }
  ticking = false;
}

addEventListener("scroll", () => {
  if (!ticking) {
    requestAnimationFrame(updateScrollScene);
    ticking = true;
  }
}, { passive: true });
addEventListener("resize", updateScrollScene);
addEventListener("resize", prepareHeroParticles);
addEventListener("resize", prepareSourceParticles);
document.fonts?.ready.then(prepareHeroParticles);
prepareHeroParticles();
if (sourceImage?.complete) prepareSourceParticles();
else sourceImage?.addEventListener("load", prepareSourceParticles, { once: true });
initialiseLiveStoryMap();
updateScrollScene();
requestAnimationFrame(animateStoryGlobe);

const observer = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) {
      entry.target.classList.add("visible");
      observer.unobserve(entry.target);
    }
  });
}, { threshold: 0.14, rootMargin: "0px 0px -7%" });
document.querySelectorAll(".reveal").forEach((item) => observer.observe(item));

const features = {
  estimate: {
    label: "Visual shortlist",
    title: "Turn one uncertain scene into places worth checking.",
    body: "Road geometry, storefronts, architecture, language, transit, and landmarks become a ranked shortlist—not an unexplained pin.",
    image: "https://images.unsplash.com/photo-1449824913935-59a10b8d2000?auto=format&fit=crop&w=1400&q=85",
  },
  precision: {
    label: "Candidate review",
    title: "Let multiple clues converge on the same place.",
    body: "Compare the leading candidates with visible clues, nearby places, and map context before choosing the result that best fits the frame.",
    image: "https://images.unsplash.com/photo-1519501025264-65ba15a82390?auto=format&fit=crop&w=1400&q=85",
  },
  confirm: {
    label: "Verified workflow",
    title: "Keep the final decision visible and accountable.",
    body: "Compare the source frame with the map, confirm the right candidate, and retain the decision context for research and authorised operational work.",
    image: "https://images.unsplash.com/photo-1477959858617-67f85cf4f1df?auto=format&fit=crop&w=1400&q=85",
  },
};

document.querySelectorAll("[data-feature]").forEach((button) => {
  button.addEventListener("click", () => {
    const feature = features[button.dataset.feature];
    if (!feature) return;
    document.querySelectorAll("[data-feature]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-selected", String(active));
    });
    const media = document.querySelector("#feature-media");
    media.classList.add("swapping");
    setTimeout(() => {
      document.querySelector("#feature-label").textContent = feature.label;
      document.querySelector("#feature-title").textContent = feature.title;
      document.querySelector("#feature-body").textContent = feature.body;
      media.style.backgroundImage = `linear-gradient(#0003,#0007),url("${feature.image}")`;
      media.classList.remove("swapping");
    }, reducedMotion ? 0 : 220);
  });
});

document.querySelector(".newsletter form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const input = event.currentTarget.querySelector("input");
  if (!input.value.trim()) {
    input.focus();
    return;
  }
  button.textContent = "Added ✓";
  input.disabled = true;
  button.disabled = true;
});
