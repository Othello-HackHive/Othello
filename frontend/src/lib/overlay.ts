/* Backend v1 integration (file-based).
 * The backend writes two files per plan:
 *   <plan_id>.json         — official Slide 8/8 output
 *   <plan_id>.overlay.json — extra drawing data + checker summary (schema_version field)
 * This module parses both tolerantly. Coordinates in the files are expected in
 * metres; anything that looks like drawing units (≫ plan bounds) is converted.
 */

import type { PlanModel, Vec2 } from "./resplan";
import type { HubResult, RuleCheck, Solution } from "./optimizer";

export interface OverlayDraw {
  schemaVersion: number | null;
  planId: string | null;
  walls: Vec2[][] | null;
  windows: Vec2[][];
  openings: Array<{ a: string; b: string; from: Vec2; to: Vec2 }>;
  nodes: Array<{ id: string; point: Vec2 }>;
  hubs: Array<{ id: string; point: Vec2 }>;
  legs: Vec2[][];
  violations: Array<{ kind: "point" | "polyline" | "polygon"; pts: Vec2[] }>;
  crossings: Vec2[];
  overlaps: Vec2[];
  bends: Vec2[];
  metrics: { L: number; X: number; O: number; B: number; J: number; areaGap: number } | null;
  rules: Record<string, boolean> | null;
  checks: RuleCheck[];
  status: string | null;
  valid: boolean | null;
  provenMinimum: boolean | null;
  lowerBoundHubs: number | null;
  notes: string[];
  warnings: string[];
}

const toNum = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);
const values = (raw: unknown): unknown[] => (Array.isArray(raw) ? raw : raw && typeof raw === "object" ? Object.values(raw) : []);

const toVec = (value: unknown): Vec2 | null => {
  if (Array.isArray(value) && value.length >= 2) {
    const x = toNum(value[0]);
    const y = toNum(value[1]);
    return x !== null && y !== null ? [x, y] : null;
  }
  if (value && typeof value === "object") {
    const bag = value as { x?: unknown; y?: unknown; point?: unknown };
    const direct = toNum(bag.x) !== null && toNum(bag.y) !== null ? [toNum(bag.x) as number, toNum(bag.y) as number] : null;
    const nested = bag.point ? toVec(bag.point) : null;
    return (direct as Vec2 | null) ?? nested;
  }
  return null;
};

const HUB_PALETTE = ["#2563eb", "#f59e0b", "#10b981", "#8b5cf6", "#f43f5e"];

/** Heuristic: drawing-unit coordinates (much larger than the plan in metres) → metres. */
function meterize(points: Vec2[], plan: PlanModel, warnings: string[], tag: string): Vec2[] {
  const maxCoord = Math.max(plan.bounds.max[0], plan.bounds.max[1], 1);
  const scaleNeeded = points.some((p) => Math.max(Math.abs(p[0]), Math.abs(p[1])) > maxCoord * 1.5);
  if (!scaleNeeded) return points;
  const converted = points.map(([x, y]) => [x * plan.mPerUnit, y * plan.mPerUnit] as Vec2);
  if (!warnings.some((w) => w.startsWith(`${tag} in units`))) warnings.push(`${tag} coordinates look like drawing units — converted with plan scale`);
  return converted;
}

const ringFromList = (raw: unknown): Vec2[] | null => {
  if (Array.isArray(raw)) {
    const direct = raw.map(toVec).filter(Boolean) as Vec2[];
    if (direct.length >= 2) return direct;
    for (const child of raw) {
      const found = ringFromList(child);
      if (found) return found;
    }
    return null;
  }
  if (raw && typeof raw === "object") {
    const bag = raw as Record<string, unknown>;
    for (const key of ["polygon", "points", "ring", "segment", "contour", "contours", "polyline", "geometry"]) {
      if (key in bag) {
        const found = ringFromList(bag[key]);
        if (found) return found;
      }
    }
    const point = toVec(bag);
    return point ? [point] : null;
  }
  return null;
};

const polylineList = (raw: unknown): Vec2[][] => {
  if (raw == null) return [];
  const bare = ringFromList(raw);
  const isBareRing = Array.isArray(raw) && raw.length >= 2 && raw.every((item) => toVec(item) !== null);
  if (bare && isBareRing) return [bare];
  return values(raw)
    .map(ringFromList)
    .filter((points): points is Vec2[] => points !== null && points.length >= 2);
};

const pointList = (raw: unknown): Vec2[] =>
  values(raw)
    .map((entry) => toVec(entry))
    .filter(Boolean) as Vec2[];

export function parseOverlay(json: Record<string, unknown>, plan: PlanModel): OverlayDraw {
  const warnings: string[] = [];
  const numOrNull = (value: unknown) => toNum(value);
  const geometry = (json.geometry ?? {}) as Record<string, unknown>;

  const violationsRaw = values(json.violations);
  const violations = violationsRaw.map((entry): { kind: "point" | "polyline" | "polygon"; pts: Vec2[] } => {
    if (Array.isArray(entry)) {
      const flat = values(entry).map(toVec).filter(Boolean) as Vec2[];
      const kind: "point" | "polyline" | "polygon" = flat.length <= 1 ? "point" : flat.length <= 2 ? "polyline" : "polygon";
      return { kind, pts: flat };
    }
    const bag = (entry ?? {}) as Record<string, unknown>;
    const point = toVec(bag.point ?? bag.location);
    const polyline = polylineList(bag.polyline ?? bag.segment);
    const polygon = polylineList(bag.polygon);
    if (point) return { kind: "point", pts: [point] };
    if (polyline[0]) return { kind: "polyline", pts: polyline[0] };
    if (polygon[0]) return { kind: "polygon", pts: polygon[0] };
    return { kind: "point", pts: [plan.bounds.center] };
  });

  const nodes = values(json.nodes).map((entry, index): { id: string; point: Vec2 } => {
    if (entry && typeof entry === "object" && !Array.isArray(entry)) {
      const bag = entry as Record<string, unknown>;
      return { id: String(bag.id ?? bag.node_id ?? `N${index + 1}`), point: toVec(bag.point ?? bag.location ?? [bag.x, bag.y]) ?? plan.bounds.center };
    }
    return { id: `N${index + 1}`, point: toVec(entry) ?? plan.bounds.center };
  });

  const hubs = values(json.hubs).map((entry, index): { id: string; point: Vec2 } => {
    if (entry && typeof entry === "object" && !Array.isArray(entry)) {
      const bag = entry as Record<string, unknown>;
      return { id: String(bag.id ?? bag.hub_id ?? `H${index + 1}`), point: toVec(bag.point ?? bag.location) ?? plan.bounds.center };
    }
    return { id: `H${index + 1}`, point: toVec(entry) ?? plan.bounds.center };
  });

  const openings = values(json.openings ?? geometry.openings)
    .map((entry) => {
      const bag = (entry ?? {}) as Record<string, unknown>;
      const seg = polylineList(bag.gap ?? bag.polygon ?? bag.segment ?? entry);
      const rooms = values(bag.rooms);
      const a = String(bag.a ?? bag.from ?? bag.room_a ?? rooms[0] ?? "");
      const b = String(bag.b ?? bag.to ?? bag.room_b ?? rooms[1] ?? "");
      return seg[0] ? { a, b, from: seg[0][0], to: seg[0][1] } : null;
    })
    .filter(Boolean) as Array<{ a: string; b: string; from: Vec2; to: Vec2 }>;

  const metricsBag = (json.metrics ?? {}) as Record<string, unknown>;
  const metrics =
    numOrNull(metricsBag.J) !== null || numOrNull(metricsBag.cost_J) !== null
      ? {
          L: numOrNull(metricsBag.L) ?? numOrNull(metricsBag.total_length_m) ?? 0,
          X: numOrNull(metricsBag.X) ?? numOrNull(metricsBag.crossings) ?? 0,
          O: numOrNull(metricsBag.O) ?? numOrNull(metricsBag.overlap_m) ?? 0,
          B: numOrNull(metricsBag.B) ?? numOrNull(metricsBag.bends) ?? 0,
          J: numOrNull(metricsBag.J) ?? numOrNull(metricsBag.cost_J) ?? 0,
          areaGap: numOrNull(metricsBag.area_gap_m2) ?? numOrNull(metricsBag.area_gap) ?? numOrNull(metricsBag.areaGap) ?? 0,
        }
      : null;

  const checks = values(json.checks).filter((item) => item && typeof item === "object" && !Array.isArray(item)).map((item) => {
    const check = item as Record<string, unknown>;
    return {
      id: String(check.rule_id ?? check.id ?? "unknown"),
      label: String(check.label ?? check.rule_id ?? "CHECK"),
      pass: check.passed === true,
      value: String(check.value ?? (check.passed === true ? "PASS" : "FAIL")),
      detail: String(check.detail ?? ""),
    };
  });
  const rulesRaw = json.rules ?? json.rule_summary;
  const rules = rulesRaw && typeof rulesRaw === "object" && !Array.isArray(rulesRaw)
    ? Object.fromEntries(Object.entries(rulesRaw).map(([k, v]) => [k, v === true])) : null;
  const boundsReport = (json.bounds_report ?? {}) as Record<string, unknown>;

  const raw = {
    schemaVersion: toNum(json.schema_version) ?? toNum(json.schemaVersion),
    planId: (json.plan_id ?? json.planId ?? null) as string | null,
    walls: polylineList(json.walls ?? geometry.walls),
    windows: polylineList(json.windows ?? geometry.windows),
    openings,
    nodes,
    hubs,
    legs: polylineList(json.legs ?? json.extra_legs),
    violations,
    crossings: pointList(json.crossings ?? (json.markers as { crossings?: unknown } | undefined)?.crossings),
    overlaps: pointList(json.overlaps ?? (json.markers as { overlaps?: unknown } | undefined)?.overlaps),
    bends: pointList(json.bends ?? (json.markers as { bends?: unknown } | undefined)?.bends),
    metrics,
    rules,
    checks,
    status: typeof json.status === "string" ? json.status : null,
    valid: typeof json.valid === "boolean" ? json.valid : null,
    provenMinimum: typeof boundsReport.proven_minimum === "boolean" ? boundsReport.proven_minimum : null,
    lowerBoundHubs: toNum(boundsReport.lower_bound_hubs),
    notes: values(json.notes).filter((item): item is string => typeof item === "string"),
  };

  const conv = (points: Vec2[], tag: string) => meterize(points, plan, warnings, tag);
  return {
    ...raw,
    walls: raw.walls.length ? raw.walls.map((w) => conv(w, "walls")) : null,
    windows: raw.windows.map((w) => conv(w, "windows")),
    openings: raw.openings.map((o) => ({ ...o, from: conv([o.from], "openings")[0], to: conv([o.to], "openings")[0] })),
    nodes: raw.nodes.map((n) => ({ ...n, point: conv([n.point], "nodes")[0] })),
    hubs: raw.hubs.map((h) => ({ ...h, point: conv([h.point], "hubs")[0] })),
    legs: raw.legs.map((l) => conv(l, "legs")),
    violations: raw.violations.map((v) => ({ ...v, pts: conv(v.pts, "violations") })),
    crossings: conv(raw.crossings, "crossings"),
    overlaps: conv(raw.overlaps, "overlaps"),
    bends: conv(raw.bends, "bends"),
    warnings,
  };
}

/* ------------------------------------------- official JSON → Solution */

export function solutionFromOfficial(
  json: Record<string, unknown>,
  rawText: string,
  plan: PlanModel,
  overlay: OverlayDraw | null,
): Solution {
  const warnings: string[] = [];
  const toM2 = (p: Vec2): Vec2 => (Math.max(Math.abs(p[0]), Math.abs(p[1])) > plan.bounds.max[0] * 1.5 ? [p[0] * plan.mPerUnit, p[1] * plan.mPerUnit] : p);

  const hubBags = values(json.hubs).map((entry) => (entry ?? {}) as Record<string, unknown>);
  const hubs: HubResult[] = hubBags.map((bag, index) => {
    const hubId = String(bag.hub_id ?? `H${index + 1}`);
    const location = toM2(toVec((bag.location as Record<string, unknown>) ? bag.location : undefined) ?? toVec([bag.x, bag.y]) ?? plan.bounds.center);
    const roomBag = (bag.route_segments ?? []) as unknown;
    void roomBag;
    const hostRoom =
      plan.rooms.find((room) => room.id === String(bag.room_id ?? "").toUpperCase()) ??
      plan.rooms.reduce((best, room) => {
        const d = (p: Vec2, q: Vec2) => Math.hypot(p[0] - q[0], p[1] - q[1]);
        return d(room.node, location) < d(best.node, location) ? room : best;
      }, plan.rooms[0]);
    const segments = values(bag.route_segments).map((entry, segIndex) => {
      const seg = (entry ?? {}) as Record<string, unknown>;
      const polyline = pointList(seg.polyline).map(toM2);
      return {
        from: String(seg.from ?? ""),
        to: String(seg.to ?? ""),
        length_m: toNum(seg.length_m) ?? 0,
        polyline,
        laneY: 0.16 + (segIndex % 3) * 0.1,
      };
    });
    const assignedRooms = values(bag.assigned_rooms).map((room) => String(room).toUpperCase());
    const area = assignedRooms.reduce((acc, id) => acc + (plan.rooms.find((room) => room.id === id)?.areaM2 ?? 0), 0);
    const nodePoints: Record<string, Vec2> = {};
    overlay?.nodes.forEach((node) => {
      nodePoints[node.id.toUpperCase()] = node.point;
    });
    const ringLength =
      toNum(bag.ring_length_m) ??
      (segments.reduce((acc, seg) => acc + seg.length_m, 0) ||
        segments.reduce((acc, seg) => {
          const pts = seg.polyline;
          for (let i = 1; i < pts.length; i += 1) acc += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
          return acc;
        }, 0));
    return {
      hub_id: hubId,
      room_id: hostRoom.id,
      location,
      assigned_rooms: assignedRooms.length ? assignedRooms : [hostRoom.id],
      assigned_nodes: values(bag.assigned_nodes).map(String),
      closed_sequence: values(bag.closed_sequence).map(String),
      route_segments: segments,
      ring_length_m: Number(ringLength.toFixed(3)),
      area_m2: Number(area.toFixed(2)),
      color: HUB_PALETTE[index % HUB_PALETTE.length],
      nodePoints,
    };
  });

  const reported = (json.reported_metrics ?? {}) as Record<string, unknown>;
  const overlayMetrics = overlay?.metrics;
  const L = overlayMetrics?.L ?? toNum(reported.total_length_m) ?? hubs.reduce((acc, h) => acc + h.ring_length_m, 0);
  const X = overlayMetrics?.X ?? 0;
  const O = overlayMetrics?.O ?? 0;
  const B = overlayMetrics?.B ?? 0;
  const J = overlayMetrics?.J ?? toNum(reported.cost_J) ?? L + 5 * X + O + 0.5 * B;
  const areas = hubs.map((hub) => hub.area_m2).sort((a, b) => b - a);
  const areaGap = overlayMetrics?.areaGap ?? (areas.length > 1 ? areas[0] - areas[areas.length - 1] : 0);

  const ruleDefs: Array<{ id: string; label: string; detail: string; aliases: string[] }> = [
    { id: "coverage", label: "COVERAGE", detail: "every eligible room & node assigned", aliases: ["coverage", "full_coverage"] },
    { id: "capacity", label: "CAPACITY", detail: "≤5 nodes · ≤100 m² per hub", aliases: ["capacity", "limits"] },
    { id: "contiguity", label: "ZONE CONTIGUITY", detail: "districts linked by doors only", aliases: ["contiguity", "zone_contiguity"] },
    { id: "ring", label: "RING CLOSURE", detail: "closed non-branching cycles", aliases: ["ring", "ring_closure", "closed_rings"] },
    { id: "axis", label: "FREE SPACE · AXIS-ALIGNED", detail: "H/V runs inside inner boundary", aliases: ["axis", "axis_aligned", "free_space"] },
    { id: "clearance", label: "HUB CLEARANCE", detail: "≥ 0.5 m from walls", aliases: ["clearance", "hub_clearance"] },
  ];
  const rules = overlay?.rules;
  const checks: RuleCheck[] = overlay?.checks.length ? overlay.checks : ruleDefs.map((rule) => {
    const known = rules ? rule.aliases.some((alias) => alias in rules) : false;
    const pass = known ? (rules as Record<string, boolean>)[rule.aliases.find((alias) => alias in (rules as Record<string, boolean>)) ?? ""] : false;
    return {
      id: rule.id,
      label: rule.label,
      pass,
      na: !known,
      value: known ? (pass ? "PASS" : "FAIL") : "CHECKER",
      detail: rule.detail,
    };
  });

  if (hubs.length !== plan.eligibleRooms.length && hubs.length === 0) {
    warnings.push("official result lists no hubs — check plan_id match");
  }

  const output: Record<string, unknown> = {
    plan_id: String(json.plan_id ?? plan.plan_id),
    units: "m",
    hubs: hubs.map((hub) => ({
      hub_id: hub.hub_id,
      room_id: hub.room_id,
      location: { x: hub.location[0], y: hub.location[1] },
      assigned_rooms: hub.assigned_rooms,
      assigned_nodes: hub.assigned_nodes,
      closed_sequence: hub.closed_sequence,
      route_segments: hub.route_segments.map((seg) => ({
        from: seg.from,
        to: seg.to,
        length_m: seg.length_m,
        polyline: seg.polyline,
      })),
      ring_length_m: hub.ring_length_m,
    })),
    reported_metrics: { hub_count: hubs.length, total_length_m: Number(L.toFixed(2)), cost_J: Number(J.toFixed(2)) },
  };

  return {
    plan_id: String(json.plan_id ?? plan.plan_id),
    units: "m",
    hubs,
    reported_metrics: { hub_count: hubs.length, total_length_m: Number(L.toFixed(2)), cost_J: Number(J.toFixed(2)) },
    metrics: {
      L,
      X,
      O,
      B,
      J,
      areaGap,
      hubLowerBound: overlay?.lowerBoundHubs ?? Math.max(Math.ceil(plan.eligibleAreaM2 / 100), Math.ceil(plan.eligibleRooms.length / 5), 1),
      nodeCount: plan.eligibleRooms.length,
      eligibleArea: Number(plan.eligibleAreaM2.toFixed(2)),
    },
    checks,
    iterations: 0,
    output,
    source: "backend",
    rawText,
  };
}
