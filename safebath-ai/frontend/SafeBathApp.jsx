/*
 * SafeBathApp.jsx — AI / Module Dashboard (Mock + Live modes)
 *
 * Single-file React component, no external UI dependencies. Drop into any React app:
 *   import SafeBathApp from "./SafeBathApp";   export default function Page(){return <SafeBathApp/>}
 * or render it directly inside a Vite/CRA entry point.
 *
 * Live mode polls <API_BASE>/api/latest (~3s); API_BASE comes from the shared
 * apiBase.js resolver (backend/api_server.py). Overrides: window.SAFEBATH_API_BASE,
 * <meta name="safebath-api-base">, or ?api=... query param.
 * Response shape is documented in that file; extra keys are additive.
 *
 * Board-1 is the 2x2 FOUR-ZONE Velostat mat and renders the four-zone card in
 * both LIVE and DEMO modes (raw ADC per zone + optional normalized %). The old
 * 12x6 grid heatmap / CoP trail is a LEGACY compatibility view only, reachable
 * behind the explicit "legacy 12×6" toggle, and is never shown by default.
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import { healthFailureMessage, resolveApiBase, resolveApiInfo } from "./apiBase.mjs";
import { MatZonesCard, matModeFromSnapshot, snapshotIsSimulated } from "./matZones.mjs";
import VoiceTestApp from "./VoiceTestApp.jsx";

const API_BASE = resolveApiBase();
const API_INFO = resolveApiInfo();
const LIVE_URL = `${API_BASE}/api/latest`;
const MAT_ROWS = 12;
const MAT_COLS = 6;
const POLL_MS = 3000;

/* ----------------------------- helpers ---------------------------------- */

function parseJsonSafe(s) {
  if (s && typeof s === "object") return s;
  if (!s || typeof s !== "string") return null;
  const cleaned = s.replace(/^\[mock-seed\]\s*/, "");
  try { return JSON.parse(cleaned); } catch { return null; }
}
function parseCsv(s) {
  if (!s) return null;
  let parts;
  if (Array.isArray(s)) {
    // live API delivers mat values as an int array
    parts = s.map((x) => parseInt(x, 10));
  } else {
    if (typeof s !== "string") return null;
    const cleaned = s.replace(/^\[mock-seed\]\s*/, "").trim();
    parts = cleaned.split(",").map((x) => parseInt(x, 10));
  }
  if (parts.length !== MAT_ROWS * MAT_COLS || parts.some(Number.isNaN)) return null;
  return parts;
}
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const fmt = (v, d = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));

/* heat color: value 0..4095 -> blue .. purple .. red */
function heatColor(v01) {
  const t = clamp(v01, 0, 1);
  const stops = [
    [30, 58, 138], [37, 99, 235], [124, 58, 237], [220, 38, 38], [248, 113, 113],
  ];
  const seg = t * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(seg));
  const f = seg - i;
  const c = stops[i].map((x, k) => Math.round(x + f * (stops[i + 1][k] - x)));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

/* center of pressure over a 12x6 row-major frame */
function centerOfPressure(values) {
  let sw = 0, sr = 0, sc = 0;
  for (let r = 0; r < MAT_ROWS; r++) {
    for (let c = 0; c < MAT_COLS; c++) {
      const w = values[r * MAT_COLS + c];
      sw += w; sr += w * r; sc += w * c;
    }
  }
  if (sw <= 0) return null;
  return { row: sr / sw, col: sc / sw };
}

/* --------------------------- mock generator ------------------------------ */

function mockFrame(t, mode) {
  const values = new Array(MAT_ROWS * MAT_COLS).fill(0);
  const jitter = mode === "fall" ? 3.5 : mode === "unsteady" ? 1.8 : 0.35;
  const rc = 5.5 + jitter * Math.sin(t * (mode === "unsteady" ? 0.9 : 0.25)) +
    (mode === "fall" ? clamp((t % 60) / 12, 0, 2.8) : 0);
  const cc = 2.5 + 0.6 * Math.sin(t * 0.18);
  const peak = mode === "fall" ? 3400 : 2600;
  for (let r = 0; r < MAT_ROWS; r++)
    for (let c = 0; c < MAT_COLS; c++) {
      const g =
        Math.exp(-(((r - rc) ** 2) / 4 + ((c - cc) ** 2) / 1.4)) * peak;
      values[r * MAT_COLS + c] = Math.round(clamp(g + 120 + Math.random() * 60, 0, 4095));
    }
  return values;
}

/* Four sample zones for the demo (2x2). Purely simulated, same phase as the
 * grid so both views tell the same story. Calibration below is demo-only. */
function mockZones(t, mode) {
  const peak = mode === "fall" ? 3200 : mode === "unsteady" ? 1500 : 900;
  const focus = Math.floor(t / 20) % 4;             // which zone is loaded now
  return [0, 1, 2, 3].map((i) => Math.round(clamp(
    120 + (i === focus ? peak : peak * 0.15) + 45 * Math.sin(t * 0.6 + i),
    0, 4095)));
}

const MOCK_ZONE_CALIBRATION = {
  polarity: "increase",
  zones: [
    { baseline: 120, max: 3000 },
    { baseline: 120, max: 3000 },
    { baseline: 120, max: 3000 },
    { baseline: 120, max: 3000 },
  ],
};

/* Live mode must NEVER display simulated values. Start from an explicitly
 * empty, non-mock snapshot and only ever fill it from real API responses; if
 * the API is unreachable the cards show their unavailable state rather than
 * quietly reusing the demo generator. */
function makeEmptyLiveSnapshot() {
  return {
    _mock: false,
    source: "real",
    modules: {},
    mat_mode: null,
    mat_zones: null,
    mat_zone_recent: [],
    mat_frame: null,
    mat_frames_recent: [],
    events: [],
    risk_score: 0,
    band: "normal",
    alert_state: "resolved",
    risk_history: [],
    radarDevice: null,
  };
}

function makeMockSnapshot() {
  const t = Date.now() / 1000;
  const phase = Math.floor(t / 20) % 4;             // cycles normal/unsteady/fall/water
  const mode = ["normal", "unsteady", "fall", "normal"][phase];
  const frame = mockFrame(t, phase === 3 && t % 20 < 10 ? "water" : mode);
  const collapse = mode === "fall" && t % 20 > 14;
  return {
    ts: t,
    mat_frame: { values: frame, rows: MAT_ROWS, cols: MAT_COLS },
    /* prototype mat is now the 2x2 four-zone layout; grid stays available */
    mat_mode: "four_zone",
    mat_zones: {
      sensor_type: "pressure_4zone",
      zones: mockZones(t, collapse ? "fall" : mode),
      count: 4,
      ts: t,
      calibration: MOCK_ZONE_CALIBRATION,
    },
    mat_zone_recent: [],
    radar: {
      presence: true,
      motion: !collapse && Math.random() > 0.25,
      stillness_ms: collapse ? Math.round((t % 20) * 1000) : 0,
      possible_collapse: collapse,
      _ts: t,
    },
    radarDevice: {
      device_id: "radar-01",
      module: "radar",
      name: "Presence Radar",
      status: "online",
      last_seen: t,
      age_seconds: 0,
      sensor_status: "ok",
      simulated: true,
      registered: true,
    },
    toilet: {
      gas_ppm: 55 + Math.random() * 15,
      voc_ppm: 22 + Math.random() * 10,
      turbidity: 1.8 + Math.random() * 1.2,
      hour_of_day: new Date().getHours(),
    },
    audio: {
      label: collapse ? "impact_thud" : "silence",
      energy: collapse ? 0.62 : 0.008 + Math.random() * 0.01,
    },
    tof: {
      avg_thermal_c: 26 + Math.random(),
      flow_pulses: 0,
      tof_collapse: collapse,
      unattended_hot_fill: false,
    },
    risk_score: collapse ? 68 : mode === "unsteady" ? 34 : 4 + Math.random() * 6,
    band: collapse ? "high" : mode === "unsteady" ? "watch" : "normal",
    alert_state: collapse ? "voice_pending" : "resolved",
    events: [],
    _mock: true,
  };
}

/* ------------------------------- cards ----------------------------------- */

function Card({ title, subtitle, children, danger }) {
  return (
    <div style={{
      background: "#111827", borderRadius: 14, padding: 16,
      border: `1px solid ${danger ? "#ef4444" : "#1f2937"}`,
      boxShadow: danger ? "0 0 14px rgba(239,68,68,.35)" : "none",
    }}>
      <div style={{ fontSize: 13, letterSpacing: 1, color: "#94a3b8", textTransform: "uppercase" }}>{title}</div>
      <div style={{ fontSize: 11, color: "#64748b", marginBottom: 10 }}>{subtitle}</div>
      {children}
    </div>
  );
}

const IconWifi = ({ ok }) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={ok ? "#22c55e" : "#ef4444"} strokeWidth="2">
    {ok ? (
      <>
        <path d="M5 12a10 10 0 0 1 14 0" /><path d="M8.5 15.5a5 5 0 0 1 7 0" />
        <circle cx="12" cy="19" r="1" fill="#22c55e" />
      </>
    ) : (
      <>
        <path d="M5 12a10 10 0 0 1 14 0" opacity=".35" /><path d="M8.5 15.5a5 5 0 0 1 7 0" opacity=".35" />
        <line x1="4" y1="4" x2="20" y2="20" />
      </>
    )}
  </svg>
);

/* Manual override so the 2x2 layout can be inspected without waiting for
 * four-zone telemetry to arrive (useful during bring-up / review). */
function MatViewToggle({ value, onChange }) {
  const btn = (v, label) => (
    <button key={v} onClick={() => onChange(v)} style={{
      fontSize: 11, padding: "3px 8px", borderRadius: 6, cursor: "pointer",
      border: `1px solid ${value === v ? "#38bdf8" : "#334155"}`,
      background: value === v ? "#0c4a6e" : "#0b1220",
      color: value === v ? "#e0f2fe" : "#94a3b8",
    }}>{label}</button>
  );
  return (
    <div style={{ display: "flex", gap: 6, marginBottom: 10, alignItems: "center" }}>
      <span style={{ fontSize: 11, color: "#64748b" }}>view:</span>
      {btn("four_zone", "4-zone")}
      {btn("grid", "legacy 12×6")}
      {btn("auto", "auto")}
    </div>
  );
}

/* Board 1: heatmap + CoP trail + numeric overlay */
function MatCard({ frameValues, recentFrames, headerExtra }) {
  const canvasRef = useRef(null);
  const [overlay, setOverlay] = useState(false);
  const cellW = 44, cellH = 26;

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    ctx.clearRect(0, 0, cv.width, cv.height);

    if (!frameValues) {
      ctx.fillStyle = "#334155";
      ctx.font = "13px monospace";
      ctx.fillText("no mat frames yet", 10, 30);
      return;
    }

    for (let r = 0; r < MAT_ROWS; r++)
      for (let c = 0; c < MAT_COLS; c++) {
        const v = frameValues[r * MAT_COLS + c] ?? 0;
        ctx.fillStyle = heatColor(v / 4095);
        ctx.fillRect(c * cellW + 2, r * cellH + 2, cellW - 4, cellH - 4);
        if (overlay) {
          ctx.fillStyle = v > 2000 ? "#fff" : "#cbd5e1";
          ctx.font = "9px monospace";
          ctx.fillText(String(v), c * cellW + 6, r * cellH + 16);
        }
      }

    // center-of-pressure trail over recent frames (visualization #3)
    const pts = (recentFrames || []).map(centerOfPressure).filter(Boolean);
    if (pts.length) {
      ctx.lineWidth = 2;
      for (let i = 1; i < pts.length; i++) {
        ctx.strokeStyle = `rgba(250,204,21,${0.15 + (i / pts.length) * 0.85})`;
        ctx.beginPath();
        ctx.moveTo(pts[i - 1].col * cellW + cellW / 2, pts[i - 1].row * cellH + cellH / 2);
        ctx.lineTo(pts[i].col * cellW + cellW / 2, pts[i].row * cellH + cellH / 2);
        ctx.stroke();
      }
      const last = pts[pts.length - 1];
      ctx.fillStyle = "#facc15";
      ctx.beginPath();
      ctx.arc(last.col * cellW + cellW / 2, last.row * cellH + cellH / 2, 4, 0, Math.PI * 2);
      ctx.fill();
    }
  }, [frameValues, recentFrames, overlay]);

  return (
    <Card title="Board 1 — Pressure Mat (LEGACY)" subtitle="sensor/mat/frame · 12×6 grid · compatibility view only"
      danger={false}>
      {headerExtra}
      <canvas ref={canvasRef} width={MAT_COLS * cellW} height={MAT_ROWS * cellH}
        style={{ width: "100%", imageRendering: "pixelated", borderRadius: 8, background: "#0b1220" }} />
      <label style={{ fontSize: 11, color: "#94a3b8", display: "inline-flex", gap: 6, alignItems: "center", marginTop: 8 }}>
        <input type="checkbox" checked={overlay} onChange={(e) => setOverlay(e.target.checked)} />
        raw ADC overlay (calibration)
      </label>
    </Card>
  );
}

/*
 * C4001 Radar Monitor — top-down sector visualization + data cards.
 *
 * The previous 360° sweep scope was physically misleading: the DFRobot C4001 is a
 * presence/motion mmWave sensor with a 100° horizontal field of view, not an
 * omnidirectional scanner. This replaced the 360° disc with a top-down plan-view
 * sector whose total horizontal angle is exactly 100° (-50° .. +50° relative to
 * the forward axis, which by default points toward the pressure mat).
 *
 * What the current firmware/telemetry contract actually provides (schemas.py
 * RadarStatusPayload): presence, motion, stillness_ms, possible_collapse.
 *
 * What it does NOT provide (shown as "Not provided", never fabricated):
 *   distance_m, target_count, signal/confidence, point cloud, target coords.
 *
 * The drawing shows the specified beam angle as a nominal FOV — not a guaranteed
 * detection boundary. No live targets, no sweep animation, no fabricated distances
 * are drawn. The vertical FOV (80° or 40°) is reported as a separate nominal spec
 * and is never drawn as horizontal coverage.
 */
import { RadarCard as RadarCardImpl } from "./radarCard.mjs";
import { radarModelConfirmed } from "./radarFieldsOfView.mjs";

const RadarCard = RadarCardImpl;

const Pill = ({ on, labelOn, labelOff }) => (
  <span style={{
    fontSize: 11, padding: "3px 10px", borderRadius: 999,
    background: on ? "#7f1d1d" : "#14532d", color: on ? "#fecaca" : "#bbf7d0",
    fontWeight: 600,
  }}>
    {on ? labelOn : labelOff}
  </span>
);

function Bar({ label, value, max, unit }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#cbd5e1" }}>
        <span>{label}</span><span>{fmt(value, 1)}{unit}</span>
      </div>
      <div style={{ height: 6, background: "#1e293b", borderRadius: 4 }}>
        <div style={{ width: `${clamp(((value || 0) / max) * 100, 0, 100)}%`, height: 6, borderRadius: 4, background: "#38bdf8" }} />
      </div>
    </div>
  );
}

/* --------------------------- system truth -------------------------------- */

const Dot = ({ status }) => {
  const color = status === "online" ? "#22c55e"
    : status === "stale" ? "#f59e0b"
    : status === "offline" ? "#64748b" : "#475569";
  return (
    <span style={{
      display: "inline-block", width: 8, height: 8, borderRadius: 999,
      background: color, marginRight: 6, verticalAlign: "middle",
    }} />
  );
};

/* Surfaces the additive truth fields the edge server exposes on /api/latest:
 *   source: "real" | "simulated"
 *   devices / emergencies / local (outbox) / cloud  — see api_server.py
 * Everything degrades gracefully when a field is missing (older backend). */
function SystemTruth({ snap, mode }) {
  const devices = Array.isArray(snap.devices) ? snap.devices : [];
  const counts = (snap.emergencies && snap.emergencies.counts) || {};
  const local = snap.local || {};
  const cloud = snap.cloud || {};
  const outbox = local.outbox || {};
  const pending = (Number(outbox.queued) || 0) + (Number(outbox.failed) || 0) +
    (Number(outbox.in_flight) || 0) + (Number(outbox.pending) || 0);

  const simulated = snapshotIsSimulated(snap, mode);
  const activeEmg = Number(counts.active) || 0;

  let cloudText, cloudColor;
  if (!cloud.configured) { cloudText = "offline-only (cloud not configured)"; cloudColor = "#94a3b8"; }
  else if (cloud.reachable) { cloudText = "cloud synced"; cloudColor = "#22c55e"; }
  else { cloudText = `cloud unreachable · ${pending} queued`; cloudColor = "#f59e0b"; }

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap",
      background: "#0b1220", border: "1px solid #1f2937", borderRadius: 12,
      padding: "10px 16px", marginBottom: 18, fontSize: 12,
    }}>
      <span style={{
        padding: "3px 10px", borderRadius: 999, fontWeight: 700,
        background: simulated ? "#422006" : "#052e16",
        color: simulated ? "#fde68a" : "#bbf7d0",
      }}>
        {simulated ? "SIMULATED DATA" : "LIVE HARDWARE"}
      </span>

      <span style={{ color: cloudColor }}>● {cloudText}</span>

      <span style={{ color: pending > 0 ? "#f59e0b" : "#94a3b8" }}>
        outbox: {pending} pending
      </span>

      <span style={{ color: activeEmg > 0 ? "#ef4444" : "#94a3b8", fontWeight: activeEmg > 0 ? 700 : 400 }}>
        emergencies: {activeEmg} active / {Number(counts.acknowledged) || 0} ack
      </span>

      <span style={{ display: "flex", gap: 14, flexWrap: "wrap", marginLeft: "auto" }}>
        {devices.length === 0
          ? <span style={{ color: "#475569" }}>{simulated ? "no hardware (mock)" : "no devices reported"}</span>
          : devices.map((d) => (
            <span key={d.device_id || d.topic} style={{ color: "#cbd5e1" }}>
              <Dot status={d.status} />
              {d.device_id || d.topic}
              {d.simulated ? " (sim)" : ""}
              {d.age_seconds != null ? ` · ${Math.round(d.age_seconds)}s` : ""}
            </span>
          ))}
      </span>
    </div>
  );
}

/* ------------------------------ main app --------------------------------- */

export default function SafeBathApp() {
  return <DashBoundary><SafeBathAppInner /></DashBoundary>;
}

class DashBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { err: null };
  }
  static getDerivedStateFromError(err) {
    return { err };
  }
  render() {
    if (this.state.err) {
      return (
        <pre style={{ color: "#fca5a5", background: "#020617", padding: 24, fontFamily: "monospace", whiteSpace: "pre-wrap" }}>
          {"Dashboard error (please report):\n"}
          {String(this.state.err && this.state.err.message || this.state.err)}
        </pre>
      );
    }
    return this.props.children;
  }
}

function SafeBathAppInner() {
  const [mode, setMode] = useState("mock");           // "mock" | "live"
  const [liveStatus, setLiveStatus] = useState(false); // true once API reachable
  const [liveError, setLiveError] = useState(null);    // last health failure detail
  const [snap, setSnap] = useState(() => makeMockSnapshot());
  const [log, setLog] = useState([]);
  const [history, setHistory] = useState([]);
  const [matView, setMatView] = useState("auto");   // "auto" | "grid" | "four_zone"
  const [radarConfig, setRadarConfig] = useState(() => loadRadarConfig());

  /* Live polling */
  useEffect(() => {
    if (mode !== "live") return undefined;
    let alive = true;
    setSnap(makeEmptyLiveSnapshot());   // drop demo values the moment LIVE starts
    const poll = async () => {
      try {
        const res = await fetch(LIVE_URL, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!alive) return;
        setLiveStatus(true);
        setLiveError(null);
        setSnap({ ...data, _mock: false, ts: Date.now() / 1000 });
        setHistory(data.risk_history?.map((h) => h.score) ?? []);
      } catch (err) {
        if (alive) {
          setLiveStatus(false);
          setLiveError(healthFailureMessage({
            apiBase: API_BASE,
            pageProtocol: window.location.protocol,
            error: err,
          }));
        }
      }
    };
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, [mode]);

  /* Mock ticking */
  useEffect(() => {
    if (mode !== "mock") return undefined;
    setSnap(makeMockSnapshot());
    const id = setInterval(() => setSnap(makeMockSnapshot()), 1000);
    return () => clearInterval(id);
  }, [mode]);

  /* event log */
  useEffect(() => {
    if (mode !== "mock") return;
    const s = snap.radar?.possible_collapse || snap.tof?.tof_collapse;
    if (s) pushLog(`possible collapse flagged (mock)`, "danger");
  }, [snap]); // eslint-disable-line

  const pushLog = (text, level) =>
    setLog((l) => [{ text, level, time: new Date().toLocaleTimeString() }, ...l].slice(0, 40));

  const matValues = useMemo(() => {
    if (mode === "live") return parseCsv(snap.mat_frame?.values);
    return snap.mat_frame.values;
  }, [mode, snap]);
  const recentFrames = useMemo(() => {
    if (mode === "live")
      return (snap.mat_frames_recent || []).map(parseCsv).filter(Boolean);
    return [matValues].filter(Boolean);
  }, [mode, snap, matValues]);

  const radar = mode === "live" ? parseJsonSafe(snap.modules?.radar?.value) : snap.radar;
  const radarDevice = Array.isArray(snap.devices)
    ? snap.devices.find((d) => d.device_id === "radar-01" || d.module === "radar")
    : null;
  const updateRadarConfig = (updates) => setRadarConfig((current) => {
    const next = { ...current, ...updates };
    try { window.localStorage.setItem("safebath.radar.config", JSON.stringify(next)); } catch {}
    return next;
  });
  const toilet = mode === "live" ? parseJsonSafe(snap.modules?.toilet?.value) : snap.toilet;
  const audio = mode === "live" ? parseJsonSafe(snap.modules?.audio?.value) : snap.audio;
  const tof = mode === "live" ? parseJsonSafe(snap.modules?.tof?.value) : snap.tof;

  /* The physical mat is the 2x2 four-zone design, so that is the default
   * Board-1 view in BOTH modes. `auto` resolves to four-zone; the legacy 12x6
   * grid is shown only when the operator explicitly selects it. A snapshot
   * carrying only legacy 72-cell data raises an explicit notice instead of
   * being drawn as four zones. */
  const detectedMatMode = matModeFromSnapshot(snap);
  const matMode = matView === "grid" ? "grid" : "four_zone";
  const legacyNotice = matMode === "four_zone" && detectedMatMode === "grid";
  const matDevice = Array.isArray(snap.devices)
    ? snap.devices.find((d) => d.device_id === "mat-01" || d.module === "mat")
    : null;
  const matError = mode === "live" && !liveStatus
    ? (liveError || "api-unreachable") : null;
  const matLoading = mode === "live" && !snap.mat_zones && !matError;
  const matToggle = <MatViewToggle value={matView} onChange={setMatView} />;

  /* Radar setup is operator configuration saved in this browser. It is not
   * hardware-reported or physically verified. */
  function loadRadarConfig() {
    try {
      const raw = typeof window !== "undefined"
        ? JSON.parse(window.localStorage.getItem("safebath.radar.config") || "null")
        : null;
      if (!raw || typeof raw !== "object") return defaultRadarConfig();
      const model = typeof raw.model === "string" ? raw.model.trim() : "";
      const y = raw.mountingYaw;
      const tilt = raw.downwardTiltDeg;
      const dist = raw.intendedDistance;
      return {
        model: model || "SEN0609",
        modelConfirmed: radarModelConfirmed(model || "SEN0609"),
        mountingYaw: y != null && Number.isFinite(y) && y >= -180 && y <= 180 ? y : null,
        downwardTiltDeg: tilt != null && Number.isFinite(tilt) && tilt >= 0 && tilt <= 90 ? tilt : null,
        intendedDistance: dist != null && Number.isFinite(dist) && dist >= 0.1 && dist <= 100 ? dist : null,
      };
    } catch {
      return defaultRadarConfig();
    }
  }
  function defaultRadarConfig() {
    return {
      model: "SEN0609",
      modelConfirmed: true,
      mountingYaw: null,
      downwardTiltDeg: null,
      intendedDistance: null,
    };
  }

  const bandColor = snap.band === "high" ? "#ef4444" : snap.band === "watch" ? "#f59e0b" : "#22c55e";

  const liveEvents = mode === "live"
    ? (snap.events || []).map((e) => ({ text: e.text, level: e.level, time: "" }))
    : log;

  return (
    <div style={{ minHeight: "100vh", background: "#020617", color: "#e2e8f0", fontFamily: "Inter, system-ui, sans-serif", padding: 20 }}>
      {/* nav */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 18 }}>
        <div style={{ fontWeight: 800, fontSize: 20 }}>🛁 SafeBath AI</div>
        {["mock", "live"].map((m) => (
          <button key={m} onClick={() => setMode(m)}
            style={{
              padding: "6px 16px", borderRadius: 999, cursor: "pointer", fontWeight: 700,
              border: "1px solid #334155",
              background: mode === m ? (m === "live" ? "#065f46" : "#1e3a8a") : "transparent",
              color: mode === m ? "#fff" : "#94a3b8",
            }}>
            {m.toUpperCase()}
          </button>
        ))}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
          <IconWifi ok={mode === "mock" || liveStatus} />
          <span style={{ color: mode === "mock" ? "#94a3b8" : liveStatus ? "#22c55e" : "#ef4444" }}>
            {mode === "mock" ? "simulated feed" : liveStatus ? "live · api connected" : "offline — api unreachable"}
          </span>
        </div>
      </div>

      {mode === "live" && !liveStatus && (
        <div style={{ fontSize: 11, color: "#fca5a5", background: "#450a0a", border: "1px solid #7f1d1d", borderRadius: 10, padding: "8px 12px", marginBottom: 18, fontFamily: "monospace", lineHeight: 1.5 }}>
          API base: <b>{API_BASE}</b>
          {API_INFO.reason
            ? ` — ${API_INFO.reason}`
            : API_INFO.hint
              ? ` — ${API_INFO.hint}`
              : ` — ${liveError || "connection failed (server not running or unreachable on port 8000)"}`}
        </div>
      )}

      {/* risk banner */}
      <div style={{ display: "flex", alignItems: "center", gap: 20, background: "#0f172a", border: `1px solid ${bandColor}`, borderRadius: 14, padding: "14px 20px", marginBottom: 18 }}>
        <div style={{ fontSize: 42, fontWeight: 800, color: bandColor, minWidth: 90 }}>
          {Math.round(clamp(Number(snap.risk_score) || 0, 0, 100))}
        </div>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700, color: bandColor }}>
            fused risk · {String(snap.band || "normal").toUpperCase()} · state: {String(snap.alert_state || "resolved")}
          </div>
          <div style={{ fontSize: 12, color: "#94a3b8" }}>
            decay 0.9/tick · escalate &gt;60 · watch 30–60 · Voice Bridge gates all High alerts
          </div>
        </div>
        <svg width="160" height="44" style={{ marginLeft: "auto" }}>
          {(history.length ? history.slice(-60) : [Number(snap.risk_score) || 0]).map((v, i, arr) =>
            i === 0 ? null : (
              <line key={i}
                x1={((i - 1) / Math.max(1, arr.length - 1)) * 158}
                y1={43 - clamp(v, 0, 100) * 0.42}
                x2={(i / Math.max(1, arr.length - 1)) * 158}
                y2={43 - clamp(v, 0, 100) * 0.42}
                stroke={bandColor} strokeWidth="2" />
            ))}
        </svg>
      </div>

      <SystemTruth snap={snap} mode={mode} />

      {/* module grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(280px,1fr))", gap: 16 }}>
        {matMode === "four_zone" ? (
          <Card title="Board 1 — Pressure Mat"
            subtitle="sensor/mat/zones · 2×2 four-zone · raw ADC per zone"
            danger={false}>
            {matToggle}
            {legacyNotice && (
              <div style={{
                fontSize: 11, color: "#fde68a", background: "#422006",
                border: "1px solid #78350f", borderRadius: 8,
                padding: "6px 8px", marginBottom: 8, lineHeight: 1.4,
              }}>
                Legacy 12×6 grid frames detected — these are NOT four-zone data,
                so they are not drawn as zones. Switch the view to “legacy 12×6”
                if you need to inspect them.
              </div>
            )}
            <MatZonesCard
              zones={snap.mat_zones?.zones}
              calibration={snap.mat_zones?.calibration}
              matTs={snap.mat_zones?.ts}
              deviceStatus={matDevice?.status}
              error={matError}
              loading={matLoading}
            />
          </Card>
        ) : (
          <MatCard frameValues={matValues} recentFrames={recentFrames}
            headerExtra={matToggle} />
        )}

        <RadarCard
          radar={radar}
          radarDevice={radarDevice}
          radarConfig={radarConfig}
          onRadarConfigChange={updateRadarConfig}
          matZones={snap.mat_zones}
          matDevice={matDevice}
          simulated={mode === "mock" || snap.source === "simulated" || !!radarDevice?.simulated}
          now={mode === "live" ? Date.now() / 1000 : undefined}
        />

        <Card title="Board 3 — Toilet Biomarkers" subtitle="MQ-135 · MQ-3 · TCS3200 → Random Forest" danger={false}>
          <Bar label="gas (MQ-135)" value={toilet?.gas_ppm ?? 0} max={200} unit=" ppm" />
          <Bar label="voc (MQ-3)" value={toilet?.voc_ppm ?? 0} max={120} unit=" ppm" />
          <Bar label="turbidity proxy" value={toilet?.turbidity ?? 0} max={10} unit="" />
          <div style={{ fontSize: 11, color: "#64748b", marginTop: 6 }}>hour_of_day: {toilet?.hour_of_day ?? "—"} · Medium severity only</div>
        </Card>

        <Card title="Board 4 — Acoustic Distress" subtitle="MAX4466 → RMS/ZCR/centroid classifier" danger={audio?.label === "impact_thud" || audio?.label === "cry_or_scream"}>
          <div style={{ fontSize: 22, fontWeight: 800, marginBottom: 8 }}>{audio?.label ?? "—"}</div>
          <Bar label="energy" value={audio?.energy ?? 0} max={1} unit="" />
          <div style={{ fontSize: 11, color: "#64748b" }}>raw audio never leaves the node — labels only</div>
        </Card>

        <Card title="Board 5 — ToF / Thermal / Voice" subtitle="3× VL53L1X · AMG8833 · relay · DFPlayer" danger={tof?.tof_collapse || tof?.unattended_hot_fill}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
            <Pill on={!!tof?.tof_collapse} labelOn="⚠ TOF COLLAPSE" labelOff="tof clear" />
            <Pill on={!!tof?.unattended_hot_fill} labelOn="⚠ HOT FILL UNATTENDED" labelOff="water safe" />
          </div>
          <Bar label="avg thermal" value={tof?.avg_thermal_c ?? 0} max={45} unit=" °C" />
          <Bar label="flow pulses" value={tof?.flow_pulses ?? 0} max={100} unit="" />
        </Card>

        <Card title="Event Log" subtitle="every event, all severities — full traceability">
          <div style={{ maxHeight: 240, overflowY: "auto", display: "grid", gap: 6 }}>
            {liveEvents.length === 0 && <div style={{ fontSize: 12, color: "#64748b" }}>no events yet</div>}
            {liveEvents.map((e, i) => (
              <div key={i} style={{
                fontSize: 11, fontFamily: "monospace", padding: "6px 8px", borderRadius: 6,
                background: e.level === "danger" ? "#450a0a" : e.level === "warn" ? "#422006" : "#0f172a",
                color: e.level === "danger" ? "#fecaca" : e.level === "warn" ? "#fde68a" : "#cbd5e1",
              }}>
                <span style={{ color: "#64748b" }}>{e.time}</span> {e.text}
              </div>
            ))}
          </div>
        </Card>
      </div>
      <section aria-label="AI voice chat" style={{ marginTop: 20, border: "1px solid #334155", borderRadius: 16, padding: "8px 16px", background: "#0f172a" }}>
        <h2 style={{ fontSize: 18, margin: "8px 0 0" }}>AI Voice Assistant</h2>
        <p style={{ fontSize: 12, color: "#94a3b8", margin: "6px 0" }}>Chat by voice or text. Shared messages update the caretaker dashboard live.</p>
        <VoiceTestApp embedded />
      </section>
    </div>
  );
}
