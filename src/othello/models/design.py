"""Contract 2: the design that Part 3 (solver) hands to Part 2 (checker).

Everything here is in metres.  A design is only a *claim*; the checker decides
whether it is valid.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .plan import Coord

# A cable path: at least two points, every piece horizontal or vertical.
Polyline = list[Coord]


class RouteLeg(BaseModel):
    """The cable between two consecutive stops on a ring (e.g. H1 to N1)."""

    from_id: str
    to_id: str
    polyline: Polyline
    doors_used: list[str] = Field(default_factory=list)

    @field_validator("polyline")
    @classmethod
    def _needs_two_points(cls, polyline: Polyline) -> Polyline:
        if len(polyline) < 2:
            raise ValueError("a polyline needs at least two points")
        return polyline


class Ring(BaseModel):
    """One closed loop: H1 -> nodes -> H1."""

    hub_id: str
    sequence: list[str]
    legs: list[RouteLeg]


class Hub(BaseModel):
    hub_id: str
    room_id: str
    location: Coord
    assigned_rooms: list[str]
    assigned_nodes: list[str]
    ring: Ring


class Design(BaseModel):
    plan_id: str
    hubs: list[Hub]
    # Internal notes only; never written into the official JSON.
    solver_notes: dict = Field(default_factory=dict)
