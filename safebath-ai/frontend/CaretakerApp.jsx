/*
 * CaretakerApp.jsx — Caregiver-facing danger monitor for SafeBath AI
 *
 * UI inspired by the "I'm Safe" women's safety app (Ruan Tech Solutions):
 *   - Clean blue theme (#2D398B, #336AEA)
 *   - Rounded cards with subtle box shadows
 *   - Prominent SOS-style emergency indicators
 *   - Caretaker-device location only after explicit browser permission
 *   - Mobile-first responsive layout
 *
 * Features:
 *   - Live patient status (score, band, state, active flags)
 *   - SSE-driven alert feed (instant push on danger)
 *   - Browser Notifications API (permission requested on first load)
 *   - Audio alarm (Web Audio API oscillator) on critical/high alerts
 *   - Acknowledge alerts (POST /caretaker/ack/{id})
 *   - Alert history with severity color coding
 *   - Connection health indicator
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { healthFailureMessage, resolveApiBase, resolveApiInfo } from "./apiBase.mjs";

/* API base is resolved by the shared apiBase.js module (window override,
 * meta tag, ?api= query param, or auto-derive http://<host>:8000 on the LAN).
 * The caretaker backend is mounted under /caretaker. */
const API_ROOT = resolveApiBase();
const API_BASE = `${API_ROOT}/caretaker`;
const API_INFO = resolveApiInfo();
const POLL_MS = 5000;

/* Leaflet CSS + JS loaded from CDN once */
function ensureLeaflet() {
  if (window.L) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
  document.head.appendChild(link);
  const script = document.createElement("script");
  script.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
  document.head.appendChild(script);
}
ensureLeaflet();

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */
const fmt = (v, d = 1) =>
  v === null || v === undefined ? "—" : Number(v).toFixed(d);

function timeAgo(ts) {
  if (!ts) return "";
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

const SEVERITY_STYLES = {
  critical: {
    bg: "linear-gradient(135deg, #dc2626 0%, #991b1b 100%)",
    border: "#dc2626",
    text: "#fff",
    icon: "🚨",
    shadow: "0 8px 32px rgba(220,38,38,0.3)",
  },
  high: {
    bg: "linear-gradient(135deg, #f59e0b 0%, #d97706 100%)",
    border: "#f59e0b",
    text: "#fff",
    icon: "⚠️",
    shadow: "0 8px 32px rgba(245,158,11,0.3)",
  },
  medium: {
    bg: "linear-gradient(135deg, #336aea 0%, #2D398B 100%)",
    border: "#336aea",
    text: "#fff",
    icon: "ℹ️",
    shadow: "0 8px 32px rgba(51,106,234,0.3)",
  },
  normal: {
    bg: "#f8fafc",
    border: "#e2e8f0",
    text: "#475569",
    icon: "✅",
    shadow: "0 4px 16px rgba(0,0,0,0.06)",
  },
};

const TRIGGER_LABELS = {
  mat_sustained_pressure: "Fall Detected — Sustained Mat Pressure",
  mat_centroid_jump: "Stumble Detected — Sudden Mat Shift",
  fall: "Fall Detected — ConvLSTM Classifier",
  radar_collapse: "Collapse Detected — Radar Motionless",
  thud_confirmed: "Impact Detected — Audio + Radar",
  sustained_cry: "Distress Detected — Sustained Cry",
  tof_collapse: "Collapse Detected — ToF Posture",
  scald_shutoff: "Scald Alert — Hot Water Shut Off",
  unattended_hot_fill: "Hot Water — Unattended",
  are_you_ok: "Voice Triage — Awaiting Response",
};

/* ------------------------------------------------------------------ */
/*  Audio alarm (Web Audio API)                                        */
/* ------------------------------------------------------------------ */
function useAlarm() {
  const ctxRef = useRef(null);
  const playingRef = useRef(false);
  const loopRef = useRef(null);

  const startAlarm = useCallback(() => {
    if (playingRef.current) return;
    try {
      const ctx =
        ctxRef.current ||
        new (window.AudioContext || window.webkitAudioContext)();
      ctxRef.current = ctx;
      if (ctx.state === "suspended") ctx.resume();
      playingRef.current = true;

      // Two-tone urgent siren: fast beeps alternating 880Hz / 1100Hz,
      // repeating every 1.2s, for as long as danger persists
      const PATTERNS = [
        { freq: 880, dur: 0.12, gap: 0.04 },
        { freq: 1100, dur: 0.12, gap: 0.04 },
        { freq: 880, dur: 0.12, gap: 0.04 },
        { freq: 1100, dur: 0.25, gap: 0.15 },
      ];
      const GAIN = 0.35;

      function playCycle() {
        if (!playingRef.current) return;
        let t = ctx.currentTime;
        for (const p of PATTERNS) {
          const osc = ctx.createOscillator();
          const g = ctx.createGain();
          osc.type = "square";
          osc.frequency.setValueAtTime(p.freq, t);
          g.gain.setValueAtTime(GAIN, t);
          g.gain.setValueAtTime(0, t + p.dur);
          osc.connect(g);
          g.connect(ctx.destination);
          osc.start(t);
          osc.stop(t + p.dur + 0.01);
          t += p.dur + p.gap;
        }
      }

      playCycle();
      loopRef.current = setInterval(() => {
        if (!playingRef.current) {
          clearInterval(loopRef.current);
          return;
        }
        playCycle();
      }, 1200);
    } catch (e) {
      /* audio blocked by browser policy */
    }
  }, []);

  const stopAlarm = useCallback(() => {
    playingRef.current = false;
    if (loopRef.current) {
      clearInterval(loopRef.current);
      loopRef.current = null;
    }
    if (ctxRef.current) {
      try {
        ctxRef.current.close();
      } catch {}
      ctxRef.current = null;
    }
  }, []);

  return { startAlarm, stopAlarm };
}

/* ------------------------------------------------------------------ */
/*  Browser Push Notification                                          */
/* ------------------------------------------------------------------ */
function requestNotificationPermission() {
  if (!("Notification" in window)) return;
  if (Notification.permission === "default") {
    Notification.requestPermission();
  }
}

function pushNotify(title, body, severity) {
  if (
    !("Notification" in window) ||
    Notification.permission !== "granted"
  )
    return;
  try {
    const n = new Notification(title, {
      body,
      tag: "safebath-alert",
      requireInteraction: severity === "critical",
    });
    setTimeout(() => n.close(), 15000);
  } catch {}
}

/* ------------------------------------------------------------------ */
/*  Web Push subscription (browser push, works when the tab is closed) */
/* ------------------------------------------------------------------ */
function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return null;
  try {
    return await navigator.serviceWorker.register("./sw.js");
  } catch {
    return null;
  }
}

/* Returns {state} where state ∈ subscribed | denied | unsupported |
 * not-configured | error. The subscription itself is POSTed to the edge server,
 * which stores it for server-side push (pywebpush) on new emergencies. */
async function getPushState() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return "unsupported";
  if (("Notification" in window) && Notification.permission === "denied") return "denied";
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    if (!reg) return "default";
    const sub = await reg.pushManager.getSubscription();
    return sub ? "subscribed" : "default";
  } catch {
    return "unsupported";
  }
}

async function enablePush() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    return { state: "unsupported" };
  }
  const reg = await registerServiceWorker();
  if (!reg) return { state: "unsupported" };
  try {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") return { state: "denied" };
    const r = await fetch(`${API_BASE}/push/vapid-public-key`);
    const info = await r.json();
    if (!info.configured || !info.public_key) return { state: "not-configured" };
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(info.public_key),
    });
    await fetch(`${API_BASE}/push/subscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sub.toJSON()),
    });
    return { state: "subscribed", subscription: sub };
  } catch {
    return { state: "error" };
  }
}

async function disablePush() {
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = reg && (await reg.pushManager.getSubscription());
    if (sub) {
      const endpoint = sub.endpoint;
      await sub.unsubscribe();
      await fetch(`${API_BASE}/push/unsubscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ endpoint }),
      });
    }
    return "default";
  } catch {
    return "error";
  }
}

/* ------------------------------------------------------------------ */
/*  Acknowledgement sync queue (offline-first)                         */
/*  Acks are applied locally first; if the POST fails they are queued   */
/*  in localStorage and replayed when the edge server is reachable.     */
/* ------------------------------------------------------------------ */
const ACK_KEY = "safebath_pending_acks";

function loadAcks() {
  try { return JSON.parse(localStorage.getItem(ACK_KEY) || "[]"); } catch { return []; }
}
function saveAcks(list) {
  try { localStorage.setItem(ACK_KEY, JSON.stringify(list)); } catch {}
}
function queueAck(kind, id, by) {
  const list = loadAcks().filter((a) => !(a.kind === kind && a.id === id));
  list.push({ kind, id, by: by || "caretaker", ts: Date.now() / 1000 });
  saveAcks(list);
  return list.length;
}
async function postAck(kind, id, by, token) {
  const by2 = encodeURIComponent(by || "caretaker");
  const url = kind === "emergency"
    ? `${API_BASE}/emergencies/${encodeURIComponent(id)}/ack?by=${by2}`
    : `${API_BASE}/ack/${id}?by=${by2}`;
  const r = await fetch(url, { method: "POST", headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (r.status === 401 || r.status === 503) {
    const error = new Error(r.status === 503 ? "Caretaker actions are disabled on the API; configure SAFEBATH_CARETAKER_TOKEN." : "Caretaker token was rejected. Check it and retry.");
    error.authorizationFailure = true;
    throw error;
  }
  // 404/409 mean the ack is moot; stop retrying but keep the local state.
  return r.ok || r.status === 404 || r.status === 409;
}
async function flushAcks(token) {
  if (!token) return loadAcks().length;
  const list = loadAcks();
  if (!list.length) return 0;
  const remaining = [];
  for (const a of list) {
    try {
      const done = await postAck(a.kind, a.id, a.by, token);
      if (!done) remaining.push(a);
    } catch {
      remaining.push(a);
    }
  }
  saveAcks(remaining);
  return remaining.length;
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

/* Status indicator dot */
function StatusDot({ color, size = 10, pulse = false }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: size,
        height: size,
        borderRadius: "50%",
        background: color,
        boxShadow: `0 0 ${pulse ? "12px" : "6px"} ${color}`,
        flexShrink: 0,
        animation: pulse ? "pulse-dot 1.5s infinite" : "none",
      }}
    />
  );
}

/* Circular risk gauge (I'm Safe style) */
function RiskGauge({ score, band, state }) {
  const c =
    band === "high"
      ? "#dc2626"
      : band === "watch"
      ? "#f59e0b"
      : "#22c55e";
  const pct = Math.min(100, Math.max(0, score));
  const isDanger = state === "alerting" || state === "voice_pending";

  return (
    <div
      style={{
        position: "relative",
        width: 160,
        height: 160,
        flexShrink: 0,
      }}
    >
      <svg viewBox="0 0 160 160" width="160" height="160">
        {/* Background circle */}
        <circle
          cx="80"
          cy="80"
          r="70"
          fill="none"
          stroke="#e2e8f0"
          strokeWidth="10"
        />
        {/* Progress arc */}
        <circle
          cx="80"
          cy="80"
          r="70"
          fill="none"
          stroke={c}
          strokeWidth="10"
          strokeDasharray={`${pct * 4.4} 440`}
          strokeLinecap="round"
          transform="rotate(-90 80 80)"
          style={{ transition: "stroke-dasharray 0.8s, stroke 0.3s" }}
        />
        {/* Score text */}
        <text
          x="80"
          y="72"
          textAnchor="middle"
          fill={c}
          fontSize="36"
          fontWeight="800"
          fontFamily="IBM Plex Sans, system-ui, sans-serif"
        >
          {Math.round(pct)}
        </text>
        <text
          x="80"
          y="96"
          textAnchor="middle"
          fill="#64748b"
          fontSize="12"
          fontWeight="600"
          fontFamily="IBM Plex Sans, system-ui, sans-serif"
        >
          {band?.toUpperCase() || "NORMAL"}
        </text>
        {/* Danger pulse ring */}
        {isDanger && (
          <circle
            cx="80"
            cy="80"
            r="74"
            fill="none"
            stroke="#dc2626"
            strokeWidth="2"
            opacity="0.6"
          >
            <animate
              attributeName="r"
              values="74;80;74"
              dur="1.5s"
              repeatCount="indefinite"
            />
            <animate
              attributeName="opacity"
              values="0.6;0.2;0.6"
              dur="1.5s"
              repeatCount="indefinite"
            />
          </circle>
        )}
      </svg>
    </div>
  );
}

/* SOS emergency button (I'm Safe style) */
function SOSButton({ isDanger, onActivate }) {
  return (
    <button
      onClick={onActivate}
      style={{
        width: 80,
        height: 80,
        borderRadius: "50%",
        border: "4px solid",
        borderColor: isDanger ? "#dc2626" : "#e2e8f0",
        background: isDanger
          ? "linear-gradient(135deg, #dc2626 0%, #991b1b 100%)"
          : "linear-gradient(135deg, #f1f5f9 0%, #e2e8f0 100%)",
        color: isDanger ? "#fff" : "#94a3b8",
        fontSize: 14,
        fontWeight: 800,
        fontFamily: "IBM Plex Sans, system-ui, sans-serif",
        cursor: "pointer",
        boxShadow: isDanger
          ? "0 8px 32px rgba(220,38,38,0.4)"
          : "0 4px 16px rgba(0,0,0,0.1)",
        transition: "all 0.3s",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
      }}
    >
      SOS
    </button>
  );
}

/* Location Map (Leaflet + device Geolocation) */
function LocationMap({ isDanger }) {
  const mapRef = useRef(null);
  const mapInstance = useRef(null);
  const markerRef = useRef(null);
  const [coords, setCoords] = useState(null);
  const [error, setError] = useState(null);
  const [requestCount, setRequestCount] = useState(0);
  const [clock, setClock] = useState(Date.now());

  useEffect(() => {
    if (!requestCount) return undefined;
    if (!window.isSecureContext) {
      setError("Browser location requires HTTPS or localhost");
      return undefined;
    }
    if (!navigator.geolocation) {
      setError("Geolocation not supported");
      return undefined;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setCoords({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          ts: Date.now() / 1000,
        });
        setError(null);
      },
      (err) => setError(err.message),
      { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 }
    );
    return undefined;
  }, [requestCount]);

  useEffect(() => {
    const id = setInterval(() => setClock(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!mapRef.current || !window.L || mapInstance.current) return;
    const L = window.L;
    if (!coords) return;
    const initial = coords;
    const map = L.map(mapRef.current, {
      center: [initial.lat, initial.lng],
      zoom: 16,
      zoomControl: false,
      attributionControl: false,
    });
    L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      { maxZoom: 19 }
    ).addTo(map);
    const icon = L.divIcon({
      className: "",
      html: `<div style="
        width:24px;height:24px;border-radius:50%;
        background:${isDanger ? "#dc2626" : "#22c55e"};
        border:4px solid #fff;box-shadow:0 0 20px ${isDanger ? "#dc2626" : "#22c55e"};
        ${isDanger ? "animation: pulse-marker 1s infinite;" : ""}
      "></div>`,
      iconSize: [24, 24],
      iconAnchor: [12, 12],
    });
    markerRef.current = L.marker([initial.lat, initial.lng], {
      icon,
    }).addTo(map);
    mapInstance.current = map;
    setTimeout(() => map.invalidateSize(), 200);
  }, [coords]);

  useEffect(() => {
    if (!coords || !mapInstance.current || !window.L) return;
    const L = window.L;
    markerRef.current.setLatLng([coords.lat, coords.lng]);
    mapInstance.current.setView([coords.lat, coords.lng], 16, {
      animate: true,
    });
    const icon = L.divIcon({
      className: "",
      html: `<div style="
        width:24px;height:24px;border-radius:50%;
        background:${isDanger ? "#dc2626" : "#22c55e"};
        border:4px solid #fff;box-shadow:0 0 20px ${isDanger ? "#dc2626" : "#22c55e"};
        ${isDanger ? "animation: pulse-marker 1s infinite;" : ""}
      "></div>`,
      iconSize: [24, 24],
      iconAnchor: [12, 12],
    });
    markerRef.current.setIcon(icon);
  }, [coords, isDanger]);

  return (
    <div
      style={{
        background: "#fff",
        borderRadius: 16,
        boxShadow: "0 4px 24px rgba(0,0,0,0.08)",
        overflow: "hidden",
        marginBottom: 20,
      }}
    >
      <div
        style={{
          padding: "14px 20px",
          borderBottom: "1px solid #f1f5f9",
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <span style={{ fontSize: 18 }}>📍</span>
        <span
          style={{
            fontSize: 15,
            fontWeight: 700,
            color: "#1a3760",
            fontFamily: "IBM Plex Sans, system-ui, sans-serif",
          }}
        >
          Caretaker Device Location
        </span>
        <button type="button" onClick={() => { setError(null); setRequestCount((count) => count + 1); }} style={{ marginLeft: "auto", border: 0, borderRadius: 8, padding: "7px 10px", background: "#336aea", color: "#fff", cursor: "pointer" }}>Request location</button>
        {coords && (
          <span
            style={{
              fontSize: 11,
              color: "#94a3b8",
              marginLeft: "auto",
              fontFamily: "IBM Plex Sans, system-ui, sans-serif",
            }}
          >
            {coords.lat.toFixed(5)}, {coords.lng.toFixed(5)}
          </span>
        )}
        {error && (
          <span
            style={{
              fontSize: 11,
              color: "#dc2626",
              marginLeft: "auto",
              fontFamily: "IBM Plex Sans, system-ui, sans-serif",
            }}
          >
            {error}
          </span>
        )}
      </div>
      <p style={{ padding: "0 18px", color: "#64748b", fontSize: 12 }}>This is the caretaker browser device only. Bathroom/incident location is unavailable because no installation coordinates or device GPS telemetry are configured.</p>
      {coords && clock / 1000 - coords.ts > 300 && <p style={{ padding: "0 18px", color: "#b45309", fontSize: 12 }}>Caretaker device coordinates are stale. Request location to refresh.</p>}
      {coords && clock / 1000 - coords.ts <= 300 && <p style={{ padding: "0 18px", fontSize: 12 }}><a href={`https://maps.google.com/?q=${coords.lat},${coords.lng}`} target="_blank" rel="noreferrer">Open caretaker device location in map</a> · not the patient location</p>}
      <div
        ref={mapRef}
        style={{
          height: 220,
          width: "100%",
          background: "#f1f5f9",
        }}
      />
    </div>
  );
}

/* Alert card (I'm Safe style with gradient background) */
function AlertCard({ alert, onAck }) {
  const sev = SEVERITY_STYLES[alert.severity] || SEVERITY_STYLES.normal;
  const label =
    TRIGGER_LABELS[alert.trigger_name] || alert.trigger_name;

  return (
    <div
      style={{
        background: sev.bg,
        borderRadius: 16,
        padding: "18px 20px",
        marginBottom: 12,
        boxShadow: sev.shadow,
        opacity: alert.active ? 1 : 0.6,
        border: `1px solid ${sev.border}20`,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 10,
        }}
      >
        <span style={{ fontSize: 22 }}>{sev.icon}</span>
        <span
          style={{
            fontSize: 12,
            fontWeight: 700,
            color: sev.text,
            textTransform: "uppercase",
            letterSpacing: 0.5,
            fontFamily: "IBM Plex Sans, system-ui, sans-serif",
          }}
        >
          {alert.severity}
        </span>
        <span
          style={{
            fontSize: 11,
            color: `${sev.text}99`,
            marginLeft: "auto",
            fontFamily: "IBM Plex Sans, system-ui, sans-serif",
          }}
        >
          {timeAgo(alert.ts)}
        </span>
      </div>
      <div
        style={{
          fontSize: 15,
          fontWeight: 700,
          color: sev.text,
          marginBottom: 6,
          fontFamily: "IBM Plex Sans, system-ui, sans-serif",
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 13,
          color: `${sev.text}cc`,
          marginBottom: alert.active ? 12 : 0,
          fontFamily: "IBM Plex Sans, system-ui, sans-serif",
        }}
      >
        {alert.message}
      </div>
      {alert.active && (
        <button
          onClick={() => onAck(alert.id)}
          style={{
            background: "rgba(255,255,255,0.2)",
            color: sev.text,
            border: `1px solid ${sev.text}40`,
            borderRadius: 10,
            padding: "10px 20px",
            fontSize: 13,
            fontWeight: 700,
            cursor: "pointer",
            width: "100%",
            fontFamily: "IBM Plex Sans, system-ui, sans-serif",
            backdropFilter: "blur(4px)",
          }}
        >
          Acknowledge Alert
        </button>
      )}
      {!alert.active && alert.acknowledged_by && (
        <div
          style={{
            fontSize: 11,
            color: `${sev.text}88`,
            fontFamily: "IBM Plex Sans, system-ui, sans-serif",
          }}
        >
          ✓ Acknowledged by {alert.acknowledged_by}{" "}
          {timeAgo(alert.acknowledged_at)}
        </div>
      )}
    </div>
  );
}

/* Emergency event card — the durable, offline-first record (emergency_events).
 * Distinct from transient alerts: acknowledging marks the *notification*
 * handled, not that the physical emergency ended. */
function EmergencyCard({ event, onAck, onResolve }) {
  const sev = event.severity || "watch";
  const color = sev === "high" || sev === "critical" ? "#dc2626"
    : sev === "watch" ? "#f59e0b" : "#0ea5e9";
  const acknowledged = event.state === "acknowledged";
  const label = TRIGGER_LABELS[event.trigger_name] || event.trigger_name || "Emergency";
  return (
    <div style={{
      background: "#fff", borderRadius: 16, padding: "18px 20px",
      marginBottom: 16, boxShadow: "0 4px 24px rgba(0,0,0,0.08)",
      borderLeft: `5px solid ${color}`,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
        <span style={{ fontSize: 11, fontWeight: 800, color,
          textTransform: "uppercase", letterSpacing: 0.5 }}>
          {sev}
        </span>
        <span style={{ fontSize: 11, color: "#94a3b8", marginLeft: "auto" }}>
          {timeAgo(event.ts)}
        </span>
      </div>
      <div style={{ fontSize: 15, fontWeight: 700, color: "#1a3760", marginBottom: 4 }}>
        {label}
      </div>
      <div style={{ fontSize: 13, color: "#475569", marginBottom: 10 }}>
        {event.message}
        {event.score != null && ` · risk ${Math.round(event.score)}`}
      </div>
      {event.evidence && <details style={{ margin: "8px 0", fontSize: 12, color: "#475569" }}><summary>Detection evidence</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(event.evidence, null, 2)}</pre></details>}
      {event.state === "resolved" ? (
        <div style={{ fontSize: 11, color: "#64748b" }}>Resolved by {event.resolved_by || "caretaker"} {event.resolved_at ? timeAgo(event.resolved_at) : ""}</div>
      ) : <>
      {acknowledged ? (
        <div style={{ fontSize: 11, color: "#64748b" }}>
          ✓ Notification acknowledged by {event.ack_by || "caretaker"}{" "}
          {event.ack_ts ? timeAgo(event.ack_ts) : ""}
        </div>
      ) : (
        <button onClick={() => onAck(event.event_id)} style={{
          background: color, color: "#fff", border: "none", borderRadius: 10,
          padding: "10px 20px", fontSize: 13, fontWeight: 700, cursor: "pointer",
          width: "100%",
        }}>
          Acknowledge Emergency
        </button>
      )}
      {acknowledged && <button onClick={() => onResolve(event.event_id)} style={{ marginTop: 10, background: "#166534", color: "#fff", border: 0, borderRadius: 10, padding: "10px 20px", fontWeight: 700, cursor: "pointer", width: "100%" }}>Resolve incident</button>}
      </>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main App                                                           */
/* ------------------------------------------------------------------ */
export default function CaretakerApp() {
  const [caretakerToken, setCaretakerToken] = useState("");
  const [status, setStatus] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [voiceMessages, setVoiceMessages] = useState([]);
  const [emergencies, setEmergencies] = useState([]);
  const [connected, setConnected] = useState(false);
  const [apiError, setApiError] = useState(null);
  const [actionError, setActionError] = useState("");
  const [dataError, setDataError] = useState("");
  const [lastDataUpdate, setLastDataUpdate] = useState(0);
  const [lastAlertId, setLastAlertId] = useState(0);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [notificationRequested, setNotificationRequested] = useState(false);
  const [pushState, setPushState] = useState("unsupported");
  const [pendingAcks, setPendingAcks] = useState(() => loadAcks().length);
  const { startAlarm, stopAlarm } = useAlarm();
  const prevScoreRef = useRef(0);
  const esRef = useRef(null);

  // register the PWA service worker (enables push + offline app shell) and
  // reflect the current push subscription state in the header button.
  useEffect(() => {
    registerServiceWorker();
    let active = true;
    (async () => {
      const s = await getPushState();
      if (active) setPushState(s);
    })();
    return () => { active = false; };
  }, []);

  // replay any acknowledgements queued while offline
  const syncAcks = useCallback(async () => {
    try {
      const remaining = await flushAcks(caretakerToken);
      setPendingAcks(remaining);
    } catch {}
  }, [caretakerToken]);
  useEffect(() => {
    syncAcks();
    const id = setInterval(syncAcks, 15000);
    window.addEventListener("online", syncAcks);
    return () => { clearInterval(id); window.removeEventListener("online", syncAcks); };
  }, [syncAcks]);

  // request notification permission on user interaction
  const requestNotifications = useCallback(() => {
    if (!notificationRequested) {
      requestNotificationPermission();
      setNotificationRequested(true);
    }
  }, [notificationRequested]);

  // poll status
  useEffect(() => {
    let active = true;
    async function poll() {
      while (active) {
        try {
          const r = await fetch(`${API_BASE}/status`);
          if (r.ok) {
            const s = await r.json();
            setStatus(s);
            setConnected(true);
            setApiError(null);

            if (
              s.state === "voice_pending" ||
              s.state === "alerting"
            ) {
              if (s.last_alert && s.last_alert.id > lastAlertId) {
                const la = s.last_alert;
                pushNotify(
                  `SafeBath — ${la.severity?.toUpperCase()}`,
                  la.message,
                  la.severity
                );
                if (soundEnabled) startAlarm();
                setLastAlertId(la.id);
              }
            }

            if (
              s.risk_score >= 60 &&
              prevScoreRef.current < 60 &&
              soundEnabled
            ) {
              startAlarm();
            }
            prevScoreRef.current = s.risk_score;
          }
        } catch (err) {
          setConnected(false);
          setApiError(healthFailureMessage({
            apiBase: API_ROOT,
            pageProtocol: window.location.protocol,
            error: err,
          }));
        }
        await new Promise((r) => setTimeout(r, POLL_MS));
      }
    }
    poll();
    return () => {
      active = false;
    };
  }, [soundEnabled, startAlarm, lastAlertId]);

  // fetch alert list + emergency events (the offline-first source of truth)
  const fetchData = useCallback(async () => {
    try {
      const responses = await Promise.all([
        fetch(`${API_BASE}/alerts?limit=30`),
        fetch(`${API_BASE}/emergencies?limit=20`),
        fetch(`${API_BASE}/voice-messages?limit=60`),
      ]);
      if (responses.some((r) => !r.ok)) throw new Error(`Caretaker data request failed (HTTP ${responses.find((r) => !r.ok).status}).`);
      const [a, e, v] = await Promise.all(responses.map((r) => r.json()));
      if (!Array.isArray(a.alerts) || !Array.isArray(e.emergencies) || !Array.isArray(v.messages)) throw new Error("Caretaker API returned an invalid response shape.");
      setAlerts(a.alerts);
      setEmergencies(e.emergencies);
      setVoiceMessages(v.messages);
      setLastDataUpdate(Date.now() / 1000);
      setDataError("");
    } catch (err) { setDataError(err.message || "Could not load caretaker records."); }
  }, []);

  useEffect(() => {
    let active = true;
    let timer;
    const poll = async () => { await fetchData(); if (active) timer = setTimeout(poll, POLL_MS); };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [fetchData]);

  // SSE stream for instant push
  useEffect(() => {
    let es;
    try {
      es = new EventSource(`${API_BASE}/stream`);
      esRef.current = es;
      es.onmessage = (e) => {
        try {
          const d = JSON.parse(e.data);
          if (d.type === "alert") {
            setAlerts((prev) => [d, ...prev].slice(0, 50));
            if (d.active) {
              pushNotify(
                `SafeBath — ${d.severity?.toUpperCase()}`,
                d.message,
                d.severity
              );
              if (soundEnabled) startAlarm();
            }
          } else if (d.type === "risk_update") {
            setStatus((prev) =>
              prev
                ? {
                    ...prev,
                    risk_score: d.score,
                    band: d.band,
                    state: d.state,
                    flags: d.flags,
                  }
                : prev
            );
          } else if (d.type === "voice_message") {
            setVoiceMessages((prev) => [...prev.filter((m) => m.id !== d.id), d].slice(-60));
          }
        } catch {}
      };
      es.onerror = () => setConnected(false);
      es.onopen = () => setConnected(true);
    } catch {}
    return () => {
      if (es) es.close();
    };
  }, [soundEnabled, startAlarm]);

  /* Acknowledge locally first, then try the server. If the edge server is
   * unreachable the ack is queued and replayed later (offline-first). */
  const handleAck = useCallback(async (id) => {
    if (!caretakerToken) { setActionError("Enter the caretaker action token before acknowledging alerts."); return; }
    setAlerts((prev) =>
      prev.map((a) =>
        a.id === id
          ? { ...a, active: 0, acknowledged_by: "caretaker",
              acknowledged_at: Date.now() / 1000 }
          : a
      )
    );
    try {
      const done = await postAck("alert", id, "caretaker", caretakerToken);
      setActionError("");
      if (!done) setPendingAcks(queueAck("alert", id, "caretaker"));
    } catch (err) {
      if (err.authorizationFailure) { setActionError(err.message); await fetchData(); return; }
      setPendingAcks(queueAck("alert", id, "caretaker"));
    }
  }, [caretakerToken, fetchData]);

  const handleEmergencyAck = useCallback(async (eventId) => {
    if (!caretakerToken) { setActionError("Enter the caretaker action token before acknowledging emergencies."); return; }
    setEmergencies((prev) =>
      prev.map((e) =>
        e.event_id === eventId
          ? { ...e, state: "acknowledged",
              acknowledged_by: "caretaker", acknowledged_at: Date.now() / 1000 }
          : e
      )
    );
    try {
      const done = await postAck("emergency", eventId, "caretaker", caretakerToken);
      setActionError("");
      if (!done) setPendingAcks(queueAck("emergency", eventId, "caretaker"));
    } catch (err) {
      if (err.authorizationFailure) { setActionError(err.message); await fetchData(); return; }
      setPendingAcks(queueAck("emergency", eventId, "caretaker"));
    }
  }, [caretakerToken, fetchData]);

  const handleEmergencyResolve = useCallback(async (eventId) => {
    try {
      const response = await fetch(`${API_BASE}/emergencies/${encodeURIComponent(eventId)}/resolve`, {
        method: "POST", headers: { Authorization: `Bearer ${caretakerToken}` },
      });
      if (!response.ok) throw new Error(response.status === 503 ? "Set SAFEBATH_CARETAKER_TOKEN on the server to enable actions." : `Resolve failed (HTTP ${response.status}).`);
      await response.json();
      setActionError("");
      setEmergencies((items) => items.map((item) => item.event_id === eventId ? { ...item, state: "resolved", resolved_by: "caretaker", resolved_at: Date.now() / 1000 } : item));
    } catch (err) { setActionError(err.message || "Could not resolve incident."); }
  }, [caretakerToken]);

  const togglePush = useCallback(async () => {
    if (pushState === "subscribed") {
      await disablePush();
      setPushState("default");
      return;
    }
    const res = await enablePush();
    setPushState(res.state === "subscribed" ? "subscribed" : res.state);
  }, [pushState]);

  const isDanger =
    status?.state === "alerting" || status?.state === "voice_pending";
  const activeAlerts = alerts.filter((a) => a.active);
  const activeEmergencies = emergencies.filter(
    (e) => e.state !== "resolved" && e.state !== "acknowledged"
  );
  const recentEmergencies = emergencies.filter((e) => e.state === "resolved" || e.state === "acknowledged");

  return (
    <div
      style={{
        minHeight: "100vh",
        background: isDanger
          ? "linear-gradient(180deg, #fef2f2 0%, #fff 100%)"
          : "linear-gradient(180deg, #f0f4ff 0%, #fff 100%)",
        fontFamily: "IBM Plex Sans, system-ui, sans-serif",
        padding: "0 0 40px 0",
        transition: "background 0.5s",
      }}
    >
      {/* Header */}
      <div
        style={{
          background: isDanger
            ? "linear-gradient(135deg, #dc2626 0%, #991b1b 100%)"
            : "linear-gradient(135deg, #2D398B 0%, #336aea 100%)",
          padding: "20px 24px",
          color: "#fff",
          display: "flex",
          alignItems: "center",
          gap: 14,
          boxShadow: isDanger
            ? "0 4px 24px rgba(220,38,38,0.3)"
            : "0 4px 24px rgba(45,57,139,0.3)",
        }}
      >
        <div
          style={{
            fontSize: 22,
            fontWeight: 800,
            letterSpacing: -0.3,
          }}
        >
          SafeBath
        </div>
        <div
          style={{
            fontSize: 14,
            fontWeight: 500,
            opacity: 0.8,
            borderLeft: "1px solid rgba(255,255,255,0.3)",
            paddingLeft: 14,
          }}
        >
          Caretaker
        </div>
        <div
          style={{
            marginLeft: "auto",
            display: "flex",
            alignItems: "center",
            gap: 10,
          }}
        >
          <input aria-label="Caretaker action token" type="password" autoComplete="off" placeholder="Caretaker action token" value={caretakerToken} onChange={(event) => { setCaretakerToken(event.target.value); setActionError(""); }} style={{ width: 150, borderRadius: 8, border: "1px solid rgba(255,255,255,.35)", padding: "7px 9px", background: "rgba(255,255,255,.12)", color: "#fff" }} />
          <button
            onClick={togglePush}
            disabled={["unsupported", "denied", "not-configured"].includes(pushState)}
            style={{
              background: pushState === "subscribed"
                ? "rgba(74,222,128,0.35)" : "rgba(255,255,255,0.15)",
              border: "none",
              color: "#fff",
              fontSize: 16,
              cursor: ["unsupported", "denied", "not-configured"].includes(pushState)
                ? "not-allowed" : "pointer",
              opacity: ["unsupported", "denied", "not-configured"].includes(pushState)
                ? 0.5 : 1,
              padding: "6px 10px",
              borderRadius: 8,
            }}
            title={
              pushState === "subscribed" ? "Push notifications on (tap to disable)"
                : pushState === "denied" ? "Push blocked in browser settings"
                : pushState === "not-configured" ? "Server push not configured (VAPID keys missing)"
                : pushState === "unsupported" ? "Push not supported on this device"
                : "Enable push notifications"
            }
          >
            {pushState === "subscribed" ? "🔔" : "🔕"}
          </button>
          <button
            onClick={() => {
              requestNotifications();
              if (soundEnabled) stopAlarm();
              setSoundEnabled((p) => !p);
            }}
            style={{
              background: "rgba(255,255,255,0.15)",
              border: "none",
              color: "#fff",
              fontSize: 16,
              cursor: "pointer",
              padding: "6px 10px",
              borderRadius: 8,
            }}
            title={soundEnabled ? "Mute alarm" : "Enable alarm"}
          >
            {soundEnabled ? "🔊" : "🔇"}
          </button>
          <StatusDot
            color={connected ? "#4ade80" : "#fca5a5"}
            pulse={connected}
          />
          <span style={{ fontSize: 12, fontWeight: 500 }}>
            {connected ? "Live" : "Offline"}
          </span>
        </div>
      </div>

      {!connected && (
        <div style={{ fontSize: 11, color: "#fca5a5", background: "#450a0a", border: "1px solid #7f1d1d", borderRadius: 10, padding: "8px 12px", margin: "8px 16px 0", fontFamily: "monospace", lineHeight: 1.5 }}>
          API base: <b>{API_ROOT}</b>
          {API_INFO.reason
            ? ` — ${API_INFO.reason}`
            : API_INFO.hint
              ? ` — ${API_INFO.hint}`
              : ` — ${apiError || "connection failed (server not running or unreachable on port 8000)"}`}
        </div>
      )}

      {/* Content container */}
      <div style={{ padding: "20px 16px", maxWidth: 480, margin: "0 auto" }}>
        {actionError && <div role="alert" style={{ background: "#fef2f2", color: "#991b1b", padding: 10, borderRadius: 10, marginBottom: 12, fontSize: 12 }}>{actionError}</div>}
        {(dataError || lastDataUpdate) && <div role={dataError ? "alert" : "status"} style={{ background: dataError ? "#fef2f2" : "#eff6ff", color: dataError ? "#991b1b" : "#475569", padding: 10, borderRadius: 10, marginBottom: 12, fontSize: 12 }}>{dataError ? `Caretaker records unavailable: ${dataError}` : `Records updated ${timeAgo(lastDataUpdate)}.`}</div>}
        {pendingAcks > 0 && (
          <div
            style={{
              background: "#fef3c7",
              color: "#92400e",
              border: "1px solid #fcd34d",
              borderRadius: 12,
              padding: "10px 14px",
              marginBottom: 16,
              fontSize: 12,
              fontWeight: 600,
            }}
          >
            ⏳ {pendingAcks} acknowledgement{pendingAcks > 1 ? "s" : ""} queued
            offline — they'll sync automatically when the edge server is reachable.
          </div>
        )}

        {/* Danger SOS banner */}
        {isDanger && (
          <div
            style={{
              background:
                "linear-gradient(135deg, #dc2626 0%, #991b1b 100%)",
              borderRadius: 16,
              padding: "24px 20px",
              marginBottom: 20,
              textAlign: "center",
              boxShadow: "0 8px 32px rgba(220,38,38,0.3)",
              animation: "pulse-bg 2s infinite",
            }}
          >
            <div
              style={{
                fontSize: 48,
                fontWeight: 900,
                color: "#fff",
                marginBottom: 4,
                letterSpacing: 2,
              }}
            >
              SOS
            </div>
            <div
              style={{
                fontSize: 16,
                fontWeight: 700,
                color: "#fecaca",
                textTransform: "uppercase",
                letterSpacing: 1,
              }}
            >
              {status?.state === "alerting"
                ? "ALERTING — CHECKING ON PATIENT"
                : "AWAITING RESPONSE"}
            </div>
            <div
              style={{
                fontSize: 13,
                color: "#fca5a5",
                marginTop: 6,
              }}
            >
              {activeAlerts.length > 0
                ? TRIGGER_LABELS[activeAlerts[0].trigger_name] ||
                  activeAlerts[0].trigger_name
                : "Risk detected"}
            </div>
          </div>
        )}

        {/* Status card with gauge + SOS */}
        <div
          style={{
            background: "#fff",
            borderRadius: 16,
            boxShadow: "0 4px 24px rgba(0,0,0,0.08)",
            padding: "24px 20px",
            marginBottom: 20,
            display: "flex",
            alignItems: "center",
            gap: 20,
          }}
        >
          <RiskGauge
            score={status?.risk_score || 0}
            band={status?.band || "normal"}
            state={status?.state || "resolved"}
          />
          <div style={{ flex: 1 }}>
            <div
              style={{
                fontSize: 18,
                fontWeight: 800,
                color: "#1a3760",
                marginBottom: 4,
              }}
            >
              Patient Status
            </div>
            <div
              style={{
                fontSize: 13,
                color: "#64748b",
                marginBottom: 8,
              }}
            >
              State:{" "}
              <span
                style={{
                  color: isDanger ? "#dc2626" : "#22c55e",
                  fontWeight: 700,
                }}
              >
                {status?.state || "resolved"}
              </span>
            </div>
            {status?.active_flags ? (
              <div
                style={{
                  display: "flex",
                  gap: 6,
                  flexWrap: "wrap",
                }}
              >
                {status.active_flags
                  .split(",")
                  .filter(Boolean)
                  .map((f) => (
                    <span
                      key={f}
                      style={{
                        background: "#fef2f2",
                        color: "#dc2626",
                        borderRadius: 8,
                        padding: "4px 10px",
                        fontSize: 11,
                        fontWeight: 700,
                        border: "1px solid #fecaca",
                      }}
                    >
                      {TRIGGER_LABELS[f] || f}
                    </span>
                  ))}
              </div>
            ) : (
              <span
                style={{
                  fontSize: 12,
                  color: "#22c55e",
                  fontWeight: 600,
                }}
              >
                ✓ All clear
              </span>
            )}
          </div>
          <SOSButton isDanger={isDanger} />
        </div>

        {status?.devices && <section style={{ background: "#fff", borderRadius: 16, padding: 16, marginBottom: 20, boxShadow: "0 4px 24px rgba(0,0,0,0.06)" }}>
          <h2 style={{ margin: "0 0 10px", fontSize: 15, color: "#1a3760" }}>Sensor health</h2>
          {!status.devices.length ? <p style={{ margin: 0, color: "#64748b", fontSize: 12 }}>Device registry is unavailable.</p> : status.devices.map((device) => <div key={device.device_id} style={{ display: "flex", justifyContent: "space-between", gap: 10, padding: "5px 0", fontSize: 12, color: "#475569" }}><span>{device.name || device.device_id} ({device.device_id})</span><span style={{ color: device.status === "online" ? "#15803d" : "#b91c1c", fontWeight: 700 }}>{device.status}{device.age_seconds != null ? ` · ${device.age_seconds}s` : " · no telemetry"}</span></div>)}
          {status.last_risk_update > 0 && Date.now() / 1000 - status.last_risk_update > 90 && <p style={{ margin: "8px 0 0", color: "#b45309", fontSize: 12 }}>Risk snapshot is stale ({timeAgo(status.last_risk_update)}). Missing telemetry is not evidence that the patient is safe.</p>}
        </section>}

        {/* Location Map */}
        <LocationMap isDanger={isDanger} />

        {/* Shared AI conversation */}
        <section aria-label="AI voice conversation" style={{ background: "#fff", borderRadius: 16, padding: 18, marginBottom: 20, boxShadow: "0 4px 24px rgba(0,0,0,0.08)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
            <h2 style={{ margin: 0, fontSize: 16, color: "#1a3760" }}>AI Voice Assistant</h2>
            <span style={{ fontSize: 11, color: "#64748b" }}>Shared chat · live</span>
          </div>
          <div aria-live="polite" style={{ display: "grid", gap: 8, maxHeight: 260, overflowY: "auto", marginTop: 12 }}>
            {!voiceMessages.length && <p style={{ margin: 0, color: "#64748b", fontSize: 13 }}>No shared conversations yet. Voice chat messages from the dashboard will appear here.</p>}
            {voiceMessages.map((message) => <article key={message.id} style={{ padding: "9px 11px", borderRadius: 10, background: message.role === "assistant" ? "#eff6ff" : "#ecfdf5", color: "#1e293b" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 11, color: "#475569", fontWeight: 700 }}><span>{message.role === "assistant" ? "AI assistant" : "User"}</span><time>{new Date(message.ts * 1000).toLocaleTimeString()}</time></div>
              <p style={{ margin: "5px 0 0", fontSize: 13, whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{message.text}</p>
            </article>)}
          </div>
        </section>

        {/* Emergencies — durable local source of truth */}
        {activeEmergencies.length > 0 && (
          <div style={{ marginBottom: 20 }}>
            <div
              style={{
                fontSize: 14,
                fontWeight: 800,
                color: "#dc2626",
                textTransform: "uppercase",
                letterSpacing: 1,
                marginBottom: 12,
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <StatusDot color="#dc2626" pulse />
              Active Emergencies ({activeEmergencies.length})
            </div>
            {activeEmergencies.map((e) => (
              <EmergencyCard key={e.event_id} event={e} onAck={handleEmergencyAck} onResolve={handleEmergencyResolve} />
            ))}
          </div>
        )}

        {recentEmergencies.length > 0 && <section style={{ marginBottom: 20 }}>
          <h2 style={{ fontSize: 14, color: "#64748b", textTransform: "uppercase" }}>Recent incidents</h2>
          {recentEmergencies.map((e) => <EmergencyCard key={e.event_id} event={e} onAck={handleEmergencyAck} onResolve={handleEmergencyResolve} />)}
        </section>}

        {/* Active alerts */}
        {activeAlerts.length > 0 && (
          <div style={{ marginBottom: 20 }}>
            <div
              style={{
                fontSize: 14,
                fontWeight: 800,
                color: "#dc2626",
                textTransform: "uppercase",
                letterSpacing: 1,
                marginBottom: 12,
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <StatusDot color="#dc2626" pulse />
              Active Alerts ({activeAlerts.length})
            </div>
            {activeAlerts.map((a) => (
              <AlertCard key={a.id} alert={a} onAck={handleAck} />
            ))}
          </div>
        )}

        {/* Alert history */}
        <div>
          <div
            style={{
              fontSize: 14,
              fontWeight: 800,
              color: "#64748b",
              textTransform: "uppercase",
              letterSpacing: 1,
              marginBottom: 12,
            }}
          >
            Alert History
          </div>
          {dataError ? <div style={{ color: "#b91c1c", padding: 20 }}>Alert history is unavailable until the API connection recovers.</div> : alerts.length === 0 ? (
            <div
              style={{
                background: "#fff",
                borderRadius: 16,
                padding: 32,
                textAlign: "center",
                color: "#94a3b8",
                fontSize: 14,
                boxShadow: "0 4px 16px rgba(0,0,0,0.06)",
              }}
            >
              <div style={{ fontSize: 36, marginBottom: 8 }}>✅</div>
              No alerts yet — system monitoring normally
            </div>
          ) : (
            alerts.map((a) => (
              <AlertCard key={a.id} alert={a} onAck={handleAck} />
            ))
          )}
        </div>
      </div>

      {/* Global styles */}
      <style>{`
        @keyframes pulse-bg {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.92; }
        }
        @keyframes pulse-dot {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.3); opacity: 0.7; }
        }
        @keyframes pulse-marker {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.4); }
        }
      `}</style>
    </div>
  );
}
