/* radarFieldsOfView.mjs — C4001 field-of-view model + top-down drawing helpers.

Hardware specifications (DFRobot mmWave presence sensors):

  | Sensor model | Horizontal FOV | Vertical FOV |
  |---|---:|---:|
  | DFRobot SEN0610, 12 m version | 100° | 80° |
  | DFRobot SEN0609, 25 m version | 100° | 40° |

These are the nominal *beam* / *field-of-view* angles published for these
sensor families. They describe the angular extent the sensor can see, NOT a
guaranteed detection boundary at any specific distance. The SafeBath project
uses a DFRobot C4001, whose exact model variant (SEN0610 vs SEN0609) is not
yet confirmed in the repo's hardware BOM, so the dashboard treats the model as
configurable and shows "Sensor model not confirmed" when it is unknown.

Coordinate system (top-down, sensor at origin looking toward +X / "forward"):

  - 0° = sensor's forward axis (toward the pressure mat by default)
  - angles run -50° .. +50° for a 100° horizontal FOV
  - positive angle = to one side, negative = to the other side
  - the drawing is a top-down plan view, so the vertical FOV is reported as a
    separate nominal spec and is NEVER drawn as horizontal coverage
  - mounting yaw rotates the whole sector around the sensor origin
  - downward tilt is a side-view concept; it does not change the top-down arc

Theoretical horizontal beam width at distance d (approximation only):

  width = 2 * d * tan(horizontal_fov_half)

This is the chord spanned by the beam at distance d. It is an approximation
derived from the published beam angle; it is NOT an experimentally validated
detection boundary and is shown only as an optional, clearly-labelled indicator.
*/

export const HORIZONTAL_FOV_DEG = 100;     // both SEN0610 and SEN0609
export const HFovHalfDeg = HORIZONTAL_FOV_DEG / 2;

export const RADAR_MODELS = {
  SEN0610: {
    label: "DFRobot SEN0610 (12 m)",
    horizontal_fov_deg: 100,
    vertical_fov_deg: 80,
    nominal_range_m: 12,
  },
  SEN0609: {
    label: "DFRobot SEN0609 (25 m)",
    horizontal_fov_deg: 100,
    vertical_fov_deg: 40,
    nominal_range_m: 25,
  },
};

export const KNOWN_MODELS = Object.keys(RADAR_MODELS);

export function radarModelConfig(model) {
  if (!model || typeof model !== "string") return null;
  const key = model.trim().toUpperCase();
  return RADAR_MODELS[key] || null;
}

export function radarModelConfirmed(model) {
  return !!model && typeof model === "string" && RADAR_MODELS.hasOwnProperty(model.trim().toUpperCase());
}

export function horizontalFovHalfRad() {
  return (HFovHalfDeg * Math.PI) / 180;
}

/** Theoretical chord width at distance d from the sensor, in the same units as d. */
export function theoreticalBeamWidthAtDistance(d) {
  if (d == null || !Number.isFinite(d) || d <= 0) return null;
  return 2 * d * Math.tan(horizontalFovHalfRad());
}

/** Points on the arc of the horizontal FOV at distance R from the sensor origin.
 *  Returns [{x,y}, ...] in canvas pixel coords where +X = forward.
 *  `startDeg`/`endDeg` are relative to the forward axis (e.g. -50, +50).
 *  `yDown` is true when canvas y increases downward (the common case).
 */
export function fovArcPoints(R, startDeg, endDeg, steps = 40, yDown = true) {
  const half = horizontalFovHalfRad();
  const a0 = (-HFovHalfDeg) * Math.PI / 180;
  const a1 = (HFovHalfDeg) * Math.PI / 180;
  const pts = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const a = a0 + (a1 - a0) * t;
    const x = R * Math.cos(a);
    const y = R * Math.sin(a);
    pts.push({ x, y: yDown ? y : -y });
  }
  return pts;
}

/** Build the SVG path for the shaded FOV sector (top-down, sensor at origin).
 *  `halfW` is half the canvas width; `scale` converts "meters from sensor" to
 *  canvas pixels; the sensor sits at (halfW, halfW) by default.
 */
export function fovSectorPath(sectorDeg, halfW, scale = 1, cx = null, cy = null) {
  const cx_ = cx == null ? halfW : cx;
  const cy_ = cy == null ? halfW : cy;
  const R = Math.min(halfW, halfW) * scale;
  const half = sectorDeg / 2;
  const a0 = (-half) * Math.PI / 180;
  const a1 = (half) * Math.PI / 180;
  const x0 = cx_ + R * Math.cos(a0);
  const y0 = cy_ + R * Math.sin(a0);
  const x1 = cx_ + R * Math.cos(a1);
  const y1 = cy_ + R * Math.sin(a1);
  // arc sweep: small-arc + sweep-flag chosen for the top-down +X-forward convention
  const large = sectorDeg > 180 ? 1 : 0;
  const sweep = 1; // positive-angle direction in our coord system
  return `M ${cx_} ${cy_} L ${x0} ${y0} A ${R} ${R} 0 ${large} ${sweep} ${x1} ${y1} Z`;
}

/** Boundary rays from sensor to the two FOV edges. */
export function fovBoundaryPaths(sectorDeg, halfW, scale = 1, cx = null, cy = null) {
  const cx_ = cx == null ? halfW : cx;
  const cy_ = cy == null ? halfW : cy;
  const R = Math.min(halfW, halfW) * scale;
  const half = sectorDeg / 2;
  const a0 = (-half) * Math.PI / 180;
  const a1 = (half) * Math.PI / 180;
  const x0 = cx_ + R * Math.cos(a0);
  const y0 = cy_ + R * Math.sin(a0);
  const x1 = cx_ + R * Math.cos(a1);
  const y1 = cy_ + R * Math.sin(a1);
  return {
    left: `M ${cx_} ${cy_} L ${x0} ${y0}`,
    right: `M ${cx_} ${cy_} L ${x1} ${y1}`,
    tipLeft: { x: x0, y: y0 },
    tipRight: { x: x1, y: y1 },
  };
}

/** Forward-axis (centre) ray. */
export function centreAxisPath(halfW, scale = 1, cx = null, cy = null) {
  const cx_ = cx == null ? halfW : cx;
  const cy_ = cy == null ? halfW : cy;
  const R = Math.min(halfW, halfW) * scale;
  return `M ${cx_} ${cy_} L ${cx_ + R} ${cy_}`;
}

/** Angle of the forward axis after applying mounting yaw (degrees, 0 = +X). */
export function forwardAxisAngle(yawDeg) {
  if (yawDeg == null || !Number.isFinite(yawDeg)) return null;
  return ((yawDeg % 360) + 360) % 360;
}

/** Rotate a point around the sensor origin by mountingYaw (degrees). */
export function rotatePoint(x, y, yawDeg) {
  if (yawDeg == null || !Number.isFinite(yawDeg)) return { x, y };
  const r = (yawDeg * Math.PI) / 180;
  const c = Math.cos(r), s = Math.sin(r);
  return { x: x * c - y * s, y: x * s + y * c };
}

/** Index for available but unsupported measurements in the radar data cards. */
export const RADAR_UNSUPPORTED_FIELDS = [
  "distance_m",
  "target_count",
  "signal_strength",
  "confidence",
  "point_cloud",
  "target_coords",
];

export function isRadarFieldSupported(field) {
  // The current C4001 telemetry contract (RadarStatusPayload) carries only:
  //   presence, motion, stillness_ms, possible_collapse
  const SUPPORTED = new Set(["presence", "motion", "stillness_ms", "possible_collapse"]);
  return SUPPORTED.has(field);
}

/** Human label for an unsupported field, used in the data cards. */
export function unsupportedFieldNote(field) {
  return "Not provided by current firmware";
}
