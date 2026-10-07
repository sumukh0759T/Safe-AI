/*
 * CaretakerApp.jsx — Caregiver-facing danger monitor for SafeBath AI
 *
 * UI inspired by the "I'm Safe" women's safety app (Ruan Tech Solutions):
 *   - Clean blue theme (#2D398B, #336AEA)
 *   - Rounded cards with subtle box shadows
 *   - Prominent SOS-style emergency indicators
 *   - Real-time location map (Leaflet + device GPS)
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

const API_BASE = "http://localhost:8000/caretaker";
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

  useEffect(() => {
    if (!navigator.geolocation) {
      setError("Geolocation not supported");
      return;
    }
    const watchId = navigator.geolocation.watchPosition(
      (pos) => {
        setCoords({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
        });
        setError(null);
      },
      (err) => setError(err.message),
      { enableHighAccuracy: true, maximumAge: 10000, timeout: 15000 }
    );
    return () => navigator.geolocation.clearWatch(watchId);
  }, []);

  useEffect(() => {
    if (!mapRef.current || !window.L || mapInstance.current) return;
    const L = window.L;
    const initial = coords || { lat: 12.9716, lng: 77.5946 };
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
  }, []);

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
          Patient Location
        </span>
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

/* ------------------------------------------------------------------ */
/*  Main App                                                           */
/* ------------------------------------------------------------------ */
export default function CaretakerApp() {
  const [status, setStatus] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [connected, setConnected] = useState(false);
  const [lastAlertId, setLastAlertId] = useState(0);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [notificationRequested, setNotificationRequested] = useState(false);
  const { startAlarm, stopAlarm } = useAlarm();
  const prevScoreRef = useRef(0);
  const esRef = useRef(null);

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
        } catch {
          setConnected(false);
        }
        await new Promise((r) => setTimeout(r, POLL_MS));
      }
    }
    poll();
    return () => {
      active = false;
    };
  }, [soundEnabled, startAlarm, lastAlertId]);

  // fetch alert list
  const fetchAlerts = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/alerts?limit=30`);
      if (r.ok) {
        const d = await r.json();
        setAlerts(d.alerts || []);
      }
    } catch {}
  }, []);

  useEffect(() => {
    fetchAlerts();
    const id = setInterval(fetchAlerts, POLL_MS);
    return () => clearInterval(id);
  }, [fetchAlerts]);

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

  const handleAck = useCallback(
    async (id) => {
      try {
        await fetch(`${API_BASE}/ack/${id}`, { method: "POST" });
        setAlerts((prev) =>
          prev.map((a) =>
            a.id === id
              ? {
                  ...a,
                  active: 0,
                  acknowledged_by: "caretaker",
                  acknowledged_at: Date.now() / 1000,
                }
              : a
          )
        );
      } catch {}
    },
    []
  );

  const isDanger =
    status?.state === "alerting" || status?.state === "voice_pending";
  const activeAlerts = alerts.filter((a) => a.active);

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

      {/* Content container */}
      <div style={{ padding: "20px 16px", maxWidth: 480, margin: "0 auto" }}>
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

        {/* Location Map */}
        <LocationMap isDanger={isDanger} />

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
          {alerts.length === 0 ? (
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
