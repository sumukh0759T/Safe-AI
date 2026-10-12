/* radarCard.mjs — C4001 Radar Monitor card for the SafeBath AI dashboard.

Replaced the previous 360° sweep scope with a top-down plan-view sector whose
total horizontal angle is exactly 100° (-50° .. +50° relative to the forward
axis, which by default points toward the pressure mat).

The C4001 exposes booleans only (presence, motion, stillness_ms,
possible_collapse) — no distance, target count, signal/confidence, or spatial
coordinates. Those are shown as "Not provided" and never fabricated.

The vertical FOV (80° for SEN0610, 40° for SEN0609) is reported as a separate
nominal spec and is never drawn as horizontal coverage.
*/
import React from "react";
import { DEFAULT_BANDS, normalizeZone, STALE_MS } from "./matZones.mjs";
import {
  RADAR_MODELS,
  KNOWN_MODELS,
  radarModelConfig,
  radarModelConfirmed,
  fovSectorPath,
  fovBoundaryPaths,
  centreAxisPath,
  forwardAxisAngle,
  rotatePoint,
  theoreticalBeamWidthAtDistance,
  isRadarFieldSupported,
  unsupportedFieldNote,
  RADAR_UNSUPPORTED_FIELDS,
} from "./radarFieldsOfView.mjs";

const h = React.createElement;
const { useRef, useEffect } = React;

/* ---------------------------------------------------------------------------
 * RadarCard — top-down sector + data cards + mat relationship panel.
 * --------------------------------------------------------------------------- */
/* Minimal Card container matching the dashboard Card styling so the radar
 * card can be rendered standalone in tests and in the dashboard without a
 * circular dependency on SafeBathApp.jsx. */
function Card({ title, subtitle, children, danger }) {
  return h(
    "div",
    {
      style: {
        background: "#111827",
        borderRadius: 14,
        padding: 16,
        border: `1px solid ${danger ? "#ef4444" : "#1f2937"}`,
        boxShadow: danger ? "0 0 14px rgba(239,68,68,.35)" : "none",
      },
    },
    [
      h("div", {
        style: { fontSize: 13, letterSpacing: 1, color: "#94a3b8", textTransform: "uppercase" },
      }, title),
      h("div", {
        style: { fontSize: 11, color: "#64748b", marginBottom: 10 },
      }, subtitle),
      children,
    ],
  );
}

function RadarCardInner({ radar, radarDevice, radarConfig, onRadarConfigChange, matZones, matDevice, simulated = false, now }) {
  // ---- radar connection state (from /api/latest devices[]) ----
  const conn = radarDevice || null;
  const connStatus = conn ? conn.status : "offline";
  const connAge = conn && conn.age_seconds != null ? conn.age_seconds : null;
  const connSimulated = simulated || !!(conn && conn.simulated);
  const sensorStatus = conn && conn.sensor_status ? conn.sensor_status : "ok";

  // ---- presence state (from actual telemetry, never invented) ----
  const radarFresh = !!radar && connStatus === "online" && (connAge == null || connAge <= 90);
  const hasPresence = radarFresh && typeof radar.presence === "boolean";
  const presence = hasPresence ? radar.presence : null;   // null = unknown
  const hasMotion = radarFresh && typeof radar.motion === "boolean";
  const motion = hasMotion ? radar.motion : null;
  const stillnessRaw = radarFresh && typeof radar.stillness_ms === "number"
    ? radar.stillness_ms : null;
  const collapseFlag = radarFresh && typeof radar.possible_collapse === "boolean"
    ? radar.possible_collapse : null;

  // ---- mat occupancy (from four-zone telemetry) ----
  const clockNow = now == null ? Date.now() / 1000 : now;
  const matFresh = !!matZones && Array.isArray(matZones.zones) && matZones.zones.length === 4 &&
    Number.isFinite(matZones.ts) && (clockNow - matZones.ts) * 1000 <= STALE_MS &&
    (!matDevice || matDevice.status === "online");
  const calibratedZone = (z, i) => {
    const calZones = matZones?.calibration?.zones;
    const cal = Array.isArray(calZones) ? calZones[i] : calZones?.[String(i + 1)] || calZones?.[i + 1];
    return cal ? normalizeZone(z, { ...cal, polarity: cal.polarity || matZones.calibration.polarity }) : null;
  };
  const matCalibrated = matFresh && matZones.zones.every((z, i) => calibratedZone(z, i)?.calibrated);
  const matPercents = matCalibrated ? matZones.zones.map((z, i) => calibratedZone(z, i).percent) : null;
  // Reuse the dashboard's established calibrated "Low" band as a conservative
  // observational threshold. Small above-baseline changes stay unknown, rather
  // than being called occupied or silently treated as an empty mat.
  const matOccupied = !matPercents
    ? null
    : matPercents.some((percent) => percent >= DEFAULT_BANDS.low)
      ? true
      : matPercents.every((percent) => percent <= 0)
        ? false
        : null;

  // ---- combined sensor relationship state (observational only, no new triggers) ----
  const relState = (() => {
    if (presence === null || matOccupied === null)
      return "Waiting for sensor data";
    if (presence && matOccupied) return "Radar presence + mat occupied";
    if (presence && !matOccupied) return "Radar presence + mat unoccupied";
    if (!presence && matOccupied) return "No radar presence + mat occupied";
    return "No radar presence + mat unoccupied";
  })();

  // ---- supported vs unavailable measurements ----
  const distance =
    radarFresh && typeof radar.distance_m === "number" &&
      Number.isFinite(radar.distance_m)
      ? radar.distance_m
      : null;
  const targetCount =
    radarFresh && typeof radar.target_count === "number" &&
      Number.isFinite(radar.target_count)
      ? radar.target_count
      : null;
  const signalOrConfidence =
    radarFresh && typeof radar.signal_strength === "number" &&
      Number.isFinite(radar.signal_strength)
      ? radar.signal_strength
      : null;

  // ---- last update time ----
  const lastUpdateTs =
    radar && typeof radar._ts === "number" ? radar._ts : null;
  const lastUpdateText =
    lastUpdateTs != null
      ? new Date(lastUpdateTs * 1000).toLocaleTimeString()
      : conn && conn.last_seen != null
        ? new Date(conn.last_seen * 1000).toLocaleTimeString()
        : "—";

  // ---- theoretical beam width indicator (optional, clearly labelled approximation) ----
  const model =
    radarConfig && radarModelConfirmed(radarConfig.model)
      ? radarModelConfig(radarConfig.model)
      : null;
  const intendedDistance =
    radarConfig &&
      Number.isFinite(radarConfig.intendedDistance) &&
      radarConfig.intendedDistance > 0
      ? radarConfig.intendedDistance
      : null;
  const theoreticalWidth =
    intendedDistance != null
      ? theoreticalBeamWidthAtDistance(intendedDistance)
      : null;

  // ---- drawing ----
  const canvasRef = useRef(null);
  const drawRef = useRef(null);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d");
    const W = cv.width, H = cv.height;
    const halfW = W / 2;
    const cx = halfW, cy = halfW;
    const R = Math.min(W, H) / 2 - 12;   // leave margin for labels

    // sector geometry in "sensor-forward = +X" coords, before mounting yaw
    const halfDeg = 50;   // 100° total / 2
    const aLeft = (-halfDeg) * Math.PI / 180;
    const aRight = (halfDeg) * Math.PI / 180;
    const orientationKnown = radarConfig?.mountingYaw != null && Number.isFinite(radarConfig.mountingYaw);
    const yawDeg = orientationKnown &&
      Number.isFinite(radarConfig.mountingYaw)
      ? radarConfig.mountingYaw
      : 0;
    const yawRad = (yawDeg * Math.PI) / 180;

    const sectorPts = [];
    const steps = 60;
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      const a = aLeft + (aRight - aLeft) * t;
      const raw = { x: R * Math.cos(a), y: R * Math.sin(a) };
      const p =
        yawDeg != null && Number.isFinite(yawDeg)
          ? rotatePoint(raw.x, raw.y, yawDeg)
          : raw;
      sectorPts.push({ x: cx + p.x, y: cy + p.y });
    }

    const leftTip = sectorPts[0];
    const rightTip = sectorPts[sectorPts.length - 1];
    const fwdTip = {
      x: cx + R * Math.cos(yawRad),
      y: cy + R * Math.sin(yawRad),
    };

    const draw = () => {
      ctx.clearRect(0, 0, W, H);

      // background
      ctx.fillStyle = "#0b1220";
      ctx.fillRect(0, 0, W, H);

      // FOV sector fill (nominal — not a guaranteed detection boundary)
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      for (const pt of sectorPts) ctx.lineTo(pt.x, pt.y);
      ctx.closePath();
      ctx.fillStyle = "rgba(56,189,248,0.10)";
      ctx.fill();
      ctx.strokeStyle = "rgba(56,189,248,0.35)";
      ctx.lineWidth = 1;
      ctx.stroke();

      // boundary rays
      ctx.strokeStyle = "#38bdf8";
      ctx.lineWidth = 1.4;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(leftTip.x, leftTip.y);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(rightTip.x, rightTip.y);
      ctx.stroke();

      // centre (forward) axis
      ctx.strokeStyle = "#94a3b8";
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.lineTo(fwdTip.x, fwdTip.y);
      ctx.stroke();
      ctx.setLineDash([]);

      // sensor dot at origin
      ctx.fillStyle = "#38bdf8";
      ctx.beginPath();
      ctx.arc(cx, cy, 4, 0, Math.PI * 2);
      ctx.fill();

      // angle tick labels at sector edges
      ctx.fillStyle = "#64748b";
      ctx.font = "10px monospace";
      ctx.textAlign = "center";
      ctx.fillText("-50°", leftTip.x, leftTip.y + 12);
      ctx.fillText("+50°", rightTip.x, rightTip.y + 12);
      ctx.fillStyle = "#94a3b8";
      ctx.fillText(orientationKnown ? "configured axis · unverified" : "orientation unknown", fwdTip.x, fwdTip.y - 6);

    };

    draw();
    drawRef.current = draw;
    return () => {
      drawRef.current = null;
    };
  }, [
    radarConfig,
    radar,
    intendedDistance,
    model,
  ]);   // re-draw when config, data, or theoretical width changes

  const connColor =
    connStatus === "online"
      ? "#22c55e"
      : connStatus === "stale"
        ? "#f59e0b"
        : "#64748b";
  const connLabel =
    connStatus === "online"
      ? "Online"
      : connStatus === "stale"
        ? "Stale"
        : connStatus === "offline"
          ? "Offline"
          : "Unknown";

  const presenceLabel =
    presence === null ? "Unknown" : presence ? "Present" : "Absent";
  const presenceColor =
    presence === null ? "#64748b" : presence ? "#22c55e" : "#64748b";

  const motionLabel =
    motion === null ? "Not provided" : motion ? "Moving" : "Stationary";
  const motionColor =
    motion === null ? "#64748b" : motion ? "#22c55e" : "#f59e0b";

  const distanceLabel =
    distance != null ? `${distance.toFixed(2)} m` : "Not provided";
  const targetCountLabel =
    targetCount != null ? String(targetCount) : "Not provided";
  const signalLabel =
    signalOrConfidence != null ? String(signalOrConfidence) : "Not provided";

  const fovHorizontalLabel = "100°";
  const fovVerticalLabel = model ? `${model.vertical_fov_deg}°` : "—";

  const modelLabel =
    model ? model.label : "Sensor model not confirmed";
  const vertNote = model ? `nominal vertical FOV` : "(model not confirmed)";

  const simulatedNote = connSimulated ? " (simulated)" : "";

  return h(
    Card,
    {
      title: "C4001 Radar Monitor",
      subtitle: `${modelLabel}${simulatedNote}`,
      danger: collapseFlag === true,
    },
    [
    /* ---- header strip: connection + model ---- */
    h("div", {
      style: {
        display: "flex",
        gap: 12,
        flexWrap: "wrap",
        marginBottom: 10,
        alignItems: "center",
      },
    }, [
      h("span", {
        style: {
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          fontSize: 12,
          color: connColor,
          fontWeight: 700,
        },
      }, [
        h("span", {
          style: {
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: connColor,
            display: "inline-block",
          },
        }),
        h("span", null, connLabel),
        connAge != null
          ? h("span", null, ` · ${Math.round(connAge)}s ago`)
          : null,
        conn && conn.device_id
          ? h("span", null, ` · ${conn.device_id}`)
          : null,
      ]),
      h("span", { style: { fontSize: 11, color: "#64748b" } },
        `sensor status: ${sensorStatus}`),
    ]),

    h("div", {
      style: { display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(135px,1fr))", gap: 8, marginBottom: 10 },
    }, [
      h(ConfigSelect, {
        label: "Sensor model",
        value: radarModelConfirmed(radarConfig?.model) ? radarConfig.model : "",
        onChange: (value) => onRadarConfigChange?.({ model: value }),
      }),
      h(ConfigNumber, { label: "Mounting yaw (°)", value: radarConfig?.mountingYaw, min: -180, max: 180,
        onChange: (value) => onRadarConfigChange?.({ mountingYaw: value }) }),
      h(ConfigNumber, { label: "Downward tilt (°)", value: radarConfig?.downwardTiltDeg, min: 0, max: 90,
        onChange: (value) => onRadarConfigChange?.({ downwardTiltDeg: value }) }),
      h(ConfigNumber, { label: "Target distance (m)", value: radarConfig?.intendedDistance, min: 0.1, max: 100,
        onChange: (value) => onRadarConfigChange?.({ intendedDistance: value }) }),
    ]),
    h("div", { style: { fontSize: 10, color: "#64748b", marginBottom: 8 } },
      `Configured orientation: ${radarConfig?.mountingYaw == null ? "unknown" : `${radarConfig.mountingYaw}° yaw, ${radarConfig.downwardTiltDeg == null ? "tilt unknown" : `${radarConfig.downwardTiltDeg}° tilt`} · unverified`}. Target distance is configuration only.${theoreticalWidth != null ? ` Theoretical beam width at that distance: ≈${theoreticalWidth.toFixed(2)} m (approximation only, not validated coverage).` : ""}`),

    /* ---- top-down sector visualization ---- */
    h("div", {
      style: { position: "relative", marginBottom: 10 },
    }, [
      h("canvas", {
        ref: canvasRef,
        width: 280,
        height: 240,
        style: {
          width: "100%",
          borderRadius: 8,
          background: "#0b1220",
          display: "block",
        },
      }),
      h("div", {
        style: {
          position: "absolute",
          bottom: 6,
          left: 8,
          right: 8,
          fontSize: 10,
          color: "#64748b",
          textAlign: "center",
          lineHeight: 1.4,
        },
      }, [
        h("span", null, "Nominal horizontal field of view: 100°"),
        h("br"),
        h("span", null,
          "This drawing shows the specified beam angle, " +
          "not a guaranteed detection boundary."),
      ]),
    ]),

    /* ---- data cards row (responsive) ---- */
    h("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
        gap: 8,
        marginBottom: 10,
      },
    }, [
      h(DataCard, { label: "Presence", value: presenceLabel, color: presenceColor }),
      h(DataCard, {
        label: "Target distance",
        value: distanceLabel,
        color: distance != null ? "#38bdf8" : "#64748b",
      }),
      h(DataCard, {
        label: "Motion state",
        value: motionLabel,
        color: motionColor,
      }),
      h(DataCard, {
        label: "Target count",
        value: targetCountLabel,
        color: targetCount != null ? "#38bdf8" : "#64748b",
      }),
      h(DataCard, {
        label: "Signal / confidence",
        value: signalLabel,
        color: signalOrConfidence != null ? "#38bdf8" : "#64748b",
      }),
      h(DataCard, {
        label: "Last sensor update",
        value: lastUpdateText,
        color: "#94a3b8",
      }),
    ]),

    /* ---- FOV cards ---- */
    h("div", {
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
        gap: 8,
        marginBottom: 10,
      },
    }, [
      h(DataCard, {
        label: "Horizontal FOV (nominal)",
        value: fovHorizontalLabel,
        color: "#38bdf8",
      }),
      h(DataCard, {
        label: `Vertical FOV (nominal) · ${vertNote}`,
        value: fovVerticalLabel,
        color: "#38bdf8",
      }),
    ]),

    /* ---- pressure mat relationship panel ---- */
    h("div", {
      style: { borderTop: "1px solid #1f2937", paddingTop: 10, marginBottom: 6 },
    }, [
      h("div", {
        style: {
          fontSize: 11,
          color: "#64748b",
          marginBottom: 6,
          textTransform: "uppercase",
          letterSpacing: 1,
        },
      }, "Pressure Mat Relationship (observational)"),
      h("div", {
        style: {
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: 6,
        },
      }, [
        h(RelationshipChip, {
          label: "Radar presence",
          value: presenceLabel,
          color: presenceColor,
        }),
        h(RelationshipChip, {
          label: "Mat occupancy",
          value:
            matOccupied === null
              ? "Unknown"
              : matOccupied
                ? "Occupied"
                : "Unoccupied",
          color:
            matOccupied === null
              ? "#64748b"
              : matOccupied
                ? "#f59e0b"
                : "#64748b",
        }),
        h(RelationshipChip, {
          label: "Combined status",
          value: relState,
          color: collapseFlag ? "#ef4444" : "#94a3b8",
        }),
      ]),
    ]),

    /* ---- stillness rule bar (retained, it uses real data) ---- */
    stillnessRaw != null
      ? h(Bar, {
          label: "Stillness duration (sensor reading)",
          value: stillnessRaw,
          max: 180000,
          unit: " ms",
        })
      : h(DataCard, { label: "Stillness duration", value: "Not provided", color: "#64748b" }),

    /* ---- unsupported-fields note ---- */
    h("div", {
      style: {
        fontSize: 10,
        color: "#475569",
        marginTop: 8,
        lineHeight: 1.4,
      },
    },
      "Current C4001 telemetry provides presence, motion, stillness_ms " +
      "and possible_collapse only. Distance, target count, " +
      "signal/confidence and spatial coordinates are not provided by " +
      "the current firmware."),
  ]);
}

export function RadarCard({ radar, radarDevice, radarConfig, matZones, now }) {
  return h(RadarCardInner, { radar, radarDevice, radarConfig, matZones, now });
}

/* ---------------------------------------------------------------------------
 * Sub-components (pure presentational, no state).
 * --------------------------------------------------------------------------- */
function DataCard({ label, value, color }) {
  return h("div", {
    style: {
      background: "#0b1220",
      borderRadius: 8,
      padding: "8px 10px",
      border: "1px solid #1f2937",
    },
  }, [
    h("div", {
      style: {
        fontSize: 10,
        color: "#64748b",
        textTransform: "uppercase",
        letterSpacing: 0.5,
        marginBottom: 2,
      },
    }, label),
    h("div", {
      style: {
        fontSize: 13,
        fontWeight: 700,
        color,
        whiteSpace: "nowrap",
        overflow: "hidden",
        textOverflow: "ellipsis",
      },
    }, value),
  ]);
}

function ConfigSelect({ label, value, onChange }) {
  return h("label", { style: { display: "grid", gap: 4, color: "#94a3b8", fontSize: 10 } }, [
    label,
    h("select", {
      value,
      onChange: (event) => onChange(event.target.value),
      style: { width: "100%", background: "#0b1220", color: "#e2e8f0", border: "1px solid #334155", borderRadius: 6, padding: "6px 7px", fontSize: 11 },
    }, [
      h("option", { value: "" }, "Not confirmed"),
      ...KNOWN_MODELS.map((key) => h("option", { key, value: key }, RADAR_MODELS[key].label)),
    ]),
  ]);
}

function ConfigNumber({ label, value, min, max, onChange }) {
  return h("label", { style: { display: "grid", gap: 4, color: "#94a3b8", fontSize: 10 } }, [
    label,
    h("input", {
      type: "number", value: value ?? "", min, max, step: "any",
      placeholder: "Unknown",
      onChange: (event) => {
        const raw = event.target.value;
        const parsed = raw === "" ? null : Number(raw);
        onChange(parsed == null || !Number.isFinite(parsed) || parsed < min || parsed > max ? null : parsed);
      },
      style: { width: "100%", boxSizing: "border-box", background: "#0b1220", color: "#e2e8f0", border: "1px solid #334155", borderRadius: 6, padding: "6px 7px", fontSize: 11 },
    }),
  ]);
}

function RelationshipChip({ label, value, color }) {
  return h("div", {
    style: {
      background: "#0b1220",
      borderRadius: 8,
      padding: "8px 10px",
      border: "1px solid #1f2937",
    },
  }, [
    h("div", {
      style: {
        fontSize: 10,
        color: "#64748b",
        textTransform: "uppercase",
        letterSpacing: 0.5,
        marginBottom: 2,
      },
    }, label),
    h("div", {
      style: { fontSize: 12, fontWeight: 700, color },
    }, value),
  ]);
}

export function Bar({ label, value, max, unit }) {
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const pct = clamp(((value || 0) / max) * 100, 0, 100);
  return h("div", { style: { marginBottom: 8 } }, [
    h("div", {
      style: {
        display: "flex",
        justifyContent: "space-between",
        fontSize: 12,
        color: "#cbd5e1",
      },
    }, [
      h("span", null, label),
      h("span", null, `${Number(value).toFixed(1)}${unit}`),
    ]),
    h("div", {
      style: { height: 6, background: "#1e293b", borderRadius: 4 },
    }, [
      h("div", {
        style: {
          width: `${pct}%`,
          height: 6,
          borderRadius: 4,
          background: "#38bdf8",
        },
      }),
    ]),
  ]);
}

/* ---------------------------------------------------------------------------
 * Radar configuration: sensor model, mounting yaw, downward tilt, intended
 * detection distance. Read from window.SAFEBATH_RADAR_CONFIG when present;
 * otherwise default to "model not confirmed".
 * --------------------------------------------------------------------------- */
export function radarConfigFromEnv() {
  try {
    const raw = typeof window !== "undefined"
      ? JSON.parse(window.localStorage.getItem("safebath.radar.config") || "null")
      : null;
    if (!raw || typeof raw !== "object") return defaultRadarConfig();
    const model =
      typeof raw.model === "string" ? raw.model.trim() : "";
    const y = raw.mountingYaw;
    const tilt = raw.downwardTiltDeg;
    const dist = raw.intendedDistance;
    return {
      model: model || "SEN0609",
      modelConfirmed: radarModelConfirmed(model || "SEN0609"),
      mountingYaw:
        y != null && Number.isFinite(y) && y >= -180 && y <= 180 ? y : null,
      downwardTiltDeg:
        tilt != null && Number.isFinite(tilt) && tilt >= 0 && tilt <= 90 ? tilt : null,
      intendedDistance:
        dist != null &&
          Number.isFinite(dist) &&
          dist >= 0.1 && dist <= 100
          ? dist
          : null,
    };
  } catch {
    return defaultRadarConfig();
  }
}

export function defaultRadarConfig() {
  return {
    model: "SEN0609",
    modelConfirmed: true,
    mountingYaw: null,
    downwardTiltDeg: null,
    intendedDistance: null,
  };
}
