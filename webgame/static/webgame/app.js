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
const categories = [...document.querySelectorAll("[name='category-filter']")];
const items = [...document.querySelectorAll(".activity-tile")];
function filterActivities() {
  const query = (search?.value || "").toLocaleLowerCase("ru").trim();
  const selected = categories.filter(c => c.checked).map(c => c.value);
  let visible = 0;
  items.forEach(item => {
    const show = item.dataset.search.includes(query) &&
      (!selected.length || selected.includes(item.dataset.category));
    item.hidden = !show;
    if (show) visible++;
  });
  document.querySelectorAll("[data-filter-group]").forEach(group => {
    group.hidden = ![...group.querySelectorAll(".activity-tile")].some(i => !i.hidden);
  });
  const empty = document.getElementById("no-activities");
  if (empty) empty.hidden = visible !== 0;
}
search?.addEventListener("input", filterActivities);
categories.forEach(c => c.addEventListener("change", filterActivities));
filterActivities();

document.querySelectorAll("form[method='post']").forEach(form => {
  form.addEventListener("submit", event => {
    if (form.closest(".tutorial-guide")) {
      if (form.dataset.tutorialPending) event.preventDefault();
      else form.dataset.tutorialPending = "true";
      return;
    }
    // Disabled submit buttons are excluded from form data.
    form.querySelector("[data-submitter]")?.remove();
    if (event.submitter?.name) {
      const selected = document.createElement("input");
      selected.type = "hidden";
      selected.name = event.submitter.name;
      selected.value = event.submitter.value;
      selected.dataset.submitter = "";
      form.append(selected);
    }
    form.querySelectorAll("button[type='submit'], button:not([type])")
      .forEach(button => {
        if (!button.disabled) button.dataset.submitting = "";
        button.disabled = true;
      });
  });
});
window.addEventListener("pageshow", () => {
  document.querySelectorAll("form[data-tutorial-pending]").forEach(form => {
    delete form.dataset.tutorialPending;
  });
  document.querySelectorAll("button[data-submitting]").forEach(b => {
    b.disabled = false;
    delete b.dataset.submitting;
  });
});

document.querySelectorAll("[data-content-form]").forEach(form => {
  const mode = form.elements.namedItem("condition_type");
  if (!mode) return;
  function updateFields() {
    ["activity", "stat", "skill", "streak", "threshold"].forEach(name => {
      const field = form.elements.namedItem(name);
      if (!field) return;
      const show = name === mode.value ||
        (name === "threshold" && mode.value !== "none");
      field.closest("p").hidden = !show;
      field.disabled = !show;
    });
  }
  mode.addEventListener("change", updateFields);
  updateFields();
});

const edit = document.getElementById("content-edit");
if (edit) {
  const records = JSON.parse(document.getElementById("description-records").textContent);
  const kind = edit.elements.kind;
  const target = edit.elements.target;
  const fill = (keepDraft = false) => {
    const row = records[kind.value].find(item => item.key === target.value);
    document.getElementById("edit-name").value = row?.name || "";
    document.getElementById("edit-details").value = row?.details || "";
    const hasDescription = kind.value === "achievement";
    const hasCondition = kind.value !== "activity";
    const labels = {quest: "Описание условия выполнения", achievement: "Описание условия получения"};
    edit.querySelector("[data-edit-description]").hidden = !hasDescription;
    edit.querySelector("[data-edit-condition]").hidden = !hasCondition;
    edit.elements.description.disabled = !hasDescription;
    edit.elements.condition_text.disabled = !hasCondition;
    edit.elements.condition_text.required = hasCondition;
    edit.querySelector('label[for="' + edit.elements.condition_text.id + '"]').textContent =
      labels[kind.value] || "Описание условия / эффекта";
    if (!keepDraft) {
      edit.elements.description.value = hasDescription ? row?.description || "" : "";
      edit.elements.condition_text.value = hasCondition ? row?.condition_text || "" : "";
    }
    edit.querySelector("button").disabled = !row || !hasCondition;
    edit.querySelector("button").hidden = !hasCondition;
  };
  kind.addEventListener("change", () => {
    target.replaceChildren(...records[kind.value].map(row => new Option(row.name, row.key)));
    fill();
  });
  target.addEventListener("change", () => fill());
  fill(edit.hasAttribute("data-bound"));
}

const displayForm = document.querySelector("[data-display-form]");
if (displayForm) {
  for (const panel of displayForm.querySelectorAll("[data-quest-pins]")) {
    const mode = displayForm.elements.namedItem(panel.dataset.questPins + "_mode");
    const update = () => {
      panel.hidden = mode.value !== "manual";
    };
    mode.addEventListener("change", update);
    update();
  }
  displayForm.querySelector("[data-all-quests]").addEventListener("click", () => {
    displayForm.elements.daily_mode.value = "all";
    displayForm.elements.weekly_mode.value = "all";
    displayForm.requestSubmit();
  });
}

const skillEditor = document.querySelector("[data-skill-links]");
if (skillEditor) {
  const hidden = skillEditor.closest("form").elements.links;
  const activity = document.getElementById("skill-link-activity");
  const xp = document.getElementById("skill-link-xp");
  const list = document.getElementById("skill-link-list");
  const status = document.getElementById("skill-link-status");
  const apply = document.getElementById("skill-link-apply");
  const labels = new Map(Array.from(activity.options, option => [option.value, option.text]));
  let links = new Map();
  try {
    const saved = JSON.parse(hidden.value || "{}");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) {
      links = new Map(Object.entries(saved));
    }
  } catch (_) {
    // The server displays validation errors for an invalid submitted draft.
  }
  const renderLinks = () => {
    list.replaceChildren();
    for (const [key, amount] of links) {
      const item = document.createElement("li");
      const label = String(labels.get(key) || key);
      const text = document.createElement("span");
      text.textContent = label + " — " + amount + " XP";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn btn-outline-secondary";
      remove.textContent = "Удалить";
      remove.setAttribute("aria-label", "Удалить связку: " + label);
      remove.addEventListener("click", () => {
        links.delete(key);
        status.textContent = "Связка удалена.";
        saveDraft();
        apply.focus();
      });
      item.append(text, remove);
      list.append(item);
    }
  };
  const saveDraft = () => {
    hidden.value = JSON.stringify(Object.fromEntries(links));
    renderLinks();
  };
  apply.addEventListener("click", () => {
    const amount = xp.valueAsNumber;
    if (!activity.value || !labels.has(activity.value)) {
      status.textContent = "Выберите активность.";
      activity.focus();
      return;
    }
    if (!Number.isSafeInteger(amount) || amount <= 0) {
      status.textContent = "Введите положительное целое число XP.";
      xp.focus();
      return;
    }
    const existed = links.has(activity.value);
    links.set(activity.value, amount);
    status.textContent = existed ? "XP связки обновлён." : "Связка добавлена.";
    saveDraft();
    activity.value = "";
    xp.value = "";
    activity.focus();
  });
  renderLinks();
}
const systemForm = document.querySelector("[data-system-form]");
if (systemForm) {
  const updateQuiet = () => {
    systemForm.elements.quiet.disabled = !systemForm.elements.autostart.checked;
    if (systemForm.elements.quiet.disabled) systemForm.elements.quiet.checked = false;
  };
  systemForm.elements.autostart.addEventListener("change", updateQuiet);
  updateQuiet();
}
const quick = document.querySelector("[data-quick-activity]");
if (quick) {
  quick.elements.activity.addEventListener("change", async () => {
    const body = new URLSearchParams({
      csrfmiddlewaretoken: quick.elements.csrfmiddlewaretoken.value,
      operation: crypto.randomUUID(),
      activity: quick.elements.activity.value
    });
    const response = await fetch(quick.dataset.quickActivity, {method: "POST", body});
    if (!response.ok) quick.elements.activity.setCustomValidity("Выбор не сохранён. Повторите.");
    else quick.elements.activity.setCustomValidity("");
  });
}

const toastData = document.getElementById("notification-toasts");
if (toastData) {
  const stack = document.getElementById("toast-stack");
  const activeKey = "liferpg-active-toasts";
  let active = [];
  try {
    active = JSON.parse(sessionStorage.getItem(activeKey) || "[]")
      .filter(item => item.expires > Date.now());
  } catch (_) { active = []; }
  for (const item of JSON.parse(toastData.textContent)) {
    const key = "liferpg-toast-" + item.id;
    if (sessionStorage.getItem(key)) continue;
    sessionStorage.setItem(key, "shown");
    active.push({...item, expires: Date.now() + 5000});
  }
  const persist = () => sessionStorage.setItem(activeKey, JSON.stringify(active));
  persist();
  for (const item of active.slice()) {
    const toast = document.createElement("button");
    toast.className = "event-toast";
    toast.textContent = item.text;
    toast.type = "button";
    toast.setAttribute("aria-label", item.text + ". Скрыть уведомление");
    const remove = () => {
      active = active.filter(row => row.id !== item.id);
      persist();
      toast.remove();
    };
    const timer = setTimeout(() => {
      active = active.filter(row => row.id !== item.id);
      persist();
      toast.classList.add("leaving");
      setTimeout(() => toast.remove(), matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 250);
    }, Math.max(0, item.expires - Date.now()));
    toast.addEventListener("click", () => {clearTimeout(timer); remove();});
    stack.append(toast);
  }
}
document.addEventListener("keydown", event => {
  if (event.key === "Escape") document.querySelector(".notification-bell")?.removeAttribute("open");
});


const tutorialData = document.getElementById("tutorial-data");
if (tutorialData) {
  const step = JSON.parse(tutorialData.textContent);
  const target = document.querySelector(step.target) || document.querySelector("#main");
  target.classList.add("tutorial-highlight");
  document.querySelectorAll("#main a, .sidebar a, .notification-bell a").forEach(link => {
    link.addEventListener("click", event => event.preventDefault());
  });
  const reveal = () => {
    closeMenu();
    window.scrollTo({top: window.scrollY + target.getBoundingClientRect().top - 95, behavior: "instant"});
  };
  window.addEventListener("resize", () => requestAnimationFrame(reveal));
  requestAnimationFrame(() => {
    reveal();
    document.querySelector(".tutorial-guide h2").focus({preventScroll: true});
  });
}
