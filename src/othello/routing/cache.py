"""Task 3.3: Bounded LRU routing cache with reliable geometry content hashing."""

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
import hashlib
from numbers import Real
import sys
import time
from types import MappingProxyType
from typing import Any

from shapely.geometry import LineString
from shapely.geometry.base import BaseGeometry

from .astar import _check_deadline, _coord, _finite_number
from .types import CacheStats, Coord, Direction, RouteSearchResult, ValidatedGraph


def plan_fingerprint(plan: Any, deadline: float | None = None) -> str:
    """Compute a deterministic cryptographic content hash over all routing fields.

    Deadlines are verified before and after hash computation to ensure strict
    budget enforcement during fingerprint generation.
    """
    _check_deadline(deadline, "fingerprint generation")
    plan_id = str(getattr(plan, "plan_id", "unnamed"))
    hasher = hashlib.sha256()
    hasher.update(plan_id.encode("utf-8"))

    # Explicit version if present
    version = getattr(plan, "geometry_version", None)
    if version is not None:
        hasher.update(f":ver:{version}".encode("utf-8"))

    # Polygonal plan boundaries and obstacles
    for attr in ("inner_m", "free_space_m", "walls_m", "windows_m"):
        geom = getattr(plan, attr, None)
        if isinstance(geom, BaseGeometry) and not geom.is_empty:
            hasher.update(attr.encode("utf-8"))
            hasher.update(geom.wkb)
        else:
            hasher.update(f":{attr}:empty".encode("utf-8"))

    # Rooms and their polygonal definitions
    rooms = getattr(plan, "rooms", {})
    if isinstance(rooms, dict):
        for room_id in sorted(rooms):
            room = rooms[room_id]
            poly = getattr(room, "polygon_m", None)
            inside = bool(getattr(room, "inside_inner", True))
            hasher.update(f":room:{room_id}:{inside}".encode("utf-8"))
            if isinstance(poly, BaseGeometry) and not poly.is_empty:
                hasher.update(poly.wkb)

    # Excluded interior room IDs
    excluded = getattr(plan, "excluded_interior_rooms", ())
    if isinstance(excluded, (list, tuple, set, frozenset)):
        for room_id in sorted(excluded):
            hasher.update(f":excluded:{room_id}".encode("utf-8"))

    # Openings: IDs, kinds, room pairs, and gap geometries
    openings = getattr(plan, "openings", {})
    if isinstance(openings, dict):
        for op_id in sorted(openings):
            op = openings[op_id]
            kind = str(getattr(op, "kind", "door"))
            op_rooms = tuple(sorted(getattr(op, "rooms", ())))
            hasher.update(f":opening:{op_id}:{kind}:{op_rooms}".encode("utf-8"))
            gap_coords = getattr(op, "gap_polyline_m", None) or getattr(op, "gap", None)
            if gap_coords is not None:
                if isinstance(gap_coords, BaseGeometry):
                    hasher.update(gap_coords.wkb)
                elif isinstance(gap_coords, (list, tuple)) and len(gap_coords) >= 2:
                    try:
                        hasher.update(LineString(gap_coords).wkb)
                    except Exception:
                        hasher.update(str(gap_coords).encode("utf-8"))

    # Fixed nodes attached to the plan
    nodes = getattr(plan, "nodes", {})
    if isinstance(nodes, dict):
        for node_id in sorted(nodes):
            node = nodes[node_id]
            point = getattr(node, "point_m", None)
            if point is not None:
                hasher.update(f":node:{node_id}:{tuple(point)}".encode("utf-8"))

    _check_deadline(deadline, "fingerprint generation")
    return f"{plan_id}:{hasher.hexdigest()}"


@dataclass(frozen=True, slots=True)
class GraphKey:
    """Complete graph-generation signature.

    Includes the plan content hash, routing configuration, the complete sorted
    terminal mapping that forms coordinate lines. A concrete ``leg`` identifies
    the pair whose connectivity chose the fallback grid and transit mode.
    ``leg=None`` is reserved for a validated, unaugmented Hanan graph in the
    policy's most restrictive mode; it is reusable only after confirming that
    the requested distinct endpoints connect on that graph.
    """

    plan_sig: str
    policy: str
    cable_wall_margin_m: float
    grid_step_m: float
    tolerance_m: float
    terminals: tuple[tuple[str, Coord], ...]
    leg: tuple[str, str] | None


@dataclass(frozen=True, slots=True)
class PathKey:
    """Complete path query key embedding the full graph-generation signature."""

    graph_key: GraphKey
    start: Coord
    goal: Coord
    initial_direction: Direction | None
    length_weight: float
    bend_weight: float


def _defensive_copy_route(res: RouteSearchResult) -> RouteSearchResult:
    """Return an isolated copy of RouteSearchResult with a fresh polyline list."""
    poly = list(res.polyline) if res.polyline is not None else None
    return RouteSearchResult(
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


class RoutingCache:
    """Bounded LRU cache for immutable ValidatedGraphs and RouteSearchResults.

    Supports dependency injection, memory usage inspection, deterministic eviction,
    and strict status admission rules.
    """

    def __init__(self, max_paths: int = 10000, max_graphs: int = 6):
        if not isinstance(max_paths, int) or max_paths <= 0:
            raise ValueError("max_paths must be a positive integer")
        if not isinstance(max_graphs, int) or max_graphs <= 0:
            raise ValueError("max_graphs must be a positive integer")
        self.max_paths = max_paths
        self.max_graphs = max_graphs
        self._paths: OrderedDict[PathKey, RouteSearchResult] = OrderedDict()
        self._graphs: OrderedDict[GraphKey, ValidatedGraph] = OrderedDict()
        self._path_hits = 0
        self._path_misses = 0
        self._graph_hits = 0
        self._graph_misses = 0
        self._evictions = 0

    def get_route(self, key: PathKey, deadline: float | None = None) -> RouteSearchResult | None:
        """Retrieve a cached route result, enforcing deadline checks before returning."""
        _check_deadline(deadline, "cache lookup")
        if key in self._paths:
            self._paths.move_to_end(key)
            self._path_hits += 1
            return _defensive_copy_route(self._paths[key])
        self._path_misses += 1
        return None

    def put_route(self, key: PathKey, result: RouteSearchResult) -> None:
        """Store a deterministic route result.

        Only success, unreachable, and unreachable_on_grid are admitted.
        Timeouts and unsupported_geometry are strictly rejected.
        """
        if result.status not in {"success", "unreachable", "unreachable_on_grid"}:
            return
        if key in self._paths:
            self._paths.move_to_end(key)
        else:
            if len(self._paths) >= self.max_paths:
                self._paths.popitem(last=False)
                self._evictions += 1
        self._paths[key] = _defensive_copy_route(result)

    def get_graph(self, key: GraphKey, deadline: float | None = None) -> ValidatedGraph | None:
        """Retrieve a cached ValidatedGraph, enforcing deadline checks."""
        _check_deadline(deadline, "graph cache lookup")
        if key in self._graphs:
            self._graphs.move_to_end(key)
            self._graph_hits += 1
            return self._graphs[key]
        self._graph_misses += 1
        return None

    def put_graph(self, key: GraphKey, graph: ValidatedGraph) -> None:
        """Store an immutable ValidatedGraph under LRU bounds."""
        if not isinstance(graph, ValidatedGraph):
            raise ValueError("RoutingCache stores only immutable ValidatedGraph instances")
        if key in self._graphs:
            self._graphs.move_to_end(key)
        else:
            if len(self._graphs) >= self.max_graphs:
                self._graphs.popitem(last=False)
                self._evictions += 1
        self._graphs[key] = graph

    def invalidate(self, plan_id: str | None = None) -> None:
        """Invalidate all cached items, or only those matching plan_id."""
        if plan_id is None:
            self._paths.clear()
            self._graphs.clear()
            return
        prefix = f"{plan_id}:"
        stale_paths = [k for k in self._paths if k.graph_key.plan_sig.startswith(prefix)]
        for k in stale_paths:
            del self._paths[k]
        stale_graphs = [k for k in self._graphs if k.plan_sig.startswith(prefix)]
        for k in stale_graphs:
            del self._graphs[k]

    def stats(self) -> CacheStats:
        """Snapshot current performance and entry metrics."""
        return CacheStats(
            path_hits=self._path_hits,
            path_misses=self._path_misses,
            graph_hits=self._graph_hits,
            graph_misses=self._graph_misses,
            evictions=self._evictions,
            path_count=len(self._paths),
            graph_count=len(self._graphs),
        )

    def memory_usage_bytes(self) -> dict[str, int]:
        """Estimate retained cache bytes, counting shared objects once.

        Includes a same-sized dictionary estimate for each mapping proxy's
        otherwise hidden backing dictionary. Python allocator overhead outside
        the reachable cache objects is not included.
        """
        seen: set[int] = set()

        def measure(value: Any) -> int:
            identity = id(value)
            if identity in seen:
                return 0
            seen.add(identity)

            size = sys.getsizeof(value)
            if isinstance(value, MappingProxyType):
                # The proxy keeps a private dict alive; getsizeof(proxy) alone
                # reports only the small wrapper. The copy approximates its
                # backing table without recounting referenced keys or values.
                size += sys.getsizeof(dict(value))
            if isinstance(value, Mapping):
                for key, item in value.items():
                    size += measure(key) + measure(item)
            elif isinstance(value, (tuple, list, set, frozenset)):
                for item in value:
                    size += measure(item)
            elif is_dataclass(value) and not isinstance(value, type):
                for field in fields(value):
                    size += measure(getattr(value, field.name))
            return size

        # Graph keys referenced by path keys are attributed to graphs first.
        graphs_mem = measure(self._graphs)
        paths_mem = measure(self._paths)

        return {
            "paths_bytes": paths_mem,
            "graphs_bytes": graphs_mem,
            "total_bytes": paths_mem + graphs_mem,
        }
