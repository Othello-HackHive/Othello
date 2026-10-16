"""Task 3.8 foundation: assemble one simple, unverified closed-ring design."""

from __future__ import annotations

from collections.abc import Mapping
import math
import time
from typing import Any

from othello.routing import RoutingCache

from .candidate_tables import candidate_tables
from .hub_candidates import HubCandidateLimitError, generate_hub_candidates
from .node_orders import enumerate_visiting_orders
from .types import F0Result


def _expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _input_territories(plan: Any, partition: Any) -> tuple[Any, ...]:
    """Check only fields needed to assemble Contract 2; Part 1 owns H1-H3."""
    try:
        plan_id = plan.plan_id
        nodes = plan.nodes
        territories = tuple(partition.territories)
        hub_count = partition.hub_count
    except (AttributeError, TypeError) as exc:
        raise ValueError("plan and partition need Contract 1 fields") from exc
    if not isinstance(plan_id, str) or not plan_id or not isinstance(nodes, Mapping):
        raise ValueError("plan needs a nonempty plan_id and node mapping")
    if isinstance(hub_count, bool) or not isinstance(hub_count, int) or hub_count != len(territories) or not territories:
        raise ValueError("partition.hub_count must equal its nonempty territory count")
    seen_territories: set[str] = set()
    seen_nodes: set[str] = set()
    for territory in territories:
        try:
            territory_id = territory.territory_id
            room_ids = tuple(territory.room_ids)
            node_ids = tuple(territory.node_ids)
        except (AttributeError, TypeError) as exc:
            raise ValueError("territory needs Contract 1 fields") from exc
        if not isinstance(territory_id, str) or not territory_id or territory_id in seen_territories:
            raise ValueError("territory IDs must be unique nonempty strings")
        if not room_ids or len(room_ids) != len(set(room_ids)) or not node_ids or len(node_ids) != len(set(node_ids)):
            raise ValueError("each territory needs distinct assigned rooms and nodes")
        if seen_nodes.intersection(node_ids) or any(node_id not in nodes for node_id in node_ids):
            raise ValueError("assigned node IDs must exist and be unique across territories")
        seen_territories.add(territory_id)
        seen_nodes.update(node_ids)
    return tuple(sorted(territories, key=lambda territory: territory.territory_id))


def _nearest_node_order(hub_point: tuple[float, float], node_ids: tuple[str, ...],
                        nodes: Mapping[str, Any]) -> tuple[str, ...]:
    """Deterministic Manhattan proxy order; routing still validates every leg."""
    remaining = set(node_ids)
    position = hub_point
    chosen: list[str] = []
    while remaining:
        node_id = min(remaining, key=lambda ident: (
            abs(position[0] - nodes[ident].point_m[0]) +
            abs(position[1] - nodes[ident].point_m[1]), ident))
        chosen.append(node_id)
        remaining.remove(node_id)
        position = nodes[node_id].point_m
    return tuple(chosen)


def construct_quick_f0_design(
    plan: Any, partition: Any, config: Any, cache: RoutingCache | None = None,
    deadline: float | None = None,
) -> F0Result:
    """Try at most three valid hub positions per territory and one greedy order."""
    return construct_f0_design(plan, partition, config, cache, deadline, _quick=True)


def construct_f0_design(
    plan: Any, partition: Any, config: Any, cache: RoutingCache | None = None,
    deadline: float | None = None, *, _quick: bool = False,
) -> F0Result:
    """Try candidate hubs and ranked orders, retaining the first complete ring.

    Every nonzero leg comes from DistanceTable's true-geometry-validated graph.
    No Part 2 checker exists here, so even a complete result is unverified.
    A coincident leg is encoded as [point, point] for Contract 2's >=2-point
    Polyline field; whether Part 2 accepts that encoding remains an integration
    question. No required visit or leg is removed.
    """
    if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                                 or not math.isfinite(deadline)):
        raise ValueError("deadline must be a finite absolute monotonic time")
    if cache is not None and not isinstance(cache, RoutingCache):
        raise ValueError("cache must be a RoutingCache")
    if _expired(deadline):
        return F0Result("timeout", diagnostics=("F0 deadline expired before construction",))
    territories = _input_territories(plan, partition)
    route_cache = cache if cache is not None else RoutingCache()
    hubs: list[dict[str, Any]] = []
    coincident_legs = 0

    for index, territory in enumerate(territories, start=1):
        if _expired(deadline):
            return F0Result("timeout", diagnostics=("F0 deadline expired; no partial design returned",))
        hub_id = f"H{index}"
        if hub_id in plan.nodes:
            raise ValueError(f"Generated hub ID {hub_id} collides with a node ID")
        try:
            candidates = generate_hub_candidates(plan, territory, config, deadline)
        except HubCandidateLimitError as exc:
            if _expired(deadline):
                return F0Result("timeout", diagnostics=("F0 deadline expired; no partial design returned",))
            return F0Result("candidate_configuration_error", diagnostics=(
                f"{territory.territory_id}: {exc}",
            ))
        if candidates.status != "ready":
            return F0Result(candidates.status, diagnostics=tuple(
                f"{territory.territory_id}: {detail}" for detail in candidates.diagnostics
            ))
        chosen: dict[str, Any] | None = None
        chosen_coincident = 0
        candidate_pool = candidates.candidates[:3] if _quick else candidates.candidates
        for candidate, table in candidate_tables(plan, hub_id, territory.node_ids,
                                                 candidate_pool, config, route_cache, deadline):
            if _expired(deadline):
                return F0Result("timeout", diagnostics=("F0 deadline expired; no partial design returned",))
            stops = table.stops
            if _quick:
                ordered_nodes = _nearest_node_order(candidate.point_m, tuple(territory.node_ids), plan.nodes)
                sequences = ((hub_id, *ordered_nodes, hub_id),)
            else:
                orders = enumerate_visiting_orders(hub_id, territory.node_ids, table, config, deadline)
                if orders.status in {"timeout", "unsupported_geometry"}:
                    return F0Result(orders.status, diagnostics=tuple(
                        f"{territory.territory_id}: {detail}" for detail in orders.diagnostics
                    ))
                if orders.status != "ready":
                    continue
                sequences = tuple(order.sequence for order in orders.orders)
            for sequence in sequences:
                if _expired(deadline):
                    return F0Result("timeout", diagnostics=("F0 deadline expired; no partial design returned",))
                if (sequence[0] != hub_id or sequence[-1] != hub_id or
                        len(sequence) != len(territory.node_ids) + 2 or
                        set(sequence[1:-1]) != set(territory.node_ids)):
                    raise ValueError("node-order result violates ring coverage or hub closure")
                legs: list[dict[str, Any]] = []
                ring_coincident = 0
                failed = False
                for source, target in zip(sequence, sequence[1:]):
                    if _expired(deadline):
                        return F0Result("timeout", diagnostics=("F0 deadline expired; no partial design returned",))
                    route = table.get_route(source, target)
                    if route.status in {"timeout", "unsupported_geometry"}:
                        return F0Result(route.status, diagnostics=tuple(
                            f"{territory.territory_id} {source}->{target}: {detail}" for detail in route.diagnostics
                        ))
                    if route.status != "success":
                        failed = True
                        break
                    if route.polyline is None or route.opening_ids is None:
                        raise ValueError("successful table route lacks geometry or opening metadata")
                    polyline = list(route.polyline)
                    if polyline[0] != stops[source] or polyline[-1] != stops[target]:
                        raise ValueError("table route changed an exact stop coordinate")
                    if len(polyline) == 1:
                        if stops[source] != stops[target] or route.length_m != 0 or route.opening_ids:
                            raise ValueError("invalid coincident route from DistanceTable")
                        polyline.append(polyline[0])
                        ring_coincident += 1
                    legs.append({"from_id": source, "to_id": target,
                                 "polyline": polyline, "doors_used": list(route.opening_ids)})
                if failed:
                    continue
                chosen = {
                    "hub_id": hub_id, "room_id": candidate.room_id, "location": candidate.point_m,
                    "assigned_rooms": list(territory.room_ids), "assigned_nodes": list(territory.node_ids),
                    "ring": {"hub_id": hub_id, "sequence": list(sequence), "legs": legs},
                }
                chosen_coincident = ring_coincident
                break
            if chosen is not None:
                break
        if chosen is None:
            prefix = "quick attempt found no complete ring" if _quick else "no complete ring found on sampled routing graphs"
            return F0Result("unreachable_on_grid", diagnostics=(
                f"{territory.territory_id}: {prefix}; geometric infeasibility is not proven",
            ))
        hubs.append(chosen)
        coincident_legs += chosen_coincident

    if _expired(deadline):
        return F0Result("timeout", diagnostics=("F0 deadline expired; no partial design returned",))
    design = {
        "plan_id": plan.plan_id,
        "hubs": hubs,
        "solver_notes": {"stage": "F0_quick" if _quick else "F0", "verification": "not_run",
                         "coincident_legs_encoded_as_duplicate_points": coincident_legs},
    }
    return F0Result("constructed_unverified", design, (
        "Part 2 check_design has not verified H1-H6 or complete-design metrics.",
    ))
