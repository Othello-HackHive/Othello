"""Task 3.5: enumerate directed node orders and retain a diverse shortlist."""

from __future__ import annotations

from itertools import permutations
import math
import time
from typing import Any, Sequence

from othello.routing.astar import _TimedOut as _RoutingTimedOut
from othello.routing.table import DistanceTable

from .types import NodeOrder, NodeOrderResult


class _TimedOut(Exception):
    pass


def _check(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise _TimedOut


def _select_diverse(ranked: list[NodeOrder], cap: int, deadline: float | None) -> tuple[NodeOrder, ...]:
    selected: list[NodeOrder] = []
    selected_ids: set[tuple[str, ...]] = set()
    first_seen: set[str | None] = set()
    last_seen: set[str | None] = set()
    while len(selected) < min(cap, len(ranked)):
        _check(deadline)
        remaining = [order for order in ranked if order.node_ids not in selected_ids]
        if not selected:
            choice = remaining[0]
        else:
            novel = [order for order in remaining if
                     order.first_door_id not in first_seen or order.last_door_id not in last_seen]
            if not novel:
                break
            choice = min(novel, key=lambda order: (
                -(int(order.first_door_id not in first_seen) + int(order.last_door_id not in last_seen)),
                order.estimated_length_m, order.node_ids,
            ))
        selected.append(choice)
        selected_ids.add(choice.node_ids)
        first_seen.add(choice.first_door_id)
        last_seen.add(choice.last_door_id)
    for order in ranked:
        _check(deadline)
        if len(selected) >= cap:
            break
        if order.node_ids not in selected_ids:
            selected.append(order)
            selected_ids.add(order.node_ids)
    return tuple(selected)


def enumerate_visiting_orders(
    hub_id: str, node_ids: Sequence[str], table: Any, config: Any,
    deadline: float | None = None,
) -> NodeOrderResult:
    """Evaluate all k! directed cycles using cached obstacle-aware leg lengths.

    A failed leg only invalidates orders containing it. Unsupported geometry and
    timeouts stop the whole ranking; no partial shortlist is returned. The
    returned distance is an estimate from independently routed legs and does
    not include complete-ring bends, crossings, overlap, or official J.
    """
    if not isinstance(hub_id, str) or not hub_id:
        raise ValueError("hub_id must be a nonempty string")
    maximum = getattr(config, "max_nodes_per_hub", 5)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 5:
        raise ValueError("max_nodes_per_hub must be between 1 and 5")
    if not isinstance(node_ids, (tuple, list)) or not 1 <= len(node_ids) <= maximum:
        raise ValueError(f"node_ids must contain 1 to {maximum} fixed nodes")
    if any(not isinstance(node_id, str) or not node_id for node_id in node_ids):
        raise ValueError("node_ids must be nonempty strings")
    if len(set(node_ids)) != len(node_ids) or hub_id in node_ids:
        raise ValueError("Each node must appear exactly once and differ from the hub")
    try:
        cap = config.limits.orders_kept
    except AttributeError as exc:
        raise ValueError("config needs limits.orders_kept") from exc
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
        raise ValueError("limits.orders_kept must be a positive integer")
    if not callable(getattr(table, "get_route", None)):
        raise ValueError("table must provide get_route(from_id, to_id)")
    if not all(stop_id in table.stops for stop_id in (hub_id, *node_ids)):
        raise ValueError("hub and node IDs must be present in DistanceTable.stops")
    if deadline is not None and (not isinstance(deadline, (float, int)) or
                                 isinstance(deadline, bool) or not math.isfinite(deadline)):
        raise ValueError("deadline must be a finite absolute monotonic time")

    enumerated = 0
    try:
        table_deadline = getattr(table, "deadline", None)
        budgets = [value for value in (deadline, table_deadline) if value is not None]
        effective = min(budgets) if budgets else None
        _check(effective)
        query = table
        if isinstance(table, DistanceTable) and effective != table.deadline:
            query = DistanceTable(table.plan, table.stops, table.config, table.cache, effective)
        leg_cache: dict[tuple[str, str], Any] = {}
        feasible: list[NodeOrder] = []
        for order in permutations(sorted(node_ids)):
            _check(effective)
            enumerated += 1
            sequence = (hub_id, *order, hub_id)
            lengths: list[float] = []
            openings: list[str] = []
            feasible_order = True
            for source, target in zip(sequence, sequence[1:]):
                _check(effective)
                pair = (source, target)
                if pair not in leg_cache:
                    leg_cache[pair] = query.get_route(source, target)
                route = leg_cache[pair]
                _check(effective)
                if route.status == "timeout":
                    return NodeOrderResult("timeout", enumerated_count=enumerated,
                                           diagnostics=tuple(route.diagnostics))
                if route.status == "unsupported_geometry":
                    return NodeOrderResult("unsupported_geometry", enumerated_count=enumerated,
                                           diagnostics=tuple(route.diagnostics))
                if route.status in {"unreachable", "unreachable_on_grid"}:
                    feasible_order = False
                    break
                if route.status != "success" or route.length_m is None or route.opening_ids is None:
                    raise ValueError("DistanceTable returned an invalid route result")
                length = float(route.length_m)
                if not math.isfinite(length) or length < 0:
                    raise ValueError("DistanceTable returned an invalid leg length")
                lengths.append(length)
                openings.extend(route.opening_ids)
            if feasible_order:
                total = math.fsum(lengths)
                if not math.isfinite(total):
                    raise ValueError("Estimated cycle length is not finite")
                opening_ids = tuple(openings)
                feasible.append(NodeOrder(order, sequence, total, opening_ids,
                                          opening_ids[0] if opening_ids else None,
                                          opening_ids[-1] if opening_ids else None))
        _check(effective)
        if not feasible:
            return NodeOrderResult("unreachable_on_grid", enumerated_count=enumerated, diagnostics=(
                "No complete order routed on the supplied search graphs; geometric infeasibility is not proven.",
            ))
        ranked = sorted(feasible, key=lambda order: (order.estimated_length_m, order.node_ids))
        selected = _select_diverse(ranked, cap, effective)
        _check(effective)
        return NodeOrderResult("ready", selected, enumerated)
    except (_TimedOut, _RoutingTimedOut):
        return NodeOrderResult("timeout", enumerated_count=enumerated,
                               diagnostics=("Node-order deadline expired; no partial shortlist returned.",))
