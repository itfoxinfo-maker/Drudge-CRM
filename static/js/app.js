// ====================================================================
// PestCare CRM — single-page front-end
// ====================================================================
const $ = (id) => document.getElementById(id);
const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));

function currencyLabel() { return (SETTINGS && SETTINGS.currency) || t("currency"); }
function money(n) { return (Number(n) || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " " + currencyLabel(); }
function fmtDate(s) { if (!s) return "—"; const d = new Date(s.replace(" ", "T")); return isNaN(d) ? s : d.toLocaleDateString(LANG === "ar" ? "ar" : "en-GB"); }
function fmtDateTime(s) { if (!s) return "—"; const d = new Date(s.replace(" ", "T")); return isNaN(d) ? s : d.toLocaleString(LANG === "ar" ? "ar" : "en-GB", { dateStyle: "medium", timeStyle: "short" }); }
function toast(msg) { const e = $("toast"); e.textContent = msg; e.classList.remove("hidden"); setTimeout(() => e.classList.add("hidden"), 2200); }

function role() { return API.user ? API.user.role : null; }
function isManager() { return ["admin", "manager"].includes(role()); }
function isStaff() { return ["admin", "manager", "agent"].includes(role()); }

// ---- RBAC: permission checks driven by API.user.permissions ----
// admin is always allowed; otherwise consult the resolved permission map.
function can(perm) {
  if (!API.user) return false;
  if (API.user.role === "admin") return true;
  const p = API.user.permissions || {};
  return !!p[perm];
}
// True if the user has any action within a module (e.g. "invoices").
function canModule(mod) {
  if (!API.user) return false;
  if (API.user.role === "admin") return true;
  const p = API.user.permissions || {};
  return Object.keys(p).some(k => k.startsWith(mod + ".") && p[k]);
}

// ---- pagination helper for list views ----
const PAGE_SIZE = 25;
function pagerHTML(d) {
  if (!d || (d.pages || 1) <= 1) return "";
  return `<div class="pager">
    <button class="btn sm secondary" data-pg="prev" ${d.page <= 1 ? "disabled" : ""}>‹ ${t("prev")}</button>
    <span class="muted small">${t("page")} ${d.page} / ${d.pages} · ${d.total}</span>
    <button class="btn sm secondary" data-pg="next" ${d.page >= d.pages ? "disabled" : ""}>${t("next")} ›</button></div>`;
}
function wirePager(scope, d, go) {
  scope.querySelectorAll("[data-pg]").forEach(b => b.addEventListener("click", () => {
    if (b.dataset.pg === "prev" && d.page > 1) go(d.page - 1);
    else if (b.dataset.pg === "next" && d.page < d.pages) go(d.page + 1);
  }));
}

// ---- caches for dropdowns ----
const cache = { services: [], agents: [], clients: [], chemicals: [] };
let SETTINGS = {};
async function loadCaches() {
  try { SETTINGS = await API.get("/settings"); } catch (e) {}
  try { cache.services = await API.get("/service-types"); } catch (e) {}
  if (isManager()) { try { cache.agents = await API.get("/agents"); } catch (e) {} }
  if (isStaff()) {
    try { cache.clients = await API.get("/clients"); } catch (e) {}
    try { cache.chemicals = await API.get("/chemicals"); } catch (e) {}
  }
}

// ====================================================================
// Boot / auth
// ====================================================================
function applyStaticLabels() {
  document.documentElement.lang = LANG;
  document.documentElement.dir = LANG === "ar" ? "rtl" : "ltr";
  $("login-title").textContent = t("app_name");
  $("login-tagline").textContent = t("tagline");
  $("lbl-email").textContent = t("email");
  $("lbl-password").textContent = t("password");
  $("login-btn").textContent = t("sign_in");
  $("login-lang").textContent = t("language");
  $("brand-name").textContent = t("app_name");
  $("logout-btn").textContent = t("logout");
  $("lang-toggle").textContent = t("language");
  $("quick-search").placeholder = t("search_placeholder");
  $("login-powered").textContent = t("powered_by");
  $("sidebar-powered").textContent = t("powered_by");
}

async function boot() {
  setLang(LANG);
  applyStaticLabels();
  if (API.token && API.user) {
    // Refresh the profile so the resolved permission map is always current.
    try { const me = await API.get("/auth/me"); API.setAuth(API.token, me); showApp(); }
    catch (e) { API.clearAuth(); showLogin(); }
  } else showLogin();
}

function showLogin() {
  $("login-screen").classList.remove("hidden");
  $("app").classList.add("hidden");
  applyStaticLabels();
}

async function showApp() {
  $("login-screen").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("user-name").textContent = API.user.full_name;
  $("user-role").textContent = t("role_" + API.user.role);
  await loadCaches();
  renderNav();
  initNotifications();
  navigate("dashboard");
}

// login form
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").classList.add("hidden");
  try {
    const r = await API.post("/auth/login", { email: $("login-email").value, password: $("login-password").value });
    API.setAuth(r.token, r.user);
    if (r.user.lang) setLang(r.user.lang);
    applyStaticLabels();
    showApp();
  } catch (err) {
    $("login-error").textContent = t("invalid_login");
    $("login-error").classList.remove("hidden");
  }
});
$("login-lang").addEventListener("click", () => { toggleLang(); applyStaticLabels(); });
$("lang-toggle").addEventListener("click", () => { toggleLang(); applyStaticLabels(); showApp(); });
$("logout-btn").addEventListener("click", async () => {
  try { await API.post("/auth/logout", {}); } catch (e) {}  // revoke token server-side
  API.clearAuth(); showLogin();
});

// quick search
$("quick-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.value.trim()) navigate("search", { q: e.target.value.trim() });
});

// ====================================================================
// Navigation
// ====================================================================
function navItems() {
  const items = [{ k: "dashboard", i: "📊", t: "nav_dashboard" }];
  if (role() === "client") {
    if (can("visits.view")) items.push({ k: "visits", i: "🗓️", t: "nav_visits" });
    if (can("contracts.view")) items.push({ k: "contracts", i: "🔁", t: "nav_contracts" });
    if (can("invoices.view")) items.push({ k: "invoices", i: "💳", t: "nav_invoices" });
    if (can("certificates.view")) items.push({ k: "certificates", i: "📄", t: "nav_certificates" });
    items.push({ k: "folder", i: "📁", t: "company_folder" });
  } else {
    if (can("clients.view")) items.push({ k: "clients", i: "🏢", t: "nav_clients" });
    if (can("visits.view")) items.push({ k: "schedule", i: "🗓️", t: "nav_schedule" });
    if (can("calendar.view")) items.push({ k: "calendar", i: "📅", t: "nav_calendar" });
    if (can("contracts.view")) items.push({ k: "contracts", i: "🔁", t: "nav_contracts" });
    if (can("chemicals.view")) items.push({ k: "chemicals", i: "🧪", t: "nav_chemicals" });
    if (can("invoices.view")) items.push({ k: "invoices", i: "💳", t: "nav_invoices" });
    if (can("analytics.view")) items.push({ k: "analytics", i: "📈", t: "nav_analytics" });
    if (can("users.view")) items.push({ k: "agents", i: "👷", t: "nav_agents" });
    if (can("settings.view")) items.push({ k: "settings", i: "⚙️", t: "nav_settings" });
    if (can("permissions.view")) items.push({ k: "permissions", i: "🛡️", t: "nav_permissions" });
  }
  items.push({ k: "search", i: "🔍", t: "nav_search" });
  return items;
}
function renderNav() {
  $("nav").innerHTML = navItems().map(it =>
    `<a class="nav-item" data-view="${it.k}"><span class="ic">${it.i}</span><span>${t(it.t)}</span></a>`).join("");
  $("nav").querySelectorAll(".nav-item").forEach(a =>
    a.addEventListener("click", () => navigate(a.dataset.view)));
}

let currentView = null;
async function navigate(view, arg) {
  currentView = view;
  $("nav").querySelectorAll(".nav-item").forEach(a =>
    a.classList.toggle("active", a.dataset.view === view));
  const v = $("view");
  v.innerHTML = `<div class="empty">${t("loading")}</div>`;
  try {
    if (view === "dashboard") await viewDashboard(v);
    else if (view === "clients") await viewClients(v);
    else if (view === "client" || view === "folder") await viewClientFolder(v, arg);
    else if (view === "client-analytics") await viewClientAnalytics(v, arg);
    else if (view === "map") await viewMap(v, arg);
    else if (view === "schedule" || view === "visits") await viewSchedule(v);
    else if (view === "visit") await viewVisit(v, arg);
    else if (view === "chemicals") await viewChemicals(v);
    else if (view === "invoices") await viewInvoices(v);
    else if (view === "invoice") await viewInvoice(v, arg);
    else if (view === "agents") await viewAgents(v);
    else if (view === "calendar") await viewCalendar(v, arg);
    else if (view === "contracts") await viewContracts(v);
    else if (view === "analytics") await viewAnalytics(v);
    else if (view === "settings") await viewSettings(v);
    else if (view === "permissions") await viewPermissions(v, arg);
    else if (view === "certificates") await viewCertificates(v);
    else if (view === "search") await viewSearch(v, arg);
  } catch (e) {
    v.innerHTML = `<div class="empty">⚠️ ${esc(e.message)}</div>`;
  }
}

// ====================================================================
// Modal helper
// ====================================================================
function openModal(title, bodyHtml, onMount) {
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = bodyHtml;
  $("modal-overlay").classList.remove("hidden");
  if (onMount) onMount($("modal-body"));
}
function closeModal() { $("modal-overlay").classList.add("hidden"); $("modal-body").innerHTML = ""; }
$("modal-close").addEventListener("click", closeModal);
$("modal-overlay").addEventListener("click", (e) => { if (e.target === $("modal-overlay")) closeModal(); });

function field(label, name, opts = {}) {
  const { type = "text", value = "", cls = "", options, textarea } = opts;
  let input;
  if (options) {
    input = `<select name="${name}">${options.map(o =>
      `<option value="${esc(o.v)}" ${String(o.v) === String(value) ? "selected" : ""}>${esc(o.l)}</option>`).join("")}</select>`;
  } else if (textarea) {
    input = `<textarea name="${name}">${esc(value)}</textarea>`;
  } else {
    input = `<input type="${type}" name="${name}" value="${esc(value)}" />`;
  }
  return `<div class="field ${cls}"><label>${esc(label)}</label>${input}</div>`;
}
function formData(root) {
  const d = {};
  root.querySelectorAll("[name]").forEach(el => { d[el.name] = el.value; });
  return d;
}
function statusBadge(s) { return `<span class="badge b-${s}">${t(statusKey(s))}</span>`; }
function statusKey(s) {
  const map = { scheduled: "st_scheduled", in_progress: "st_in_progress", completed: "st_completed",
    cancelled: "st_cancelled", draft: "inv_draft", sent: "inv_sent", paid: "inv_paid",
    overdue: "inv_overdue", accepted: "accepted", active: "active", inactive: "status" };
  return map[s] || s;
}

// ====================================================================
// Dashboard
// ====================================================================
async function viewDashboard(v) {
  const d = await API.get("/dashboard");
  let cards = "";
  const card = (val, label, icon, cls = "c-green") =>
    `<div class="stat-card ${cls}"><div class="sc-ic">${icon}</div><div><div class="v">${val}</div><div class="l">${label}</div></div></div>`;
  if (d.role === "client") {
    cards = card(d.upcoming_visits, t("dash_upcoming"), "🗓️", "c-blue") +
      card(d.completed_visits, t("dash_completed"), "✅", "c-green") +
      card(d.open_invoices, t("dash_open_invoices"), "🧾", "c-amber") +
      card(money(d.outstanding), t("dash_outstanding"), "💰", "danger");
  } else {
    cards = card(d.clients, t("dash_clients"), "🏢", "c-blue") +
      card(d.agents, t("dash_agents"), "👷", "c-teal") +
      card(d.visits_today, t("dash_visits_today"), "📅", "c-green") +
      card(d.scheduled, t("dash_scheduled"), "🗓️", "c-purple") +
      card(d.low_stock, t("dash_low_stock"), "🧪", d.low_stock > 0 ? "warn" : "c-green") +
      card(money(d.outstanding), t("dash_outstanding"), "💰", "danger");
    if (d.my_visits !== undefined) cards += card(d.my_visits, t("dash_my_visits"), "📋", "c-blue");
  }
  v.innerHTML = `<div class="page-head"><h2>${t("welcome")}, ${esc(API.user.full_name)}</h2></div>
    <div class="cards">${cards}</div>
    <div class="panel"><h3>${t("nav_schedule")}</h3><div id="dash-visits">${t("loading")}</div></div>`;
  // upcoming visits table
  const visits = await API.get("/visits");
  $("dash-visits").innerHTML = visitsTable(visits.slice(0, 8));
  wireVisitRows($("dash-visits"));
}

// ====================================================================
// Clients list
// ====================================================================
async function viewClients(v) {
  v.innerHTML = `<div class="page-head"><h2>${t("clients_title")}</h2>
    ${isManager() ? `<button class="btn" id="add-client">+ ${t("new_client")}</button>` : ""}</div>
    <div class="panel" id="clients-list">${t("loading")}</div>`;
  if ($("add-client")) $("add-client").addEventListener("click", () => clientForm());
  const render = async (page) => {
    const d = await API.get(`/clients?page=${page}&limit=${PAGE_SIZE}`);
    const rows = d.items;
    $("clients-list").innerHTML =
      `<table><thead><tr><th>${t("company_name_en")}</th><th>${t("contact_person")}</th>
        <th>${t("phone")}</th><th>${t("city")}</th><th>${t("status")}</th></tr></thead>
      <tbody>${rows.map(c => `<tr class="clickable" data-id="${c.id}">
        <td><strong>${esc(localized(c, "name"))}</strong></td>
        <td>${esc(c.contact_person)}</td><td>${esc(c.phone)}</td>
        <td>${esc(c.city)}</td><td>${statusBadge(c.status)}</td></tr>`).join("") ||
        `<tr><td colspan="5" class="empty">${t("none")}</td></tr>`}</tbody></table>` + pagerHTML(d);
    $("clients-list").querySelectorAll("tr[data-id]").forEach(tr =>
      tr.addEventListener("click", () => navigate("client", { id: tr.dataset.id })));
    wirePager($("clients-list"), d, render);
  };
  render(1);
}

function clientForm(c) {
  const isEdit = !!c; c = c || {};
  openModal(isEdit ? t("edit") : t("new_client"), `<form id="cf"><div class="form-grid">
    ${field(t("company_name_en"), "name_en", { value: c.name_en })}
    ${field(t("company_name_ar"), "name_ar", { value: c.name_ar })}
    ${field(t("contact_person"), "contact_person", { value: c.contact_person })}
    ${field(t("phone"), "phone", { value: c.phone })}
    ${field(t("email"), "email", { value: c.email })}
    ${field(t("city"), "city", { value: c.city })}
    ${field(t("address_en"), "address_en", { value: c.address_en, cls: "full" })}
    ${field(t("address_ar"), "address_ar", { value: c.address_ar, cls: "full" })}
    ${field(t("notes"), "notes", { value: c.notes, textarea: true, cls: "full" })}
    </div><div class="form-actions"><button type="button" class="btn secondary" id="cf-cancel">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    $("cf-cancel").addEventListener("click", closeModal);
    root.querySelector("#cf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = formData(root);
      try {
        const saved = isEdit ? await API.put("/clients/" + c.id, d) : await API.post("/clients", d);
        closeModal(); toast(t("saved"));
        cache.clients = await API.get("/clients");
        navigate("client", { id: saved.id });
      } catch (err) { alert(err.message); }
    });
  });
}

// ====================================================================
// Client folder (detail)
// ====================================================================
async function viewClientFolder(v, arg) {
  const id = (arg && arg.id) || (role() === "client" ? API.user.client_id : null);
  if (!id) { v.innerHTML = `<div class="empty">${t("none")}</div>`; return; }
  const c = await API.get("/clients/" + id);
  const fin = c.finance;
  v.innerHTML = `
    ${role() !== "client" ? `<div class="breadcrumb" id="bc">← ${t("clients_title")}</div>` : ""}
    <div class="page-head"><h2>📁 ${esc(localized(c, "name"))}</h2>
      <div style="display:flex;gap:8px">
        <button class="btn sm" id="client-analytics-btn">📊 ${t("view_analytics")}</button>
        ${isManager() ? `<button class="btn secondary sm" id="edit-client">${t("edit")}</button>` : ""}</div></div>
    <div class="grid-2">
      <div class="panel"><h3>${t("company_folder")}</h3>
        <div class="kv">
          <div>${t("contact_person")}</div><div>${esc(c.contact_person) || "—"}</div>
          <div>${t("phone")}</div><div>${esc(c.phone) || "—"}</div>
          <div>${t("email")}</div><div>${esc(c.email) || "—"}</div>
          <div>${t("city")}</div><div>${esc(c.city) || "—"}</div>
          <div>${t("address_en")}</div><div>${esc(localized(c, "address")) || "—"}</div>
          <div>${t("notes")}</div><div>${esc(c.notes) || "—"}</div>
        </div></div>
      <div class="panel"><h3>${t("finance")}</h3>
        <div class="cards" style="grid-template-columns:1fr 1fr;">
          <div class="stat-card"><div class="v" style="font-size:18px">${money(fin.total_invoiced)}</div><div class="l">${t("total_invoiced")}</div></div>
          <div class="stat-card"><div class="v" style="font-size:18px">${money(fin.total_paid)}</div><div class="l">${t("total_paid")}</div></div>
          <div class="stat-card danger"><div class="v" style="font-size:18px">${money(fin.outstanding)}</div><div class="l">${t("outstanding")}</div></div>
        </div>
        <table style="margin-top:8px"><thead><tr><th>${t("invoice_no")}</th><th>${t("total")}</th><th>${t("status")}</th></tr></thead>
        <tbody>${fin.invoices.map(i => `<tr class="clickable" data-inv="${i.id}"><td>${esc(i.number)}</td><td>${money(i.total)}</td><td>${statusBadge(i.status)}</td></tr>`).join("") || `<tr><td colspan="3" class="empty">${t("none")}</td></tr>`}</tbody></table>
      </div>
    </div>

    ${isManager() ? `<div class="panel"><div class="section-title"><h3>${t("sites")}</h3><button class="btn sm" id="add-site">+ ${t("add_site")}</button></div>
      <table><thead><tr><th>${t("site_name")}</th><th>${t("address_en")}</th><th>${t("area")}</th><th></th></tr></thead>
      <tbody id="sites-body">${(c.sites || []).map(s => `<tr><td>${esc(s.name)}</td><td>${esc(s.address)}</td><td>${esc(s.area)}</td>
        <td><button class="link-btn danger sm" data-rmsite="${s.id}">${t("delete")}</button></td></tr>`).join("") || `<tr><td colspan="4" class="empty">${t("none")}</td></tr>`}</tbody></table></div>` : ""}

    <div class="panel"><div class="section-title"><h3>${t("recent_visits")}</h3>
      ${isManager() ? `<button class="btn sm" id="add-visit">+ ${t("new_visit")}</button>` : ""}</div>
      ${visitsTable(c.recent_visits)}</div>

    <div class="panel"><div class="section-title"><h3>🗺️ ${t("maps")}</h3>
      ${isManager() ? `<button class="btn sm" id="add-map">📤 ${t("upload_map")}</button>` : ""}</div>
      <div id="maps-box">${t("loading")}</div></div>

    <div class="panel"><div class="section-title"><h3>${t("photos")}</h3>
      ${role() !== "client" ? `<button class="btn sm" id="add-photo">📷 ${t("upload_photo")}</button>` : ""}</div>
      <div id="photos" class="photo-grid"></div></div>`;

  if ($("bc")) $("bc").addEventListener("click", () => navigate("clients"));
  loadClientMaps(c);
  if ($("add-map")) $("add-map").addEventListener("click", () => uploadMapDialog(c));
  if ($("client-analytics-btn")) $("client-analytics-btn").addEventListener("click", () => navigate("client-analytics", { id: c.id }));
  if ($("edit-client")) $("edit-client").addEventListener("click", () => clientForm(c));
  if ($("add-visit")) $("add-visit").addEventListener("click", () => visitForm({ client_id: c.id }));
  wireVisitRows(v);
  v.querySelectorAll("tr[data-inv]").forEach(tr => tr.addEventListener("click", () => navigate("invoice", { id: tr.dataset.inv })));
  if ($("add-site")) $("add-site").addEventListener("click", () => siteForm(c.id));
  v.querySelectorAll("[data-rmsite]").forEach(b => b.addEventListener("click", async () => {
    if (confirm(t("confirm_delete"))) { await API.del("/sites/" + b.dataset.rmsite); navigate("client", { id: c.id }); }
  }));
  renderPhotos("client", c.id, c.photos);
  if ($("add-photo")) $("add-photo").addEventListener("click", () => uploadPhotoDialog("client", c.id, () => navigate("client", { id: c.id })));
}

function siteForm(clientId) {
  openModal(t("add_site"), `<form id="sf">
    ${field(t("site_name"), "name")}${field(t("address_en"), "address")}${field(t("area"), "area")}
    <div class="form-actions"><button type="button" class="btn secondary" id="sf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    $("sf-x").addEventListener("click", closeModal);
    root.querySelector("#sf").addEventListener("submit", async (e) => {
      e.preventDefault();
      await API.post(`/clients/${clientId}/sites`, formData(root));
      closeModal(); navigate("client", { id: clientId });
    });
  });
}

// ---- photos ----
function renderPhotos(entityType, entityId, photos) {
  const box = $("photos");
  if (!box) return;
  if (!photos || !photos.length) { box.innerHTML = `<div class="empty">${t("no_photos")}</div>`; return; }
  box.innerHTML = photos.map(p => `<div class="photo-item">
    <img src="/uploads/${esc(p.filename)}" alt="${esc(p.caption)}" />
    ${role() !== "client" ? `<button class="rm" data-rmphoto="${p.id}">✕</button>` : ""}
    <div class="cap">${esc(p.caption || p.original_name || "")}</div></div>`).join("");
  box.querySelectorAll("[data-rmphoto]").forEach(b => b.addEventListener("click", async () => {
    if (confirm(t("confirm_delete"))) { await API.del("/photos/" + b.dataset.rmphoto); navigate(currentView, { id: entityId }); }
  }));
}
function uploadPhotoDialog(entityType, entityId, after) {
  openModal(t("upload_photo"), `<form id="pf">
    <div class="field"><label>${t("photos")}</label><input type="file" name="file" accept="image/*" required /></div>
    ${field(t("caption"), "caption")}
    <div class="form-actions"><button type="button" class="btn secondary" id="pf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("upload_photo")}</button></div></form>`, (root) => {
    $("pf-x").addEventListener("click", closeModal);
    root.querySelector("#pf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const file = root.querySelector("[name=file]").files[0];
      if (!file) return;
      try { await API.uploadPhoto(entityType, entityId, file, root.querySelector("[name=caption]").value);
        closeModal(); toast(t("saved")); after && after(); }
      catch (err) { alert(err.message); }
    });
  });
}

// ====================================================================
// Schedule / visits
// ====================================================================
function visitsTable(visits) {
  if (!visits || !visits.length) return `<div class="empty">${t("none")}</div>`;
  return `<table><thead><tr><th>${t("scheduled_start")}</th><th>${t("client")}</th>
    <th>${t("service")}</th><th>${t("agent")}</th><th>${t("status")}</th></tr></thead>
    <tbody>${visits.map(v => `<tr class="clickable" data-visit="${v.id}">
      <td>${fmtDateTime(v.scheduled_start)}</td>
      <td>${esc(localized(v, "client") || "")}</td>
      <td>${esc(localized(v, "service") || "—")}</td>
      <td>${esc(v.agent_name || "—")}</td>
      <td>${statusBadge(v.status)}</td></tr>`).join("")}</tbody></table>`;
}
function wireVisitRows(root) {
  root.querySelectorAll("tr[data-visit]").forEach(tr =>
    tr.addEventListener("click", () => navigate("visit", { id: tr.dataset.visit })));
}

async function viewSchedule(v) {
  const statuses = ["", "scheduled", "in_progress", "completed", "cancelled"];
  v.innerHTML = `<div class="page-head"><h2>${t("schedule_title")}</h2>
    ${isManager() ? `<button class="btn" id="add-visit">+ ${t("new_visit")}</button>` : ""}</div>
    <div class="toolbar">
      <label>${t("status")}: <select id="f-status">${statuses.map(s => `<option value="${s}">${s ? t(statusKey(s)) : t("all")}</option>`).join("")}</select></label>
      ${isManager() ? `<label>${t("agent")}: <select id="f-agent"><option value="">${t("all")}</option>${cache.agents.map(a => `<option value="${a.id}">${esc(a.full_name)}</option>`).join("")}</select></label>` : ""}
      <label>${t("from")}: <input type="date" id="f-from"></label>
      <label>${t("to")}: <input type="date" id="f-to"></label>
    </div>
    <div class="panel" id="visit-list">${t("loading")}</div>`;
  async function refresh(page = 1) {
    const qp = [`page=${page}`, `limit=${PAGE_SIZE}`];
    if ($("f-status").value) qp.push("status=" + $("f-status").value);
    if ($("f-agent") && $("f-agent").value) qp.push("agent=" + $("f-agent").value);
    if ($("f-from").value) qp.push("from=" + $("f-from").value);
    if ($("f-to").value) qp.push("to=" + $("f-to").value);
    const d = await API.get("/visits?" + qp.join("&"));
    $("visit-list").innerHTML = visitsTable(d.items) + pagerHTML(d);
    wireVisitRows($("visit-list"));
    wirePager($("visit-list"), d, refresh);
  }
  ["f-status", "f-agent", "f-from", "f-to"].forEach(id => { if ($(id)) $(id).addEventListener("change", () => refresh(1)); });
  if ($("add-visit")) $("add-visit").addEventListener("click", () => visitForm());
  refresh(1);
}

function visitForm(preset) {
  preset = preset || {};
  const clientOpts = cache.clients.map(c => ({ v: c.id, l: localized(c, "name") }));
  const agentOpts = [{ v: "", l: t("none") }].concat(cache.agents.map(a => ({ v: a.id, l: a.full_name })));
  const svcOpts = [{ v: "", l: t("none") }].concat(cache.services.map(s => ({ v: s.id, l: localized(s, "name") })));
  openModal(t("new_visit"), `<form id="vf"><div class="form-grid">
    ${field(t("client"), "client_id", { options: clientOpts, value: preset.client_id, cls: "full" })}
    ${field(t("agent"), "agent_id", { options: agentOpts })}
    ${field(t("service"), "service_type_id", { options: svcOpts })}
    ${field(t("scheduled_start"), "scheduled_start", { type: "datetime-local" })}
    ${field(t("scheduled_end"), "scheduled_end", { type: "datetime-local" })}
    ${field(t("location"), "location", { cls: "full" })}
    ${field(t("notes"), "notes", { textarea: true, cls: "full" })}
    </div><div class="form-actions"><button type="button" class="btn secondary" id="vf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    $("vf-x").addEventListener("click", closeModal);
    root.querySelector("#vf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = formData(root);
      Object.keys(d).forEach(k => { if (d[k] === "") delete d[k]; });
      try { const saved = await API.post("/visits", d); closeModal(); navigate("visit", { id: saved.id }); }
      catch (err) { alert(err.message); }
    });
  });
}

// ---- visit detail ----
async function viewVisit(v, arg) {
  const id = arg.id;
  const visit = await API.get("/visits/" + id);
  const canEdit = isManager() || (role() === "agent");
  const rep = visit.report || {};
  v.innerHTML = `
    <div class="breadcrumb" id="bc">← ${t("nav_schedule")}</div>
    <div class="page-head"><h2>${t("visit_detail")} — ${esc(localized(visit, "client"))}</h2>
      <div style="display:flex;gap:8px;align-items:center">
        ${visit.status === "completed" ? `<button class="btn sm" id="print-cert">📄 ${t("print_certificate")}</button>` : ""}
        ${statusBadge(visit.status)}</div></div>
    <div class="grid-2">
      <div class="panel"><h3>${t("nav_visits")}</h3><div class="kv">
        <div>${t("client")}</div><div>${esc(localized(visit, "client"))}</div>
        <div>${t("service")}</div><div>${esc(localized(visit, "service") || "—")}</div>
        <div>${t("agent")}</div><div>${esc(visit.agent_name || "—")}</div>
        <div>${t("scheduled_start")}</div><div>${fmtDateTime(visit.scheduled_start)}</div>
        <div>${t("location")}</div><div>${esc(visit.location || visit.site_name || "—")}</div>
        <div>${t("notes")}</div><div>${esc(visit.notes || "—")}</div>
      </div>
      ${canEdit ? `<div class="form-actions" style="justify-content:flex-start">
        <select id="v-status">${["scheduled", "in_progress", "completed", "cancelled"].map(s => `<option value="${s}" ${visit.status === s ? "selected" : ""}>${t(statusKey(s))}</option>`).join("")}</select>
        <button class="btn sm" id="v-status-btn">${t("change_status")}</button></div>` : ""}
      </div>
      <div class="panel"><div class="section-title"><h3>${t("report")}</h3></div>
        ${role() === "client" ? clientReportView(rep) : reportForm(rep, id, canEdit)}
      </div>
    </div>

    ${role() !== "client" ? `<div class="panel"><div class="section-title"><h3>${t("chemicals_used")}</h3>
      ${canEdit ? `<button class="btn sm" id="add-chem">+ ${t("add_chemical")}</button>` : ""}</div>
      <table><thead><tr><th>${t("name_en")}</th><th>${t("quantity")}</th><th>${t("area_treated")}</th><th></th></tr></thead>
      <tbody>${(visit.chemicals || []).map(cu => `<tr><td>${esc(localized(cu, "name"))}</td>
        <td>${cu.quantity} ${esc(cu.unit)}</td><td>${esc(cu.area_treated || "—")}</td>
        <td>${canEdit ? `<button class="link-btn danger sm" data-rmuse="${cu.id}">${t("delete")}</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="4" class="empty">${t("none")}</td></tr>`}</tbody></table></div>` : ""}

    <div class="panel"><div class="section-title"><h3>${t("signatures")}</h3></div>
      <div class="grid-2">${sigBlock("customer", visit, id, canEdit)}${sigBlock("technician", visit, id, canEdit)}</div></div>

    <div class="panel"><div class="section-title"><h3>${t("photos")}</h3>
      ${role() !== "client" ? `<button class="btn sm" id="add-photo">📷 ${t("upload_photo")}</button>` : ""}</div>
      <div id="photos" class="photo-grid"></div></div>`;

  $("bc").addEventListener("click", () => navigate(role() === "client" ? "visits" : "schedule"));
  if ($("print-cert")) $("print-cert").addEventListener("click", () => {
    if (!visit.report || !(visit.report.summary || visit.report.findings || visit.report.pests_found)) {
      alert(t("no_report_for_cert")); return;
    }
    printCertificate(visit);
  });
  v.querySelectorAll("[data-sign]").forEach(b => b.addEventListener("click", () => signatureDialog(id, b.dataset.sign)));
  if ($("v-status-btn")) $("v-status-btn").addEventListener("click", async () => {
    await API.put("/visits/" + id, { status: $("v-status").value }); toast(t("saved")); navigate("visit", { id });
  });
  if ($("report-form")) $("report-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await API.post(`/visits/${id}/report`, formData($("report-form"))); toast(t("saved"));
  });
  if ($("add-chem")) $("add-chem").addEventListener("click", () => usageForm(id));
  v.querySelectorAll("[data-rmuse]").forEach(b => b.addEventListener("click", async () => {
    await API.del("/usage/" + b.dataset.rmuse); navigate("visit", { id });
  }));
  const allPhotos = (visit.photos || []).concat(visit.report_photos || []);
  renderPhotos("visit", id, allPhotos);
  if ($("add-photo")) $("add-photo").addEventListener("click", () => uploadPhotoDialog("visit", id, () => navigate("visit", { id })));
}

function reportForm(rep, visitId, canEdit) {
  const sev = ["low", "medium", "high", "critical"].map(s => ({ v: s, l: t("sev_" + s) }));
  const dis = canEdit ? "" : "disabled";
  return `<form id="report-form">
    ${field(t("summary"), "summary", { value: rep.summary, textarea: true })}
    ${field(t("pests_found"), "pests_found", { value: rep.pests_found })}
    ${field(t("findings"), "findings", { value: rep.findings, textarea: true })}
    ${field(t("recommendations"), "recommendations", { value: rep.recommendations, textarea: true })}
    <div class="form-grid">
      ${field(t("severity"), "severity", { options: sev, value: rep.severity || "low" })}
      ${field(t("next_visit_due"), "next_visit_due", { type: "date", value: rep.next_visit_due })}
    </div>
    <div class="section-title" style="margin:18px 0 8px"><h3>🧰 ${t("materials_used")}</h3></div>
    ${field(t("spare_parts_changed"), "spare_parts_changed", { value: rep.spare_parts_changed })}
    <div class="form-grid">
      ${field(t("lamps_used"), "lamps_used", { type: "number", value: rep.lamps_used || "" })}
      ${field(t("cables_used"), "cables_used", { type: "number", value: rep.cables_used || "" })}
      ${field(t("transformers_used"), "transformers_used", { type: "number", value: rep.transformers_used || "" })}
      ${field(t("light_sheets_used"), "light_sheets_used", { type: "number", value: rep.light_sheets_used || "" })}
      ${field(t("fipronil_ml"), "fipronil_ml", { type: "number", value: rep.fipronil_ml || "" })}
      ${field(t("imidacloprid_gm"), "imidacloprid_gm", { type: "number", value: rep.imidacloprid_gm || "" })}
      ${field(t("baits_count"), "baits_count", { type: "number", value: rep.baits_count || "" })}
      ${field(t("glo_pieces"), "glo_pieces", { type: "number", value: rep.glo_pieces || "" })}
      ${field(t("flybase_bags"), "flybase_bags", { type: "number", value: rep.flybase_bags || "" })}
    </div>
    ${field(t("branch_issue"), "branch_issue", { value: rep.branch_issue, textarea: true })}
    ${canEdit ? `<div class="form-actions"><button class="btn" type="submit">${t("save_report")}</button></div>` : ""}
    </form>${!canEdit ? "<script>document.querySelectorAll('#report-form [name]').forEach(e=>e.disabled=true)</script>" : ""}`;
}
function clientReportView(rep) {
  if (!rep || !rep.id) return `<div class="empty">${t("none")}</div>`;
  // material rows: only show the ones the engineer actually recorded
  const matKeys = ["lamps_used", "cables_used", "transformers_used", "light_sheets_used",
    "fipronil_ml", "imidacloprid_gm", "baits_count", "glo_pieces", "flybase_bags"];
  const mat = matKeys.filter(k => Number(rep[k]) > 0)
    .map(k => `<div>${t(k)}</div><div>${esc(rep[k])}</div>`).join("");
  return `<div class="kv">
    <div>${t("summary")}</div><div>${esc(rep.summary || "—")}</div>
    <div>${t("pests_found")}</div><div>${esc(rep.pests_found || "—")}</div>
    <div>${t("findings")}</div><div>${esc(rep.findings || "—")}</div>
    <div>${t("recommendations")}</div><div>${esc(rep.recommendations || "—")}</div>
    <div>${t("severity")}</div><div class="sev-${rep.severity}">${t("sev_" + (rep.severity || "low"))}</div>
    <div>${t("next_visit_due")}</div><div>${fmtDate(rep.next_visit_due)}</div>
    ${rep.spare_parts_changed ? `<div>${t("spare_parts_changed")}</div><div>${esc(rep.spare_parts_changed)}</div>` : ""}
    ${mat}
    ${rep.branch_issue ? `<div>${t("branch_issue")}</div><div>${esc(rep.branch_issue)}</div>` : ""}</div>`;
}
function usageForm(visitId) {
  const opts = cache.chemicals.map(c => ({ v: c.id, l: `${localized(c, "name")} (${c.quantity_in_stock} ${c.unit})` }));
  openModal(t("add_chemical"), `<form id="uf">
    ${field(t("name_en"), "chemical_id", { options: opts })}
    ${field(t("quantity"), "quantity", { type: "number" })}
    ${field(t("area_treated"), "area_treated")}
    <div class="form-actions"><button type="button" class="btn secondary" id="uf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    $("uf-x").addEventListener("click", closeModal);
    root.querySelector("#uf").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { await API.post(`/visits/${visitId}/usage`, formData(root)); closeModal(); navigate("visit", { id: visitId }); }
      catch (err) { alert(err.message); }
    });
  });
}

// ====================================================================
// Chemicals & inventory
// ====================================================================
async function viewChemicals(v) {
  const chems = await API.get("/chemicals");
  v.innerHTML = `<div class="page-head"><h2>${t("chemicals_title")}</h2>
    ${isManager() ? `<button class="btn" id="add-chem">+ ${t("new_chemical")}</button>` : ""}</div>
    <div class="panel"><table><thead><tr>
      <th>${t("name_en")}</th><th>${t("active_ingredient")}</th><th>${t("in_stock")}</th>
      <th>${t("reorder_level")}</th><th>${t("hazard_class")}</th>${isManager() ? `<th>${t("actions")}</th>` : ""}</tr></thead>
      <tbody>${chems.map(c => {
        const low = c.quantity_in_stock <= c.reorder_level;
        return `<tr><td><strong>${esc(localized(c, "name"))}</strong><div class="muted small">${esc(c.reg_no || "")}</div></td>
        <td>${esc(c.active_ingredient || "—")}</td>
        <td class="${low ? "lowstock" : ""}">${c.quantity_in_stock} ${esc(c.unit)} ${low ? `· ${t("low_stock_warn")}` : ""}</td>
        <td>${c.reorder_level} ${esc(c.unit)}</td><td>${esc(c.hazard_class || "—")}</td>
        ${isManager() ? `<td><button class="link-btn sm" data-stock="${c.id}">${t("adjust_stock")}</button>
          · <button class="link-btn sm" data-edit="${c.id}">${t("edit")}</button></td>` : ""}</tr>`;
      }).join("") || `<tr><td colspan="6" class="empty">${t("none")}</td></tr>`}</tbody></table></div>`;
  if ($("add-chem")) $("add-chem").addEventListener("click", () => chemForm());
  v.querySelectorAll("[data-edit]").forEach(b => b.addEventListener("click", () =>
    chemForm(chems.find(c => c.id == b.dataset.edit))));
  v.querySelectorAll("[data-stock]").forEach(b => b.addEventListener("click", () => stockForm(b.dataset.stock)));
}

function chemForm(c) {
  const isEdit = !!c; c = c || {};
  const units = ["L", "ml", "kg", "g", "unit"].map(u => ({ v: u, l: u }));
  openModal(isEdit ? t("edit") : t("new_chemical"), `<form id="chf"><div class="form-grid">
    ${field(t("name_en"), "name_en", { value: c.name_en })}
    ${field(t("name_ar"), "name_ar", { value: c.name_ar })}
    ${field(t("active_ingredient"), "active_ingredient", { value: c.active_ingredient })}
    ${field(t("unit"), "unit", { options: units, value: c.unit })}
    ${isEdit ? "" : field(t("in_stock"), "quantity_in_stock", { type: "number", value: c.quantity_in_stock || 0 })}
    ${field(t("reorder_level"), "reorder_level", { type: "number", value: c.reorder_level || 0 })}
    ${field(t("hazard_class"), "hazard_class", { value: c.hazard_class })}
    ${field(t("reg_no"), "reg_no", { value: c.reg_no })}
    ${field(t("cost_per_unit"), "cost_per_unit", { type: "number", value: c.cost_per_unit || 0 })}
    </div><div class="form-actions"><button type="button" class="btn secondary" id="chf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    $("chf-x").addEventListener("click", closeModal);
    root.querySelector("#chf").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { if (isEdit) await API.put("/chemicals/" + c.id, formData(root)); else await API.post("/chemicals", formData(root));
        closeModal(); cache.chemicals = await API.get("/chemicals"); navigate("chemicals"); }
      catch (err) { alert(err.message); }
    });
  });
}
function stockForm(id) {
  const reasons = [{ v: "purchase", l: t("add_stock") }, { v: "adjustment", l: t("adjust_stock") }];
  openModal(t("adjust_stock"), `<form id="sf">
    ${field(t("stock_change"), "change", { type: "number" })}
    ${field(t("actions"), "reason", { options: reasons })}
    ${field(t("notes"), "note")}
    <div class="form-actions"><button type="button" class="btn secondary" id="sf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    $("sf-x").addEventListener("click", closeModal);
    root.querySelector("#sf").addEventListener("submit", async (e) => {
      e.preventDefault();
      await API.post(`/chemicals/${id}/stock`, formData(root)); closeModal();
      cache.chemicals = await API.get("/chemicals"); navigate("chemicals");
    });
  });
}

// ====================================================================
// Invoices & finance
// ====================================================================
let invoiceTab = "invoice";
async function viewInvoices(v) {
  const dt = invoiceTab;
  const tab = (k, label) => `<button class="btn sm ${invoiceTab === k ? "" : "secondary"}" data-tab="${k}">${label}</button>`;
  v.innerHTML = `<div class="page-head"><h2>${t("invoices_title")}</h2>
    <div style="display:flex;gap:8px">
      ${isManager() ? `<button class="btn secondary sm" id="exp-inv">⬇ ${t("export_csv")}</button>` : ""}
      ${isManager() ? `<button class="btn" id="add-inv">+ ${dt === "quote" ? t("quote") : t("new_invoice")}</button>` : ""}</div></div>
    <div class="toolbar">${tab("invoice", t("nav_invoices"))} ${tab("quote", t("nav_quotes"))}</div>
    <div class="panel" id="inv-list">${t("loading")}</div>`;
  v.querySelectorAll("[data-tab]").forEach(b => b.addEventListener("click", () => { invoiceTab = b.dataset.tab; navigate("invoices"); }));
  if ($("add-inv")) $("add-inv").addEventListener("click", () => invoiceForm({ doc_type: dt }));
  if ($("exp-inv")) $("exp-inv").addEventListener("click", () => downloadCsv("invoices"));
  const render = async (page) => {
    const d = await API.get(`/invoices?doc_type=${dt}&page=${page}&limit=${PAGE_SIZE}`);
    $("inv-list").innerHTML =
      `<table><thead><tr><th>${t("invoice_no")}</th><th>${t("client")}</th>
        <th>${t("issue_date")}</th><th>${t("total")}</th>${dt === "invoice" ? `<th>${t("paid")}</th>` : ""}<th>${t("status")}</th></tr></thead>
      <tbody>${d.items.map(i => `<tr class="clickable" data-inv="${i.id}">
        <td>${esc(i.number)}</td><td>${esc(localized(i, "client"))}</td>
        <td>${fmtDate(i.issue_date)}</td><td>${money(i.total)}</td>${dt === "invoice" ? `<td>${money(i.paid)}</td>` : ""}
        <td>${statusBadge(i.status)}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">${t("none")}</td></tr>`}</tbody></table>` + pagerHTML(d);
    $("inv-list").querySelectorAll("tr[data-inv]").forEach(tr => tr.addEventListener("click", () => navigate("invoice", { id: tr.dataset.inv })));
    wirePager($("inv-list"), d, render);
  };
  render(1);
}

// Line-items editor used by the invoice/quote form.
function lineItemsEditor(items) {
  items = items && items.length ? items : [{ description: "", quantity: 1, unit_price: 0 }];
  const row = (it) => `<tr class="li-row">
    <td><input class="li-desc" value="${esc(it.description || "")}" placeholder="${t("description")}"></td>
    <td><input class="li-qty" type="number" step="any" value="${it.quantity ?? 1}" style="width:70px"></td>
    <td><input class="li-price" type="number" step="any" value="${it.unit_price ?? 0}" style="width:100px"></td>
    <td class="li-amt num">0.00</td><td><button type="button" class="link-btn danger sm li-rm">✕</button></td></tr>`;
  return `<div class="full"><label>${t("line_items")}</label>
    <table class="li-table"><thead><tr><th>${t("description")}</th><th>${t("qty")}</th>
      <th>${t("unit_price")}</th><th>${t("line_total")}</th><th></th></tr></thead>
      <tbody id="li-body">${items.map(row).join("")}</tbody></table>
    <button type="button" class="btn secondary sm" id="li-add" style="margin-top:8px">${t("add_line")}</button>
    <div style="text-align:end;margin-top:8px"><strong>${t("subtotal")}: <span id="li-sub">0.00</span></strong></div></div>`;
}
function wireLineItems(root, taxInputName) {
  const recalc = () => {
    let sub = 0;
    root.querySelectorAll(".li-row").forEach(r => {
      const q = parseFloat(r.querySelector(".li-qty").value) || 0;
      const p = parseFloat(r.querySelector(".li-price").value) || 0;
      const amt = q * p; sub += amt;
      r.querySelector(".li-amt").textContent = amt.toFixed(2);
    });
    root.querySelector("#li-sub").textContent = sub.toFixed(2);
    const taxRate = parseFloat(SETTINGS.tax_rate || 0);
    const taxEl = root.querySelector(`[name=${taxInputName}]`);
    if (taxEl && !taxEl.dataset.touched) taxEl.value = (sub * taxRate / 100).toFixed(2);
  };
  root.addEventListener("input", (e) => {
    if (e.target.name === taxInputName) e.target.dataset.touched = "1";
    recalc();
  });
  root.querySelector("#li-add").addEventListener("click", () => {
    const tb = root.querySelector("#li-body");
    const div = document.createElement("tbody");
    div.innerHTML = `<tr class="li-row"><td><input class="li-desc" placeholder="${t("description")}"></td>
      <td><input class="li-qty" type="number" step="any" value="1" style="width:70px"></td>
      <td><input class="li-price" type="number" step="any" value="0" style="width:100px"></td>
      <td class="li-amt num">0.00</td><td><button type="button" class="link-btn danger sm li-rm">✕</button></td></tr>`;
    tb.appendChild(div.firstElementChild);
    recalc();
  });
  root.addEventListener("click", (e) => { if (e.target.classList.contains("li-rm")) { e.target.closest("tr").remove(); recalc(); } });
  recalc();
}
function collectLineItems(root) {
  return [...root.querySelectorAll(".li-row")].map(r => ({
    description: r.querySelector(".li-desc").value,
    quantity: parseFloat(r.querySelector(".li-qty").value) || 0,
    unit_price: parseFloat(r.querySelector(".li-price").value) || 0,
  })).filter(it => it.description || it.quantity || it.unit_price);
}

function invoiceForm(preset) {
  preset = preset || {};
  const isEdit = !!preset.id;                 // editing an existing document
  const isQuote = preset.doc_type === "quote";
  const clientOpts = cache.clients.map(c => ({ v: c.id, l: localized(c, "name") }));
  const statuses = (isQuote ? ["draft", "sent", "accepted", "cancelled"] : ["draft", "sent", "paid", "overdue", "cancelled"])
    .map(s => ({ v: s, l: t(statusKey(s)) }));
  const title = isEdit ? `${t("edit")} — ${esc(preset.number)}` : (isQuote ? t("quote") : t("new_invoice"));
  openModal(title, `<form id="if"><div class="form-grid">
    ${field(t("client"), "client_id", { options: clientOpts, value: preset.client_id, cls: "full" })}
    ${field(t("issue_date"), "issue_date", { type: "date", value: preset.issue_date })}
    ${isQuote ? field(t("valid_until"), "valid_until", { type: "date", value: preset.valid_until })
              : field(t("due_date"), "due_date", { type: "date", value: preset.due_date })}
    ${field(t("invoice_status"), "status", { options: statuses, value: preset.status })}
    ${field(t("tax"), "tax", { type: "number", value: preset.tax ?? 0 })}
    ${lineItemsEditor(preset.items)}
    ${field(t("notes"), "notes", { textarea: true, cls: "full", value: preset.notes })}
    </div><div class="form-actions"><button type="button" class="btn secondary" id="if-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    $("if-x").addEventListener("click", closeModal);
    // In edit mode the client can't be moved and the existing tax shouldn't be auto-overwritten.
    if (isEdit) {
      const cl = root.querySelector("[name=client_id]"); if (cl) cl.disabled = true;
      const tx = root.querySelector("[name=tax]"); if (tx) tx.dataset.touched = "1";
    }
    wireLineItems(root, "tax");
    root.querySelector("#if").addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = formData(root);
      d.items = collectLineItems(root);
      try {
        let saved;
        if (isEdit) { saved = await API.put("/invoices/" + preset.id, d); }
        else { d.doc_type = preset.doc_type || "invoice"; saved = await API.post("/invoices", d); }
        closeModal(); toast(t("saved")); navigate("invoice", { id: saved.id });
      } catch (err) { alert(err.message); }
    });
  });
}

async function viewInvoice(v, arg) {
  const inv = await API.get("/invoices/" + arg.id);
  v.innerHTML = `<div class="breadcrumb" id="bc">← ${t("invoices_title")}</div>
    <div class="page-head"><h2>${esc(inv.number)} — ${esc(localized(inv, "client"))}</h2>
      <div>${statusBadge(inv.status)}
        ${isManager() ? `<button class="btn secondary sm" id="edit-inv">✏️ ${t("edit")}</button>` : ""}
        ${(inv.doc_type === "quote" && isManager() && inv.status !== "accepted") ? `<button class="btn sm" id="convert-inv">➡ ${t("convert_to_invoice")}</button>` : ""}
        <button class="btn sm" id="print-inv">🖨️ ${t("print_pdf")}</button></div></div>
    <div class="grid-2">
      <div class="panel"><h3>${inv.doc_type === "quote" ? t("quote") : t("invoice_no")}</h3><div class="kv">
        <div>${t("issue_date")}</div><div>${fmtDate(inv.issue_date)}</div>
        <div>${inv.doc_type === "quote" ? t("valid_until") : t("due_date")}</div><div>${fmtDate(inv.doc_type === "quote" ? inv.valid_until : inv.due_date)}</div>
        <div>${t("amount")}</div><div>${money(inv.amount)}</div>
        <div>${t("tax")}</div><div>${money(inv.tax)}</div>
        <div>${t("total")}</div><div><strong>${money(inv.total)}</strong></div>
        ${inv.doc_type === "quote" ? "" : `<div>${t("paid")}</div><div>${money(inv.paid)}</div>
        <div>${t("outstanding")}</div><div><strong>${money(inv.total - inv.paid)}</strong></div>`}
        <div>${t("notes")}</div><div>${esc(inv.notes || "—")}</div>
      </div>
      ${(inv.items && inv.items.length) ? `<table style="margin-top:12px"><thead><tr><th>${t("description")}</th><th>${t("qty")}</th><th class="num">${t("line_total")}</th></tr></thead>
        <tbody>${inv.items.map(it => `<tr><td>${esc(it.description)}</td><td>${it.quantity}</td><td class="num">${money(it.amount)}</td></tr>`).join("")}</tbody></table>` : ""}
      </div>
      ${inv.doc_type === "quote" ? "" : `<div class="panel"><div class="section-title"><h3>${t("add_payment")}</h3></div>
        ${isManager() ? `<form id="pay-form" class="form-grid">
          ${field(t("payment_amount"), "amount", { type: "number" })}
          ${field(t("payment_method"), "method", { options: [
            { v: "cash", l: "Cash / نقدي" }, { v: "bank_transfer", l: "Bank / تحويل" }, { v: "card", l: "Card / بطاقة" }] })}
          <div class="form-actions full"><button class="btn" type="submit">${t("record_payment")}</button></div>
        </form>` : ""}
        <table style="margin-top:10px"><thead><tr><th>${t("issue_date")}</th><th>${t("payment_method")}</th><th>${t("amount")}</th></tr></thead>
        <tbody>${(inv.payments || []).map(p => `<tr><td>${fmtDateTime(p.paid_at)}</td><td>${esc(p.method)}</td><td>${money(p.amount)}</td></tr>`).join("") || `<tr><td colspan="3" class="empty">${t("none")}</td></tr>`}</tbody></table>
      </div>`}</div>`;
  $("bc").addEventListener("click", () => navigate("invoices"));
  $("print-inv").addEventListener("click", () => printInvoice(inv));
  if ($("edit-inv")) $("edit-inv").addEventListener("click", () => invoiceForm(inv));
  if ($("convert-inv")) $("convert-inv").addEventListener("click", async () => {
    try { const ni = await API.post(`/invoices/${inv.id}/convert`); toast(t("saved")); invoiceTab = "invoice"; navigate("invoice", { id: ni.id }); }
    catch (err) { alert(err.message); }
  });
  if ($("pay-form")) $("pay-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { await API.post(`/invoices/${inv.id}/payments`, formData($("pay-form"))); toast(t("saved")); navigate("invoice", { id: inv.id }); }
    catch (err) { alert(err.message); }
  });
}

// ---- printable / PDF invoice (opens a clean document and triggers print) ----
// Company details come from Settings (editable in the Settings screen).
function printInvoice(inv) {
  const ar = LANG === "ar";
  const dir = ar ? "rtl" : "ltr";
  const S = SETTINGS || {};
  const compName = (ar ? S.company_name_ar : S.company_name_en) || S.company_name_en || "Company";
  const compAddr = (ar ? S.address_ar : S.address_en) || S.address_en || "";
  const isQuote = inv.doc_type === "quote";
  const docTitle = isQuote ? t("quote") : t("invoice_doc");
  const logoHtml = S.logo ? `<img src="/uploads/${esc(S.logo)}" style="height:48px">` : `<div class="logo">🐜</div>`;
  const clientName = localized(inv, "client");
  const clientAddr = ar ? (inv.client_address_ar || inv.client_address_en) : (inv.client_address_en || inv.client_address_ar);
  const due = (inv.total || 0) - (inv.paid || 0);
  // line items table (falls back to a single line when none)
  const items = (inv.items && inv.items.length) ? inv.items
    : [{ description: inv.notes || (ar ? "خدمات مكافحة الآفات" : "Pest control services"),
         quantity: 1, unit_price: inv.amount, amount: inv.amount }];
  const itemRows = items.map(it => `<tr><td>${esc(it.description)}</td>
    <td class="num">${it.quantity}</td><td class="num">${money(it.unit_price)}</td>
    <td class="num">${money(it.amount)}</td></tr>`).join("");
  const payRows = (inv.payments || []).map(p =>
    `<tr><td>${fmtDate(p.paid_at)}</td><td>${esc(p.method)}</td><td class="num">${money(p.amount)}</td></tr>`).join("");
  const doc = `<!DOCTYPE html><html lang="${LANG}" dir="${dir}"><head><meta charset="utf-8">
    <title>${esc(inv.number)}</title>
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
      *{box-sizing:border-box}
      body{font-family:${ar ? "'Cairo'" : "'Inter'"},system-ui,sans-serif;color:#1c2733;margin:0;padding:40px;font-size:13px}
      .inv{max-width:760px;margin:auto}
      .top{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #1f8a4c;padding-bottom:18px}
      .logo{font-size:34px}
      .co h1{margin:0 0 4px;font-size:19px;color:#156c3a}
      .co .muted{color:#6b7a87;line-height:1.6}
      .title{text-align:${ar ? "left" : "right"}}
      .title h2{margin:0;font-size:30px;letter-spacing:2px;color:#1f8a4c}
      .title .no{font-size:14px;font-weight:600;margin-top:4px}
      .parties{display:flex;justify-content:space-between;gap:20px;margin:26px 0}
      .parties h3{margin:0 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:#6b7a87}
      .parties .box{line-height:1.7}
      .meta{text-align:${ar ? "left" : "right"};line-height:1.8}
      table{width:100%;border-collapse:collapse;margin-top:10px}
      th,td{padding:11px 12px;text-align:${ar ? "right" : "left"};border-bottom:1px solid #e3e8ec}
      th{background:#f0f7f2;color:#156c3a;font-size:11px;text-transform:uppercase;letter-spacing:.05em}
      .num{text-align:${ar ? "left" : "right"};white-space:nowrap}
      .totals{margin-top:18px;display:flex;justify-content:${ar ? "flex-start" : "flex-end"}}
      .totals table{width:300px}
      .totals td{border:none;padding:6px 12px}
      .totals .grand td{border-top:2px solid #1f8a4c;font-size:16px;font-weight:700;color:#156c3a}
      .totals .due td{font-weight:700;color:#d23f3f}
      .status{display:inline-block;padding:4px 14px;border-radius:20px;font-weight:700;font-size:12px;
        border:2px solid #1f8a4c;color:#156c3a;margin-top:8px}
      .status.paid{background:#1f8a4c;color:#fff}
      .status.overdue{border-color:#d23f3f;color:#d23f3f}
      h3.sec{margin:26px 0 4px;font-size:12px;color:#6b7a87;text-transform:uppercase;letter-spacing:.06em}
      .foot{margin-top:34px;padding-top:14px;border-top:1px solid #e3e8ec;color:#6b7a87;text-align:center}
      @media print{body{padding:0}.noprint{display:none}}
      .noprint{text-align:center;margin-bottom:20px}
      .pbtn{background:#1f8a4c;color:#fff;border:none;padding:10px 22px;border-radius:8px;font-size:14px;cursor:pointer}
    </style></head><body>
    <div class="noprint"><button class="pbtn" onclick="window.print()">🖨️ ${esc(t("print_pdf"))}</button></div>
    <div class="inv">
      <div class="top">
        <div style="display:flex;gap:12px">${logoHtml}
          <div class="co"><h1>${esc(compName)}</h1>
            <div class="muted">${esc(compAddr)}<br>${esc(S.phone || "")} · ${esc(S.email || "")}<br>${esc(t("vat_no"))}: ${esc(S.vat_no || "")}</div>
          </div></div>
        <div class="title"><h2>${esc(docTitle)}</h2><div class="no">${esc(inv.number)}</div>
          <div class="status ${inv.status}">${esc(t(statusKey(inv.status)))}</div></div>
      </div>
      <div class="parties">
        <div class="box"><h3>${esc(t("bill_to"))}</h3>
          <strong>${esc(clientName)}</strong><br>
          ${inv.client_contact ? esc(inv.client_contact) + "<br>" : ""}
          ${clientAddr ? esc(clientAddr) + "<br>" : ""}
          ${inv.client_city ? esc(inv.client_city) + "<br>" : ""}
          ${inv.client_phone ? esc(inv.client_phone) : ""}</div>
        <div class="meta">
          <div><strong>${esc(t("issue_date"))}:</strong> ${fmtDate(inv.issue_date)}</div>
          <div><strong>${esc(isQuote ? t("valid_until") : t("due_date"))}:</strong> ${fmtDate(isQuote ? inv.valid_until : inv.due_date)}</div>
        </div>
      </div>
      <table><thead><tr><th>${esc(t("description"))}</th><th class="num">${esc(t("qty"))}</th>
        <th class="num">${esc(t("unit_price"))}</th><th class="num">${esc(t("amount"))}</th></tr></thead>
        <tbody>${itemRows}</tbody></table>
      <div class="totals"><table>
        <tr><td>${esc(t("subtotal"))}</td><td class="num">${money(inv.amount)}</td></tr>
        <tr><td>${esc(t("tax"))}</td><td class="num">${money(inv.tax)}</td></tr>
        <tr class="grand"><td>${esc(t("total"))}</td><td class="num">${money(inv.total)}</td></tr>
        ${isQuote ? "" : `<tr><td>${esc(t("paid"))}</td><td class="num">${money(inv.paid)}</td></tr>
        <tr class="due"><td>${esc(t("balance_due"))}</td><td class="num">${money(due)}</td></tr>`}
      </table></div>
      ${payRows ? `<h3 class="sec">${esc(t("payments_received"))}</h3>
        <table><thead><tr><th>${esc(t("date"))}</th><th>${esc(t("payment_method"))}</th><th class="num">${esc(t("amount"))}</th></tr></thead>
        <tbody>${payRows}</tbody></table>` : ""}
      <div class="foot">${esc(t("thank_you"))}</div>
    </div>
    <script>window.onload=function(){setTimeout(function(){window.print()},400)}<\/script>
    </body></html>`;
  const w = window.open("", "_blank");
  if (!w) { alert("Please allow pop-ups to print the invoice."); return; }
  w.document.open();
  w.document.write(doc);
  w.document.close();
}

// ---- printable pest-control service / compliance certificate ----
// Built from the already-loaded visit + report; opens a clean doc and prints.
function printCertificate(visit) {
  const ar = LANG === "ar";
  const dir = ar ? "rtl" : "ltr";
  const S = SETTINGS || {};
  const compName = (ar ? S.company_name_ar : S.company_name_en) || S.company_name_en || "Company";
  const compAddr = (ar ? S.address_ar : S.address_en) || S.address_en || "";
  const logoHtml = S.logo ? `<img src="/uploads/${esc(S.logo)}" style="height:52px">` : `<div class="logo">🐜</div>`;
  const rep = visit.report || {};
  const certNo = "CERT-" + String(visit.id).padStart(5, "0");
  const svcDate = visit.completed_at || visit.scheduled_start;
  // Certificate wording is editable in Settings; fall back to the built-in text.
  const statement = (ar ? S.cert_statement_ar : S.cert_statement_en) || t("cert_statement");
  const footer = (ar ? S.cert_footer_ar : S.cert_footer_en) || t("cert_footer");
  const sevColors = { low: "#1f8a4c", medium: "#d97706", high: "#e0541b", critical: "#d23f3f" };
  const sev = rep.severity || "low";
  const sigImg = f => f ? `<img src="/uploads/${esc(f)}" style="max-height:70px;max-width:220px">` : "";
  const chemRows = (visit.chemicals || []).map(cu =>
    `<tr><td>${esc(localized(cu, "name"))}</td><td class="num">${cu.quantity} ${esc(cu.unit || "")}</td>
     <td>${esc(cu.area_treated || "—")}</td></tr>`).join("");
  // engineer service-log materials (only the ones with recorded quantities)
  const matKeys = ["lamps_used", "cables_used", "transformers_used", "light_sheets_used",
    "fipronil_ml", "imidacloprid_gm", "baits_count", "glo_pieces", "flybase_bags"];
  const matRows = matKeys.filter(k => Number(rep[k]) > 0)
    .map(k => `<tr><td>${esc(t(k))}</td><td class="num">${esc(rep[k])}</td></tr>`).join("");
  const row = (label, val) => val ? `<tr><td class="lbl">${esc(label)}</td><td>${esc(val)}</td></tr>` : "";
  const doc = `<!DOCTYPE html><html lang="${LANG}" dir="${dir}"><head><meta charset="utf-8">
    <title>${esc(certNo)}</title>
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
      *{box-sizing:border-box}
      body{font-family:${ar ? "'Cairo'" : "'Inter'"},system-ui,sans-serif;color:#1c2733;margin:0;padding:40px;font-size:13px}
      .cert{max-width:780px;margin:auto;border:2px solid #1f8a4c;border-radius:10px;padding:30px 34px;position:relative}
      .cert:before{content:"";position:absolute;inset:6px;border:1px solid #cfe6d8;border-radius:7px;pointer-events:none}
      .top{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #1f8a4c;padding-bottom:16px}
      .logo{font-size:36px}
      .co h1{margin:0 0 4px;font-size:18px;color:#156c3a}
      .co .muted{color:#6b7a87;line-height:1.6;font-size:12px}
      .title{text-align:${ar ? "left" : "right"}}
      .title h2{margin:0;font-size:21px;color:#1f8a4c;line-height:1.25}
      .title .no{font-size:13px;font-weight:600;margin-top:6px;color:#6b7a87}
      .statement{margin:20px 0;padding:14px 16px;background:#f0f7f2;border-radius:8px;line-height:1.7;color:#2b3a45}
      h3.sec{margin:20px 0 6px;font-size:11px;color:#156c3a;text-transform:uppercase;letter-spacing:.07em}
      table{width:100%;border-collapse:collapse}
      .kvt td{padding:6px 4px;vertical-align:top;line-height:1.6}
      .kvt td.lbl{color:#6b7a87;width:34%;white-space:nowrap}
      .data th,.data td{padding:9px 10px;text-align:${ar ? "right" : "left"};border-bottom:1px solid #e3e8ec}
      .data th{background:#f0f7f2;color:#156c3a;font-size:11px;text-transform:uppercase}
      .num{text-align:${ar ? "left" : "right"};white-space:nowrap}
      .sev{display:inline-block;padding:3px 12px;border-radius:20px;font-weight:700;font-size:12px;color:#fff;background:${sevColors[sev]}}
      .sigs{display:flex;justify-content:space-between;gap:24px;margin-top:30px}
      .sig{flex:1;text-align:center}
      .sig .ln{border-top:1px solid #1c2733;margin-top:6px;padding-top:6px;color:#6b7a87;font-size:12px}
      .foot{margin-top:24px;padding-top:12px;border-top:1px solid #e3e8ec;color:#6b7a87;text-align:center;font-size:11px;line-height:1.6}
      @media print{body{padding:0}.noprint{display:none}*{-webkit-print-color-adjust:exact !important;print-color-adjust:exact !important}}
      .noprint{text-align:center;margin-bottom:20px}
      .pbtn{background:#1f8a4c;color:#fff;border:none;padding:10px 22px;border-radius:8px;font-size:14px;cursor:pointer}
    </style></head><body>
    <div class="noprint"><button class="pbtn" onclick="window.print()">🖨️ ${esc(t("print_pdf"))}</button></div>
    <div class="cert">
      <div class="top">
        <div style="display:flex;gap:12px">${logoHtml}
          <div class="co"><h1>${esc(compName)}</h1>
            <div class="muted">${esc(compAddr)}<br>${esc(S.phone || "")} · ${esc(S.email || "")}<br>${esc(t("vat_no"))}: ${esc(S.vat_no || "")}</div>
          </div></div>
        <div class="title"><h2>${esc(t("service_certificate"))}</h2>
          <div class="no">${esc(t("cert_no"))}: ${esc(certNo)}</div>
          <div class="no">${esc(t("issued_on"))}: ${fmtDate(new Date().toISOString())}</div>
          ${S.cert_license_no ? `<div class="no">${esc(t("cert_license_no"))}: ${esc(S.cert_license_no)}</div>` : ""}</div>
      </div>
      <div class="statement">${esc(statement)}</div>
      <div style="display:flex;gap:24px">
        <div style="flex:1"><h3 class="sec">${esc(t("premises"))}</h3>
          <table class="kvt">
            ${row(t("client"), localized(visit, "client"))}
            ${row(t("location"), visit.location || visit.site_name)}
          </table></div>
        <div style="flex:1"><h3 class="sec">${esc(t("nav_visits"))}</h3>
          <table class="kvt">
            ${row(t("service"), localized(visit, "service"))}
            ${row(t("date_of_service"), fmtDateTime(svcDate))}
            ${row(t("agent"), visit.agent_name)}
          </table></div>
      </div>
      <h3 class="sec">${esc(t("report"))}</h3>
      <table class="kvt">
        ${row(t("pests_found"), rep.pests_found)}
        ${row(t("findings"), rep.findings)}
        ${row(t("recommendations"), rep.recommendations)}
        <tr><td class="lbl">${esc(t("severity"))}</td><td><span class="sev">${esc(t("sev_" + sev))}</span></td></tr>
        ${row(t("next_visit_due"), rep.next_visit_due ? fmtDate(rep.next_visit_due) : "")}
        ${row(t("spare_parts_changed"), rep.spare_parts_changed)}
        ${row(t("branch_issue"), rep.branch_issue)}
      </table>
      ${matRows ? `<h3 class="sec">${esc(t("materials_used"))}</h3>
        <table class="data"><thead><tr><th>${esc(t("materials_used"))}</th><th class="num">${esc(t("quantity"))}</th></tr></thead>
        <tbody>${matRows}</tbody></table>` : ""}
      ${chemRows ? `<h3 class="sec">${esc(t("chemicals_applied"))}</h3>
        <table class="data"><thead><tr><th>${esc(t("name_en"))}</th><th class="num">${esc(t("quantity"))}</th>
        <th>${esc(t("area_treated"))}</th></tr></thead><tbody>${chemRows}</tbody></table>` : ""}
      <div class="sigs">
        <div class="sig">${sigImg(rep.customer_signature)}<div class="ln">${esc(rep.customer_name || t("customer_signature"))}</div></div>
        <div class="sig">${sigImg(rep.technician_signature)}<div class="ln">${esc(visit.agent_name || t("technician_signature"))}</div></div>
        <div class="sig"><div style="height:70px"></div><div class="ln">${esc(t("authorized_signature"))} — ${esc(compName)}</div></div>
      </div>
      <div class="foot">${esc(footer)}</div>
    </div>
    <script>window.onload=function(){setTimeout(function(){window.print()},400)}<\/script>
    </body></html>`;
  const w = window.open("", "_blank");
  if (!w) { alert("Please allow pop-ups to print the certificate."); return; }
  w.document.open();
  w.document.write(doc);
  w.document.close();
}

// ---- client-facing certificates list (download per completed visit) ----
async function viewCertificates(v) {
  const visits = await API.get("/visits?status=completed");
  const rows = visits.map(vis => {
    const ready = !!vis.has_report;
    const cell = ready
      ? `<button class="btn sm" data-cert="${vis.id}">📄 ${t("download_certificate")}</button>`
      : `<span class="muted">${t("report_pending")}</span>`;
    return `<tr>
      <td>CERT-${String(vis.id).padStart(5, "0")}</td>
      <td>${fmtDate(vis.completed_at || vis.scheduled_start)}</td>
      <td>${esc(localized(vis, "service") || "—")}</td>
      <td>${esc(vis.agent_name || "—")}</td>
      <td>${cell}</td></tr>`;
  }).join("") || `<tr><td colspan="5" class="empty">${t("none")}</td></tr>`;
  v.innerHTML = `<div class="page-head"><h2>📄 ${t("my_certificates")}</h2></div>
    <div class="panel"><p class="muted" style="margin:0 0 14px">${t("certificates_hint")}</p>
      <table><thead><tr><th>${t("cert_no")}</th><th>${t("date_of_service")}</th>
      <th>${t("service")}</th><th>${t("agent")}</th><th>${t("certificate")}</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  v.querySelectorAll("[data-cert]").forEach(b => b.addEventListener("click", async () => {
    const visit = await API.get("/visits/" + b.dataset.cert);
    if (!visit.report || !(visit.report.summary || visit.report.findings || visit.report.pests_found)) {
      alert(t("no_report_for_cert")); return;
    }
    printCertificate(visit);
  }));
}

// ====================================================================
// Agents & users
// ====================================================================
async function viewAgents(v) {
  const users = await API.get("/users");
  const canPerms = can("permissions.edit");
  v.innerHTML = `<div class="page-head"><h2>${t("agents_title")}</h2>
    ${can("users.create") ? `<button class="btn" id="add-user">+ ${t("new_user")}</button>` : ""}</div>
    <div class="panel"><table><thead><tr><th>${t("full_name")}</th><th>${t("email")}</th>
      <th>${t("role")}</th><th>${t("phone")}</th><th>${t("specialization")}</th><th>${t("actions")}</th></tr></thead>
      <tbody>${users.map(u => `<tr><td><strong>${esc(u.full_name)}</strong></td><td>${esc(u.email)}</td>
        <td>${t("role_" + u.role)}</td><td>${esc(u.phone || "—")}</td><td>${esc(u.specialization || "—")}</td>
        <td>${can("users.edit") ? `<button class="link-btn sm" data-edit="${u.id}">${t("edit")}</button>` : ""}
          ${canPerms && u.role !== "admin" ? `<button class="link-btn sm" data-perms="${u.id}">🛡️ ${t("permissions_title")}</button>` : ""}</td></tr>`).join("")}</tbody></table></div>`;
  if ($("add-user")) $("add-user").addEventListener("click", () => userForm());
  v.querySelectorAll("[data-edit]").forEach(b => b.addEventListener("click", () =>
    userForm(users.find(u => u.id == b.dataset.edit))));
  v.querySelectorAll("[data-perms]").forEach(b => b.addEventListener("click", () =>
    navigate("permissions", { tab: "user", userId: b.dataset.perms })));
}

function userForm(u) {
  const isEdit = !!u; u = u || {};
  const roles = ["admin", "manager", "agent", "client"].map(r => ({ v: r, l: t("role_" + r) }));
  const clientOpts = [{ v: "", l: t("none") }].concat(cache.clients.map(c => ({ v: c.id, l: localized(c, "name") })));
  openModal(isEdit ? t("edit") : t("new_user"), `<form id="uf"><div class="form-grid">
    ${field(t("full_name"), "full_name", { value: u.full_name })}
    ${field(t("email"), "email", { value: u.email, type: "email" })}
    ${field(t("password"), "password", { type: "password" })}
    ${field(t("role"), "role", { options: roles, value: u.role })}
    ${field(t("phone"), "phone", { value: u.phone })}
    ${field(t("specialization"), "specialization", { value: u.specialization })}
    ${field(t("hire_date"), "hire_date", { type: "date", value: u.hire_date })}
    ${field(t("belongs_to"), "client_id", { options: clientOpts, value: u.client_id })}
    </div><div class="form-actions"><button type="button" class="btn secondary" id="uf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    if (isEdit) { const em = root.querySelector("[name=email]"); if (em) em.disabled = true; }
    $("uf-x").addEventListener("click", closeModal);
    root.querySelector("#uf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = formData(root);
      if (d.client_id === "") delete d.client_id;
      if (isEdit && !d.password) delete d.password;
      try { if (isEdit) await API.put("/users/" + u.id, d); else await API.post("/users", d);
        closeModal(); cache.clients = await API.get("/clients"); navigate("agents"); }
      catch (err) { alert(err.message); }
    });
  });
}

// ====================================================================
// Permissions (RBAC) — role defaults + per-user overrides
// ====================================================================
const PERM_COLS = ["view", "create", "edit", "delete"];
let _permCatalog = null;

async function loadPermCatalog() {
  if (!_permCatalog) _permCatalog = await API.get("/permissions/catalog");
  return _permCatalog;
}

// Re-fetch my own profile so nav reflects any change to my role/user perms.
async function refreshMyPerms() {
  try { const me = await API.get("/auth/me"); API.setAuth(API.token, me); renderNav(); } catch (e) {}
}

function permMatrixHTML(catalog, effective, opts) {
  const ovr = opts.overrides || {};
  const rows = catalog.map(m => {
    const cells = PERM_COLS.map(act => {
      if (!m.actions.includes(act)) return `<td class="pcell na"></td>`;
      const perm = m.module + "." + act;
      const checked = effective[perm] ? "checked" : "";
      const over = (perm in ovr) ? " overridden" : "";
      const dis = opts.editable ? "" : "disabled";
      return `<td class="pcell${over}" title="${over ? t('perms_overridden') : ''}">
        <input type="checkbox" data-perm="${perm}" ${checked} ${dis}></td>`;
    }).join("");
    const rowToggle = opts.editable
      ? `<input type="checkbox" class="prow" data-mod="${m.module}" title="${t('perms_all')}"> ` : "";
    return `<tr><td class="pmod">${rowToggle}${t("mod_" + m.module)}</td>${cells}</tr>`;
  }).join("");
  return `<table class="perm-table"><thead><tr><th>${t("perms_feature")}</th>
    ${PERM_COLS.map(c => `<th>${t("col_" + c)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>`;
}

function collectPerms(wrap) {
  const o = {};
  wrap.querySelectorAll("input[data-perm]").forEach(c => { o[c.dataset.perm] = c.checked; });
  return o;
}

// Module master checkbox toggles every action in its row, and stays in sync.
function wirePermRowToggles(wrap) {
  wrap.querySelectorAll("input.prow").forEach(rc => {
    const mod = rc.dataset.mod;
    const cells = () => [...wrap.querySelectorAll(`input[data-perm^="${mod}."]`)];
    const sync = () => { const cs = cells(); rc.checked = cs.length > 0 && cs.every(c => c.checked); };
    sync();
    rc.addEventListener("change", () => cells().forEach(c => { c.checked = rc.checked; }));
    cells().forEach(c => c.addEventListener("change", sync));
  });
}

async function viewPermissions(v, arg) {
  if (!can("permissions.view")) { v.innerHTML = `<div class="empty">${t("perms_admin_note")}</div>`; return; }
  const cat = await loadPermCatalog();
  const tab = (arg && arg.tab) || "roles";
  v.innerHTML = `<div class="page-head"><h2>🛡️ ${t("permissions_title")}</h2></div>
    <div class="tabs">
      <button class="tab ${tab === "roles" ? "active" : ""}" data-tab="roles">${t("perms_roles_tab")}</button>
      <button class="tab ${tab === "user" ? "active" : ""}" data-tab="user">${t("perms_users_tab")}</button>
    </div><div id="perm-body">${t("loading")}</div>`;
  v.querySelectorAll(".tab").forEach(b =>
    b.addEventListener("click", () => navigate("permissions", { tab: b.dataset.tab })));
  if (tab === "user") await permUsersTab(cat, arg);
  else await permRolesTab(cat, arg);
}

async function permRolesTab(cat, arg) {
  const sel = (arg && arg.role) || "manager";
  $("perm-body").innerHTML = `<div class="panel">
    <div class="toolbar"><label>${t("perms_role_label")}:
      <select id="perm-role">${cat.roles.map(r =>
        `<option value="${r}" ${r === sel ? "selected" : ""}>${t("role_" + r)}</option>`).join("")}</select></label></div>
    <div id="perm-matrix"></div></div>`;
  $("perm-role").addEventListener("change", e =>
    navigate("permissions", { tab: "roles", role: e.target.value }));
  renderRoleMatrix(cat, sel);
}

function renderRoleMatrix(cat, roleName) {
  const wrap = $("perm-matrix");
  const eff = cat.roles_effective[roleName];
  const editable = can("permissions.edit") && roleName !== "admin";
  const intro = roleName === "admin" ? t("perms_admin_note") : t("perms_role_intro");
  wrap.innerHTML = `<p class="muted">${intro}</p>` +
    permMatrixHTML(cat.catalog, eff, { editable, overrides: cat.role_overrides[roleName] }) +
    (editable ? `<div class="form-actions"><button class="btn" id="perm-save">${t("perms_save")}</button></div>` : "");
  wirePermRowToggles(wrap);
  if (editable) $("perm-save").addEventListener("click", async () => {
    try {
      await API.put("/permissions/roles/" + roleName, { perms: collectPerms(wrap) });
      _permCatalog = null;
      toast(t("perms_saved"));
      await refreshMyPerms();
    } catch (err) { alert(err.message); }
  });
}

async function permUsersTab(cat, arg) {
  const users = (await API.get("/users")).filter(u => u.role !== "admin");
  const selId = (arg && arg.userId) || "";
  $("perm-body").innerHTML = `<div class="panel">
    <div class="toolbar"><label>${t("perms_user_label")}:
      <select id="perm-user"><option value="">${t("perms_select_user")}</option>
        ${users.map(u => `<option value="${u.id}" ${String(u.id) === String(selId) ? "selected" : ""}>${esc(u.full_name)} — ${t("role_" + u.role)}</option>`).join("")}</select></label></div>
    <div id="perm-matrix"></div></div>`;
  $("perm-user").addEventListener("change", e =>
    navigate("permissions", { tab: "user", userId: e.target.value }));
  if (selId) await renderUserMatrix(cat, selId);
}

async function renderUserMatrix(cat, userId) {
  const wrap = $("perm-matrix");
  const data = await API.get("/permissions/users/" + userId);
  const editable = can("permissions.edit");
  wrap.innerHTML = `<p class="muted">${t("perms_user_intro")}</p>` +
    permMatrixHTML(cat.catalog, data.effective, { editable, overrides: data.overrides }) +
    (editable ? `<div class="form-actions">
      <button class="btn secondary" id="perm-reset">${t("perms_reset_user")}</button>
      <button class="btn" id="perm-save">${t("perms_save")}</button></div>` : "");
  wirePermRowToggles(wrap);
  if (!editable) return;
  $("perm-save").addEventListener("click", async () => {
    // Store an override only where the chosen value differs from the role default;
    // anything matching the role is sent as null to clear/inherit.
    const cur = collectPerms(wrap), base = data.role_effective, perms = {};
    Object.keys(cur).forEach(p => { perms[p] = (cur[p] === !!base[p]) ? null : cur[p]; });
    try {
      await API.put("/permissions/users/" + userId, { perms });
      toast(t("perms_saved"));
      await refreshMyPerms();
      navigate("permissions", { tab: "user", userId });
    } catch (err) { alert(err.message); }
  });
  $("perm-reset").addEventListener("click", async () => {
    const perms = {};
    Object.keys(data.effective).forEach(p => { perms[p] = null; });
    try {
      await API.put("/permissions/users/" + userId, { perms });
      toast(t("perms_saved"));
      await refreshMyPerms();
      navigate("permissions", { tab: "user", userId });
    } catch (err) { alert(err.message); }
  });
}

// ====================================================================
// Search
// ====================================================================
async function viewSearch(v, arg) {
  const q = (arg && arg.q) || "";
  v.innerHTML = `<div class="page-head"><h2>${t("search_title")}</h2></div>
    <div class="toolbar"><input id="s-input" placeholder="${t("search_placeholder")}" value="${esc(q)}" style="flex:1;max-width:480px" />
    <button class="btn" id="s-go">${t("search")}</button></div><div id="s-results"></div>`;
  const run = async () => {
    const term = $("s-input").value.trim();
    if (!term) { $("s-results").innerHTML = ""; return; }
    const r = await API.get("/search?q=" + encodeURIComponent(term));
    let html = "";
    if (r.clients && r.clients.length) html += `<div class="panel"><h3>${t("nav_clients")}</h3>` +
      r.clients.map(c => `<div><a href="#" data-go="client" data-id="${c.id}">${esc(localized(c, "name"))}</a> <span class="muted small">${esc(c.city || "")}</span></div>`).join("") + `</div>`;
    if (r.visits && r.visits.length) html += `<div class="panel"><h3>${t("nav_visits")}</h3>` +
      r.visits.map(x => `<div><a href="#" data-go="visit" data-id="${x.id}">${esc(x.client_en)} · ${fmtDate(x.scheduled_start)}</a> ${statusBadge(x.status)}</div>`).join("") + `</div>`;
    if (r.chemicals && r.chemicals.length) html += `<div class="panel"><h3>${t("nav_chemicals")}</h3>` +
      r.chemicals.map(c => `<div>${esc(localized(c, "name"))} — ${c.quantity_in_stock} ${esc(c.unit)}</div>`).join("") + `</div>`;
    if (r.invoices && r.invoices.length) html += `<div class="panel"><h3>${t("nav_invoices")}</h3>` +
      r.invoices.map(i => `<div><a href="#" data-go="invoice" data-id="${i.id}">${esc(i.number)}</a> — ${money(i.total)} ${statusBadge(i.status)}</div>`).join("") + `</div>`;
    $("s-results").innerHTML = html || `<div class="empty">${t("no_results")}</div>`;
    $("s-results").querySelectorAll("[data-go]").forEach(a => a.addEventListener("click", (e) => {
      e.preventDefault(); navigate(a.dataset.go, { id: a.dataset.id });
    }));
  };
  $("s-go").addEventListener("click", run);
  $("s-input").addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  if (q) run();
}

// ====================================================================
// CSV download
// ====================================================================
async function downloadCsv(entity) {
  const res = await fetch("/api/export/" + entity + ".csv", { headers: { Authorization: "Bearer " + API.token } });
  if (!res.ok) { alert("Export failed"); return; }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = entity + ".csv"; a.click();
  URL.revokeObjectURL(url);
}

// ====================================================================
// Notifications (topbar bell)
// ====================================================================
let notifTimer = null;
async function initNotifications() {
  if (!isStaff()) { $("topbar-right").innerHTML = ""; return; }
  $("topbar-right").innerHTML = `<div class="bell-wrap">
    <button id="bell" class="icon-btn" style="font-size:20px;position:relative">🔔<span id="bell-count" class="bell-badge hidden">0</span></button>
    <div id="bell-menu" class="bell-menu hidden"></div></div>`;
  $("bell").addEventListener("click", toggleBell);
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".bell-wrap")) $("bell-menu").classList.add("hidden");
  });
  await refreshNotifications();
  clearInterval(notifTimer);
  notifTimer = setInterval(refreshNotifications, 60000);
}
async function refreshNotifications() {
  try {
    const d = await API.get("/notifications");
    const c = $("bell-count");
    if (!c) return;
    if (d.unread > 0) { c.textContent = d.unread; c.classList.remove("hidden"); }
    else c.classList.add("hidden");
    window._notifs = d.items;
  } catch (e) {}
}
function toggleBell() {
  const menu = $("bell-menu");
  if (!menu.classList.contains("hidden")) { menu.classList.add("hidden"); return; }
  const items = window._notifs || [];
  menu.innerHTML = `<div class="bell-head"><strong>${t("notifications_title")}</strong>
    <button class="link-btn sm" id="bell-read">${t("mark_all_read")}</button></div>
    ${items.length ? items.map(n => `<div class="notif ${n.is_read ? "" : "unread"}" data-link="${n.link_view || ""}" data-id="${n.link_id || ""}">
      <div class="nt">${esc(n.title)}</div><div class="nb muted small">${esc(n.body || "")}</div></div>`).join("")
      : `<div class="empty">${t("no_notifications")}</div>`}`;
  menu.classList.remove("hidden");
  $("bell-read").addEventListener("click", async (e) => { e.stopPropagation(); await API.post("/notifications/read", {}); refreshNotifications(); menu.classList.add("hidden"); });
  menu.querySelectorAll(".notif").forEach(n => n.addEventListener("click", () => {
    const view = n.dataset.link, id = n.dataset.id;
    menu.classList.add("hidden");
    if (view) navigate(view, { id });
  }));
}

// ====================================================================
// Calendar + agent day view
// ====================================================================
function ymd(d) { return d.toISOString().slice(0, 10); }
async function viewCalendar(v, arg) {
  const base = (arg && arg.month) ? new Date(arg.month + "-01") : new Date();
  base.setDate(1);
  const y = base.getFullYear(), m = base.getMonth();
  const monthStr = `${y}-${String(m + 1).padStart(2, "0")}`;
  const first = new Date(y, m, 1), last = new Date(y, m + 1, 0);
  const visits = await API.get(`/visits?from=${y}-${String(m + 1).padStart(2, "0")}-01&to=${ymd(last)}`);
  const byDay = {};
  visits.forEach(vi => { const k = (vi.scheduled_start || "").slice(0, 10); (byDay[k] = byDay[k] || []).push(vi); });
  const agentFilter = isManager() ? `<select id="cal-agent"><option value="">${t("all")} ${t("nav_agents")}</option>
    ${cache.agents.map(a => `<option value="${a.id}">${esc(a.full_name)}</option>`).join("")}</select>` : "";
  const monthName = base.toLocaleDateString(LANG === "ar" ? "ar" : "en-GB", { month: "long", year: "numeric" });
  let cells = "";
  const startDow = (first.getDay() + 6) % 7; // Monday-first
  for (let i = 0; i < startDow; i++) cells += `<div class="cal-cell empty-cell"></div>`;
  for (let day = 1; day <= last.getDate(); day++) {
    const k = `${y}-${String(m + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const dayVisits = byDay[k] || [];
    const isToday = k === ymd(new Date());
    cells += `<div class="cal-cell ${isToday ? "today" : ""}"><div class="cal-day">${day}</div>
      ${dayVisits.slice(0, 4).map(vi => `<div class="cal-ev b-${vi.status}" data-visit="${vi.id}" data-agent="${vi.agent_id || ""}"
        title="${esc(localized(vi, "client"))}">${esc((localized(vi, "client") || "").slice(0, 16))}</div>`).join("")}
      ${dayVisits.length > 4 ? `<div class="muted small">+${dayVisits.length - 4}</div>` : ""}</div>`;
  }
  const dows = (LANG === "ar" ? ["إث", "ثل", "أر", "خم", "جم", "سب", "أح"] : ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]);
  v.innerHTML = `<div class="page-head"><h2>${t("calendar_title")}</h2><div style="display:flex;gap:8px;align-items:center">
      ${agentFilter}
      <button class="btn secondary sm" id="cal-prev">${t("prev")}</button>
      <strong style="min-width:140px;text-align:center">${monthName}</strong>
      <button class="btn secondary sm" id="cal-next">${t("next")}</button>
      <button class="btn secondary sm" id="cal-today">${t("today")}</button></div></div>
    <div class="cal-grid head">${dows.map(d => `<div class="cal-dow">${d}</div>`).join("")}</div>
    <div class="cal-grid" id="cal-body">${cells}</div>`;
  const go = (delta) => { const nd = new Date(y, m + delta, 1); navigate("calendar", { month: `${nd.getFullYear()}-${String(nd.getMonth() + 1).padStart(2, "0")}` }); };
  $("cal-prev").addEventListener("click", () => go(-1));
  $("cal-next").addEventListener("click", () => go(1));
  $("cal-today").addEventListener("click", () => navigate("calendar"));
  const applyFilter = () => {
    const a = $("cal-agent") ? $("cal-agent").value : "";
    v.querySelectorAll(".cal-ev").forEach(ev => {
      ev.style.display = (!a || ev.dataset.agent === a) ? "" : "none";
    });
  };
  if ($("cal-agent")) $("cal-agent").addEventListener("change", applyFilter);
  v.querySelectorAll(".cal-ev").forEach(ev => ev.addEventListener("click", () => navigate("visit", { id: ev.dataset.visit })));
}

// ====================================================================
// Contracts (recurring)
// ====================================================================
const FREQS = ["weekly", "biweekly", "monthly", "quarterly", "semiannual", "annual"];
async function viewContracts(v) {
  const list = await API.get("/contracts");
  v.innerHTML = `<div class="page-head"><h2>${t("contracts_title")}</h2>
    <div style="display:flex;gap:8px">
      ${isManager() ? `<button class="btn secondary" id="run-ct">⚙ ${t("run_now")}</button>` : ""}
      ${isManager() ? `<button class="btn" id="add-ct">+ ${t("new_contract")}</button>` : ""}</div></div>
    <div class="panel"><table><thead><tr><th>${t("client")}</th><th>${t("service")}</th><th>${t("agent")}</th>
      <th>${t("frequency")}</th><th>${t("next_run")}</th><th>${t("price")}</th><th>${t("contract_status")}</th>${isManager() ? `<th></th>` : ""}</tr></thead>
      <tbody>${list.map(c => `<tr><td>${esc(localized(c, "client"))}</td><td>${esc(localized(c, "service") || "—")}</td>
        <td>${esc(c.agent_name || "—")}</td><td>${t("freq_" + c.frequency)}</td><td>${fmtDate(c.next_run_date)}</td>
        <td>${money(c.price)}</td><td><span class="badge b-${c.status === "active" ? "active" : "inactive"}">${t("ct_" + c.status)}</span></td>
        ${isManager() ? `<td><button class="link-btn sm" data-edit="${c.id}">${t("edit")}</button> · <button class="link-btn danger sm" data-del="${c.id}">${t("delete")}</button></td>` : ""}</tr>`).join("")
        || `<tr><td colspan="8" class="empty">${t("none")}</td></tr>`}</tbody></table></div>`;
  if ($("add-ct")) $("add-ct").addEventListener("click", () => contractForm());
  if ($("run-ct")) $("run-ct").addEventListener("click", async () => {
    const r = await API.post("/contracts/run", {}); toast(`${r.created} ${t("generated")}`); navigate("contracts");
  });
  v.querySelectorAll("[data-edit]").forEach(b => b.addEventListener("click", () => contractForm(list.find(c => c.id == b.dataset.edit))));
  v.querySelectorAll("[data-del]").forEach(b => b.addEventListener("click", async () => {
    if (confirm(t("confirm_delete"))) { await API.del("/contracts/" + b.dataset.del); navigate("contracts"); }
  }));
}
function contractForm(c) {
  const isEdit = !!c; c = c || {};
  const clientOpts = cache.clients.map(x => ({ v: x.id, l: localized(x, "name") }));
  const agentOpts = [{ v: "", l: t("none") }].concat(cache.agents.map(a => ({ v: a.id, l: a.full_name })));
  const svcOpts = [{ v: "", l: t("none") }].concat(cache.services.map(s => ({ v: s.id, l: localized(s, "name") })));
  const freqOpts = FREQS.map(f => ({ v: f, l: t("freq_" + f) }));
  const statusOpts = ["active", "paused", "ended"].map(s => ({ v: s, l: t("ct_" + s) }));
  openModal(isEdit ? t("edit") : t("new_contract"), `<form id="ctf"><div class="form-grid">
    ${field(t("client"), "client_id", { options: clientOpts, value: c.client_id, cls: "full" })}
    ${field(t("service"), "service_type_id", { options: svcOpts, value: c.service_type_id })}
    ${field(t("agent"), "agent_id", { options: agentOpts, value: c.agent_id })}
    ${field(t("frequency"), "frequency", { options: freqOpts, value: c.frequency || "monthly" })}
    ${field(t("price"), "price", { type: "number", value: c.price || 0 })}
    ${field(t("start_date"), "start_date", { type: "date", value: c.start_date })}
    ${field(t("end_date"), "end_date", { type: "date", value: c.end_date })}
    ${isEdit ? field(t("contract_status"), "status", { options: statusOpts, value: c.status }) : ""}
    ${field(t("notes"), "notes", { textarea: true, cls: "full", value: c.notes })}
    </div><div class="form-actions"><button type="button" class="btn secondary" id="ctf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    $("ctf-x").addEventListener("click", closeModal);
    root.querySelector("#ctf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = formData(root);
      Object.keys(d).forEach(k => { if (d[k] === "") delete d[k]; });
      try { if (isEdit) await API.put("/contracts/" + c.id, d); else await API.post("/contracts", d);
        closeModal(); navigate("contracts"); } catch (err) { alert(err.message); }
    });
  });
}

// ====================================================================
// Analytics
// ====================================================================
function bar(label, value, max, color) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return `<div class="bar-row"><div class="bar-label">${esc(label)}</div>
    <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${color || "var(--green)"}"></div></div>
    <div class="bar-val">${typeof value === "number" && value % 1 ? money(value) : value}</div></div>`;
}
async function viewAnalytics(v) {
  const a = await API.get("/analytics");
  const T = a.totals;
  const maxMonth = Math.max(1, ...a.months.map(m => m.total || 0));
  const maxAgent = Math.max(1, ...a.agents.map(x => x.total || 0));
  const maxChem = Math.max(1, ...a.chemicals.map(x => x.used || 0));
  const maxSvc = Math.max(1, ...a.services.map(x => x.cnt || 0));
  const agingLabels = { current: t("bucket_current"), "1-30": t("bucket_1_30"), "31-60": t("bucket_31_60"), "60+": t("bucket_60") };
  const maxAge = Math.max(1, ...a.ar_aging.map(x => x.due || 0));
  const empty = `<div class="empty">${t("none")}</div>`;
  // build panel markup once → shared by screen + PDF export
  const parts = {
    cards: `<div class="cards">
      <div class="stat-card c-green"><div class="sc-ic">💰</div><div><div class="v">${money(T.revenue)}</div><div class="l">${t("total_revenue")}</div></div></div>
      <div class="stat-card c-blue"><div class="sc-ic">🧾</div><div><div class="v">${money(T.invoiced)}</div><div class="l">${t("total_invoiced")}</div></div></div>
      <div class="stat-card c-teal"><div class="sc-ic">✅</div><div><div class="v">${T.visits_completed}</div><div class="l">${t("visits_completed")}</div></div></div>
      <div class="stat-card c-purple"><div class="sc-ic">🔁</div><div><div class="v">${T.active_contracts}</div><div class="l">${t("active_contracts")}</div></div></div>
    </div>`,
    revenue: a.months.length ? a.months.slice().reverse().map(m =>
      bar(m.m, m.total || 0, maxMonth) + `<div style="margin-top:-6px">${bar("→ " + t("paid"), m.paid || 0, maxMonth, "var(--blue)")}</div>`).join("") : empty,
    aging: a.ar_aging.length ? a.ar_aging.map(x =>
      bar(agingLabels[x.bucket] || x.bucket, x.due || 0, maxAge, x.bucket === "60+" ? "var(--red)" : "var(--amber)")).join("") : empty,
    agents: a.agents.length ? a.agents.map(x =>
      bar(x.full_name + ` (${x.completed}/${x.total})`, x.total || 0, maxAgent)).join("") : empty,
    chemicals: a.chemicals.length ? a.chemicals.map(x =>
      bar(localized(x, "name") + ` (${x.unit})`, x.used || 0, maxChem, "#7b61ff")).join("") : empty,
    services: a.services.length ? a.services.map(x =>
      bar(localized(x, "name"), x.cnt || 0, maxSvc, "var(--green-d)")).join("") : empty,
  };
  v.innerHTML = `<div class="page-head"><h2>${t("analytics_title")}</h2>
      <button class="btn sm" id="export-main-analytics">🖨️ ${t("export_pdf")}</button></div>
    ${parts.cards}
    <div class="grid-2">
      <div class="panel"><h3>${t("monthly_revenue")}</h3>${parts.revenue}</div>
      <div class="panel"><h3>${t("ar_aging")}</h3>${parts.aging}</div>
      <div class="panel"><h3>${t("agent_productivity")}</h3>${parts.agents}</div>
      <div class="panel"><h3>${t("chemical_usage")}</h3>${parts.chemicals}</div>
      <div class="panel"><h3>${t("service_mix")}</h3>${parts.services}</div>
    </div>`;
  $("export-main-analytics").addEventListener("click", () => printMainAnalytics(parts));
}

function printMainAnalytics(parts) {
  const body = `${parts.cards}
    <div class="grid-2">
      <div class="panel"><h3>${esc(t("monthly_revenue"))}</h3>${parts.revenue}</div>
      <div class="panel"><h3>${esc(t("ar_aging"))}</h3>${parts.aging}</div>
      <div class="panel"><h3>${esc(t("agent_productivity"))}</h3>${parts.agents}</div>
      <div class="panel"><h3>${esc(t("chemical_usage"))}</h3>${parts.chemicals}</div>
      <div class="panel"><h3>${esc(t("service_mix"))}</h3>${parts.services}</div>
    </div>`;
  analyticsReportDoc(t("analytics_title"), "", body);
}

// ====================================================================
// Settings (company / branding)
// ====================================================================
async function viewSettings(v) {
  const s = await API.get("/settings");
  v.innerHTML = `<div class="page-head"><h2>${t("settings_title")}</h2></div>
    <div class="panel"><h3>${t("company_info")}</h3>
      <form id="set-form"><div class="form-grid">
        ${field(t("company_name_en"), "company_name_en", { value: s.company_name_en })}
        ${field(t("company_name_ar"), "company_name_ar", { value: s.company_name_ar })}
        ${field(t("address_en"), "address_en", { value: s.address_en })}
        ${field(t("address_ar"), "address_ar", { value: s.address_ar })}
        ${field(t("phone"), "phone", { value: s.phone })}
        ${field(t("email"), "email", { value: s.email })}
        ${field(t("vat_no"), "vat_no", { value: s.vat_no })}
        ${field(t("currency_label"), "currency", { value: s.currency })}
        ${field(t("tax_rate"), "tax_rate", { type: "number", value: s.tax_rate })}
      </div>
      <div class="form-actions"><button class="btn" type="submit">${t("save_settings")}</button></div></form>
      <div class="section-title"><h3>${t("logo")}</h3></div>
      <div style="display:flex;align-items:center;gap:16px">
        ${s.logo ? `<img src="/uploads/${esc(s.logo)}" style="height:54px;border:1px solid var(--line);border-radius:8px;padding:4px">` : `<div class="logo" style="font-size:42px">🐜</div>`}
        <form id="logo-form"><input type="file" name="file" accept="image/*" required>
          <button class="btn secondary sm" type="submit">${t("upload_logo")}</button></form>
      </div>
    </div>
    <div class="panel"><h3>📄 ${t("cert_settings")}</h3>
      <p class="muted" style="margin:0 0 12px">${t("cert_settings_hint")}</p>
      <form id="cert-form">
        ${field(t("cert_license_no"), "cert_license_no", { value: s.cert_license_no })}
        ${field(t("cert_statement") + " (EN)", "cert_statement_en", { value: s.cert_statement_en, textarea: true })}
        ${field(t("cert_statement") + " (AR)", "cert_statement_ar", { value: s.cert_statement_ar, textarea: true })}
        ${field(t("cert_footer_label") + " (EN)", "cert_footer_en", { value: s.cert_footer_en, textarea: true })}
        ${field(t("cert_footer_label") + " (AR)", "cert_footer_ar", { value: s.cert_footer_ar, textarea: true })}
        <div class="form-actions"><button class="btn" type="submit">${t("save_settings")}</button></div></form></div>
    <div class="panel"><h3>${t("smtp_note")}</h3>
      <form id="smtp-form"><div class="form-grid">
        ${field(t("smtp_host"), "smtp_host", { value: s.smtp_host })}
        ${field(t("smtp_port"), "smtp_port", { value: s.smtp_port || "587" })}
        ${field(t("smtp_user"), "smtp_user", { value: s.smtp_user })}
        ${field(t("smtp_pass"), "smtp_pass", { type: "password", value: s.smtp_pass })}
        ${field(t("smtp_from"), "smtp_from", { value: s.smtp_from })}
      </div><div class="form-actions"><button class="btn" type="submit">${t("save_settings")}</button></div></form></div>`;
  $("set-form").addEventListener("submit", async (e) => {
    e.preventDefault(); SETTINGS = await API.put("/settings", formData($("set-form"))); toast(t("settings_saved"));
  });
  $("cert-form").addEventListener("submit", async (e) => {
    e.preventDefault(); SETTINGS = await API.put("/settings", formData($("cert-form"))); toast(t("settings_saved"));
  });
  $("smtp-form").addEventListener("submit", async (e) => {
    e.preventDefault(); SETTINGS = await API.put("/settings", formData($("smtp-form"))); toast(t("settings_saved"));
  });
  $("logo-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = $("logo-form").querySelector("[name=file]").files[0];
    if (!file) return;
    const fd = new FormData(); fd.append("file", file);
    const res = await fetch("/api/settings/logo", { method: "POST", headers: { Authorization: "Bearer " + API.token }, body: fd });
    if (res.ok) { SETTINGS = await res.json(); toast(t("settings_saved")); navigate("settings"); }
    else alert("Upload failed");
  });
}

// ====================================================================
// Signature capture (canvas)
// ====================================================================
function sigBlock(which, visit, visitId, canEdit) {
  const rep = visit.report || {};
  const file = which === "customer" ? rep.customer_signature : rep.technician_signature;
  const label = which === "customer" ? t("customer_signature") : t("technician_signature");
  const name = which === "customer" && rep.customer_name ? `<div class="muted small">${esc(rep.customer_name)}</div>` : "";
  return `<div class="panel" style="margin:0">
    <div class="section-title" style="margin:0 0 8px"><h3 style="font-size:13px">${label}</h3>
      ${canEdit ? `<button class="link-btn sm" data-sign="${which}">${file ? t("edit") : t("capture_sig")}</button>` : ""}</div>
    ${file ? `<img src="/uploads/${esc(file)}" style="max-height:90px;border:1px solid var(--line);border-radius:6px;background:#fff">${name}`
      : `<div class="muted small">${t("none")}</div>`}</div>`;
}
function signatureDialog(visitId, which) {
  const label = which === "customer" ? t("customer_signature") : t("technician_signature");
  openModal(label, `<div>
    ${which === "customer" ? field(t("customer_name"), "sig_name") : ""}
    <label class="small">${t("sign_here")}</label>
    <canvas id="sigpad" width="540" height="200" style="border:2px dashed var(--line);border-radius:8px;width:100%;touch-action:none;background:#fff"></canvas>
    <div class="form-actions"><button class="btn secondary" id="sig-clear">${t("clear")}</button>
      <button class="btn" id="sig-save">${t("save_signature")}</button></div></div>`, (root) => {
    const cv = root.querySelector("#sigpad"), ctx = cv.getContext("2d");
    ctx.lineWidth = 2.2; ctx.lineCap = "round"; ctx.strokeStyle = "#16324f";
    let drawing = false, last = null;
    const pos = (e) => {
      const r = cv.getBoundingClientRect();
      const cx = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
      const cy = (e.touches ? e.touches[0].clientY : e.clientY) - r.top;
      return { x: cx * (cv.width / r.width), y: cy * (cv.height / r.height) };
    };
    const start = (e) => { drawing = true; last = pos(e); e.preventDefault(); };
    const move = (e) => { if (!drawing) return; const p = pos(e); ctx.beginPath(); ctx.moveTo(last.x, last.y); ctx.lineTo(p.x, p.y); ctx.stroke(); last = p; e.preventDefault(); };
    const end = () => { drawing = false; };
    cv.addEventListener("mousedown", start); cv.addEventListener("mousemove", move); window.addEventListener("mouseup", end);
    cv.addEventListener("touchstart", start); cv.addEventListener("touchmove", move); cv.addEventListener("touchend", end);
    root.querySelector("#sig-clear").addEventListener("click", () => ctx.clearRect(0, 0, cv.width, cv.height));
    root.querySelector("#sig-save").addEventListener("click", async () => {
      const data = cv.toDataURL("image/png");
      const body = { which, data };
      const nameEl = root.querySelector("[name=sig_name]");
      if (nameEl) body.customer_name = nameEl.value;
      try { await API.post(`/visits/${visitId}/signature`, body); closeModal(); toast(t("saved")); navigate("visit", { id: visitId }); }
      catch (err) { alert(err.message); }
    });
  });
}

// ====================================================================
// Chart helpers — dependency-free SVG curves + CSS-3D pie/columns
// ====================================================================
let _chartSeq = 0;
const PALETTE = ["#16a34a", "#2563eb", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#65a30d", "#dc2626"];

function monthShort(m) {
  const d = new Date(m + "-01");
  return isNaN(d) ? m : d.toLocaleDateString(LANG === "ar" ? "ar" : "en", { month: "short" });
}

function _smoothPath(pts) {
  if (pts.length < 2) return pts.length ? `M${pts[0][0]},${pts[0][1]}` : "";
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2[0]},${p2[1]}`;
  }
  return d;
}

// labels: string[]; series: [{name,color,values:[]}]
function curveChart(labels, series, opts = {}) {
  const W = 640, H = 260, padL = 46, padR = 14, padT = 16, padB = 30;
  const n = labels.length;
  const max = Math.max(1, ...series.flatMap(s => s.values));
  const x = (i) => padL + (n <= 1 ? 0 : i * (W - padL - padR) / (n - 1));
  const y = (val) => H - padB - (val / max) * (H - padT - padB);
  const fmt = opts.money ? (v) => money(v) : (v) => Math.round(v);
  // gridlines + y labels
  let grid = "";
  for (let g = 0; g <= 4; g++) {
    const gy = padT + g * (H - padT - padB) / 4;
    const val = max * (1 - g / 4);
    grid += `<line x1="${padL}" y1="${gy}" x2="${W - padR}" y2="${gy}" stroke="#eef2f6" stroke-width="1"/>
      <text x="${padL - 8}" y="${gy + 4}" text-anchor="end" font-size="10" fill="#94a3b8">${opts.money ? Math.round(val) : Math.round(val)}</text>`;
  }
  // x labels
  let xlab = "";
  labels.forEach((l, i) => { if (i % 2 === 0 || i === n - 1) xlab += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle" font-size="10" fill="#94a3b8">${esc(l)}</text>`; });
  // series paths
  let paths = "", dots = "", defs = "";
  series.forEach((s, si) => {
    const id = `grad${_chartSeq++}`;
    const pts = s.values.map((v, i) => [x(i), y(v)]);
    const line = _smoothPath(pts);
    const area = `${line} L${x(n - 1)},${H - padB} L${x(0)},${H - padB} Z`;
    defs += `<linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="${s.color}" stop-opacity="0.32"/>
      <stop offset="100%" stop-color="${s.color}" stop-opacity="0"/></linearGradient>`;
    paths += `<path d="${area}" fill="url(#${id})"/><path d="${line}" fill="none" stroke="${s.color}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>`;
    pts.forEach(p => { dots += `<circle cx="${p[0]}" cy="${p[1]}" r="3" fill="#fff" stroke="${s.color}" stroke-width="2"/>`; });
  });
  const legend = series.map(s => `<span class="lg"><span class="sw" style="background:${s.color}"></span>${esc(s.name)}</span>`).join("");
  return `<div class="chart-legend">${legend}</div>
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" preserveAspectRatio="xMidYMid meet">
    <defs>${defs}</defs>${grid}${paths}${dots}${xlab}</svg>`;
}

// segments: [{label,value,color}] -> tilted 3D pie + legend
function pie3d(segments, opts = {}) {
  const total = segments.reduce((s, x) => s + x.value, 0);
  if (!total) return `<div class="empty">${t("none")}</div>`;
  let acc = 0; const parts = [];
  segments.forEach(s => {
    const a = (acc / total) * 360, b = ((acc + s.value) / total) * 360;
    parts.push(`${s.color} ${a.toFixed(2)}deg ${b.toFixed(2)}deg`); acc += s.value;
  });
  const legend = segments.map(s => `<div class="lg"><span class="sw" style="background:${s.color}"></span>${esc(s.label)} <strong>${s.value}</strong> <span class="muted">(${Math.round(s.value / total * 100)}%)</span></div>`).join("");
  return `<div class="pie3d-row">
    <div class="pie3d-stage"><div class="pie3d" style="background:conic-gradient(${parts.join(",")})"></div></div>
    <div class="pie-legend">${legend}</div></div>`;
}

// items: [{label,value,color}] -> 3D cylinder columns
function cols3d(items) {
  if (!items.length) return `<div class="empty">${t("none")}</div>`;
  const max = Math.max(1, ...items.map(i => i.value));
  const Hpx = 170;
  const cols = items.map((it, i) => {
    const h = Math.max(4, Math.round((it.value / max) * Hpx));
    const c = it.color || PALETTE[i % PALETTE.length];
    return `<div class="col3d-col">
      <div class="col3d-bars" style="height:${Hpx + 26}px">
        <div class="col3d-val">${it.value % 1 ? money(it.value) : it.value}</div>
        <div class="col3d" style="height:${h}px;--c:${c};--cl:${c}cc"><span class="col3d-cap" style="background:${c}"></span></div>
      </div>
      <div class="col3d-lbl">${esc(it.label)}</div></div>`;
  }).join("");
  return `<div class="cols3d">${cols}</div>`;
}

// Pest-activity trend section (device monitoring) — returns "" when the
// client has no devices, so it only appears where it's meaningful.
function pestTrendsBlock(tr) {
  if (!tr || !tr.totals || !tr.totals.devices) return "";
  const T = tr.totals;
  const labels = tr.months.map(m => monthShort(m.m));
  const statusColors = { ok: "#16a34a", needs_service: "#d97706", activity: "#dc2626", missing: "#64748b" };
  const sc = (val, label, icon, cls) => `<div class="stat-card ${cls}"><div class="sc-ic">${icon}</div><div><div class="v">${val}</div><div class="l">${label}</div></div></div>`;
  const typeItems = tr.by_type.map((r, i) => ({
    label: r.type ? t("type_" + r.type) : t("none"), value: r.detections,
    color: PALETTE[(i + 1) % PALETTE.length] }));
  const trend = curveChart(labels, [
    { name: t("inspections"), color: "#2563eb", values: tr.months.map(m => m.inspections) },
    { name: t("detections"), color: "#dc2626", values: tr.months.map(m => m.detections) }]);
  const hotRows = (tr.hotspots || []).map(h => `<tr>
      <td>${esc(h.label || "#" + h.id)}</td>
      <td>${esc(h.type ? t("type_" + h.type) : "—")}</td>
      <td>${esc(h.map_name || "—")}</td>
      <td class="num"><strong>${h.detections}</strong></td>
      <td><span style="display:inline-block;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:700;color:#fff;background:${statusColors[h.status] || "#64748b"}">${esc(t("mst_" + h.status))}</span></td>
    </tr>`).join("") || `<tr><td colspan="5" class="empty">${t("no_trend_data")}</td></tr>`;
  return `
    <div class="cards">
      ${sc(T.devices, t("total_devices"), "📍", "c-blue")}
      ${sc(T.inspections, t("monitoring_events"), "🔎", "c-teal")}
      ${sc(T.detections, t("activity_detections"), "🐭", "danger")}
      ${sc(T.active_now, t("mst_activity"), "⚠️", "c-amber")}</div>
    <div class="panel"><h3>📈 ${t("pest_trends")}</h3>${trend}</div>
    <div class="grid-2">
      <div class="panel"><h3>🐛 ${t("by_device_type")}</h3>${cols3d(typeItems)}</div>
      <div class="panel"><h3>🔥 ${t("device_hotspots")}</h3>
        <table><thead><tr><th>${t("marker_label")}</th><th>${t("marker_type")}</th><th>${t("map_name")}</th>
        <th class="num">${t("detections")}</th><th>${t("marker_status")}</th></tr></thead>
        <tbody>${hotRows}</tbody></table></div>
    </div>`;
}

// Materials-consumed rollup: total parts/chemicals used across all the
// client's visits (from the engineer service log). Returns "" when none.
function materialsBlock(materials) {
  if (!materials || !materials.length) return "";
  const cards = materials.map((m, i) =>
    `<div class="stat-card ${["c-blue", "c-green", "c-teal", "c-amber", "c-purple"][i % 5]}">
      <div class="sc-ic">📦</div><div><div class="v">${m.total}</div><div class="l">${esc(t(m.key))}</div></div></div>`).join("");
  return `<div class="cards">${cards}</div>`;
}

// ====================================================================
// Per-company analytics
// ====================================================================
async function viewClientAnalytics(v, arg) {
  const id = (arg && arg.id) || (role() === "client" ? API.user.client_id : null);
  if (!id) { v.innerHTML = `<div class="empty">${t("none")}</div>`; return; }
  const [c, a, tr] = await Promise.all([API.get("/clients/" + id),
    API.get(`/clients/${id}/analytics`),
    API.get(`/clients/${id}/pest-trends`).catch(() => null)]);
  const T = a.totals;
  const labels = a.months.map(m => monthShort(m.m));
  const statusColors = { scheduled: "#2563eb", in_progress: "#d97706", completed: "#16a34a", cancelled: "#dc2626" };
  const sevColors = { low: "#16a34a", medium: "#d97706", high: "#ef4444", critical: "#b91c1c" };
  const statusSeg = a.status.map(s => ({ label: t(statusKey(s.status)), value: s.cnt, color: statusColors[s.status] || "#64748b" }));
  const sevSeg = a.severity.map(s => ({ label: t("sev_" + s.severity), value: s.cnt, color: sevColors[s.severity] || "#64748b" }));
  const svcItems = a.services.map((s, i) => ({ label: localized(s, "name"), value: s.cnt, color: PALETTE[i % PALETTE.length] }));
  const chemItems = a.chemicals.map((ch, i) => ({ label: localized(ch, "name"), value: Math.round((ch.used || 0) * 100) / 100, color: PALETTE[(i + 3) % PALETTE.length] }));
  const sc = (val, label, icon, cls) => `<div class="stat-card ${cls}"><div class="sc-ic">${icon}</div><div><div class="v">${val}</div><div class="l">${label}</div></div></div>`;
  // Build chart markup once so the screen and the PDF export are identical.
  const parts = {
    cards: `<div class="cards">
      ${sc(T.visits, t("total_visits"), "🗓️", "c-blue")}
      ${sc(T.completed, t("visits_completed"), "✅", "c-green")}
      ${sc(money(T.invoiced), t("total_invoiced"), "🧾", "c-purple")}
      ${sc(money(T.paid), t("total_paid"), "💰", "c-teal")}
      ${sc(money(T.outstanding), t("outstanding"), "⚠️", "danger")}
      ${sc(T.contracts, t("active_contracts"), "🔁", "c-amber")}</div>`,
    revenue: curveChart(labels, [
      { name: t("invoiced"), color: "#16a34a", values: a.months.map(m => m.invoiced) },
      { name: t("paid"), color: "#2563eb", values: a.months.map(m => m.paid) }], { money: true }),
    visits: curveChart(labels, [{ name: t("nav_visits"), color: "#7c3aed", values: a.months.map(m => m.visits) }]),
    status: pie3d(statusSeg), severity: pie3d(sevSeg),
    services: cols3d(svcItems), chemicals: cols3d(chemItems),
    materials: materialsBlock(a.materials),
    pestTrends: pestTrendsBlock(tr),
  };
  v.innerHTML = `
    <div class="breadcrumb" id="bc">← 📁 ${esc(localized(c, "name"))}</div>
    <div class="page-head"><h2>📊 ${t("company_analytics")} — ${esc(localized(c, "name"))}</h2>
      <button class="btn sm" id="export-analytics">🖨️ ${t("export_pdf")}</button></div>
    ${parts.cards}
    <div class="panel"><h3>📈 ${t("revenue_trend")}</h3>${parts.revenue}</div>
    <div class="grid-2">
      <div class="panel"><h3>📉 ${t("visits_trend")}</h3>${parts.visits}</div>
      <div class="panel"><h3>🟢 ${t("status_distribution")}</h3>${parts.status}</div>
      <div class="panel"><h3>🧭 ${t("severity_distribution")}</h3>${parts.severity}</div>
      <div class="panel"><h3>🧰 ${t("service_mix")}</h3>${parts.services}</div>
    </div>
    <div class="panel"><h3>🧪 ${t("chemical_usage")}</h3>${parts.chemicals}</div>
    ${parts.materials ? `<div class="panel"><h3>📦 ${t("materials_consumed")}</h3>${parts.materials}</div>` : ""}
    ${parts.pestTrends ? `<div class="section-title" style="margin-top:8px"><h2>🐭 ${t("pest_trends")}</h2></div>${parts.pestTrends}` : ""}`;
  $("bc").addEventListener("click", () => navigate(role() === "client" ? "folder" : "client", { id }));
  $("export-analytics").addEventListener("click", () => printAnalytics(c, parts));
}

// ---- shared printable / PDF analytics report shell ----
// titleText = report title; subtitle = client name or scope; bodyHtml = panels.
function analyticsReportDoc(titleText, subtitle, bodyHtml) {
  const ar = LANG === "ar";
  const dir = ar ? "rtl" : "ltr";
  const S = SETTINGS || {};
  const compName = (ar ? S.company_name_ar : S.company_name_en) || S.company_name_en || "Company";
  const compAddr = (ar ? S.address_ar : S.address_en) || S.address_en || "";
  const logoHtml = S.logo ? `<img src="/uploads/${esc(S.logo)}" style="height:46px">` : `<div class="logo" style="width:46px;height:46px;font-size:24px;border-radius:12px">🐜</div>`;
  const today = new Date().toLocaleDateString(ar ? "ar" : "en-GB", { dateStyle: "medium" });
  const doc = `<!DOCTYPE html><html lang="${LANG}" dir="${dir}"><head><meta charset="utf-8">
    <title>${esc(titleText)}${subtitle ? " — " + esc(subtitle) : ""}</title>
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="/css/styles.css">
    <style>
      html,body{background:#fff}
      body{padding:28px;font-family:${ar ? "'Cairo'" : "'Inter'"},system-ui,sans-serif}
      /* force chart colors/gradients to print */
      *{ -webkit-print-color-adjust:exact !important; print-color-adjust:exact !important; }
      .rpt-top{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #16a34a;padding-bottom:16px;margin-bottom:18px}
      .rpt-top .co h1{margin:0;font-size:18px;color:#15803d}
      .rpt-top .co .muted{color:#64748b;font-size:12px;line-height:1.6}
      .rpt-title{text-align:${ar ? "left" : "right"}}
      .rpt-title h2{margin:0;font-size:22px;color:#16a34a}
      .rpt-title .sub{font-size:13px;font-weight:600;margin-top:4px}
      .rpt-title .date{font-size:11px;color:#64748b;margin-top:2px}
      .panel{break-inside:avoid;page-break-inside:avoid;box-shadow:none;border:1px solid #e6ecf2}
      .grid-2{grid-template-columns:1fr 1fr}
      .noprint{text-align:center;margin-bottom:16px}
      .pbtn{background:#16a34a;color:#fff;border:none;padding:10px 22px;border-radius:9px;font-size:14px;cursor:pointer}
      @media print{.noprint{display:none}body{padding:0}}
    </style></head><body>
    <div class="noprint"><button class="pbtn" onclick="window.print()">🖨️ ${esc(t("export_pdf"))}</button></div>
    <div class="rpt-top">
      <div style="display:flex;gap:12px;align-items:center">${logoHtml}
        <div class="co"><h1>${esc(compName)}</h1>
          <div class="muted">${esc(compAddr)}<br>${esc(S.phone || "")} · ${esc(S.email || "")}</div></div></div>
      <div class="rpt-title"><h2>${esc(titleText)}</h2>
        ${subtitle ? `<div class="sub">${esc(subtitle)}</div>` : ""}
        <div class="date">${esc(t("generated_on"))} ${today}</div></div>
    </div>
    ${bodyHtml}
    <script>window.onload=function(){setTimeout(function(){window.print()},600)}<\/script>
    </body></html>`;
  const w = window.open("", "_blank");
  if (!w) { alert("Please allow pop-ups to export the report."); return; }
  w.document.open(); w.document.write(doc); w.document.close();
}

function printAnalytics(c, parts) {
  const body = `${parts.cards}
    <div class="panel"><h3>📈 ${esc(t("revenue_trend"))}</h3>${parts.revenue}</div>
    <div class="grid-2">
      <div class="panel"><h3>📉 ${esc(t("visits_trend"))}</h3>${parts.visits}</div>
      <div class="panel"><h3>🟢 ${esc(t("status_distribution"))}</h3>${parts.status}</div>
      <div class="panel"><h3>🧭 ${esc(t("severity_distribution"))}</h3>${parts.severity}</div>
      <div class="panel"><h3>🧰 ${esc(t("service_mix"))}</h3>${parts.services}</div>
    </div>
    <div class="panel"><h3>🧪 ${esc(t("chemical_usage"))}</h3>${parts.chemicals}</div>
    ${parts.materials ? `<div class="panel"><h3>📦 ${esc(t("materials_consumed"))}</h3>${parts.materials}</div>` : ""}
    ${parts.pestTrends ? `<h2 style="margin:18px 0 6px">🐭 ${esc(t("pest_trends"))}</h2>${parts.pestTrends}` : ""}`;
  analyticsReportDoc(t("analytics_report"), localized(c, "name"), body);
}

// ====================================================================
// Site maps + device markers (traps, bait stations, monitors …)
// ====================================================================
const MARKER_TYPES = [
  { k: "bait_station", icon: "📦" }, { k: "rodent_trap", icon: "🪤" },
  { k: "insect_light", icon: "💡" }, { k: "monitor", icon: "🔎" },
  { k: "treatment", icon: "🧪" }, { k: "other", icon: "📍" },
];
const MARKER_STATUS = { ok: "#16a34a", needs_service: "#d97706", activity: "#dc2626", missing: "#64748b" };
function markerIcon(type) { const m = MARKER_TYPES.find(x => x.k === type); return m ? m.icon : "📍"; }

async function loadClientMaps(c) {
  const box = $("maps-box");
  if (!box) return;
  const maps = await API.get(`/clients/${c.id}/maps`);
  if (!maps.length) { box.innerHTML = `<div class="empty">${t("no_maps")}</div>`; return; }
  box.innerHTML = `<div class="map-thumbs">${maps.map(m => `
    <div class="map-thumb" data-map="${m.id}">
      <img src="/uploads/${esc(m.filename)}">
      <div class="mt-meta"><strong>${esc(m.name)}</strong>
        <span class="muted small">${m.site_name ? esc(m.site_name) + " · " : ""}${m.marker_count} ${t("devices")}</span></div>
      ${isManager() ? `<button class="rm" data-rmmap="${m.id}">✕</button>` : ""}
    </div>`).join("")}</div>`;
  box.querySelectorAll("[data-map]").forEach(el => el.addEventListener("click", (e) => {
    if (e.target.dataset.rmmap !== undefined) return;
    navigate("map", { id: el.dataset.map });
  }));
  box.querySelectorAll("[data-rmmap]").forEach(b => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (confirm(t("confirm_delete"))) { await API.del("/maps/" + b.dataset.rmmap); loadClientMaps(c); }
  }));
}

function uploadMapDialog(c) {
  const siteOpts = [{ v: "", l: t("none") }].concat((c.sites || []).map(s => ({ v: s.id, l: s.name })));
  openModal(t("upload_map"), `<form id="mapf">
    ${field(t("map_name"), "name", { value: "Site map" })}
    ${field(t("map_for_site"), "site_id", { options: siteOpts })}
    <div class="field"><label>${t("maps")}</label><input type="file" name="file" accept="image/*" required></div>
    <div class="form-actions"><button type="button" class="btn secondary" id="mapf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("upload_map")}</button></div></form>`, (root) => {
    $("mapf-x").addEventListener("click", closeModal);
    root.querySelector("#mapf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const file = root.querySelector("[name=file]").files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("client_id", c.id);
      fd.append("name", root.querySelector("[name=name]").value || "Site map");
      if (root.querySelector("[name=site_id]").value) fd.append("site_id", root.querySelector("[name=site_id]").value);
      fd.append("file", file);
      const res = await fetch("/api/maps", { method: "POST", headers: { Authorization: "Bearer " + API.token }, body: fd });
      if (res.ok) { closeModal(); toast(t("saved")); loadClientMaps(c); }
      else { const d = await res.json().catch(() => null); alert((d && d.error) || "Upload failed"); }
    });
  });
}

function markerPin(mk) {
  return `<button class="map-pin" style="left:${mk.x}%;top:${mk.y}%;--mc:${MARKER_STATUS[mk.status] || "#64748b"}"
    data-marker="${mk.id}" title="${esc(mk.label || "")}">
    <span class="pin-ic">${markerIcon(mk.type)}</span>${mk.label ? `<span class="pin-lbl">${esc(mk.label)}</span>` : ""}</button>`;
}
function mapLegend() {
  const types = MARKER_TYPES.map(x => `<span class="lg">${x.icon} ${t("type_" + x.k)}</span>`).join("");
  const stat = Object.entries(MARKER_STATUS).map(([k, col]) => `<span class="lg"><span class="dot" style="background:${col}"></span>${t("mst_" + k)}</span>`).join("");
  return types + ` &nbsp;|&nbsp; ` + stat;
}

async function viewMap(v, arg) {
  const map = await API.get("/maps/" + arg.id);
  const canEdit = role() !== "client";
  const typeOpts = MARKER_TYPES.map(x => `<option value="${x.k}">${x.icon} ${t("type_" + x.k)}</option>`).join("");
  v.innerHTML = `
    <div class="breadcrumb" id="bc">← 📁 ${esc(localized(map, "client"))}</div>
    <div class="page-head"><h2>🗺️ ${esc(map.name)}${map.site_name ? ` — ${esc(map.site_name)}` : ""}
      <span class="muted small">(${map.markers.length} ${t("total_devices")})</span></h2>
      ${canEdit ? `<div style="display:flex;gap:8px;align-items:center">
        <select id="mk-type" class="toolbar-select">${typeOpts}</select>
        <button class="btn sm" id="place-btn">➕ ${t("add_device")}</button></div>` : ""}</div>
    <div class="map-legend-bar">${mapLegend()}</div>
    <div class="map-stage" id="stage">
      <img id="map-img" src="/uploads/${esc(map.filename)}">
      ${map.markers.map(markerPin).join("")}
    </div>`;
  $("bc").addEventListener("click", () => navigate("client", { id: map.client_id }));
  const stage = $("stage");
  let placing = false;
  const setPlacing = (p) => {
    placing = p; stage.classList.toggle("placing", p);
    if ($("place-btn")) $("place-btn").textContent = p ? `✋ ${t("done_placing")}` : `➕ ${t("add_device")}`;
    if (p) toast(t("click_to_place"));
  };
  if ($("place-btn")) $("place-btn").addEventListener("click", () => setPlacing(!placing));
  stage.addEventListener("click", (e) => {
    if (!placing) return;
    const img = $("map-img"), r = img.getBoundingClientRect();
    const x = Math.min(100, Math.max(0, ((e.clientX - r.left) / r.width) * 100));
    const y = Math.min(100, Math.max(0, ((e.clientY - r.top) / r.height) * 100));
    setPlacing(false);
    markerForm(map.id, { x: +x.toFixed(2), y: +y.toFixed(2), type: $("mk-type").value, status: "ok" }, false);
  });
  v.querySelectorAll(".map-pin").forEach(p => p.addEventListener("click", (e) => {
    e.stopPropagation();
    const mk = map.markers.find(x => x.id == p.dataset.marker);
    if (canEdit) markerForm(map.id, mk, true); else showMarkerInfo(mk);
  }));
}

function markerForm(mapId, mk, isEdit) {
  const typeOpts = MARKER_TYPES.map(x => ({ v: x.k, l: x.icon + " " + t("type_" + x.k) }));
  const statusOpts = ["ok", "needs_service", "activity", "missing"].map(s => ({ v: s, l: t("mst_" + s) }));
  openModal(isEdit ? t("edit") : t("add_device"), `<form id="mkf">
    ${field(t("marker_type"), "type", { options: typeOpts, value: mk.type })}
    ${field(t("marker_label"), "label", { value: mk.label })}
    ${field(t("marker_status"), "status", { options: statusOpts, value: mk.status })}
    ${field(t("marker_notes"), "notes", { textarea: true, value: mk.notes })}
    <div class="form-actions">${isEdit ? `<button type="button" class="btn danger" id="mk-del" style="margin-inline-end:auto">${t("delete")}</button>` : ""}
      <button type="button" class="btn secondary" id="mk-x">${t("cancel")}</button>
      <button class="btn" type="submit">${t("save_device")}</button></div></form>`, (root) => {
    $("mk-x").addEventListener("click", closeModal);
    if ($("mk-del")) $("mk-del").addEventListener("click", async () => {
      await API.del("/markers/" + mk.id); closeModal(); navigate("map", { id: mapId });
    });
    root.querySelector("#mkf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = formData(root);
      try {
        if (isEdit) await API.put("/markers/" + mk.id, d);
        else await API.post(`/maps/${mapId}/markers`, { ...d, x: mk.x, y: mk.y });
        closeModal(); navigate("map", { id: mapId });
      } catch (err) { alert(err.message); }
    });
  });
}
function showMarkerInfo(mk) {
  openModal(markerIcon(mk.type) + " " + t("type_" + mk.type), `<div class="kv">
    <div>${t("marker_label")}</div><div>${esc(mk.label || "—")}</div>
    <div>${t("marker_status")}</div><div style="color:${MARKER_STATUS[mk.status]}">${t("mst_" + mk.status)}</div>
    <div>${t("marker_notes")}</div><div>${esc(mk.notes || "—")}</div></div>
    <div class="form-actions"><button class="btn" id="mi-x">${t("close")}</button></div>`, () => {
    $("mi-x").addEventListener("click", closeModal);
  });
}

// ====================================================================
boot();
