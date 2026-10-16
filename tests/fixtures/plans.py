"""Practice houses, built by hand (everything in metres).

* ``tiny_plan``    two rooms joined by one door.
* ``slide5_plan``  six eligible rooms plus a bathroom, shaped like the
  organisers' slide 5 picture (areas 16/16/16/15/40/15 m2 = 118 m2, so at
  least 2 hubs are needed).

Walls are thin strips (0.1 m) around every room edge, with gaps left for doors
and windows.  Free space is the inside of the house minus walls and windows.
"""

from __future__ import annotations

from shapely.affinity import scale as scale_geometry
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from othello.models import FloorPlan, Node, Opening, Room

SCALE = 18.0  # drawing units per metre (a typical ResPlan value)
WALL = 0.1  # wall thickness in metres
HUB_CLEARANCE = 0.5

ELIGIBLE_KINDS = ("living", "bedroom", "kitchen", "storage")

Gap = list[tuple[float, float]]


def _gap_strip(gap: Gap, half_width: float) -> Polygon:
    """A flat-ended strip centred on a gap line; it cuts a hole in a wall."""
    return LineString(gap).buffer(half_width, cap_style="flat")


def _build_plan(
    plan_id: str,
    rooms: list[tuple[str, str, Polygon]],
    nodes: list[tuple[str, str, tuple[float, float]]],
    openings: list[tuple[str, str, list[str], Gap, bool]],
    windows: list[Gap] | None = None,
) -> FloorPlan:
    windows = windows or []
    polygons = {room_id: polygon for room_id, _, polygon in rooms}
    inner = unary_union(list(polygons.values())).buffer(0)

    edges = unary_union([polygon.exterior for polygon in polygons.values()])
    walls = edges.buffer(WALL / 2, cap_style="flat", join_style="mitre")
    door_cuts = unary_union([_gap_strip(gap, WALL) for _, _, _, gap, _ in openings])
    window_cuts = unary_union([_gap_strip(gap, WALL) for gap in windows]) if windows else Polygon()
    walls_m = walls.difference(door_cuts).difference(window_cuts)
    windows_m = (
        unary_union([_gap_strip(gap, WALL / 2 + 0.01) for gap in windows]) if windows else Polygon()
    )
    free_space = inner.difference(walls_m).difference(windows_m)

    room_models: dict[str, Room] = {}
    for room_id, kind, polygon in rooms:
        room_models[room_id] = Room(
            room_id=room_id,
            kind=kind,
            eligible=kind in ELIGIBLE_KINDS,
            polygon_m=polygon,
            polygon_units=scale_geometry(polygon, SCALE, SCALE, origin=(0, 0)),
            area_m2=polygon.area,
            inside_inner=True,
        )

    node_models = {
        node_id: Node(
            node_id=node_id,
            room_id=room_id,
            point_m=point,
            point_units=(point[0] * SCALE, point[1] * SCALE),
        )
        for node_id, room_id, point in nodes
    }

    opening_models = {
        opening_id: Opening(
            opening_id=opening_id,
            kind=kind,
            rooms=room_ids,
            gap_polyline_m=gap,
            counts_for_zone=counts,
        )
        for opening_id, kind, room_ids, gap, counts in openings
    }

    eligible_ids = [rid for rid, room in room_models.items() if room.eligible]
    room_graph: dict[str, list[str]] = {rid: [] for rid in eligible_ids}
    for opening in opening_models.values():
        if not opening.counts_for_zone or len(opening.rooms) != 2:
            continue
        a, b = opening.rooms
        if a in room_graph and b in room_graph:
            room_graph[a].append(b)
            room_graph[b].append(a)

    # Hub zone = room minus 0.5 m around the walls AND the windows (a window is a wall
    # for clearance), so the zone never leaks into a window opening.
    keep_away = unary_union([walls_m, windows_m]).buffer(HUB_CLEARANCE)
    hub_zones = {rid: polygons[rid].difference(keep_away) for rid in eligible_ids}

    return FloorPlan(
        plan_id=plan_id,
        scale=SCALE,
        rooms=room_models,
        nodes=node_models,
        openings=opening_models,
        inner_m=inner,
        walls_m=walls_m,
        windows_m=windows_m,
        free_space_m=free_space,
        hub_zones_m=hub_zones,
        room_graph=room_graph,
        excluded_interior_rooms=[rid for rid, room in room_models.items() if not room.eligible],
        output_transform={"scale": SCALE, "origin": [0.0, 0.0], "flip_y": False},
        repairs=[],
    )


def tiny_plan() -> FloorPlan:
    """Two 4 x 4 m rooms (living, bedroom) joined by one door in the middle wall."""
    return _build_plan(
        "TINY_PLAN",
        rooms=[
            ("R01", "living", box(0, 0, 4, 4)),
            ("R02", "bedroom", box(4, 0, 8, 4)),
        ],
        nodes=[("N1", "R01", (2.0, 2.0)), ("N2", "R02", (6.0, 2.0))],
        openings=[("D1", "door", ["R01", "R02"], [(4.0, 1.5), (4.0, 2.5)], True)],
    )


def slide5_plan() -> FloorPlan:
    """Seven rooms in a 14 x 9 m house, like slide 5.

        y=9  +------+------+------+--+--+
             |  R01 |  R02 |  R03 | R07 |      R01 Bedroom 1  16 m2   node N1
             |  bed |  bed | kitch| bath|      R02 Bedroom 2  16 m2   node N2
        y=5  +--+---+------+--+---+-----+      R03 Kitchen    16 m2   node N3
             |R04|     R05    |  R06    |      R04 Bedroom 3  15 m2   node N4
             |bed|    living  | storage |      R05 Living     40 m2   node N5
        y=0  +---+------------+---------+      R06 Storage    15 m2   node N6
             x=0  3           11        14     R07 Bathroom    8 m2   (excluded)
    """
    return _build_plan(
        "SLIDE5_PLAN",
        rooms=[
            ("R01", "bedroom", box(0, 5, 4, 9)),
            ("R02", "bedroom", box(4, 5, 8, 9)),
            ("R03", "kitchen", box(8, 5, 12, 9)),
            ("R04", "bedroom", box(0, 0, 3, 5)),
            ("R05", "living", box(3, 0, 11, 5)),
            ("R06", "storage", box(11, 0, 14, 5)),
            ("R07", "bathroom", box(12, 5, 14, 9)),
        ],
        nodes=[
            ("N1", "R01", (2.0, 7.0)),
            ("N2", "R02", (6.0, 7.0)),
            ("N3", "R03", (10.0, 7.0)),
            ("N4", "R04", (1.5, 2.5)),
            ("N5", "R05", (7.0, 2.5)),
            ("N6", "R06", (12.5, 2.5)),
        ],
        openings=[
            ("D1", "door", ["R01", "R05"], [(3.05, 5.0), (3.95, 5.0)], True),
            ("D2", "door", ["R02", "R05"], [(5.1, 5.0), (6.0, 5.0)], True),
            ("D3", "door", ["R03", "R05"], [(9.1, 5.0), (10.0, 5.0)], True),
            ("D4", "door", ["R04", "R05"], [(3.0, 1.0), (3.0, 1.9)], True),
            ("D5", "door", ["R05", "R06"], [(11.0, 1.0), (11.0, 1.9)], True),
            ("D6", "door", ["R03", "R07"], [(12.0, 6.0), (12.0, 6.9)], True),
            ("D7", "door", ["R02", "R03"], [(8.0, 6.0), (8.0, 6.9)], True),
            ("D8", "door", ["R03", "R06"], [(11.1, 5.0), (11.9, 5.0)], True),
            ("E1", "entrance", ["R05"], [(6.0, 0.0), (7.0, 0.0)], False),
        ],
        windows=[
            [(1.0, 9.0), (3.0, 9.0)],
            [(9.0, 9.0), (11.0, 9.0)],
            [(8.0, 0.0), (10.0, 0.0)],
        ],
    )


# Hub areas of the good slide 5 design (H1: R05+R01+R04, H2: R03+R02+R06).
SLIDE5_HUB_AREAS = {"H1": 71.0, "H2": 47.0}
TINY_HUB_AREAS = {"H1": 32.0}


def point_of(plan: FloorPlan, node_id: str) -> Point:
    x, y = plan.nodes[node_id].point_m
    return Point(x, y)
