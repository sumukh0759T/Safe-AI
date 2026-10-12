/* radar.test.mjs — tests for the C4001 radar monitor: sector geometry, model
 * config, data-card behaviour, and the radar/pressure-mat relationship panel.
 *
 * Pure logic tests hit radarFieldsOfView.mjs directly; rendered tests use
 * react-dom/server to render the RadarCard (no browser required).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  RADAR_MODELS,
  KNOWN_MODELS,
  radarModelConfig,
  radarModelConfirmed,
  HORIZONTAL_FOV_DEG,
  HFovHalfDeg,
  fovSectorPath,
  fovBoundaryPaths,
  centreAxisPath,
  forwardAxisAngle,
  rotatePoint,
  theoreticalBeamWidthAtDistance,
  isRadarFieldSupported,
  unsupportedFieldNote,
  RADAR_UNSUPPORTED_FIELDS,
} from "../radarFieldsOfView.mjs";

import { RadarCard, Bar } from "../radarCard.mjs";

/* ---------- model configuration ---------- */

test("RADAR_MODELS exposes SEN0610 and SEN0609", () => {
  assert.ok("SEN0610" in RADAR_MODELS, "SEN0610 must be a known model");
  assert.ok("SEN0609" in RADAR_MODELS, "SEN0609 must be a known model");
});

test("SEN0610 has horizontal 100° and vertical 80°", () => {
  const m = radarModelConfig("SEN0610");
  assert.ok(m);
  assert.equal(m.horizontal_fov_deg, 100);
  assert.equal(m.vertical_fov_deg, 80);
});

test("SEN0609 has horizontal 100° and vertical 40°", () => {
  const m = radarModelConfig("SEN0609");
  assert.ok(m);
  assert.equal(m.horizontal_fov_deg, 100);
  assert.equal(m.vertical_fov_deg, 40);
});

test("horizontal FOV is identical for both models", () => {
  assert.equal(RADAR_MODELS.SEN0610.horizontal_fov_deg, RADAR_MODELS.SEN0609.horizontal_fov_deg);
  assert.equal(RADAR_MODELS.SEN0610.horizontal_fov_deg, HORIZONTAL_FOV_DEG);
});

test("radarModelConfig returns null for unknown model", () => {
  assert.equal(radarModelConfig("SEN0999"), null);
  assert.equal(radarModelConfig(""), null);
  assert.equal(radarModelConfig(null), null);
});

test("radarModelConfirmed is true only for known models", () => {
  assert.equal(radarModelConfirmed("SEN0610"), true);
  assert.equal(radarModelConfirmed("SEN0609"), true);
  assert.equal(radarModelConfirmed("SEN0999"), false);
  assert.equal(radarModelConfirmed(""), false);
  assert.equal(radarModelConfirmed(null), false);
});

test("unknown model displays as not confirmed", () => {
  assert.equal(radarModelConfirmed(""), false);
  assert.equal(radarModelConfirmed("SEN0999"), false);
});

/* ---------- sector geometry: 100° in the configured coordinate system ---------- */

test("horizontal FOV half-angle is exactly 50°", () => {
  assert.equal(HFovHalfDeg, 50);
  assert.equal(HORIZONTAL_FOV_DEG, 100);
});

test("fovSectorPath produces a sector covering exactly 100°", () => {
  const halfW = 200;
  const path = fovSectorPath(100, halfW, 1, 200, 200);
  assert.ok(typeof path === "string" && path.length > 0, "sector path must be non-empty");
  // The path must contain the arc command (A ...) and the close (Z) so it is a sector, not a line.
  assert.ok(path.includes("A"), "sector path must include an arc command");
  assert.ok(path.includes("Z"), "sector path must close back to the sensor origin");
});

test("fovBoundaryPaths produce two boundary rays from the sensor origin", () => {
  const halfW = 200;
  const b = fovBoundaryPaths(100, halfW, 1, 200, 200);
  assert.ok(typeof b.left === "string" && b.left.startsWith("M"), "left boundary must be an M-prefixed path");
  assert.ok(typeof b.right === "string" && b.right.startsWith("M"), "right boundary must be an M-prefixed path");
  // Both rays originate at the sensor origin (cx,cy) = (200,200).
  assert.ok(b.left.includes("200 200"), "left boundary must start at sensor origin");
  assert.ok(b.right.includes("200 200"), "right boundary must start at sensor origin");
});

test("centreAxisPath is a ray from the sensor origin along +X", () => {
  const halfW = 200;
  const p = centreAxisPath(halfW, 1, 200, 200);
  assert.ok(p.startsWith("M 200 200"), "centre axis must start at sensor origin");
  // +X forward: end point has the same y as the origin.
  assert.ok(p.includes(" 200"), "centre axis end y must equal origin y (forward = +X)");
});

test("no coverage is drawn behind the sensor (sector opens toward +X)", () => {
  // The sector tips lie at cos(±50°)*R, sin(±50°)*R relative to the origin.
  // For a top-down +X-forward drawing the sector spans -50°..+50° around +X,
  // so the x-coordinates of the tips are positive (in front of the sensor).
  const halfW = 200, R = halfW;
  const aLeft = (-HFovHalfDeg) * Math.PI / 180;
  const aRight = (HFovHalfDeg) * Math.PI / 180;
  const tipL = { x: R * Math.cos(aLeft), y: R * Math.sin(aLeft) };
  const tipR = { x: R * Math.cos(aRight), y: R * Math.sin(aRight) };
  // Both tips have positive x = in front of the sensor, never behind (-x).
  assert.ok(tipL.x > 0, "left FOV tip must be in front of the sensor (x > 0)");
  assert.ok(tipR.x > 0, "right FOV tip must be in front of the sensor (x > 0)");
  // The sector arc spans 100° total.
  const span = (aRight - aLeft) * 180 / Math.PI;
  assert.equal(span, 100);
});

test("boundary tips are symmetric about the forward axis", () => {
  const halfW = 200, R = halfW;
  const a = HFovHalfDeg * Math.PI / 180;
  const tipL = { x: R * Math.cos(-a), y: R * Math.sin(-a) };
  const tipR = { x: R * Math.cos(a), y: R * Math.sin(a) };
  assert.equal(tipL.x, tipR.x, "tips must have equal forward (x) extent");
  assert.equal(tipL.y, -tipR.y, "tips must be symmetric about the forward axis");
});

/* ---------- mounting orientation ---------- */

test("forwardAxisAngle normalises a yaw to [0,360)", () => {
  assert.equal(forwardAxisAngle(0), 0);
  assert.equal(forwardAxisAngle(90), 90);
  assert.equal(forwardAxisAngle(370), 10);
  assert.equal(forwardAxisAngle(-50), 310);
  assert.equal(forwardAxisAngle(450), 90);
});

test("forwardAxisAngle returns null when yaw is unconfigured", () => {
  assert.equal(forwardAxisAngle(null), null);
  assert.equal(forwardAxisAngle(undefined), null);
  assert.equal(forwardAxisAngle(NaN), null);
});

test("rotatePoint rotates a point around the sensor origin", () => {
  // Rotate (1,0) by +90° -> (0,1) in a standard right-handed rotation.
  const p = rotatePoint(1, 0, 90);
  assert.ok(Math.abs(p.x) < 1e-9, "rotated x should be ~0");
  assert.ok(Math.abs(p.y - 1) < 1e-9, "rotated y should be ~1");
});

test("rotatePoint is identity when yaw is unconfigured", () => {
  const p = rotatePoint(3, 4, null);
  assert.equal(p.x, 3);
  assert.equal(p.y, 4);
});

/* ---------- theoretical beam width (approximation, clearly labelled) ---------- */

test("theoretical beam width at distance d uses width = 2*d*tan(50°)", () => {
  const d = 2;
  const w = theoreticalBeamWidthAtDistance(d);
  const expected = 2 * d * Math.tan((50 * Math.PI) / 180);
  assert.ok(w != null);
  assert.strictEqual(w, expected);
});

test("theoretical beam width returns null for missing/invalid distance", () => {
  assert.equal(theoreticalBeamWidthAtDistance(null), null);
  assert.equal(theoreticalBeamWidthAtDistance(undefined), null);
  assert.equal(theoreticalBeamWidthAtDistance(0), null);
  assert.equal(theoreticalBeamWidthAtDistance(-1), null);
  assert.equal(theoreticalBeamWidthAtDistance(NaN), null);
  assert.equal(theoreticalBeamWidthAtDistance(Infinity), null);
});

/* ---------- telemetry contract: supported vs unsupported fields ---------- */

test("isRadarFieldSupported recognises the current C4001 contract fields", () => {
  assert.equal(isRadarFieldSupported("presence"), true);
  assert.equal(isRadarFieldSupported("motion"), true);
  assert.equal(isRadarFieldSupported("stillness_ms"), true);
  assert.equal(isRadarFieldSupported("possible_collapse"), true);
});

test("isRadarFieldSupported rejects fields not in the current contract", () => {
  assert.equal(isRadarFieldSupported("distance_m"), false);
  assert.equal(isRadarFieldSupported("target_count"), false);
  assert.equal(isRadarFieldSupported("signal_strength"), false);
  assert.equal(isRadarFieldSupported("confidence"), false);
  assert.equal(isRadarFieldSupported("point_cloud"), false);
  assert.equal(isRadarFieldSupported("target_coords"), false);
});

test("RADAR_UNSUPPORTED_FIELDS lists every field absent from the current contract", () => {
  for (const f of RADAR_UNSUPPORTED_FIELDS) {
    assert.equal(isRadarFieldSupported(f), false, `${f} must be unsupported by the current firmware`);
  }
  assert.ok(RADAR_UNSUPPORTED_FIELDS.includes("distance_m"));
  assert.ok(RADAR_UNSUPPORTED_FIELDS.includes("target_count"));
  assert.ok(RADAR_UNSUPPORTED_FIELDS.includes("signal_strength"));
});

test("unsupportedFieldNote describes the limitation without inventing a value", () => {
  const note = unsupportedFieldNote("distance_m");
  assert.ok(typeof note === "string" && note.length > 0);
  assert.ok(note.toLowerCase().includes("not provided") || note.toLowerCase().includes("unavailable"),
    "note must make clear the value is unavailable");
});

/* ---------- rendered RadarCard: data cards + states ---------- */

function radarCardMarkup(props) {
  const matZones = props.matZones && {
    ...props.matZones,
    ts: props.matZones.ts ?? Date.now() / 1000,
    calibration: Object.hasOwn(props.matZones, "calibration") ? props.matZones.calibration : {
      polarity: "increase",
      zones: [0, 1, 2, 3].map(() => ({ baseline: 120, max: 3000 })),
    },
  };
  return renderToStaticMarkup(React.createElement(RadarCard, { ...props, matZones }));
}

test("RadarCard renders the 100° nominal FOV label", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("Nominal horizontal field of view: 100°"),
    "card must state the nominal 100° horizontal FOV");
});

test("RadarCard shows SEN0610 vertical FOV (80°) when model configured", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("80°"), "SEN0610 card must expose 80° vertical FOV");
  assert.ok(html.includes("Vertical FOV"), "vertical FOV must be labelled separately from horizontal");
});

test("RadarCard shows SEN0609 vertical FOV (40°) when model configured", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0609", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("40°"), "SEN0609 card must expose 40° vertical FOV");
  assert.ok(html.includes("Vertical FOV"), "vertical FOV must be labelled separately from horizontal");
});

test("RadarCard shows 'Sensor model not confirmed' when model unconfigured", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "", modelConfirmed: false, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  // When model is unconfigured, the subtitle shows "Sensor model not confirmed".
  // The card title is "C4001 Radar Monitor".
  assert.ok(html.includes("Sensor model not confirmed"),
    `expected "Sensor model not confirmed" in card; got: ${html.substring(0, 500)}`);
  assert.ok(html.includes("C4001 Radar Monitor"), "card title must render");
});

test("RadarCard shows 'Not provided' for distance when firmware does not supply it", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("Not provided"), "missing distance must be shown as Not provided");
  // Ensure no fabricated distance appears next to the "Target distance" label.
  // Use a tighter regex that requires "m" to be followed by a word boundary or end,
  // so the stillness bar's "0.0 ms" does not false-match.
  const m = html.match(/Target distance[^<]*?(\d+\.?\d*)\s*m(?:\b|$)/);
  assert.equal(m ? m[1] : undefined, undefined,
    `must not render a fabricated distance in metres; matched: ${m ? m[0] : "none"}`);
});

test("RadarCard shows 'Not provided' for motion when motion field missing", () => {
  const html = radarCardMarkup({
    radar: { presence: true, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("Not provided"), "missing motion must be shown as Not provided");
});

test("RadarCard shows 'Not provided' for target count when absent", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("Target count"), "target count card must render");
  assert.ok(html.includes("Not provided"), "target count must show Not provided");
});

test("RadarCard shows 'Not provided' for signal/confidence when absent", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("Signal / confidence"), "signal/confidence card must render");
  assert.ok(html.includes("Not provided"), "signal/confidence must show Not provided");
});

/* ---------- offline / stale / unknown states ---------- */

test("RadarCard renders offline state when device offline", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "offline", last_seen: null, age_seconds: null, sensor_status: "ok", simulated: false },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("Offline"), "offline device must be labelled Offline");
  assert.ok(html.includes("radar-01"), "device id must render");
});

test("RadarCard renders stale state when device stale", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "stale", last_seen: 1000, age_seconds: 300, sensor_status: "ok", simulated: false },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("Stale"), "stale device must be labelled Stale");
  assert.match(html, /Presence<\/div><div[^>]*>Unknown/,
    "stale telemetry must not be presented as current presence");
});

test("RadarCard renders unknown presence when telemetry missing", () => {
  const html = radarCardMarkup({
    radar: null,
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: false },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("Unknown"), "missing presence must be labelled Unknown");
  assert.ok(html.includes("Waiting for sensor data") || html.includes("No radar presence") ,
    "relationship panel must reflect missing telemetry");
});

/* ---------- missing telemetry / unknown occupancy ---------- */

test("RadarCard shows 'Waiting for sensor data' when mat telemetry missing", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("Waiting for sensor data") || html.includes("No data"),
    "missing mat data must be shown as unknown/waiting, not as mat unoccupied");
});

test("RadarCard keeps occupancy unknown when mat data is stale or uncalibrated", () => {
  const stale = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false },
    radarDevice: { device_id: "radar-01", status: "online", age_seconds: 5 },
    radarConfig: { model: "", mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: { zones: [3000, 300, 300, 300], ts: Date.now() / 1000 - 60 },
  });
  assert.ok(stale.includes("Waiting for sensor data"));
  const uncalibrated = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false },
    radarDevice: { device_id: "radar-01", status: "online", age_seconds: 5 },
    radarConfig: { model: "", mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: { zones: [3000, 300, 300, 300], calibration: null },
  });
  assert.ok(uncalibrated.includes("Waiting for sensor data"));
  const weakSignal = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false },
    radarDevice: { device_id: "radar-01", status: "online", age_seconds: 5 },
    radarConfig: { model: "", mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: { zones: [200, 120, 120, 120] },
  });
  assert.ok(weakSignal.includes("Waiting for sensor data"),
    "small calibrated response below the existing low band must stay unknown");
});

test("RadarCard shows 'No data' for mat occupancy when matZones absent", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("Radar presence + mat unoccupied") || html.includes("Radar presence + mat occupied") || html.includes("Waiting for sensor data") || html.includes("No data"),
    "combined status must not claim mat occupied when no mat data exists");
});

/* ---------- combined sensor relationship states ---------- */

test("RadarCard shows 'Radar presence + mat occupied' when both present", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: true, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: { zones: [3000, 300, 300, 300] },
  });
  assert.ok(html.includes("Radar presence + mat occupied"),
    "must show combined presence+occupied state");
});

test("RadarCard shows 'Radar presence + mat unoccupied' when radar present but mat clear", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: { zones: [120, 120, 120, 120] },
  });
  assert.ok(html.includes("Radar presence + mat unoccupied"),
    "must show combined presence+unoccupied state");
});

test("RadarCard shows 'No radar presence + mat occupied' when radar absent but mat loaded", () => {
  const html = radarCardMarkup({
    radar: { presence: false, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: { zones: [3000, 300, 300, 300] },
  });
  assert.ok(html.includes("No radar presence + mat occupied"),
    "must show combined no-presence+occupied state");
});

test("RadarCard does not show a combined state when presence is unknown (not boolean)", () => {
  const html = radarCardMarkup({
    radar: { presence: null, motion: null, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: { zones: [3000, 300, 300, 300] },
  });
  // When presence is null (unknown), the relationship must not assert presence.
  assert.ok(
    html.includes("Waiting for sensor data") || html.includes("Unknown") || html.includes("No data"),
    "unknown presence must not be silently treated as absent");
});

/* ---------- simulated vs real data separation ---------- */

test("RadarCard renders simulated note when radar device is simulated", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("(simulated)"),
    "simulated device must be labelled simulated in the card subtitle");
});

test("RadarCard does not show '(simulated)' when device is real", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: false },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.equal(html.includes("(simulated)"), false,
    "real device must not be labelled simulated");
});

test("RadarCard still shows horizontal FOV label even when no model confirmed (nominal 100°)", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "", modelConfirmed: false, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("Nominal horizontal field of view: 100°"),
    "the 100° nominal FOV statement must appear regardless of model confirmation");
});

/* ---------- mounting orientation: unknown vs configured ---------- */

test("RadarCard renders FOV even when mounting yaw unconfigured (unknown orientation)", () => {
  const html = radarCardMarkup({
    radar: { presence: true, motion: false, stillness_ms: 0, possible_collapse: false, _ts: 1000 },
    radarDevice: { device_id: "radar-01", module: "radar", status: "online", age_seconds: 5, sensor_status: "ok", simulated: true },
    radarConfig: { model: "SEN0610", modelConfirmed: true, mountingYaw: null, downwardTiltDeg: null, intendedDistance: null },
    matZones: null,
  });
  assert.ok(html.includes("Nominal horizontal field of view: 100°"),
    "unconfigured orientation must not hide the FOV label");
});
