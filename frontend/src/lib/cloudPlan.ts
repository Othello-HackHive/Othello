import { polygonArea, type Vec2 } from "./resplan";

const ROOM_KINDS = [
  "living", "bedroom", "kitchen", "storage",
  "bathroom", "balcony", "stair", "garden", "pool", "parking", "land",
];

function points(value: unknown): Vec2[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((point) => {
    if (!Array.isArray(point) || point.length < 2) return [];
    const x = Number(point[0]);
    const y = Number(point[1]);
    return Number.isFinite(x) && Number.isFinite(y) ? [[x, y] as Vec2] : [];
  });
}

function groups(text: string): string[] {
  const found: string[] = [];
  let depth = 0;
  let start = 0;
  for (let index = 0; index < text.length; index += 1) {
    if (text[index] === "(") {
      if (depth === 0) start = index + 1;
      depth += 1;
    } else if (text[index] === ")") {
      depth -= 1;
      if (depth === 0) found.push(text.slice(start, index));
    }
  }
  return found;
}

function wktRing(text: string): Vec2[] {
  return text.split(",").flatMap((point) => {
    const [x, y] = point.trim().split(/\s+/).map(Number);
    return Number.isFinite(x) && Number.isFinite(y) ? [[x, y] as Vec2] : [];
  });
}

function splitTopLevel(text: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let index = 0; index < text.length; index += 1) {
    if (text[index] === "(") depth += 1;
    else if (text[index] === ")") depth -= 1;
    else if (text[index] === "," && depth === 0) {
      parts.push(text.slice(start, index).trim());
      start = index + 1;
    }
  }
  parts.push(text.slice(start).trim());
  return parts.filter(Boolean);
}

/** Outer polygon rings, in the same order as the backend's room parser. */
export function geometryRings(value: unknown): Vec2[][] {
  if (typeof value === "string") {
    const kind = value.trim().match(/^(POLYGON|MULTIPOLYGON|GEOMETRYCOLLECTION)\s*/i)?.[1]?.toUpperCase();
    const body = groups(value.slice(value.indexOf("(")))[0];
    if (!body) return [];
    if (kind === "POLYGON") {
      const ring = wktRing(groups(body)[0] ?? "");
      return ring.length >= 3 ? [ring] : [];
    }
    if (kind === "MULTIPOLYGON") {
      return groups(body).map((polygon) => wktRing(groups(polygon)[0] ?? "")).filter((ring) => ring.length >= 3);
    }
    if (kind === "GEOMETRYCOLLECTION") return splitTopLevel(body).flatMap(geometryRings);
    return [];
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  const geometry = value as { type?: string; coordinates?: unknown; geometries?: unknown };
  if (geometry.type === "Polygon" && Array.isArray(geometry.coordinates)) {
    const ring = points(geometry.coordinates[0]);
    return ring.length >= 3 ? [ring] : [];
  }
  if (geometry.type === "MultiPolygon" && Array.isArray(geometry.coordinates)) {
    return geometry.coordinates
      .map((polygon) => points(Array.isArray(polygon) ? polygon[0] : null))
      .filter((ring) => ring.length >= 3);
  }
  if (geometry.type === "GeometryCollection" && Array.isArray(geometry.geometries)) {
    return geometry.geometries.flatMap(geometryRings);
  }
  return [];
}

/** The cloud API consumes raw ResPlan geometry; the viewer needs individual labelled rooms. */
export function viewerPlanFromCloud(raw: Record<string, unknown>): Record<string, unknown> {
  if (raw.rooms || raw.spaces || raw.labels) return raw;
  const rooms: Record<string, { label: string; polygon: Vec2[] }> = {};
  for (const kind of ROOM_KINDS) {
    for (const ring of geometryRings(raw[kind])) {
      const id = `R${String(Object.keys(rooms).length + 1).padStart(2, "0")}`;
      rooms[id] = { label: kind, polygon: ring };
    }
  }
  const innerArea = geometryRings(raw.inner).reduce((sum, ring) => sum + polygonArea(ring), 0);
  const area = typeof raw.area === "number" ? raw.area : 0;
  const mPerUnit = innerArea > 0 && area > 0 ? Math.sqrt(area / innerArea) : undefined;
  const openings = geometryRings(raw.door).map((ring) => {
    const xs = ring.map((point) => point[0]);
    const ys = ring.map((point) => point[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const contour: Vec2[] = maxX - minX < maxY - minY
      ? [[(minX + maxX) / 2, minY], [(minX + maxX) / 2, maxY]]
      : [[minX, (minY + maxY) / 2], [maxX, (minY + maxY) / 2]];
    return { contour };
  });
  return {
    ...raw,
    plan_id: String(raw.id ?? "UPLOADED_PLAN"),
    ...(mPerUnit ? { m_per_unit: mPerUnit } : {}),
    rooms,
    openings: { doors: openings },
  };
}
