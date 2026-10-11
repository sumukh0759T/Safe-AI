/* Shared API-base resolution + health diagnostics for both SafeBath apps.
 *
 * Resolution order (highest first):
 *   1. window.SAFEBATH_API_BASE
 *   2. <meta name="safebath-api-base" content="...">
 *   3. ?api=<base> query param                  (persisted to sessionStorage)
 *   4. sessionStorage "safebath-api-base"
 *   5. Derive: page over http from a non-local host -> http://<host>:8000
 *      (typical LAN: Pi serves HTML on :8080, API on :8000)
 *   6. Fallback: http://localhost:8000          (laptop running both)
 *
 * resolveApiInfo() additionally returns a `reason` explaining why the chosen
 * base is likely wrong, so the UI can tell the user what to fix instead of
 * just showing "API unreachable".
 */
const TRIM = /\/$/;

function defaults() {
  return {
    window: typeof window !== "undefined" ? window : undefined,
    document: typeof document !== "undefined" ? document : undefined,
    location: typeof location !== "undefined" ? location : undefined,
  };
}

export function resolveApiBase(env) {
  const e = { ...defaults(), ...env };
  const w = e.window;
  const doc = e.document;
  const loc = e.location || (w && w.location) || { protocol: "", hostname: "" };
  const store = (e.sessionStorage || (w && w.sessionStorage));

  if (w && w.SAFEBATH_API_BASE)
    return String(w.SAFEBATH_API_BASE).replace(TRIM, "");
  if (doc) {
    const m = doc.querySelector('meta[name="safebath-api-base"]');
    if (m && m.content) return String(m.content).replace(TRIM, "");
  }
  if (loc.search) {
    const qp = new URLSearchParams(loc.search).get("api");
    if (qp) {
      if (store) { try { store.setItem("safebath-api-base", qp); } catch (_) {} }
      return String(qp).replace(TRIM, "");
    }
  }
  if (store) {
    try {
      const s = store.getItem("safebath-api-base");
      if (s) return String(s).replace(TRIM, "");
    } catch (_) {}
  }
  const proto = loc.protocol || "";
  const host = loc.hostname || "";
  if (proto === "http:" && host && host !== "localhost" && host !== "127.0.0.1")
    return `http://${host}:8000`;
  return "http://localhost:8000";
}

export function resolveApiInfo(env) {
  const e = { ...defaults(), ...env };
  const loc = e.location || (e.window && e.window.location) ||
    { protocol: "", hostname: "" };
  const proto = loc.protocol || "";
  const host = loc.hostname || "";
  const base = resolveApiBase(e);

  if (proto === "https:" && base.startsWith("http://")) {
    return {
      base, via: "override-or-fallback",
      ok: false,
      reason:
        "This page is https but the API base is http — browsers block mixed " +
        'content. Point the app at an https API via window.SAFEBATH_API_BASE or ' +
        '<meta name="safebath-api-base" content="https://...">.',
    };
  }
  if (proto === "file:") {
    const overridden = (e.window && e.window.SAFEBATH_API_BASE) ||
      (e.document && e.document.querySelector &&
        e.document.querySelector('meta[name="safebath-api-base"]'));
    return {
      base, via: "fallback",
      ok: false,
      reason:
        "Opened from file:// so no host exists to derive the API from. Serve " +
        "the UI over http (python -m http.server 8080 in frontend/) or set " +
        (overridden
          ? "an override."
          : 'the <meta name="safebath-api-base"> tag / ?api=... query param.'),
    };
  }
  if (base === "http://localhost:8000" && host && host !== "localhost" &&
      host !== "127.0.0.1") {
    return {
      base, via: "fallback",
      ok: false,
      reason:
        "No override set, so the app targets localhost:8000 (this device). If " +
        `the edge server runs elsewhere (e.g. http://${host}:8000), set ` +
        "window.SAFEBATH_API_BASE or <meta name=\"safebath-api-base\">.",
    };
  }
  if (host === "localhost" || host === "127.0.0.1" || !host) {
    const overridden = (e.window && e.window.SAFEBATH_API_BASE) ||
      (e.document && e.document.querySelector &&
        e.document.querySelector('meta[name="safebath-api-base"]'));
    return {
      base, via: "default",
      ok: true, reason: null,
      hint: overridden
        ? undefined
        : "The page is served from this device, so the app targets " +
          "localhost:8000. If the edge server runs on a Raspberry Pi on the " +
          "LAN, create frontend/config.local.js with " +
          "window.SAFEBATH_API_BASE = \"http://<pi-ip>:8000\" (see " +
          "config.example.js) or open the page with ?api=http://<pi-ip>:8000.",
    };
  }
  return { base, via: "ok", ok: true, reason: null };
}

export function healthFailureMessage(ctx) {
  const { apiBase, pageProtocol, error, response } = ctx || {};
  if (apiBase && apiBase.startsWith("http://") &&
      pageProtocol === "https:") {
    return "Mixed content: the page is https but the API base is http, so " +
      "the browser blocks the request. Set window.SAFEBATH_API_BASE (or the " +
      "meta tag) to an https API.";
  }
  if (response) {
    return `API answered HTTP ${response.status}${response.statusText ? " " + response.statusText : ""}.`;
  }
  if (error && error.name === "AbortError") {
    return "API request timed out.";
  }
  return `Can't reach ${apiBase || "(unknown)"} — check the edge server is running (uvicorn backend.api_server on :8000) and reachable from this device/network.`;
}