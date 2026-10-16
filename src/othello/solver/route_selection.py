"""Task 3.7: bounded route combinations; only Part 2 supplies official J."""

from __future__ import annotations

from copy import deepcopy
from importlib import import_module
from itertools import product
import math
import time
from typing import Any, Callable

from shapely.geometry import LineString

from othello.routing import DistanceTable, RoutingCache, build_route_library
from othello.routing.astar import _TimedOut as _RoutingTimedOut
from othello.routing.types import RouteVariant

from .types import RouteSelectionResult


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise _TimedOut


class _TimedOut(Exception):
    pass


def _optional_checker() -> Callable[[Any, Any, Any], Any] | None:
    """Use Part 2's public checker when installed; surface its other import errors."""
    try:
        module = import_module("othello.checker")
    except ModuleNotFoundError as exc:
        if exc.name == "othello.checker":
            return None
        raise
    return getattr(module, "check_design", None)


def _limits(config: Any) -> tuple[int, int]:
    try:
        exact, beam = config.limits.exact_combo_limit, config.limits.beam_width
        weights = config.weights
        proxy_weights = (weights.crossing, weights.overlap)
    except AttributeError as exc:
        raise ValueError("config needs limits.exact_combo_limit, limits.beam_width and interaction weights") from exc
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (exact, beam)):
        raise ValueError("exact_combo_limit and beam_width must be positive integers")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or
           not math.isfinite(value) or value < 0 for value in proxy_weights):
        raise ValueError("crossing and overlap weights must be finite and nonnegative")
    return exact, beam


def _legs_and_tables(plan: Any, design: dict[str, Any], config: Any,
                     cache: RoutingCache, deadline: float | None):
    if not isinstance(design, dict) or design.get("plan_id") != getattr(plan, "plan_id", None):
        raise ValueError("design must be Contract 2 data for this plan")
    hubs = design.get("hubs")
    if not isinstance(hubs, list) or not hubs:
        raise ValueError("design needs at least one hub")
    entries = []
    seen_hubs = set()
    for hub_index, hub in enumerate(hubs):
        _check_deadline(deadline)
        hub_id = hub["hub_id"]
        if hub_id in seen_hubs or hub_id in plan.nodes:
            raise ValueError("hub IDs must be unique and separate from node IDs")
        seen_hubs.add(hub_id)
        ring = hub["ring"]
        sequence = ring["sequence"]
        if ring["hub_id"] != hub_id or sequence[0] != hub_id or sequence[-1] != hub_id or \
                len(sequence) != len(ring["legs"]) + 1 or \
                len(sequence[1:-1]) != len(set(sequence[1:-1])) or \
                set(sequence[1:-1]) != set(hub["assigned_nodes"]):
            raise ValueError("design has a broken ring or node coverage")
        stops = {hub_id: tuple(hub["location"])}
        stops.update({node_id: tuple(plan.nodes[node_id].point_m) for node_id in hub["assigned_nodes"]})
        table = DistanceTable(plan, stops, config, cache, deadline)
        for leg_index, (source, target) in enumerate(zip(sequence, sequence[1:])):
            _check_deadline(deadline)
            leg = ring["legs"][leg_index]
            if leg["from_id"] != source or leg["to_id"] != target or \
                    tuple(leg["polyline"][0]) != stops[source] or \
                    tuple(leg["polyline"][-1]) != stops[target]:
                raise ValueError("design leg chain or exact endpoint is broken")
            entries.append((hub_index, leg_index, source, target, stops[source], stops[target], table))
    return entries


def _candidate_design(base: dict[str, Any], choices: tuple[int, ...], libraries: list[tuple[RouteVariant, ...]]) -> dict[str, Any]:
    candidate = deepcopy(base)
    position = 0
    for hub in candidate["hubs"]:
        for leg in hub["ring"]["legs"]:
            variant = libraries[position][choices[position]]
            polyline = list(variant.polyline)
            if len(polyline) == 1:
                polyline.append(polyline[0])
            leg["polyline"] = polyline
            leg["doors_used"] = list(variant.opening_ids)
            position += 1
    return candidate


def _geometry_key(design: dict[str, Any]) -> tuple:
    return tuple((tuple(tuple(point) for point in leg["polyline"]), tuple(leg["doors_used"]))
                 for hub in design["hubs"] for leg in hub["ring"]["legs"])


def _choice_key(choices: tuple[int, ...], libraries: list[tuple[RouteVariant, ...]]) -> tuple:
    return tuple((libraries[index][choice].polyline, libraries[index][choice].opening_ids)
                 for index, choice in enumerate(choices))


def _pairwise_proxy(a: RouteVariant, b: RouteVariant, crossing_weight: float,
                    overlap_weight: float) -> float:
    """Only a beam-ordering proxy; pair counts are not the official X or O."""
    if len(a.polyline) < 2 or len(b.polyline) < 2:
        return 0.0
    first, second = LineString(a.polyline), LineString(b.polyline)
    intersection = first.intersection(second)
    if intersection.is_empty:
        return 0.0
    overlap = intersection.length
    crossings = 0
    stack = [intersection]
    endpoints = {a.polyline[0], a.polyline[-1], b.polyline[0], b.polyline[-1]}
    while stack:
        part = stack.pop()
        if part.geom_type == "Point":
            if (part.x, part.y) not in endpoints:
                crossings += 1
        elif hasattr(part, "geoms"):
            stack.extend(part.geoms)
    return crossing_weight * crossings + overlap_weight * overlap


def _score_of_verified(check_result: Any) -> float | None:
    valid = getattr(check_result, "valid", None)
    if not isinstance(valid, bool):
        raise ValueError("checker must return a ValidationResult with boolean valid")
    if not valid:
        return None
    metrics = getattr(check_result, "metrics", None)
    score = getattr(metrics, "J", None)
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or score < 0:
        raise ValueError("verified design needs finite nonnegative metrics.J from Part 2")
    return float(score)


def _invoke_checker(checker: Callable[[Any, Any, Any], Any], plan: Any,
                    design: dict[str, Any], config: Any) -> tuple[Any | None, float | None, str | None]:
    """Isolate an injected checker's failure from the search incumbent."""
    try:
        response = checker(plan, design, config)
        return response, _score_of_verified(response), None
    except Exception as exc:
        return None, None, f"Part 2 checker failed ({type(exc).__name__}): {exc}"


def select_whole_design_routes(
    plan: Any, design: dict[str, Any], config: Any,
    checker: Callable[[Any, Any, Any], Any] | None = None,
    cache: RoutingCache | None = None, deadline: float | None = None,
) -> RouteSelectionResult:
    """Search one route choice per existing leg, without changing hubs or order.

    Exact enumeration covers small products; larger products use a bounded beam
    ordered by leg costs and a non-authoritative pairwise geometry proxy. Every
    complete candidate is checked by Part 2 when available. Only checker-valid
    designs can carry J or be called verified. No global-optimality claim follows.
    """
    if deadline is not None and (isinstance(deadline, bool) or not isinstance(deadline, (int, float))
                                 or not math.isfinite(deadline)):
        raise ValueError("deadline must be a finite absolute monotonic time")
    if cache is not None and not isinstance(cache, RoutingCache):
        raise ValueError("cache must be a RoutingCache")
    if checker is not None and not callable(checker):
        raise ValueError("checker must be callable")
    exact_cap, beam_width = _limits(config)
    route_cache = cache if cache is not None else RoutingCache()
    checker = checker if checker is not None else _optional_checker()
    incumbent: dict[str, Any] | None = None
    incumbent_j: float | None = None
    incumbent_key: tuple | None = None
    checked = 0
    checker_failures = 0
    checker_diagnostics: list[str] = []
    combinations = 0
    mode: str = "none"

    def result(status: str, diagnostics: tuple[str, ...] = ()) -> RouteSelectionResult:
        failures = tuple(checker_diagnostics)
        if checker_failures > len(failures):
            failures += (f"{checker_failures - len(failures)} additional checker failures",)
        return RouteSelectionResult(status, deepcopy(incumbent) if incumbent is not None else None,
                                    incumbent is not None, incumbent_j, mode, combinations, checked,
                                    diagnostics + failures)

    def evaluate(candidate: dict[str, Any]) -> None:
        nonlocal checked, checker_failures, incumbent, incumbent_j, incumbent_key
        _check_deadline(deadline)
        assert checker is not None
        checked += 1
        _, score, failure = _invoke_checker(checker, plan, candidate, config)
        if failure is not None:
            checker_failures += 1
            if len(checker_diagnostics) < 3:
                checker_diagnostics.append(f"checker invocation {checked}: {failure}")
        if score is not None:
            key = (score, _geometry_key(candidate))
            if incumbent_key is None or key < incumbent_key:
                incumbent, incumbent_j, incumbent_key = deepcopy(candidate), score, key
        _check_deadline(deadline)

    try:
        _check_deadline(deadline)
        entries = _legs_and_tables(plan, design, config, route_cache, deadline)
        # Preserve an independently checked F0 before potentially expensive
        # library construction or later beam expansion.
        if checker is not None:
            evaluate(deepcopy(design))
        libraries: list[tuple[RouteVariant, ...]] = []
        for _, _, source, target, start, goal, table in entries:
            _check_deadline(deadline)
            found = build_route_library(table, source, target, config, deadline)
            if found.status != "ready":
                return result(found.status, found.diagnostics)
            if not found.routes:
                raise ValueError("ready route library is empty")
            for route in found.routes:
                if not route.polyline or route.polyline[0] != start or route.polyline[-1] != goal:
                    raise ValueError("route library changed an exact leg endpoint")
            libraries.append(found.routes)
        combinations = math.prod(len(routes) for routes in libraries)
        mode = "exact" if combinations <= exact_cap else "beam"
        weights = config.weights
        pair_cache: dict[tuple[int, int, int, int], float] = {}

        def proxy_increment(prior: tuple[int, ...], leg_index: int, route_index: int) -> float:
            route = libraries[leg_index][route_index]
            increment = route.weighted_leg_cost
            for other_index, other_route_index in enumerate(prior):
                _check_deadline(deadline)
                pair = (other_index, other_route_index, leg_index, route_index)
                if pair not in pair_cache:
                    pair_cache[pair] = _pairwise_proxy(
                        libraries[other_index][other_route_index], route,
                        weights.crossing, weights.overlap,
                    )
                increment += pair_cache[pair]
            return increment

        unverified_choices: tuple[int, ...] | None = None
        if mode == "exact":
            best_proxy: tuple | None = None
            for choices in product(*(range(len(routes)) for routes in libraries)):
                _check_deadline(deadline)
                if checker is not None:
                    evaluate(_candidate_design(design, choices, libraries))
                else:
                    proxy = 0.0
                    for index, choice in enumerate(choices):
                        proxy += proxy_increment(choices[:index], index, choice)
                    key = (proxy, _choice_key(choices, libraries))
                    if best_proxy is None or key < best_proxy:
                        best_proxy, unverified_choices = key, choices
        else:
            beam: list[tuple[float, tuple[int, ...]]] = [(0.0, ())]
            for leg_index, routes in enumerate(libraries):
                _check_deadline(deadline)
                expanded: list[tuple[float, tuple[int, ...]]] = []
                for proxy, prior in beam:
                    for route_index in range(len(routes)):
                        _check_deadline(deadline)
                        expanded.append((proxy + proxy_increment(prior, leg_index, route_index),
                                         (*prior, route_index)))
                beam = sorted(expanded, key=lambda entry: (entry[0], _choice_key(entry[1], libraries)))[:beam_width]
            if checker is None:
                unverified_choices = beam[0][1]
            else:
                for _, choices in beam:
                    _check_deadline(deadline)
                    evaluate(_candidate_design(design, choices, libraries))
        if checker is None:
            _check_deadline(deadline)
            assert unverified_choices is not None
            return RouteSelectionResult(
                "unverified", _candidate_design(design, unverified_choices, libraries),
                False, None, mode, combinations, 0,
                ("Route choice used a non-authoritative interaction proxy; Part 2 check_design is unavailable.",),
            )
        return result("verified" if incumbent is not None else "no_verified_design",
                      () if incumbent is not None else ("No library combination passed the independent checker.",))
    except (_TimedOut, _RoutingTimedOut):
        return result("timeout", ("Route-selection deadline expired; any returned incumbent passed the checker.",))
