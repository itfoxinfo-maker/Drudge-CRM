// Thin fetch wrapper that injects the auth token and parses JSON.
function _readStoredUser() {
  try { return JSON.parse(localStorage.getItem("user") || "null"); }
  catch (e) { return null; }
}
const API = {
  token: localStorage.getItem("token") || null,
  user: _readStoredUser(),

  setAuth(token, user) {
    this.token = token;
    this.user = user;
    localStorage.setItem("token", token);
    localStorage.setItem("user", JSON.stringify(user));
  },
  clearAuth() {
    this.token = null;
    this.user = null;
    localStorage.removeItem("token");
    localStorage.removeItem("user");
  },

  async request(method, path, body) {
    const headers = {};
    if (this.token) headers["Authorization"] = "Bearer " + this.token;
    const opts = { method, headers };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const mutating = method !== "GET";
    let res;
    try {
      res = await fetch("/api" + path, opts);
    } catch (e) {
      // Network failure: queue mutations for later sync; reads just fail.
      if (mutating && window.OfflineQueue) {
        await window.OfflineQueue.enqueue({ kind: "json", method, path, body: body || {} });
        return { __queued: true };
      }
      throw new Error("offline");
    }
    if (res.status === 401 && this.token) {
      this.clearAuth();
      location.reload();
      return;
    }
    let data = null;
    try { data = await res.json(); } catch (e) {}
    if (!res.ok) throw new Error((data && data.error) || "Request failed");
    return data;
  },

  get(p) { return this.request("GET", p); },
  post(p, b) { return this.request("POST", p, b); },
  put(p, b) { return this.request("PUT", p, b); },
  del(p) { return this.request("DELETE", p); },

  async uploadPhoto(entityType, entityId, file, caption) {
    const fd = new FormData();
    fd.append("entity_type", entityType);
    fd.append("entity_id", entityId);
    if (caption) fd.append("caption", caption);
    fd.append("file", file);
    const headers = {};
    if (this.token) headers["Authorization"] = "Bearer " + this.token;
    let res;
    try {
      res = await fetch("/api/photos", { method: "POST", headers, body: fd });
    } catch (e) {
      // Offline: queue the photo (Blob is stored in IndexedDB) for later sync.
      if (window.OfflineQueue) {
        await window.OfflineQueue.enqueue({
          kind: "photo", method: "POST", path: "/photos",
          entity_type: entityType, entity_id: entityId, caption: caption || "",
          file, filename: file.name || "photo.jpg",
        });
        return { __queued: true };
      }
      throw new Error("offline");
    }
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error((data && data.error) || "Upload failed");
    return data;
  },
};
