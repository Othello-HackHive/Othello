"""Routing-local results, not replacements for the shared domain models."""

from dataclasses import dataclass, field
from typing import Any, Literal

import networkx as nx

Coord = tuple[float, float]
GraphStatus = Literal["ready", "unreachable_on_grid", "timeout", "unsupported_geometry"]
TransitMode = Literal["permitted_only", "allow"]
Direction = Literal["E", "W", "N", "S"]
RouteStatus = Literal["success", "unreachable", "timeout"]


@dataclass(frozen=True, slots=True)
class GraphAttempt:
    """Evidence from one fully built graph and completed connectivity check."""

    transit_mode: TransitMode
    fallback_used: bool
    connected: bool


@dataclass(frozen=True, slots=True)
class GraphBuildResult:
    """Only ready results expose a graph. Disconnection is never a proof.

    Nodes are exact coordinate tuples. Each directed edge has length_m,
    direction (E/W/N/S), and opening_ids in traversal order. The terminal
    mapping preserves aliases at coincident points. No weights or bends are
    computed here; Task 3.2 can iterate graph.adj[vertex].
    """

    status: GraphStatus
    graph: nx.DiGraph | None = None
    terminal_vertices: dict[str, Coord] = field(default_factory=dict)
    transit_mode: TransitMode | None = None
    fallback_used: bool = False
    attempts: tuple[GraphAttempt, ...] = ()
    diagnostics: tuple[str, ...] = ()
    # True only for deterministic plan-wide geometry validation failures.
    # Terminal-specific failures and timeouts must not poison other legs.
    cacheable_unsupported: bool = False


@dataclass(frozen=True, slots=True)
class RouteSearchResult:
    """One leg's graph-optimal length/bend result, not an official score.

    Failure results contain only status/diagnostics. initial_bend accounts for
    the supplied arriving heading; internal_bends excludes both leg junctions.
    There is no charge for a future outgoing heading at the goal. Opening IDs
    retain traversal order and repetitions.

    A zero-length success has a one-point internal polyline and no headings.
    Ring assembly must carry its previous heading through that leg; adapting
    this result to the shared >=2-point Polyline contract remains separate.
    """

    status: RouteStatus
    polyline: list[Coord] | None = None
    length_m: float | None = None
    cost: float | None = None
    internal_bends: int | None = None
    initial_bend: int | None = None
    opening_ids: tuple[str, ...] | None = None
    first_direction: Direction | None = None
    last_direction: Direction | None = None
    diagnostics: tuple[str, ...] = ()


TableRouteStatus = Literal["success", "unreachable", "unreachable_on_grid", "unsupported_geometry", "timeout"]


@dataclass(frozen=True, slots=True)
class TableRouteResult:
    """Leg routing result from DistanceTable with full status fidelity.

    Never masks timeouts or unsupported geometry as infinite distance or
    proven geometric infeasibility.
    """

    status: TableRouteStatus
    polyline: list[Coord] | None = None
    length_m: float | None = None
    cost: float | None = None
    internal_bends: int | None = None
    initial_bend: int | None = None
    opening_ids: tuple[str, ...] | None = None
    first_direction: Direction | None = None
    last_direction: Direction | None = None
    diagnostics: tuple[str, ...] = ()


RouteLibraryStatus = Literal["ready", "unreachable_on_grid", "unsupported_geometry", "timeout"]


@dataclass(frozen=True, slots=True)
class RouteVariant:
    """One graph-valid leg option; weighted_leg_cost is not complete-design J."""

    polyline: tuple[Coord, ...]
    length_m: float
    weighted_leg_cost: float
    internal_bends: int
    initial_bend: int
    opening_ids: tuple[str, ...]
    first_direction: Direction | None
    last_direction: Direction | None
    source_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RouteLibraryResult:
    status: RouteLibraryStatus
    routes: tuple[RouteVariant, ...] = ()
    diagnostics: tuple[str, ...] = ()


class RoutingFailureError(RuntimeError):
    """Raised when distance or cost is queried for a non-success route."""

    def __init__(self, status: str, diagnostics: tuple[str, ...] = ()):
        super().__init__(f"Routing failed with status {status!r}: {'; '.join(diagnostics) or 'no diagnostics'}")
        self.status = status
        self.diagnostics = diagnostics


@dataclass(frozen=True, slots=True)
class ValidatedGraph:
    """Authoritative, deeply immutable validated graph representation.

    Adjacency is wrapped in MappingProxyType over a private dictionary
    containing immutable tuples of _Arc objects. Vertices is a frozenset.
    """

    adjacency: Any  # MappingProxyType[Coord, tuple[_Arc, ...]]
    vertices: frozenset[Coord]
    node_count: int
    edge_count: int


@dataclass(frozen=True, slots=True)
class CacheStats:
    """Metrics snapshot for RoutingCache."""

    path_hits: int
    path_misses: int
    graph_hits: int
    graph_misses: int
    evictions: int
    path_count: int
    graph_count: int
    graph_builds: int = 0
    graph_build_seconds: float = 0.0

    @property
    def path_hit_rate(self) -> float:
        total = self.path_hits + self.path_misses
        return (self.path_hits / total) if total > 0 else 0.0

    @property
    def graph_hit_rate(self) -> float:
        total = self.graph_hits + self.graph_misses
        return (self.graph_hits / total) if total > 0 else 0.0

    def summary(self) -> str:
        return (
            f"CacheStats(paths={self.path_count}, graphs={self.graph_count}, "
            f"path_hits={self.path_hits}, path_misses={self.path_misses}, "
            f"path_hit_rate={self.path_hit_rate:.1%}, graph_hits={self.graph_hits}, "
            f"graph_misses={self.graph_misses}, graph_hit_rate={self.graph_hit_rate:.1%}, "
            f"evictions={self.evictions})"
        )

