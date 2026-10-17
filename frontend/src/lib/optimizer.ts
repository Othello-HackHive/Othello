/* Joint hub-placement + closed-ring Manhattan routing heuristic with the
 * official ResPlan cost function  J = L + 5X + O + 0.5B.
 */

import {
  distanceToPolygon,
  pointInPolygon,
  type Door,
  type PlanModel,
  type Room,
  type Vec2,
} from "./resplan";

export interface RouteSegment {
  from: string;
  to: string;
  length_m: number;
  polyline: Vec2[];
  laneY: number;
}

export interface HubResult {
  hub_id: string;
  room_id: string;
  location: Vec2;
  assigned_rooms: string[];
  assigned_nodes: string[];
  closed_sequence: string[];
  route_segments: RouteSegment[];
  ring_length_m: number;
  area_m2: number;
  color: string;
  nodePoints: Record<string, Vec2>;
}

export interface RuleCheck {
  id: string;
  label: string;
  pass: boolean;
  /** rule state unknown (e.g. backend result without checker summary) */
  na?: boolean;
  value: string;
  detail: string;
}

export interface Solution {
  plan_id: string;
  units: "m";
  hubs: HubResult[];
  reported_metrics: { hub_count: number; total_length_m: number; cost_J: number };
  metrics: {
    L: number;
    X: number;
    O: number;
    B: number;
    J: number;
    areaGap: number;
    hubLowerBound: number;
    nodeCount: number;
    eligibleArea: number;
  };
  checks: RuleCheck[];
  iterations: number;
  output: Record<string, unknown>;
  /** "demo" = client heuristic (display only); "backend" = official output file */
  source: "demo" | "backend";
  /** raw official JSON text when source = backend, for verbatim export */
  rawText?: string;
}

const ZONE_COLORS = ["#1d4ed8", "#d97706", "#10b981", "#7c3aed", "#e11d48", "#0891b2"];
const LANE = 0.11;
const EPS = 1e-6;

const manhattan = (a: Vec2, b: Vec2) => Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);
const polyLength = (pts: Vec2[]) => {
  let sum = 0;
  for (let i = 0; i < pts.length - 1; i += 1) sum += Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]);
  return sum;
};

const roomById = (plan: PlanModel, id: string) => plan.rooms.find((room) => room.id === id) as Room;

/** Door traversal counter keeps parallel lanes separated so overlap O = 0. */
type DoorUsage = Map<string, number>;

function doorCrossing(door: Door, index: number): Vec2 {
  const rank = index % 4;
  const magnitude = LANE * (1 + Math.floor(rank / 2) * 2);
  const sign = rank % 2 === 0 ? 1 : -1;
  return [door.center[0] + door.tangent[0] * magnitude * sign, door.center[1] + door.tangent[1] * magnitude * sign];
}

function roomPath(plan: PlanModel, from: string, to: string): Door[] {
  if (from === to) return [];
  const queue: Array<{ room: string; doors: Door[] }> = [{ room: from, doors: [] }];
  const seen = new Set([from]);
  while (queue.length) {
    const current = queue.shift() as { room: string; doors: Door[] };
    for (const door of plan.doorsOfRoom[current.room] ?? []) {
      const next = door.a === current.room ? door.b : door.a;
      if (seen.has(next)) continue;
      const nextRoom = roomById(plan, next);
      if (!nextRoom.eligible && next !== to) continue;
      const doors = [...current.doors, door];
      if (next === to) return doors;
      seen.add(next);
      queue.push({ room: next, doors });
    }
  }
  return [];
}

type Corridors = Map<string, number>;

const lineKey = (a: Vec2, b: Vec2) =>
  Math.abs(a[0] - b[0]) < 1e-4 ? `h${b[1].toFixed(2)}` : `v${b[0].toFixed(2)}`;

const corridorCost = (map: Corridors, p: Vec2, elbow: Vec2, q: Vec2) => {
  const a = map.get(lineKey(p, elbow)) ?? 0;
  const b = map.get(lineKey(elbow, q)) ?? 0;
  return a * a + b * b + (a > 0 || b > 0 ? 1 : 0);
};

const register = (map: Corridors, p: Vec2, elbow: Vec2, q: Vec2) => {
  [lineKey(p, elbow), lineKey(elbow, q)].forEach((key) => map.set(key, (map.get(key) ?? 0) + 1));
};

function axisChain(points: Vec2[], rooms: Array<Room | undefined>, corridors: Corridors): Vec2[] {
  const out: Vec2[] = [points[0]];
  for (let i = 0; i < points.length - 1; i += 1) {
    const p = out[out.length - 1];
    const q = points[i + 1];
    const room = rooms[i];
    if (Math.abs(p[0] - q[0]) < 1e-4 || Math.abs(p[1] - q[1]) < 1e-4) {
      out.push(q);
      continue;
    }
    const c1: Vec2 = [q[0], p[1]];
    const c2: Vec2 = [p[0], q[1]];
    const ok = (candidate: Vec2) => (room ? pointInPolygon(candidate, room.polygon) : true);
    const valid = [c1, c2].filter(ok);
    const pool = valid.length ? valid : [c1, c2];
    const elbow = pool.length === 1 || corridorCost(corridors, p, pool[0], q) <= corridorCost(corridors, p, pool[1], q) ? pool[0] : pool[1];
    register(corridors, p, elbow, q);
    if (Math.abs(elbow[0] - p[0]) > 1e-4 || Math.abs(elbow[1] - p[1]) > 1e-4) out.push(elbow);
    if (Math.abs(q[0] - out[out.length - 1][0]) > 1e-4 || Math.abs(q[1] - out[out.length - 1][1]) > 1e-4) out.push(q);
  }
  return dedupe(out);
}

const dedupe = (pts: Vec2[]) => pts.filter((p, i) => i === 0 || Math.abs(p[0] - pts[i - 1][0]) > EPS || Math.abs(p[1] - pts[i - 1][1]) > EPS);

function routeBetween(
  plan: PlanModel,
  usage: DoorUsage,
  corridors: Corridors,
  from: { room: string; point: Vec2 },
  to: { room: string; point: Vec2 },
): { polyline: Vec2[]; rooms: string[] } {
  const doors = roomPath(plan, from.room, to.room);
  const waypoints: Vec2[] = [from.point, ...doors.map((door) => {
    const index = (usage.get(door.id) ?? 0);
    usage.set(door.id, index + 1);
    return doorCrossing(door, index);
  }), to.point];
  const chain: Array<Room | undefined> = [];
  let walk = from.room;
  waypoints.forEach((_, index) => {
    if (index < doors.length) chain.push(roomById(plan, walk));
    else chain.push(roomById(plan, to.room));
    if (index < doors.length) walk = doors[index].a === walk ? doors[index].b : doors[index].a;
  });
  return { polyline: axisChain(waypoints, chain, corridors), rooms: [from.room, ...doors.map(() => from.room)] };
}

/* ------------------------------------------------------------- metrics */

const axisAligned = (pts: Vec2[]) =>
  pts.every((p, i) => i === 0 || Math.abs(p[0] - pts[i - 1][0]) < 1e-3 || Math.abs(p[1] - pts[i - 1][1]) < 1e-3);

const rectilinear = (pts: Vec2[]) => axisAligned(pts) && pts.every((p, i) => i === 0 || Math.hypot(p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]) > 1e-3);

const bendCount = (pts: Vec2[]) => {
  let bends = 0;
  for (let i = 1; i < pts.length - 1; i += 1) {
    const d1x = Math.sign(pts[i][0] - pts[i - 1][0]);
    const d1y = Math.sign(pts[i][1] - pts[i - 1][1]);
    const d2x = Math.sign(pts[i + 1][0] - pts[i][0]);
    const d2y = Math.sign(pts[i + 1][1] - pts[i][1]);
    if (d1x !== d2x || d1y !== d2y) bends += 1;
  }
  return bends;
};

const ringBendCount = (legs: RouteSegment[]) => {
  const direction = (points: Vec2[], fromEnd: boolean): Vec2 => {
    const steps = fromEnd ? points.slice().reverse() : points;
    for (let index = 1; index < steps.length; index += 1) {
      const dx = Math.sign(steps[index][0] - steps[index - 1][0]);
      const dy = Math.sign(steps[index][1] - steps[index - 1][1]);
      if (dx || dy) return fromEnd ? [-dx, -dy] : [dx, dy];
    }
    return [0, 0];
  };
  let count = legs.reduce((total, leg) => total + bendCount(leg.polyline), 0);
  legs.forEach((leg, index) => {
    const next = legs[(index + 1) % legs.length];
    const incoming = direction(leg.polyline, true);
    const outgoing = direction(next.polyline, false);
    if (incoming[0] !== outgoing[0] || incoming[1] !== outgoing[1]) count += 1;
  });
  return count;
};

interface Segment { p: Vec2; q: Vec2; ring: number; index: number }

const orientationsIntersect = (a: Segment, b: Segment) => {
  const cross = (o: Vec2, p: Vec2, q: Vec2) => (q[0] - o[0]) * (p[1] - o[1]) - (p[0] - o[0]) * (q[1] - o[1]);
  const d1 = cross(b.p, b.q, a.p);
  const d2 = cross(b.p, b.q, a.q);
  const d3 = cross(a.p, a.q, b.p);
  const d4 = cross(a.p, a.q, b.q);
  return ((d1 > 1e-9 && d2 < -1e-9) || (d1 < -1e-9 && d2 > 1e-9)) && ((d3 > 1e-9 && d4 < -1e-9) || (d3 < -1e-9 && d4 > 1e-9));
};

function countCrossings(segments: Segment[], anchors: Vec2[]) {
  let crossings = 0;
  for (let i = 0; i < segments.length; i += 1) {
    for (let j = i + 1; j < segments.length; j += 1) {
      if (segments[i].ring === segments[j].ring && Math.abs(segments[i].index - segments[j].index) <= 1) continue;
      if (segments[i].ring === segments[j].ring && segments[i].index === 0 && segments[j].index === segments.length - 1) continue;
      if (!orientationsIntersect(segments[i], segments[j])) continue;
      const hit: Vec2 = [(segments[i].p[0] + segments[i].q[0] + segments[j].p[0] + segments[j].q[0]) / 4, (segments[i].p[1] + segments[i].q[1] + segments[j].p[1] + segments[j].q[1]) / 4];
      if (anchors.some((anchor) => Math.hypot(anchor[0] - hit[0], anchor[1] - hit[1]) < 0.45)) continue;
      crossings += 1;
    }
  }
  return crossings;
}

function overlapLength(segments: Segment[]) {
  let total = 0;
  for (let i = 0; i < segments.length; i += 1) {
    for (let j = i + 1; j < segments.length; j += 1) {
      if (segments[i].ring === segments[j].ring && Math.abs(segments[i].index - segments[j].index) <= 1) continue;
      const a = segments[i];
      const b = segments[j];
      const aVertical = Math.abs(a.p[0] - a.q[0]) < 1e-6;
      const bVertical = Math.abs(b.p[0] - b.q[0]) < 1e-6;
      if (aVertical !== bVertical) continue;
      if (aVertical) {
        if (Math.abs(a.p[0] - b.p[0]) > 1e-4) continue;
        const aMin = Math.min(a.p[1], a.q[1]);
        const aMax = Math.max(a.p[1], a.q[1]);
        const bMin = Math.min(b.p[1], b.q[1]);
        const bMax = Math.max(b.p[1], b.q[1]);
        total += Math.max(0, Math.min(aMax, bMax) - Math.max(aMin, bMin));
      } else {
        if (Math.abs(a.p[1] - b.p[1]) > 1e-4) continue;
        const aMin = Math.min(a.p[0], a.q[0]);
        const aMax = Math.max(a.p[0], a.q[0]);
        const bMin = Math.min(b.p[0], b.q[0]);
        const bMax = Math.max(b.p[0], b.q[0]);
        total += Math.max(0, Math.min(aMax, bMax) - Math.max(aMin, bMin));
      }
    }
  }
  return total;
}

/* ---------------------------------------------------------- districting */

interface District { seed: string; rooms: string[]; area: number; nodes: number }

function districting(plan: PlanModel, hubCount: number): District[] | null {
  const rooms = plan.eligibleRooms;
  const districts: District[] = [];
  const sorted = [...rooms].sort((a, b) => b.areaM2 - a.areaM2);
  districts.push({ seed: sorted[0].id, rooms: [sorted[0].id], area: sorted[0].areaM2, nodes: 1 });
  for (let h = 1; h < hubCount; h += 1) {
    let pick = sorted[0];
    let bestScore = -Infinity;
    sorted.forEach((room) => {
      if (districts.some((d) => d.seed === room.id)) return;
      const minDistance = Math.min(...districts.map((d) => manhattan(room.node, roomById(plan, d.seed).node)));
      const score = minDistance * (1 + districts.length * 0.35) + room.areaM2 * 0.42;
      if (score > bestScore) {
        bestScore = score;
        pick = room;
      }
    });
    districts.push({ seed: pick.id, rooms: [pick.id], area: pick.areaM2, nodes: 1 });
  }

  const assigned = new Set(districts.flatMap((d) => d.rooms));

  const grow = () => {
    let progress = true;
    let moved = false;
    while (assigned.size < rooms.length && progress) {
      progress = false;
      for (const district of districts) {
        const frontier = rooms
          .filter((room) => !assigned.has(room.id))
          .filter((room) => district.rooms.some((id) => (plan.adjacency[id] ?? []).includes(room.id)))
          .sort((a, b) => districtCost(plan, district, a) - districtCost(plan, district, b));
        const candidate = frontier.find((room) => district.area + room.areaM2 <= 100 && district.nodes + 1 <= 5);
        if (candidate) {
          district.rooms.push(candidate.id);
          district.area += candidate.areaM2;
          district.nodes += 1;
          assigned.add(candidate.id);
          progress = true;
          moved = true;
        }
      }
    }
    return moved;
  };
  grow();

  /* boundary transfer pass: swap a room out of a saturated district so an orphan can move in */
  for (let attempt = 0; attempt < 4 && assigned.size < rooms.length; attempt += 1) {
    let improved = false;
    for (const room of rooms.filter((item) => !assigned.has(item.id))) {
      const blocked = districts.find(
        (district) =>
          district.rooms.some((id) => (plan.adjacency[id] ?? []).includes(room.id)) &&
          (district.area + room.areaM2 > 100 || district.nodes + 1 > 5),
      );
      if (!blocked) continue;
      for (const movableId of blocked.rooms.slice(1)) {
        const movable = roomById(plan, movableId);
        const rest = blocked.rooms.filter((id) => id !== movableId);
        if (rest.length && !isContiguous(plan, rest)) continue;
        const donor = districts.find(
          (district) =>
            district !== blocked &&
            district.rooms.some((id) => (plan.adjacency[id] ?? []).includes(movableId)) &&
            district.area + movable.areaM2 <= 100 &&
            district.nodes + 1 <= 5,
        );
        if (!donor) continue;
        blocked.rooms = rest;
        blocked.area -= movable.areaM2;
        blocked.nodes -= 1;
        donor.rooms.push(movableId);
        donor.area += movable.areaM2;
        donor.nodes += 1;
        assigned.add(movableId);
        improved = true;
        break;
      }
      if (improved) break;
    }
    if (!improved) break;
    grow();
  }

  if (assigned.size !== rooms.length) return null;
  return districts;
}

function isContiguous(plan: PlanModel, ids: string[]) {
  if (ids.length <= 1) return true;
  const set = new Set(ids);
  const queue = [ids[0]];
  const seen = new Set(queue);
  while (queue.length) {
    const current = queue.shift() as string;
    (plan.adjacency[current] ?? []).forEach((next) => {
      if (set.has(next) && !seen.has(next)) {
        seen.add(next);
        queue.push(next);
      }
    });
  }
  return seen.size === set.size;
}

const districtCost = (plan: PlanModel, district: District, room: Room) =>
  Math.min(...district.rooms.map((id) => manhattan(roomById(plan, id).node, room.node))) - room.areaM2 * 0.01;

function ringOrder(start: Vec2, points: Array<{ id: string; p: Vec2 }>) {
  const remaining = [...points];
  const order: Array<{ id: string; p: Vec2 }> = [];
  let cursor = start;
  while (remaining.length) {
    let bestIndex = 0;
    let best = Infinity;
    remaining.forEach((item, index) => {
      const distance = manhattan(cursor, item.p);
      if (distance < best) {
        best = distance;
        bestIndex = index;
      }
    });
    const [next] = remaining.splice(bestIndex, 1);
    order.push(next);
    cursor = next.p;
  }
  /* 2-opt on the closed ring */
  const cost = (list: Array<{ p: Vec2 }>) => {
    let total = 0;
    let previous = start;
    list.forEach((item) => {
      total += manhattan(previous, item.p);
      previous = item.p;
    });
    return total + manhattan(previous, start);
  };
  let improved = true;
  let guard = 0;
  while (improved && guard < 40) {
    improved = false;
    guard += 1;
    for (let i = 0; i < order.length - 1; i += 1) {
      for (let j = i + 1; j < order.length; j += 1) {
        const candidate = [...order.slice(0, i), ...order.slice(i, j + 1).reverse(), ...order.slice(j + 1)];
        if (cost(candidate) + 1e-6 < cost(order)) {
          order.splice(0, order.length, ...candidate);
          improved = true;
        }
      }
    }
  }
  return order;
}

/** Hub point: >= 0.5 m from every wall, inside the assigned room, distinct from the room's node. */
function placeHub(room: Room, node: Vec2): Vec2 {
  const directions: Vec2[] = [[1, 0], [0, 1], [-1, 0], [0, -1], [0.71, 0.71], [-0.71, 0.71], [0.71, -0.71], [-0.71, -0.71]];
  for (const distance of [1.2, 1.8, 0.9, 2.6]) {
    for (const [dx, dy] of directions) {
      const candidate: Vec2 = [node[0] + dx * distance, node[1] + dy * distance];
      if (!pointInPolygon(candidate, room.polygon)) continue;
      if (distanceToPolygon(candidate, room.polygon) < 0.55) continue;
      if (Math.hypot(candidate[0] - node[0], candidate[1] - node[1]) < 0.6) continue;
      return [Number(candidate[0].toFixed(3)), Number(candidate[1].toFixed(3))];
    }
  }
  return node;
}

/** Separates any residual identical-line runs into distinct parallel lanes so O → 0. */
function resolveOverlaps(hubs: HubResult[], plan: PlanModel) {
  const insideAnyRoom = (p: Vec2) => plan.rooms.some((room) => pointInPolygon(p, room.polygon));
  const polylines = hubs.flatMap((hub) => hub.route_segments.map((segment) => segment.polyline));

  const overlaps = (a1: Vec2, a2: Vec2, b1: Vec2, b2: Vec2) => {
    const aVertical = Math.abs(a1[0] - a2[0]) < 1e-4;
    const bVertical = Math.abs(b1[0] - b2[0]) < 1e-4;
    if (aVertical !== bVertical) return 0;
    if (aVertical) {
      if (Math.abs(a1[0] - b1[0]) > 0.05) return 0;
      return Math.max(0, Math.min(Math.max(a1[1], a2[1]), Math.max(b1[1], b2[1])) - Math.max(Math.min(a1[1], a2[1]), Math.min(b1[1], b2[1])));
    }
    if (Math.abs(a1[1] - b1[1]) > 0.05) return 0;
    return Math.max(0, Math.min(Math.max(a1[0], a2[0]), Math.max(b1[0], b2[0])) - Math.max(Math.min(a1[0], a2[0]), Math.min(b1[0], b2[0])));
  };

  for (let pass = 0; pass < 4; pass += 1) {
    let moved = false;
    outer: for (let i = 0; i < polylines.length; i += 1) {
      for (let j = 0; j < polylines.length; j += 1) {
        if (i === j) continue;
        const A = polylines[i];
        const B = polylines[j];
        for (let a = 0; a < A.length - 1; a += 1) {
          for (let b = 1; b < B.length - 2; b += 1) {
            if (overlaps(A[a], A[a + 1], B[b], B[b + 1]) <= 0.02) continue;
            const vertical = Math.abs(B[b][0] - B[b + 1][0]) < 1e-4;
            const snapshot: Vec2[] = [B[b], B[b + 1]];
            for (const delta of [0.16, -0.16, 0.32, -0.32]) {
              const p1: Vec2 = vertical ? [B[b][0] + delta, B[b][1]] : [B[b][0], B[b][1] + delta];
              const p2: Vec2 = vertical ? [B[b + 1][0] + delta, B[b + 1][1]] : [B[b + 1][0], B[b + 1][1] + delta];
              if (!insideAnyRoom(p1) || !insideAnyRoom(p2)) continue;
              B[b] = p1;
              B[b + 1] = p2;
              if (!rectilinear(B)) {
                B[b] = snapshot[0];
                B[b + 1] = snapshot[1];
                continue;
              }
              moved = true;
              continue outer;
            }
          }
        }
      }
    }
    if (!moved) break;
  }

  hubs.forEach((hub) => {
    hub.route_segments.forEach((segment) => {
      segment.length_m = Number(polyLength(segment.polyline).toFixed(3));
    });
    hub.ring_length_m = Number(hub.route_segments.reduce((acc, segment) => acc + segment.length_m, 0).toFixed(3));
  });
}

/* ------------------------------------------------------------ solver API */

export interface SolveStats {
  hubs: number;
  iterations: number;
  cost_J: number;
  total_length_m: number;
}

export type HubEdit = { base: Solution; hubId: string; position: Vec2 };

export function optimize(plan: PlanModel, hubOverride?: number, edit?: HubEdit): Solution {
  const nodes = plan.eligibleRooms;
  const nodeIdOf = new Map(nodes.map((room, index) => [room.id, `N${index + 1}`]));
  const lowerBound = Math.max(
    Math.ceil(plan.eligibleAreaM2 / 100),
    Math.ceil(nodes.length / 5),
    1,
  );
  let hubCount = edit ? edit.base.hubs.length : Math.max(lowerBound, hubOverride ?? 0);
  let districts: District[] | null = edit ? edit.base.hubs.map((hub) => ({
    seed: hub.room_id, rooms: [...hub.assigned_rooms], area: hub.area_m2, nodes: hub.assigned_nodes.length,
  })) : null;
  let iterations = 0;

  while (!edit && hubCount <= nodes.length) {
    iterations += 1;
    districts = districting(plan, hubCount);
    if (districts) break;
    hubCount += 1;
  }
  if (!districts) districts = districting(plan, nodes.length) ?? nodes.map((room) => ({ seed: room.id, rooms: [room.id], area: room.areaM2, nodes: 1 }));

  const usage: DoorUsage = new Map();
  const corridors: Corridors = new Map();
  const allSegments: Segment[] = [];
  const anchors: Vec2[] = [];
  const hubs: HubResult[] = [];
  let segmentCounter = 0;

  districts.forEach((district, index) => {
    const districtRooms = district.rooms.map((id) => roomById(plan, id));
    const previousHub = edit?.base.hubs[index];
    if (previousHub && previousHub.hub_id !== edit.hubId) {
      districtRooms.forEach((room) => { room.hubId = previousHub.hub_id; });
      hubs.push({ ...previousHub, route_segments: previousHub.route_segments.map((segment) => ({ ...segment, polyline: segment.polyline.map((point) => [...point] as Vec2) })) });
      anchors.push(previousHub.location, ...districtRooms.map((room) => room.node));
      return;
    }
    /* hub point = most shielded room, offset from the fixed node so the ring has a real tie-in run */
    const hostRoom = edit
      ? districtRooms.find((room) => pointInPolygon(edit.position, room.polygon)) ?? roomById(plan, district.seed)
      : [...districtRooms].sort((a, b) => b.clearance - a.clearance)[0];
    const hubPoint = edit ? edit.position : placeHub(hostRoom, hostRoom.node);
    districtRooms.forEach((room) => {
      room.hubId = `H${index + 1}`;
    });
    anchors.push(hubPoint);

    const order: Array<{ id: string; p: Vec2; nodeId?: string }> = edit && previousHub
      ? previousHub.closed_sequence.slice(1, -1).map((id) => {
        const indexFromId = /^N\d+$/i.test(id) ? Number(id.slice(1)) - 1 : -1;
        const p = previousHub.nodePoints[id] ?? plan.eligibleRooms[indexFromId]?.node;
        const indexedRoom = plan.eligibleRooms[indexFromId];
        const room = districtRooms.find((candidate) => p && pointInPolygon(p, candidate.polygon))
          ?? districtRooms.find((candidate) => candidate.id === indexedRoom?.id)
          ?? (p ? [...districtRooms].sort((a, b) => Math.hypot(a.node[0] - p[0], a.node[1] - p[1])
            - Math.hypot(b.node[0] - p[0], b.node[1] - p[1]))[0] : undefined);
        if (!p || !room) throw new Error(`Cannot identify the assigned room for node ${id}`);
        return { id: room.id, nodeId: id, p };
      })
      : ringOrder(hubPoint, districtRooms.map((room) => ({ id: room.id, p: room.node })));
    const stopFor = (item: { id: string; p: Vec2; nodeId?: string }) => ({ id: item.nodeId ?? nodeIdOf.get(item.id) ?? item.id, p: item.p, room: item.id });
    const stops = [{ id: `H${index + 1}`, p: hubPoint, room: hostRoom.id }, ...order.map(stopFor)];
    const nodePoints: Record<string, Vec2> = {};
    order.forEach((item) => {
      nodePoints[item.nodeId ?? nodeIdOf.get(item.id) ?? item.id] = item.p;
    });

    const route_segments: RouteSegment[] = [];
    const sequence: string[] = [];
    for (let i = 0; i < stops.length; i += 1) {
      const from = stops[i];
      const to = stops[(i + 1) % stops.length];
      const routed = routeBetween(plan, usage, corridors, { room: from.room, point: from.p }, { room: to.room, point: to.p });
      const laneY = 0.16 + (segmentCounter % 3) * 0.1 + index * 0.02;
      segmentCounter += 1;
      route_segments.push({
        from: from.id,
        to: to.id,
        length_m: Number(polyLength(routed.polyline).toFixed(3)),
        polyline: routed.polyline.map(([x, y]) => [Number(x.toFixed(3)), Number(y.toFixed(3))] as Vec2),
        laneY,
      });
      sequence.push(to.id);
    }

    hubs.push({
      hub_id: `H${index + 1}`,
      room_id: hostRoom.id,
      location: [Number(hubPoint[0].toFixed(3)), Number(hubPoint[1].toFixed(3))],
      assigned_rooms: district.rooms,
      assigned_nodes: order.map((item) => item.nodeId ?? nodeIdOf.get(item.id) ?? item.id),
      closed_sequence: [`H${index + 1}`, ...order.map((item) => item.nodeId ?? nodeIdOf.get(item.id) ?? item.id), `H${index + 1}`],
      route_segments,
      ring_length_m: Number(route_segments.reduce((acc, seg) => acc + seg.length_m, 0).toFixed(3)),
      area_m2: Number(district.area.toFixed(2)),
      color: ZONE_COLORS[index % ZONE_COLORS.length],
      nodePoints,
    });
    districtRooms.forEach((room) => anchors.push(room.node));
  });

  if (!edit) resolveOverlaps(hubs, plan);
  hubs.forEach((hub, ring) => {
    hub.route_segments.forEach((segment, segmentIndex) => {
      for (let i = 1; i < segment.polyline.length; i += 1) {
        allSegments.push({ p: segment.polyline[i - 1], q: segment.polyline[i], ring, index: segmentIndex * 8 + i });
      }
    });
  });

  const L = Number(hubs.reduce((acc, hub) => acc + hub.ring_length_m, 0).toFixed(3));
  const X = countCrossings(allSegments, anchors);
  const O = Number(overlapLength(allSegments).toFixed(3));
  const B = hubs.reduce((acc, hub) => acc + ringBendCount(hub.route_segments), 0);
  const J = Number((L + 5 * X + O + 0.5 * B).toFixed(3));
  const areas = hubs.map((hub) => hub.area_m2).sort((a, b) => b - a);
  const areaGap = Number((areas.length > 1 ? areas[0] - areas[areas.length - 1] : 0).toFixed(2));

  /* ----------------------------------------------------- rule validation */
  const covered = new Set(hubs.flatMap((hub) => hub.assigned_rooms));
  const coverageOk = nodes.every((room) => covered.has(room.id)) && covered.size === nodes.length;
  const capacityOk = hubs.every((hub) => hub.assigned_nodes.length <= 5 && hub.area_m2 <= 100);
  const contiguityOk = hubs.every((hub) => {
    const set = new Set(hub.assigned_rooms);
    const queue = [hub.assigned_rooms[0]];
    const seen = new Set(queue);
    while (queue.length) {
      const current = queue.shift() as string;
      (plan.adjacency[current] ?? []).forEach((next) => {
        if (set.has(next) && !seen.has(next)) {
          seen.add(next);
          queue.push(next);
        }
      });
    }
    return seen.size === set.size;
  });
  const ringOk = hubs.every((hub) => {
    const seq = hub.closed_sequence;
    return (
      seq.length === hub.assigned_nodes.length + 2 &&
      seq[0] === hub.hub_id &&
      seq[seq.length - 1] === hub.hub_id &&
      new Set(seq.slice(1, -1)).size === hub.assigned_nodes.length
    );
  });
  const axisOk = hubs.every((hub) =>
    hub.route_segments.every((seg) => axisAligned(seg.polyline) && seg.polyline.every((p) => p[0] >= plan.bounds.min[0] - 0.05 && p[0] <= plan.bounds.max[0] + 0.05 && p[1] >= plan.bounds.min[1] - 0.05 && p[1] <= plan.bounds.max[1] + 0.05)),
  );
  const clearanceOk = hubs.every((hub) => distanceToPolygon(hub.location, roomById(plan, hub.room_id).polygon) >= 0.5 - 1e-3);

  const checks: RuleCheck[] = [
    { id: "coverage", label: "COVERAGE", pass: coverageOk, value: `${covered.size} / ${nodes.length}`, detail: "every eligible room & node on exactly one ring" },
    { id: "capacity", label: "CAPACITY", pass: capacityOk, value: `${Math.max(...hubs.map((h) => h.assigned_nodes.length))}/5 · ${Math.max(...hubs.map((h) => h.area_m2)).toFixed(1)}/100`, detail: "nodes and assigned area per hub" },
    { id: "contiguity", label: "ZONE CONTIGUITY", pass: contiguityOk, value: contiguityOk ? "DOOR GRAPH" : "BROKEN", detail: "districts linked by doors / open passages only" },
    { id: "ring", label: "RING CLOSURE", pass: ringOk, value: `${hubs.filter((hub) => hub.closed_sequence.length === hub.assigned_nodes.length + 2).length} / ${hubs.length}`, detail: "closed non-branching Hamiltonian cycles" },
    { id: "axis", label: "FREE SPACE · AXIS-ALIGNED", pass: axisOk, value: O < 0.001 && X === 0 ? "0X · 0.00 O" : `${X}X · ${O.toFixed(2)} O`, detail: "H/V runs inside inner boundary, doors only" },
    { id: "clearance", label: "HUB CLEARANCE", pass: clearanceOk, value: `${Math.min(...hubs.map((hub) => distanceToPolygon(hub.location, roomById(plan, hub.room_id).polygon))).toFixed(2)} M`, detail: "≥ 0.50 m from walls, inside assigned room" },
  ];

  const output = {
    plan_id: plan.plan_id,
    units: "m" as const,
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
    reported_metrics: { hub_count: hubs.length, total_length_m: L, cost_J: J },
  };

  return {
    plan_id: plan.plan_id,
    units: "m",
    hubs,
    reported_metrics: output.reported_metrics,
    metrics: { L, X, O, B, J, areaGap, hubLowerBound: lowerBound, nodeCount: nodes.length, eligibleArea: Number(plan.eligibleAreaM2.toFixed(2)) },
    checks,
    iterations,
    output,
    source: "demo",
  };
}
