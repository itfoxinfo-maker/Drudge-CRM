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

// A write that was queued offline returns {__queued:true} instead of the saved
// record — it has no server id yet, and the usual follow-up (refresh GET +
// navigate to the new/updated record) would fail offline and mask the success.
// In that case just close the form; the oq-queued toast ("saved offline — will
// sync") already confirms it. Returns true when the caller should stop here.
function handledOffline(saved, optimisticEl) {
  if (saved && saved.__queued) {
    // Offline delete: drop the row immediately (no refresh possible) so the UI
    // reflects the queued removal; the delete replays on reconnect.
    if (optimisticEl) optimisticEl.remove();
    closeModal();
    return true;
  }
  return false;
}

function role() { return API.user ? API.user.role : null; }

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
  if (can("users.view")) { try { cache.agents = await API.get("/agents"); } catch (e) {} }
  if (can("clients.view")) { try { cache.clients = await API.get("/clients"); } catch (e) {} }
  if (can("chemicals.view")) { try { cache.chemicals = await API.get("/chemicals"); } catch (e) {} }
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

// Wrap data tables in a horizontal-scroll box so a wide table scrolls inside
// itself instead of pushing the whole page sideways — WITHOUT changing the
// table's layout (so narrow tables stay full-width, no blank gaps). Runs on
// every render via a MutationObserver on the view + modal containers.
function installTableScroll() {
  const wrap = (t) => {
    if (!t.parentNode || (t.parentNode.classList && t.parentNode.classList.contains("table-scroll"))) return;
    const box = document.createElement("div");
    box.className = "table-scroll";
    t.parentNode.insertBefore(box, t);
    box.appendChild(t);
  };
  const scan = (root) => { if (root.querySelectorAll) root.querySelectorAll("table").forEach(wrap); };
  const obs = new MutationObserver((muts) => {
    for (const m of muts) for (const n of m.addedNodes) {
      if (n.nodeType !== 1) continue;
      if (n.tagName === "TABLE") wrap(n); else scan(n);
    }
  });
  ["view", "modal-body"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) { scan(el); obs.observe(el, { childList: true, subtree: true }); }
  });
}

async function boot() {
  setLang(LANG);
  applyStaticLabels();
  installTableScroll();
  if (API.token && API.user) {
    // Refresh the profile so the resolved permission map is always current.
    try {
      const me = await API.get("/auth/me");
      if (me) API.setAuth(API.token, me);
      showApp();
    } catch (e) {
      // Offline / network error: keep the cached session so field agents can
      // keep working. A real 401 clears auth + reloads inside API.request.
      if (API.token && API.user) showApp();
      else { API.clearAuth(); showLogin(); }
    }
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
  initOfflineUI();
  // A QR deep link (/scan/<token>) opens the scanned device straight away.
  const scanTok = scanTokenFromUrl();
  if (scanTok) { navigate("scan", { token: scanTok }); return; }
  navigate("dashboard");
  promptDraftReports();
}

// Pull the device code/token out of a /scan/<x> deep link, if we're on one.
// Matches both printed device codes (LIT0001) and legacy marker hex tokens.
function scanTokenFromUrl() {
  const m = location.pathname.match(/^\/scan\/([A-Za-z0-9]+)/);
  return m ? m[1] : null;
}

// On login, if the user has unfinished (draft) reports, pop up a reminder to
// complete & save them. Drafts are auto-saved server-side, so this fires when an
// agent logged out mid-report.
async function promptDraftReports() {
  if (role() === "client") return;
  try {
    const d = await API.get("/reports/drafts");
    const items = (d && d.items) || [];
    if (!items.length) return;
    const rows = items.map(r => `<div class="notif" data-visit="${r.visit_id}" style="cursor:pointer">
        <div class="nt">${esc(localized(r, "name"))}</div>
        <div class="nb muted small">${r.scheduled_start ? fmtDateTime(r.scheduled_start) : ""}${role() !== "agent" && r.agent_name ? " · " + esc(r.agent_name) : ""}</div>
      </div>`).join("");
    openModal(`⚠ ${t("drafts_pending_title")}`, `<p class="muted">${t("drafts_pending_body")}</p>${rows}`, (body) => {
      body.querySelectorAll("[data-visit]").forEach(el => el.addEventListener("click", () => {
        closeModal(); navigate("visit", { id: el.dataset.visit });
      }));
    });
  } catch (e) { /* non-blocking */ }
}

// ---- offline status pill + pending-sync badge ----
function initOfflineUI() {
  const el = $("offline-status");
  if (!el || el.dataset.wired) {
    if (el) renderOfflineStatus();
    if (navigator.onLine && window.OfflineQueue) window.OfflineQueue.flush();
    return;
  }
  el.dataset.wired = "1";
  el.addEventListener("click", () => { if (window.OfflineQueue) window.OfflineQueue.flush(); });
  window.addEventListener("online", renderOfflineStatus);
  window.addEventListener("offline", renderOfflineStatus);
  window.addEventListener("oq-change", renderOfflineStatus);
  window.addEventListener("oq-queued", (e) => toast(t(e.detail && e.detail.method === "DELETE" ? "deleted_offline" : "saved_offline")));
  window.addEventListener("oq-synced", (e) => { toast(`${e.detail.synced} ${t("synced_ok")}`); renderOfflineStatus(); });
  renderOfflineStatus();
  if (navigator.onLine && window.OfflineQueue) window.OfflineQueue.flush();
}

async function renderOfflineStatus() {
  const el = $("offline-status");
  if (!el) return;
  let count = 0;
  try { count = window.OfflineQueue ? await window.OfflineQueue.count() : 0; } catch (e) {}
  if (!navigator.onLine) {
    el.className = "offline-status off";
    el.textContent = "⚠ " + t("offline") + (count ? ` · ${count}` : "");
  } else if (count > 0) {
    el.className = "offline-status pend";
    el.textContent = `⟳ ${count} ${t("to_sync")}`;
  } else {
    el.className = "offline-status hidden";
  }
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
    if (can("visits.view")) items.push({ k: "requests", i: "📨", t: "nav_requests" });
    if (can("contracts.view")) items.push({ k: "contracts", i: "🔁", t: "nav_contracts" });
    if (can("invoices.view")) items.push({ k: "invoices", i: "💳", t: "nav_invoices" });
    if (can("certificates.view")) items.push({ k: "certificates", i: "📄", t: "nav_certificates" });
    items.push({ k: "folder", i: "📁", t: "company_folder" });
  } else {
    if (role() === "agent") items.push({ k: "myday", i: "🧭", t: "nav_myday" });
    if (can("clients.view")) items.push({ k: "clients", i: "🏢", t: "nav_clients" });
    if (can("clients.view")) items.push({ k: "locations", i: "📍", t: "nav_locations" });
    if (can("visits.view")) items.push({ k: "schedule", i: "🗓️", t: "nav_schedule" });
    if (can("visits.edit")) items.push({ k: "dispatch", i: "🚚", t: "nav_dispatch" });
    if (can("visits.view")) items.push({ k: "requests", i: "📨", t: "nav_requests" });
    if (can("visits.view")) items.push({ k: "reports", i: "📋", t: "nav_reports" });
    if (can("calendar.view")) items.push({ k: "calendar", i: "📅", t: "nav_calendar" });
    if (can("contracts.view")) items.push({ k: "contracts", i: "🔁", t: "nav_contracts" });
    if (can("chemicals.view")) items.push({ k: "chemicals", i: "🧪", t: "nav_chemicals" });
    if (can("issues.view")) items.push({ k: "issues", i: "📦", t: "nav_issues" });
    if (can("maps.view")) items.push({ k: "devices", i: "🏷️", t: "nav_devices" });
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
  // Once the user leaves a scanned-device deep link, drop /scan/<token> from the
  // address bar so a reload returns to the app rather than re-opening the device.
  if (view !== "scan" && location.pathname.startsWith("/scan/")) {
    try { history.replaceState({}, "", "/"); } catch (e) { /* ignore */ }
  }
  $("nav").querySelectorAll(".nav-item").forEach(a =>
    a.classList.toggle("active", a.dataset.view === view));
  const v = $("view");
  v.innerHTML = `<div class="empty">${t("loading")}</div>`;
  try {
    if (view === "dashboard") await viewDashboard(v);
    else if (view === "clients") await viewClients(v);
    else if (view === "locations") await viewLocations(v);
    else if (view === "client" || view === "folder") await viewClientFolder(v, arg);
    else if (view === "client-analytics") await viewClientAnalytics(v, arg);
    else if (view === "map") await viewMap(v, arg);
    else if (view === "devices") await viewDevices(v);
    else if (view === "scan") await viewScan(v, arg);
    else if (view === "schedule" || view === "visits") await viewSchedule(v);
    else if (view === "dispatch") await viewDispatch(v, arg);
    else if (view === "requests") await viewRequests(v);
    else if (view === "myday") await viewMyDay(v, arg);
    else if (view === "reports") await viewReports(v);
    else if (view === "report") await viewReportDoc(v, arg);
    else if (view === "visit") await viewVisit(v, arg);
    else if (view === "chemicals") await viewChemicals(v);
    else if (view === "issues") await viewIssues(v);
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

// Collapsible sidebar — toggled from the topbar, remembered across sessions.
function applySidebarState() {
  $("app").classList.toggle("nav-collapsed", localStorage.getItem("navCollapsed") === "1");
}
if ($("nav-toggle")) $("nav-toggle").addEventListener("click", () => {
  const collapsed = $("app").classList.toggle("nav-collapsed");
  localStorage.setItem("navCollapsed", collapsed ? "1" : "0");
});
applySidebarState();

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
// Fill a <select name="site_id"> with the client's locations. Sets
// dataset.hasSites="1" when the client has any (so the form can require one).
// firstLabel is the placeholder shown for the empty option.
async function loadSiteOptions(clientId, selectEl, selected, firstLabel) {
  if (!selectEl) return;
  if (!clientId) { selectEl.innerHTML = `<option value="">${t("none")}</option>`; selectEl.dataset.hasSites = ""; return; }
  selectEl.innerHTML = `<option value="">${t("loading")}</option>`;
  let sites = [];
  try { const c = await API.get("/clients/" + clientId); sites = c.sites || []; } catch (e) {}
  selectEl.dataset.hasSites = sites.length ? "1" : "";
  const ph = firstLabel || (sites.length ? t("select_location") : t("none"));
  const opts = [{ v: "", l: ph }].concat(sites.map(s => ({ v: s.id, l: s.name })));
  selectEl.innerHTML = opts.map(o =>
    `<option value="${esc(o.v)}" ${String(o.v) === String(selected || "") ? "selected" : ""}>${esc(o.l)}</option>`).join("");
}
function statusBadge(s) { return `<span class="badge b-${s}">${t(statusKey(s))}</span>`; }
function statusKey(s) {
  const map = { scheduled: "st_scheduled", in_progress: "st_in_progress", completed: "st_completed",
    cancelled: "st_cancelled", draft: "inv_draft", sent: "inv_sent", paid: "inv_paid",
    overdue: "inv_overdue", accepted: "accepted", active: "active", inactive: "status" };
  return map[s] || s;
}

// Human label for a required report field returned in a report_incomplete error.
function reportFieldLabel(key) {
  const map = { customer_signature: "customer_sig", technician_signature: "technician_sig" };
  return t(map[key] || key);
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
  const isClient = d.role === "client";
  v.innerHTML = `<div class="page-head"><h2>${t("welcome")}, ${esc(API.user.full_name)}</h2>
    ${isClient ? `<button class="btn" id="dash-req">+ ${t("request_visit")}</button>` : ""}</div>
    <div class="cards">${cards}</div>
    ${d.cockpit ? cockpitSection(d.cockpit) : ""}
    ${(!isClient && d.devices && d.devices.total) ? deviceDashStrip(d.devices) : ""}
    ${isClient ? `<div id="dash-sla"></div>` : ""}
    <div class="panel"><h3>${t("nav_schedule")}</h3><div id="dash-visits">${t("loading")}</div></div>`;
  if ($("dash-req")) $("dash-req").addEventListener("click", requestForm);
  if (isClient) loadSlaStrip("dash-sla");
  // upcoming visits table
  const visits = await API.get("/visits");
  const vlist = Array.isArray(visits) ? visits : (visits.items || []);
  $("dash-visits").innerHTML = visitsTable(vlist.slice(0, 8));
  wireVisitRows($("dash-visits"));
}

// QR device fleet health strip on the staff dashboard: fleet size, this
// month's service coverage, devices needing service, activity detections.
function deviceDashStrip(dv) {
  const card = (val, label, icon, cls) =>
    `<div class="stat-card ${cls}"><div class="sc-ic">${icon}</div><div><div class="v">${val}</div><div class="l">${label}</div></div></div>`;
  return `<div class="section-title" style="margin-top:8px"><h3>🏷️ ${t("nav_devices")}</h3></div>
    <div class="cards">
      ${card(dv.total, t("total_devices"), "📍", "c-blue")}
      ${card(dv.coverage != null ? dv.coverage + "%" : "—", t("coverage_month"), "✅", (dv.coverage != null && dv.coverage < 60) ? "warn" : "c-green")}
      ${card(dv.stale || 0, t("overdue_devices"), "⏰", dv.stale > 0 ? "danger" : "c-green")}
      ${card(dv.needs_service, t("mst_needs_service"), "🛠️", dv.needs_service > 0 ? "warn" : "c-green")}
      ${card(dv.activity_month, t("activity_detections"), "🐭", dv.activity_month > 0 ? "danger" : "c-green")}</div>`;
}

// Owner cockpit: revenue / overdue billing / SLA health KPI strip + a
// per-technician utilization panel for the current month.
function cockpitSection(c) {
  const prev = Number(c.revenue_prev) || 0, cur = Number(c.revenue_month) || 0;
  const delta = cur - prev;
  const pct = prev ? Math.round(delta * 100 / prev) : (cur ? 100 : 0);
  const arrow = delta > 0 ? "▲" : (delta < 0 ? "▼" : "—");
  const trendCls = delta > 0 ? "up" : (delta < 0 ? "down" : "");
  const kpi = (val, label, icon, cls, extra = "") =>
    `<div class="stat-card ${cls}"><div class="sc-ic">${icon}</div><div>
      <div class="v">${val}</div><div class="l">${label}</div>${extra}</div></div>`;
  const cards =
    kpi(money(c.revenue_month), t("dash_revenue_month"), "💵", "c-green",
        `<div class="sc-trend ${trendCls}">${arrow} ${Math.abs(pct)}% ${t("vs_last_month")}</div>`) +
    kpi(c.overdue_invoices, t("dash_overdue_invoices"), "⏰",
        c.overdue_invoices > 0 ? "danger" : "c-green",
        `<div class="sc-trend">${money(c.overdue_amount)}</div>`) +
    kpi(c.sla.overdue, t("dash_sla_overdue"), "🚨", c.sla.overdue > 0 ? "danger" : "c-green") +
    kpi(c.sla.due_soon, t("dash_sla_due_soon"), "🕒", c.sla.due_soon > 0 ? "warn" : "c-green");
  const u = c.utilization || [];
  const rows = u.length ? u.map(a => `<tr>
      <td>${esc(a.name)}</td>
      <td class="num">${a.completed}/${a.total}</td>
      <td><div class="util-bar"><span style="width:${a.rate}%"></span></div></td>
      <td class="num">${a.rate}%</td></tr>`).join("")
    : `<tr><td colspan="4" class="empty">${t("none")}</td></tr>`;
  return `<div class="cards cockpit-kpis">${cards}</div>
    <div class="panel"><h3>${t("tech_utilization")} <span class="muted">· ${t("this_month")}</span></h3>
      <table class="util-table"><thead><tr>
        <th>${t("nav_agents")}</th><th class="num">${t("col_completed_assigned")}</th>
        <th>${t("rate")}</th><th class="num">%</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}

// ====================================================================
// Clients list
// ====================================================================
async function viewClients(v) {
  v.innerHTML = `<div class="page-head"><h2>${t("clients_title")}</h2>
    ${can("clients.create") ? `<button class="btn" id="add-client">+ ${t("new_client")}</button>` : ""}</div>
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
        if (handledOffline(saved)) return;
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
        ${can("clients.edit") ? `<button class="btn secondary sm" id="edit-client">${t("edit")}</button>` : ""}</div></div>
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
      ${fin ? `<div class="panel"><h3>${t("finance")}</h3>
        <div class="cards" style="grid-template-columns:1fr 1fr;">
          <div class="stat-card"><div class="v" style="font-size:18px">${money(fin.total_invoiced)}</div><div class="l">${t("total_invoiced")}</div></div>
          <div class="stat-card"><div class="v" style="font-size:18px">${money(fin.total_paid)}</div><div class="l">${t("total_paid")}</div></div>
          <div class="stat-card danger"><div class="v" style="font-size:18px">${money(fin.outstanding)}</div><div class="l">${t("outstanding")}</div></div>
        </div>
        <table style="margin-top:8px"><thead><tr><th>${t("invoice_no")}</th><th>${t("total")}</th><th>${t("status")}</th></tr></thead>
        <tbody>${fin.invoices.map(i => `<tr class="clickable" data-inv="${i.id}"><td>${esc(i.number)}</td><td>${money(i.total)}</td><td>${statusBadge(i.status)}</td></tr>`).join("") || `<tr><td colspan="3" class="empty">${t("none")}</td></tr>`}</tbody></table>
      </div>` : ""}
    </div>

    ${can("clients.edit") ? `<div class="panel"><div class="section-title"><h3>${t("sites")}</h3><button class="btn sm" id="add-site">+ ${t("add_site")}</button></div>
      <table><thead><tr><th>${t("site_name")}</th><th>${t("address_en")}</th><th>${t("area")}</th><th></th></tr></thead>
      <tbody id="sites-body">${(c.sites || []).map(s => `<tr><td>${esc(s.name)}</td><td>${esc(s.address)}</td><td>${esc(s.area)}</td>
        <td><button class="link-btn danger sm" data-rmsite="${s.id}">${t("delete")}</button></td></tr>`).join("") || `<tr><td colspan="4" class="empty">${t("none")}</td></tr>`}</tbody></table></div>` : ""}

    <div class="panel"><div class="section-title"><h3>${t("recent_visits")}</h3>
      ${can("visits.create") ? `<button class="btn sm" id="add-visit">+ ${t("new_visit")}</button>` : ""}</div>
      ${visitsTable(c.recent_visits)}</div>

    <div class="panel"><div class="section-title"><h3>🗺️ ${t("maps")}</h3>
      ${can("maps.create") ? `<button class="btn sm" id="add-map">📤 ${t("upload_map")}</button>` : ""}</div>
      <div id="maps-box">${t("loading")}</div></div>

    <div class="panel"><div class="section-title"><h3>${t("attachments")}</h3>
      ${role() !== "client" ? `<button class="btn sm" id="add-photo">📎 ${t("add_attachment")}</button>` : ""}</div>
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
    if (confirm(t("confirm_delete"))) { const r = await API.del("/sites/" + b.dataset.rmsite); if (handledOffline(r, b.closest("tr"))) return; navigate("client", { id: c.id }); }
  }));
  renderPhotos("client", c.id, c.photos);
  if ($("add-photo")) $("add-photo").addEventListener("click", () => uploadPhotoDialog("client", c.id, () => navigate("client", { id: c.id })));
}

function siteForm(clientId, site, after) {
  const s = site || {};
  const isEdit = !!site;
  openModal(isEdit ? t("edit") : t("add_site"), `<form id="sf">
    ${field(t("site_name"), "name", { value: s.name })}
    ${field(t("address_en"), "address", { value: s.address })}
    ${field(t("area"), "area", { value: s.area })}
    <div class="field"><label>${esc(t("coordinates"))} <span class="muted small">(${esc(t("coords_hint"))})</span></label>
      <div style="display:flex;gap:6px">
        <input type="text" name="lat" placeholder="lat" value="${esc(s.lat ?? "")}" style="flex:1" />
        <input type="text" name="lng" placeholder="lng" value="${esc(s.lng ?? "")}" style="flex:1" />
        <button type="button" class="btn secondary sm" id="sf-geo">📍 ${esc(t("use_my_location"))}</button>
      </div>
      <input type="text" id="sf-paste" placeholder="${esc(t("paste_maps_link"))}" style="margin-top:6px;width:100%" /></div>
    <div class="form-actions"><button type="button" class="btn secondary" id="sf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    $("sf-x").addEventListener("click", closeModal);
    const latI = root.querySelector("[name=lat]"), lngI = root.querySelector("[name=lng]");
    // paste a "lat,lng" or a Google Maps link -> fill the coordinate fields
    root.querySelector("#sf-paste").addEventListener("input", (e) => {
      const ll = parseLatLng(e.target.value);
      if (ll) { latI.value = ll[0]; lngI.value = ll[1]; }
    });
    $("sf-geo").addEventListener("click", () => {
      if (!navigator.geolocation) { alert(t("geo_unsupported")); return; }
      $("sf-geo").textContent = "…";
      navigator.geolocation.getCurrentPosition(
        (p) => { latI.value = p.coords.latitude.toFixed(6); lngI.value = p.coords.longitude.toFixed(6); $("sf-geo").textContent = "📍 " + t("use_my_location"); },
        () => { alert(t("geo_failed")); $("sf-geo").textContent = "📍 " + t("use_my_location"); });
    });
    root.querySelector("#sf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = formData(root);
      const saved = isEdit ? await API.put(`/sites/${s.id}`, d) : await API.post(`/clients/${clientId}/sites`, d);
      if (handledOffline(saved)) return;
      closeModal();
      if (after) after(); else navigate("client", { id: clientId });
    });
  });
}

// Parse "lat,lng" or a Google Maps URL into [lat, lng] (mirrors the server).
function parseLatLng(text) {
  if (!text) return null;
  const pats = [/@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)/, /[?&]q=(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)/, /^\s*(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*$/];
  for (const p of pats) {
    const m = text.match(p);
    if (m) { const la = +m[1], ln = +m[2]; if (la >= -90 && la <= 90 && ln >= -180 && ln <= 180) return [la, ln]; }
  }
  return null;
}

// ---- photos ----
// Classify an attachment by its file extension so non-images render as a
// download link rather than a broken <img>.
function attachKind(name) {
  const ext = (name || "").toLowerCase().split(".").pop();
  if (["jpg", "jpeg", "png", "gif", "webp"].includes(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (ext === "xls" || ext === "xlsx") return "excel";
  return "file";
}
// Render images + document attachments into a grid (defaults to #photos; pass
// containerId to target another grid, e.g. report attachments).
function renderPhotos(entityType, entityId, photos, containerId) {
  const box = $(containerId || "photos");
  if (!box) return;
  if (!photos || !photos.length) { box.innerHTML = `<div class="empty">${t("no_photos")}</div>`; return; }
  const canRemove = role() !== "client";
  box.innerHTML = photos.map(p => {
    const rm = canRemove ? `<button class="rm" data-rmphoto="${p.id}">✕</button>` : "";
    const bp = Number(p.is_business_plan) ? `<span class="badge b-active" style="margin-inline-start:4px">${t("business_plan")}</span>` : "";
    const cap = `<div class="cap">${esc(p.caption || p.original_name || "")}${bp}</div>`;
    if (attachKind(p.filename) === "image") {
      return `<div class="photo-item"><img src="/uploads/${esc(p.filename)}" alt="${esc(p.caption || "")}" />${rm}${cap}</div>`;
    }
    const icon = attachKind(p.filename) === "pdf" ? "📄" : attachKind(p.filename) === "excel" ? "📊" : "📎";
    return `<div class="photo-item file-item">
      <a class="file-link" href="/uploads/${esc(p.filename)}" target="_blank" rel="noopener" download="${esc(p.original_name || "")}">
        <span class="file-icon">${icon}</span><span class="file-name">${esc(p.original_name || p.filename)}</span></a>${rm}${cap}</div>`;
  }).join("");
  box.querySelectorAll("[data-rmphoto]").forEach(b => b.addEventListener("click", async () => {
    if (confirm(t("confirm_delete"))) { const r = await API.del("/photos/" + b.dataset.rmphoto); if (handledOffline(r, b.closest(".photo-item"))) return; navigate(currentView, { id: entityId }); }
  }));
}
function uploadPhotoDialog(entityType, entityId, after) {
  openModal(t("add_attachment"), `<form id="pf">
    <div class="field"><label>${t("files")}</label>
      <input type="file" name="file" accept="image/*,.pdf,.xls,.xlsx" multiple required />
      <div class="muted small">${t("attach_hint")}</div></div>
    ${field(t("comment"), "caption")}
    <div class="field"><label style="display:flex;align-items:center;gap:8px;cursor:pointer">
      <input type="checkbox" name="business_plan" style="width:auto"> ${t("business_plan")}</label></div>
    <div class="form-actions"><button type="button" class="btn secondary" id="pf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("upload")}</button></div></form>`, (root) => {
    $("pf-x").addEventListener("click", closeModal);
    root.querySelector("#pf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const files = Array.from(root.querySelector("[name=file]").files);
      if (!files.length) return;
      const caption = root.querySelector("[name=caption]").value;
      const businessPlan = root.querySelector("[name=business_plan]").checked;
      try {
        let queued = false;
        for (const file of files) {
          const saved = await API.uploadPhoto(entityType, entityId, file, caption, businessPlan);
          if (saved && saved.__queued) queued = true;
        }
        closeModal();
        if (queued) return;  // queued offline; oq-queued toast already shown, can't refresh
        toast(t("saved")); after && after();
      } catch (err) { alert(err.message); }
    });
  });
}

// ====================================================================
// Schedule / visits
// ====================================================================
function visitsTable(visits) {
  if (!visits || !visits.length) return `<div class="empty">${t("none")}</div>`;
  return `<table><thead><tr><th>${t("scheduled_start")}</th><th>${t("client")}</th>
    <th>${t("location_lbl")}</th><th>${t("service")}</th><th>${t("agent")}</th><th>${t("status")}</th></tr></thead>
    <tbody>${visits.map(v => `<tr class="clickable" data-visit="${v.id}">
      <td>${fmtDateTime(v.scheduled_start)}</td>
      <td>${esc(localized(v, "client") || "")}</td>
      <td>${esc(v.site_name || v.location || "—")}</td>
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
    ${can("visits.create") ? `<button class="btn" id="add-visit">+ ${t("new_visit")}</button>` : ""}</div>
    <div class="toolbar">
      <label>${t("status")}: <select id="f-status">${statuses.map(s => `<option value="${s}">${s ? t(statusKey(s)) : t("all")}</option>`).join("")}</select></label>
      ${can("users.view") ? `<label>${t("agent")}: <select id="f-agent"><option value="">${t("all")}</option>${cache.agents.map(a => `<option value="${a.id}">${esc(a.full_name)}</option>`).join("")}</select></label>` : ""}
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

// ====================================================================
// Smart Dispatch — drag-drop board, geographic route optimization, SLA
// ====================================================================
function shiftDate(d, delta) { const x = new Date(d + "T00:00:00"); x.setDate(x.getDate() + delta); return ymd(x); }

async function viewDispatch(v, arg) {
  if (!cache.agents.length) { try { cache.agents = await API.get("/agents"); } catch (e) {} }
  const date = (arg && arg.date) || ymd(new Date());
  v.innerHTML = `<div class="page-head"><h2>🚚 ${t("nav_dispatch")}</h2>
    <div style="display:flex;gap:8px;align-items:center">
      <button class="btn secondary sm" id="dp-prev">${t("prev")}</button>
      <input type="date" id="dp-date" value="${date}">
      <button class="btn secondary sm" id="dp-next">${t("next")}</button>
      <button class="btn secondary sm" id="dp-today">${t("today")}</button></div></div>
    <div id="dp-sla"></div>
    <p class="muted small">${t("dispatch_hint")}</p>
    <div id="dp-board" class="dispatch-board">${t("loading")}</div>`;
  $("dp-prev").addEventListener("click", () => navigate("dispatch", { date: shiftDate(date, -1) }));
  $("dp-next").addEventListener("click", () => navigate("dispatch", { date: shiftDate(date, 1) }));
  $("dp-today").addEventListener("click", () => navigate("dispatch"));
  $("dp-date").addEventListener("change", (e) => navigate("dispatch", { date: e.target.value }));
  loadSlaStrip();
  await renderBoard(date);
}

async function loadSlaStrip(targetId = "dp-sla") {
  const box = $(targetId);
  if (!box) return;
  let sla;
  try { sla = await API.get("/dispatch/sla"); } catch (e) { return; }
  const c = sla.counts;
  const chip = (n, cls, label) => `<span class="sla-chip ${cls}">${n} ${label}</span>`;
  const rows = sla.items.filter(r => r.status !== "ok");
  box.innerHTML = `<div class="panel sla-strip">
    <div class="sla-head"><strong>📡 ${t("sla_tracking")}</strong>
      ${chip(c.overdue, "sla-overdue", t("sla_overdue"))}
      ${chip(c.due_soon, "sla-due", t("sla_due_soon"))}
      ${chip(c.ok, "sla-ok", t("sla_on_track"))}
      ${rows.length ? `<button class="link-btn sm" id="sla-toggle">${t("view")}</button>` : ""}</div>
    <div id="sla-list" class="hidden">${rows.map(r => `<div class="sla-row ${r.status === "overdue" ? "sla-overdue" : "sla-due"}" data-client="${r.client_id}">
      <span><strong>${esc(localized(r, "client"))}</strong>${r.site_name ? " — " + esc(r.site_name) : ""}</span>
      <span class="muted small">${t("freq_" + r.frequency)} · ${r.last_service ? t("last_service") + ": " + fmtDate(r.last_service) : t("never_serviced")}${r.days_overdue > 0 ? " · " + r.days_overdue + " " + t("days_overdue") : ""}</span>
    </div>`).join("") || `<div class="muted small">${t("none")}</div>`}</div></div>`;
  if ($("sla-toggle")) $("sla-toggle").addEventListener("click", () => $("sla-list").classList.toggle("hidden"));
  box.querySelectorAll(".sla-row[data-client]").forEach(r =>
    r.addEventListener("click", () => navigate("client-analytics", { id: r.dataset.client })));
}

async function renderBoard(date) {
  const board = $("dp-board");
  if (!board) return;
  board.innerHTML = `<div class="empty">${t("loading")}</div>`;
  const res = await API.get(`/visits?from=${date}&to=${date}`);
  const items = Array.isArray(res) ? res : (res.items || []);
  const cols = [{ id: "", name: "🚩 " + t("unassigned") }]
    .concat((cache.agents || []).map(a => ({ id: String(a.id), name: a.full_name })));
  const byAgent = {};
  cols.forEach(c => byAgent[c.id] = []);
  items.forEach(vi => { const k = vi.agent_id ? String(vi.agent_id) : ""; (byAgent[k] = byAgent[k] || []).push(vi); });
  Object.values(byAgent).forEach(list => list.sort((a, b) => (a.scheduled_start || "").localeCompare(b.scheduled_start || "")));
  board.innerHTML = cols.map(c => dispatchColumn(c, byAgent[c.id] || [])).join("");
  wireDispatchDnD(date);
}

function dispatchGeocoded(v) { return v.site_lat != null && v.site_lng != null ? true : !!parseLatLng(v.location); }

function dispatchColumn(col, list) {
  const geo = list.filter(dispatchGeocoded).length;
  const optBtn = (col.id && list.length > 1)
    ? `<button class="btn secondary sm" data-optimize="${col.id}">🧭 ${t("optimize")}</button>` : "";
  const cards = list.map(dispatchCard).join("") || `<div class="dp-empty">${t("drop_here")}</div>`;
  return `<div class="dp-col"><div class="dp-col-head">
      <strong>${esc(col.name)}</strong>
      <span class="muted small">${list.length} · 📍${geo}/${list.length}</span>${optBtn}</div>
    <div class="dp-col-body" data-agent="${col.id}">${cards}</div></div>`;
}

function dispatchCard(v) {
  const loc = v.site_name || v.location || "";
  const time = (v.scheduled_start || "").slice(11, 16);
  return `<div class="dp-card b-${v.status}" draggable="true" data-visit="${v.id}">
    <div class="dp-card-top"><span class="dp-time">${time || "—"}</span>
      <span class="badge b-${v.status}">${t(statusKey(v.status))}</span></div>
    <div class="dp-client">${esc(localized(v, "client"))}</div>
    ${loc ? `<div class="muted small">${dispatchGeocoded(v) ? "📍" : "⚠️"} ${esc(loc)}</div>` : ""}</div>`;
}

function wireDispatchDnD(date) {
  let dragId = null;
  document.querySelectorAll(".dp-card").forEach(card => {
    card.addEventListener("dragstart", (e) => { dragId = card.dataset.visit; card.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
    card.addEventListener("click", () => navigate("visit", { id: card.dataset.visit }));
  });
  document.querySelectorAll(".dp-col-body").forEach(body => {
    body.addEventListener("dragover", (e) => { e.preventDefault(); body.classList.add("dp-over"); });
    body.addEventListener("dragleave", () => body.classList.remove("dp-over"));
    body.addEventListener("drop", async (e) => {
      e.preventDefault(); body.classList.remove("dp-over");
      if (dragId == null) return;
      const id = dragId; dragId = null;
      try {
        const saved = await API.put(`/visits/${id}`, { agent_id: body.dataset.agent || null });
        if (handledOffline(saved)) return;
        await renderBoard(date); loadSlaStrip();
      } catch (err) { alert(err.message); }
    });
  });
  document.querySelectorAll("[data-optimize]").forEach(b => b.addEventListener("click", (e) => {
    e.stopPropagation(); optimizeRoute(b.dataset.optimize, date);
  }));
}

async function optimizeRoute(agentId, date) {
  let res;
  try { res = await API.post("/dispatch/optimize", { agent_id: agentId, date, apply: false }); }
  catch (err) { alert(err.message); return; }
  if (!res.order.length) { alert(t("no_visits_to_optimize")); return; }
  const orderList = res.order.map(o => `<li><strong>${(o.scheduled_start || "").slice(11, 16) || "—"}</strong> ${esc(localized(o, "client"))}${o.site_name ? ` <span class="muted">(${esc(o.site_name)})</span>` : ""}${o.lat == null ? " ⚠️" : ""}</li>`).join("");
  openModal(`🧭 ${t("optimize_route")}`, `
    <div class="opt-summary">
      <div><div class="muted small">${t("distance_before")}</div><strong>${res.km_before} km</strong></div>
      <div><div class="muted small">${t("distance_after")}</div><strong>${res.km_after} km</strong></div>
      <div class="opt-saved"><div class="muted small">${t("distance_saved")}</div><strong>${res.km_saved} km</strong></div>
    </div>
    ${res.ungeocoded ? `<p class="muted small">⚠️ ${res.ungeocoded} ${t("ungeocoded_note")}</p>` : ""}
    ${!res.has_start ? `<p class="muted small">${t("no_start_note")}</p>` : ""}
    <ol class="opt-order">${orderList}</ol>
    <div class="form-actions"><button type="button" class="btn secondary" id="opt-x">${t("cancel")}</button>
    <button type="button" class="btn" id="opt-apply">✅ ${t("apply_route")}</button></div>`, () => {
    $("opt-x").addEventListener("click", closeModal);
    $("opt-apply").addEventListener("click", async () => {
      try { await API.post("/dispatch/optimize", { agent_id: agentId, date, apply: true }); }
      catch (err) { alert(err.message); return; }
      closeModal(); await renderBoard(date); toast(t("route_applied"));
    });
  });
}

// ====================================================================
// Visit requests — client self-service "request a visit" + staff inbox
// ====================================================================
async function viewRequests(v) {
  const isClient = role() === "client";
  const canAct = can("visits.create");
  const rows = await API.get("/visit-requests");
  v.innerHTML = `<div class="page-head"><h2>📨 ${t("nav_requests")}</h2>
    ${isClient ? `<button class="btn" id="req-add">+ ${t("request_visit")}</button>` : ""}</div>
    <div class="panel" id="req-list">${requestsTable(rows, isClient, canAct)}</div>`;
  if ($("req-add")) $("req-add").addEventListener("click", requestForm);
  wireRequestRows(v);
}

function requestsTable(rows, isClient, canAct) {
  if (!rows.length) return `<div class="empty">${t("no_requests")}</div>`;
  const badge = s => `<span class="badge b-${s === "approved" ? "completed" : s === "declined" ? "cancelled" : "scheduled"}">${t("rq_" + s)}</span>`;
  const head = `<tr><th>${t("date_requested")}</th>${isClient ? "" : `<th>${t("client")}</th>`}<th>${t("location_lbl")}</th>
    <th>${t("preferred_date")}</th><th>${t("notes")}</th><th>${t("status")}</th><th></th></tr>`;
  const body = rows.map(r => `<tr>
    <td>${fmtDate(r.created_at)}</td>
    ${isClient ? "" : `<td>${esc(localized(r, "client"))}</td>`}
    <td>${esc(r.site_name || "—")}</td>
    <td>${r.preferred_date ? fmtDate(r.preferred_date) : "—"}</td>
    <td>${esc((r.note || "").slice(0, 60)) || "—"}</td>
    <td>${badge(r.status)}</td>
    <td>${(canAct && r.status === "pending")
      ? `<button class="btn sm" data-approve="${r.id}">✅ ${t("approve")}</button> <button class="btn secondary sm" data-decline="${r.id}">${t("decline")}</button>`
      : (r.visit_id ? `<button class="link-btn sm" data-open="${r.visit_id}">${t("view")}</button>` : "")}</td>
  </tr>`).join("");
  return `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function wireRequestRows(root) {
  root.querySelectorAll("[data-open]").forEach(b => b.addEventListener("click", () => navigate("visit", { id: b.dataset.open })));
  root.querySelectorAll("[data-approve]").forEach(b => b.addEventListener("click", () => approveRequestDialog(b.dataset.approve)));
  root.querySelectorAll("[data-decline]").forEach(b => b.addEventListener("click", async () => {
    if (!confirm(t("confirm_decline"))) return;
    try { await API.post(`/visit-requests/${b.dataset.decline}/decline`, {}); navigate("requests"); }
    catch (e) { alert(e.message); }
  }));
}

async function requestForm() {
  const cid = API.user.client_id;
  openModal(`📨 ${t("request_visit")}`, `<form id="rqf">
    <div class="field"><label>${t("location_lbl")}</label><select name="site_id" id="rqf-site"><option value="">${t("loading")}…</option></select></div>
    ${field(t("preferred_date"), "preferred_date", { type: "date" })}
    ${field(t("notes"), "note", { textarea: true })}
    <div class="form-actions"><button type="button" class="btn secondary" id="rqf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("submit_request")}</button></div></form>`, async (root) => {
    $("rqf-x").addEventListener("click", closeModal);
    await loadSiteOptions(cid, $("rqf-site"), "", t("none"));
    root.querySelector("#rqf").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const saved = await API.post("/visit-requests", formData(root));
        if (handledOffline(saved)) return;
        closeModal(); toast(t("request_sent")); navigate("requests");
      } catch (err) { alert(err.message); }
    });
  });
}

function approveRequestDialog(id) {
  const agentOpts = [{ v: "", l: t("none") }].concat((cache.agents || []).map(a => ({ v: a.id, l: a.full_name })));
  const svcOpts = [{ v: "", l: t("none") }].concat((cache.services || []).map(s => ({ v: s.id, l: localized(s, "name") })));
  openModal(`✅ ${t("approve_request")}`, `<form id="apr"><div class="form-grid">
    ${field(t("scheduled_start"), "scheduled_start", { type: "datetime-local" })}
    ${field(t("agent"), "agent_id", { options: agentOpts })}
    ${field(t("service"), "service_type_id", { options: svcOpts })}
    </div><p class="muted small">${t("approve_hint")}</p>
    <div class="form-actions"><button type="button" class="btn secondary" id="apr-x">${t("cancel")}</button>
    <button class="btn" type="submit">✅ ${t("approve")}</button></div></form>`, (root) => {
    $("apr-x").addEventListener("click", closeModal);
    root.querySelector("#apr").addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = formData(root);
      if (d.scheduled_start) d.scheduled_start = d.scheduled_start.replace("T", " ") + ":00";
      try { await API.post(`/visit-requests/${id}/approve`, d); closeModal(); toast(t("request_approved_msg")); navigate("requests"); }
      catch (err) { alert(err.message); }
    });
  });
}

// ====================================================================
// Agent "My Day" — today's route as an ordered list, with a map toggle
// ====================================================================
function coordsOf(v) {
  if (v.site_lat != null && v.site_lng != null) return [v.site_lat, v.site_lng];
  return parseLatLng(v.location);
}
function haversineKm(a, b) {
  const R = 6371, r = Math.PI / 180;
  const dLa = (b[0] - a[0]) * r, dLn = (b[1] - a[1]) * r;
  const h = Math.sin(dLa / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dLn / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
function routeKmJs(pts) { let s = 0; for (let i = 0; i < pts.length - 1; i++) s += haversineKm(pts[i], pts[i + 1]); return s; }

async function viewMyDay(v, arg) {
  const date = (arg && arg.date) || ymd(new Date());
  const res = await API.get(`/visits?from=${date}&to=${date}`);
  const items = (Array.isArray(res) ? res : (res.items || []))
    .slice().sort((a, b) => (a.scheduled_start || "").localeCompare(b.scheduled_start || ""));
  v.innerHTML = `<div class="page-head"><h2>🧭 ${t("nav_myday")}</h2>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <button class="btn secondary sm" id="md-prev">${t("prev")}</button>
      <input type="date" id="md-date" value="${date}">
      <button class="btn secondary sm" id="md-next">${t("next")}</button>
      <button class="btn secondary sm" id="md-today">${t("today")}</button>
      <button class="btn secondary sm" id="md-toggle">🗺️ ${t("map_view")}</button></div></div>
    <div id="md-body"></div><div id="md-map" class="hidden"></div>`;
  $("md-prev").addEventListener("click", () => navigate("myday", { date: shiftDate(date, -1) }));
  $("md-next").addEventListener("click", () => navigate("myday", { date: shiftDate(date, 1) }));
  $("md-today").addEventListener("click", () => navigate("myday"));
  $("md-date").addEventListener("change", (e) => navigate("myday", { date: e.target.value }));
  renderMyDayList(items);
  let mapShown = false;
  $("md-toggle").addEventListener("click", async () => {
    mapShown = !mapShown;
    $("md-map").classList.toggle("hidden", !mapShown);
    $("md-body").classList.toggle("hidden", mapShown);
    $("md-toggle").textContent = mapShown ? "📋 " + t("list_view") : "🗺️ " + t("map_view");
    if (mapShown) await renderMyDayMap(items);
  });
}

function renderMyDayList(items) {
  const body = $("md-body");
  if (!items.length) { body.innerHTML = `<div class="empty">${t("no_visits_today")}</div>`; return; }
  const geo = items.map(coordsOf).filter(Boolean);
  const km = geo.length > 1 ? Math.round(routeKmJs(geo)) : 0;
  const rows = items.map((vi, i) => {
    const c = coordsOf(vi);
    const dir = c ? `<a class="btn secondary sm" target="_blank" rel="noopener" href="https://www.google.com/maps/dir/?api=1&destination=${c[0]},${c[1]}">🧭 ${t("navigate")}</a>` : "";
    const start = vi.status === "scheduled" ? `<button class="btn sm" data-start="${vi.id}">▶️ ${t("start_visit")}</button>` : "";
    return `<div class="md-stop b-${vi.status}">
      <div class="md-seq">${i + 1}</div>
      <div class="md-main">
        <div class="md-time">${(vi.scheduled_start || "").slice(11, 16) || "—"} · <span class="badge b-${vi.status}">${t(statusKey(vi.status))}</span></div>
        <div class="md-client">${esc(localized(vi, "client"))}</div>
        ${(vi.site_name || vi.location) ? `<div class="muted small">${c ? "📍" : "⚠️"} ${esc(vi.site_name || vi.location)}</div>` : ""}</div>
      <div class="md-actions">${start}${dir}<button class="link-btn sm" data-open="${vi.id}">${t("view")}</button></div></div>`;
  }).join("");
  body.innerHTML = `${km ? `<p class="muted small">${t("route_distance")}: <strong>${km} km</strong> · ${items.length} ${t("stops")}</p>` : ""}${rows}`;
  body.querySelectorAll("[data-open]").forEach(b => b.addEventListener("click", () => navigate("visit", { id: b.dataset.open })));
  body.querySelectorAll("[data-start]").forEach(b => b.addEventListener("click", async () => {
    try { const s = await API.put(`/visits/${b.dataset.start}`, { status: "in_progress" }); if (handledOffline(s)) return; navigate("visit", { id: b.dataset.start }); }
    catch (e) { alert(e.message); }
  }));
}

async function renderMyDayMap(items) {
  const box = $("md-map");
  box.innerHTML = `<div class="empty">${t("loading")}</div>`;
  let g;
  try { g = await ensureMapsApi(); } catch (e) { g = null; }
  if (!g) { box.innerHTML = `<div class="empty">🗺️ ${t("map_needs_key")}</div>`; return; }
  const pts = items.map(v => ({ v, c: coordsOf(v) })).filter(x => x.c);
  if (!pts.length) { box.innerHTML = `<div class="empty">${t("no_geocoded_today")}</div>`; return; }
  box.innerHTML = `<div id="md-canvas" style="height:62vh;border-radius:12px;border:1px solid var(--line)"></div>`;
  const map = new g.maps.Map($("md-canvas"), { zoom: 11, center: { lat: pts[0].c[0], lng: pts[0].c[1] }, mapTypeControl: false, streetViewControl: false });
  const bounds = new g.maps.LatLngBounds(), path = [];
  pts.forEach((p, i) => {
    const pos = { lat: p.c[0], lng: p.c[1] };
    new g.maps.Marker({ position: pos, map, label: String(i + 1), title: localized(p.v, "client") });
    bounds.extend(pos); path.push(pos);
  });
  new g.maps.Polyline({ path, map, strokeColor: "#1f74d6", strokeWeight: 3, strokeOpacity: .85 });
  map.fitBounds(bounds);
}

// ---- central reports list (admin/owner: all reports, filterable + printable) ----
function reportsTable(rows, forPrint) {
  if (!rows || !rows.length) return `<div class="empty">${t("none")}</div>`;
  const head = `<tr><th>${t("scheduled_start")}</th><th>${t("client")}</th><th>${t("location_lbl")}</th>
    <th>${t("agent")}</th><th>${t("status")}</th><th>${t("summary")}</th></tr>`;
  const body = rows.map(r => {
    const stB = `<span class="badge b-${r.status === "complete" ? "completed" : "draft"}">${t(r.status === "complete" ? "report_complete" : "report_draft")}</span>`;
    const attr = forPrint ? "" : `class="clickable" data-visit="${r.visit_id}"`;
    return `<tr ${attr}><td>${fmtDateTime(r.scheduled_start)}</td><td>${esc(localized(r, "client") || "")}</td>
      <td>${esc(r.site_name || "—")}</td><td>${esc(r.agent_name || "—")}</td>
      <td>${stB}</td><td>${esc((r.summary || "").slice(0, 70))}</td></tr>`;
  }).join("");
  return `<table><thead>${head}</thead><tbody>${body}</tbody></table>`;
}
async function viewReports(v) {
  const stats = ["", "complete", "draft"];
  const clientOpts = `<option value="">${t("all")}</option>` +
    cache.clients.map(c => `<option value="${c.id}">${esc(localized(c, "name"))}</option>`).join("");
  const agentSel = can("users.view")
    ? `<label>${t("agent")}: <select id="rf-agent"><option value="">${t("all")}</option>${cache.agents.map(a => `<option value="${a.id}">${esc(a.full_name)}</option>`).join("")}</select></label>` : "";
  v.innerHTML = `<div class="page-head"><h2>📋 ${t("nav_reports")}</h2>
      <button class="btn sm" id="rf-print">🖨️ ${t("print")}</button></div>
    <div class="toolbar">
      <label>${t("client")}: <select id="rf-client">${clientOpts}</select></label>
      <label>${t("location_lbl")}: <select id="rf-site"><option value="">${t("all")}</option></select></label>
      ${agentSel}
      <label>${t("status")}: <select id="rf-status">${stats.map(s => `<option value="${s}">${s ? t(s === "complete" ? "report_complete" : "report_draft") : t("all")}</option>`).join("")}</select></label>
      <label>${t("from")}: <input type="date" id="rf-from"></label>
      <label>${t("to")}: <input type="date" id="rf-to"></label>
    </div>
    <div class="panel" id="rep-list">${t("loading")}</div>`;
  const val = (id) => ($(id) && $(id).value) || "";
  function buildQuery(paged) {
    const p = [`lang=${LANG}`];   // summaries shown in the current CRM language
    if (paged) p.push(`page=${paged}`, `limit=${PAGE_SIZE}`);
    if (val("rf-client")) p.push("client=" + val("rf-client"));
    if (val("rf-site")) p.push("site=" + val("rf-site"));
    if (val("rf-agent")) p.push("agent=" + val("rf-agent"));
    if (val("rf-status")) p.push("status=" + val("rf-status"));
    if (val("rf-from")) p.push("from=" + val("rf-from"));
    if (val("rf-to")) p.push("to=" + val("rf-to"));
    return p.join("&");
  }
  async function refresh(page = 1) {
    const d = await API.get("/reports?" + buildQuery(page));
    $("rep-list").innerHTML = reportsTable(d.items) + pagerHTML(d);
    $("rep-list").querySelectorAll("tr[data-visit]").forEach(tr =>
      tr.addEventListener("click", () => navigate("report", { id: tr.dataset.visit })));
    wirePager($("rep-list"), d, refresh);
  }
  $("rf-client").addEventListener("change", async () => {
    const cid = val("rf-client"), sel = $("rf-site");
    if (!cid) { sel.innerHTML = `<option value="">${t("all")}</option>`; refresh(1); return; }
    await loadSiteOptions(cid, sel, "", t("all"));
    if (sel.dataset.hasSites === "1") sel.insertAdjacentHTML("beforeend", `<option value="none">${t("unassigned")}</option>`);
    refresh(1);
  });
  ["rf-site", "rf-agent", "rf-status", "rf-from", "rf-to"].forEach(id => {
    if ($(id)) $(id).addEventListener("change", () => refresh(1));
  });
  $("rf-print").addEventListener("click", async () => {
    const res = await API.get("/reports?" + buildQuery(0));   // no page -> full list
    const rows = Array.isArray(res) ? res : (res.items || []);
    const sub = [val("rf-client") && $("rf-client").selectedOptions[0].text,
                 val("rf-site") && $("rf-site").selectedOptions[0].text,
                 $("rf-agent") && val("rf-agent") && $("rf-agent").selectedOptions[0].text]
                .filter(Boolean).join(" · ") || t("all");
    analyticsReportDoc(t("nav_reports"), sub, `<div class="panel">${reportsTable(rows, true)}</div>`);
  });
  refresh(1);
}

// Report fields, in document order. area=multi-line. Materials are numbers.
const REPORT_TEXT_FIELDS = [
  { n: "summary", area: true }, { n: "pests_found" }, { n: "findings", area: true },
  { n: "recommendations", area: true }, { n: "spare_parts_changed" }, { n: "branch_issue", area: true },
];
const REPORT_MAT_FIELDS = ["lamps_used", "cables_used", "transformers_used", "light_sheets_used",
  "fipronil_ml", "imidacloprid_gm", "baits_count", "glo_pieces", "flybase_bags"];

// Single-report document view, opened ONLY from the Reports sidebar list.
// Looks like a printable report, is editable, and shows only fields that have
// content (empty fields are tucked behind a "show all fields" toggle so they can
// Read-only on-screen tables of the device follow-up data captured by QR scans
// on this visit, grouped by device type (mirrors the printed follow-up form).
// Returns "" when nothing was scanned.
function reportFollowupHtml(groups) {
  const order = ["bait_station", "fly_trap", "glue_station", "light_trap"];
  return order.filter(ty => (groups[ty] || []).length).map(ty => {
    const fields = DEVICE_FIELDS[ty] || [];
    const head = `<th>${t("code")}</th><th>${t("label")}</th><th>${t("status")}</th>`
      + fields.map(f => `<th>${esc(t("df_" + f.key))}</th>`).join("");
    const rows = groups[ty].map(r => `<tr>
      <td><strong>${esc(r.code)}</strong></td><td>${esc(r.label || "—")}</td>
      <td>${t("mst_" + r.status)}</td>
      ${fields.map(f => `<td>${fmtDetailCell(f, (r.details || {})[f.key])}</td>`).join("")}
    </tr>`).join("");
    return `<div class="rdoc-fu"><h4>${devIcon(ty)} ${esc(t("sec_" + ty))}</h4>
      <table class="rdoc-table"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }).join("");
}

// still be added). New design — intentionally distinct from the certificate.
async function viewReportDoc(v, arg) {
  const id = arg && arg.id;
  // Load the report already translated into the current CRM language for
  // display/printing. Edits are dirty-tracked so untouched (translated) fields
  // are never saved back over the agent's original text.
  const visit = await API.get(`/visits/${id}?lang=${LANG}`);
  const rep = visit.report || {};
  // Device follow-up captured by QR scans on this visit (best-effort).
  const followup = await API.get(`/visits/${id}/followup`).catch(() => ({ groups: {} }));
  const fuHtml = reportFollowupHtml(followup.groups || {});
  const photos = visit.photos || [];
  const has = (val) => val !== null && val !== undefined && String(val).trim() !== "";
  const textRow = (f) => {
    const val = rep[f.n] || "";
    const input = f.area ? `<textarea data-rep="${f.n}" rows="3">${esc(val)}</textarea>`
                         : `<input type="text" data-rep="${f.n}" value="${esc(val)}">`;
    return `<div class="rdoc-row" data-empty="${has(val) ? "0" : "1"}"><label>${esc(t(f.n))}</label>${input}</div>`;
  };
  const matRow = (k) => {
    const val = rep[k] || "";
    return `<div class="rdoc-row" data-empty="${Number(val) > 0 ? "0" : "1"}"><label>${esc(t(k))}</label>
      <input type="number" step="any" data-rep="${k}" value="${esc(Number(val) > 0 ? val : "")}"></div>`;
  };
  const sig = (f, label) => f ? `<div class="rdoc-sig"><img src="/uploads/${esc(f)}"><div class="ln">${esc(label)}</div></div>` : "";
  const chemRows = (visit.chemicals || []).map(cu =>
    `<tr><td>${esc(localized(cu, "name"))}</td><td>${cu.quantity} ${esc(cu.unit || "")}</td><td>${esc(cu.area_treated || "—")}</td></tr>`).join("");

  const allRows = [...REPORT_TEXT_FIELDS.map(textRow), ...REPORT_MAT_FIELDS.map(matRow)].join("");
  const statusBadgeHtml = `<span class="badge b-${rep.status === "complete" ? "completed" : "draft"}">${t(rep.status === "complete" ? "report_complete" : "report_draft")}</span>`;
  // Attachments flagged "Business plan" on the visit are surfaced on the report.
  const bpPhotos = (visit.photos || []).filter(p => Number(p.is_business_plan));

  v.innerHTML = `
    <div class="breadcrumb" id="bc">← 📋 ${t("nav_reports")}</div>
    <div class="page-head"><h2>📋 ${t("report")} — ${esc(localized(visit, "client"))}</h2>
      <div style="display:flex;gap:8px;align-items:center">
        <label class="muted small"><input type="checkbox" id="rd-showall"> ${t("show_all_fields")}</label>
        <button class="btn sm secondary" id="rd-print">🖨️ ${t("print")}</button>
        ${can("visits.edit") ? `<button class="btn sm" id="rd-save">💾 ${t("save")}</button>` : ""}</div></div>
    <div class="rdoc" id="rdoc">
      <div class="rdoc-head">
        <div><div class="rdoc-title">${t("service_report")}</div>
          <div class="rdoc-no">#${String(visit.id).padStart(5, "0")} · ${fmtDateTime(visit.completed_at || visit.scheduled_start)}</div></div>
        ${statusBadgeHtml}
      </div>
      <div class="rdoc-meta">
        <div><span>${t("client")}</span><b>${esc(localized(visit, "client") || "—")}</b></div>
        <div><span>${t("location_lbl")}</span><b>${esc(visit.site_name || visit.location || "—")}</b></div>
        <div><span>${t("agent")}</span><b>${esc(visit.agent_name || "—")}</b></div>
        <div><span>${t("service")}</span><b>${esc(localized(visit, "service") || "—")}</b></div>
        ${visit.visit_number ? `<div><span>${t("visit_number")}</span><b>${esc(visit.visit_number)}</b></div>` : ""}
      </div>
      <div class="rdoc-body">${allRows}</div>
      <div class="rdoc-extra" data-empty="${fuHtml ? "0" : "1"}">
        <h3 class="rdoc-sec">🏷️ ${t("followup_report")}</h3>
        ${fuHtml || `<div class="empty">${t("followup_nothing")}</div>`}</div>
      <div class="rdoc-extra" data-empty="${photos.length ? "0" : "1"}">
        <h3 class="rdoc-sec" style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <span>📷 ${t("photos")}</span>
          ${can("visits.edit") ? `<button type="button" class="btn sm secondary" id="rd-addphoto">📎 ${t("add_attachment")}</button>` : ""}</h3>
        <div id="report-photos" class="photo-grid"></div></div>
      ${chemRows ? `<h3 class="rdoc-sec">${t("chemicals_applied")}</h3>
        <table class="rdoc-table"><thead><tr><th>${t("name_en")}</th><th>${t("quantity")}</th><th>${t("area_treated")}</th></tr></thead>
        <tbody>${chemRows}</tbody></table>` : ""}
      ${(rep.customer_signature || rep.technician_signature) ? `<div class="rdoc-sigs">
        ${sig(rep.customer_signature, rep.customer_name || t("customer_signature"))}
        ${sig(rep.technician_signature, visit.agent_name || t("technician_signature"))}</div>` : ""}
      ${bpPhotos.length ? `<h3 class="rdoc-sec">📎 ${t("business_plan")}</h3>
        <div id="report-bp-files" class="photo-grid"></div>` : ""}
    </div>`;

  if (bpPhotos.length) renderPhotos("visit", id, bpPhotos, "report-bp-files");
  renderPhotos("visit", id, photos, "report-photos");
  if ($("rd-addphoto")) $("rd-addphoto").addEventListener("click",
    () => uploadPhotoDialog("visit", id, () => navigate("report", { id })));
  $("bc").addEventListener("click", () => navigate("reports"));
  // Empty fields/sections are hidden by default; the toggle reveals them (e.g.
  // to fill a blank field, or to see the empty follow-up/photos capture areas).
  const applyShowAll = () => {
    const show = $("rd-showall").checked;
    $("rdoc").querySelectorAll('[data-empty="1"]').forEach(el => el.classList.toggle("hidden", !show));
  };
  applyShowAll();
  $("rd-showall").addEventListener("change", applyShowAll);
  // mark a field dirty once the user actually edits it (so we only save changes,
  // never the auto-translated text of fields they left alone)
  $("rdoc").querySelectorAll("[data-rep]").forEach(el =>
    el.addEventListener("input", () => { el.dataset.dirty = "1"; }));
  $("rd-print").addEventListener("click", () => printReportDoc(visit, followup.groups || {}));
  if ($("rd-save")) $("rd-save").addEventListener("click", async () => {
    const d = {};
    $("rdoc").querySelectorAll('[data-rep][data-dirty="1"]').forEach(el => { d[el.dataset.rep] = el.value; });
    if (!Object.keys(d).length) { toast(t("saved")); return; }   // nothing changed
    const saved = await API.post(`/visits/${id}/report`, d);   // no status -> keeps draft/complete
    if (handledOffline(saved)) return;
    toast(t("saved")); navigate("report", { id });
  });
}

// Printable PDF of one report — a clean, NEW layout (not the certificate).
// Renders only the fields that have content.
// Popup-proof printing: render a full HTML document into a hidden same-page
// iframe and let its own onload auto-print fire there — instead of window.open,
// which pop-up blockers and the Android WebView routinely block. Replaces the
// old `window.open("","_blank") + document.write` dance everywhere.
function printHtmlDoc(doc) {
  // Android app: WebViews don't implement window.print(), so hand the document
  // to the native bridge (see PrintBridge in MainActivity.java) which renders it
  // and routes to Android's PrintManager.
  if (window.PestPrint && typeof window.PestPrint.printHtml === "function") {
    try { window.PestPrint.printHtml(doc); return; } catch (e) { /* fall back to iframe */ }
  }
  const prev = document.getElementById("print-frame");
  if (prev) prev.remove();
  const ifr = document.createElement("iframe");
  ifr.id = "print-frame";
  ifr.setAttribute("aria-hidden", "true");
  ifr.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;opacity:0";
  document.body.appendChild(ifr);
  const idoc = ifr.contentWindow.document;
  idoc.open(); idoc.write(doc); idoc.close();   // inline auto-print script runs here
  // Clean the frame up later so they don't pile up (print dialog is async).
  setTimeout(() => { const f = document.getElementById("print-frame"); if (f === ifr) ifr.remove(); }, 120000);
}
function printReportDoc(visit, fuGroups) {
  const ar = LANG === "ar";
  const dir = ar ? "rtl" : "ltr";
  const S = SETTINGS || {};
  const rep = visit.report || {};
  // Device follow-up (QR scans) as print tables, reusing the report styles.
  const fuOrder = ["bait_station", "fly_trap", "glue_station", "light_trap"];
  const fuHtml = fuOrder.filter(ty => ((fuGroups || {})[ty] || []).length).map(ty => {
    const fields = DEVICE_FIELDS[ty] || [];
    const head = `<th>${esc(t("code"))}</th><th>${esc(t("label"))}</th><th>${esc(t("status"))}</th>`
      + fields.map(f => `<th>${esc(t("df_" + f.key))}</th>`).join("");
    const rows = fuGroups[ty].map(r => `<tr><td>${esc(r.code)}</td><td>${esc(r.label || "—")}</td>`
      + `<td>${esc(t("mst_" + r.status))}</td>`
      + fields.map(f => `<td>${esc(fmtDetailCell(f, (r.details || {})[f.key]))}</td>`).join("") + `</tr>`).join("");
    return `<h4 class="fusec">${devIcon(ty)} ${esc(t("sec_" + ty))}</h4>
      <table class="data"><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;
  }).join("");
  const compName = (ar ? S.company_name_ar : S.company_name_en) || S.company_name_en || "Company";
  const compAddr = (ar ? S.address_ar : S.address_en) || S.address_en || "";
  const logoHtml = S.logo ? `<img src="/uploads/${esc(S.logo)}" style="height:46px">` : `<div style="font-size:32px">🐜</div>`;
  const has = (x) => x !== null && x !== undefined && String(x).trim() !== "";
  const row = (label, val) => has(val) ? `<tr><td class="l">${esc(label)}</td><td>${esc(val).replace(/\n/g, "<br>")}</td></tr>` : "";
  const matRows = REPORT_MAT_FIELDS.filter(k => Number(rep[k]) > 0)
    .map(k => `<tr><td class="l">${esc(t(k))}</td><td>${esc(rep[k])}</td></tr>`).join("");
  const chemRows = (visit.chemicals || []).map(cu =>
    `<tr><td>${esc(localized(cu, "name"))}</td><td>${cu.quantity} ${esc(cu.unit || "")}</td><td>${esc(cu.area_treated || "—")}</td></tr>`).join("");
  const sig = (f, label) => f ? `<div class="sg"><img src="/uploads/${esc(f)}"><div class="ln">${esc(label)}</div></div>` : "";
  // "Business plan" attachments from the visit, surfaced on the printed report.
  const bpHtml = (visit.photos || []).filter(p => Number(p.is_business_plan)).map(p => {
    const isImg = /\.(jpe?g|png|gif|webp)$/i.test(p.filename || "");
    const cap = esc(p.caption || p.original_name || "");
    return isImg
      ? `<div class="bp-item"><img src="/uploads/${esc(p.filename)}"><div class="bp-cap">${cap}</div></div>`
      : `<div class="bp-item bp-file">📎 ${esc(p.original_name || p.filename)}<div class="bp-cap">${cap}</div></div>`;
  }).join("");
  // Captured images on the visit (excluding the business-plan ones shown below).
  const photoHtml = (visit.photos || []).filter(p =>
    !Number(p.is_business_plan) && /\.(jpe?g|png|gif|webp)$/i.test(p.filename || "")).map(p =>
    `<div class="bp-item"><img src="/uploads/${esc(p.filename)}"><div class="bp-cap">${esc(p.caption || p.original_name || "")}</div></div>`).join("");
  const reportRows = [
    row(t("pests_found"), rep.pests_found), row(t("findings"), rep.findings),
    row(t("recommendations"), rep.recommendations),
    row(t("summary"), rep.summary), row(t("spare_parts_changed"), rep.spare_parts_changed),
    row(t("branch_issue"), rep.branch_issue),
  ].join("");
  const doc = `<!DOCTYPE html><html lang="${LANG}" dir="${dir}"><head><meta charset="utf-8">
    <title>${esc(t("service_report"))} #${String(visit.id).padStart(5, "0")}</title>
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
      *{box-sizing:border-box}
      body{font-family:${ar ? "'Cairo'" : "'Inter'"},system-ui,sans-serif;color:#1c2733;margin:0;padding:40px;font-size:13px}
      .sheet{max-width:760px;margin:auto}
      .top{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #0f172a;padding-bottom:14px;margin-bottom:6px}
      .co h1{margin:0;font-size:17px}.co .m{color:#64748b;font-size:11px;line-height:1.6}
      .rt{text-align:${ar ? "left" : "right"}}
      .rt h2{margin:0;font-size:20px;letter-spacing:.02em}
      .rt .no{color:#64748b;font-size:12px;margin-top:3px}
      .meta{display:grid;grid-template-columns:1fr 1fr;gap:6px 22px;margin:16px 0 6px}
      .meta div{font-size:12px}.meta span{color:#64748b}.meta b{margin-${ar ? "right" : "left"}:6px}
      h3.sec{margin:20px 0 6px;font-size:11px;color:#0f172a;text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid #e3e8ec;padding-bottom:4px}
      h4.fusec{margin:12px 0 4px;font-size:12px;color:#0f766e}
      table{width:100%;border-collapse:collapse}
      .kv td{padding:7px 4px;vertical-align:top;line-height:1.6;border-bottom:1px solid #eef2f5}
      .kv td.l{color:#64748b;width:32%;white-space:nowrap}
      .data th,.data td{padding:8px 10px;text-align:${ar ? "right" : "left"};border-bottom:1px solid #e3e8ec}
      .data th{background:#f1f5f9;font-size:11px;text-transform:uppercase;color:#475569}
      .sev{display:inline-block;padding:3px 12px;border-radius:20px;font-weight:700;font-size:12px;color:#fff}
      .sigs{display:flex;gap:30px;margin-top:30px}
      .sg{flex:1;text-align:center}.sg img{max-height:70px;max-width:220px}
      .sg .ln{border-top:1px solid #1c2733;margin-top:6px;padding-top:6px;color:#64748b;font-size:12px}
      .bp-grid{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px}
      .bp-item{width:170px}.bp-item img{width:100%;border:1px solid #e3e8ec;border-radius:8px}
      .bp-file{padding:10px;border:1px solid #e3e8ec;border-radius:8px;font-size:12px;word-break:break-all}
      .bp-cap{color:#64748b;font-size:11px;margin-top:4px}
      .noprint{text-align:center;margin-bottom:18px}
      .pbtn{background:#0f172a;color:#fff;border:none;padding:10px 22px;border-radius:8px;font-size:14px;cursor:pointer}
      @media print{body{padding:0}.noprint{display:none}*{-webkit-print-color-adjust:exact !important;print-color-adjust:exact !important}}
    </style></head><body>
    <div class="noprint"><button class="pbtn" onclick="window.print()">🖨️ ${esc(t("print"))}</button></div>
    <div class="sheet">
      <div class="top">
        <div style="display:flex;gap:12px;align-items:center">${logoHtml}
          <div class="co"><h1>${esc(compName)}</h1><div class="m">${esc(compAddr)}<br>${esc(S.phone || "")} · ${esc(S.email || "")}</div></div></div>
        <div class="rt"><h2>${esc(t("service_report"))}</h2>
          <div class="no">#${String(visit.id).padStart(5, "0")} · ${fmtDate(visit.completed_at || visit.scheduled_start)}</div></div>
      </div>
      <div class="meta">
        <div><span>${t("client")}:</span><b>${esc(localized(visit, "client") || "—")}</b></div>
        <div><span>${t("location_lbl")}:</span><b>${esc(visit.site_name || visit.location || "—")}</b></div>
        <div><span>${t("agent")}:</span><b>${esc(visit.agent_name || "—")}</b></div>
        <div><span>${t("service")}:</span><b>${esc(localized(visit, "service") || "—")}</b></div>
        ${visit.visit_number ? `<div><span>${t("visit_number")}:</span><b>${esc(visit.visit_number)}</b></div>` : ""}
      </div>
      <h3 class="sec">${esc(t("report"))}</h3>
      <table class="kv">${reportRows}</table>
      ${matRows ? `<h3 class="sec">${esc(t("materials_used"))}</h3><table class="kv">${matRows}</table>` : ""}
      ${chemRows ? `<h3 class="sec">${esc(t("chemicals_applied"))}</h3>
        <table class="data"><thead><tr><th>${esc(t("name_en"))}</th><th>${esc(t("quantity"))}</th><th>${esc(t("area_treated"))}</th></tr></thead>
        <tbody>${chemRows}</tbody></table>` : ""}
      ${fuHtml ? `<h3 class="sec">🏷️ ${esc(t("followup_report"))}</h3>${fuHtml}` : ""}
      ${photoHtml ? `<h3 class="sec">📷 ${esc(t("photos"))}</h3><div class="bp-grid">${photoHtml}</div>` : ""}
      ${(rep.customer_signature || rep.technician_signature) ? `<div class="sigs">
        ${sig(rep.customer_signature, rep.customer_name || t("customer_signature"))}
        ${sig(rep.technician_signature, visit.agent_name || t("technician_signature"))}</div>` : ""}
      ${bpHtml ? `<h3 class="sec">${esc(t("business_plan"))}</h3><div class="bp-grid">${bpHtml}</div>` : ""}
    </div>
    <script>window.onload=function(){setTimeout(function(){window.print()},400)}<\/script>
    </body></html>`;
  printHtmlDoc(doc);
}

// Visit duration choices: 15 min up to 90 min in 5-minute steps.
function durationOpts() {
  const opts = [];
  for (let m = 15; m <= 90; m += 5) opts.push({ v: m, l: `${m} ${t("minutes")}` });
  return opts;
}
function visitForm(preset) {
  preset = preset || {};
  const clientOpts = cache.clients.map(c => ({ v: c.id, l: localized(c, "name") }));
  const agentOpts = [{ v: "", l: t("none") }].concat(cache.agents.map(a => ({ v: a.id, l: a.full_name })));
  const svcOpts = [{ v: "", l: t("none") }].concat(cache.services.map(s => ({ v: s.id, l: localized(s, "name") })));
  openModal(t("new_visit"), `<form id="vf"><div class="form-grid">
    ${field(t("client"), "client_id", { options: clientOpts, value: preset.client_id, cls: "full" })}
    ${field(t("location_lbl"), "site_id", { options: [{ v: "", l: t("none") }], cls: "full" })}
    ${field(t("agent"), "agent_id", { options: agentOpts })}
    ${field(t("service"), "service_type_id", { options: svcOpts })}
    ${field(t("scheduled_start"), "scheduled_start", { type: "datetime-local" })}
    ${field(t("duration"), "duration", { options: durationOpts(), value: 30 })}
    ${field(t("location_detail"), "location", { cls: "full" })}
    ${field(t("notes"), "notes", { textarea: true, cls: "full" })}
    </div><div class="form-actions"><button type="button" class="btn secondary" id="vf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    $("vf-x").addEventListener("click", closeModal);
    const clientSel = root.querySelector("[name=client_id]");
    const siteSel = root.querySelector("[name=site_id]");
    loadSiteOptions(preset.client_id || (clientSel && clientSel.value), siteSel, preset.site_id);
    if (clientSel) clientSel.addEventListener("change", () => loadSiteOptions(clientSel.value, siteSel));
    root.querySelector("#vf").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (siteSel && siteSel.dataset.hasSites === "1" && !siteSel.value) {
        alert(t("select_location")); siteSel.focus(); return;
      }
      const d = formData(root);
      // The end time is derived from start + chosen duration (no manual end field).
      if (d.scheduled_start && d.duration) {
        const start = new Date(d.scheduled_start);
        if (!isNaN(start)) {
          const end = new Date(start.getTime() + Number(d.duration) * 60000);
          const pad = (n) => String(n).padStart(2, "0");
          d.scheduled_end = `${end.getFullYear()}-${pad(end.getMonth() + 1)}-${pad(end.getDate())}T${pad(end.getHours())}:${pad(end.getMinutes())}`;
        }
      }
      delete d.duration;
      Object.keys(d).forEach(k => { if (d[k] === "") delete d[k]; });
      try { const saved = await API.post("/visits", d); if (handledOffline(saved)) return; closeModal(); navigate("visit", { id: saved.id }); }
      catch (err) { alert(err.message); }
    });
  });
}

// ---- visit detail ----
async function viewVisit(v, arg) {
  const id = arg.id;
  // Clients get the report auto-translated into the current language (read-only).
  // Staff edit the original text, so we don't translate the editable form.
  const langParam = role() === "client" ? `?lang=${LANG}` : "";
  const visit = await API.get("/visits/" + id + langParam);
  const canEdit = can("visits.edit");
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
        <div>${t("location_lbl")}</div><div>${esc(visit.site_name || visit.location || "—")}</div>
        <div>${t("notes")}</div><div>${esc(visit.notes || "—")}</div>
        <div>${t("visit_number")}</div><div>${canEdit
          ? `<select id="v-visitnum"><option value="">—</option>${Array.from({ length: 12 }, (_, i) => i + 1).map(n =>
              `<option value="${n}" ${Number(visit.visit_number) === n ? "selected" : ""}>${n}</option>`).join("")}</select>`
          : (visit.visit_number || "—")}</div>
      </div>
      ${canEdit && role() !== "agent" ? `<div class="form-actions" style="justify-content:flex-start;flex-wrap:wrap">
        <select id="v-site" style="min-width:140px"><option value="">${t("none")}</option></select>
        <button class="btn sm secondary" id="v-site-btn">📍 ${t("set_location")}</button></div>` : ""}
      ${canEdit ? `<div class="form-actions" style="justify-content:flex-start">
        <select id="v-status">${["scheduled", "in_progress", "completed", "cancelled"].map(s => `<option value="${s}" ${visit.status === s ? "selected" : ""}>${t(statusKey(s))}</option>`).join("")}</select>
        <button class="btn sm" id="v-status-btn">${t("change_status")}</button></div>` : ""}
      ${visit.site_map_image ? `<div class="section-title" style="margin-top:12px"><h3>🗺️ ${t("site_map")}</h3></div>
        <img src="/uploads/${esc(visit.site_map_image)}" alt="${esc(t("site_map"))}" style="width:100%;border:1px solid var(--line);border-radius:10px;display:block">` : ""}
      </div>
      <div class="panel"><div class="section-title"><h3>${t("report")}</h3>
        ${role() !== "client" && rep.id ? `<span class="badge b-${rep.status === "complete" ? "completed" : "draft"}">${t(rep.status === "complete" ? "report_complete" : "report_draft")}</span>` : ""}</div>
        ${role() === "client" ? clientReportView(rep) : reportForm(rep, id, canEdit)}
      </div>
    </div>

    ${role() !== "client" ? `<div class="panel"><div class="section-title"><h3>${t("chemicals_used")}</h3>
      ${canEdit ? `<button class="btn sm" id="add-chem">+ ${t("add_chemical")}</button>` : ""}</div>
      <table><thead><tr><th>${t("name_en")}</th><th>${t("quantity")}</th><th>${t("area_treated")}</th><th></th></tr></thead>
      <tbody>${(visit.chemicals || []).map(cu => `<tr><td>${esc(localized(cu, "name"))}</td>
        <td>${cu.quantity} ${esc(cu.unit)}</td><td>${esc(cu.area_treated || "—")}</td>
        <td>${canEdit ? `<button class="link-btn danger sm" data-rmuse="${cu.id}">${t("delete")}</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="4" class="empty">${t("none")}</td></tr>`}</tbody></table></div>` : ""}

    ${role() !== "client" ? `<div class="panel" id="dev-coverage"><div class="section-title"><h3>🏷️ ${t("device_coverage")}</h3></div>
      <div class="muted">${t("loading")}</div></div>` : ""}

    <div class="panel"><div class="section-title"><h3>${t("signatures")}</h3></div>
      <div class="grid-2">${sigBlock("customer", visit, id, canEdit)}${sigBlock("technician", visit, id, canEdit)}</div></div>

    <div class="panel"><div class="section-title"><h3>${t("attachments")}</h3>
      ${role() !== "client" ? `<button class="btn sm" id="add-photo">📎 ${t("add_attachment")}</button>` : ""}</div>
      <div id="photos" class="photo-grid"></div></div>`;

  $("bc").addEventListener("click", () => navigate(role() === "client" ? "visits" : "schedule"));
  if ($("print-cert")) $("print-cert").addEventListener("click", async () => {
    if (!visit.report || visit.report.status !== "complete") {
      alert(t("no_report_for_cert")); return;
    }
    const vt = await API.get(`/visits/${id}?lang=${LANG}`);   // certificate in current language
    printCertificate(vt);
  });
  v.querySelectorAll("[data-sign]").forEach(b => b.addEventListener("click", () => signatureDialog(id, b.dataset.sign)));
  if ($("v-status-btn")) $("v-status-btn").addEventListener("click", async () => {
    const saved = await API.put("/visits/" + id, { status: $("v-status").value }); if (handledOffline(saved)) return; toast(t("saved")); navigate("visit", { id });
  });
  if ($("v-visitnum")) $("v-visitnum").addEventListener("change", async () => {
    const saved = await API.put("/visits/" + id, { visit_number: $("v-visitnum").value || null });
    if (handledOffline(saved)) return; toast(t("saved"));
  });
  if ($("v-site")) {
    loadSiteOptions(visit.client_id, $("v-site"), visit.site_id, t("none"));
    $("v-site-btn").addEventListener("click", async () => {
      const saved = await API.put("/visits/" + id, { site_id: $("v-site").value || null });
      if (handledOffline(saved)) return; toast(t("saved")); navigate("visit", { id });
    });
  }
  if ($("report-form")) {
    const form = $("report-form");
    const hint = $("report-draft-hint");
    // Auto-save as a draft while the agent types so an unfinished report is never
    // lost if they log out / close the app before completing it.
    let draftTimer = null, draftBusy = false;
    async function saveDraft() {
      if (draftBusy) return;
      draftBusy = true;
      try {
        const r = await API.post(`/visits/${id}/report`, { ...formData(form), status: "draft" });
        if (handledOffline(r)) return;
        if (hint) hint.textContent = "✓ " + t("draft_saved");
      } catch (e) { /* keep typing; will retry on next change */ }
      finally { draftBusy = false; }
    }
    form.addEventListener("input", () => {
      if (hint) hint.textContent = "…";
      clearTimeout(draftTimer);
      draftTimer = setTimeout(saveDraft, 1200);
    });
    // Submit = finalise. Requires the core fields + both signatures (server-checked).
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      clearTimeout(draftTimer);
      try {
        const saved = await API.post(`/visits/${id}/report`, { ...formData(form), status: "complete" });
        if (handledOffline(saved)) return;
        toast(t("report_completed"));
        navigate("visit", { id });
      } catch (err) {
        const msg = String(err && err.message || "");
        if (msg.startsWith("report_incomplete:")) {
          const keys = msg.slice("report_incomplete:".length).split(",").filter(Boolean);
          alert(t("report_incomplete_msg") + "\n\n• " + keys.map(reportFieldLabel).join("\n• "));
        } else {
          alert(msg || t("error"));
        }
      }
    });
  }
  if ($("add-chem")) $("add-chem").addEventListener("click", () => usageForm(id));
  v.querySelectorAll("[data-rmuse]").forEach(b => b.addEventListener("click", async () => {
    const r = await API.del("/usage/" + b.dataset.rmuse); if (handledOffline(r, b.closest("tr"))) return; navigate("visit", { id });
  }));
  renderPhotos("visit", id, visit.photos);
  if (role() !== "client") loadVisitCoverage(id);
  if ($("add-photo")) $("add-photo").addEventListener("click", () => uploadPhotoDialog("visit", id, () => navigate("visit", { id })));
  // report-level attachments (images / PDF / Excel) live under the report panel
  if (rep.id) renderPhotos("report", rep.id, visit.report_photos, "report-files");
  if ($("add-report-file")) $("add-report-file").addEventListener("click", () => uploadPhotoDialog("report", rep.id, () => navigate("visit", { id })));
}

function reportForm(rep, visitId, canEdit) {
  const dis = canEdit ? "" : "disabled";
  return `<form id="report-form">
    ${field(t("summary"), "summary", { value: rep.summary, textarea: true })}
    ${field(t("pests_found"), "pests_found", { value: rep.pests_found })}
    ${field(t("findings"), "findings", { value: rep.findings, textarea: true })}
    ${field(t("recommendations"), "recommendations", { value: rep.recommendations, textarea: true })}
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
    ${canEdit ? `<div class="form-actions" style="justify-content:space-between;align-items:center">
        <span id="report-draft-hint" class="muted small"></span>
        <button class="btn" type="submit">✔ ${t("complete_save_report")}</button></div>` : ""}
    </form>${!canEdit ? "<script>document.querySelectorAll('#report-form [name]').forEach(e=>e.disabled=true)</script>" : ""}
    ${rep.id ? `<div class="section-title" style="margin:18px 0 8px"><h3>📎 ${t("attachments")}</h3>
      ${canEdit ? `<button type="button" class="btn sm" id="add-report-file">📎 ${t("add_attachment")}</button>` : ""}</div>
      <div id="report-files" class="photo-grid"></div>` : `<div class="muted small" style="margin-top:12px">${t("attach_after_save")}</div>`}`;
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
    ${rep.spare_parts_changed ? `<div>${t("spare_parts_changed")}</div><div>${esc(rep.spare_parts_changed)}</div>` : ""}
    ${mat}
    ${rep.branch_issue ? `<div>${t("branch_issue")}</div><div>${esc(rep.branch_issue)}</div>` : ""}</div>
    <div class="section-title" style="margin:18px 0 8px"><h3>📎 ${t("attachments")}</h3></div>
    <div id="report-files" class="photo-grid"></div>`;
}
function usageForm(visitId) {
  // Consumable materials (UV lamps, glue boards, ...) are recorded via the
  // report's counter fields, not here, so exclude them to avoid double-counting.
  const opts = cache.chemicals.filter(c => !c.material_key)
    .map(c => ({ v: c.id, l: `${localized(c, "name")} (${c.quantity_in_stock} ${c.unit})` }));
  openModal(t("add_chemical"), `<form id="uf">
    ${field(t("name_en"), "chemical_id", { options: opts })}
    ${field(t("quantity"), "quantity", { type: "number" })}
    ${field(t("area_treated"), "area_treated")}
    <div class="form-actions"><button type="button" class="btn secondary" id="uf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    $("uf-x").addEventListener("click", closeModal);
    root.querySelector("#uf").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { const saved = await API.post(`/visits/${visitId}/usage`, formData(root)); if (handledOffline(saved)) return; closeModal(); navigate("visit", { id: visitId }); }
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
    ${can("chemicals.create") ? `<button class="btn" id="add-chem">+ ${t("new_chemical")}</button>` : ""}</div>
    <div class="panel"><table><thead><tr>
      <th>${t("name_en")}</th><th>${t("active_ingredient")}</th><th>${t("in_stock")}</th>
      <th>${t("reorder_level")}</th><th>${t("hazard_class")}</th>${can("chemicals.edit") ? `<th>${t("actions")}</th>` : ""}</tr></thead>
      <tbody>${chems.map(c => {
        const low = c.quantity_in_stock <= c.reorder_level;
        return `<tr><td><strong>${esc(localized(c, "name"))}</strong><div class="muted small">${esc(c.reg_no || "")}</div></td>
        <td>${esc(c.active_ingredient || "—")}</td>
        <td class="${low ? "lowstock" : ""}">${c.quantity_in_stock} ${esc(c.unit)} ${low ? `· ${t("low_stock_warn")}` : ""}</td>
        <td>${c.reorder_level} ${esc(c.unit)}</td><td>${esc(c.hazard_class || "—")}</td>
        ${can("chemicals.edit") ? `<td><button class="link-btn sm" data-stock="${c.id}">${t("adjust_stock")}</button>
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
      try { const saved = isEdit ? await API.put("/chemicals/" + c.id, formData(root)) : await API.post("/chemicals", formData(root));
        if (handledOffline(saved)) return;
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
      const saved = await API.post(`/chemicals/${id}/stock`, formData(root));
      if (handledOffline(saved)) return;
      closeModal();
      cache.chemicals = await API.get("/chemicals"); navigate("chemicals");
    });
  });
}

// ====================================================================
// Engineer material issues (stock checked out of inventory by an engineer)
// ====================================================================
// Per-engineer materials balance: issued − used (on their visits) = remaining.
function renderIssueBalance(balance, mine) {
  const engineers = (balance && balance.engineers) || [];
  if (!engineers.length) return "";
  const qty = (n, unit) => `${(+n || 0).toLocaleString()} ${esc(unit || "")}`.trim();
  const block = (e) => {
    if (!e.materials.length) return "";
    const rows = e.materials.map(m => {
      const low = m.remaining <= 0;
      return `<tr${low ? ` class="bal-empty"` : ""}>
        <td>${esc(localized(m, "name") || ("#" + m.chemical_id))}</td>
        <td>${qty(m.issued, m.unit)}</td>
        <td>${qty(m.used, m.unit)}</td>
        <td><strong>${qty(m.remaining, m.unit)}</strong></td></tr>`;
    }).join("");
    return `${mine ? "" : `<div style="font-weight:600;margin:10px 0 4px">${esc(e.agent_name)}</div>`}
      <table><thead><tr><th>${t("material")}</th><th>${t("issued")}</th>
        <th>${t("used")}</th><th>${t("remaining")}</th></tr></thead><tbody>${rows}</tbody></table>`;
  };
  const body = engineers.map(block).join("");
  if (!body.trim()) return "";
  return `<div class="panel"><h3 style="margin:0 0 8px">📊 ${t("materials_on_hand")}</h3>
    <p class="muted" style="margin:0 0 10px">${t("materials_on_hand_hint")}</p>${body}</div>`;
}

async function viewIssues(v) {
  const mine = role() === "agent";
  const [issues, balance] = await Promise.all([API.get("/issues"), API.get("/issues/balance")]);
  const canDelete = can("issues.delete");
  v.innerHTML = `<div class="page-head"><h2>📦 ${t("nav_issues")}</h2>
    ${can("issues.create") ? `<button class="btn" id="add-issue">+ ${t("new_issue")}</button>` : ""}</div>
    ${renderIssueBalance(balance, mine)}
    <h3 style="margin:18px 0 8px">${t("issue_history")}</h3>
    <div class="panel"><table><thead><tr>
      <th>${t("date")}</th>${mine ? "" : `<th>${t("engineer")}</th>`}<th>${t("materials")}</th><th>${t("notes")}</th>${canDelete ? "<th></th>" : ""}</tr></thead>
      <tbody>${issues.map(i => `<tr class="clickable" data-issue="${i.id}">
        <td>${fmtDateTime(i.created_at)}</td>${mine ? "" : `<td>${esc(i.agent_name)}</td>`}
        <td>${i.item_count} ${t("items_n")}</td><td>${esc(i.note || "—")}</td>
        ${canDelete ? `<td><button class="link-btn danger sm" data-rmissue="${i.id}">${t("delete")}</button></td>` : ""}</tr>`).join("")
      || `<tr><td colspan="5" class="empty">${t("none")}</td></tr>`}</tbody></table></div>`;
  if ($("add-issue")) $("add-issue").addEventListener("click", () => issueForm());
  v.querySelectorAll("[data-issue]").forEach(tr => tr.addEventListener("click", (e) => {
    if (e.target.dataset.rmissue !== undefined) return;
    issueDetail(tr.dataset.issue);
  }));
  v.querySelectorAll("[data-rmissue]").forEach(b => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (confirm(t("confirm_delete"))) {
      const r = await API.del("/issues/" + b.dataset.rmissue);
      if (handledOffline(r, b.closest("tr"))) return;
      navigate("issues");
    }
  }));
}

function issueForm() {
  const isManager = role() === "admin" || role() === "manager";
  const agentOpts = (cache.agents || []).map(a => ({ v: a.id, l: a.full_name }));
  const chemOpts = (cache.chemicals || []).map(c => ({ v: c.id, l: `${localized(c, "name")} (${c.quantity_in_stock} ${c.unit})` }));
  openModal(t("new_issue"), `<form id="isf">
    ${isManager ? field(t("engineer"), "agent_id", { options: agentOpts }) : ""}
    <div class="full"><label>${t("materials")}</label>
      <table class="li-table"><thead><tr><th>${t("material")}</th><th>${t("qty")}</th><th></th></tr></thead>
      <tbody id="iss-body"></tbody></table>
      <button type="button" class="btn secondary sm" id="iss-add" style="margin-top:8px">+ ${t("add_material")}</button></div>
    ${field(t("notes"), "note", { textarea: true, cls: "full" })}
    <div class="form-actions"><button type="button" class="btn secondary" id="isf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    const chemSelect = () => `<select class="iss-chem">${chemOpts.map(o => `<option value="${esc(o.v)}">${esc(o.l)}</option>`).join("")}</select>`;
    const addRow = () => {
      const tr = document.createElement("tr"); tr.className = "li-row";
      tr.innerHTML = `<td>${chemSelect()}</td>
        <td><input class="iss-qty" type="number" step="any" value="1" style="width:80px"></td>
        <td><button type="button" class="link-btn danger sm li-rm">✕</button></td>`;
      root.querySelector("#iss-body").appendChild(tr);
    };
    addRow();
    root.querySelector("#iss-add").addEventListener("click", addRow);
    root.addEventListener("click", (e) => { if (e.target.classList.contains("li-rm")) e.target.closest("tr").remove(); });
    $("isf-x").addEventListener("click", closeModal);
    root.querySelector("#isf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const items = [...root.querySelectorAll(".li-row")].map(r => ({
        chemical_id: r.querySelector(".iss-chem").value,
        quantity: parseFloat(r.querySelector(".iss-qty").value) || 0,
      })).filter(it => it.chemical_id && it.quantity > 0);
      if (!items.length) { alert(t("need_one_material")); return; }
      const body = { items, note: root.querySelector("[name=note]").value };
      const agentSel = root.querySelector("[name=agent_id]");
      if (agentSel) body.agent_id = agentSel.value;
      try {
        const saved = await API.post("/issues", body);
        if (handledOffline(saved)) return;
        closeModal(); toast(t("saved"));
        if (cache.chemicals) cache.chemicals = await API.get("/chemicals");  // reflect deducted stock
        navigate("issues");
      } catch (err) { alert(err.message); }
    });
  });
}

async function issueDetail(id) {
  const iss = await API.get("/issues/" + id);
  const rows = (iss.items || []).map(it =>
    `<tr><td>${esc(localized(it, "name"))}</td><td>${it.quantity} ${esc(it.unit)}</td></tr>`).join("");
  openModal(t("issue_detail"), `<div class="kv">
      <div>${t("engineer")}</div><div>${esc(iss.agent_name)}</div>
      <div>${t("date")}</div><div>${fmtDateTime(iss.created_at)}</div>
      <div>${t("notes")}</div><div>${esc(iss.note || "—")}</div></div>
    <table style="margin-top:12px"><thead><tr><th>${t("material")}</th><th>${t("qty")}</th></tr></thead><tbody>${rows}</tbody></table>
    <div class="form-actions"><button type="button" class="btn secondary" id="id-x">${t("close")}</button>
      ${can("issues.delete") ? `<button type="button" class="btn danger" id="id-del">${t("delete")}</button>` : ""}</div>`, (root) => {
    $("id-x").addEventListener("click", closeModal);
    if ($("id-del")) $("id-del").addEventListener("click", async () => {
      if (!confirm(t("confirm_delete"))) return;
      const r = await API.del("/issues/" + id);
      if (handledOffline(r)) return;
      closeModal(); toast(t("saved")); navigate("issues");
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
      ${can("invoices.view") ? `<button class="btn secondary sm" id="exp-inv">⬇ ${t("export_csv")}</button>` : ""}
      ${can("invoices.create") ? `<button class="btn" id="add-inv">+ ${dt === "quote" ? t("quote") : t("new_invoice")}</button>` : ""}</div></div>
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
    ${field(t("location_lbl"), "site_id", { options: [{ v: "", l: t("none") }], cls: "full" })}
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
    const clientSel = root.querySelector("[name=client_id]");
    const siteSel = root.querySelector("[name=site_id]");
    loadSiteOptions(preset.client_id || (clientSel && clientSel.value), siteSel, preset.site_id, t("none"));
    if (clientSel) clientSel.addEventListener("change", () => loadSiteOptions(clientSel.value, siteSel, null, t("none")));
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
        if (handledOffline(saved)) return;
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
        ${(inv.doc_type === "invoice" && inv.status !== "cancelled" && (inv.total - (inv.paid || 0)) > 0.009 && (role() === "client" || can("payments.create"))) ? `<button class="btn sm" id="pay-online">💳 ${t("pay_now")}</button>` : ""}
        ${can("invoices.edit") ? `<button class="btn secondary sm" id="edit-inv">✏️ ${t("edit")}</button>` : ""}
        ${(inv.doc_type === "quote" && can("invoices.edit") && inv.status !== "accepted") ? `<button class="btn sm" id="convert-inv">➡ ${t("convert_to_invoice")}</button>` : ""}
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
        ${can("payments.create") ? `<form id="pay-form" class="form-grid">
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
  if ($("pay-online")) $("pay-online").addEventListener("click", () => payInvoice(inv));
  if ($("edit-inv")) $("edit-inv").addEventListener("click", () => invoiceForm(inv));
  if ($("convert-inv")) $("convert-inv").addEventListener("click", async () => {
    try { const ni = await API.post(`/invoices/${inv.id}/convert`); if (handledOffline(ni)) return; toast(t("saved")); invoiceTab = "invoice"; navigate("invoice", { id: ni.id }); }
    catch (err) { alert(err.message); }
  });
  if ($("pay-form")) $("pay-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const saved = await API.post(`/invoices/${inv.id}/payments`, formData($("pay-form"))); if (handledOffline(saved)) return; toast(t("saved")); navigate("invoice", { id: inv.id }); }
    catch (err) { alert(err.message); }
  });
}

// Start an online payment for an invoice. Real gateways return a hosted
// checkout_url we redirect to; the built-in "manual" sandbox provider has no
// external page, so we confirm in-app and post the callback ourselves.
async function payInvoice(inv) {
  try {
    const r = await API.post(`/invoices/${inv.id}/pay`, {});
    if (r.provider === "manual") {
      if (!confirm(t("pay_sandbox_confirm").replace("{amt}", money(r.amount) + " " + (r.currency || "")))) return;
      await API.post(`/payments/callback/manual`, { token: r.token });
      toast(t("payment_done"));
      navigate("invoice", { id: inv.id });
    } else if (r.checkout_url) {
      window.location.href = r.checkout_url;   // hosted gateway checkout
    } else {
      alert(t("pay_unavailable"));
    }
  } catch (e) { alert(e.message); }
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
  printHtmlDoc(doc);
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
  printHtmlDoc(doc);
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
    const visit = await API.get(`/visits/${b.dataset.cert}?lang=${LANG}`);
    if (!visit.report || visit.report.status !== "complete") {
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
    ${field(t("license_no"), "license_no", { value: u.license_no })}
    ${field(t("license_expiry"), "license_expiry", { type: "date", value: u.license_expiry })}
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
      try { const saved = isEdit ? await API.put("/users/" + u.id, d) : await API.post("/users", d);
        if (handledOffline(saved)) return;
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
let notifLastMaxId = null;            // baseline; sound plays when it grows
let notifMuted = localStorage.getItem("notifMuted") === "1";
let _audioCtx = null;
// Short two-tone chime via Web Audio (no asset needed). Unlocked on first click.
function playNotifSound() {
  if (notifMuted) return;
  try {
    _audioCtx = _audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (_audioCtx.state === "suspended") _audioCtx.resume();
    const beep = (freq, at, dur) => {
      const o = _audioCtx.createOscillator(), g = _audioCtx.createGain();
      o.connect(g); g.connect(_audioCtx.destination); o.type = "sine"; o.frequency.value = freq;
      const t0 = _audioCtx.currentTime + at;
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(0.3, t0 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
      o.start(t0); o.stop(t0 + dur);
    };
    beep(880, 0, 0.35); beep(1175, 0.18, 0.4);
  } catch (e) {}
}
// Browsers require a user gesture before audio can play — unlock on first click.
document.addEventListener("click", function unlockAudio() {
  try {
    _audioCtx = _audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (_audioCtx.state === "suspended") _audioCtx.resume();
  } catch (e) {}
  document.removeEventListener("click", unlockAudio);
}, { once: true });

async function initNotifications() {
  if (role() === "client") { $("topbar-right").innerHTML = ""; return; }
  $("topbar-right").innerHTML = `<div class="bell-wrap">
    <button id="bell" class="icon-btn" style="font-size:20px;position:relative">🔔<span id="bell-count" class="bell-badge hidden">0</span></button>
    <div id="bell-menu" class="bell-menu hidden"></div></div>`;
  $("bell").addEventListener("click", toggleBell);
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".bell-wrap")) $("bell-menu").classList.add("hidden");
  });
  notifLastMaxId = null;   // reset baseline on (re)login so we don't replay old ones
  await refreshNotifications();
  clearInterval(notifTimer);
  notifTimer = setInterval(refreshNotifications, 60000);
}
async function refreshNotifications() {
  try {
    const d = await API.get(`/notifications?lang=${LANG}`);
    const c = $("bell-count");
    if (!c) return;
    if (d.unread > 0) { c.textContent = d.unread; c.classList.remove("hidden"); }
    else c.classList.add("hidden");
    window._notifs = d.items;
    // play a sound when a genuinely new notification has arrived since last poll
    const maxId = (d.items || []).reduce((m, n) => Math.max(m, n.id || 0), 0);
    if (notifLastMaxId !== null && maxId > notifLastMaxId && d.unread > 0) playNotifSound();
    notifLastMaxId = maxId;
  } catch (e) {}
}
function toggleBell() {
  const menu = $("bell-menu");
  if (!menu.classList.contains("hidden")) { menu.classList.add("hidden"); return; }
  const items = window._notifs || [];
  menu.innerHTML = `<div class="bell-head"><strong>${t("notifications_title")}</strong>
    <span style="display:flex;gap:10px;align-items:center">
      <button class="link-btn sm" id="bell-mute" title="${t("sound")}">${notifMuted ? "🔇" : "🔊"}</button>
      <button class="link-btn sm" id="bell-read">${t("mark_all_read")}</button></span></div>
    ${items.length ? items.map(n => `<div class="notif ${n.is_read ? "" : "unread"}" data-link="${n.link_view || ""}" data-id="${n.link_id || ""}">
      <div class="nt">${esc(n.title)}</div><div class="nb muted small">${esc(n.body || "")}</div></div>`).join("")
      : `<div class="empty">${t("no_notifications")}</div>`}`;
  menu.classList.remove("hidden");
  $("bell-mute").addEventListener("click", (e) => {
    e.stopPropagation();
    notifMuted = !notifMuted;
    localStorage.setItem("notifMuted", notifMuted ? "1" : "0");
    $("bell-mute").textContent = notifMuted ? "🔇" : "🔊";
    if (!notifMuted) playNotifSound();   // confirm sound when re-enabling
  });
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
  const agentFilter = can("users.view") ? `<select id="cal-agent"><option value="">${t("all")} ${t("nav_agents")}</option>
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
      ${dayVisits.slice(0, 4).map(vi => { const loc = vi.site_name || vi.location || ""; return `<div class="cal-ev b-${vi.status}" data-visit="${vi.id}" data-agent="${vi.agent_id || ""}"
        title="${esc(localized(vi, "client"))}${loc ? " — " + esc(loc) : ""}">${esc((localized(vi, "client") || "").slice(0, 16))}${loc ? `<span class="cal-ev-site">📍 ${esc(loc.slice(0, 16))}</span>` : ""}</div>`; }).join("")}
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
// Locations — every site across all clients
// ====================================================================
async function viewLocations(v) {
  // Every client is listed — with its sites, or a "no locations" row when it
  // has none yet (independent of whether the client has any contracts).
  const [clients, sites] = await Promise.all([API.get("/clients"), API.get("/sites")]);
  const list = Array.isArray(clients) ? clients : (clients.items || []);
  list.sort((a, b) => localized(a, "name").localeCompare(localized(b, "name")));
  const byClient = {};
  sites.forEach(s => { (byClient[s.client_id] = byClient[s.client_id] || []).push(s); });
  const canEdit = can("clients.edit");
  const clink = c => `<a href="#" class="link-btn" data-open="${c.id}">${esc(localized(c, "name"))}</a>`;
  const locCell = addr => addr
    ? `<a class="link-btn" href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(addr)}" target="_blank" rel="noopener">📍 ${t("map_pin")}</a>` : "—";
  // The site-map column: a thumbnail of the uploaded map picture (if any) plus
  // an upload/replace button and a remove button (for editors).
  const mapImgCell = s => {
    const thumb = s.map_image
      ? `<a href="/uploads/${esc(s.map_image)}" target="_blank" rel="noopener" title="${t("view_map_img")}"><img src="/uploads/${esc(s.map_image)}" alt="" style="height:36px;border-radius:6px;border:1px solid var(--line);vertical-align:middle"></a>` : "";
    const editBtn = canEdit
      ? `<button class="link-btn sm" data-mapedit="${s.id}">✏️ ${s.map_image ? t("replace") : t("upload_site_map")}</button>` : "";
    const delBtn = (canEdit && s.map_image)
      ? `<button class="link-btn danger sm" data-mapdel="${s.id}">✕</button>` : "";
    const parts = [thumb, editBtn, delBtn].filter(Boolean);
    return parts.length ? `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">${parts.join("")}</div>` : "—";
  };
  // Coordinates cell: a maps pin when the site is geocoded (needed for route
  // optimization), plus an edit button to set / change them.
  const geoCell = s => {
    const has = s.lat != null && s.lng != null;
    const pin = has
      ? `<a class="link-btn" href="https://www.google.com/maps/search/?api=1&query=${s.lat},${s.lng}" target="_blank" rel="noopener">📍 ${(+s.lat).toFixed(4)}, ${(+s.lng).toFixed(4)}</a>`
      : `<span class="muted small">${t("no_coords")}</span>`;
    const edit = canEdit ? `<button class="link-btn sm" data-siteedit="${s.id}">✏️</button>` : "";
    return `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">${pin}${edit}</div>`;
  };
  const byId = {};
  sites.forEach(s => { byId[s.id] = s; });
  const rows = list.map(c => {
    const cs = byClient[c.id] || [];
    if (!cs.length) {
      return `<tr data-client="${c.id}"><td>${clink(c)}</td>
        <td colspan="5" class="muted">${t("no_locations")}</td><td>—</td></tr>`;
    }
    return cs.map(s => `<tr data-client="${c.id}"><td>${clink(c)}</td>
      <td>${esc(s.name)}</td><td>${esc(s.address || "—")}</td><td>${esc(s.area || "—")}</td>
      <td>${geoCell(s)}</td><td>${locCell(s.address)}</td><td>${mapImgCell(s)}</td></tr>`).join("");
  }).join("");
  v.innerHTML = `<div class="page-head"><h2>${t("locations_title")}</h2>
    <span class="muted">${list.length} ${t("nav_clients")} · ${sites.length} ${t("sites_count")}</span></div>
    <div class="panel">
      <input id="loc-q" type="search" placeholder="${t("search")}…" style="margin-bottom:12px;max-width:340px;width:100%">
      <table><thead><tr><th>${t("client")}</th><th>${t("site_name")}</th><th>${t("address_en")}</th>
        <th>${t("area")}</th><th>${t("coordinates")}</th><th>${t("location_lbl")}</th><th>${t("site_map")}</th></tr></thead>
      <tbody id="loc-body">${rows || `<tr><td colspan="7" class="empty">${t("none")}</td></tr>`}</tbody>
      </table></div>`;
  v.querySelectorAll("[data-siteedit]").forEach(b => b.addEventListener("click", () =>
    siteForm(byId[b.dataset.siteedit].client_id, byId[b.dataset.siteedit], () => navigate("locations"))));
  v.querySelectorAll("[data-open]").forEach(a => a.addEventListener("click", (e) => {
    e.preventDefault(); navigate("client", { id: a.dataset.open });
  }));
  v.querySelectorAll("[data-mapedit]").forEach(b => b.addEventListener("click", () =>
    siteMapDialog(b.dataset.mapedit, () => navigate("locations"))));
  v.querySelectorAll("[data-mapdel]").forEach(b => b.addEventListener("click", async () => {
    if (confirm(t("confirm_delete"))) { await API.del("/sites/" + b.dataset.mapdel + "/map"); navigate("locations"); }
  }));
  const q = $("loc-q");
  if (q) q.addEventListener("input", () => {
    const term = q.value.trim().toLowerCase();
    v.querySelectorAll("#loc-body tr").forEach(tr =>
      tr.classList.toggle("hidden", !!term && !tr.textContent.toLowerCase().includes(term)));
  });
}
// Upload / replace the map-design picture for a single site.
function siteMapDialog(siteId, after) {
  openModal(t("upload_site_map"), `<form id="smf">
    <div class="field"><label>${t("site_map")}</label>
      <input type="file" name="file" accept="image/*" required />
      <div class="muted small">${t("site_map_hint")}</div></div>
    <div class="form-actions"><button type="button" class="btn secondary" id="smf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("upload")}</button></div></form>`, (root) => {
    $("smf-x").addEventListener("click", closeModal);
    root.querySelector("#smf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const file = root.querySelector("[name=file]").files[0];
      if (!file) return;
      try { await API.uploadSiteMap(siteId, file); closeModal(); toast(t("saved")); after && after(); }
      catch (err) { alert(err.message); }
    });
  });
}

// ====================================================================
// Contracts (recurring)
// ====================================================================
const FREQS = ["weekly", "biweekly", "monthly", "quarterly", "semiannual", "annual"];
// Maps a location string to a Google Maps URL (a maps link is used as-is;
// anything else is treated as a search query / coordinates).
function mapsUrl(loc) {
  const v = (loc || "").trim();
  return /^https?:\/\//i.test(v) ? v : "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(v);
}
// One contract: the summary row plus a hidden detail row listing its sites.
function contractRow(c, ncols) {
  const sites = c.sites || [];
  const acts = can("contracts.edit") || can("contracts.delete");
  const toggle = sites.length
    ? `<button class="link-btn sm ct-toggle" data-toggle="${c.id}" style="margin-inline-end:6px">▸</button>` : "";
  const main = `<tr data-row="${c.id}"><td>${toggle}${esc(localized(c, "client"))}
      ${sites.length ? `<span class="muted small">(${sites.length} ${t("sites_count")})</span>` : ""}</td>
    <td>${esc(localized(c, "service") || "—")}</td>
    <td>${esc(c.agent_name || "—")}</td><td>${t("freq_" + c.frequency)}${c.auto_invoice ? `<br><span class="badge b-active" title="${esc(t("auto_bill_on_hint"))}">💸 ${t("auto_bill")}${c.next_bill_date ? " · " + fmtDate(c.next_bill_date) : ""}</span>` : ""}</td><td>${fmtDate(c.next_run_date)}</td>
    <td>${money(c.price)}</td><td><span class="badge b-${c.status === "active" ? "active" : "inactive"}">${t("ct_" + c.status)}</span></td>
    ${acts ? `<td>${can("contracts.edit") ? `<button class="link-btn sm" data-edit="${c.id}">${t("edit")}</button>` : ""}${(can("contracts.edit") && can("contracts.delete")) ? " · " : ""}${can("contracts.delete") ? `<button class="link-btn danger sm" data-del="${c.id}">${t("delete")}</button>` : ""}</td>` : ""}</tr>`;
  if (!sites.length) return main;
  const rows = sites.map(s => `<tr><td>${esc(s.site_name || t("unassigned"))}</td>
    <td>${s.map_location ? `<a href="${esc(mapsUrl(s.map_location))}" target="_blank" rel="noopener">📍 ${esc(s.map_location)}</a>` : "—"}</td>
    <td style="text-align:end">${money(s.price)}</td></tr>`).join("");
  const detail = `<tr class="ct-sites-detail hidden" data-detail="${c.id}"><td colspan="${ncols}">
    <table><thead><tr><th>${t("location_lbl")}</th><th>${t("map_pin")}</th><th style="text-align:end">${t("price")}</th></tr></thead>
    <tbody>${rows}</tbody></table></td></tr>`;
  return main + detail;
}
async function viewContracts(v) {
  const list = await API.get("/contracts");
  const ncols = 7 + ((can("contracts.edit") || can("contracts.delete")) ? 1 : 0);
  v.innerHTML = `<div class="page-head"><h2>${t("contracts_title")}</h2>
    <div style="display:flex;gap:8px">
      ${can("contracts.edit") ? `<button class="btn secondary" id="run-ct">⚙ ${t("run_now")}</button>` : ""}
      ${can("invoices.create") ? `<button class="btn secondary" id="bill-ct">💸 ${t("bill_now")}</button>` : ""}
      ${can("contracts.create") ? `<button class="btn" id="add-ct">+ ${t("new_contract")}</button>` : ""}</div></div>
    <div class="panel"><table><thead><tr><th>${t("client")}</th><th>${t("service")}</th><th>${t("agent")}</th>
      <th>${t("frequency")}</th><th>${t("next_run")}</th><th>${t("price")}</th><th>${t("contract_status")}</th>${(can("contracts.edit") || can("contracts.delete")) ? `<th></th>` : ""}</tr></thead>
      <tbody>${list.map(c => contractRow(c, ncols)).join("")
        || `<tr><td colspan="${ncols}" class="empty">${t("none")}</td></tr>`}</tbody></table></div>`;
  if ($("add-ct")) $("add-ct").addEventListener("click", () => contractForm());
  if ($("run-ct")) $("run-ct").addEventListener("click", async () => {
    const r = await API.post("/contracts/run", {}); toast(`${r.created} ${t("generated")}`); navigate("contracts");
  });
  if ($("bill-ct")) $("bill-ct").addEventListener("click", async () => {
    const r = await API.post("/contracts/bill", {}); toast(`${r.created} ${t("invoices_generated")}`); navigate("contracts");
  });
  v.querySelectorAll("[data-edit]").forEach(b => b.addEventListener("click", () => contractForm(list.find(c => c.id == b.dataset.edit))));
  v.querySelectorAll("[data-del]").forEach(b => b.addEventListener("click", async () => {
    if (confirm(t("confirm_delete"))) { const r = await API.del("/contracts/" + b.dataset.del); if (handledOffline(r, b.closest("tr"))) return; navigate("contracts"); }
  }));
  v.querySelectorAll("[data-toggle]").forEach(b => b.addEventListener("click", () => {
    const det = v.querySelector(`[data-detail="${b.dataset.toggle}"]`);
    if (!det) return;
    const open = det.classList.toggle("hidden");
    b.textContent = open ? "▸" : "▾";
  }));
}
// Lazily load the Google Maps JS API (Places library) using the key saved in
// Settings. Resolves to the google namespace, or null when no key is configured.
// The promise is cached so the script is only ever injected once.
let _mapsPromise = null;
function ensureMapsApi() {
  const key = (typeof SETTINGS !== "undefined" && SETTINGS && SETTINGS.google_maps_api_key) || "";
  if (!key) return Promise.resolve(null);
  if (_mapsPromise) return _mapsPromise;
  _mapsPromise = new Promise((resolve, reject) => {
    if (window.google && window.google.maps && window.google.maps.places) return resolve(window.google);
    window.__crmMapsReady = () => resolve(window.google);
    const sc = document.createElement("script");
    sc.src = "https://maps.googleapis.com/maps/api/js?key=" + encodeURIComponent(key) +
             "&libraries=places&callback=__crmMapsReady";
    sc.async = true;
    sc.onerror = () => { _mapsPromise = null; reject(new Error("maps load failed")); };
    document.head.appendChild(sc);
  });
  return _mapsPromise;
}
// Attach Places search to a contract site-location input (no-op without a key).
function attachPlaces(input) {
  ensureMapsApi().then(g => {
    if (!g || input.dataset.ac) return;
    input.dataset.ac = "1";
    const ac = new g.maps.places.Autocomplete(input, { fields: ["formatted_address", "geometry", "name"] });
    ac.addListener("place_changed", () => {
      const p = ac.getPlace();
      if (p && p.geometry) input.dataset.latlng = p.geometry.location.lat() + "," + p.geometry.location.lng();
      if (p && !input.value && p.name) input.value = p.name;
    });
  }).catch(() => {});
}

function contractForm(c) {
  const isEdit = !!c; c = c || {};
  const clientOpts = cache.clients.map(x => ({ v: x.id, l: localized(x, "name") }));
  const svcOpts = [{ v: "", l: t("none") }].concat(cache.services.map(s => ({ v: s.id, l: localized(s, "name") })));
  const freqOpts = FREQS.map(f => ({ v: f, l: t("freq_" + f) }));
  const statusOpts = ["active", "paused", "ended"].map(s => ({ v: s, l: t("ct_" + s) }));
  openModal(isEdit ? t("edit") : t("new_contract"), `<form id="ctf"><div class="form-grid">
    ${field(t("client"), "client_id", { options: clientOpts, value: c.client_id, cls: "full" })}
    ${field(t("service"), "service_type_id", { options: svcOpts, value: c.service_type_id })}
    ${field(t("frequency"), "frequency", { options: freqOpts, value: c.frequency || "monthly" })}
    ${field(t("start_date"), "start_date", { type: "date", value: c.start_date })}
    ${field(t("end_date"), "end_date", { type: "date", value: c.end_date })}
    ${isEdit ? field(t("contract_status"), "status", { options: statusOpts, value: c.status }) : ""}
    ${field(t("notes"), "notes", { textarea: true, cls: "full", value: c.notes })}
    <div class="field full"><label class="muted small" style="display:flex;align-items:center;gap:8px">
      <input type="checkbox" id="ct-auto" style="width:auto" ${c.auto_invoice ? "checked" : ""}> ${t("auto_bill")}</label>
      <div class="muted small">${t("auto_bill_hint")}</div></div>
    ${field(t("bill_every"), "bill_every", { options: [{ v: "", l: t("bill_same_as_service") }].concat(FREQS.map(f => ({ v: f, l: t("freq_" + f) }))), value: c.bill_every || "" })}
    ${field(t("next_bill_date"), "next_bill_date", { type: "date", value: c.next_bill_date })}
    </div>
    <div class="section-title" style="margin-top:14px"><h3>${t("sites_pricing")}</h3>
      <button type="button" class="btn sm" id="ct-add-site">+ ${t("add_site_row")}</button></div>
    <table class="table"><thead><tr>
      <th>${t("location_lbl")}</th><th>${t("map_location")}</th><th style="width:110px">${t("price")}</th><th></th>
    </tr></thead><tbody id="ct-sites-body"></tbody>
    <tfoot><tr><td colspan="2" style="text-align:right"><b>${t("total")}</b></td>
      <td><b id="ct-total">0</b></td><td></td></tr></tfoot></table>
    <div class="form-actions"><button type="button" class="btn secondary" id="ctf-x">${t("cancel")}</button>
    <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    const clientSel = root.querySelector("[name=client_id]");
    const tbody = root.querySelector("#ct-sites-body");
    let clientSites = [];

    const siteOptionsHtml = (selected) =>
      [`<option value="">${esc(t("none"))}</option>`].concat(clientSites.map(s =>
        `<option value="${esc(s.id)}" ${String(s.id) === String(selected || "") ? "selected" : ""}>${esc(s.name)}</option>`)).join("");

    function recalc() {
      let tot = 0;
      tbody.querySelectorAll(".cs-price").forEach(i => { tot += parseFloat(i.value) || 0; });
      root.querySelector("#ct-total").textContent = tot.toFixed(2);
    }
    function addRow(row) {
      row = row || {};
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td><select class="cs-site">${siteOptionsHtml(row.site_id)}</select></td>
         <td style="display:flex;gap:4px;align-items:center">
           <input class="cs-map" type="text" placeholder="https://maps.google.com/…" value="${esc(row.map_location || "")}" style="flex:1" />
           <button type="button" class="link-btn cs-open" title="${esc(t("open_map"))}">📍</button></td>
         <td><input class="cs-price" type="number" min="0" step="0.01" value="${esc(row.price != null ? row.price : 0)}" style="width:100px" /></td>
         <td><button type="button" class="link-btn danger sm cs-rm">${esc(t("delete"))}</button></td>`;
      tbody.appendChild(tr);
      const mapInput = tr.querySelector(".cs-map");
      tr.querySelector(".cs-rm").addEventListener("click", () => { tr.remove(); recalc(); });
      tr.querySelector(".cs-price").addEventListener("input", recalc);
      tr.querySelector(".cs-open").addEventListener("click", () => {
        const v = mapInput.value.trim();
        const q = mapInput.dataset.latlng || v;
        if (!q) return;
        const url = /^https?:\/\//i.test(q) ? q : "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(q);
        window.open(url, "_blank", "noopener");
      });
      if (row.latlng) mapInput.dataset.latlng = row.latlng;
      attachPlaces(mapInput);   // Places search when a Maps API key is configured
      recalc();
    }
    async function loadSites(clientId) {
      clientSites = [];
      if (clientId) { try { const cl = await API.get("/clients/" + clientId); clientSites = cl.sites || []; } catch (e) {} }
      tbody.querySelectorAll(".cs-site").forEach(sel => { sel.innerHTML = siteOptionsHtml(sel.value); });
    }

    root.querySelector("#ct-add-site").addEventListener("click", () => addRow());
    clientSel.addEventListener("change", () => loadSites(clientSel.value));

    (async () => {
      await loadSites(clientSel.value);
      const existing = c.sites || [];
      if (existing.length) existing.forEach(addRow);
      else addRow();   // start with one empty row
    })();

    $("ctf-x").addEventListener("click", closeModal);
    root.querySelector("#ctf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = formData(root);
      d.auto_invoice = root.querySelector("#ct-auto").checked ? 1 : 0;
      Object.keys(d).forEach(k => { if (d[k] === "") delete d[k]; });
      d.sites = Array.from(tbody.querySelectorAll("tr")).map(tr => ({
        site_id: tr.querySelector(".cs-site").value || null,
        map_location: tr.querySelector(".cs-map").value.trim() || null,
        price: parseFloat(tr.querySelector(".cs-price").value) || 0,
      })).filter(r => r.site_id || r.map_location || r.price);
      try { const saved = isEdit ? await API.put("/contracts/" + c.id, d) : await API.post("/contracts", d);
        if (handledOffline(saved)) return;
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
// Date-range presets for the company analytics page. Returns {from,to} ISO.
function anRange(preset) {
  const today = new Date();
  const iso = (d) => d.toISOString().slice(0, 10);
  const to = iso(today);
  const back = (fn) => { const d = new Date(today); fn(d); return iso(d); };
  if (preset === "month") return { from: iso(new Date(today.getFullYear(), today.getMonth(), 1)), to };
  if (preset === "quarter") return { from: back(d => d.setMonth(d.getMonth() - 3)), to };
  if (preset === "all") return { from: "2000-01-01", to };
  return { from: back(d => d.setFullYear(d.getFullYear() - 1)), to };   // "year" (default)
}
let _anPreset = "year";

async function viewAnalytics(v) {
  const presets = [["month", t("range_month")], ["quarter", t("range_quarter")],
    ["year", t("range_year")], ["all", t("range_all")]];
  v.innerHTML = `<div class="page-head"><h2>${t("analytics_title")}</h2>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <select id="an-range" class="toolbar-select">${presets.map(([k, l]) =>
          `<option value="${k}"${k === _anPreset ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>
        <button class="btn sm secondary" id="an-csv">⬇️ ${t("export_csv")}</button>
        <button class="btn sm" id="an-pdf">🖨️ ${t("export_pdf")}</button></div></div>
    <div id="an-body">${t("loading")}</div>`;
  const render = async () => {
    const { from, to } = anRange(_anPreset);
    const a = await API.get(`/analytics?from=${from}&to=${to}`);
    const parts = analyticsParts(a);
    $("an-body").innerHTML = parts.html;
    // drill-downs
    $("an-body").querySelectorAll("[data-nav]").forEach(el =>
      el.addEventListener("click", () => navigate(el.dataset.nav)));
    $("an-pdf").onclick = () => analyticsReportDoc(t("analytics_title"),
      `${fmtDate(a.range.from)} → ${fmtDate(a.range.to)}`, parts.html);
    $("an-csv").onclick = () => exportAnalyticsCsv(a);
  };
  $("an-range").addEventListener("change", (e) => { _anPreset = e.target.value; render(); });
  await render();
}

// Build all analytics panels from the payload. Returns { html }.
function analyticsParts(a) {
  const T = a.totals, F = a.fleet || { kpi: {}, months: [], top_clients: [], replaced: {} };
  const empty = `<div class="empty">${t("none")}</div>`;
  const card = (val, label, icon, cls, nav) =>
    `<div class="stat-card ${cls}"${nav ? ` data-nav="${nav}" style="cursor:pointer"` : ""}><div class="sc-ic">${icon}</div><div><div class="v">${val}</div><div class="l">${esc(label)}</div></div></div>`;
  const barList = (rows, fn, nav) => rows.length
    ? `<div${nav ? ` data-nav="${nav}" style="cursor:pointer"` : ""}>${rows.map(fn).join("")}</div>` : empty;
  const maxAge = Math.max(1, ...a.ar_aging.map(x => x.due || 0));
  const maxAgent = Math.max(1, ...a.agents.map(x => x.total || 0));
  const agingLabels = { current: t("bucket_current"), "1-30": t("bucket_1_30"), "31-60": t("bucket_31_60"), "60+": t("bucket_60") };
  const labels = a.months.map(m => monthShort(m.m));
  // finance + operations cards
  const cards = `<div class="cards">
      ${card(money(T.revenue), t("total_revenue"), "💰", "c-green")}
      ${card(money(T.invoiced), t("total_invoiced"), "🧾", "c-blue")}
      ${card(T.collection_rate + "%", t("collection_rate"), "📥", T.collection_rate < 70 ? "warn" : "c-teal")}
      ${card(money(T.revenue_per_visit), t("revenue_per_visit"), "🧮", "c-purple")}
      ${card(`${T.visits_completed}/${T.visits_total}`, t("visits_completed"), "✅", "c-teal")}
      ${card(T.completion_rate + "%", t("completion_rate"), "🎯", T.completion_rate < 70 ? "warn" : "c-green")}
      ${card(T.sla_overdue, t("dash_sla_overdue"), "⏰", T.sla_overdue > 0 ? "danger" : "c-green", "schedule")}
      ${card(T.active_contracts, t("active_contracts"), "🔁", "c-blue")}</div>`;
  const revenue = a.months.some(m => m.total || m.paid)
    ? curveChart(labels, [
        { name: t("total_invoiced"), color: "#2563eb", values: a.months.map(m => m.total || 0) },
        { name: t("paid"), color: "#16a34a", values: a.months.map(m => m.paid || 0) }]) : empty;
  const aging = barList(a.ar_aging, x =>
    bar(agingLabels[x.bucket] || x.bucket, x.due || 0, maxAge, x.bucket === "60+" ? "var(--red)" : "var(--amber)"), "invoices");
  const agents = barList(a.agents, x =>
    bar(x.full_name + ` (${x.completed}/${x.total})`, x.total || 0, maxAgent), "agents");
  const chemItems = a.chemicals.map((x, i) => ({ label: localized(x, "name") + ` (${x.unit})`, value: Math.round((x.used || 0) * 10) / 10, color: PALETTE[i % PALETTE.length] }));
  const svcItems = a.services.map((x, i) => ({ label: localized(x, "name"), value: x.cnt, color: PALETTE[(i + 2) % PALETTE.length] }));
  // fleet & pest
  const K = F.kpi;
  const fleetCards = `<div class="cards">
      ${card(K.devices || 0, t("total_devices"), "📍", "c-blue")}
      ${card(K.coverage != null ? K.coverage + "%" : "—", t("coverage_month"), "✅", (K.coverage != null && K.coverage < 60) ? "warn" : "c-green", "devices")}
      ${card(K.needs_service || 0, t("mst_needs_service"), "🛠️", K.needs_service > 0 ? "warn" : "c-green", "devices")}
      ${card(K.activity || 0, t("activity_detections"), "🐭", K.activity > 0 ? "danger" : "c-green")}</div>`;
  const hasFly = F.months.some(m => m.fly != null), hasBait = F.months.some(m => m.bait_pct != null);
  const activityCurve = F.months.some(m => m.inspections || m.detections)
    ? curveChart(labels, [
        { name: t("inspections"), color: "#2563eb", values: F.months.map(m => m.inspections) },
        { name: t("detections"), color: "#dc2626", values: F.months.map(m => m.detections) }]) : empty;
  const pressure = (hasFly || hasBait) ? curveChart(labels, [
      ...(hasFly ? [{ name: t("avg_fly"), color: "#7c3aed", values: F.months.map(m => m.fly || 0) }] : []),
      ...(hasBait ? [{ name: t("bait_consumption"), color: "#d97706", values: F.months.map(m => m.bait_pct || 0) }] : [])]) : "";
  // service-coverage-over-time trend (% of fleet scanned each month)
  const coverageCurve = F.months.some(m => m.coverage) ? curveChart(labels, [
      { name: t("service_coverage"), color: "#16a34a", values: F.months.map(m => m.coverage || 0) }]) : "";
  // devices overdue for a scan (never / not in N days)
  const stale = F.stale || [];
  const staleTable = stale.length ? `<table><thead><tr><th>${t("code")}</th><th>${t("marker_type")}</th>
      <th>${t("client")}</th><th>${t("location_lbl")}</th><th>${t("last_scanned")}</th></tr></thead><tbody>${stale.map(x => `<tr>
      <td><strong>${esc(x.code)}</strong></td><td>${devIcon(x.type)} ${esc(t("dt_" + x.type))}</td>
      <td>${esc(localized(x, "client") || "—")}</td><td>${esc(x.loc || x.site_name || "—")}</td>
      <td>${x.last_seen ? fmtDate(x.last_seen) : `<span class="warn-line">${t("never_scanned")}</span>`}</td></tr>`).join("")}</tbody></table>` : "";
  const maxTop = Math.max(1, ...F.top_clients.map(x => x.detections || 0));
  const topClients = barList(F.top_clients, x => bar(localized(x, "name"), x.detections || 0, maxTop, "var(--red)"));
  const rep = F.replaced || {};
  const repItems = [["baits", "baits_count"], ["lamps", "lamps_used"], ["sheets", "light_sheets_used"], ["glue_boards", "glo_pieces"]]
    .filter(([k]) => (rep[k] || 0) > 0).map(([k, tk], i) => ({ label: t(tk), value: rep[k], color: PALETTE[i % PALETTE.length] }));
  const showFleet = (K.devices || 0) > 0;
  const html = `${cards}
    <div class="grid-2">
      <div class="panel"><h3>📈 ${t("monthly_revenue")}</h3>${revenue}</div>
      <div class="panel"><h3>${t("ar_aging")}</h3>${aging}</div>
      <div class="panel"><h3>${t("agent_productivity")}</h3>${agents}</div>
      <div class="panel"><h3>${t("service_mix")}</h3>${svcItems.length ? cols3d(svcItems) : empty}</div>
      <div class="panel"><h3>${t("chemical_usage")}</h3>${chemItems.length ? cols3d(chemItems) : empty}</div>
    </div>
    ${showFleet ? `<div class="section-title" style="margin-top:8px"><h2>🏷️ ${t("nav_devices")} — ${t("pest_trends")}</h2></div>
      ${fleetCards}
      <div class="grid-2">
        <div class="panel"><h3>📈 ${t("pest_trends")}</h3>${activityCurve}</div>
        <div class="panel"><h3>✅ ${t("service_coverage")}</h3>${coverageCurve || empty}</div>
      </div>
      ${pressure ? `<div class="panel"><h3>🪰 ${t("pest_pressure")}</h3>${pressure}</div>` : ""}
      <div class="grid-2">
        <div class="panel"><h3>🔥 ${t("top_clients_activity")}</h3>${topClients}</div>
        ${repItems.length ? `<div class="panel"><h3>🔧 ${t("consumables_replaced")}</h3>${cols3d(repItems)}</div>` : ""}
      </div>
      ${F.stale_count ? `<div class="panel" data-nav="devices" style="cursor:pointer">
        <h3 style="display:flex;justify-content:space-between;align-items:center">
          <span>⏰ ${t("overdue_devices")}</span><span class="badge b-draft">${F.stale_count}</span></h3>
        <div class="muted small" style="margin:-4px 0 8px">${t("overdue_devices_hint").replace("{n}", F.stale_days)}</div>
        ${staleTable}</div>` : ""}` : ""}`;
  return { html };
}

// Multi-section CSV of the analytics payload → downloaded file.
function exportAnalyticsCsv(a) {
  const T = a.totals, F = a.fleet || {};
  const esc = (s) => `"${String(s == null ? "" : s).replace(/"/g, '""')}"`;
  const lines = [];
  const sec = (title, header, rows) => {
    lines.push(esc(title));
    if (header) lines.push(header.map(esc).join(","));
    rows.forEach(r => lines.push(r.map(esc).join(",")));
    lines.push("");
  };
  sec(t("analytics_title"), ["from", "to"], [[a.range.from, a.range.to]]);
  sec(t("summary"), ["metric", "value"], [
    [t("total_revenue"), T.revenue], [t("total_invoiced"), T.invoiced],
    [t("collection_rate"), T.collection_rate + "%"], [t("revenue_per_visit"), T.revenue_per_visit],
    [t("visits_completed"), `${T.visits_completed}/${T.visits_total}`],
    [t("completion_rate"), T.completion_rate + "%"], [t("dash_sla_overdue"), T.sla_overdue],
    [t("active_contracts"), T.active_contracts]]);
  sec(t("monthly_revenue"), ["month", "invoiced", "paid"], a.months.map(m => [m.m, m.total, m.paid]));
  sec(t("agent_productivity"), ["agent", "completed", "total"], a.agents.map(x => [x.full_name, x.completed, x.total]));
  sec(t("chemical_usage"), ["name", "unit", "used"], a.chemicals.map(x => [localized(x, "name"), x.unit, x.used]));
  sec(t("service_mix"), ["service", "visits"], a.services.map(x => [localized(x, "name"), x.cnt]));
  if (F.top_clients) sec(t("top_clients_activity"), ["client", "detections"], F.top_clients.map(x => [localized(x, "name"), x.detections]));
  if (F.stale) sec(t("overdue_devices"), ["code", "type", "client", "location", "last_scanned"],
    F.stale.map(x => [x.code, t("dt_" + x.type), localized(x, "client"), x.loc || x.site_name || "", x.last_seen || t("never_scanned")]));
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = `analytics_${a.range.from}_${a.range.to}.csv`;
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
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
        ${field(t("google_maps_api_key"), "google_maps_api_key", { value: s.google_maps_api_key, cls: "full" })}
        <div class="field full"><label>📍 ${esc(t("company_geo"))} <span class="muted small">(${esc(t("company_geo_hint"))})</span></label>
          <div style="display:flex;gap:6px">
            <input type="text" name="company_geo" value="${esc(s.company_geo || "")}" placeholder="lat,lng" style="flex:1" />
            <button type="button" class="btn secondary sm" id="set-geo">📍 ${esc(t("use_my_location"))}</button>
          </div></div>
      </div>
      <p class="muted small" style="margin:-4px 0 8px">${t("maps_key_hint")}</p>
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
  if ($("set-geo")) $("set-geo").addEventListener("click", () => {
    if (!navigator.geolocation) { alert(t("geo_unsupported")); return; }
    $("set-geo").textContent = "…";
    navigator.geolocation.getCurrentPosition(
      (p) => { $("set-form").querySelector("[name=company_geo]").value = p.coords.latitude.toFixed(6) + "," + p.coords.longitude.toFixed(6); $("set-geo").textContent = "📍 " + t("use_my_location"); },
      () => { alert(t("geo_failed")); $("set-geo").textContent = "📍 " + t("use_my_location"); });
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
      try { const saved = await API.post(`/visits/${visitId}/signature`, body); if (handledOffline(saved)) return; closeModal(); toast(t("saved")); navigate("visit", { id: visitId }); }
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
  const K = tr.kpis || {};
  const labels = tr.months.map(m => monthShort(m.m));
  const statusColors = { ok: "#16a34a", needs_service: "#d97706", activity: "#dc2626", missing: "#64748b" };
  const sc = (val, label, icon, cls) => `<div class="stat-card ${cls}"><div class="sc-ic">${icon}</div><div><div class="v">${val}</div><div class="l">${label}</div></div></div>`;
  const typeItems = tr.by_type.map((r, i) => ({
    label: r.type ? t("dt_" + r.type) : t("none"), value: r.detections,
    color: PALETTE[(i + 1) % PALETTE.length] }));
  const trend = curveChart(labels, [
    { name: t("inspections"), color: "#2563eb", values: tr.months.map(m => m.inspections) },
    { name: t("detections"), color: "#dc2626", values: tr.months.map(m => m.detections) }]);
  // Pest-pressure curve (fly counts + bait consumption %) — only when we have data.
  const hasFly = tr.months.some(m => m.fly != null);
  const hasBait = tr.months.some(m => m.bait_pct != null);
  const pressure = (hasFly || hasBait) ? `<div class="panel"><h3>🪰 ${t("pest_pressure")}</h3>${curveChart(labels, [
    ...(hasFly ? [{ name: t("avg_fly"), color: "#7c3aed", values: tr.months.map(m => m.fly || 0) }] : []),
    ...(hasBait ? [{ name: t("bait_consumption"), color: "#d97706", values: tr.months.map(m => m.bait_pct || 0) }] : [])])}</div>` : "";
  const hotRows = (tr.hotspots || []).map(h => `<tr>
      <td><strong>${esc(h.label || "#" + h.id)}</strong></td>
      <td>${esc(h.type ? t("dt_" + h.type) : "—")}</td>
      <td>${esc(h.loc || "—")}</td>
      <td class="num"><strong>${h.detections}</strong></td>
      <td><span style="display:inline-block;padding:2px 10px;border-radius:20px;font-size:11px;font-weight:700;color:#fff;background:${statusColors[h.status] || "#64748b"}">${esc(t("mst_" + h.status))}</span></td>
    </tr>`).join("") || `<tr><td colspan="5" class="empty">${t("no_trend_data")}</td></tr>`;
  // Consumables replaced (from scan follow-up) — restocking / billing signal.
  const rep = K.replaced || {};
  const repItems = [["baits", "baits_count"], ["lamps", "lamps_used"], ["sheets", "light_sheets_used"], ["glue_boards", "glo_pieces"]]
    .filter(([k]) => (rep[k] || 0) > 0)
    .map(([k, tk], i) => ({ label: t(tk), value: rep[k], color: PALETTE[i % PALETTE.length] }));
  return `
    <div class="cards">
      ${sc(T.devices, t("total_devices"), "📍", "c-blue")}
      ${sc(T.inspections, t("monitoring_events"), "🔎", "c-teal")}
      ${sc(T.detections, t("activity_detections"), "🐭", "danger")}
      ${sc(T.needs_service || 0, t("mst_needs_service"), "🛠️", "c-amber")}
      ${K.fly_avg != null ? sc(K.fly_avg, t("avg_fly"), "🪰", "c-purple") : ""}
      ${K.catch_rate != null ? sc(K.catch_rate + "%", t("catch_rate"), "🎯", "c-teal") : ""}</div>
    <div class="panel"><h3>📈 ${t("pest_trends")}</h3>${trend}</div>
    ${pressure}
    <div class="grid-2">
      <div class="panel"><h3>🐛 ${t("by_device_type")}</h3>${cols3d(typeItems)}</div>
      <div class="panel"><h3>🔥 ${t("device_hotspots")}</h3>
        <table><thead><tr><th>${t("code")}</th><th>${t("marker_type")}</th><th>${t("location_lbl")}</th>
        <th class="num">${t("detections")}</th><th>${t("marker_status")}</th></tr></thead>
        <tbody>${hotRows}</tbody></table></div>
    </div>
    ${repItems.length ? `<div class="panel"><h3>🔧 ${t("consumables_replaced")}</h3>${cols3d(repItems)}</div>` : ""}`;
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
  const c = await API.get("/clients/" + id);
  const sites = c.sites || [];
  // Location filter: total + each location (+ unassigned bucket when sites exist).
  const locOpts = [`<option value="">${esc(t("all_locations"))}</option>`]
    .concat(sites.map(s => `<option value="${s.id}">${esc(s.name)}</option>`));
  if (sites.length) locOpts.push(`<option value="none">${esc(t("unassigned"))}</option>`);
  const locName = (sid) => !sid ? t("all_locations")
    : sid === "none" ? t("unassigned")
    : (sites.find(s => String(s.id) === String(sid)) || {}).name || "";

  v.innerHTML = `
    <div class="breadcrumb" id="bc">← 📁 ${esc(localized(c, "name"))}</div>
    <div class="page-head"><h2>📊 ${t("company_analytics")} — ${esc(localized(c, "name"))}</h2>
      <div style="display:flex;gap:8px;align-items:center">
        ${sites.length ? `<label class="muted small">📍 ${t("location_lbl")}:</label>
          <select id="loc-filter">${locOpts.join("")}</select>` : ""}
        ${can("analytics.view") || role() === "client" ? `<button class="btn sm" id="audit-pack">📦 ${t("audit_pack")}</button>` : ""}
        <button class="btn sm" id="export-analytics">🖨️ ${t("export_pdf")}</button></div></div>
    <div id="analytics-body"><div class="empty">${t("loading")}</div></div>`;
  $("bc").addEventListener("click", () => navigate(role() === "client" ? "folder" : "client", { id }));

  let currentParts = null, currentSite = "";
  async function render(siteId) {
    currentSite = siteId || "";
    const body = $("analytics-body");
    body.innerHTML = `<div class="empty">${t("loading")}</div>`;
    const q = siteId ? `?site_id=${encodeURIComponent(siteId)}` : "";
    const [a, tr] = await Promise.all([
      API.get(`/clients/${id}/analytics${q}`),
      API.get(`/clients/${id}/pest-trends${q}`).catch(() => null)]);
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
    currentParts = parts;
    body.innerHTML = `
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
  }

  if ($("loc-filter")) $("loc-filter").addEventListener("change", (e) => render(e.target.value));
  $("export-analytics").addEventListener("click", () => {
    if (!currentParts) return;
    const sub = localized(c, "name") + " — " + locName(currentSite);
    printAnalytics(c, currentParts, sub);
  });
  if ($("audit-pack")) $("audit-pack").addEventListener("click", () => auditPackDialog(c, currentSite, locName));
  await render("");
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
  printHtmlDoc(doc);
}

function printAnalytics(c, parts, subtitle) {
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
  analyticsReportDoc(t("analytics_report"), subtitle || localized(c, "name"), body);
}

// ====================================================================
// One-click Audit Pack — the binder an auditor asks for, as one branded PDF.
// (Per-site service history, device trends, chemical usage log + SDS/labels,
//  technician licences, and corrective actions.)
// ====================================================================
function auditPackDialog(c, siteId, locName) {
  const iso = (d) => d.toISOString().slice(0, 10);
  const today = new Date();
  const from = new Date(today); from.setFullYear(from.getFullYear() - 1);
  openModal(`📦 ${t("audit_pack")}`, `<form id="apf">
    <p class="muted small" style="margin:0 0 12px">${esc(t("audit_pack_hint"))}</p>
    <div class="form-grid">
      ${field(t("from_date"), "from", { type: "date", value: iso(from) })}
      ${field(t("to_date"), "to", { type: "date", value: iso(today) })}
    </div>
    <p class="muted small">📍 ${esc(t("location_lbl"))}: <strong>${esc(locName(siteId))}</strong></p>
    <div class="form-actions"><button type="button" class="btn secondary" id="apf-x">${t("cancel")}</button>
    <button class="btn" type="submit">📦 ${t("audit_generate")}</button></div></form>`, (root) => {
    $("apf-x").addEventListener("click", closeModal);
    root.querySelector("#apf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = formData(root);
      closeModal();
      await generateAuditPack(c, siteId, locName(siteId), d.from, d.to);
    });
  });
}

async function generateAuditPack(c, siteId, siteName, from, to) {
  let data;
  try {
    const q = new URLSearchParams();
    if (siteId) q.set("site_id", siteId);
    if (from) q.set("from", from);
    if (to) q.set("to", to);
    data = await API.get(`/clients/${c.id}/audit-pack?${q.toString()}`);
  } catch (err) { alert(err.message); return; }
  const sub = localized(c, "name") + (siteName ? " — " + siteName : "");
  analyticsReportDoc(`📦 ${t("audit_pack")}`, sub, renderAuditPack(data));
}

function renderAuditPack(d) {
  const sevColors = { low: "#16a34a", medium: "#d97706", high: "#ef4444", critical: "#b91c1c" };
  const sevBadge = (s) => s ? `<span style="display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;color:#fff;background:${sevColors[s] || "#64748b"}">${esc(t("sev_" + s))}</span>` : "—";
  const S = d.summary;
  const sc = (val, label, icon, cls) => `<div class="stat-card ${cls}"><div class="sc-ic">${icon}</div><div><div class="v">${val}</div><div class="l">${esc(label)}</div></div></div>`;
  const rangeTxt = `${fmtDate(d.range.from)} → ${fmtDate(d.range.to)}`;

  // 1. cover / summary
  const cover = `<div class="panel" style="border-left:4px solid #16a34a">
      <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px">
        <div><strong>${esc(localized(d.client, "name"))}</strong>
          ${d.site ? ` · ${esc(d.site.name)}` : ` · ${esc(t("all_locations"))}`}</div>
        <div class="muted">${esc(t("audit_period"))}: <strong>${esc(rangeTxt)}</strong></div></div></div>
    <div class="cards">
      ${sc(S.visits, t("total_visits"), "🗓️", "c-blue")}
      ${sc(S.completed, t("visits_completed"), "✅", "c-green")}
      ${sc(S.signed, t("signed_reports"), "✍️", "c-teal")}
      ${sc(S.products, t("products_used"), "🧪", "c-purple")}
      ${sc(S.detections, t("activity_detections"), "🐭", "danger")}
      ${sc(S.corrective, t("corrective_actions"), "🛠️", "c-amber")}</div>`;

  // 2. device / pest-activity trend
  let trends = "";
  if (S.detections || (d.trend.months || []).some(m => m.inspections)) {
    const labels = d.trend.months.map(m => monthShort(m.m));
    const curve = curveChart(labels, [
      { name: t("inspections"), color: "#2563eb", values: d.trend.months.map(m => m.inspections) },
      { name: t("detections"), color: "#dc2626", values: d.trend.months.map(m => m.detections) }]);
    const typeItems = d.trend.by_type.map((r, i) => ({
      label: r.type ? t("type_" + r.type) : t("none"), value: r.detections, color: PALETTE[(i + 1) % PALETTE.length] }));
    trends = `<h2 style="margin:18px 0 6px">🐭 ${esc(t("pest_trends"))}</h2>
      <div class="panel"><h3>📈 ${esc(t("pest_trends"))}</h3>${curve}</div>
      ${typeItems.length ? `<div class="panel"><h3>🐛 ${esc(t("by_device_type"))}</h3>${cols3d(typeItems)}</div>` : ""}`;
  }

  // 3. service history
  const histRows = d.history.map(h => `<tr>
      <td>${fmtDate(h.scheduled_start)}</td>
      <td>${esc(h.site_name || "—")}</td>
      <td>${esc(localized({ name_en: h.svc_en, name_ar: h.svc_ar }, "name") || "—")}</td>
      <td>${esc(h.agent || "—")}</td>
      <td>${sevBadge(h.severity)}</td>
      <td>${esc(h.summary || h.findings || "—")}</td>
      <td style="text-align:center">${h.customer_signature && h.technician_signature ? "✅" : "—"}</td>
    </tr>`).join("") || `<tr><td colspan="7" class="empty">${t("none")}</td></tr>`;
  const history = `<h2 style="margin:18px 0 6px">📋 ${esc(t("service_history"))}</h2>
    <div class="panel"><table><thead><tr>
      <th>${t("date")}</th><th>${t("location_lbl")}</th><th>${t("service")}</th><th>${t("agent")}</th>
      <th>${t("severity")}</th><th>${t("summary")}</th><th>${t("customer_sig")}</th></tr></thead>
      <tbody>${histRows}</tbody></table></div>`;

  // 4. chemical usage log + product / SDS list
  const rate = (r) => {
    const a = parseFloat(r.area_treated);
    return (!isNaN(a) && a > 0) ? `${Math.round((r.quantity / a) * 1000) / 1000} ${esc(r.unit || "")}/${esc(t("unit_area"))}` : "—";
  };
  const chemRows = d.chem_log.map(r => `<tr>
      <td>${fmtDate(r.scheduled_start)}</td>
      <td>${esc(localized(r, "name"))}</td>
      <td>${esc(r.active_ingredient || "—")}</td>
      <td>${esc(r.reg_no || "—")}</td>
      <td class="num">${r.quantity} ${esc(r.unit || "")}</td>
      <td>${esc(r.area_treated || "—")}</td>
      <td>${rate(r)}</td>
      <td>${esc(r.agent || "—")}</td></tr>`).join("") || `<tr><td colspan="8" class="empty">${t("none")}</td></tr>`;
  const prodRows = d.products.map(p => {
    const docs = (p.attachments || []).length
      ? p.attachments.map(a => `<a href="/uploads/${esc(a.filename)}" target="_blank">${esc(a.original_name || t("sds_label"))}</a>`).join(", ")
      : `<span class="muted">${t("no_sds")}</span>`;
    return `<tr><td>${esc(localized(p, "name"))}</td><td>${esc(p.active_ingredient || "—")}</td>
      <td>${esc(p.reg_no || "—")}</td><td>${esc(p.hazard_class || "—")}</td><td>${docs}</td></tr>`;
  }).join("") || `<tr><td colspan="5" class="empty">${t("none")}</td></tr>`;
  const chemicals = `<h2 style="margin:18px 0 6px">🧪 ${esc(t("chemical_usage_log"))}</h2>
    <div class="panel"><table><thead><tr>
      <th>${t("date")}</th><th>${t("product")}</th><th>${t("active_ingredient")}</th><th>${t("reg_no")}</th>
      <th class="num">${t("quantity")}</th><th>${t("area_treated")}</th><th>${t("application_rate")}</th><th>${t("agent")}</th>
      </tr></thead><tbody>${chemRows}</tbody></table></div>
    <div class="panel"><h3>📄 ${esc(t("products_sds"))}</h3><table><thead><tr>
      <th>${t("product")}</th><th>${t("active_ingredient")}</th><th>${t("reg_no")}</th>
      <th>${t("hazard_class")}</th><th>${t("sds_label")}</th></tr></thead>
      <tbody>${prodRows}</tbody></table></div>`;

  // 5. technician credentials
  const techRows = d.technicians.map(u => `<tr>
      <td>${esc(u.full_name)}</td>
      <td>${esc(u.specialization || "—")}</td>
      <td>${esc(u.license_no || "—")}</td>
      <td>${u.license_expiry ? fmtDate(u.license_expiry) : "—"}</td>
      <td class="num">${u.visits}</td>
      <td>${fmtDate(u.last_visit)}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">${t("none")}</td></tr>`;
  const techs = `<h2 style="margin:18px 0 6px">👷 ${esc(t("technician_credentials"))}</h2>
    <div class="panel"><table><thead><tr>
      <th>${t("technician")}</th><th>${t("specialization")}</th><th>${t("license_no")}</th>
      <th>${t("license_expiry")}</th><th class="num">${t("nav_visits")}</th><th>${t("last_visit")}</th>
      </tr></thead><tbody>${techRows}</tbody></table></div>`;

  // 6. corrective actions (+ open device alerts)
  const corrRows = d.corrective.map(r => `<tr>
      <td>${fmtDate(r.scheduled_start)}</td><td>${esc(r.site_name || "—")}</td>
      <td>${sevBadge(r.severity)}</td>
      <td>${esc(r.findings || r.pests_found || r.branch_issue || "—")}</td>
      <td>${esc(r.recommendations || "—")}</td>
      <td>${esc(r.agent || "—")}</td></tr>`).join("") || `<tr><td colspan="6" class="empty">${t("no_corrective")}</td></tr>`;
  const alertRows = (d.device_alerts || []).map(a => `<tr>
      <td>${esc(a.label || "—")}</td><td>${esc(a.type ? t("dt_" + a.type) : "—")}</td>
      <td>${esc(a.loc || "—")}</td>
      <td><span style="display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700;color:#fff;background:${a.status === "activity" ? "#dc2626" : "#d97706"}">${esc(t("mst_" + a.status))}</span></td>
      <td>${a.last_seen ? fmtDate(a.last_seen) : "—"}</td></tr>`).join("");
  const corrective = `<h2 style="margin:18px 0 6px">🛠️ ${esc(t("corrective_actions"))}</h2>
    <div class="panel"><table><thead><tr>
      <th>${t("date")}</th><th>${t("location_lbl")}</th><th>${t("severity")}</th>
      <th>${t("finding")}</th><th>${t("action_taken")}</th><th>${t("agent")}</th>
      </tr></thead><tbody>${corrRows}</tbody></table></div>
    ${alertRows ? `<div class="panel"><h3>⚠️ ${esc(t("open_device_alerts"))}</h3><table><thead><tr>
      <th>${t("code")}</th><th>${t("marker_type")}</th><th>${t("location_lbl")}</th>
      <th>${t("marker_status")}</th><th>${t("last_seen")}</th></tr></thead>
      <tbody>${alertRows}</tbody></table></div>` : ""}`;

  return cover + trends + history + chemicals + techs + corrective;
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
      ${can("maps.delete") ? `<button class="rm" data-rmmap="${m.id}">✕</button>` : ""}
    </div>`).join("")}</div>`;
  box.querySelectorAll("[data-map]").forEach(el => el.addEventListener("click", (e) => {
    if (e.target.dataset.rmmap !== undefined) return;
    navigate("map", { id: el.dataset.map });
  }));
  box.querySelectorAll("[data-rmmap]").forEach(b => b.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (confirm(t("confirm_delete"))) { const r = await API.del("/maps/" + b.dataset.rmmap); if (handledOffline(r, b.closest(".map-thumb"))) return; loadClientMaps(c); }
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
        <button class="btn sm" id="place-btn">➕ ${t("add_device")}</button>
        ${map.markers.length ? `<button class="btn sm secondary" id="qr-sheet">🏷️ ${t("print_qr_labels")}</button>` : ""}</div>` : ""}</div>
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
  if ($("qr-sheet")) $("qr-sheet").addEventListener("click", () => printQrSheet(map));
  stage.addEventListener("click", (e) => {
    if (!placing) return;
    const img = $("map-img"), r = img.getBoundingClientRect();
    const x = Math.min(100, Math.max(0, ((e.clientX - r.left) / r.width) * 100));
    const y = Math.min(100, Math.max(0, ((e.clientY - r.top) / r.height) * 100));
    setPlacing(false);
    markerForm(map, { x: +x.toFixed(2), y: +y.toFixed(2), type: $("mk-type").value, status: "ok" }, false);
  });
  v.querySelectorAll(".map-pin").forEach(p => p.addEventListener("click", (e) => {
    e.stopPropagation();
    const mk = map.markers.find(x => x.id == p.dataset.marker);
    if (canEdit) markerForm(map, mk, true); else showMarkerInfo(mk);
  }));
}

function markerForm(map, mk, isEdit) {
  const mapId = map.id;
  const typeOpts = MARKER_TYPES.map(x => ({ v: x.k, l: x.icon + " " + t("type_" + x.k) }));
  const statusOpts = ["ok", "needs_service", "activity", "missing"].map(s => ({ v: s, l: t("mst_" + s) }));
  openModal(isEdit ? t("edit") : t("add_device"), `<form id="mkf">
    ${field(t("marker_type"), "type", { options: typeOpts, value: mk.type })}
    ${field(t("marker_label"), "label", { value: mk.label })}
    ${field(t("marker_status"), "status", { options: statusOpts, value: mk.status })}
    ${field(t("marker_notes"), "notes", { textarea: true, value: mk.notes })}
    ${isEdit && mk.qr_token ? `<div class="qr-mini"><div class="qr-mini-img">${qrSvg(deviceScanUrl(mk), 3)}</div>
      <button type="button" class="btn sm secondary" id="mk-qr">🏷️ ${t("print_qr")}</button></div>` : ""}
    <div class="form-actions">${isEdit ? `<button type="button" class="btn danger" id="mk-del" style="margin-inline-end:auto">${t("delete")}</button>` : ""}
      <button type="button" class="btn secondary" id="mk-x">${t("cancel")}</button>
      <button class="btn" type="submit">${t("save_device")}</button></div></form>`, (root) => {
    $("mk-x").addEventListener("click", closeModal);
    if ($("mk-qr")) $("mk-qr").addEventListener("click", () => printQrSheet(map, mk));
    if ($("mk-del")) $("mk-del").addEventListener("click", async () => {
      const r = await API.del("/markers/" + mk.id); if (handledOffline(r)) return; closeModal(); navigate("map", { id: mapId });
    });
    root.querySelector("#mkf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const d = formData(root);
      try {
        const saved = isEdit ? await API.put("/markers/" + mk.id, d)
          : await API.post(`/maps/${mapId}/markers`, { ...d, x: mk.x, y: mk.y });
        if (handledOffline(saved)) return;
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
// QR-CODED DEVICES — printable labels + scan-to-inspect (the audit moat)
// ====================================================================
// QR-coded devices: code generation, scan-to-report, printable labels.
// The four field-device types and their printed code prefixes.
const DEVICE_TYPES = [
  { k: "light_trap",   icon: "💡", pre: "LIT" },
  { k: "glue_station", icon: "🟨", pre: "GLU" },
  { k: "bait_station", icon: "📦", pre: "BAI" },
  { k: "fly_trap",     icon: "🪰", pre: "FLY" },
];
function devIcon(type) { const m = DEVICE_TYPES.find(x => x.k === type); return m ? m.icon : "🏷️"; }

// Per-type follow-up fields captured on a scan (the simplified key fields from
// the printed follow-up form). Drives both the scan form and the printout.
// kind: "select" (opts = option keys, labelled via t("df_opt_"+key)),
//       "num" (min/max range), "bool" (yes/no). Labels via t("df_"+key).
const DEVICE_FIELDS = {
  bait_station: [
    { key: "station_condition", kind: "select", opts: ["good", "replaced"] },
    { key: "cleaned", kind: "bool" },
    { key: "bait_status", kind: "select", opts: ["intact", "changed", "damaged", "missing"] },
    { key: "consumption_pct", kind: "num", min: 0, max: 100 },
  ],
  fly_trap: [
    { key: "trap_condition", kind: "select", opts: ["good", "replaced"] },
    { key: "washed", kind: "bool" },
    { key: "water_refilled", kind: "bool" },
    { key: "fly_density", kind: "num", min: 0, max: 1000 },
  ],
  glue_station: [
    { key: "station_condition", kind: "select", opts: ["good", "replaced"] },
    { key: "cleaned", kind: "bool" },
    { key: "glue_status", kind: "select", opts: ["intact", "changed", "damaged", "missing"] },
    { key: "caught", kind: "bool" },
  ],
  light_trap: [
    { key: "trap_condition", kind: "select", opts: ["good", "replaced"] },
    { key: "electricity", kind: "select", opts: ["connected", "reconnected", "disconnected"] },
    { key: "lamp_status", kind: "select", opts: ["good", "replaced", "missing"] },
    { key: "sheet_status", kind: "select", opts: ["good", "replaced", "missing"] },
    { key: "fly_count", kind: "num", min: 0, max: 1000 },
  ],
};
// Build the follow-up field inputs for a device type. `pf` is an optional
// prefill object (the details from this visit's last inspection of the device).
function scanFieldsHtml(type, pf) {
  pf = pf || {};
  const inp = (f) => {
    const cur = pf[f.key];
    if (f.kind === "num")
      return `<input type="number" name="${f.key}" inputmode="numeric"${f.min != null ? ` min="${f.min}"` : ""}${f.max != null ? ` max="${f.max}"` : ""} value="${cur != null ? esc(String(cur)) : ""}">`;
    const opts = f.kind === "bool" ? ["yes", "no"] : f.opts;
    const optHtml = opts.map(o => `<option value="${o}"${String(cur) === o ? " selected" : ""}>${f.kind === "bool" ? t(o) : t("df_opt_" + o)}</option>`).join("");
    return `<select name="${f.key}"><option value="">—</option>${optHtml}</select>`;
  };
  return `<div class="scan-fields">${(DEVICE_FIELDS[type] || []).map(f =>
    `<label class="scan-f"><span>${t("df_" + f.key)}</span>${inp(f)}</label>`).join("")}</div>`;
}

// Human-readable one-line summary of a stored details object, for history rows
// and the coverage table. `det` is the parsed object; `type` its device type.
function deviceDetailsSummary(type, det) {
  if (!det || typeof det !== "object") return "";
  return (DEVICE_FIELDS[type] || []).filter(f => det[f.key] !== undefined && det[f.key] !== "")
    .map(f => {
      const v = det[f.key];
      const shown = f.kind === "num" ? v
        : f.kind === "bool" ? t(String(v) === "yes" || v === true ? "yes" : "no")
        : t("df_opt_" + v);
      return `${t("df_" + f.key)}: ${shown}`;
    }).join(" · ");
}
// Absolute URL a printed device code resolves to (the SPA scan view).
function codeScanUrl(code) { return location.origin + "/scan/" + code; }
// (kept for the legacy floor-plan map markers)
function deviceScanUrl(mk) { return location.origin + "/scan/" + mk.qr_token; }

// Render `text` as an inline QR SVG string (crisp at any print size). Returns
// "" if the QR library failed to load (degrade, never throw).
function qrSvg(text, cell) {
  try {
    if (typeof qrcode !== "function") return "";
    const qr = qrcode(0, "M");           // 0 = auto version, M = ~15% recovery
    qr.addData(text); qr.make();
    return qr.createSvgTag({ cellSize: cell || 4, margin: 2 });
  } catch (e) { return ""; }
}

// Scan landing: fetch by token/code, then render the device or (legacy) marker UI.
async function viewScan(v, arg) {
  const token = arg.token;
  let d;
  try { d = await API.get("/scan/" + token); }
  catch (e) {
    v.innerHTML = `<div class="scan-wrap"><div class="scan-card">
      <div class="empty">⚠️ ${esc(e.message || t("device_unknown"))}</div>
      <div class="form-actions"><button class="btn" id="sc-home">${t("nav_dashboard")}</button></div>
    </div></div>`;
    if ($("sc-home")) $("sc-home").addEventListener("click", () => navigate("dashboard"));
    return;
  }
  if (d.code) return renderDeviceScan(v, token, d);
  return renderMarkerScan(v, token, d);
}

// Device scan: the agent's two-second report for one trap during a visit.
function renderDeviceScan(v, code, d) {
  const unassigned = !d.client_id;
  const canInspect = can("maps.edit") && !unassigned;
  const stBtns = ["ok", "activity", "needs_service", "missing"].map(s =>
    `<button class="scan-st" data-st="${s}" style="--c:${MARKER_STATUS[s]}">${t("mst_" + s)}</button>`).join("");
  const visitLine = d.active_visit_id
    ? `<div class="scan-now ok-line">✓ ${t("filing_on_visit")} #${d.active_visit_id}</div>`
    : `<div class="scan-now warn-line">⚠ ${t("no_active_visit")}</div>`;
  v.innerHTML = `<div class="scan-wrap"><div class="scan-card">
    <div class="scan-head">
      <div class="scan-ic" style="background:${MARKER_STATUS[d.status] || "#64748b"}">${devIcon(d.type)}</div>
      <div><div class="scan-title">${esc(d.code)}</div>
        <div class="muted small">${devIcon(d.type)} ${esc(t("dt_" + d.type))}${d.label ? " · " + esc(d.label) : ""}</div>
        <div class="muted small">${unassigned ? `<span class="warn-line">${t("unassigned")}</span>`
          : esc(localized(d, "client")) + (d.site_name ? " · " + esc(d.site_name) : "")}</div></div>
      <div class="scan-qr"><div class="scan-qr-img">${qrSvg(codeScanUrl(d.code), 3) || "🏷️"}</div>
        <button type="button" class="btn sm secondary" id="sc-print">🏷️ ${t("print_qr")}</button></div>
    </div>
    ${unassigned ? `<div class="scan-now warn-line">${t("device_unassigned_hint")}</div>` : (canInspect ? visitLine : "")}
    ${canInspect ? `
      <div class="scan-q">${t("scan_prompt")}</div>
      <div class="scan-sts">${stBtns}</div>
      <div class="scan-q">${t("followup_details")}</div>
      ${scanFieldsHtml(d.type, scanPrefill(d))}
      <textarea id="sc-find" class="scan-note" rows="2" placeholder="${t("findings")}"></textarea>
      <label class="scan-geo"><input type="checkbox" id="sc-geo" checked> ${t("scan_geostamp")}</label>
      <div class="form-actions" style="margin-top:12px"><button class="btn" id="sc-save">✔ ${t("save_inspection")}</button></div>`
      : (unassigned ? "" : `<div class="muted" style="margin:12px 0">${t("scan_readonly")}</div>`)}
    <div class="scan-hist"><h3>${t("scan_history")}</h3>
      ${(d.history || []).length ? d.history.map(h => {
        let det = {}; try { det = h.details ? JSON.parse(h.details) : {}; } catch (e) { det = {}; }
        const ds = deviceDetailsSummary(d.type, det);
        return `
        <div class="scan-ev"><span class="dot" style="background:${MARKER_STATUS[h.status] || "#64748b"}"></span>
          <div class="se-main"><strong>${t("mst_" + h.status)}</strong>${h.findings ? " — " + esc(h.findings) : (h.note ? " — " + esc(h.note) : "")}
            ${ds ? `<div class="muted small">${esc(ds)}</div>` : ""}
            <div class="muted small">${fmtDateTime(h.recorded_at)}${h.recorded_by_name ? " · " + esc(h.recorded_by_name) : ""}${h.visit_id ? " · " + t("visit") + " #" + h.visit_id : ""}${(h.lat != null && h.lng != null) ? ` · <a href="https://www.google.com/maps/search/?api=1&query=${h.lat},${h.lng}" target="_blank" rel="noopener">📍</a>` : ""}</div>
          </div></div>`; }).join("") : `<div class="empty">${t("none")}</div>`}
    </div>
    <div class="form-actions"><button class="btn secondary" id="sc-home">← ${t("nav_dashboard")}</button></div>
  </div></div>`;
  if ($("sc-home")) $("sc-home").addEventListener("click", () => navigate("dashboard"));
  if ($("sc-print")) $("sc-print").addEventListener("click", () => printDeviceCodes([d]));
  // Status buttons toggle a single selection (default: last-known device status).
  let selSt = d.status && _DEV_STATUSES.includes(d.status) ? d.status : "ok";
  const paint = () => v.querySelectorAll(".scan-st").forEach(b =>
    b.classList.toggle("sel", b.dataset.st === selSt));
  if (canInspect) {
    paint();
    v.querySelectorAll(".scan-st").forEach(b =>
      b.addEventListener("click", () => { selSt = b.dataset.st; paint(); }));
    if ($("sc-save")) $("sc-save").addEventListener("click", () =>
      submitDeviceScan(code, selSt, d.active_visit_id, d.type, v));
  }
}
const _DEV_STATUSES = ["ok", "activity", "needs_service", "missing"];

// Latest inspection this device got on the current active visit — used to
// prefill the fields so re-scanning a device shows what was already entered.
function scanPrefill(d) {
  const h = (d.history || []).find(x => x.visit_id && x.visit_id === d.active_visit_id);
  if (!h || !h.details) return {};
  try { return JSON.parse(h.details); } catch (e) { return {}; }
}

// File one device's inspection. Geo-stamp is best-effort, never blocks the log.
async function submitDeviceScan(code, status, visitId, type, root) {
  const findings = $("sc-find") ? $("sc-find").value.trim() : "";
  const wantGeo = $("sc-geo") && $("sc-geo").checked;
  const details = {};
  (root || document).querySelectorAll(".scan-fields [name]").forEach(el => {
    if (el.value !== "") details[el.name] = el.value;
  });
  const send = async (lat, lng) => {
    try {
      const r = await API.post("/scan/" + code, { status, findings, details, visit_id: visitId || null, lat, lng });
      if (r && r.__queued) { toast(t("saved_offline")); return; }
      toast(t("scan_logged"));
      navigate("scan", { token: code });
    } catch (e) { alert(e.message); }
  };
  if (wantGeo && navigator.geolocation) {
    toast(t("scan_locating"));
    navigator.geolocation.getCurrentPosition(
      p => send(p.coords.latitude, p.coords.longitude),
      () => send(null, null),
      { enableHighAccuracy: true, timeout: 6000, maximumAge: 30000 });
  } else { send(null, null); }
}

// One follow-up field's value as a printable cell.
function fmtDetailCell(f, val) {
  if (val === undefined || val === "" || val === null) return "—";
  if (f.kind === "num") return esc(String(val));
  if (f.kind === "bool") return t(String(val) === "yes" || val === true ? "yes" : "no");
  return t("df_opt_" + val);
}

// Printable per-visit follow-up report, laid out like Follow up.pdf: company
// header + one section per device type scanned on the visit, each a table whose
// columns are that type's follow-up fields.
async function printFollowupReport(visitId) {
  let data;
  try { data = await API.get(`/visits/${visitId}/followup`); }
  catch (e) { alert(e.message); return; }
  const groups = data.groups || {};
  if (!Object.keys(groups).length) { toast(t("followup_nothing")); return; }
  const ar = LANG === "ar", dir = ar ? "rtl" : "ltr";
  const S = SETTINGS || {};
  const v = data.visit || {};
  const compName = (ar ? S.company_name_ar : S.company_name_en) || S.company_name_en || "Company";
  const compAddr = (ar ? S.address_ar : S.address_en) || S.address_en || "";
  const logoHtml = S.logo ? `<img src="/uploads/${esc(S.logo)}" style="height:46px">` : `<div style="font-size:32px">🐜</div>`;
  const clientName = ar ? (v.client_ar || v.client_en) : (v.client_en || v.client_ar);
  // Sections in the same order as the printed form.
  const order = ["bait_station", "fly_trap", "glue_station", "light_trap"];
  const sections = order.filter(ty => (groups[ty] || []).length).map(ty => {
    const fields = DEVICE_FIELDS[ty] || [];
    const head = `<th>${t("code")}</th><th>${t("label")}</th><th>${t("status")}</th>`
      + fields.map(f => `<th>${esc(t("df_" + f.key))}</th>`).join("");
    const rows = groups[ty].map(r => `<tr>
      <td class="c">${esc(r.code)}</td><td>${esc(r.label || "—")}</td>
      <td>${t("mst_" + r.status)}</td>
      ${fields.map(f => `<td class="c">${fmtDetailCell(f, (r.details || {})[f.key])}</td>`).join("")}
    </tr>`).join("");
    return `<div class="sec"><h3>${devIcon(ty)} ${esc(t("sec_" + ty))}</h3>
      <table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }).join("");
  const meta = [
    [t("client"), clientName],
    [t("site_name"), v.site_name],
    [t("agent"), v.agent_name],
    [t("visit"), "#" + String(v.id).padStart(5, "0")],
    [t("date"), fmtDate(v.scheduled_start)],
    [t("visit_number"), v.visit_number],
  ].filter(([, val]) => val != null && String(val).trim() !== "")
    .map(([l, val]) => `<span><b>${esc(l)}:</b> ${esc(String(val))}</span>`).join("");
  const doc = `<!DOCTYPE html><html lang="${LANG}" dir="${dir}"><head><meta charset="utf-8">
    <title>${esc(t("followup_report"))} #${String(v.id).padStart(5, "0")}</title>
    <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
      *{box-sizing:border-box}
      body{font-family:${ar ? "'Cairo'" : "'Inter'"},system-ui,sans-serif;color:#1c2733;margin:0;padding:36px;font-size:12px}
      .sheet{max-width:900px;margin:auto}
      .top{display:flex;justify-content:space-between;align-items:center;border-bottom:2px solid #0f172a;padding-bottom:12px;margin-bottom:8px}
      .co h1{margin:0;font-size:16px}.co .m{color:#64748b;font-size:10px;line-height:1.6}
      .rt{text-align:${ar ? "left" : "right"}}.rt h2{margin:0;font-size:18px}
      .meta{display:flex;flex-wrap:wrap;gap:6px 18px;margin:10px 0 6px;color:#334155}
      .sec{margin-top:16px;break-inside:avoid}.sec h3{margin:0 0 6px;font-size:14px;color:#0f766e}
      table{width:100%;border-collapse:collapse;font-size:11px}
      th,td{border:1px solid #cbd5e1;padding:5px 7px;text-align:${ar ? "right" : "left"};vertical-align:top}
      th{background:#f1f5f9;font-weight:700}.c{text-align:center}
      .noprint{margin-bottom:12px}.pbtn{background:#0f766e;color:#fff;border:none;padding:9px 20px;border-radius:8px;font-size:14px;cursor:pointer}
      @media print{.noprint{display:none}body{padding:0}}
    </style></head><body>
    <div class="sheet">
      <div class="noprint"><button class="pbtn" onclick="window.print()">🖨️ ${esc(t("print"))}</button></div>
      <div class="top">
        <div class="co">${logoHtml}<h1>${esc(compName)}</h1>${compAddr ? `<div class="m">${esc(compAddr)}</div>` : ""}</div>
        <div class="rt"><h2>${esc(t("followup_report"))}</h2></div>
      </div>
      <div class="meta">${meta}</div>
      ${sections}
    </div>
    <script>window.onload=function(){setTimeout(function(){window.print()},400)}<\/script>
    </body></html>`;
  printHtmlDoc(doc);
}

// Printable label sheet for a set of device codes (big code text + scannable QR).
function printDeviceCodes(devices) {
  const list = (devices || []).filter(d => d.code);
  if (!list.length) { toast(t("no_devices")); return; }
  const S = SETTINGS || {};
  const comp = (LANG === "ar" ? S.company_name_ar : S.company_name_en) || S.company_name_en || "PestCare";
  const cells = list.map(d => `
    <div class="label"><div class="qr">${qrSvg(codeScanUrl(d.code), 4)}</div>
      <div class="meta">
        <div class="code">${esc(d.code)}</div>
        <div class="dt">${devIcon(d.type)} ${esc(t("dt_" + d.type))}</div>
        <div class="dc">${d.client_id ? esc(localized(d, "client")) : ""}${d.label ? " · " + esc(d.label) : ""}</div>
        <div class="cmp">${esc(comp)}</div></div></div>`).join("");
  const doc = `<!DOCTYPE html><html lang="${LANG}" dir="${LANG === "ar" ? "rtl" : "ltr"}"><head><meta charset="utf-8">
    <title>${esc(t("device_codes"))}</title><style>
      *{box-sizing:border-box}body{font-family:system-ui,sans-serif;margin:0;padding:12px}
      .sheet{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
      .label{border:1px dashed #94a3b8;border-radius:8px;padding:10px;display:flex;gap:10px;align-items:center;break-inside:avoid}
      .qr{width:104px;height:104px;flex:none}.qr svg{width:100%;height:100%}
      .meta{font-size:12px;line-height:1.35;overflow:hidden}
      .code{font-weight:800;font-size:17px;letter-spacing:.04em}
      .dt{color:#475569;font-size:12px}.dc{color:#64748b;font-size:11px}
      .cmp{color:#0f766e;font-weight:600;margin-top:2px;font-size:11px}
      .noprint{margin-bottom:10px}.pbtn{background:#0f766e;color:#fff;border:none;padding:9px 20px;border-radius:8px;font-size:14px;cursor:pointer}
      @media print{.noprint{display:none}body{padding:0}}
    </style></head><body>
    <div class="noprint"><button class="pbtn" onclick="window.print()">🖨️ ${esc(t("print"))}</button></div>
    <div class="sheet">${cells}</div>
    <script>window.onload=function(){setTimeout(function(){window.print()},500)}<\/script>
    </body></html>`;
  printHtmlDoc(doc);
}

// ---- Sidebar: Device QR Codes registry (generate / assign / print / scan) ----
let _devCurrent = [];
let _devFilter = { client_id: "", type: "", unassigned: "" };

async function viewDevices(v) {
  const canManage = can("maps.create");
  const clientOpts = [{ v: "", l: t("all_clients") }].concat(cache.clients.map(c => ({ v: c.id, l: localized(c, "name") })));
  const typeOpts = [{ v: "", l: t("all") }].concat(DEVICE_TYPES.map(x => ({ v: x.k, l: x.icon + " " + t("dt_" + x.k) })));
  const sel = (id, opts) => `<select id="${id}" class="toolbar-select">${opts.map(o =>
    `<option value="${esc(o.v)}">${esc(o.l)}</option>`).join("")}</select>`;
  v.innerHTML = `
    <div class="page-head"><h2>🏷️ ${t("nav_devices")}</h2>
      ${canManage ? `<button class="btn" id="gen-codes">＋ ${t("generate_codes")}</button>` : ""}</div>
    <div class="muted small" style="margin-bottom:10px">${t("devices_hint")}</div>
    <div class="dev-toolbar">
      ${sel("df-client", clientOpts)} ${sel("df-type", typeOpts)}
      <label class="scan-geo"><input type="checkbox" id="df-unassigned"> ${t("unassigned_only")}</label>
      <span class="spacer"></span>
      ${canManage ? `<button class="btn sm secondary" id="dev-assign" disabled>${t("assign_to_client")}</button>` : ""}
      <button class="btn sm" id="dev-print" disabled>🏷️ ${t("print_selected")}</button>
    </div>
    <div class="panel" id="dev-list">${t("loading")}</div>`;
  $("df-client").value = _devFilter.client_id; $("df-type").value = _devFilter.type;
  $("df-unassigned").checked = !!_devFilter.unassigned;
  const selectedIds = () => Array.from(document.querySelectorAll(".dev-cb:checked")).map(cb => +cb.dataset.id);
  const updateBulk = () => {
    const n = selectedIds().length;
    if ($("dev-print")) $("dev-print").disabled = !n;
    if ($("dev-assign")) $("dev-assign").disabled = !n;
  };
  const render = (devs) => {
    _devCurrent = devs;
    const box = $("dev-list");
    if (!devs.length) { box.innerHTML = `<div class="empty">${t("devices_none")}</div>`; updateBulk(); return; }
    box.innerHTML = `<table><thead><tr>
      <th style="width:26px"><input type="checkbox" id="dev-all"></th>
      <th>${t("qr_code")}</th><th>${t("code")}</th><th>${t("marker_type")}</th><th>${t("client")}</th>
      <th>${t("location_lbl")}</th><th>${t("label")}</th><th>${t("status")}</th><th>${t("last_seen")}</th><th></th>
    </tr></thead><tbody>
      ${devs.map(d => `<tr>
        <td><input type="checkbox" class="dev-cb" data-id="${d.id}"></td>
        <td class="qr-td"><button type="button" class="qr-cell" data-qr="${esc(d.code)}" title="${t("print_qr")}">${qrSvg(codeScanUrl(d.code), 2) || "🏷️"}</button></td>
        <td><strong>${esc(d.code)}</strong></td>
        <td>${devIcon(d.type)} ${esc(t("dt_" + d.type))}</td>
        <td>${d.client_id ? esc(localized(d, "client")) : `<span class="muted">${t("unassigned")}</span>`}</td>
        <td>${esc(d.site_name || "—")}</td><td>${esc(d.label || "—")}</td>
        <td><span style="color:${MARKER_STATUS[d.status] || "#64748b"}">${t("mst_" + d.status)}</span></td>
        <td class="muted small">${d.last_seen ? fmtDateTime(d.last_seen) : "—"}</td>
        <td><div style="display:flex;gap:6px;justify-content:flex-end">
          <button class="btn sm secondary" data-open="${esc(d.code)}">${t("open")}</button>
          ${canManage ? `<button class="btn sm secondary" data-edit="${d.id}">✏️</button>` : ""}
        </div></td></tr>`).join("")}
    </tbody></table>`;
    box.querySelectorAll(".dev-cb").forEach(cb => cb.addEventListener("change", updateBulk));
    if ($("dev-all")) $("dev-all").addEventListener("change", e => {
      box.querySelectorAll(".dev-cb").forEach(cb => cb.checked = e.target.checked); updateBulk();
    });
    box.querySelectorAll("[data-open]").forEach(b => b.addEventListener("click",
      () => navigate("scan", { token: b.dataset.open })));
    box.querySelectorAll("[data-qr]").forEach(b => b.addEventListener("click",
      () => printDeviceCodes(_devCurrent.filter(x => x.code === b.dataset.qr))));
    box.querySelectorAll("[data-edit]").forEach(b => b.addEventListener("click",
      () => deviceEditDialog(_devCurrent.find(x => x.id == b.dataset.edit), load)));
    updateBulk();
  };
  const load = async () => {
    const qp = [];
    if ($("df-client").value) qp.push("client_id=" + $("df-client").value);
    if ($("df-type").value) qp.push("type=" + $("df-type").value);
    if ($("df-unassigned").checked) qp.push("unassigned=1");
    _devFilter = { client_id: $("df-client").value, type: $("df-type").value, unassigned: $("df-unassigned").checked ? "1" : "" };
    render(await API.get("/devices" + (qp.length ? "?" + qp.join("&") : "")));
  };
  ["df-client", "df-type", "df-unassigned"].forEach(id => $(id).addEventListener("change", load));
  if ($("gen-codes")) $("gen-codes").addEventListener("click", () => generateCodesDialog(load));
  if ($("dev-print")) $("dev-print").addEventListener("click", () =>
    printDeviceCodes(_devCurrent.filter(d => selectedIds().includes(d.id))));
  if ($("dev-assign")) $("dev-assign").addEventListener("click", () => assignDevicesDialog(selectedIds(), load));
  await load();
}

function generateCodesDialog(onDone) {
  const typeOpts = DEVICE_TYPES.map(x => ({ v: x.k, l: x.icon + " " + t("dt_" + x.k) }));
  const clientOpts = [{ v: "", l: t("assign_later") }].concat(cache.clients.map(c => ({ v: c.id, l: localized(c, "name") })));
  openModal(t("generate_codes"), `<form id="genf">
    ${field(t("marker_type"), "type", { options: typeOpts })}
    ${field(t("quantity"), "count", { type: "number", value: "50" })}
    ${field(t("assign_to_client"), "client_id", { options: clientOpts })}
    <div class="field"><label>${t("for_site_optional")}</label><select name="site_id"><option value="">${t("none")}</option></select></div>
    <div class="muted small">${t("generate_hint")}</div>
    <div class="form-actions"><button type="button" class="btn secondary" id="gen-x">${t("cancel")}</button>
      <button class="btn" type="submit">${t("generate")}</button></div></form>`, (root) => {
    $("gen-x").addEventListener("click", closeModal);
    const cs = root.querySelector("[name=client_id]"), ss = root.querySelector("[name=site_id]");
    cs.addEventListener("change", () => loadSiteOptions(cs.value, ss, "", t("none")));
    root.querySelector("#genf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = formData(root);
      const cnt = parseInt(f.count, 10);
      if (!(cnt >= 1 && cnt <= 500)) { alert(t("count_range")); return; }
      try {
        const r = await API.post("/devices/generate",
          { type: f.type, count: cnt, client_id: f.client_id || null, site_id: f.site_id || null });
        closeModal(); toast(t("codes_generated").replace("{n}", r.codes.length));
        if (onDone) await onDone();
        // Build printable label objects from the returned codes (+ client name).
        const cl = f.client_id ? cache.clients.find(c => String(c.id) === String(f.client_id)) : null;
        const objs = r.codes.map(code => ({ code, type: r.type, client_id: f.client_id || null,
          client_en: cl && cl.name_en, client_ar: cl && cl.name_ar }));
        if (confirm(t("print_now_q"))) printDeviceCodes(objs);
      } catch (err) { alert(err.message); }
    });
  });
}

function assignDevicesDialog(ids, onDone) {
  if (!ids.length) return;
  const clientOpts = [{ v: "", l: t("select") }].concat(cache.clients.map(c => ({ v: c.id, l: localized(c, "name") })));
  openModal(t("assign_to_client"), `<form id="asf">
    <div class="muted small" style="margin-bottom:8px">${t("assign_count").replace("{n}", ids.length)}</div>
    ${field(t("client"), "client_id", { options: clientOpts })}
    <div class="field"><label>${t("for_site_optional")}</label><select name="site_id"><option value="">${t("none")}</option></select></div>
    <div class="form-actions"><button type="button" class="btn secondary" id="as-x">${t("cancel")}</button>
      <button class="btn" type="submit">${t("assign")}</button></div></form>`, (root) => {
    $("as-x").addEventListener("click", closeModal);
    const cs = root.querySelector("[name=client_id]"), ss = root.querySelector("[name=site_id]");
    cs.addEventListener("change", () => loadSiteOptions(cs.value, ss, "", t("none")));
    root.querySelector("#asf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = formData(root);
      if (!f.client_id) { alert(t("select_client")); return; }
      try {
        await API.post("/devices/assign", { ids, client_id: +f.client_id, site_id: f.site_id || null });
        closeModal(); toast(t("saved")); if (onDone) await onDone();
      } catch (err) { alert(err.message); }
    });
  });
}

function deviceEditDialog(d, onDone) {
  if (!d) return;
  const statusOpts = ["ok", "activity", "needs_service", "missing"].map(s => ({ v: s, l: t("mst_" + s) }));
  const clientOpts = [{ v: "", l: t("unassigned") }].concat(cache.clients.map(c => ({ v: c.id, l: localized(c, "name") })));
  openModal(d.code, `<form id="edf">
    ${field(t("label"), "label", { value: d.label })}
    ${field(t("client"), "client_id", { options: clientOpts, value: d.client_id || "" })}
    <div class="field"><label>${t("location_lbl")}</label><select name="site_id"><option value="">${t("none")}</option></select></div>
    ${field(t("status"), "status", { options: statusOpts, value: d.status })}
    <div class="form-actions"><button type="button" class="btn danger" id="ed-del" style="margin-inline-end:auto">${t("delete")}</button>
      <button type="button" class="btn secondary" id="ed-x">${t("cancel")}</button>
      <button class="btn" type="submit">${t("save")}</button></div></form>`, (root) => {
    $("ed-x").addEventListener("click", closeModal);
    const cs = root.querySelector("[name=client_id]"), ss = root.querySelector("[name=site_id]");
    loadSiteOptions(d.client_id, ss, d.site_id, t("none"));
    cs.addEventListener("change", () => loadSiteOptions(cs.value, ss, "", t("none")));
    $("ed-del").addEventListener("click", async () => {
      if (!confirm(t("confirm_delete"))) return;
      await API.del("/devices/" + d.id); closeModal(); toast(t("saved")); if (onDone) await onDone();
    });
    root.querySelector("#edf").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = formData(root);
      try {
        await API.put("/devices/" + d.id,
          { label: f.label, client_id: f.client_id || null, site_id: f.site_id || null, status: f.status });
        closeModal(); toast(t("saved")); if (onDone) await onDone();
      } catch (err) { alert(err.message); }
    });
  });
}

// Legacy floor-plan marker scan (kept for users still placing pins on map images).
function renderMarkerScan(v, token, d) {
  const canInspect = can("maps.edit");
  const stBtns = ["ok", "activity", "needs_service", "missing"].map(s =>
    `<button class="scan-st" data-st="${s}" style="--c:${MARKER_STATUS[s]}">${t("mst_" + s)}</button>`).join("");
  v.innerHTML = `<div class="scan-wrap"><div class="scan-card">
    <div class="scan-head">
      <div class="scan-ic" style="background:${MARKER_STATUS[d.status] || "#64748b"}">${markerIcon(d.type)}</div>
      <div><div class="scan-title">${esc(d.label || t("type_" + d.type))}</div>
        <div class="muted small">${esc(t("type_" + d.type))} · ${esc(localized(d, "client"))}${d.site_name ? " · " + esc(d.site_name) : ""}</div></div>
    </div>
    ${canInspect ? `<div class="scan-q">${t("scan_prompt")}</div>
      <div class="scan-sts">${stBtns}</div>
      <textarea id="sc-note" class="scan-note" rows="2" placeholder="${t("marker_notes")}"></textarea>
      <label class="scan-geo"><input type="checkbox" id="sc-geo" checked> ${t("scan_geostamp")}</label>`
      : `<div class="muted" style="margin:12px 0">${t("scan_readonly")}</div>`}
    <div class="scan-hist"><h3>${t("scan_history")}</h3>
      ${(d.history || []).length ? d.history.map(h => `
        <div class="scan-ev"><span class="dot" style="background:${MARKER_STATUS[h.status] || "#64748b"}"></span>
          <div class="se-main"><strong>${t("mst_" + h.status)}</strong>${h.note ? " — " + esc(h.note) : ""}
            <div class="muted small">${fmtDateTime(h.recorded_at)}${h.recorded_by_name ? " · " + esc(h.recorded_by_name) : ""}</div>
          </div></div>`).join("") : `<div class="empty">${t("none")}</div>`}
    </div>
    <div class="form-actions"><button class="btn secondary" id="sc-home">← ${t("nav_dashboard")}</button></div>
  </div></div>`;
  if ($("sc-home")) $("sc-home").addEventListener("click", () => navigate("dashboard"));
  if (canInspect) v.querySelectorAll(".scan-st").forEach(b =>
    b.addEventListener("click", () => submitScan(token, b.dataset.st)));
}
async function submitScan(token, status) {
  const note = $("sc-note") ? $("sc-note").value.trim() : "";
  const wantGeo = $("sc-geo") && $("sc-geo").checked;
  const send = async (lat, lng) => {
    try {
      const r = await API.post("/scan/" + token, { status, note, lat, lng });
      if (r && r.__queued) { toast(t("saved_offline")); return; }
      toast(t("scan_logged")); navigate("scan", { token });
    } catch (e) { alert(e.message); }
  };
  if (wantGeo && navigator.geolocation) {
    toast(t("scan_locating"));
    navigator.geolocation.getCurrentPosition(
      p => send(p.coords.latitude, p.coords.longitude),
      () => send(null, null), { enableHighAccuracy: true, timeout: 6000, maximumAge: 30000 });
  } else { send(null, null); }
}
// Printable QR sheet for a floor-plan map's markers (legacy).
function printQrSheet(map, only) {
  const markers = (only ? [only] : map.markers).filter(m => m.qr_token);
  if (!markers.length) { toast(t("no_devices")); return; }
  printDeviceCodes(markers.map(m => ({ code: m.qr_token, type: m.type, label: m.label,
    client_id: map.client_id, client_en: map.client_en, client_ar: map.client_ar })));
}

// Per-visit device coverage panel: how many traps the agent scanned this visit.
async function loadVisitCoverage(visitId) {
  const box = $("dev-coverage");
  if (!box) return;
  let cov;
  try { cov = await API.get(`/visits/${visitId}/devices`); }
  catch (e) { box.remove(); return; }
  if (!cov.total) {
    box.innerHTML = `<div class="section-title"><h3>🏷️ ${t("device_coverage")}</h3></div>
      <div class="empty">${t("no_devices_for_client")}</div>`;
    return;
  }
  const pct = Math.round((cov.scanned / cov.total) * 100);
  const done = cov.scanned === cov.total;
  box.innerHTML = `<div class="section-title" style="display:flex;justify-content:space-between;align-items:center">
      <h3>🏷️ ${t("device_coverage")} <span class="badge b-${done ? "completed" : "draft"}">${cov.scanned}/${cov.total} ${t("scanned")}</span></h3>
      ${cov.scanned ? `<button class="btn sm secondary" id="cov-followup">🖨️ ${t("followup_report")}</button>` : ""}</div>
    <div class="cov-bar"><span style="width:${pct}%"></span></div>
    <table style="margin-top:10px"><thead><tr><th>${t("code")}</th><th>${t("marker_type")}</th>
      <th>${t("label")}</th><th>${t("status")}</th><th>${t("scanned")}</th></tr></thead>
      <tbody>${cov.devices.map(d => `<tr class="${d.scanned_at ? "" : "cov-pending"}">
        <td><a href="#" data-open="${esc(d.code)}"><strong>${esc(d.code)}</strong></a></td>
        <td>${devIcon(d.type)} ${esc(t("dt_" + d.type))}</td>
        <td>${esc(d.label || "—")}</td>
        <td><span style="color:${MARKER_STATUS[d.status] || "#64748b"}">${t("mst_" + d.status)}</span></td>
        <td>${d.scanned_at ? "✓ " + fmtDateTime(d.scanned_at) : `<span class="muted">${t("pending")}</span>`}</td>
      </tr>`).join("")}</tbody></table>`;
  box.querySelectorAll("[data-open]").forEach(a => a.addEventListener("click", (e) => {
    e.preventDefault(); navigate("scan", { token: a.dataset.open });
  }));
  if ($("cov-followup")) $("cov-followup").addEventListener("click", () => printFollowupReport(visitId));
}

// ====================================================================
// PWA: register the service worker for offline app-shell loading.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch((e) => console.warn("SW registration failed", e));
  });
}

boot();
