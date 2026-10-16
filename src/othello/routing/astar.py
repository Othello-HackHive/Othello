"""Direction-aware A* on Task 3.1 graphs, without geometry or policy changes."""

from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count
from math import fsum, isfinite
from numbers import Real
import time
from types import MappingProxyType

import networkx as nx

from .types import Coord, Direction, RouteSearchResult, ValidatedGraph

_DIRECTIONS = ("E", "W", "N", "S")
_State = tuple[Coord, Direction | None]


class _TimedOut(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _Arc:
    target: Coord
    direction: Direction
    length_m: float
    opening_ids: tuple[str, ...]


def _check_deadline(deadline, phase):
    if deadline is not None and time.monotonic() >= deadline:
        raise _TimedOut(f"Deadline reached during A* {phase}; no partial route returned.")


def _finite_number(value, name):
    try:
        valid = isinstance(value, Real) and not isinstance(value, bool) and isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _coordinate(value):
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError("Graph vertices and endpoints must be (x, y) tuples")
    for component in value:
        _finite_number(component, "Coordinate")


def _coord(value: Any, name: str = "coordinate") -> Coord:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError(f"{name} must be an (x, y) coordinate")
    return (float(_finite_number(value[0], f"{name}[0]")), float(_finite_number(value[1], f"{name}[1]")))


def _finite_cost(value):
    if not isfinite(value):
        raise ValueError("Coordinates and weights produce a non-finite route cost")
    return value


def validate_graph(graph: Any, deadline: float | None = None) -> ValidatedGraph:
    """Pre-validate a Task 3.1 directed graph into an immutable ValidatedGraph.

    Adjacency is wrapped in MappingProxyType over a private dictionary
    containing immutable tuples of _Arc objects. Vertices is a frozenset.
    """
    if isinstance(graph, ValidatedGraph):
        if deadline is not None:
            _finite_number(deadline, "deadline")
        _check_deadline(deadline, "validation")
        return graph

    if deadline is not None:
        _finite_number(deadline, "deadline")
    _check_deadline(deadline, "validation")
    if not isinstance(graph, nx.DiGraph) or graph.is_multigraph():
        raise ValueError("graph must be a Task 3.1 directed graph, not a build result or multigraph")

    adjacency_raw = {}
    for vertex in graph:
        _check_deadline(deadline, "validation")
        _coordinate(vertex)
        adjacency_raw[vertex] = []
    for source, target, data in graph.edges(data=True):
        _check_deadline(deadline, "validation")
        dx, dy = target[0] - source[0], target[1] - source[1]
        if (dx == 0) == (dy == 0):
            raise ValueError("Graph edges must be nonzero and exactly axis aligned")
        direction = ("E" if dx > 0 else "W") if dy == 0 else ("N" if dy > 0 else "S")
        try:
            length = _finite_number(data["length_m"], "edge length_m")
            edge_direction, opening_ids = data["direction"], data["opening_ids"]
        except KeyError as error:
            raise ValueError(f"Missing Task 3.1 edge attribute: {error.args[0]}") from error
        if length <= 0 or length != abs(dx) + abs(dy):
            raise ValueError("edge length_m must equal its positive rectilinear length")
        if edge_direction != direction:
            raise ValueError("Edge direction must agree with its endpoint coordinates")
        if not isinstance(opening_ids, (tuple, list)):
            raise ValueError("opening_ids must be an ordered sequence of strings")
        for opening_id in opening_ids:
            _check_deadline(deadline, "validation")
            if not isinstance(opening_id, str):
                raise ValueError("opening_ids must contain strings")
        adjacency_raw[source].append(_Arc(target, direction, length, tuple(opening_ids)))

    adjacency_frozen = {}
    for vertex, arcs in adjacency_raw.items():
        _check_deadline(deadline, "validation")
        arcs.sort(key=lambda arc: (_DIRECTIONS.index(arc.direction), arc.target))
        adjacency_frozen[vertex] = tuple(arcs)
    _check_deadline(deadline, "validation")

    return ValidatedGraph(
        adjacency=MappingProxyType(adjacency_frozen),
        vertices=frozenset(adjacency_frozen.keys()),
        node_count=len(adjacency_frozen),
        edge_count=graph.number_of_edges(),
    )


def _validate(graph, start, goal, config, deadline, initial_direction):
    if deadline is not None:
        _finite_number(deadline, "deadline")
    _check_deadline(deadline, "validation")
    if initial_direction is not None and initial_direction not in _DIRECTIONS:
        raise ValueError("initial_direction must be E, W, N, S, or None")
    try:
        length_weight = _finite_number(config.weights.length, "weights.length")
        bend_weight = _finite_number(config.weights.bend, "weights.bend")
    except AttributeError as error:
        raise ValueError("config must supply weights.length and weights.bend") from error
    if length_weight < 0 or bend_weight < 0:
        raise ValueError("Length and bend weights must be nonnegative")
    _coordinate(start)
    _coordinate(goal)

    if isinstance(graph, ValidatedGraph):
        if start not in graph.vertices or goal not in graph.vertices:
            raise ValueError("Both endpoints must belong to the supplied graph")
        return graph.adjacency, length_weight, bend_weight

    if not isinstance(graph, nx.DiGraph) or graph.is_multigraph():
        raise ValueError("graph must be a Task 3.1 directed graph, not a build result or multigraph")
    if start not in graph or goal not in graph:
        raise ValueError("Both endpoints must belong to the supplied graph")

    validated = validate_graph(graph, deadline)
    return validated.adjacency, length_weight, bend_weight


def _reconstruct(parents, initial_state, goal_state, start, goal,
                 length_weight, bend_weight, deadline):
    reverse_edges = []
    state = goal_state
    while state != initial_state:
        _check_deadline(deadline, "reconstruction")
        parent, arc = parents[state]
        reverse_edges.append(arc)
        state = parent
    reverse_edges.reverse()
    polyline, lengths, opening_ids = [start], [], []
    first_direction = last_direction = None
    internal_bends = 0
    for arc in reverse_edges:
        _check_deadline(deadline, "reconstruction")
        if first_direction is None:
            first_direction = arc.direction
        if last_direction == arc.direction:
            # Only same-signed travel is merged. A reversal must survive.
            polyline[-1] = arc.target
        else:
            polyline.append(arc.target)
            internal_bends += int(last_direction is not None)
        last_direction = arc.direction
        lengths.append(arc.length_m)
        for opening_id in arc.opening_ids:
            _check_deadline(deadline, "reconstruction")
            opening_ids.append(opening_id)
    if reverse_edges:
        polyline[-1] = goal
    initial_direction = initial_state[1]
    initial_bend = int(first_direction is not None and initial_direction is not None
                       and first_direction != initial_direction)
    try:
        length_m = fsum(lengths)
    except OverflowError as error:
        raise ValueError("Route length is not finite") from error
    cost = _finite_cost(length_weight * length_m + bend_weight * (internal_bends + initial_bend))
    _check_deadline(deadline, "completion")
    return RouteSearchResult("success", polyline, length_m, cost, internal_bends, initial_bend,
                             tuple(opening_ids), first_direction, last_direction)


def astar_route(graph, start: Coord, goal: Coord, config, deadline: float | None = None,
                initial_direction: Direction | None = None) -> RouteSearchResult:
    """Minimize weighted length plus bends for one leg on a validated graph.

    The caller must use a Task 3.1 ready graph selected for this same endpoint
    pair. A* cannot validate geometry, propagate unsupported_geometry, or
    reconsider an excluded-room policy from a raw graph. It never changes the
    graph. initial_direction is travel ARRIVING at start, not a required first
    move. A reversal costs one bend, just like a right-angle turn.

    A success contains internal bends and the optional initial junction bend
    separately, and never charges an unknown goal junction. Complete-ring J
    remains Part 2's responsibility. Coincident endpoints yield [start], no
    travel directions and zero cost, even when an initial heading is supplied.
    """
    try:
        adjacency, length_weight, bend_weight = _validate(
            graph, start, goal, config, deadline, initial_direction)

        def heuristic(vertex):
            return _finite_cost(length_weight * (abs(vertex[0] - goal[0]) + abs(vertex[1] - goal[1])))

        initial_state = (start, initial_direction)
        best = {initial_state: 0.0}
        parents = {}
        serial = count()
        h = heuristic(start)
        frontier = [(h, h, next(serial), 0.0, initial_state)]
        while frontier:
            _check_deadline(deadline, "expansion")
            _, _, _, current_cost, state = heappop(frontier)
            if current_cost != best[state]:
                continue
            vertex, incoming = state
            if vertex == goal:
                return _reconstruct(parents, initial_state, state, start, goal,
                                    length_weight, bend_weight, deadline)
            for arc in adjacency[vertex]:
                _check_deadline(deadline, "relaxation")
                turn = int(incoming is not None and incoming != arc.direction)
                candidate = _finite_cost(current_cost + length_weight * arc.length_m + bend_weight * turn)
                next_state = (arc.target, arc.direction)
                if candidate < best.get(next_state, float("inf")):
                    best[next_state] = candidate
                    parents[next_state] = (state, arc)
                    h = heuristic(arc.target)
                    heappush(frontier, (_finite_cost(candidate + h), h, next(serial), candidate, next_state))
        _check_deadline(deadline, "completion")
        return RouteSearchResult("unreachable", diagnostics=(
            "Direction-expanded search exhausted on the supplied graph; not proven geometric infeasibility.",))
    except _TimedOut as error:
        return RouteSearchResult("timeout", diagnostics=(str(error),))
