// Start each new page in dark mode, regardless of a previous visit's choice.
const themeButton = document.querySelector(".theme-toggle");
function setArticleTheme(light) {
  document.documentElement.dataset.theme = light ? "light" : "dark";
  themeButton?.setAttribute("aria-pressed", String(light));
  themeButton?.setAttribute("aria-label", light ? "Switch to dark mode" : "Switch to light mode");
  const icon = themeButton?.querySelector("span");
  if (icon) icon.textContent = light ? "☾" : "☀";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", light ? "#f6f7f3" : "#090a0b");
}
setArticleTheme(false);
themeButton?.addEventListener("click", () => setArticleTheme(document.documentElement.dataset.theme !== "light"));
