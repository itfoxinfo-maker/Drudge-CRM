// ====================================================================
// Offline submission queue (field agents on poor connectivity).
// Mutations that fail because the device is offline are stored in IndexedDB
// and replayed, in order, when connectivity returns. Exposes window.OfflineQueue.
// ====================================================================
(function () {
  const DB_NAME = "pestcare-offline";
  const STORE = "queue";

  function openDB() {
    return new Promise((resolve, reject) => {
      const r = indexedDB.open(DB_NAME, 1);
      r.onupgradeneeded = () => {
        if (!r.result.objectStoreNames.contains(STORE)) {
          r.result.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
        }
      };
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  }

  function op(mode, fn) {
    return openDB().then(db => new Promise((resolve, reject) => {
      const t = db.transaction(STORE, mode);
      const store = t.objectStore(STORE);
      const result = fn(store);
      t.oncomplete = () => resolve(result.value);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error);
    }));
  }

  const wrap = (req, holder) => { req.onsuccess = () => { holder.value = req.result; }; };

  async function count() {
    const h = {};
    await op("readonly", s => { wrap(s.count(), h); return h; });
    return h.value || 0;
  }

  async function emitChange() {
    const c = await count();
    window.dispatchEvent(new CustomEvent("oq-change", { detail: { count: c } }));
    return c;
  }

  async function enqueue(entry) {
    const h = {};
    await op("readwrite", s => { wrap(s.add({ ...entry, ts: Date.now() }), h); return h; });
    window.dispatchEvent(new CustomEvent("oq-queued", { detail: { path: entry.path, method: entry.method } }));
    await emitChange();
    return h.value;
  }

  async function all() {
    const h = {};
    await op("readonly", s => { wrap(s.getAll(), h); return h; });
    return (h.value || []).sort((a, b) => a.id - b.id);
  }

  async function remove(id) {
    await op("readwrite", s => { s.delete(id); return {}; });
  }

  // Replay one queued entry. Returns a fetch Response (throws on network error).
  function replay(entry) {
    const token = localStorage.getItem("token");
    const headers = {};
    if (token) headers["Authorization"] = "Bearer " + token;
    let body;
    if (entry.kind === "photo") {
      const fd = new FormData();
      fd.append("entity_type", entry.entity_type);
      fd.append("entity_id", entry.entity_id);
      if (entry.caption) fd.append("caption", entry.caption);
      if (entry.business_plan) fd.append("business_plan", entry.business_plan);
      fd.append("file", entry.file, entry.filename || "photo");
      body = fd;
    } else {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(entry.body || {});
    }
    return fetch("/api" + entry.path, { method: entry.method, headers, body });
  }

  let flushing = false;
  async function flush() {
    if (flushing || !navigator.onLine) return;
    flushing = true;
    let synced = 0;
    try {
      const items = await all();
      for (const entry of items) {
        let res;
        try {
          res = await replay(entry);
        } catch (e) {
          break; // still offline / network error — keep order, retry later
        }
        if (res.ok) {
          await remove(entry.id); synced++;
        } else if (res.status === 401) {
          // Token expired/invalid (e.g. agent was offline past the token TTL).
          // Keep the queue and stop — it'll retry after the next sign-in rather
          // than silently discarding the agent's offline work.
          break;
        } else if (res.status >= 400 && res.status < 500) {
          // permanent client error (e.g. validation) — drop it
          await remove(entry.id);
          console.warn("Dropped un-syncable queued request", entry.path, res.status);
        } else {
          break; // server error — retry later
        }
      }
    } finally {
      flushing = false;
    }
    const remaining = await emitChange();
    if (synced > 0) {
      window.dispatchEvent(new CustomEvent("oq-synced", { detail: { synced, remaining } }));
    }
    return synced;
  }

  window.addEventListener("online", flush);

  window.OfflineQueue = { enqueue, count, flush, emitChange };
})();
