"""Task 3.6: bounded alternatives on an already policy-selected leg graph."""

from __future__ import annotations

from dataclasses import replace
import math
import time
from types import MappingProxyType, SimpleNamespace
from typing import Any

from .astar import _TimedOut, astar_route
from .table import DistanceTable
from .types import Coord, Direction, RouteLibraryResult, RouteVariant, ValidatedGraph


class _UnsupportedVariant(Exception):
    pass


def _check(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise _TimedOut("Route-library deadline expired; no partial library returned.")


def _signature(polyline: list[Coord] | tuple[Coord, ...], start: Coord, goal: Coord) -> tuple[Coord, ...]:
    """Remove same-signed collinear splits, preserving reversals and endpoints."""
    if not polyline or tuple(polyline[0]) != start or tuple(polyline[-1]) != goal:
        raise ValueError("A* route does not preserve exact leg endpoints")
    if len(polyline) == 1:
        if start != goal:
            raise ValueError("A noncoincident leg needs a traversed segment")
        return (start,)
    result = [start]
    previous_direction = None
    for source, target in zip(polyline, polyline[1:]):
        source, target = tuple(source), tuple(target)
        dx, dy = target[0] - source[0], target[1] - source[1]
        if (dx == 0) == (dy == 0):
            raise ValueError("A* route has a zero-length or non-axis-aligned segment")
        direction = ("E" if dx > 0 else "W") if dy == 0 else ("N" if dy > 0 else "S")
        if direction == previous_direction:
            result[-1] = target
        else:
            result.append(target)
        previous_direction = direction
    return tuple(result)


def _subset(graph: ValidatedGraph, banned_openings: frozenset[str], deadline: float | None) -> ValidatedGraph:
    """Only delete validated arcs; never add geometry or relax transit policy."""
    adjacency = {}
    count = 0
    for vertex, arcs in graph.adjacency.items():
        _check(deadline)
        retained = tuple(arc for arc in arcs if banned_openings.isdisjoint(arc.opening_ids))
        adjacency[vertex] = retained
        count += len(retained)
    return ValidatedGraph(MappingProxyType(adjacency), graph.vertices, graph.node_count, count)


def _effective_deadline(table: DistanceTable, deadline: float | None) -> float | None:
    if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or
                                 not math.isfinite(deadline)):
        raise ValueError("deadline must be a finite absolute monotonic time")
    values = [value for value in (table.deadline, deadline) if value is not None]
    return min(values) if values else None


def _query_table(table: DistanceTable, deadline: float | None) -> DistanceTable:
    if deadline == table.deadline:
        return table
    # Same injected cache and complete keys, with the stricter per-call budget.
    return DistanceTable(table.plan, table.stops, table.config, table.cache, deadline)


def _leg_graph(table: DistanceTable, from_id: str, to_id: str,
               initial_direction: Direction | None, deadline: float | None
               ) -> tuple[ValidatedGraph | None, str | None, tuple[str, ...]]:
    key = table._make_path_key(from_id, to_id, initial_direction).graph_key
    graph, status, diagnostics, _ = table._select_leg_graph(key, from_id, to_id)
    return graph, status, diagnostics


def build_route_library(
    table: DistanceTable, from_id: str, to_id: str, config: Any,
    deadline: float | None = None, initial_direction: Direction | None = None,
) -> RouteLibraryResult:
    """Return up to limits.routes_per_leg distinct routes for one directed leg.

    The DistanceTable first selects the leg-specific transit policy graph and
    caches the normal weighted route. Alternatives only search that graph or
    a stricter door-filtered subgraph. No occupancy-penalty variant is claimed:
    neither the current A* API nor this call has an occupied-cable cost field.
    """
    if not isinstance(table, DistanceTable):
        raise ValueError("table must be a DistanceTable")
    try:
        cap = config.limits.routes_per_leg
        weights = config.weights
        routing_fields = ("grid_step_m", "cable_wall_margin_m", "tolerance_m", "cable_transit_policy")
        if any(getattr(config, field) != getattr(table.config, field) for field in routing_fields):
            raise ValueError("Route-library config must match the DistanceTable routing config")
        if (weights.length, weights.bend) != (table.config.weights.length, table.config.weights.bend):
            raise ValueError("Route-library weights must match the DistanceTable weights")
    except AttributeError as exc:
        raise ValueError("config needs routing fields, weights and limits.routes_per_leg") from exc
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
        raise ValueError("limits.routes_per_leg must be a positive integer")
    if from_id not in table.stops or to_id not in table.stops:
        raise ValueError("Leg endpoint IDs must be in DistanceTable.stops")
    if initial_direction is not None and initial_direction not in {"E", "W", "N", "S"}:
        raise ValueError("initial_direction must be E, W, N, S, or None")
    effective = _effective_deadline(table, deadline)

    try:
        _check(effective)
        query = _query_table(table, effective)
        baseline = query.get_route(from_id, to_id, initial_direction)
        _check(effective)
        if baseline.status == "timeout":
            return RouteLibraryResult("timeout", diagnostics=baseline.diagnostics)
        if baseline.status == "unsupported_geometry":
            return RouteLibraryResult("unsupported_geometry", diagnostics=baseline.diagnostics)
        if baseline.status in {"unreachable", "unreachable_on_grid"}:
            return RouteLibraryResult("unreachable_on_grid", diagnostics=baseline.diagnostics)
        if baseline.status != "success":
            raise ValueError(f"Unexpected DistanceTable route status: {baseline.status}")
        start, goal = query.stops[from_id], query.stops[to_id]
        distinct: dict[tuple[Coord, ...], RouteVariant] = {}

        def add(route: Any, tag: str) -> None:
            _check(effective)
            if route.status == "timeout":
                raise _TimedOut("Route-library variant search timed out; no partial library returned.")
            if route.status == "unreachable":
                return
            if route.status != "success" or route.polyline is None or route.length_m is None or \
                    route.internal_bends is None or route.initial_bend is None or route.opening_ids is None:
                raise ValueError("A* returned an incomplete route variant")
            signature = _signature(route.polyline, start, goal)
            existing = distinct.get(signature)
            if existing is not None:
                if existing.opening_ids != tuple(route.opening_ids):
                    raise _UnsupportedVariant("Same route geometry has conflicting opening metadata")
                distinct[signature] = replace(existing, source_tags=(*existing.source_tags, tag))
                return
            cost = weights.length * route.length_m + weights.bend * (route.internal_bends + route.initial_bend)
            if not math.isfinite(cost):
                raise ValueError("Route variant has non-finite weighted leg cost")
            distinct[signature] = RouteVariant(
                signature, route.length_m, cost, route.internal_bends, route.initial_bend,
                tuple(route.opening_ids), route.first_direction, route.last_direction, (tag,),
            )

        add(baseline, "normal_weighted")
        if cap == 1 or start == goal:
            _check(effective)
            return RouteLibraryResult("ready", tuple(distinct.values()))
        graph, graph_status, graph_diagnostics = _leg_graph(query, from_id, to_id, initial_direction, effective)
        _check(effective)
        if graph_status is not None:
            return RouteLibraryResult(
                "unreachable_on_grid" if graph_status == "unreachable_on_grid" else graph_status,
                diagnostics=graph_diagnostics,
            )
        assert graph is not None
        if start != goal:
            shortest_config = SimpleNamespace(weights=SimpleNamespace(length=1.0, bend=0.0))
            bends_config = SimpleNamespace(weights=SimpleNamespace(length=0.0, bend=1.0))
            add(astar_route(graph, start, goal, shortest_config, effective, initial_direction), "shortest_length")
            add(astar_route(graph, start, goal, bends_config, effective, initial_direction), "fewest_bends")
            used_doors = tuple(dict.fromkeys(baseline.opening_ids or ()))
            if used_doors:
                bans = [frozenset(used_doors)]
                bans.extend(frozenset((opening_id,)) for opening_id in used_doors if len(used_doors) > 1)
                for banned in bans:
                    _check(effective)
                    filtered = _subset(graph, banned, effective)
                    tag = "alternative_door:" + ",".join(sorted(banned))
                    add(astar_route(filtered, start, goal, query.config, effective, initial_direction), tag)

        _check(effective)
        variants = list(distinct.values())
        baseline_route = next(route for route in variants if "normal_weighted" in route.source_tags)
        selected = [baseline_route]
        others = [route for route in variants if route is not baseline_route]
        seen_openings = {baseline_route.opening_ids}
        for route in sorted(others, key=lambda r: (r.length_m, r.opening_ids, r.polyline)):
            _check(effective)
            if len(selected) >= cap:
                break
            if route.opening_ids not in seen_openings:
                selected.append(route)
                seen_openings.add(route.opening_ids)
        for tag in ("shortest_length", "fewest_bends"):
            _check(effective)
            if len(selected) >= cap:
                break
            choices = [route for route in others if route not in selected and tag in route.source_tags]
            if choices:
                selected.append(min(choices, key=lambda r: (r.length_m, r.polyline)))
        for route in sorted(others, key=lambda r: (r.length_m, r.opening_ids, r.polyline)):
            _check(effective)
            if len(selected) >= cap:
                break
            if route not in selected:
                selected.append(route)
        _check(effective)
        return RouteLibraryResult("ready", tuple(selected))
    except _TimedOut as exc:
        return RouteLibraryResult("timeout", diagnostics=(str(exc),))
    except _UnsupportedVariant as exc:
        return RouteLibraryResult("unsupported_geometry", diagnostics=(str(exc),))
