"""Rectilinear search graphs; shared domain models remain owned by the team."""

from .grid import build_routing_graph
from .astar import astar_route, validate_graph
from .cache import GraphKey, PathKey, RoutingCache, plan_fingerprint
from .table import DistanceTable, build_distance_table
from .route_library import build_route_library
from .types import (
    CacheStats,
    Direction,
    GraphAttempt,
    GraphBuildResult,
    RouteSearchResult,
    RoutingFailureError,
    RouteLibraryResult,
    RouteVariant,
    TableRouteResult,
    TableRouteStatus,
    ValidatedGraph,
)

__all__ = [
    "CacheStats",
    "Direction",
    "DistanceTable",
    "GraphAttempt",
    "GraphBuildResult",
    "GraphKey",
    "PathKey",
    "RouteSearchResult",
    "RoutingCache",
    "RoutingFailureError",
    "RouteLibraryResult",
    "RouteVariant",
    "TableRouteResult",
    "TableRouteStatus",
    "ValidatedGraph",
    "astar_route",
    "build_distance_table",
    "build_routing_graph",
    "build_route_library",
    "plan_fingerprint",
    "validate_graph",
]

