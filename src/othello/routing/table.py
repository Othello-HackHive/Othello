"""Task 3.3: Lazy obstacle-aware distance table with explicit cache dependency injection."""

from collections import deque
from collections.abc import Mapping
from dataclasses import replace
from numbers import Real
import time
from typing import Any

from .astar import _TimedOut, _check_deadline, _coord, _finite_number, astar_route, validate_graph
from .cache import GraphKey, PathKey, RoutingCache, plan_fingerprint
from .grid import build_routing_graph
from .types import Coord, Direction, RouteSearchResult, RoutingFailureError, TableRouteResult, ValidatedGraph


def _copy_table_result(res: TableRouteResult) -> TableRouteResult:
    """Return an isolated copy of TableRouteResult with fresh polyline list."""
    poly = list(res.polyline) if res.polyline is not None else None
    return TableRouteResult(
        status=res.status,
        polyline=poly,
        length_m=res.length_m,
        cost=res.cost,
        internal_bends=res.internal_bends,
        initial_bend=res.initial_bend,
        opening_ids=res.opening_ids,
        first_direction=res.first_direction,
        last_direction=res.last_direction,
        diagnostics=res.diagnostics,
    )


def _shared_reaches(graph: ValidatedGraph, start: Coord, goal: Coord,
                    deadline: float | None) -> bool:
    """Check a shared Hanan graph before treating it as this leg's graph."""
    _check_deadline(deadline, "shared graph connectivity")
    if start not in graph.vertices or goal not in graph.vertices:
        return False
    queue = deque([start])
    seen = {start}
    while queue:
        _check_deadline(deadline, "shared graph connectivity")
        for arc in graph.adjacency[queue.popleft()]:
            _check_deadline(deadline, "shared graph connectivity")
            if arc.target == goal:
                return True
            if arc.target not in seen:
                seen.add(arc.target)
                queue.append(arc.target)
    return False


class DistanceTable:
    """Lazy obstacle-aware distance and route table between stops.

    Computes legs lazily on demand, validates plan fingerprints on every lookup,
    and preserves strict status fidelity without masking timeouts or unsupported
    geometry as infinite distance.
    """

    def __init__(
        self,
        plan: Any,
        stops: Mapping[str, Coord],
        config: Any,
        cache: RoutingCache,
        deadline: float | None = None,
    ):
        if not isinstance(stops, Mapping) or not stops:
            raise ValueError("stops must be a nonempty mapping of IDs to coordinates")
        if not isinstance(cache, RoutingCache):
            raise ValueError("cache must be an explicit RoutingCache instance")
        if deadline is not None:
            _finite_number(deadline, "deadline")
        _check_deadline(deadline, "distance table initialization")

        # Validate configuration fields
        _finite_number(config.grid_step_m, "grid_step_m")
        _finite_number(config.cable_wall_margin_m, "cable_wall_margin_m")
        _finite_number(config.tolerance_m, "tolerance_m")
        if config.cable_transit_policy not in {"forbid", "avoid_if_possible", "allow"}:
            raise ValueError("Unknown cable_transit_policy")
        _finite_number(config.weights.length, "weights.length")
        _finite_number(config.weights.bend, "weights.bend")

        self.plan = plan
        self.stops = {str(k): _coord(v, f"stop {k}") for k, v in stops.items()}
        self.config = config
        self.cache = cache
        self.deadline = deadline
        self._routes: dict[PathKey, TableRouteResult] = {}

    def _make_path_key(self, from_id: str, to_id: str, initial_direction: Direction | None) -> PathKey:
        """Construct the complete PathKey using the current plan fingerprint."""
        plan_sig = plan_fingerprint(self.plan, self.deadline)
        terminals = tuple(sorted((k, tuple(v)) for k, v in self.stops.items()))
        # Graph construction stops at the first grid connecting this pair,
        # so even forbid/allow graphs must be keyed by the requested leg.
        leg = (from_id, to_id)
        graph_key = GraphKey(
            plan_sig=plan_sig,
            policy=str(self.config.cable_transit_policy),
            cable_wall_margin_m=float(self.config.cable_wall_margin_m),
            grid_step_m=float(self.config.grid_step_m),
            tolerance_m=float(self.config.tolerance_m),
            terminals=terminals,
            leg=leg,
        )
        return PathKey(
            graph_key=graph_key,
            start=self.stops[from_id],
            goal=self.stops[to_id],
            initial_direction=initial_direction,
            length_weight=float(self.config.weights.length),
            bend_weight=float(self.config.weights.bend),
        )

    def _build_leg_graph(self, graph_key: GraphKey, from_id: str, to_id: str
                         ) -> tuple[ValidatedGraph | None, str | None, tuple[str, ...]]:
        """Run the unchanged pair-specific policy/fallback sequence."""
        built = build_routing_graph(self.plan, self.stops, from_id, to_id,
                                    self.config, self.deadline)
        if built.status != "ready":
            if built.status == "unsupported_geometry" and built.diagnostics:
                failure_key = replace(graph_key, leg=None) if built.cacheable_unsupported else graph_key
                self.cache.put_unsupported_geometry(failure_key, built.diagnostics)
            return None, built.status, built.diagnostics
        graph = validate_graph(built.graph, self.deadline)
        shareable_mode = ("allow" if self.config.cable_transit_policy == "allow"
                          else "permitted_only")
        if (self.stops[from_id] != self.stops[to_id]
                and getattr(built, "fallback_used", None) is False
                and getattr(built, "transit_mode", None) == shareable_mode):
            self.cache.put_graph(replace(graph_key, leg=None), graph)
        else:
            self.cache.put_graph(graph_key, graph)
        return graph, None, ()

    def _select_leg_graph(self, graph_key: GraphKey, from_id: str, to_id: str
                          ) -> tuple[ValidatedGraph | None, str | None, tuple[str, ...], bool]:
        """Prefer a pair graph, then a compatible shared Hanan graph."""
        unsupported = self.cache.get_unsupported_geometry(graph_key, self.deadline)
        if unsupported is None:
            unsupported = self.cache.get_unsupported_geometry(replace(graph_key, leg=None), self.deadline)
        if unsupported is not None:
            return None, "unsupported_geometry", unsupported, False
        graph = self.cache.get_graph(graph_key, self.deadline)
        if graph is not None:
            return graph, None, (), False
        start, goal = self.stops[from_id], self.stops[to_id]
        # A* accepts an isolated vertex as a zero-length route. A fresh build
        # must validate coincident terminals against walls, free space, and policy.
        if start != goal:
            shared = self.cache.get_graph(replace(graph_key, leg=None), self.deadline)
            if shared is not None and _shared_reaches(shared, start, goal, self.deadline):
                return shared, None, (), True
        graph, status, diagnostics = self._build_leg_graph(graph_key, from_id, to_id)
        return graph, status, diagnostics, False

    def get_route(
        self, from_id: str, to_id: str, initial_direction: Direction | None = None
    ) -> TableRouteResult:
        """Retrieve or compute a route leg lazily, checking cache and status invariants."""
        try:
            _check_deadline(self.deadline, "table route query")
            if from_id not in self.stops:
                raise ValueError(f"Unknown stop ID: {from_id!r}")
            if to_id not in self.stops:
                raise ValueError(f"Unknown stop ID: {to_id!r}")
            if initial_direction is not None and initial_direction not in {"E", "W", "N", "S"}:
                raise ValueError("initial_direction must be E, W, N, S, or None")

            start = self.stops[from_id]
            goal = self.stops[to_id]

            path_key = self._make_path_key(from_id, to_id, initial_direction)

            # Check explicit injected cache
            cached_res = self.cache.get_route(path_key, self.deadline)
            if cached_res is not None:
                table_res = TableRouteResult(
                    status=cached_res.status,
                    polyline=cached_res.polyline,
                    length_m=cached_res.length_m,
                    cost=cached_res.cost,
                    internal_bends=cached_res.internal_bends,
                    initial_bend=cached_res.initial_bend,
                    opening_ids=cached_res.opening_ids,
                    first_direction=cached_res.first_direction,
                    last_direction=cached_res.last_direction,
                    diagnostics=cached_res.diagnostics,
                )
                self._routes[path_key] = table_res
                return _copy_table_result(table_res)

            # Route miss: reuse only a graph that a fresh build would select.
            graph_key = path_key.graph_key
            val_graph, graph_status, diag, shared = self._select_leg_graph(
                graph_key, from_id, to_id)
            if graph_status is not None:
                if graph_status == "unreachable_on_grid":
                    res = TableRouteResult("unreachable_on_grid", diagnostics=diag)
                    self.cache.put_route(
                        path_key, RouteSearchResult("unreachable_on_grid", diagnostics=diag)
                    )
                    self._routes[path_key] = res
                    return _copy_table_result(res)
                return TableRouteResult(graph_status, diagnostics=diag)
            assert val_graph is not None

            # Run direction-aware A* on the validated graph
            search_res = astar_route(
                val_graph, start, goal, self.config, self.deadline, initial_direction
            )
            if shared and search_res.status == "unreachable":
                # Do not turn a shared-graph miss into a negative path result.
                # The full builder may connect this pair on fallback lines.
                val_graph, graph_status, diag = self._build_leg_graph(
                    graph_key, from_id, to_id)
                if graph_status is not None:
                    if graph_status == "unreachable_on_grid":
                        res = TableRouteResult("unreachable_on_grid", diagnostics=diag)
                        self.cache.put_route(
                            path_key, RouteSearchResult("unreachable_on_grid", diagnostics=diag)
                        )
                        self._routes[path_key] = res
                        return _copy_table_result(res)
                    return TableRouteResult(graph_status, diagnostics=diag)
                assert val_graph is not None
                search_res = astar_route(
                    val_graph, start, goal, self.config, self.deadline, initial_direction
                )
            if search_res.status == "timeout":
                return TableRouteResult("timeout", diagnostics=search_res.diagnostics)

            table_res = TableRouteResult(
                status=search_res.status,
                polyline=search_res.polyline,
                length_m=search_res.length_m,
                cost=search_res.cost,
                internal_bends=search_res.internal_bends,
                initial_bend=search_res.initial_bend,
                opening_ids=search_res.opening_ids,
                first_direction=search_res.first_direction,
                last_direction=search_res.last_direction,
                diagnostics=search_res.diagnostics,
            )
            if search_res.status in {"success", "unreachable"}:
                self.cache.put_route(path_key, search_res)
                self._routes[path_key] = table_res
            return _copy_table_result(table_res)
        except _TimedOut as error:
            return TableRouteResult("timeout", diagnostics=(str(error),))

    def get_distance(self, from_id: str, to_id: str, default: float | None = None) -> float:
        """Return physical metres of the selected weighted-optimal route.

        Raises RoutingFailureError on failure unless an explicit default is supplied.
        """
        res = self.get_route(from_id, to_id, initial_direction=None)
        if res.status == "success" and res.length_m is not None:
            return res.length_m
        if default is not None:
            return default
        raise RoutingFailureError(res.status, res.diagnostics)

    def get_cost(
        self, from_id: str, to_id: str, initial_direction: Direction | None = None, default: float | None = None
    ) -> float:
        """Return the weighted A* cost of the route.

        Raises RoutingFailureError on failure unless an explicit default is supplied.
        """
        res = self.get_route(from_id, to_id, initial_direction=initial_direction)
        if res.status == "success" and res.cost is not None:
            return res.cost
        if default is not None:
            return default
        raise RoutingFailureError(res.status, res.diagnostics)

    def distance_matrix(self, default: float | None = None) -> dict[tuple[str, str], float]:
        """Compute all-pairs physical distances lazily across all stops."""
        matrix = {}
        for u in self.stops:
            for v in self.stops:
                matrix[(u, v)] = self.get_distance(u, v, default=default)
        return matrix


def build_distance_table(
    plan: Any,
    stops: Mapping[str, Coord],
    config: Any,
    cache: RoutingCache,
    deadline: float | None = None,
) -> DistanceTable:
    """Factory creating an obstacle-aware lazy DistanceTable."""
    return DistanceTable(plan, stops, config, cache, deadline)
