"""The scorekeeper: L, X, O, B and J, counted the way the PPT defines them.

Pure Python on purpose (no shapely, no solver grid): the checker must not share
code or assumptions with the solver.  Everything is in metres.

    J = wL*L + wX*X + wO*O + wB*B        defaults 1, 5, 1, 0.5

* L  cable length, every leg of every ring, return legs included.
* X  distinct points where two cables meet away from a node or hub.
* O  length lying on the same line; a stretch covered by k legs counts (k-1)x.
* B  direction changes along each closed ring (nodes and hub included);
     a reversal counts once.

Tolerance (default 1 mm): points within it are the same point, lines within it
are the same line.  Crossings and overlaps are penalties, never violations.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

from othello.models.config import SolverConfig, Weights
from othello.models.design import Design, Ring
from othello.models.plan import Coord
from othello.models.result import Metrics

Polyline = list[Coord]


# ---------------------------------------------------------------- basics


def _same_point(a: Coord, b: Coord, tol: float) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def direction(a: Coord, b: Coord, tol: float) -> tuple[int, int]:
    """Which way a piece goes: (-1, 0, 1) for x and for y; (0, 0) if no length."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    sx = 0 if abs(dx) <= tol else (1 if dx > 0 else -1)
    sy = 0 if abs(dy) <= tol else (1 if dy > 0 else -1)
    return sx, sy


def _is_axis(d: tuple[int, int]) -> bool:
    return (d[0] == 0) != (d[1] == 0)


def clean_polyline(points: list[Coord], tol: float) -> Polyline:
    """Drop repeated points and merge straight stretches into one piece.

    A reversal (going back on yourself) is kept: it is a real turn.
    """
    result: Polyline = []
    for raw in points:
        p = (float(raw[0]), float(raw[1]))
        if result and _same_point(result[-1], p, tol):
            continue
        if len(result) >= 2:
            d1 = direction(result[-2], result[-1], tol)
            d2 = direction(result[-1], p, tol)
            if _is_axis(d1) and d1 == d2:
                result[-1] = p  # same straight line: extend instead of adding a corner
                continue
        result.append(p)
    return result


def polyline_length(points: list[Coord]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in itertools.pairwise(points))


def ring_points(ring: Ring, tol: float) -> Polyline:
    """The whole loop as one path: leg after leg, hub back to hub."""
    pts: list[Coord] = []
    for leg in ring.legs:
        pts.extend(leg.polyline)
    return clean_polyline(pts, tol)


# ------------------------------------------------------------------ bends


def bend_points_closed(points: list[Coord], tol: float) -> list[Coord]:
    """Where a closed ring turns, including the turn where it closes at the hub."""
    pts = clean_polyline(points, tol)
    pieces = [(a, direction(a, b, tol)) for a, b in itertools.pairwise(pts)]
    pieces = [piece for piece in pieces if piece[1] != (0, 0)]
    if len(pieces) < 2:
        return []
    turns = [after[0] for before, after in itertools.pairwise(pieces) if before[1] != after[1]]
    if pieces[-1][1] != pieces[0][1]:  # closing turn at the hub
        turns.append(pieces[0][0])
    return turns


def count_bends_closed(points: list[Coord], tol: float) -> int:
    """Turns along a closed ring, including the turn where it closes at the hub."""
    return len(bend_points_closed(points, tol))


# ------------------------------------------------------------ segments


@dataclass(frozen=True)
class Segment:
    a: Coord
    b: Coord
    owner: int  # which leg it belongs to
    index: int  # position inside that leg

    def horizontal(self, tol: float) -> bool:
        return abs(self.a[1] - self.b[1]) <= tol and abs(self.a[0] - self.b[0]) > tol

    def vertical(self, tol: float) -> bool:
        return abs(self.a[0] - self.b[0]) <= tol and abs(self.a[1] - self.b[1]) > tol


def build_segments(legs: list[Polyline], tol: float) -> list[Segment]:
    segments: list[Segment] = []
    for owner, leg in enumerate(legs):
        cleaned = clean_polyline(leg, tol)
        for index, (a, b) in enumerate(itertools.pairwise(cleaned)):
            segments.append(Segment(a, b, owner, index))
    return segments


def _distinct_points(points: list[Coord], tol: float) -> list[Coord]:
    distinct: list[Coord] = []
    for p in points:
        if not any(_same_point(p, q, tol) for q in distinct):
            distinct.append(p)
    return distinct


# --------------------------------------------------------------- crossings


def count_crossings(
    legs: list[Polyline],
    anchors: list[Coord],
    tol: float = 0.001,
    t_touch_counts: bool = True,
    overlap_endpoint_counts: bool = False,
) -> int:
    """How many distinct points where two cables meet away from a node or hub."""
    return len(crossing_points(legs, anchors, tol, t_touch_counts, overlap_endpoint_counts))


def crossing_points(
    legs: list[Polyline],
    anchors: list[Coord],
    tol: float = 0.001,
    t_touch_counts: bool = True,
    overlap_endpoint_counts: bool = False,
) -> list[Coord]:
    """The distinct points where two cables meet away from a node or hub.

    * Meeting at an anchor (node or hub) never counts.
    * A cable bending at its own corner never counts.
    * Three cables at one point count once.
    * t_touch_counts=False: only strict cross-overs count (both cables pass
      through the point), not a cable ending on another.
    * Collinear overlaps belong to O; their ends add no crossing unless
      overlap_endpoint_counts is True.
    """
    segments = build_segments(legs, tol)
    found: list[Coord] = []

    def is_anchor(p: Coord) -> bool:
        return any(_same_point(p, q, tol) for q in anchors)

    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            s, t = segments[i], segments[j]
            if s.owner == t.owner and abs(s.index - t.index) == 1:
                continue  # neighbours inside one leg: that is a bend, not a crossing
            if s.horizontal(tol) and t.vertical(tol):
                h, v = s, t
            elif s.vertical(tol) and t.horizontal(tol):
                h, v = t, s
            elif overlap_endpoint_counts:
                found.extend(_overlap_end_points(s, t, tol))
                continue
            else:
                continue
            hx1, hx2 = sorted((h.a[0], h.b[0]))
            vy1, vy2 = sorted((v.a[1], v.b[1]))
            vx, hy = v.a[0], h.a[1]
            if not (hx1 - tol <= vx <= hx2 + tol and vy1 - tol <= hy <= vy2 + tol):
                continue
            point = (vx, hy)
            if is_anchor(point):
                continue
            if not t_touch_counts:
                inside_h = hx1 + tol < vx < hx2 - tol
                inside_v = vy1 + tol < hy < vy2 - tol
                if not (inside_h and inside_v):
                    continue
            found.append(point)

    return _distinct_points([p for p in found if not is_anchor(p)], tol)


def _overlap_end_points(s: Segment, t: Segment, tol: float) -> list[Coord]:
    if s.horizontal(tol) and t.horizontal(tol) and abs(s.a[1] - t.a[1]) <= tol:
        lo = max(min(s.a[0], s.b[0]), min(t.a[0], t.b[0]))
        hi = min(max(s.a[0], s.b[0]), max(t.a[0], t.b[0]))
        if hi - lo > tol:
            return [(lo, s.a[1]), (hi, s.a[1])]
    if s.vertical(tol) and t.vertical(tol) and abs(s.a[0] - t.a[0]) <= tol:
        lo = max(min(s.a[1], s.b[1]), min(t.a[1], t.b[1]))
        hi = min(max(s.a[1], s.b[1]), max(t.a[1], t.b[1]))
        if hi - lo > tol:
            return [(s.a[0], lo), (s.a[0], hi)]
    return []


# ----------------------------------------------------------------- overlap


def _group_lines(
    lines: list[tuple[float, float, float]], tol: float
) -> list[tuple[float, list[tuple[float, float]]]]:
    """Group (line_coord, lo, hi) pieces whose line is within tol into the same line.

    Each group is (the line's coordinate, [(lo, hi), ...]).
    """
    groups: list[tuple[float, list[tuple[float, float]]]] = []
    last_coord: float | None = None
    for coord, lo, hi in sorted(lines):
        if last_coord is None or coord - last_coord > tol:
            groups.append((coord, []))
        groups[-1][1].append((lo, hi))
        last_coord = coord
    return groups


def _covered_stretches(intervals: list[tuple[float, float]], tol: float) -> list[tuple[float, float, int]]:
    """The stretches covered by k >= 2 pieces, as (start, end, k)."""
    cuts = sorted({v for lo, hi in intervals for v in (lo, hi)})
    found: list[tuple[float, float, int]] = []
    for left, right in itertools.pairwise(cuts):
        if right - left <= tol:
            continue
        mid = (left + right) / 2
        k = sum(1 for lo, hi in intervals if lo < mid < hi)
        if k > 1:
            if found and found[-1][2] == k and abs(found[-1][1] - left) <= tol:
                found[-1] = (found[-1][0], right, k)  # join neighbouring stretches covered the same
            else:
                found.append((left, right, k))
    return found


def overlap_stretches(legs: list[Polyline], tol: float = 0.001) -> list[tuple[Coord, Coord, int]]:
    """Every stretch where cables lie on one line: (start, end, how many cover it)."""
    segments = build_segments(legs, tol)
    horizontal = [(s.a[1], min(s.a[0], s.b[0]), max(s.a[0], s.b[0])) for s in segments if s.horizontal(tol)]
    vertical = [(s.a[0], min(s.a[1], s.b[1]), max(s.a[1], s.b[1])) for s in segments if s.vertical(tol)]
    found: list[tuple[Coord, Coord, int]] = []
    for y, intervals in _group_lines(horizontal, tol):
        found.extend(((lo, y), (hi, y), k) for lo, hi, k in _covered_stretches(intervals, tol))
    for x, intervals in _group_lines(vertical, tol):
        found.extend(((x, lo), (x, hi), k) for lo, hi, k in _covered_stretches(intervals, tol))
    return found


def overlap_length(legs: list[Polyline], tol: float = 0.001) -> float:
    """A stretch covered by k pieces counts (k - 1) times its length."""
    return sum((k - 1) * math.dist(a, b) for a, b, k in overlap_stretches(legs, tol))


# -------------------------------------------------------------------- cost


def total_cost(L: float, X: int, O: float, B: int, weights: Weights | None = None) -> float:
    w = weights or Weights()
    return w.length * L + w.crossing * X + w.overlap * O + w.bend * B


def area_gap(hub_areas: dict[str, float]) -> float:
    """Tie-break value: largest hub area minus smallest (assumption A3)."""
    if not hub_areas:
        return 0.0
    return max(hub_areas.values()) - min(hub_areas.values())


def collect_legs_and_anchors(design: Design, tol: float) -> tuple[list[Polyline], list[Coord]]:
    """Every cleaned leg of the design, and the anchors (hub spots and node spots)."""
    legs: list[Polyline] = []
    anchors: list[Coord] = []
    for hub in design.hubs:
        anchors.append((float(hub.location[0]), float(hub.location[1])))
        for leg in hub.ring.legs:
            cleaned = clean_polyline(leg.polyline, tol)
            legs.append(cleaned)
            if cleaned:
                anchors.append(cleaned[0])
                anchors.append(cleaned[-1])
    return legs, anchors


def design_markers(design: Design, config: SolverConfig | None = None) -> dict[str, list]:
    """WHERE the costs are: crossing points, overlapping stretches and bend points.

    {"crossings": [[x, y]], "overlaps": [[[x1, y1], [x2, y2]]], "bends": [[x, y]]}
    The counts match ``compute_metrics`` exactly (same functions, same tolerance).
    """
    cfg = config or SolverConfig()
    tol = cfg.tolerance_m
    legs, anchors = collect_legs_and_anchors(design, tol)
    crossings = crossing_points(
        legs,
        anchors,
        tol,
        t_touch_counts=cfg.t_touch_counts_as_crossing,
        overlap_endpoint_counts=cfg.overlap_endpoint_crossing,
    )
    overlaps = [[a, b] for a, b, _ in overlap_stretches(legs, tol)]
    bends = [point for hub in design.hubs for point in bend_points_closed(ring_points(hub.ring, tol), tol)]
    return {
        "crossings": [list(p) for p in crossings],
        "overlaps": overlaps,
        "bends": [list(p) for p in bends],
    }


def compute_metrics(
    design: Design,
    config: SolverConfig | None = None,
    hub_areas: dict[str, float] | None = None,
) -> Metrics:
    """Score a whole design from its polylines (never from reported numbers)."""
    cfg = config or SolverConfig()
    tol = cfg.tolerance_m

    legs, anchors = collect_legs_and_anchors(design, tol)
    per_hub: dict[str, dict] = {}
    total_bends = 0

    for hub in design.hubs:
        hub_length = sum(polyline_length(clean_polyline(leg.polyline, tol)) for leg in hub.ring.legs)
        bends = count_bends_closed(ring_points(hub.ring, tol), tol)
        total_bends += bends
        per_hub[hub.hub_id] = {
            "ring_length_m": hub_length,
            "bends": bends,
            "node_count": len(hub.assigned_nodes),
        }
        if hub_areas and hub.hub_id in hub_areas:
            per_hub[hub.hub_id]["area_m2"] = hub_areas[hub.hub_id]

    L = sum(polyline_length(leg) for leg in legs)
    X = count_crossings(
        legs,
        anchors,
        tol,
        t_touch_counts=cfg.t_touch_counts_as_crossing,
        overlap_endpoint_counts=cfg.overlap_endpoint_crossing,
    )
    O = overlap_length(legs, tol)
    J = total_cost(L, X, O, total_bends, cfg.weights)

    areas_of_hubs = {h.hub_id: hub_areas[h.hub_id] for h in design.hubs if hub_areas and h.hub_id in hub_areas}
    return Metrics(
        hub_count=len(design.hubs),
        L=L,
        X=X,
        O=O,
        B=total_bends,
        J=J,
        per_hub=per_hub,
        area_gap_m2=area_gap(areas_of_hubs),
    )


def is_better(a: Metrics, b: Metrics, tol: float = 1e-6) -> bool:
    """Official ranking: fewer hubs, then lower J, then smaller area gap."""
    if a.hub_count != b.hub_count:
        return a.hub_count < b.hub_count
    if abs(a.J - b.J) > tol:
        return a.J < b.J
    return a.area_gap_m2 < b.area_gap_m2 - tol
