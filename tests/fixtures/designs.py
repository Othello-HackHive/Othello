"""Practice cable plans for the practice houses in ``plans.py``.

Good plans (all numbers worked out by hand):

* ``tiny_design``      1 hub, 2 nodes.   L = 10 m, X = 0, O = 0, B = 6, J = 13.
* ``slide5_design``    2 hubs, 6 nodes.  L = 50.8 m, X = 0, O = 0, B = 28, J = 64.8.
      H1 (living, bedroom 1, bedroom 3): L = 27.2, B = 12.
      H2 (kitchen, bedroom 2, storage):  L = 23.6, B = 16.
  Single-door rooms are entered and left on two different parallel lines, so
  there is no overlap.

Valid but costly (the checker must still call these valid):

* ``slide5_with_crossing``   one crossing at (3.3, 6.0).
* ``slide5_with_overlap``    cables share lines (overlap > 0).

Deliberately broken (each breaks one thing):

* ``broken_*`` functions below.  Names say what is wrong.
"""

from __future__ import annotations

import itertools

from othello.models.design import Design, Hub, Ring, RouteLeg

Coord = tuple[float, float]


def _leg(from_id: str, to_id: str, polyline: list[Coord]) -> RouteLeg:
    return RouteLeg(from_id=from_id, to_id=to_id, polyline=polyline)


def _hub(
    hub_id: str,
    room_id: str,
    location: Coord,
    rooms: list[str],
    nodes: list[str],
    legs: list[RouteLeg],
) -> Hub:
    sequence = [legs[0].from_id] + [leg.to_id for leg in legs]
    ring = Ring(hub_id=hub_id, sequence=sequence, legs=legs)
    return Hub(
        hub_id=hub_id,
        room_id=room_id,
        location=location,
        assigned_rooms=rooms,
        assigned_nodes=nodes,
        ring=ring,
    )


# ------------------------------------------------------------- tiny house


def tiny_design() -> Design:
    legs = [
        _leg("H1", "N1", [(3.0, 3.0), (2.0, 3.0), (2.0, 2.0)]),
        _leg("N1", "N2", [(2.0, 2.0), (6.0, 2.0)]),
        _leg("N2", "H1", [(6.0, 2.0), (6.0, 2.3), (3.0, 2.3), (3.0, 3.0)]),
    ]
    hub = _hub("H1", "R01", (3.0, 3.0), ["R01", "R02"], ["N1", "N2"], legs)
    return Design(plan_id="TINY_PLAN", hubs=[hub])


# ----------------------------------------------------------- slide 5 house

H1_LOC: Coord = (5.0, 3.5)
H2_LOC: Coord = (11.0, 6.0)

H1_LEGS = {
    "to_N5": [(5.0, 3.5), (7.0, 3.5), (7.0, 2.5)],
    "to_N4": [(7.0, 2.5), (7.0, 1.5), (1.5, 1.5), (1.5, 2.5)],
    "to_N1": [(1.5, 2.5), (2.5, 2.5), (2.5, 1.7), (3.3, 1.7), (3.3, 7.0), (2.0, 7.0)],
    "to_H1": [(2.0, 7.0), (2.0, 7.5), (3.7, 7.5), (3.7, 3.5), (5.0, 3.5)],
}

H2_LEGS = {
    "to_N6": [(11.0, 6.0), (11.5, 6.0), (11.5, 2.5), (12.5, 2.5)],
    "to_N3": [(12.5, 2.5), (12.5, 4.0), (11.8, 4.0), (11.8, 6.5), (10.0, 6.5), (10.0, 7.0)],
    "to_N2": [(10.0, 7.0), (9.0, 7.0), (9.0, 6.7), (6.0, 6.7), (6.0, 7.0)],
    "to_H2": [(6.0, 7.0), (5.5, 7.0), (5.5, 6.3), (11.0, 6.3), (11.0, 6.0)],
}


def _h1(legs: dict[str, list[Coord]] | None = None) -> Hub:
    p = legs or H1_LEGS
    return _hub(
        "H1",
        "R05",
        H1_LOC,
        ["R05", "R01", "R04"],
        ["N5", "N1", "N4"],
        [
            _leg("H1", "N5", p["to_N5"]),
            _leg("N5", "N4", p["to_N4"]),
            _leg("N4", "N1", p["to_N1"]),
            _leg("N1", "H1", p["to_H1"]),
        ],
    )


def _h2(legs: dict[str, list[Coord]] | None = None) -> Hub:
    p = legs or H2_LEGS
    return _hub(
        "H2",
        "R03",
        H2_LOC,
        ["R03", "R02", "R06"],
        ["N3", "N2", "N6"],
        [
            _leg("H2", "N6", p["to_N6"]),
            _leg("N6", "N3", p["to_N3"]),
            _leg("N3", "N2", p["to_N2"]),
            _leg("N2", "H2", p["to_H2"]),
        ],
    )


def slide5_design() -> Design:
    return Design(plan_id="SLIDE5_PLAN", hubs=[_h1(), _h2()])


# ------------------------------------------- valid, but with extra cost


def slide5_with_crossing() -> Design:
    """H1's last leg cuts across its own third leg: one crossing at (3.3, 6.0)."""
    legs = dict(H1_LEGS)
    legs["to_H1"] = [(2.0, 7.0), (2.0, 6.0), (3.7, 6.0), (3.7, 3.5), (5.0, 3.5)]
    return Design(plan_id="SLIDE5_PLAN", hubs=[_h1(legs), _h2()])


def slide5_with_overlap() -> Design:
    """H1's last leg lies on top of its third leg: 1.3 m + 3.5 m overlap."""
    legs = dict(H1_LEGS)
    legs["to_H1"] = [(2.0, 7.0), (3.3, 7.0), (3.3, 3.5), (5.0, 3.5)]
    return Design(plan_id="SLIDE5_PLAN", hubs=[_h1(legs), _h2()])


# ---------------------------------------------------- deliberately broken


def broken_missing_node() -> Design:
    """H1's ring skips N4 (but H1 still owns it): RING_NODE_MISSING."""
    hub = _hub(
        "H1",
        "R05",
        H1_LOC,
        ["R05", "R01", "R04"],
        ["N5", "N1", "N4"],
        [
            _leg("H1", "N5", H1_LEGS["to_N5"]),
            _leg("N5", "N1", [(7.0, 2.5), (3.4, 2.5), (3.4, 7.0), (2.0, 7.0)]),
            _leg("N1", "H1", H1_LEGS["to_H1"]),
        ],
    )
    return Design(plan_id="SLIDE5_PLAN", hubs=[hub, _h2()])


def broken_ring_ends_at_other_hub() -> Design:
    """H1's last leg is labelled as going to H2: RING_WRONG_HUB."""
    design = slide5_design()
    ring = design.hubs[0].ring
    ring.legs[-1].to_id = "H2"
    ring.sequence[-1] = "H2"
    return design


def broken_capacity_all_in_one_hub() -> Design:
    """One hub owns all six rooms (6 nodes, 118 m2): CAPACITY_NODES and CAPACITY_AREA.

    Only the lists matter here; the cable pieces are plain L-shapes that may
    cross walls.
    """

    def l_shape(a: Coord, b: Coord) -> list[Coord]:
        return [a, (b[0], a[1]), b]

    stops = [
        ("H1", H1_LOC),
        ("N5", (7.0, 2.5)),
        ("N4", (1.5, 2.5)),
        ("N1", (2.0, 7.0)),
        ("N2", (6.0, 7.0)),
        ("N3", (10.0, 7.0)),
        ("N6", (12.5, 2.5)),
        ("H1", H1_LOC),
    ]
    legs = [_leg(a[0], b[0], l_shape(a[1], b[1])) for a, b in itertools.pairwise(stops)]
    hub = _hub(
        "H1",
        "R05",
        H1_LOC,
        ["R01", "R02", "R03", "R04", "R05", "R06"],
        ["N1", "N2", "N3", "N4", "N5", "N6"],
        legs,
    )
    return Design(plan_id="SLIDE5_PLAN", hubs=[hub])


def broken_hub_in_wall() -> Design:
    """H1's hub sits on the wall at (3.0, 2.5): HUB_WALL_CLEARANCE."""
    design = slide5_design()
    hub = design.hubs[0]
    hub.location = (3.0, 2.5)
    hub.ring.legs[0].polyline = [(3.0, 2.5), (3.0, 3.5), (7.0, 3.5), (7.0, 2.5)]
    hub.ring.legs[-1].polyline = [(2.0, 7.0), (2.0, 7.5), (3.7, 7.5), (3.7, 3.5), (3.0, 3.5), (3.0, 2.5)]
    return design


def broken_hub_too_close_to_wall() -> Design:
    """H1's hub is inside the living room but only 0.3 m from a wall: HUB_WALL_CLEARANCE."""
    design = slide5_design()
    hub = design.hubs[0]
    hub.location = (3.3, 3.5)
    hub.ring.legs[0].polyline = [(3.3, 3.5), (7.0, 3.5), (7.0, 2.5)]
    hub.ring.legs[-1].polyline = [(2.0, 7.0), (2.0, 7.5), (3.7, 7.5), (3.7, 3.5), (3.3, 3.5)]
    return design


def broken_cable_through_window() -> Design:
    """H1's first leg leaves through the window at the bottom wall: WINDOW_INTERSECTION."""
    design = slide5_design()
    design.hubs[0].ring.legs[0].polyline = [
        (5.0, 3.5),
        (9.0, 3.5),
        (9.0, -0.5),
        (7.0, -0.5),
        (7.0, 2.5),
    ]
    return design


def broken_cable_through_wall() -> Design:
    """H1's third leg goes straight through a wall with no door: WALL_INTERSECTION."""
    design = slide5_design()
    design.hubs[0].ring.legs[2].polyline = [(1.5, 2.5), (1.5, 7.0), (2.0, 7.0)]
    return design


def broken_diagonal_segment() -> Design:
    """H1's first leg is a slanted line: SEGMENT_NOT_AXIS_ALIGNED."""
    design = slide5_design()
    design.hubs[0].ring.legs[0].polyline = [(5.0, 3.5), (7.0, 2.5)]
    return design


def broken_cable_outside_inner() -> Design:
    """H2's first leg runs out past the east wall: OUTSIDE_INNER."""
    design = slide5_design()
    design.hubs[1].ring.legs[0].polyline = [
        (11.0, 6.0),
        (11.5, 6.0),
        (11.5, 2.5),
        (15.0, 2.5),
        (12.5, 2.5),
    ]
    return design


def broken_territory_disconnected() -> Design:
    """H2 also takes bedroom 3 (R04), which only touches the living room: ZONE_DISCONNECTED.

    Only the lists are changed; the cables are not re-routed.
    """
    design = slide5_design()
    design.hubs[0].assigned_rooms = ["R05", "R01"]
    design.hubs[0].assigned_nodes = ["N5", "N1"]
    design.hubs[1].assigned_rooms = ["R03", "R02", "R06", "R04"]
    design.hubs[1].assigned_nodes = ["N3", "N2", "N6", "N4"]
    return design


def broken_coverage_missing() -> Design:
    """Nobody owns the storage room (R06 / N6): COVERAGE_MISSING."""
    design = slide5_design()
    design.hubs[1].assigned_rooms = ["R03", "R02"]
    design.hubs[1].assigned_nodes = ["N3", "N2"]
    return design


def broken_coverage_duplicate() -> Design:
    """Both hubs claim the storage room (R06 / N6): COVERAGE_DUPLICATE."""
    design = slide5_design()
    design.hubs[0].assigned_rooms = ["R05", "R01", "R04", "R06"]
    design.hubs[0].assigned_nodes = ["N5", "N1", "N4", "N6"]
    return design


def broken_node_moved() -> Design:
    """The cable meets node N1 at (2.3, 7.0) instead of (2.0, 7.0): NODE_MOVED."""
    design = slide5_design()
    legs = design.hubs[0].ring.legs
    legs[2].polyline = [*legs[2].polyline[:-1], (2.3, 7.0)]
    legs[3].polyline = [(2.3, 7.0), (2.3, 7.5), (3.7, 7.5), (3.7, 3.5), (5.0, 3.5)]
    return design


ALL_BROKEN = {
    "missing_node": broken_missing_node,
    "ring_ends_at_other_hub": broken_ring_ends_at_other_hub,
    "capacity_all_in_one_hub": broken_capacity_all_in_one_hub,
    "hub_in_wall": broken_hub_in_wall,
    "hub_too_close_to_wall": broken_hub_too_close_to_wall,
    "cable_through_window": broken_cable_through_window,
    "cable_through_wall": broken_cable_through_wall,
    "diagonal_segment": broken_diagonal_segment,
    "cable_outside_inner": broken_cable_outside_inner,
    "territory_disconnected": broken_territory_disconnected,
    "coverage_missing": broken_coverage_missing,
    "coverage_duplicate": broken_coverage_duplicate,
    "node_moved": broken_node_moved,
}
