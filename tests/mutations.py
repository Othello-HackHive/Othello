"""The damaging machine (T13): small, deliberate ways to break (or harmlessly change) a design.

Every function takes a design and returns a NEW, changed copy; the original is
never touched.  Breaking mutations say which rule they should trip; "harmless"
mutations must leave the design valid.
"""

from __future__ import annotations

from shapely.geometry import LineString, Point

from othello.models.design import Design, Hub, RouteLeg
from othello.models.plan import Coord, FloorPlan

DIRECTIONS: dict[str, Coord] = {"E": (1.0, 0.0), "W": (-1.0, 0.0), "N": (0.0, 1.0), "S": (0.0, -1.0)}


def copy(design: Design) -> Design:
    return design.model_copy(deep=True)


def hub_of(design: Design, hub_id: str) -> Hub:
    return next(h for h in design.hubs if h.hub_id == hub_id)


def stops(design: Design) -> list[tuple[str, str, int, Coord]]:
    """Every place a cable meets a hub or node: (hub_id, stop_id, leg_index, point).

    A hub appears once (the start of its first leg); a node is the end of leg ``leg_index``.
    """
    found: list[tuple[str, str, int, Coord]] = []
    for hub in design.hubs:
        legs = hub.ring.legs
        found.append((hub.hub_id, hub.hub_id, 0, legs[0].polyline[0]))
        for index, leg in enumerate(legs[:-1]):
            found.append((hub.hub_id, leg.to_id, index, leg.polyline[-1]))
    return found


# ------------------------------------------------------------- spikes


def spike(design: Design, hub_id: str, stop_id: str, leg_index: int, direction: str, length: float) -> Design:
    """Make the cable poke out from a hub/node in a straight line and come back."""
    out = copy(design)
    hub = hub_of(out, hub_id)
    dx, dy = DIRECTIONS[direction]
    if stop_id == hub_id:
        leg = hub.ring.legs[0]
        point = leg.polyline[0]
        tip = (point[0] + dx * length, point[1] + dy * length)
        leg.polyline = [point, tip, *leg.polyline]
    else:
        leg = hub.ring.legs[leg_index]
        point = leg.polyline[-1]
        tip = (point[0] + dx * length, point[1] + dy * length)
        leg.polyline = [*leg.polyline, tip, point]
    return out


def distance_along(point: Coord, direction: str, geometry) -> float:
    """How far from ``point`` going in ``direction`` until the first touch of ``geometry`` (inf if never)."""
    dx, dy = DIRECTIONS[direction]
    ray = LineString([point, (point[0] + dx * 200, point[1] + dy * 200)])
    hit = ray.intersection(geometry)
    return float("inf") if hit.is_empty else float(Point(point).distance(hit))


def spike_plans(plan: FloorPlan, design: Design) -> dict[str, list[tuple]]:
    """Work out, for every hub/node and direction, a spike that must trip a given rule.

    Returns {"wall": [...], "window": [...], "outside": [...]} where each entry is
    (hub_id, stop_id, leg_index, direction, length).
    """
    plans: dict[str, list[tuple]] = {"wall": [], "window": [], "outside": []}
    # Shrink walls and windows by 1 cm so a ray that only grazes a door or window
    # edge (which the 1 mm rule allows) is not counted as a hit.
    walls = plan.walls_m.buffer(-0.01)
    windows = plan.windows_m.buffer(-0.01)
    for hub_id, stop_id, leg_index, point in stops(design):
        for direction in DIRECTIONS:
            d_wall = distance_along(point, direction, walls)
            d_window = distance_along(point, direction, windows)
            d_exit = distance_along(point, direction, plan.inner_m.boundary)
            if d_wall < d_window and d_wall < float("inf"):
                plans["wall"].append((hub_id, stop_id, leg_index, direction, d_wall + 0.3))
            if d_window < d_wall and d_window < float("inf"):
                plans["window"].append((hub_id, stop_id, leg_index, direction, d_window + 0.3))
            if d_exit < float("inf"):
                plans["outside"].append((hub_id, stop_id, leg_index, direction, d_exit + 0.5))
    return plans


# ------------------------------------------------------- the ring itself


def drop_node_from_ring(design: Design, hub_id: str, node_id: str) -> Design:
    """The ring no longer stops at the node (its two legs are joined into one)."""
    out = copy(design)
    ring = hub_of(out, hub_id).ring
    k = ring.sequence.index(node_id)
    before, after = ring.legs[k - 1], ring.legs[k]
    merged = RouteLeg(
        from_id=before.from_id,
        to_id=after.to_id,
        polyline=[*before.polyline, *after.polyline[1:]],
    )
    ring.legs[k - 1 : k + 1] = [merged]
    del ring.sequence[k]
    return out


def visit_node_twice(design: Design, hub_id: str, node_id: str) -> Design:
    """The ring visits the node a second time (a zero-length extra leg)."""
    out = copy(design)
    ring = hub_of(out, hub_id).ring
    k = ring.sequence.index(node_id)
    point = ring.legs[k - 1].polyline[-1]
    ring.legs.insert(k, RouteLeg(from_id=node_id, to_id=node_id, polyline=[point, point]))
    ring.sequence.insert(k + 1, node_id)
    return out


def end_ring_at_other_hub(design: Design, hub_id: str, other_hub_id: str) -> Design:
    out = copy(design)
    ring = hub_of(out, hub_id).ring
    ring.sequence[-1] = other_hub_id
    ring.legs[-1].to_id = other_hub_id
    return out


def end_ring_at_a_node(design: Design, hub_id: str) -> Design:
    out = copy(design)
    ring = hub_of(out, hub_id).ring
    ring.sequence[-1] = ring.sequence[1]
    ring.legs[-1].to_id = ring.sequence[1]
    return out


def jump_between_legs(design: Design, hub_id: str, leg_index: int, dx: float = 0.5) -> Design:
    """The end of one leg no longer meets the start of the next."""
    out = copy(design)
    leg = hub_of(out, hub_id).ring.legs[leg_index]
    x, y = leg.polyline[-1]
    leg.polyline = [*leg.polyline[:-1], (x + dx, y)]
    return out


def start_ring_away_from_hub(design: Design, hub_id: str, dy: float = 0.5) -> Design:
    out = copy(design)
    leg = hub_of(out, hub_id).ring.legs[0]
    x, y = leg.polyline[0]
    leg.polyline = [(x, y + dy), *leg.polyline[1:]]
    return out


def shift_node(design: Design, node_id: str, dx: float = 0.3, dy: float = 0.0) -> Design:
    """Cable meets the node somewhere else (both legs that touch it are moved)."""
    out = copy(design)
    for hub in out.hubs:
        for leg in hub.ring.legs:
            if leg.to_id == node_id:
                x, y = leg.polyline[-1]
                leg.polyline = [*leg.polyline[:-1], (x + dx, y + dy)]
            if leg.from_id == node_id:
                x, y = leg.polyline[0]
                leg.polyline = [(x + dx, y + dy), *leg.polyline[1:]]
    return out


def slant_leg(design: Design, hub_id: str, leg_index: int) -> Design:
    """Add a slanted piece at the start of a leg."""
    out = copy(design)
    leg = hub_of(out, hub_id).ring.legs[leg_index]
    x, y = leg.polyline[0]
    leg.polyline = [(x, y), (x + 0.3, y + 0.3), *leg.polyline[1:]]
    return out


# ------------------------------------------------------------- the hub


def move_hub(design: Design, hub_id: str, new: Coord) -> Design:
    """Move the hub; the first and last cable pieces are joined to the new spot with an L."""
    out = copy(design)
    hub = hub_of(out, hub_id)
    old = hub.location
    corner = (new[0], old[1])
    hub.location = new
    first, last = hub.ring.legs[0], hub.ring.legs[-1]
    first.polyline = [new, corner, *first.polyline]
    last.polyline = [*last.polyline, corner, new]
    return out


def wall_point_of_room(plan: FloorPlan, room_id: str) -> Coord:
    """A point on the room's edge that really lies inside a wall."""
    boundary = plan.rooms[room_id].polygon_m.boundary
    for k in range(1, 40):
        point = boundary.interpolate(k / 40, normalized=True)
        if plan.walls_m.contains(point):
            return (point.x, point.y)
    raise ValueError(f"no wall point found on {room_id}")


def near_wall_point_of_room(plan: FloorPlan, room_id: str, gap: float = 0.3) -> Coord:
    """A point inside a (box-shaped) room only ``gap`` metres from its left edge."""
    min_x, min_y, _, max_y = plan.rooms[room_id].polygon_m.bounds
    return (min_x + gap, (min_y + max_y) / 2)


def put_hub_in_room(design: Design, hub_id: str, room_id: str) -> Design:
    out = copy(design)
    hub_of(out, hub_id).room_id = room_id
    return out


# ------------------------------------------------------ the room lists


def node_of_room(plan: FloorPlan, room_id: str) -> str:
    return next(n.node_id for n in plan.nodes.values() if n.room_id == room_id)


def drop_room_from_its_hub(design: Design, plan: FloorPlan, room_id: str) -> Design:
    out = copy(design)
    node_id = node_of_room(plan, room_id)
    for hub in out.hubs:
        if room_id in hub.assigned_rooms:
            hub.assigned_rooms.remove(room_id)
            hub.assigned_nodes.remove(node_id)
    return out


def give_room_to_another_hub(design: Design, plan: FloorPlan, room_id: str, to_hub: str) -> Design:
    out = copy(design)
    hub = hub_of(out, to_hub)
    hub.assigned_rooms.append(room_id)
    hub.assigned_nodes.append(node_of_room(plan, room_id))
    return out


def move_room(design: Design, plan: FloorPlan, room_id: str, to_hub: str) -> Design:
    """Take the room (and its node) from its current hub and give it to another one."""
    return give_room_to_another_hub(drop_room_from_its_hub(design, plan, room_id), plan, room_id, to_hub)


def pile_everything_on(design: Design, plan: FloorPlan, hub_id: str) -> Design:
    out = copy(design)
    target = hub_of(out, hub_id)
    for hub in out.hubs:
        if hub is not target:
            target.assigned_rooms.extend(hub.assigned_rooms)
            target.assigned_nodes.extend(hub.assigned_nodes)
            hub.assigned_rooms, hub.assigned_nodes = [], []
    return out


# ---------------------------------------------------------- the format


def wrong_plan_id(design: Design) -> Design:
    out = copy(design)
    out.plan_id = "SOMETHING_ELSE"
    return out


def unknown_room(design: Design) -> Design:
    out = copy(design)
    out.hubs[0].assigned_rooms.append("R99")
    return out


def duplicate_hub_id(design: Design) -> Design:
    out = copy(design)
    out.hubs[-1].hub_id = out.hubs[0].hub_id
    return out


def assign_excluded_room(design: Design, room_id: str) -> Design:
    out = copy(design)
    out.hubs[0].assigned_rooms.append(room_id)
    return out


# ------------------------------------------------- harmless changes (stay valid)


def split_straight_pieces(design: Design) -> Design:
    """Cut every straight piece in two with an extra point on the same line."""
    out = copy(design)
    for hub in out.hubs:
        for leg in hub.ring.legs:
            points: list[Coord] = [leg.polyline[0]]
            for p, q in zip(leg.polyline, leg.polyline[1:], strict=False):
                points.append(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2))
                points.append(q)
            leg.polyline = points
    return out


def nudge_everything(design: Design, dx: float = 0.0004, dy: float = -0.0004) -> Design:
    """Shift every coordinate by well under a millimetre."""
    out = copy(design)
    for hub in out.hubs:
        hub.location = (hub.location[0] + dx, hub.location[1] + dy)
        for leg in hub.ring.legs:
            leg.polyline = [(x + dx, y + dy) for x, y in leg.polyline]
    return out


def reverse_ring(design: Design, hub_id: str) -> Design:
    """Visit the nodes in the opposite order along the same cable."""
    out = copy(design)
    ring = hub_of(out, hub_id).ring
    ring.legs = [
        RouteLeg(from_id=leg.to_id, to_id=leg.from_id, polyline=list(reversed(leg.polyline)), doors_used=leg.doors_used)
        for leg in reversed(ring.legs)
    ]
    ring.sequence = list(reversed(ring.sequence))
    return out


def reverse_every_ring(design: Design) -> Design:
    out = design
    for hub in design.hubs:
        out = reverse_ring(out, hub.hub_id)
    return out
