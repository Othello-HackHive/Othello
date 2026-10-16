"""Lightweight Part 1 model doubles; no production shared models."""

from types import SimpleNamespace

from shapely.geometry import GeometryCollection, box
from shapely.ops import unary_union


def make_config(**changes):
    values = dict(hub_grid_step_m=0.5, hub_wall_clearance_m=0.5,
                  tolerance_m=0.001, limits=SimpleNamespace(hub_candidates=40))
    values.update(changes)
    return SimpleNamespace(**values)


def make_room(room_id, polygon, *, eligible=True, inside_inner=True):
    return SimpleNamespace(room_id=room_id, polygon_m=polygon,
                           eligible=eligible, inside_inner=inside_inner)


def make_node(node_id, room_id, point):
    return SimpleNamespace(node_id=node_id, room_id=room_id, point_m=point)


def make_opening(opening_id, gap, *, rooms=("R1", "OTHER"), kind="door"):
    return SimpleNamespace(opening_id=opening_id, gap_polyline_m=gap,
                           rooms=list(rooms), kind=kind, counts_for_zone=kind != "entrance")


def make_case(*, rooms=None, zones=None, nodes=None, openings=(), walls=None,
              inner=None, territory_zone=None, room_ids=None, node_ids=None):
    rooms = [make_room("R1", box(0, 0, 4, 4))] if rooms is None else rooms
    by_room = {room.room_id: room for room in rooms}
    zones = {room.room_id: room.polygon_m for room in rooms} if zones is None else zones
    nodes = [make_node("N1", "R1", (1.0, 1.0))] if nodes is None else nodes
    by_node = {node.node_id: node for node in nodes}
    walls = GeometryCollection() if walls is None else walls
    inner = unary_union([room.polygon_m for room in rooms]) if inner is None else inner
    plan = SimpleNamespace(rooms=by_room, nodes=by_node,
                           openings={opening.opening_id: opening for opening in openings},
                           hub_zones_m=zones, walls_m=walls, inner_m=inner,
                           free_space_m=inner.difference(walls))
    room_ids = list(by_room) if room_ids is None else room_ids
    node_ids = list(by_node) if node_ids is None else node_ids
    if territory_zone is None:
        territory_zone = unary_union([zones[room_id] for room_id in room_ids])
    territory = SimpleNamespace(room_ids=room_ids, node_ids=node_ids,
                                hub_zone_m=territory_zone)
    return plan, territory
