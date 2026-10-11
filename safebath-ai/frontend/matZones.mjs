/*
 * matZones.mjs — four-zone pressure-mat model for the SafeBath AI dashboard.
 *
 * The physical mat changed from a 12x6 Velostat grid (72 cells) to four larger
 * sensing zones in a 2x2 layout. This module owns the zone geometry, the safe
 * per-zone normalization, the qualitative pressure bands/colors, and the small
 * presentational card, so the pure logic can be unit-tested with `node --test`
 * (react-dom/server renders the card without a browser).
 *
 * Ground rules baked in here:
 *   - Raw values are ADC counts, never "force" or "weight". They are labelled
 *     as ADC readings unless a calibration turns them into a percentage.
 *   - No fabricated percentages: without calibration every zone is explicitly
 *     "Uncalibrated" and shows only the raw reading.
 *   - ADC may rise OR fall under pressure (circuit-dependent), so normalization
 *     honours a configured polarity instead of assuming one direction.
 *   - Unavailable / stale / error states are neutral grey, never a fake "low".
 *
 * Configurable band defaults (percent of calibrated response):
 *   < 25  -> Low        (cool)
 *   < 60  -> Medium     (intermediate)
 *   >= 60 -> High       (warm)
 *   0     -> No Pressure (neutral)
 */
import React from "react";

export const ZONE_COUNT = 4;

export const ZONE_META = [
  { id: 1, label: "Zone 1 — Top Left", short: "Top Left" },
  { id: 2, label: "Zone 2 — Top Right", short: "Top Right" },
  { id: 3, label: "Zone 3 — Bottom Left", short: "Bottom Left" },
  { id: 4, label: "Zone 4 — Bottom Right", short: "Bottom Right" },
];

// Percent thresholds for the qualitative bands (kept in one reviewable place).
export const DEFAULT_BANDS = { low: 25, medium: 60 };

// A four-zone frame older than this is treated as stale even if the device
// status endpoint is unavailable (milliseconds).
export const STALE_MS = 15000;

export const ZONE_COLORS = {
  none: "#334155",        // no pressure
  low: "#2563eb",         // cool
  medium: "#a855f7",      // intermediate
  high: "#dc2626",        // warm
  unavailable: "#475569", // neutral grey (stale / offline / uncalibrated / error)
};

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

function asFiniteNumber(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/*
 * Normalize one zone reading to a 0-100% pressure response.
 * Returns { percent, calibrated }. `percent` is null when calibration is
 * absent/invalid so callers can never show a fabricated value.
 *
 * cal: { baseline, max, polarity? } where polarity is "increase" (reading goes
 * up with pressure) or "decrease" (reading goes down with pressure).
 */
export function normalizeZone(adc, cal) {
  const v = asFiniteNumber(adc);
  if (v === null || !cal) return { percent: null, calibrated: false };
  const baseline = asFiniteNumber(cal.baseline);
  const max = asFiniteNumber(cal.max);
  if (baseline === null || max === null || baseline === max) {
    return { percent: null, calibrated: false };
  }
  const polarity = cal.polarity === "decrease" ? "decrease" : "increase";
  const pct = polarity === "decrease"
    ? ((baseline - v) / (baseline - max)) * 100
    : ((v - baseline) / (max - baseline)) * 100;
  return { percent: clamp(pct, 0, 100), calibrated: true };
}

export function zoneState(percent, calibrated, bands = DEFAULT_BANDS) {
  if (!calibrated || percent === null || percent === undefined) {
    return "Uncalibrated";
  }
  if (percent <= 0) return "No Pressure";
  if (percent < bands.low) return "Low";
  if (percent < bands.medium) return "Medium";
  return "High";
}

export function zoneColor(percent, calibrated, opts = {}) {
  const { stale = false, unavailable = false } = opts;
  if (unavailable || stale || !calibrated || percent === null) {
    return ZONE_COLORS.unavailable;
  }
  if (percent <= 0) return ZONE_COLORS.none;
  if (percent < DEFAULT_BANDS.low) return ZONE_COLORS.low;
  if (percent < DEFAULT_BANDS.medium) return ZONE_COLORS.medium;
  return ZONE_COLORS.high;
}

export function isStale(ts, now, staleMs = STALE_MS) {
  const t = asFiniteNumber(ts);
  if (t === null) return false;
  const n = (now === undefined || now === null) ? Date.now() / 1000 : now;
  return (n - t) * 1000 > staleMs;
}

function calibrationForZone(calibration, index, id) {
  if (!calibration || !calibration.zones) return null;
  const zones = calibration.zones;
  let z = null;
  if (Array.isArray(zones)) z = zones[index];
  else if (typeof zones === "object") z = zones[String(id)] || zones[id];
  if (!z) return null;
  const cal = { ...z };
  if (!cal.polarity && calibration.polarity) cal.polarity = calibration.polarity;
  return cal;
}

/*
 * Build the four renderable zone cells from the latest frame + calibration.
 * Always returns exactly ZONE_COUNT cells in fixed order (Zone 1..4), which is
 * what guarantees the UI never shows a variable number of cells.
 *
 * opts: { stale, error, loading, bands }
 */
export function buildZoneCells(zones, calibration, opts = {}) {
  const { stale = false, error = null, loading = false,
          bands = DEFAULT_BANDS } = opts;
  const list = Array.isArray(zones) ? zones : [];
  return ZONE_META.map((meta, i) => {
    const raw = asFiniteNumber(list[i]);
    const cal = calibrationForZone(calibration, i, meta.id);
    const norm = normalizeZone(raw, cal);

    let state = zoneState(norm.percent, norm.calibrated, bands);
    let note = null;
    let unavailable = false;

    if (error) {
      state = "API Unavailable";
      note = "error";
      unavailable = true;
    } else if (loading || raw === null) {
      state = "No Data";
      note = "waiting";
      unavailable = true;
    } else if (stale) {
      note = "stale";
      unavailable = true;
    } else if (!norm.calibrated) {
      note = "uncalibrated";
      unavailable = true;
    }

    return {
      id: meta.id,
      label: meta.label,
      short: meta.short,
      raw,
      percent: norm.calibrated ? norm.percent : null,
      calibrated: norm.calibrated,
      state,
      color: zoneColor(norm.percent, norm.calibrated, { stale, unavailable }),
      note,
    };
  });
}

/*
 * Which mat visualization to show. Fails safe to the legacy grid unless the
 * backend explicitly reports four-zone mode, so 72-cell telemetry can never be
 * mistaken for a 2x2 mat.
 */
export function matModeFromSnapshot(snap) {
  if (!snap) return "grid";
  if (snap.mat_mode === "four_zone") return "four_zone";
  return "grid";
}

const h = React.createElement;

const zoneCellStyle = (cell) => ({
  border: `1px solid ${cell.note === "error" ? "#7f1d1d" : "#1f2937"}`,
  borderRadius: 10,
  padding: 12,
  background: "#0b1220",
  display: "flex",
  flexDirection: "column",
  gap: 6,
  minHeight: 108,
  borderTop: `4px solid ${cell.color}`,
});

/*
 * MatZonesCard — the 2x2 responsive grid. Renders exactly four cells. The
 * caller wraps it in the dashboard Card so the styling matches the other
 * boards; this component only owns the cells.
 */
export function MatZonesCard({ zones, calibration, matTs, deviceStatus,
                               error, loading, now, staleMs = STALE_MS }) {
  const deviceStale = !!deviceStatus && deviceStatus !== "online";
  const stale = deviceStale || isStale(matTs, now, staleMs);
  const cells = buildZoneCells(zones, calibration, { stale, error, loading });

  return h("div", { "data-testid": "mat-zones" }, [
    h("div", {
      key: "grid",
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(2, minmax(120px, 1fr))",
        gap: 10,
      },
    }, cells.map((cell) => h("div", {
      key: cell.id,
      "data-zone": cell.id,
      "data-state": cell.state,
      "data-calibrated": cell.calibrated ? "true" : "false",
      style: zoneCellStyle(cell),
    }, [
      h("div", {
        key: "label",
        style: { fontSize: 12, fontWeight: 700, color: "#e2e8f0" },
      }, cell.label),
      h("div", {
        key: "raw",
        style: { fontSize: 22, fontWeight: 800, color: cell.color },
      }, cell.raw === null
        ? "—"
        : [
            String(Math.round(cell.raw)),
            h("span", {
              key: "unit",
              style: { fontSize: 10, fontWeight: 600, color: "#64748b",
                       marginLeft: 4 },
            }, "ADC"),
          ]),
      cell.calibrated && cell.percent !== null
        ? h("div", {
            key: "pct",
            style: { fontSize: 12, color: "#cbd5e1" },
          }, `${Math.round(cell.percent)}% response`)
        : h("div", {
            key: "pct",
            style: { fontSize: 12, color: "#64748b" },
          }, "Uncalibrated — raw ADC only"),
      h("div", {
        key: "state",
        style: {
          fontSize: 11, fontWeight: 700, marginTop: "auto",
          color: cell.state === "High" ? ZONE_COLORS.high
            : cell.state === "Medium" ? ZONE_COLORS.medium
            : cell.state === "Low" ? "#93c5fd"
            : "#94a3b8",
        },
      }, cell.state + (cell.note === "stale" ? " · stale"
        : cell.note === "error" ? " · API error" : "")),
    ]))),
    h("div", {
      key: "foot",
      style: { fontSize: 11, color: "#64748b", marginTop: 8 },
    }, "2×2 four-zone mat · values are raw ADC counts"
      + ` · last update ${asFiniteNumber(matTs)
        ? new Date(Number(matTs) * 1000).toLocaleTimeString() : "—"}`
      + (deviceStatus ? ` · device ${deviceStatus}` : "")
      + (stale ? " · reading is stale/offline" : "")),
  ]);
}
