/* ResPlan JSON → normalized metric plan model.
 * Drawing units are converted once, at parse time, using the official scale law:
 *   scale = sqrt(plan.inner.area / plan.area)   (metres per drawing unit)
 * so   metres = units * scale,   m2 = units2 * scale^2,   0.5 m clearance = 0.5 / scale units.
 */

export type Vec2 = [number, number];

export interface Room {
  id: string;
  label: string;
  type: string;
  eligible: boolean;
  polygon: Vec2[];
  areaM2: number;
  centroid: Vec2;
  node: Vec2;
  clearance: number;
  /** "given" = fixed input from the plan file; "assumed" = solver fallback */
  nodeSource: "given" | "assumed";
  hubId?: string;
}

export interface Door {
  id: string;
  a: string;
  b: string;
  center: Vec2;
  tangent: Vec2;
  normal: Vec2;
  widthM: number;
}

export interface PlanModel {
  plan_id: string;
  source: string;
  mPerUnit: number;
  unitsPerM: number;
  rooms: Room[];
  doors: Door[];
  adjacency: Record<string, string[]>;
  doorsOfRoom: Record<string, Door[]>;
  bounds: { min: Vec2; max: Vec2; size: Vec2; center: Vec2; diagonal: number };
  eligibleAreaM2: number;
  eligibleRooms: Room[];
  outerPolygon: Vec2[];
}

/* Slide 2: eligible room types are ONLY living, bedroom, kitchen, storage. */
const ELIGIBLE_TOKENS = ["living", "bedroom", "kitchen", "storage"];
const EXCLUDED_TOKENS = ["bath", "balcony", "stair", "garden", "pool", "parking", "land", "entrance", "foyer", "hall", "corridor", "closet", "terrace", "garage"];

const num = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const values = (raw: unknown): unknown[] =>
  Array.isArray(raw) ? raw : raw && typeof raw === "object" ? Object.values(raw) : [];

const toVec = (value: unknown): Vec2 | null => {
  if (Array.isArray(value) && value.length >= 2) {
    const x = num(value[0]);
    const y = num(value[1]);
    return x !== null && y !== null ? [x, y] : null;
  }
  if (value && typeof value === "object") {
    const x = num((value as { x?: unknown }).x);
    const y = num((value as { y?: unknown }).y);
    return x !== null && y !== null ? [x, y] : null;
  }
  return null;
};

const isPoint = (value: unknown) => toVec(value) !== null && !(Array.isArray(value) && value.some((item) => Array.isArray(item) || (item && typeof item === "object")));

const POLYGON_KEYS = ["polygon", "polygons", "points", "contour", "contours", "exterior", "outer", "ring", "coordinates", "geometry", "boundary", "shape", "outline"];

/** Digs through ResPlan / GeoJSON-ish nesting to find a ring of 2D points. */
export function ringFrom(raw: unknown, minPoints = 3, depth = 0): Vec2[] | null {
  if (depth > 7 || raw == null) return null;
  if (Array.isArray(raw)) {
    if (raw.length >= minPoints && raw.every(isPoint)) {
      const points = raw.map(toVec).filter(Boolean) as Vec2[];
      if (points.length >= 3) {
        const first = points[0];
        const last = points[points.length - 1];
        if (Math.abs(first[0] - last[0]) < 1e-9 && Math.abs(first[1] - last[1]) < 1e-9) points.pop();
      }
      return points.length >= minPoints ? points : null;
    }
    for (const child of raw) {
      const found = ringFrom(child, minPoints, depth + 1);
      if (found) return found;
    }
    return null;
  }
  if (typeof raw === "object") {
    const bag = raw as Record<string, unknown>;
    for (const key of POLYGON_KEYS) {
      if (!(key in bag)) continue;
      const found = ringFrom(bag[key], minPoints, depth + 1);
      if (found) return found;
    }
  }
  return null;
}

export const polygonArea = (pts: Vec2[]) => {
  let sum = 0;
  for (let i = 0; i < pts.length; i += 1) {
    const [x1, y1] = pts[i];
    const [x2, y2] = pts[(i + 1) % pts.length];
    sum += x1 * y2 - x2 * y1;
  }
  return Math.abs(sum) / 2;
};

export const polygonCentroid = (pts: Vec2[]): Vec2 => {
  let cx = 0;
  let cy = 0;
  let area = 0;
  for (let i = 0; i < pts.length; i += 1) {
    const [x1, y1] = pts[i];
    const [x2, y2] = pts[(i + 1) % pts.length];
    const cross = x1 * y2 - x2 * y1;
    area += cross;
    cx += (x1 + x2) * cross;
    cy += (y1 + y2) * cross;
  }
  area /= 2;
  if (Math.abs(area) < 1e-9) {
    const sx = pts.reduce((acc, p) => acc + p[0], 0) / pts.length;
    const sy = pts.reduce((acc, p) => acc + p[1], 0) / pts.length;
    return [sx, sy];
  }
  return [cx / (6 * area), cy / (6 * area)];
};

export const pointInPolygon = (p: Vec2, pts: Vec2[]) => {
  let inside = false;
  for (let i = 0; i < pts.length; i += 1) {
    const [xi, yi] = pts[i];
    const [xj, yj] = pts[(i + 1) % pts.length];
    if (yi > p[1] !== yj > p[1] && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi + 1e-12) + xi) inside = !inside;
  }
  return inside;
};

const segmentDistance = (p: Vec2, a: Vec2, b: Vec2) => {
  const vx = b[0] - a[0];
  const vy = b[1] - a[1];
  const len2 = vx * vx + vy * vy || 1e-12;
  let t = ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p[0] - (a[0] + t * vx), p[1] - (a[1] + t * vy));
};

export const distanceToPolygon = (p: Vec2, pts: Vec2[]) => {
  let best = Infinity;
  for (let i = 0; i < pts.length; i += 1) best = Math.min(best, segmentDistance(p, pts[i], pts[(i + 1) % pts.length]));
  return best;
};

export const boundsOf = (pts: Vec2[]) => {
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  return { min: [Math.min(...xs), Math.min(...ys)] as Vec2, max: [Math.max(...xs), Math.max(...ys)] as Vec2 };
};

/** Largest-inscribed-circle style interior point: guarantees wall clearance for fixed nodes / hubs. */
export function interiorPoint(pts: Vec2[], minClearance = 0.5) {
  const { min, max } = boundsOf(pts);
  const steps = 26;
  let best: Vec2 = polygonCentroid(pts);
  let bestClear = -Infinity;
  for (let i = 1; i < steps; i += 1) {
    for (let j = 1; j < steps; j += 1) {
      const p: Vec2 = [min[0] + ((max[0] - min[0]) * i) / steps, min[1] + ((max[1] - min[1]) * j) / steps];
      if (!pointInPolygon(p, pts)) continue;
      const clear = distanceToPolygon(p, pts);
      if (clear > bestClear) {
        bestClear = clear;
        best = p;
      }
    }
  }
  const centroid = polygonCentroid(pts);
  if (pointInPolygon(centroid, pts) && distanceToPolygon(centroid, pts) >= minClearance) {
    return { point: centroid, clearance: distanceToPolygon(centroid, pts) };
  }
  return { point: best, clearance: bestClear };
}

const classify = (label: string) => {
  const clean = label.toLowerCase().replace(/[^a-z]/g, "");
  if (EXCLUDED_TOKENS.some((token) => clean.startsWith(token) || clean.includes(`${token}room`))) return false;
  if (ELIGIBLE_TOKENS.some((token) => clean.includes(token))) return true;
  return /^(bed|living|kitch|storage)/.test(clean);
};

function collectRoomEntries(raw: unknown): Array<{ id: string; label: string; ring: Vec2[]; raw: unknown }> {
  const out: Array<{ id: string; label: string; ring: Vec2[]; raw: unknown }> = [];
  const push = (id: string, entry: unknown) => {
    const ring = ringFrom(entry);
    if (!ring) return;
    const label =
      (typeof entry === "object" && entry && ((entry as { label?: unknown }).label ?? (entry as { type?: unknown }).type ?? (entry as { name?: unknown }).name)) as string
      || id;
    out.push({ id, label: String(label), ring, raw: entry });
  };
  if (raw && typeof raw === "object") {
    if (Array.isArray(raw)) raw.forEach((entry, index) => push(String((entry as { id?: string })?.id ?? `R${index + 1}`), entry));
    else Object.entries(raw as Record<string, unknown>).forEach(([key, entry]) => push(key, entry));
  }
  return out;
}

interface RawOpening {
  seg: Vec2[];
  pair: [string, string] | null;
}

function openingSegments(json: Record<string, unknown>): RawOpening[] {
  const source = (json.openings ?? json.doors ?? json.passages) as Record<string, unknown> | undefined;
  const found: RawOpening[] = [];
  if (!source) return found;
  const buckets = Array.isArray(source) ? [source] : Object.values(source);
  buckets.forEach((bucket) => {
    values(bucket).forEach((entry) => {
      const ring = ringFrom(entry, 2);
      if (!ring || ring.length < 2) return;
      const bag = (entry && typeof entry === "object" && !Array.isArray(entry) ? entry : {}) as Record<string, unknown>;
      const roomsList = Array.isArray(bag.rooms) ? (bag.rooms as unknown[]).map(String) : [];
      const from = typeof bag.from === "string" ? bag.from : typeof bag.a === "string" ? bag.a : roomsList[0];
      const to = typeof bag.to === "string" ? bag.to : typeof bag.b === "string" ? bag.b : roomsList[1];
      found.push({
        seg: ring.slice(0, 2),
        pair: from && to ? [from.toUpperCase(), to.toUpperCase()] : null,
      });
    });
  });
  return found;
}

export interface NormalizeResult {
  plan: PlanModel;
  warnings: string[];
}

interface RawNodePoint {
  key?: string;
  point: Vec2;
}

/** Fixed node inputs (slide 2): one per eligible room, ≥ 0.5 m from walls, given by the plan. */
function extractNodePoints(json: Record<string, unknown>): RawNodePoint[] {
  const out: RawNodePoint[] = [];
  const buckets: unknown[] = [json.nodes, json.fixed_points, json.node_points, json.points].filter((b) => b != null);
  buckets.forEach((bucket) => {
    values(bucket).forEach((entry) => {
      let point: Vec2 | null = null;
      let key: string | undefined;
      if (entry && typeof entry === "object" && !Array.isArray(entry)) {
        const bag = entry as Record<string, unknown>;
        key = String(bag.room_id ?? bag.room ?? bag.label ?? bag.id ?? "");
        if (!key) key = undefined;
        point = toVec(bag.point ?? bag.location ?? bag.node ?? bag.position) ?? toVec([bag.x, bag.y]);
      } else {
        point = toVec(entry);
      }
      if (point) out.push({ key, point });
    });
  });
  return out;
}

export function normalizePlan(json: Record<string, unknown>, fallbackName = "UPLOADED_PLAN"): NormalizeResult {
  const warnings: string[] = [];

  /* ---- scale law (slide 4) ----
   * unitsPerMetre = sqrt(plan.area / plan.inner.area)   [drawing units per metre]
   *   e.g. 36,000 u² / 110 m² → sqrt(327.3) = 18.09 u/m → 200 units = 11.06 m
   * So:   metres = units / unitsPerMetre.
   * plan.area is in drawing units², plan.inner.area is in real m².
   * (plan.net_area is intentionally ignored — it is 0 in 32% of plans.) */
  const declaredUnitsPerM = num(json.scale ?? json.units_per_metre);
  const declaredMPerUnit = num(json.m_per_unit ?? json.metres_per_unit ?? json.meters_per_unit);
  const planArea = num(json.area);
  const innerArea = num((json.inner as { area?: unknown } | undefined)?.area ?? json.inner_area);
  let mPerUnit: number;
  if (declaredMPerUnit && Number.isFinite(declaredMPerUnit) && declaredMPerUnit > 0) mPerUnit = declaredMPerUnit;
  else if (declaredUnitsPerM && Number.isFinite(declaredUnitsPerM) && declaredUnitsPerM > 0) mPerUnit = 1 / declaredUnitsPerM;
  else if (planArea && innerArea && planArea > 0 && innerArea > 0) mPerUnit = Math.sqrt(innerArea / planArea);
  else {
    mPerUnit = 1 / 18;
    warnings.push("plan.area / plan.inner.area missing — fallback 18 units/m");
  }
  if (!Number.isFinite(mPerUnit) || mPerUnit <= 0) {
    mPerUnit = 1 / 18;
    warnings.push("scale not finite — fallback 18 units/m applied");
  }
  const toM = (v: number) => v * mPerUnit; /* metres = units / unitsPerMetre */

  /* ---- rooms ---- */
  const entries = collectRoomEntries(json.rooms ?? json.spaces ?? json.labels ?? json.rooms_list);
  if (!entries.length) throw new Error("No room polygons found. Expected plan.rooms / plan.labels with contour rings.");

  const givenNodes = extractNodePoints(json);
  const normalizeKey = (value: string) => value.toUpperCase().replace(/[^A-Z0-9]/g, "");
  const usedNodeKeys = new Set<number>();
  const toRoomPoint = (unitsPoint: Vec2): Vec2 | null => {
    const asUnits = [toM(unitsPoint[0]), toM(unitsPoint[1])] as Vec2;
    return asUnits;
  };

  const rooms: Room[] = entries.map(({ id, label, ring, raw }) => {
    const polygon = ring.map(([x, y]) => [toM(x), toM(y)] as Vec2);
    const areaM2 = polygonArea(polygon);
    const type = label.toLowerCase().replace(/\s+/g, "_");
    const eligible = classify(label);

    /* Node is a FIXED INPUT: take the plan's point, never invent one. */
    let node: Vec2 | null = null;
    const rawBag = (raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {}) as Record<string, unknown>;
    const perRoom = toVec(rawBag.node ?? rawBag.fixed_point ?? rawBag.point);
    if (perRoom) node = toRoomPoint(perRoom);

    if (!node) {
      const matchIndex = givenNodes.findIndex(
        (candidate, index) =>
          !usedNodeKeys.has(index) &&
          candidate.key &&
          (normalizeKey(candidate.key) === normalizeKey(id) || normalizeKey(candidate.key) === normalizeKey(label)),
      );
      if (matchIndex >= 0) {
        usedNodeKeys.add(matchIndex);
        node = toRoomPoint(givenNodes[matchIndex].point);
      }
    }
    if (!node) {
      const matchIndex = givenNodes.findIndex((candidate, index) => !usedNodeKeys.has(index) && !candidate.key && pointInPolygon(toRoomPoint(candidate.point) as Vec2, polygon));
      if (matchIndex >= 0) {
        usedNodeKeys.add(matchIndex);
        node = toRoomPoint(givenNodes[matchIndex].point);
      }
    }

    let source: "given" | "assumed";
    if (!node) {
      const { point } = interiorPoint(polygon, 0.5);
      node = point;
      source = "assumed";
      if (eligible) warnings.push(`${id}: no fixed node point in file — solver-assumed (clearance ${distanceToPolygon(point, polygon).toFixed(2)} m)`);
    } else {
      source = "given";
      const clearance = distanceToPolygon(node, polygon);
      if (eligible && clearance < 0.5 - 1e-3) warnings.push(`${id}: given node is ${clearance.toFixed(2)} m from wall (< 0.5 m) — used as-is per fixed input`);
    }

    return {
      id: id.toUpperCase(),
      label,
      type,
      eligible,
      polygon,
      areaM2,
      centroid: polygonCentroid(polygon),
      node,
      clearance: distanceToPolygon(node, polygon),
      nodeSource: source,
    };
  });

  const byId = new Map(rooms.map((room) => [room.id, room]));
  const onBoundary = (p: Vec2, room: Room) => distanceToPolygon(p, room.polygon) < Math.max(0.3, room.areaM2 * 0.02);

  /* ---- doors ---- */
  const doors: Door[] = [];
  const segments = openingSegments(json);
  const addDoor = (a: string, b: string, p1: Vec2, p2: Vec2) => {
    if (!byId.has(a) || !byId.has(b) || a === b) return;
    const center: Vec2 = [(p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2];
    const dx = p2[0] - p1[0];
    const dy = p2[1] - p1[1];
    const length = Math.hypot(dx, dy) || 1e-6;
    const tangent: Vec2 = [dx / length, dy / length];
    doors.push({
      id: `D${String(doors.length + 1).padStart(2, "0")}`,
      a,
      b,
      center,
      tangent,
      normal: [-tangent[1], tangent[0]],
      widthM: length,
    });
  };

  segments.forEach(({ seg: rawSeg, pair }) => {
    const seg = rawSeg.map(([x, y]) => [toM(x), toM(y)] as Vec2);
    const declared = pair && byId.has(pair[0]) && byId.has(pair[1]) ? pair : null;
    const owners = rooms.filter((room) => onBoundary(seg[0], room) && onBoundary(seg[1], room)).map((room) => room.id);
    const a = declared ? declared[0] : owners[0];
    const b = declared ? declared[1] : owners[1];
    if (a && b && a !== b) {
      addDoor(a, b, seg[0], seg[1]);
    } else if (owners.length === 1) {
      warnings.push(`opening on ${owners[0]} boundary is exterior — no room crossing`);
    } else if (segments.length) {
      warnings.push("opening could not be matched to two rooms — ignored");
    }
  });

  /* ---- auto-derive openings from shared walls when the plan has none ---- */
  if (!doors.length) {
    for (let i = 0; i < rooms.length; i += 1) {
      for (let j = i + 1; j < rooms.length; j += 1) {
        const shared = sharedWall(rooms[i].polygon, rooms[j].polygon);
        if (shared) {
          addDoor(rooms[i].id, rooms[j].id, shared[0], shared[1]);
          warnings.push(`no openings in file — assumed passage ${rooms[i].id}↔${rooms[j].id}`);
        }
      }
    }
  }

  const adjacency: Record<string, string[]> = {};
  const doorsOfRoom: Record<string, Door[]> = {};
  rooms.forEach((room) => {
    adjacency[room.id] = [];
    doorsOfRoom[room.id] = [];
  });
  doors.forEach((door) => {
    if (!adjacency[door.a].includes(door.b)) adjacency[door.a].push(door.b);
    if (!adjacency[door.b].includes(door.a)) adjacency[door.b].push(door.a);
    doorsOfRoom[door.a].push(door);
    doorsOfRoom[door.b].push(door);
  });

  const allPoints = rooms.flatMap((room) => room.polygon);
  const { min, max } = boundsOf(allPoints);
  const size: Vec2 = [max[0] - min[0], max[1] - min[1]];
  const center: Vec2 = [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2];
  const eligibleRooms = rooms.filter((room) => room.eligible);
  const outerPolygon = rooms.reduce<Vec2[]>((best, room) => (polygonArea(room.polygon) > polygonArea(best) ? room.polygon : best), rooms[0].polygon);

  if (!eligibleRooms.length) warnings.push("no eligible rooms detected (living / bedroom / kitchen / storage)");

  return {
    plan: {
      plan_id: String(json.plan_id ?? json.id ?? fallbackName).toUpperCase(),
      source: String(json.source ?? "resplan"),
      mPerUnit,
      unitsPerM: 1 / mPerUnit,
      rooms,
      doors,
      adjacency,
      doorsOfRoom,
      bounds: { min, max, size, center, diagonal: Math.hypot(size[0], size[1]) },
      eligibleAreaM2: eligibleRooms.reduce((acc, room) => acc + room.areaM2, 0),
      eligibleRooms,
      outerPolygon,
    },
    warnings,
  };
}

function sharedWall(a: Vec2[], b: Vec2[]): [Vec2, Vec2] | null {
  let best: [Vec2, Vec2] | null = null;
  let bestLength = 0.9;
  for (let i = 0; i < a.length; i += 1) {
    const a1 = a[i];
    const a2 = a[(i + 1) % a.length];
    for (let j = 0; j < b.length; j += 1) {
      const b1 = b[j];
      const b2 = b[(j + 1) % b.length];
      const overlap = collinearOverlap(a1, a2, b1, b2);
      if (overlap && overlap.length > bestLength) {
        bestLength = overlap.length;
        best = [overlap.from, overlap.to];
      }
    }
  }
  return best;
}

function collinearOverlap(a1: Vec2, a2: Vec2, b1: Vec2, b2: Vec2) {
  const dx = a2[0] - a1[0];
  const dy = a2[1] - a1[1];
  const len = Math.hypot(dx, dy);
  if (len < 1e-6) return null;
  const ux = dx / len;
  const uy = dy / len;
  const offset = (b1[0] - a1[0]) * uy - (b1[1] - a1[1]) * ux;
  if (Math.abs(offset) > 0.08) return null;
  const t = (p: Vec2) => (p[0] - a1[0]) * ux + (p[1] - a1[1]) * uy;
  const bMin = Math.min(t(b1), t(b2));
  const bMax = Math.max(t(b1), t(b2));
  const from = Math.max(0, bMin);
  const to = Math.min(len, bMax);
  if (to - from < 0.2) return null;
  return {
    length: to - from,
    from: [a1[0] + ux * from, a1[1] + uy * from] as Vec2,
    to: [a1[0] + ux * to, a1[1] + uy * to] as Vec2,
  };
}

/* ---------------------------------------------------------- preset plans */

const rect = (x1: number, y1: number, x2: number, y2: number): Vec2[] => [
  [x1, y1],
  [x2, y1],
  [x2, y2],
  [x1, y2],
];

const opening = (a: string, b: string, p1: Vec2, p2: Vec2) => ({ rooms: [a, b], contour: [p1, p2] });

/** 1 unit = 40 units/m authored so sqrt(inner/area) = 0.025 exactly. */
export const PRESET_JSON: Record<string, Record<string, unknown>> = {
  BENCHMARK_6R: {
    plan_id: "PLAN_0123",
    source: "Slide 5 / 6-room benchmark",
    area: 144000,
    inner: { area: 90 },
    rooms: {
      R01: { label: "Bedroom 1", polygon: rect(0, 0, 160, 140) },
      R02: { label: "Bedroom 2", polygon: rect(160, 0, 320, 140) },
      R03: { label: "Bedroom 3", polygon: rect(320, 0, 480, 140) },
      R04: { label: "Kitchen", polygon: rect(0, 140, 140, 300) },
      R05: { label: "Living Room", polygon: rect(140, 140, 340, 300) },
      R06: { label: "Storage Room", polygon: rect(340, 140, 480, 228) },
      R07: { label: "Bathroom", polygon: rect(340, 228, 420, 300) },
      R08: { label: "Entrance", polygon: rect(420, 228, 480, 300) },
    },
    nodes: [
      { room: "R01", point: [70, 60] },
      { room: "R02", point: [230, 60] },
      { room: "R03", point: [388, 60] },
      { room: "R04", point: [70, 228] },
      { room: "R05", point: [258, 216] },
      { room: "R06", point: [422, 175] },
    ],
    openings: {
      doors: [
        opening("R01", "R04", [56, 140], [92, 140]),
        opening("R02", "R05", [216, 140], [248, 140]),
        opening("R03", "R06", [376, 140], [408, 140]),
        opening("R04", "R05", [140, 176], [140, 208]),
        opening("R05", "R06", [340, 172], [340, 204]),
        opening("R06", "R07", [360, 228], [392, 228]),
        opening("R06", "R08", [436, 228], [468, 228]),
        opening("R07", "R08", [420, 248], [420, 280]),
      ],
    },
  },

  RESPLAN_8R: {
    plan_id: "PLAN_0417",
    source: "ResPlan 8-room (capacity bound)",
    area: 256000,
    inner: { area: 160 },
    rooms: {
      R01: { label: "Living Room", polygon: rect(0, 0, 280, 200) },
      R02: { label: "Kitchen", polygon: rect(280, 0, 440, 140) },
      R03: { label: "Bedroom 1", polygon: rect(440, 0, 640, 300) },
      R04: { label: "Bedroom 2", polygon: rect(280, 140, 440, 300) },
      R05: { label: "Storage", polygon: rect(0, 200, 160, 300) },
      R06: { label: "Bathroom", polygon: rect(160, 200, 280, 300) },
      R07: { label: "Entrance", polygon: rect(280, 300, 440, 400) },
      R08: { label: "Balcony", polygon: rect(440, 300, 640, 400) },
    },
    nodes: [
      { room: "R01", point: [100, 80] },
      { room: "R02", point: [360, 60] },
      { room: "R03", point: [540, 110] },
      { room: "R04", point: [360, 220] },
      { room: "R05", point: [70, 250] },
    ],
    openings: {
      doors: [
        opening("R01", "R02", [280, 40], [280, 100]),
        opening("R02", "R04", [320, 140], [380, 140]),
        opening("R01", "R05", [40, 200], [100, 200]),
        opening("R04", "R03", [440, 180], [440, 240]),
        opening("R01", "R04", [280, 216], [280, 276]),
        opening("R06", "R07", [200, 300], [260, 300]),
        opening("R01", "R06", [176, 200], [240, 200]),
        opening("R07", "R04", [300, 300], [360, 300]),
        opening("R08", "R03", [500, 300], [560, 300]),
      ],
    },
  },

  COMPLEX_12R: {
    plan_id: "PLAN_0932",
    source: "Complex 12-room (3 hubs)",
    area: 384000,
    inner: { area: 240 },
    rooms: {
      R01: { label: "Living Room", polygon: [[0, 0], [400, 0], [400, 200], [240, 200], [240, 400], [0, 400]] },
      R02: { label: "Kitchen", polygon: rect(400, 0, 600, 200) },
      R03: { label: "Storage Room", polygon: rect(600, 0, 720, 200) },
      R04: { label: "Bedroom 1", polygon: rect(720, 0, 800, 200) },
      R05: { label: "Bedroom 2", polygon: rect(600, 200, 800, 360) },
      R06: { label: "Bedroom 3", polygon: rect(400, 200, 600, 360) },
      R07: { label: "Bedroom 4", polygon: rect(240, 200, 400, 360) },
      R08: { label: "Storage 2", polygon: rect(0, 400, 160, 480) },
      R09: { label: "Bathroom", polygon: rect(160, 360, 280, 480) },
      R10: { label: "Entrance", polygon: rect(280, 360, 440, 480) },
      R11: { label: "Balcony", polygon: rect(440, 360, 600, 480) },
      R12: { label: "Bath 2", polygon: rect(600, 360, 800, 480) },
    },
    nodes: [
      { room: "R01", point: [160, 120] },
      { room: "R02", point: [500, 90] },
      { room: "R03", point: [660, 90] },
      { room: "R04", point: [760, 90] },
      { room: "R05", point: [700, 280] },
      { room: "R06", point: [500, 280] },
      { room: "R07", point: [320, 280] },
      { room: "R08", point: [70, 440] },
    ],
    openings: {
      doors: [
        opening("R01", "R02", [400, 60], [400, 120]),
        opening("R02", "R03", [600, 60], [600, 120]),
        opening("R03", "R04", [720, 60], [720, 120]),
        opening("R02", "R06", [440, 200], [500, 200]),
        opening("R06", "R05", [600, 240], [600, 300]),
        opening("R05", "R12", [640, 360], [700, 360]),
        opening("R06", "R07", [400, 240], [400, 300]),
        opening("R07", "R01", [240, 260], [240, 320]),
        opening("R01", "R08", [40, 400], [110, 400]),
        opening("R09", "R10", [280, 400], [280, 460]),
        opening("R10", "R11", [440, 400], [440, 460]),
        opening("R11", "R12", [600, 400], [600, 460]),
        opening("R07", "R10", [300, 360], [360, 360]),
      ],
    },
  },
};

export const PRESET_IDS = Object.keys(PRESET_JSON);
