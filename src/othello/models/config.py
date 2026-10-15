from pydantic import BaseModel, Field


class Weights(BaseModel):
    length: float = 1.0
    crossing: float = 5.0
    overlap: float = 1.0
    bend: float = 0.5


class Limits(BaseModel):
    partitions_per_hub_count: int = 12


class SolverConfig(BaseModel):
    """All rule constants and provisional policies live in one place."""

    max_nodes_per_hub: int = 5
    max_area_per_hub_m2: float = 100.0
    hub_wall_clearance_m: float = 0.5
    tolerance_m: float = 0.001
    cable_transit_policy: str = "avoid_if_possible"
    zone_link_through_excluded: bool = False
    repair_geometry: bool = True
    weights: Weights = Field(default_factory=Weights)
    limits: Limits = Field(default_factory=Limits)
