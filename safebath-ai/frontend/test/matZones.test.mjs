/*
 * Four-zone mat UI tests — pure model + a real server-rendered card (no
 * browser needed). Proves the 2x2 grid has exactly four labelled cells, that
 * readings map to the right zone, that uncalibrated data never shows a
 * fabricated percentage, and that stale/error states are surfaced.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  ZONE_COUNT,
  ZONE_META,
  MatZonesCard,
  buildZoneCells,
  isStale,
  matModeFromSnapshot,
  normalizeZone,
  zoneState,
} from "../matZones.mjs";

const markup = (props) =>
  renderToStaticMarkup(React.createElement(MatZonesCard, props));

function countZones(html) {
  return (html.match(/data-zone="/g) || []).length;
}

test("four-zone mode renders exactly four cells", () => {
  const html = markup({ zones: [100, 200, 300, 400] });
  assert.equal(ZONE_COUNT, 4);
  assert.equal(countZones(html), 4);
});

test("each cell displays its fixed zone label", () => {
  const html = markup({ zones: [100, 200, 300, 400] });
  for (const meta of ZONE_META) assert.ok(html.includes(meta.label), meta.label);
});

test("new readings update the correct cell", () => {
  let cells = buildZoneCells([1, 2, 3, 4], null);
  assert.deepEqual(cells.map((c) => c.raw), [1, 2, 3, 4]);
  cells = buildZoneCells([10, 20, 30, 40], null);
  assert.deepEqual(cells.map((c) => c.raw), [10, 20, 30, 40]);
  assert.deepEqual(cells.map((c) => c.id), [1, 2, 3, 4]);
});

test("uncalibrated values do not display fabricated percentages", () => {
  const cells = buildZoneCells([1200, 2900, 600, 2100], null);
  for (const c of cells) {
    assert.equal(c.calibrated, false);
    assert.equal(c.percent, null);
    assert.equal(c.state, "Uncalibrated");
  }
  const html = markup({ zones: [1200, 2900, 600, 2100], calibration: null });
  assert.equal(html.includes("% response"), false);
  assert.ok(html.includes("Uncalibrated"));
  assert.ok(html.includes("1200"));
});

test("calibrated increase polarity normalizes to 0-100%", () => {
  const cal = { baseline: 100, max: 2100 };
  assert.equal(normalizeZone(1100, cal).percent, 50);
  assert.equal(normalizeZone(0, cal).percent, 0);      // clamped low
  assert.equal(normalizeZone(9999, cal).percent, 100);  // clamped high
  const cells = buildZoneCells([1100, 100, 2100, 500], { zones: [cal, cal, cal, cal] });
  assert.equal(cells[0].percent, 50);
  assert.equal(cells[0].state, "Medium");
  assert.equal(cells[1].state, "No Pressure");
  assert.equal(cells[2].state, "High");
});

test("calibrated decrease polarity is honoured", () => {
  const cal = { baseline: 3000, max: 200, polarity: "decrease" };
  assert.equal(normalizeZone(1600, cal).percent, 50);
  assert.equal(normalizeZone(3000, cal).percent, 0);
});

test("per-zone polarity overrides the global default", () => {
  const calibration = {
    polarity: "increase",
    zones: [{ baseline: 3000, max: 200, polarity: "decrease" },
            { baseline: 100, max: 2100 }],
  };
  const cells = buildZoneCells([1600, 1100], calibration);
  assert.equal(cells[0].percent, 50);
  assert.equal(cells[1].percent, 50);
});

test("zoneState bands", () => {
  assert.equal(zoneState(0, true), "No Pressure");
  assert.equal(zoneState(10, true), "Low");
  assert.equal(zoneState(40, true), "Medium");
  assert.equal(zoneState(80, true), "High");
  assert.equal(zoneState(null, false), "Uncalibrated");
});

test("isStale uses the freshness window", () => {
  const now = 1_000_000;
  assert.equal(isStale(now - 5, now, 15000), false);
  assert.equal(isStale(now - 20, now, 15000), true);
  assert.equal(isStale(null, now, 15000), false);
});

test("stale device state is surfaced", () => {
  const html = markup({ zones: [100, 200, 300, 400], deviceStatus: "offline" });
  assert.ok(html.includes("stale"));
  assert.equal(countZones(html), 4);
});

test("API error state is surfaced for every cell", () => {
  const html = markup({ zones: null, error: "api-unreachable" });
  assert.ok(html.includes("API Unavailable"));
  assert.equal(countZones(html), 4);
});

test("loading state renders four placeholder cells", () => {
  const html = markup({ zones: null, loading: true });
  assert.ok(html.includes("No Data"));
  assert.equal(countZones(html), 4);
});

test("legacy 72-cell snapshot does not render as four zones", () => {
  assert.equal(matModeFromSnapshot({ mat_mode: "grid" }), "grid");
  assert.equal(matModeFromSnapshot({}), "grid");
  assert.equal(matModeFromSnapshot(undefined), "grid");
  assert.equal(matModeFromSnapshot({ mat_frame: { values: new Array(72).fill(0) } }),
    "grid");
  assert.equal(matModeFromSnapshot({ mat_mode: "four_zone" }), "four_zone");
});
