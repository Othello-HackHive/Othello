"""The teacher's measuring tools (T06).

Small, careful geometry helpers the rule checks will use.  They test the *real*
normalised shapes of the house (walls, windows, inner outline), never the
solver's search grid.  Every comparison allows the official 1 mm tolerance.

All numbers are metres.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

from shapely.geometry import LineString, Point

from othello.checker.metrics import clean_polyline
from othello.models.plan import Coord, FloorPlan

DEFAULT_TOL = 0.001  # 1 mm

# The kinds of problem a cable piece can have (same words as the violation codes).
NOT_AXIS_ALIGNED = "SEGMENT_NOT_AXIS_ALIGNED"
WALL_HIT = "WALL_INTERSECTION"
WINDOW_HIT = "WINDOW_INTERSECTION"
OUTSIDE = "OUTSIDE_INNER"


# ------------------------------------------------------------ points and lines


def almost_equal(a: Coord, b: Coord, tol: float = DEFAULT_TOL) -> bool:
    """Are two points the same point?  (within tolerance in both directions)"""
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def is_axis_aligned(p: Coord, q: Coord, tol: float = DEFAULT_TOL) -> bool:
    """Is the piece p-q exactly horizontal or exactly vertical?"""
    return abs(p[0] - q[0]) <= tol or abs(p[1] - q[1]) <= tol


def non_axis_segments(polyline: list[Coord], tol: float = DEFAULT_TOL) -> list[int]:
    """Positions of the pieces that are slanted."""
    return [
        i
        for i, (p, q) in enumerate(itertools.pairwise(polyline))
        if not is_axis_aligned(p, q, tol)
    ]


def merge_collinear(polyline: list[Coord], tol: float = DEFAULT_TOL) -> list[Coord]:
    """Join straight stretches into one piece and drop repeated points."""
    return clean_polyline(polyline, tol)


# ------------------------------------------------------------ rooms and walls


def point_in_polygon(point: Coord, polygon: Any, tol: float = DEFAULT_TOL) -> bool:
    """Is the point inside the room (touching the edge counts, within tolerance)?"""
    return bool(polygon.buffer(tol).contains(Point(point)))


def distance_to(point: Coord, geometry: Any) -> float:
    """How far the point is from the nearest part of a shape (0 if on or inside it).

    An empty shape (a plan with no walls, or no windows) is infinitely far away.
    """
    if geometry is None or geometry.is_empty:
        return float("inf")
    return float(Point(point).distance(geometry))


def has_clearance(
    point: Coord, walls: Any, minimum: float = 0.5, tol: float = DEFAULT_TOL
) -> bool:
    """Is the point at least ``minimum`` metres away from every wall?"""
    return distance_to(point, walls) >= minimum - tol


# -------------------------------------------------------- cable vs real shapes


@dataclass(frozen=True)
class GeometryIssue:
    """One problem found on a cable piece."""

    kind: str  # one of the four constants above
    segment_index: int
    point: Coord  # a spot on the problem, so the overlay can mark it red


class FreeSpaceChecker:
    """Tests cables against the house's real walls, windows and outline.

    A cable only touching a wall's surface is fine; it must go deeper than the
    tolerance to count as hitting it.
    """

    def __init__(self, inner: Any, walls: Any, windows: Any, tol: float = DEFAULT_TOL):
        self.tol = tol
        self._inner = inner.buffer(tol)
        self._walls = self._shrink(walls, tol)
        self._windows = self._shrink(windows, tol)

    @classmethod
    def from_plan(cls, plan: FloorPlan, tol: float = DEFAULT_TOL) -> FreeSpaceChecker:
        return cls(plan.inner_m, plan.walls_m, plan.windows_m, tol)

    @staticmethod
    def _shrink(geometry: Any, tol: float) -> Any:
        if geometry is None or geometry.is_empty:
            return geometry
        return geometry.buffer(-tol)

    def issues(self, polyline: list[Coord]) -> list[GeometryIssue]:
        """Every problem on every piece of the cable, in order."""
        found: list[GeometryIssue] = []
        for index, (p, q) in enumerate(itertools.pairwise(polyline)):
            if almost_equal(p, q, self.tol):
                continue  # a zero-length piece cannot hit anything new
            line = LineString([p, q])
            if not is_axis_aligned(p, q, self.tol):
                found.append(GeometryIssue(NOT_AXIS_ALIGNED, index, _middle(p, q)))
            outside = line.difference(self._inner)
            if not outside.is_empty:
                found.append(GeometryIssue(OUTSIDE, index, _spot(outside)))
            for kind, shape in ((WALL_HIT, self._walls), (WINDOW_HIT, self._windows)):
                if shape is None or shape.is_empty:
                    continue
                hit = line.intersection(shape)
                if not hit.is_empty:
                    found.append(GeometryIssue(kind, index, _spot(hit)))
        return found

    def is_clear(self, polyline: list[Coord]) -> bool:
        return not self.issues(polyline)


def _middle(p: Coord, q: Coord) -> Coord:
    return ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)


def _spot(geometry: Any) -> Coord:
    point = geometry.representative_point()
    return (float(point.x), float(point.y))
