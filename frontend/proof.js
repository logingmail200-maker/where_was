const root = document.documentElement;
const theme = document.querySelector(".theme-toggle");
root.dataset.theme = "dark";
theme?.addEventListener("click", () => {
  const light = root.dataset.theme !== "light";
  root.dataset.theme = light ? "light" : "dark";
  theme.setAttribute("aria-pressed", String(light));
  theme.setAttribute("aria-label", light ? "Switch to dark mode" : "Switch to light mode");
  theme.querySelector("span").textContent = light ? "☾" : "☀";
});

const header = document.querySelector("#site-header");
const menu = document.querySelector(".menu-toggle");
function setMenu(open) {
  header.classList.toggle("menu-open", open);
  menu.setAttribute("aria-expanded", String(open));
  menu.setAttribute("aria-label", open ? "Close menu" : "Open menu");
}
menu.addEventListener("click", () => setMenu(!header.classList.contains("menu-open")));
document.querySelectorAll("#primary-nav a").forEach(link => {
  link.addEventListener("click", () => setMenu(false));
});

const demo = document.querySelector("#proof-demo");
const play = document.querySelector("#demo-play");
const steps = document.querySelectorAll(".proof-timeline button");
let phase = 0;
let paused = matchMedia("(prefers-reduced-motion: reduce)").matches;
let visible = false;

function showPhase(next) {
  phase = next;
  demo.dataset.phase = String(next);
  steps.forEach(button => {
    const active = Number(button.dataset.phase) === next;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function updatePlayback() {
  play.textContent = paused ? "Replay ↻" : "Pause Ⅱ";
  play.setAttribute("aria-label", paused ? "Replay demonstration" : "Pause demonstration");
}
play.addEventListener("click", () => {
  paused = !paused;
  if (!paused) showPhase(0);
  updatePlayback();
});
steps.forEach(button => button.addEventListener("click", () => {
  paused = true;
  showPhase(Number(button.dataset.phase));
  updatePlayback();
}));

new IntersectionObserver(([entry]) => {
  visible = entry.intersectionRatio >= .25;
}, {threshold: .25}).observe(demo);
setInterval(() => {
  if (paused || !visible || document.hidden) return;
  if (phase === 2) {
    paused = true;
    updatePlayback();
  } else {
    showPhase(phase + 1);
  }
}, 3500);
showPhase(0);
updatePlayback();

const features = {
  estimate: {
    label: "Visual shortlist",
    title: "Explore likely places from one photo.",
    body: "Compare a ranked shortlist with a score, radius and map context.",
    image: "field-london.jpg",
  },
  precision: {
    label: "Candidate review",
    title: "Inspect each candidate in context.",
    body: "Review the place and its search radius before making your choice.",
    image: "field-switzerland.jpg",
  },
  confirm: {
    label: "Review workflow",
    title: "Keep uncertainty in the picture.",
    body: "A promising place is a lead to inspect, with uncertainty clearly shown.",
    image: "field-mumbai.jpg",
  },
};
const featureTabs = document.querySelectorAll("[data-feature]");
featureTabs.forEach(tab => tab.addEventListener("click", () => {
  const feature = features[tab.dataset.feature];
  featureTabs.forEach(button => {
    button.classList.toggle("active", button === tab);
    button.setAttribute("aria-selected", String(button === tab));
  });
  document.querySelector("#feature-label").textContent = feature.label;
  document.querySelector("#feature-title").textContent = feature.title;
  document.querySelector("#feature-body").textContent = feature.body;
  document.querySelector("#feature-media").style.backgroundImage =
    'linear-gradient(#0003,#0007),url("/assets/' + feature.image + '")';
}));
document.querySelector('[data-feature="estimate"]')?.click();
