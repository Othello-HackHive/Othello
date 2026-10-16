"""Task 3.10: bounded, checker-led local improvement of complete designs."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
import time
from types import SimpleNamespace
from typing import Any, Callable

from shapely.errors import GEOSException
from shapely.ops import unary_union

from othello.routing import DistanceTable, RoutingCache
from othello.routing.astar import _TimedOut as _RoutingTimedOut

from .hub_candidates import HubCandidateLimitError, generate_hub_candidates
from .node_orders import enumerate_visiting_orders
from .outer import _design_key
from .route_selection import _invoke_checker, select_whole_design_routes
from .types import ImprovementResult


class _Expired(Exception):
    pass


class _Unsupported(Exception):
    pass


class _CandidateConfiguration(Exception):
    pass


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise _Expired


def _validate_design(plan: Any, design: Any) -> None:
    """Check the move invariants; Part 2 remains the geometry authority."""
    if not isinstance(design, dict) or design.get("plan_id") != getattr(plan, "plan_id", None):
        raise ValueError("design must be Contract 2 data for this plan")
    hubs = design.get("hubs")
    if not isinstance(hubs, list) or not hubs or not isinstance(getattr(plan, "nodes", None), Mapping):
        raise ValueError("design needs hubs and a plan node mapping")
    seen_hubs: set[str] = set()
    seen_nodes: set[str] = set()
    for hub in hubs:
        try:
            hub_id, node_ids = hub["hub_id"], hub["assigned_nodes"]
            room_ids, location = hub["assigned_rooms"], hub["location"]
            ring = hub["ring"]
            sequence, legs = ring["sequence"], ring["legs"]
            if (not isinstance(hub_id, str) or not hub_id or hub_id in seen_hubs or
                    hub_id in plan.nodes or not room_ids or not node_ids or
                    len(node_ids) != len(set(node_ids)) or seen_nodes.intersection(node_ids) or
                    len(room_ids) != len(set(room_ids)) or hub["room_id"] not in room_ids or
                    ring["hub_id"] != hub_id or sequence[0] != hub_id or
                    sequence[-1] != hub_id or len(legs) != len(sequence) - 1 or
                    len(sequence[1:-1]) != len(node_ids) or
                    set(sequence[1:-1]) != set(node_ids)):
                raise ValueError("design has a broken ring, assignments, or hub identity")
            stops = {hub_id: tuple(location)}
            stops.update({node_id: tuple(plan.nodes[node_id].point_m) for node_id in node_ids})
            for (source, target), leg in zip(zip(sequence, sequence[1:]), legs):
                if (leg["from_id"] != source or leg["to_id"] != target or
                        not leg["polyline"] or leg["doors_used"] is None or
                        tuple(leg["polyline"][0]) != stops[source] or
                        tuple(leg["polyline"][-1]) != stops[target]):
                    raise ValueError("design has a broken leg chain or moved endpoint")
            seen_hubs.add(hub_id)
            seen_nodes.update(node_ids)
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ValueError("design lacks required Contract 2 ring fields") from exc


def _stops(plan: Any, hub: dict, point: tuple[float, float]) -> dict[str, tuple[float, float]]:
    stops = {hub["hub_id"]: point}
    stops.update({node_id: tuple(plan.nodes[node_id].point_m) for node_id in hub["assigned_nodes"]})
    return stops


def _assignments(design: dict) -> tuple:
    return tuple(sorted((hub["hub_id"], tuple(sorted(hub["assigned_rooms"])),
                         tuple(sorted(hub["assigned_nodes"]))) for hub in design["hubs"]))


def _routed_hub(hub: dict, room_id: str, point: tuple[float, float], sequence: tuple[str, ...],
                table: DistanceTable, deadline: float) -> dict | None:
    """Return a complete replacement ring or skip a grid-disconnected move."""
    if (room_id not in hub["assigned_rooms"] or len(sequence) != len(hub["assigned_nodes"]) + 2 or
            sequence[0] != hub["hub_id"] or sequence[-1] != hub["hub_id"] or
            set(sequence[1:-1]) != set(hub["assigned_nodes"])):
        raise ValueError("local move changed assigned room, node coverage, or ring closure")
    stops = table.stops
    legs = []
    for source, target in zip(sequence, sequence[1:]):
        _check(deadline)
        route = table.get_route(source, target)
        if route.status == "timeout":
            raise _Expired
        if route.status == "unsupported_geometry":
            raise _Unsupported("; ".join(route.diagnostics))
        if route.status in {"unreachable", "unreachable_on_grid"}:
            return None
        if route.status != "success" or route.polyline is None or route.opening_ids is None:
            raise ValueError("DistanceTable returned an invalid successful route")
        polyline = list(route.polyline)
        if not polyline or polyline[0] != stops[source] or polyline[-1] != stops[target]:
            raise ValueError("routing changed an exact endpoint")
        if len(polyline) == 1:
            if stops[source] != stops[target] or route.length_m != 0 or route.opening_ids:
                raise ValueError("invalid coincident leg from DistanceTable")
            polyline.append(polyline[0])
        legs.append({"from_id": source, "to_id": target, "polyline": polyline,
                     "doors_used": list(route.opening_ids)})
    replacement = deepcopy(hub)
    replacement["room_id"], replacement["location"] = room_id, point
    replacement["ring"] = {"hub_id": hub["hub_id"], "sequence": list(sequence), "legs": legs}
    return replacement


def improve_design(
    plan: Any, design: dict[str, Any], config: Any,
    checker: Callable[[Any, Any, Any], Any] | None = None,
    cache: RoutingCache | None = None, deadline: float | None = None,
) -> ImprovementResult:
    """Try route, node-order, and hub-position moves without claiming optimality.

    All moves keep the input's assignments and hub count, so its area gap is
    constant. The injected checker alone decides validity and complete-design J.
    Search uses one neighborhood sweep and at most two route-library searches.
    """
    if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                                 or not math.isfinite(deadline)):
        raise ValueError("deadline must be a finite absolute monotonic time")
    if checker is not None and not callable(checker):
        raise ValueError("checker must be callable")
    if cache is not None and not isinstance(cache, RoutingCache):
        raise ValueError("cache must be a RoutingCache")
    try:
        budget = float(config.time_budget_s)
        limits = config.limits
        caps = (limits.hub_candidates, limits.orders_kept, limits.routes_per_leg,
                limits.beam_width, limits.exact_combo_limit)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("config needs time_budget_s and the documented search limits") from exc
    if (not math.isfinite(budget) or budget <= 0 or
            any(isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0 for cap in caps)):
        raise ValueError("time budget and search limits must be positive")
    _validate_design(plan, design)
    assigned = _assignments(design)
    if checker is None:
        return ImprovementResult("unverified", provisional_design=deepcopy(design), diagnostics=(
            "No injected Part 2 checker; no move can become a verified incumbent.",
        ))

    budget_deadline = time.monotonic() + budget
    effective = min(deadline, budget_deadline) if deadline is not None else budget_deadline
    route_cache = cache if cache is not None else RoutingCache()
    incumbent: dict[str, Any] | None = None
    best_key: tuple | None = None
    initial_key: tuple | None = None
    checked: dict[tuple, Any] = {}
    diagnostics: list[str] = []
    checker_failures = 0
    moves_considered = 0

    def verified_checker(check_plan: Any, candidate: dict, check_config: Any) -> Any:
        nonlocal incumbent, best_key, checker_failures
        _validate_design(plan, candidate)
        if _assignments(candidate) != assigned:
            raise ValueError("local move changed hub count or assigned rooms and nodes")
        signature = _design_key(candidate)
        if signature in checked:
            return checked[signature]
        _check(effective)
        result, score, failure = _invoke_checker(checker, check_plan, candidate, check_config)
        if failure is not None:
            checker_failures += 1
            if checker_failures <= 3:
                diagnostics.append(f"checker invocation {checker_failures}: {failure}")
            _check(effective)
            return SimpleNamespace(valid=False, metrics=None)
        checked[signature] = result
        if score is not None:
            key = (len(candidate["hubs"]), score, signature)
            if best_key is None or key < best_key:
                incumbent, best_key = deepcopy(candidate), key
        return result

    def propose(base: dict, index: int, replacement: dict) -> None:
        nonlocal moves_considered
        _check(effective)
        candidate = deepcopy(base)
        candidate["hubs"][index] = replacement
        signature = _design_key(candidate)
        if signature not in checked:
            moves_considered += 1
            verified_checker(plan, candidate, config)
        _check(effective)

    def select_routes(base: dict) -> str:
        _check(effective)
        selected = select_whole_design_routes(plan, base, config, verified_checker,
                                              route_cache, effective)
        if selected.status == "timeout":
            raise _Expired
        if selected.status == "unsupported_geometry":
            raise _Unsupported("; ".join(selected.diagnostics))
        if selected.status == "verified":
            if selected.design is None:
                raise ValueError("verified route selection lacks a complete design")
            verified_checker(plan, selected.design, config)
        elif selected.status not in {"verified", "no_verified_design", "unreachable_on_grid"}:
            diagnostics.append(f"Route selection: {selected.status}: {'; '.join(selected.diagnostics)}")
        if selected.status in {"verified", "no_verified_design"}:
            diagnostics.extend(f"Route selection: {detail}" for detail in selected.diagnostics
                               if "checker" in detail.lower())
        return selected.status

    failure: str | None = None
    try:
        _check(effective)
        verified_checker(plan, design, config)
        initial_key = best_key
        _check(effective)
        select_routes(design)
        base = deepcopy(incumbent if incumbent is not None else design)
        route_seed = _design_key(base)
        for index in sorted(range(len(base["hubs"])), key=lambda i: base["hubs"][i]["hub_id"]):
            _check(effective)
            hub = base["hubs"][index]
            hub_id = hub["hub_id"]
            location = tuple(hub["location"])
            sequence = tuple(hub["ring"]["sequence"])
            current_table = DistanceTable(plan, _stops(plan, hub, location), config, route_cache, effective)
            orders = enumerate_visiting_orders(hub_id, hub["assigned_nodes"], current_table,
                                               config, effective)
            if orders.status == "timeout":
                raise _Expired
            if orders.status == "unsupported_geometry":
                raise _Unsupported("; ".join(orders.diagnostics))
            if orders.status == "ready":
                for order in orders.orders:
                    _check(effective)
                    if order.sequence == sequence:
                        continue
                    replacement = _routed_hub(hub, hub["room_id"], location,
                                              order.sequence, current_table, effective)
                    if replacement is not None:
                        propose(base, index, replacement)
            else:
                diagnostics.append(f"{hub_id}: no alternative node order routed on the grid")

            try:
                zone = unary_union([plan.hub_zones_m[room_id] for room_id in hub["assigned_rooms"]])
            except (KeyError, GEOSException) as exc:
                raise _Unsupported(f"{hub_id}: assigned hub zones are missing or unusable") from exc
            territory = SimpleNamespace(territory_id=hub_id,
                                        room_ids=tuple(hub["assigned_rooms"]),
                                        node_ids=tuple(hub["assigned_nodes"]), hub_zone_m=zone)
            try:
                candidates = generate_hub_candidates(plan, territory, config, effective)
            except HubCandidateLimitError as exc:
                _check(effective)
                raise _CandidateConfiguration(f"{hub_id}: {exc}") from exc
            if candidates.status == "timeout":
                raise _Expired
            if candidates.status == "unsupported_geometry":
                raise _Unsupported("; ".join(candidates.diagnostics))
            if candidates.status == "no_candidates":
                diagnostics.append(f"{hub_id}: no sampled alternative hub position")
            for candidate in candidates.candidates:
                _check(effective)
                if candidate.room_id == hub["room_id"] and candidate.point_m == location:
                    continue
                table = DistanceTable(plan, _stops(plan, hub, candidate.point_m), config,
                                      route_cache, effective)
                replacement = _routed_hub(hub, candidate.room_id, candidate.point_m,
                                          sequence, table, effective)
                if replacement is not None:
                    propose(base, index, replacement)
        if incumbent is not None and _design_key(incumbent) != route_seed:
            select_routes(incumbent)
        _check(effective)
    except (_Expired, _RoutingTimedOut):
        failure = "timeout"
        diagnostics.append("Improvement deadline expired; no infeasibility conclusion follows")
    except _Unsupported as exc:
        failure = "unsupported_geometry"
        diagnostics.append(str(exc))
    except _CandidateConfiguration as exc:
        failure = "candidate_configuration_error"
        diagnostics.append(str(exc))

    if checker_failures > 3:
        diagnostics.append(f"{checker_failures - 3} additional checker failures")

    if failure is not None:
        status = failure
    elif incumbent is None:
        status = "no_verified_design"
    elif initial_key is None:
        status = "verified"
    elif best_key < initial_key:
        status = "improved"
    else:
        status = "unchanged"
    return ImprovementResult(status, deepcopy(incumbent) if incumbent is not None else None,
                             deepcopy(design) if incumbent is None else None,
                             incumbent is not None, best_key[1] if best_key is not None else None,
                             moves_considered, len(checked), tuple(diagnostics))
