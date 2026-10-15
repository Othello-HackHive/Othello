from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

Coord = tuple[float, float]


class GeometryModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)


class Room(GeometryModel):
    room_id: str
    kind: str
    eligible: bool
    polygon_m: Any
    polygon_units: Any
    area_m2: float
    inside_inner: bool


class Node(BaseModel):
    node_id: str
    room_id: str
    point_m: Coord
    point_units: Coord


class Opening(GeometryModel):
    opening_id: str
    kind: str
    rooms: list[str]
    gap_polyline_m: list[Coord]
    counts_for_zone: bool


class FloorPlan(GeometryModel):
    plan_id: str
    scale: float
    rooms: dict[str, Room]
    nodes: dict[str, Node]
    openings: dict[str, Opening]
    inner_m: Any
    walls_m: Any
    windows_m: Any
    free_space_m: Any
    hub_zones_m: dict[str, Any]
    room_graph: dict[str, list[str]]
    excluded_interior_rooms: list[str]
    output_transform: dict[str, Any] = Field(default_factory=dict)
    repairs: list[str] = Field(default_factory=list)

    def eligible_rooms(self) -> list[Room]:
        return [room for room in self.rooms.values() if room.eligible]

    def total_eligible_area_m2(self) -> float:
        return sum(room.area_m2 for room in self.eligible_rooms())
