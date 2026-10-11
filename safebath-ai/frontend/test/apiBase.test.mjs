import { test } from "node:test";
import assert from "node:assert/strict";

import {
  healthFailureMessage,
  resolveApiBase,
  resolveApiInfo,
} from "../apiBase.mjs";

function fakeEnv({
  apiBase = undefined,
  hostname = "localhost",
  protocol = "http:",
  search = "",
  meta = null,
  session = {},
}) {
  const storage = {
    getItem: (k) => (k in session ? session[k] : null),
    setItem: (k, v) => { session[k] = String(v); },
    removeItem: (k) => { delete session[k]; },
  };
  const documentObj = {
    querySelector: (sel) =>
      sel === 'meta[name="safebath-api-base"]' && meta
        ? { content: meta }
        : null,
  };
  const windowObj = { location: { protocol, hostname, search } };
  if (apiBase) windowObj.SAFEBATH_API_BASE = apiBase;
  return {
    window: windowObj,
    document: documentObj,
    location: windowObj.location,
    sessionStorage: storage,
  };
}

test("window override has highest priority", () => {
  const e = fakeEnv({ apiBase: "https://safebath.example.com", hostname: "192.168.1.50" });
  assert.equal(resolveApiBase(e), "https://safebath.example.com");
});

test("meta tag beats derive + query param", () => {
  const e = fakeEnv({
    protocol: "http:",
    hostname: "192.168.43.66",
    search: "?api=http://127.0.0.1:9000",
    meta: "https://meta.example.com/edge",
  });
  assert.equal(resolveApiBase(e), "https://meta.example.com/edge");
});

test("?api= query param overrides fallback and persists to sessionStorage", () => {
  const session = {};
  const e = fakeEnv({ hostname: "hostinger.example", protocol: "https:", search: "?api=https://edge.example.net", session });
  assert.equal(resolveApiBase(e), "https://edge.example.net");
  assert.equal(session["safebath-api-base"], "https://edge.example.net");
});

test("sessionStorage value is used when present", () => {
  const e = fakeEnv({ protocol: "https:", hostname: "hostinger.example", session: { "safebath-api-base": "https://saved.example.net" } });
  assert.equal(resolveApiBase(e), "https://saved.example.net");
});

test("LAN http page on a non-local host derives host:8000", () => {
  const e = fakeEnv({ protocol: "http:", hostname: "192.168.43.66" });
  assert.equal(resolveApiBase(e), "http://192.168.43.66:8000");
  const info = resolveApiInfo(e);
  assert.equal(info.ok, true);
});

test("localhost http page falls back to localhost:8000", () => {
  const e = fakeEnv({ protocol: "http:", hostname: "localhost" });
  assert.equal(resolveApiBase(e), "http://localhost:8000");
});

test("localhost http page info is ok but hints at the Pi override", () => {
  const e = fakeEnv({ protocol: "http:", hostname: "localhost" });
  const info = resolveApiInfo(e);
  assert.equal(info.ok, true);
  assert.match(info.hint, /config\.local\.js/i);
  assert.match(info.hint, /<pi-ip>/i);
});

test("hint suppressed when an override is set on a localhost page", () => {
  const e = fakeEnv({ protocol: "http:", hostname: "localhost", apiBase: "http://192.168.43.66:8000" });
  const info = resolveApiInfo(e);
  assert.equal(info.ok, true);
  assert.equal(info.hint, undefined);
});

test("https page without override falls back but is flagged mixed-content", () => {
  const e = fakeEnv({ protocol: "https:", hostname: "safebath.hostinger.site" });
  assert.equal(resolveApiBase(e), "http://localhost:8000");
  const info = resolveApiInfo(e);
  assert.equal(info.ok, false);
  assert.match(info.reason, /mixed content/i);
});

test("https page with https override resolves ok", () => {
  const e = fakeEnv({ protocol: "https:", hostname: "safebath.hostinger.site", apiBase: "https://edge.example.net" });
  const info = resolveApiInfo(e);
  assert.equal(info.ok, true);
  assert.equal(info.reason, null);
});

test("file:// page is flagged with an actionable reason", () => {
  const e = fakeEnv({ protocol: "file:", hostname: "" });
  assert.equal(resolveApiBase(e), "http://localhost:8000");
  const info = resolveApiInfo(e);
  assert.equal(info.ok, false);
  assert.match(info.reason, /file:\/\//);
});

test("healthFailureMessage reports mixed content first", () => {
  const msg = healthFailureMessage({
    apiBase: "http://192.168.1.50:8000",
    pageProtocol: "https:",
    error: new TypeError("Failed to fetch"),
  });
  assert.match(msg, /mixed content/i);
});

test("healthFailureMessage reports HTTP status when server answers", () => {
  const msg = healthFailureMessage({
    apiBase: "http://localhost:8000",
    pageProtocol: "http:",
    response: { status: 503, statusText: "Service Unavailable" },
  });
  assert.match(msg, /503/);
});

test("healthFailureMessage reports network failure with base", () => {
  const msg = healthFailureMessage({
    apiBase: "http://192.168.43.66:8000",
    pageProtocol: "http:",
    error: new TypeError("Failed to fetch"),
  });
  assert.match(msg, /Can't reach/);
  assert.match(msg, /192\.168\.43\.66:8000/);
});