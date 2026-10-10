/*
 * SafeBathApp.jsx — AI / Module Dashboard (Mock + Live modes)
 *
 * Single-file React component, no external UI dependencies. Drop into any React app:
 *   import SafeBathApp from "./SafeBathApp";   export default function Page(){return <SafeBathApp/>}
 * or render it directly inside a Vite/CRA entry point.
 *
 * Live mode polls http://localhost:8000/api/latest every ~3s (backend/api_server.py).
 * Response shape is documented in that file; extra keys are additive.
 *
 * Board-1 card implements Master Guide Section 2.4 visualizations #1-#3:
 *   1. live 16x4 Velostat heatmap (blue -> red)
 *   2. numeric ADC overlay toggle (calibration sessions)
 *   3. center-of-pressure trail (the sway trajectory ConvLSTM learns from)
 */
import React, { useEffect, useMemo, useRef, useState } from "react";

/* API base is configurable so the same static bundle works on the Pi, a phone
 * on the LAN, or a hosted dashboard. Override with either of:
 *   window.SAFEBATH_API_BASE = "http://192.168.1.20:8000"   (set before load)
 *   <meta name="safebath-api-base" content="http://192.168.1.20:8000">
 * Defaults to the local edge server. */
function resolveApiBase() {
  if (typeof window !== "undefined") {
    if (window.SAFEBATH_API_BASE) return String(window.SAFEBATH_API_BASE).replace(/\/$/, "");
    const m = document.querySelector('meta[name="safebath-api-base"]');
    if (m && m.content) return m.content.replace(/\/$/, "");
  }
  return "http://localhost:8000";
}
const API_BASE = resolveApiBase();
const LIVE_URL = `${API_BASE}/api/latest`;
const MAT_ROWS = 16;
const MAT_COLS = 4;
const POLL_MS = 3000;

/* ----------------------------- helpers ---------------------------------- */

function parseJsonSafe(s) {
  if (s && typeof s === "object") return s;
  if (!s || typeof s !== "string") return null;
  const cleaned = s.replace(/^\[mock-seed\]\s*/, "");
  try { return JSON.parse(cleaned); } catch { return null; }
}
function parseCsv64(s) {
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
  if (parts.length !== 64 || parts.some(Number.isNaN)) return null;
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

/* center of pressure over a 16x4 row-major frame */
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
  const values = new Array(64).fill(0);
  const jitter = mode === "fall" ? 3.5 : mode === "unsteady" ? 1.8 : 0.35;
  const rc = 7.5 + jitter * Math.sin(t * (mode === "unsteady" ? 0.9 : 0.25)) +
    (mode === "fall" ? clamp((t % 60) / 12, 0, 3.2) : 0);
  const cc = 1.5 + 0.6 * Math.sin(t * 0.18);
  const peak = mode === "fall" ? 3400 : 2600;
  for (let r = 0; r < MAT_ROWS; r++)
    for (let c = 0; c < MAT_COLS; c++) {
      const g =
        Math.exp(-(((r - rc) ** 2) / 4 + ((c - cc) ** 2) / 1.4)) * peak;
      values[r * MAT_COLS + c] = Math.round(clamp(g + 120 + Math.random() * 60, 0, 4095));
    }
  return values;
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
    radar: {
      presence: true,
      motion: !collapse && Math.random() > 0.25,
      stillness_ms: collapse ? Math.round((t % 20) * 1000) : 0,
      possible_collapse: collapse,
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

/* Board 1: heatmap + CoP trail + numeric overlay */
function MatCard({ frameValues, recentFrames }) {
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
    <Card title="Board 1 — Pressure Mat" subtitle="sensor/mat/frame · 16×4 · ConvLSTM fall-risk"
      danger={false}>
      <canvas ref={canvasRef} width={MAT_COLS * cellW} height={MAT_ROWS * cellH}
        style={{ width: "100%", imageRendering: "pixelated", borderRadius: 8, background: "#0b1220" }} />
      <label style={{ fontSize: 11, color: "#94a3b8", display: "inline-flex", gap: 6, alignItems: "center", marginTop: 8 }}>
        <input type="checkbox" checked={overlay} onChange={(e) => setOverlay(e.target.checked)} />
        raw ADC overlay (calibration)
      </label>
    </Card>
  );
}

/* Board 2: radar scope — sweep + presence blip + stillness ring toward 120s.
   The C4001 exposes booleans only (no spatial returns), so this is a status
   scope, not a map: blip = presence, green/fast = motion, ring = stillness. */
function RadarCard({ radar }) {
  const canvasRef = useRef(null);
  const stateRef = useRef(radar);
  stateRef.current = radar;

  useEffect(() => {
    let raf;
    const draw = () => {
      const cv = canvasRef.current;
      if (cv) {
        const ctx = cv.getContext("2d");
        const W = cv.width, H = cv.height;
        const cx = W / 2, cy = H / 2, R = Math.min(W, H) / 2 - 8;
        const st = stateRef.current || {};
        const t = performance.now() / 1000;

        ctx.clearRect(0, 0, W, H);

        /* disc + graticule */
        ctx.fillStyle = "#0b1220";
        ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = "#1e293b"; ctx.lineWidth = 1;
        for (const f of [1 / 3, 2 / 3, 1]) {
          ctx.beginPath(); ctx.arc(cx, cy, R * f, 0, Math.PI * 2); ctx.stroke();
        }
        ctx.beginPath();
        ctx.moveTo(cx - R, cy); ctx.lineTo(cx + R, cy);
        ctx.moveTo(cx, cy - R); ctx.lineTo(cx, cy + R);
        ctx.stroke();

        /* rotating sweep with fading tail */
        const ang = (t * 1.4) % (Math.PI * 2);
        for (let i = 0; i < 26; i++) {
          const a = ang - i * 0.035;
          ctx.strokeStyle = `rgba(56,189,248,${0.4 * (1 - i / 26)})`;
          ctx.lineWidth = 2;
          ctx.beginPath(); ctx.moveTo(cx, cy);
          ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
          ctx.stroke();
        }

        /* stillness progress ring — fills toward the 120 s collapse rule */
        const frac = clamp((Number(st.stillness_ms) || 0) / 120000, 0, 1);
        if (frac > 0) {
          ctx.lineWidth = 5;
          ctx.strokeStyle = frac > 0.8 ? "#ef4444" : "#f59e0b";
          ctx.beginPath();
          ctx.arc(cx, cy, R - 3, -Math.PI / 2, -Math.PI / 2 + frac * Math.PI * 2);
          ctx.stroke();
        }

        /* presence blip — lights up as the sweep passes; drifts slowly */
        if (st.presence && !st.possible_collapse) {
          const blipA = -Math.PI / 3 + Math.sin(t * 0.11) * 0.9;
          const rel = (((ang - blipA) % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2);
          if (rel < 1.4) {
            const rr = st.motion ? R * 0.72 : R * 0.42;
            const alpha = 1 - rel / 1.4;
            ctx.fillStyle = st.motion
              ? `rgba(34,197,94,${alpha})`
              : `rgba(250,204,21,${alpha})`;
            ctx.beginPath();
            ctx.arc(cx + Math.cos(blipA) * rr, cy + Math.sin(blipA) * rr,
              st.motion ? 6 : 4, 0, Math.PI * 2);
            ctx.fill();
          }
        }

        /* sensor dot */
        ctx.fillStyle = "#38bdf8";
        ctx.beginPath(); ctx.arc(cx, cy, 3, 0, Math.PI * 2); ctx.fill();

        /* collapse pulse overlay */
        if (st.possible_collapse) {
          const p = (Math.sin(t * 5) + 1) / 2;
          ctx.fillStyle = `rgba(239,68,68,${0.10 + 0.20 * p})`;
          ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();
          ctx.fillStyle = "#fecaca";
          ctx.font = "bold 11px monospace"; ctx.textAlign = "center";
          ctx.fillText("POSSIBLE COLLAPSE", cx, cy + R - 14);
        }
      }
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <Card title="Board 2 — Radar C4001" subtitle="presence / motion / stillness rule (>120s)"
      danger={!!radar?.possible_collapse}>
      <canvas ref={canvasRef} width={260} height={170}
        style={{ width: "100%", borderRadius: 8, background: "#0b1220" }} />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "10px 0" }}>
        <Pill on={!!radar?.presence} labelOn="PRESENCE" labelOff="empty" />
        <Pill on={!!radar?.motion} labelOn="MOVING" labelOff="still" />
        <Pill on={!!radar?.possible_collapse} labelOn="⚠ COLLAPSE" labelOff="no flag" />
      </div>
      <Bar label="stillness → 120 s trigger" value={radar?.stillness_ms ?? 0} max={180000} unit=" ms" />
    </Card>
  );
}

function Bar({ label, value, max, unit }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#cbd5e1" }}>
        <span>{label}</span><span>{fmt(value, 1)}{unit}</span>
      </div>
      <div style={{ height: 6, background: "#1e293b", borderRadius: 4 }}>
        <div style={{ width: `${clamp((value / max) * 100, 0, 100)}%`, height: 6, borderRadius: 4, background: "#38bdf8" }} />
      </div>
    </div>
  );
}

const Pill = ({ on, labelOn, labelOff }) => (
  <span style={{
    fontSize: 11, padding: "3px 10px", borderRadius: 999,
    background: on ? "#7f1d1d" : "#14532d", color: on ? "#fecaca" : "#bbf7d0",
    fontWeight: 600,
  }}>
    {on ? labelOn : labelOff}
  </span>
);

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

  const source = snap.source || (mode === "live" ? "real" : "simulated");
  const simulated = source !== "real";
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
  const [snap, setSnap] = useState(() => makeMockSnapshot());
  const [log, setLog] = useState([]);
  const [history, setHistory] = useState([]);

  /* Live polling */
  useEffect(() => {
    if (mode !== "live") return undefined;
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch(LIVE_URL, { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (!alive) return;
        setLiveStatus(true);
        setSnap({ ...data, _mock: false, ts: Date.now() / 1000 });
        setHistory(data.risk_history?.map((h) => h.score) ?? []);
      } catch {
        if (alive) setLiveStatus(false);
      }
    };
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, [mode]);

  /* Mock ticking */
  useEffect(() => {
    if (mode !== "mock") return undefined;
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
    if (mode === "live") return parseCsv64(snap.mat_frame?.values);
    return snap.mat_frame.values;
  }, [mode, snap]);
  const recentFrames = useMemo(() => {
    if (mode === "live")
      return (snap.mat_frames_recent || []).map(parseCsv64).filter(Boolean);
    return [matValues].filter(Boolean);
  }, [mode, snap, matValues]);

  const radar = mode === "live" ? parseJsonSafe(snap.modules?.radar?.value) : snap.radar;
  const toilet = mode === "live" ? parseJsonSafe(snap.modules?.toilet?.value) : snap.toilet;
  const audio = mode === "live" ? parseJsonSafe(snap.modules?.audio?.value) : snap.audio;
  const tof = mode === "live" ? parseJsonSafe(snap.modules?.tof?.value) : snap.tof;

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
        <MatCard frameValues={matValues} recentFrames={recentFrames} />

        <RadarCard radar={radar} />

        <Card title="Board 3 — Toilet Biomarkers" subtitle="MQ-135 · MQ-3 · TCS3200 → Random Forest" danger={false}>
          <Bar label="gas (MQ-135)" value={toilet?.gas_ppm ?? 0} max={200} unit=" ppm" />
          <Bar label="voc (MQ-3)" value={toilet?.voc_ppm ?? 0} max={120} unit=" ppm" />
          <Bar label="turbidity proxy" value={toilet?.turbidity ?? 0} max={10} unit="" />
          <div style={{ fontSize: 11, color: "#64748b", marginTop: 6 }}>hour_of_day: {toilet?.hour_of_day ?? "—"} · Medium severity only</div>
        </Card>

        <Card title="Board 4 — Acoustic Distress" subtitle="MAX4466 → RMS/ZCR/centroid classifier" danger={audio?.label === "impact_thud" || audio?.label === "speech_or_cry"}>
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
    </div>
  );
}
