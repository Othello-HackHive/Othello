from pydantic import BaseModel, Field


class Weights(BaseModel):
    length: float = 1.0
    crossing: float = 5.0
    overlap: float = 1.0
    bend: float = 0.5


class Limits(BaseModel):
    partitions_per_H: int = 12
    hub_candidates: int = 40
    orders_kept: int = 12
    routes_per_leg: int = 6
    beam_width: int = 20
    exact_combo_limit: int = 20_000


class SolverConfig(BaseModel):
    """All rule constants and provisional policies live in one place."""

    max_nodes_per_hub: int = 5
    max_area_per_hub_m2: float = 100.0
    hub_wall_clearance_m: float = 0.5
    tolerance_m: float = 0.001
    cable_transit_policy: str = "avoid_if_possible"
    zone_link_through_excluded: bool = False
    tiebreak_gap_definition: str = "max_minus_min"
    cable_wall_margin_m: float = 0.02
    t_touch_counts_as_crossing: bool = True
    overlap_endpoint_crossing: bool = False
    parallel_lane_offset_m: float = 0.0
    repair_geometry: bool = True
    time_budget_s: float = 60.0
    final_check_reserve_fraction: float = 0.10
    hub_grid_step_m: float = 0.25
    grid_step_m: float = 0.10
    output_precision_decimals: int = 4
    weights: Weights = Field(default_factory=Weights)
    limits: Limits = Field(default_factory=Limits)
