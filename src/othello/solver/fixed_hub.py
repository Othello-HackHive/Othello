"""Bounded AWS rerouting after a user fixes one hub at a chosen position."""

from __future__ import annotations

import time
from copy import deepcopy

from shapely.geometry import Point

from othello.checker import check_design
from othello.io.official import build_official, design_from_official
from othello.models.design import Design
from othello.routing import DistanceTable, RoutingCache

from .improvement import _Expired, _routed_hub, _stops, _Unsupported
from .node_orders import enumerate_visiting_orders


def solve_fixed_hub_edit(plan, design: Design, hub_id: str, config, *, budget_s: float = 12.0):
    """Reroute one ring on the server while preserving every hub and assignment.

    The moved point is fixed. Candidate scores come only from the independent
    checker, including a round trip through the official output format.
    """
    base = design.model_dump(mode="python")
    matches = [index for index, hub in enumerate(base["hubs"]) if hub["hub_id"] == hub_id]
    if len(matches) != 1:
        raise ValueError("the selected hub is not in this design")
    index = matches[0]
    hub = base["hubs"][index]
    point = tuple(hub["location"])
    room_id = hub["room_id"]
    zone = plan.hub_zones_m.get(room_id)
    if room_id not in hub["assigned_rooms"] or zone is None or not zone.covers(Point(point)):
        raise ValueError("the moved hub must be inside its assigned room's legal hub zone")

    deadline = time.monotonic() + budget_s
    table = DistanceTable(plan, _stops(plan, hub, point), config, RoutingCache(), deadline)
    best = None
    best_key = None
    seen = set()

    def try_order(sequence):
        nonlocal best, best_key
        sequence = tuple(sequence)
        if sequence in seen or time.monotonic() >= deadline:
            return
        seen.add(sequence)
        replacement = _routed_hub(hub, room_id, point, sequence, table, deadline)
        if replacement is None:
            return
        candidate = deepcopy(base)
        candidate["hubs"][index] = replacement
        official = build_official(plan, Design.model_validate(candidate), config)
        checked = check_design(plan, design_from_official(plan, official), config)
        if checked.valid and checked.metrics is not None:
            key = (checked.metrics.J, sequence)
            if best_key is None or key < best_key:
                best, best_key = (official, checked), key

    try:
        try_order(hub["ring"]["sequence"])
        if time.monotonic() < deadline:
            ranked = enumerate_visiting_orders(hub_id, hub["assigned_nodes"], table, config, deadline)
            if ranked.status == "ready":
                for order in ranked.orders:
                    try_order(order.sequence)
    except (_Expired, _Unsupported):
        pass
    return best
