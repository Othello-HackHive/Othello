"""The teacher's rule checks (T07).

Each check looks at a finished design and the real house, and returns the list
of rules it breaks (empty list = rule passed).  Nothing here uses the solver's
search grid.

Crossings and overlaps are NOT rule breaks; they only cost points (metrics.py).

``segment_index`` in a violation is the position of the *leg* inside the ring
(0 = first leg); the detail text names the cable piece.
"""

from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil, hypot

import numpy as np
import shapely
from shapely.geometry import LineString, Point

from othello.checker.geometry import (
    FreeSpaceChecker,
    almost_equal,
    has_clearance,
    point_in_polygon,
)
from othello.models.config import SolverConfig
from othello.models.design import Design, Hub
from othello.models.plan import Coord, FloorPlan
from othello.models.result import Violation

# A cable that changes room must cross within this distance of a door/opening.
DOOR_MARGIN_M = 0.25
# How finely the cable is sampled to see which rooms it walks through.
SAMPLE_STEP_M = 0.05


def _violation(
    code: str,
    hub_id: str | None = None,
    leg: int | None = None,
    point: Coord | None = None,
    detail: str = "",
) -> Violation:
    return Violation(type=code, hub_id=hub_id, ring_id=hub_id, segment_index=leg, point=point, detail=detail)


# --------------------------------------------------------------- the format


def check_schema(plan: FloorPlan, design: Design, cfg: SolverConfig) -> list[Violation]:
    """Is the design even filled in sensibly (known ids, matching lists)?"""
    found: list[Violation] = []
    if design.plan_id != plan.plan_id:
        found.append(_violation("OUTPUT_SCHEMA", detail=f"plan_id {design.plan_id} is not {plan.plan_id}"))
    hub_ids = [hub.hub_id for hub in design.hubs]
    for hub_id, count in Counter(hub_ids).items():
        if count > 1:
            found.append(_violation("OUTPUT_SCHEMA", hub_id, detail=f"hub id {hub_id} used {count} times"))
    stop_ids = set(plan.nodes) | set(hub_ids)

    for hub in design.hubs:
        for room_id in hub.assigned_rooms:
            if room_id not in plan.rooms:
                found.append(_violation("OUTPUT_SCHEMA", hub.hub_id, detail=f"unknown room {room_id}"))
            elif not plan.rooms[room_id].eligible:
                found.append(_violation("OUTPUT_SCHEMA", hub.hub_id, detail=f"room {room_id} is excluded"))
        for node_id in hub.assigned_nodes:
            if node_id not in plan.nodes:
                found.append(_violation("OUTPUT_SCHEMA", hub.hub_id, detail=f"unknown node {node_id}"))
        if hub.room_id not in plan.rooms:
            found.append(_violation("OUTPUT_SCHEMA", hub.hub_id, detail=f"unknown hub room {hub.room_id}"))
        owned_rooms = set(hub.assigned_rooms)
        expected = {n.node_id for n in plan.nodes.values() if n.room_id in owned_rooms}
        if set(hub.assigned_nodes) != expected:
            found.append(_violation("OUTPUT_SCHEMA", hub.hub_id, detail="assigned nodes do not match assigned rooms"))
        if hub.ring.hub_id != hub.hub_id:
            found.append(_violation("OUTPUT_SCHEMA", hub.hub_id, detail=f"ring belongs to {hub.ring.hub_id}"))
        for index, leg in enumerate(hub.ring.legs):
            for stop in (leg.from_id, leg.to_id):
                if stop not in stop_ids:
                    found.append(_violation("OUTPUT_SCHEMA", hub.hub_id, index, detail=f"unknown stop {stop}"))
    return found


# --------------------------------------------------------------- H1 coverage


def check_coverage(plan: FloorPlan, design: Design, cfg: SolverConfig) -> list[Violation]:
    """Every eligible room and node belongs to exactly one hub."""
    found: list[Violation] = []
    rooms = Counter(r for hub in design.hubs for r in hub.assigned_rooms)
    nodes = Counter(n for hub in design.hubs for n in hub.assigned_nodes)
    for room in plan.eligible_rooms():
        found.extend(_coverage_for("room", room.room_id, rooms[room.room_id], design))
    for node_id in plan.nodes:
        found.extend(_coverage_for("node", node_id, nodes[node_id], design))
    return found


def _coverage_for(kind: str, item_id: str, count: int, design: Design) -> list[Violation]:
    if count == 1:
        return []
    if count == 0:
        return [_violation("COVERAGE_MISSING", detail=f"{kind} {item_id} belongs to no hub")]
    owners = [
        hub.hub_id for hub in design.hubs if item_id in (hub.assigned_rooms if kind == "room" else hub.assigned_nodes)
    ]
    return [_violation("COVERAGE_DUPLICATE", detail=f"{kind} {item_id} belongs to {count} hubs: {owners}")]


# --------------------------------------------------------------- H2 capacity


def hub_area_m2(plan: FloorPlan, hub: Hub) -> float:
    return sum(plan.rooms[r].area_m2 for r in hub.assigned_rooms if r in plan.rooms)


def check_capacity(plan: FloorPlan, design: Design, cfg: SolverConfig) -> list[Violation]:
    """At most max_nodes_per_hub nodes and max_area_per_hub_m2 of eligible area per hub."""
    found: list[Violation] = []
    for hub in design.hubs:
        if len(hub.assigned_nodes) > cfg.max_nodes_per_hub:
            found.append(
                _violation(
                    "CAPACITY_NODES",
                    hub.hub_id,
                    detail=f"{len(hub.assigned_nodes)} nodes, limit {cfg.max_nodes_per_hub}",
                )
            )
        area = hub_area_m2(plan, hub)
        if area > cfg.max_area_per_hub_m2 + 1e-9:
            found.append(
                _violation(
                    "CAPACITY_AREA",
                    hub.hub_id,
                    detail=f"{area:.2f} m2, limit {cfg.max_area_per_hub_m2:g}",
                )
            )
    return found


# ------------------------------------------------------------------ H3 zones


def check_zones(plan: FloorPlan, design: Design, cfg: SolverConfig) -> list[Violation]:
    """A hub's rooms must be connected through doors or open passages only."""
    found: list[Violation] = []
    for hub in design.hubs:
        rooms = [r for r in hub.assigned_rooms if r in plan.room_graph]
        if len(rooms) < 2:
            continue
        seen = {rooms[0]}
        todo = [rooms[0]]
        while todo:
            for neighbour in plan.room_graph[todo.pop()]:
                if neighbour in rooms and neighbour not in seen:
                    seen.add(neighbour)
                    todo.append(neighbour)
        if len(seen) != len(rooms):
            cut_off = sorted(set(rooms) - seen)
            found.append(
                _violation("ZONE_DISCONNECTED", hub.hub_id, detail=f"rooms {cut_off} are not joined to {sorted(seen)}")
            )
    return found


# ------------------------------------------------------------ H6 hub spot


def check_hub_points(plan: FloorPlan, design: Design, cfg: SolverConfig) -> list[Violation]:
    """Hub inside one of its own eligible rooms and at least 0.5 m from walls."""
    found: list[Violation] = []
    tol = cfg.tolerance_m
    for hub in design.hubs:
        room = plan.rooms.get(hub.room_id)
        where = (float(hub.location[0]), float(hub.location[1]))
        if room is None or not room.eligible:
            found.append(_violation("HUB_NOT_IN_ROOM", hub.hub_id, point=where, detail="hub room is unknown or excluded"))
            continue
        if hub.room_id not in hub.assigned_rooms:
            found.append(_violation("HUB_NOT_IN_ROOM", hub.hub_id, point=where, detail="hub room is not one of its rooms"))
        if not point_in_polygon(where, room.polygon_m, tol):
            found.append(_violation("HUB_NOT_IN_ROOM", hub.hub_id, point=where, detail=f"hub is outside {hub.room_id}"))
        if not has_clearance(where, plan.walls_m, cfg.hub_wall_clearance_m, tol):
            found.append(
                _violation(
                    "HUB_WALL_CLEARANCE",
                    hub.hub_id,
                    point=where,
                    detail=f"closer than {cfg.hub_wall_clearance_m:g} m to a wall",
                )
            )
    return found


# ------------------------------------------------------------------ H4 ring


def check_rings(plan: FloorPlan, design: Design, cfg: SolverConfig) -> list[Violation]:
    """One closed loop per hub: hub, every own node exactly once, same hub."""
    found: list[Violation] = []
    tol = cfg.tolerance_m
    hub_ids = {hub.hub_id for hub in design.hubs}
    for hub in design.hubs:
        ring = hub.ring
        seq = ring.sequence
        # --- the ends of the loop
        if len(seq) < 2 or seq[0] != hub.hub_id:
            code = "RING_WRONG_HUB" if seq and seq[0] in hub_ids else "RING_NOT_CLOSED"
            found.append(_violation(code, hub.hub_id, detail=f"ring starts at {seq[0] if seq else 'nothing'}"))
        elif seq[-1] != hub.hub_id:
            code = "RING_WRONG_HUB" if seq[-1] in hub_ids else "RING_NOT_CLOSED"
            found.append(_violation(code, hub.hub_id, detail=f"ring ends at {seq[-1]}, not {hub.hub_id}"))
        # --- the stops in between
        interior = seq[1:-1] if len(seq) >= 2 else []
        for stop in interior:
            if stop in hub_ids:
                found.append(_violation("RING_WRONG_HUB", hub.hub_id, detail=f"ring uses hub {stop} as a stop"))
        visits = Counter(stop for stop in interior if stop not in hub_ids)
        for node_id in hub.assigned_nodes:
            if visits[node_id] == 0:
                found.append(_violation("RING_NODE_MISSING", hub.hub_id, detail=f"ring never visits {node_id}"))
            elif visits[node_id] > 1:
                found.append(
                    _violation("RING_NODE_REPEATED", hub.hub_id, detail=f"ring visits {node_id} {visits[node_id]} times")
                )
        for node_id in visits:
            if node_id not in hub.assigned_nodes:
                found.append(_violation("RING_BRANCH", hub.hub_id, detail=f"ring visits {node_id}, which it does not own"))
        # --- the cable really forms one unbroken chain
        if len(ring.legs) != max(len(seq) - 1, 0):
            found.append(
                _violation("RING_BRANCH", hub.hub_id, detail=f"{len(ring.legs)} legs for {max(len(seq) - 1, 0)} hops")
            )
            continue
        for index, leg in enumerate(ring.legs):
            if leg.from_id != seq[index] or leg.to_id != seq[index + 1]:
                found.append(_violation("RING_BRANCH", hub.hub_id, index, detail="leg does not match the sequence"))
            if index > 0 and not almost_equal(ring.legs[index - 1].polyline[-1], leg.polyline[0], tol):
                found.append(
                    _violation("RING_BRANCH", hub.hub_id, index, point=leg.polyline[0], detail="cable jumps between legs")
                )
        # --- the cable starts and ends at the hub itself
        where = (float(hub.location[0]), float(hub.location[1]))
        if ring.legs:
            if not almost_equal(ring.legs[0].polyline[0], where, tol):
                found.append(_violation("RING_NOT_CLOSED", hub.hub_id, 0, detail="cable does not start at the hub"))
            if not almost_equal(ring.legs[-1].polyline[-1], where, tol):
                found.append(
                    _violation("RING_NOT_CLOSED", hub.hub_id, len(ring.legs) - 1, detail="cable does not end at the hub")
                )
    return found


# --------------------------------------------------------- nodes stay put


def check_node_positions(plan: FloorPlan, design: Design, cfg: SolverConfig) -> list[Violation]:
    """The cable meets each node exactly where the plan put it."""
    found: list[Violation] = []
    tol = cfg.tolerance_m
    for hub in design.hubs:
        for index, leg in enumerate(hub.ring.legs):
            for stop, end in ((leg.from_id, leg.polyline[0]), (leg.to_id, leg.polyline[-1])):
                node = plan.nodes.get(stop)
                if node is not None and not almost_equal(end, node.point_m, tol):
                    found.append(
                        _violation("NODE_MOVED", hub.hub_id, index, point=end, detail=f"cable meets {stop} in the wrong spot")
                    )
    return found


# ----------------------------------------------------- H5 free space (walls)


def check_free_space(plan: FloorPlan, design: Design, cfg: SolverConfig) -> list[Violation]:
    """Straight pieces only, inside the house, never through walls or windows."""
    checker = FreeSpaceChecker.from_plan(plan, cfg.tolerance_m)
    found: list[Violation] = []
    for hub in design.hubs:
        for index, leg in enumerate(hub.ring.legs):
            for issue in checker.issues(leg.polyline):
                found.append(
                    _violation(
                        issue.kind,
                        hub.hub_id,
                        index,
                        point=issue.point,
                        detail=f"piece {issue.segment_index + 1} of leg {leg.from_id}->{leg.to_id}",
                    )
                )
    return found


# ------------------------------------------------- H5 rooms only via doors


def rooms_walked(plan: FloorPlan, polyline: list[Coord]) -> list[tuple[str | None, int, Coord]]:
    """Which room each sample of the cable is in (None = in no room, e.g. a door gap)."""
    samples: list[Coord] = []
    for p, q in itertools.pairwise(polyline):
        steps = max(1, ceil(hypot(q[0] - p[0], q[1] - p[1]) / SAMPLE_STEP_M))
        samples.extend((p[0] + (q[0] - p[0]) * k / steps, p[1] + (q[1] - p[1]) * k / steps) for k in range(steps))
    if polyline:
        samples.append((float(polyline[-1][0]), float(polyline[-1][1])))
    if not samples:
        return []
    xs = np.array([s[0] for s in samples])
    ys = np.array([s[1] for s in samples])
    labels: list[str | None] = [None] * len(samples)
    for room_id, room in plan.rooms.items():
        inside = shapely.contains_xy(room.polygon_m, xs, ys)
        for i in np.nonzero(inside)[0]:
            if labels[i] is None:
                labels[i] = room_id
    return [(labels[i], i, samples[i]) for i in range(len(samples))]


def room_changes(plan: FloorPlan, polyline: list[Coord]) -> list[tuple[str, str, Coord]]:
    """Every time the cable walks from one room into another: (from, to, where)."""
    changes: list[tuple[str, str, Coord]] = []
    last_room: str | None = None
    last_point: Coord | None = None
    for room, _, point in rooms_walked(plan, polyline):
        if room is None:
            continue
        if last_room is not None and room != last_room and last_point is not None:
            middle = ((last_point[0] + point[0]) / 2, (last_point[1] + point[1]) / 2)
            changes.append((last_room, room, middle))
        last_room, last_point = room, point
    return changes


def check_doors(plan: FloorPlan, design: Design, cfg: SolverConfig) -> list[Violation]:
    """Between rooms, the cable may only go through a door or open passage."""
    openings = [
        (set(o.rooms), LineString(o.gap_polyline_m) if o.gap_polyline_m[0] != o.gap_polyline_m[-1] else Point(o.gap_polyline_m[0]))
        for o in plan.openings.values()
        if len(o.rooms) == 2
    ]
    found: list[Violation] = []
    for hub in design.hubs:
        for index, leg in enumerate(hub.ring.legs):
            for room_a, room_b, where in room_changes(plan, leg.polyline):
                spot = Point(where)
                through_a_door = any(
                    {room_a, room_b} == pair and gap.distance(spot) <= DOOR_MARGIN_M for pair, gap in openings
                )
                if not through_a_door:
                    found.append(
                        _violation(
                            "ROOM_TRANSITION_NOT_VIA_OPENING",
                            hub.hub_id,
                            index,
                            point=where,
                            detail=f"{room_a} -> {room_b} without a door or opening",
                        )
                    )
    return found


# ------------------------------------------------------------- all together


@dataclass(frozen=True)
class Rule:
    rule_id: str
    label: str
    run: Callable[[FloorPlan, Design, SolverConfig], list[Violation]]


RULES: tuple[Rule, ...] = (
    Rule("schema", "OUTPUT FORMAT", check_schema),
    Rule("coverage", "COVERAGE", check_coverage),
    Rule("capacity", "CAPACITY", check_capacity),
    Rule("zones", "ZONE CONTIGUITY", check_zones),
    Rule("hub_points", "HUB CLEARANCE", check_hub_points),
    Rule("rings", "RING CLOSURE", check_rings),
    Rule("nodes", "NODE POSITIONS", check_node_positions),
    Rule("free_space", "FREE SPACE", check_free_space),
    Rule("doors", "DOOR PASSAGE", check_doors),
)


def run_rules(plan: FloorPlan, design: Design, cfg: SolverConfig | None = None) -> dict[str, list[Violation]]:
    """Run every rule; result maps rule id -> the violations that rule found."""
    config = cfg or SolverConfig()
    return {rule.rule_id: rule.run(plan, design, config) for rule in RULES}


def check_all(plan: FloorPlan, design: Design, cfg: SolverConfig | None = None) -> list[Violation]:
    """Every violation from every rule, in rule order."""
    return [v for violations in run_rules(plan, design, cfg).values() for v in violations]
