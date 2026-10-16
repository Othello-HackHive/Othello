from othello.routing import DistanceTable, RoutingCache
from othello.models import SolverConfig
from othello.solver.candidate_tables import _CandidateTable, candidate_tables
from othello.solver.hub_candidates import generate_hub_candidates
from othello.territory import generate_partitions, hub_lower_bound
from tests.fixtures.plans import tiny_plan


def test_candidate_family_routes_match_individual_tables():
    plan = tiny_plan()
    config = SolverConfig()
    options, status = generate_partitions(plan, hub_lower_bound(plan, config), config)
    assert status == "feasible"
    territory = options[0].territories[0]
    candidates = generate_hub_candidates(plan, territory, config).candidates[:4]
    family_cache = RoutingCache()
    family = list(candidate_tables(plan, "H1", territory.node_ids, candidates,
                                   config, family_cache, None))
    assert len(family) == len(candidates)
    assert any(isinstance(table, _CandidateTable) for _, table in family)
    for candidate, table in family:
        individual = DistanceTable(plan, {"H1": candidate.point_m,
                                          **{node_id: plan.nodes[node_id].point_m
                                             for node_id in territory.node_ids}},
                                   config, RoutingCache())
        assert table.get_route("H1", "N1").status == individual.get_route("H1", "N1").status
        assert table.get_route("H1", "N1").polyline == individual.get_route("H1", "N1").polyline
    assert family_cache.stats().graph_builds == 1
