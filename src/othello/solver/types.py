"""Routing-independent, solver-local hub candidate results."""

from dataclasses import dataclass
from typing import Any, Literal


Coord = tuple[float, float]
HubCandidateStatus = Literal["ready", "no_candidates", "unsupported_geometry", "timeout"]


@dataclass(frozen=True, slots=True)
class HubCandidate:
    """An exact metre-coordinate candidate; the proxy is not routed distance or J."""

    point_m: Coord
    room_id: str
    source_tags: tuple[str, ...]
    nearby_opening_ids: tuple[str, ...]
    attachment_proxy_m: float


@dataclass(frozen=True, slots=True)
class HubCandidateResult:
    status: HubCandidateStatus
    candidates: tuple[HubCandidate, ...] = ()
    diagnostics: tuple[str, ...] = ()


NodeOrderStatus = Literal["ready", "unreachable_on_grid", "unsupported_geometry", "timeout"]


@dataclass(frozen=True, slots=True)
class NodeOrder:
    """One directed cycle; its distance is a routing estimate, never official J."""

    node_ids: tuple[str, ...]
    sequence: tuple[str, ...]
    estimated_length_m: float
    estimated_opening_ids: tuple[str, ...]
    first_door_id: str | None
    last_door_id: str | None


@dataclass(frozen=True, slots=True)
class NodeOrderResult:
    status: NodeOrderStatus
    orders: tuple[NodeOrder, ...] = ()
    enumerated_count: int = 0
    diagnostics: tuple[str, ...] = ()


F0Status = Literal[
    "constructed_unverified", "no_candidates", "unreachable_on_grid",
    "unsupported_geometry", "candidate_configuration_error", "timeout",
]


@dataclass(frozen=True, slots=True)
class F0Result:
    """Contract-shaped Design data awaiting the independent Part 2 checker.

    ``design`` is a nested mapping with exactly the documented Design, Hub,
    Ring, and RouteLeg fields. It is not a shared model or a validity verdict.
    """

    status: F0Status
    design: dict[str, Any] | None = None
    diagnostics: tuple[str, ...] = ()


RouteSelectionStatus = Literal[
    "verified", "unverified", "no_verified_design", "unreachable_on_grid",
    "unsupported_geometry", "timeout",
]


@dataclass(frozen=True, slots=True)
class RouteSelectionResult:
    """One bounded library search; J exists only after independent verification."""

    status: RouteSelectionStatus
    design: dict[str, Any] | None = None
    verified: bool = False
    J: float | None = None
    search_mode: Literal["none", "exact", "beam"] = "none"
    combinations: int = 0
    checked_designs: int = 0
    diagnostics: tuple[str, ...] = ()


OuterSearchStatus = Literal[
    "verified", "unverified", "no_design_found", "candidate_configuration_error", "timeout",
]


@dataclass(frozen=True, slots=True)
class HubCountSummary:
    lower_bound: int
    tried: dict[int, Literal["feasible", "proven_infeasible", "unresolved"]]
    proven_minimum: bool


@dataclass(frozen=True, slots=True)
class OuterSearchResult:
    """Part 3-local anytime result; only checker-verified designs enter design."""

    status: OuterSearchStatus
    design: dict[str, Any] | None
    provisional_design: dict[str, Any] | None
    verified: bool
    J: float | None
    hub_count_status: HubCountSummary
    partitions_considered: int = 0
    configurations_considered: int = 0
    diagnostics: tuple[str, ...] = ()

    @property
    def submission_design(self) -> dict[str, Any] | None:
        """Only the independently checked incumbent can be submitted."""
        return self.design if self.verified and self.design is not None and self.J is not None else None

    @property
    def timeout_with_verified_design(self) -> bool:
        return self.status == "timeout" and self.submission_design is not None


ImprovementStatus = Literal[
    "improved", "unchanged", "verified", "unverified",
    "no_verified_design", "unsupported_geometry", "candidate_configuration_error", "timeout",
]


@dataclass(frozen=True, slots=True)
class ImprovementResult:
    """A local search result; design and J exist only after checker verification."""

    status: ImprovementStatus
    design: dict[str, Any] | None = None
    provisional_design: dict[str, Any] | None = None
    verified: bool = False
    J: float | None = None
    moves_considered: int = 0
    checked_designs: int = 0
    diagnostics: tuple[str, ...] = ()
