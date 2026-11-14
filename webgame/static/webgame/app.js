"use strict";
const menu = document.getElementById("menu-toggle");
const sidebar = document.getElementById("sidebar");
menu?.addEventListener("click", () => {
  menu.setAttribute("aria-expanded", String(sidebar.classList.toggle("open")));
});
function closeMenu() {
  sidebar?.classList.remove("open");
  menu?.setAttribute("aria-expanded", "false");
}
document.addEventListener("keydown", e => { if (e.key === "Escape") closeMenu(); });
document.addEventListener("click", e => {
  if (!sidebar?.contains(e.target) && !menu?.contains(e.target)) closeMenu();
});
const search = document.getElementById("activity-search");
const category = document.getElementById("category-filter");
const items = [...document.querySelectorAll(".catalog-item")];
[...new Set(items.map(item => item.dataset.category))].forEach(name => {
  const option = document.createElement("option");
  option.textContent = name;
  option.value = name;
  category?.append(option);
});
function filterActivities() {
  const query = (search?.value || "").toLocaleLowerCase("ru").trim();
  let visible = 0;
  items.forEach(item => {
    const show = item.dataset.search.includes(query) &&
      (!category.value || category.value === item.dataset.category);
    item.hidden = !show;
    if (show) visible++;
  });
  const empty = document.getElementById("no-activities");
  if (empty) empty.hidden = visible !== 0;
}
search?.addEventListener("input", filterActivities);
category?.addEventListener("change", filterActivities);
document.querySelectorAll("form[method='post']").forEach(form => {
  form.addEventListener("submit", () => {
    form.querySelectorAll("button[type='submit'], button:not([type])")
      .forEach(button => { button.disabled = true; });
  });
});
window.addEventListener("pageshow", () => {
  document.querySelectorAll("form button").forEach(b => { b.disabled = false; });
});
