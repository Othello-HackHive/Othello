"""What comes back after solving and checking: violations, metrics, results.

Crossings and overlaps are *never* violations; they only raise the cost J.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .design import Design
from .plan import Coord

# Every rule the checker can report as broken (BACKEND_SCHEMA section 10).
VIOLATION_CODES: tuple[str, ...] = (
    "COVERAGE_MISSING",
    "COVERAGE_DUPLICATE",
    "CAPACITY_NODES",
    "CAPACITY_AREA",
    "ZONE_DISCONNECTED",
    "HUB_NOT_IN_ROOM",
    "HUB_WALL_CLEARANCE",
    "RING_NOT_CLOSED",
    "RING_WRONG_HUB",
    "RING_NODE_MISSING",
    "RING_NODE_REPEATED",
    "RING_BRANCH",
    "SEGMENT_NOT_AXIS_ALIGNED",
    "WALL_INTERSECTION",
    "WINDOW_INTERSECTION",
    "OUTSIDE_INNER",
    "ROOM_TRANSITION_NOT_VIA_OPENING",
    "NODE_MOVED",
    "OUTPUT_SCHEMA",
)


class Violation(BaseModel):
    type: str
    hub_id: str | None = None
    ring_id: str | None = None
    segment_index: int | None = None
    # Where it happened, so the overlay can draw it in red (metres).
    point: Coord | None = None
    detail: str = ""


class Metrics(BaseModel):
    hub_count: int
    L: float
    X: int
    O: float
    B: int
    J: float
    # Per hub: area_m2, node_count, ring_length_m, bends.
    per_hub: dict[str, dict] = Field(default_factory=dict)
    # Tie-break value (largest hub area minus smallest).
    area_gap_m2: float = 0.0


class RuleCheck(BaseModel):
    """One line of the audit panel: a rule, did it pass, and a short value."""

    rule_id: str
    label: str
    passed: bool
    value: str = ""
    detail: str = ""


class ValidationResult(BaseModel):
    valid: bool
    violations: list[Violation] = Field(default_factory=list)
    # Filled whenever it can be computed (also when valid is False).
    metrics: Metrics | None = None
    # Per-rule pass/fail summary for the audit panel.
    checks: list[RuleCheck] = Field(default_factory=list)


class HubCountStatus(BaseModel):
    lower_bound: int
    # hub count -> "feasible" | "proven_infeasible" | "unresolved"
    tried: dict[int, Literal["feasible", "proven_infeasible", "unresolved"]] = Field(default_factory=dict)
    proven_minimum: bool = False


class SolveResult(BaseModel):
    status: Literal["ok", "no_design_found"]
    # Only a checker-verified design is ever put here.
    design: Design | None = None
    validation: ValidationResult | None = None
    hub_count_status: HubCountStatus | None = None
    lower_bound_hubs: int = 0
    runtime_s: float = 0.0
    notes: list[str] = Field(default_factory=list)
