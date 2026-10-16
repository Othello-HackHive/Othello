"""Uploaded plan files for the service tests."""

import json

from shapely.geometry import MultiPolygon, box, mapping


def geojson(geometry):
    return json.loads(json.dumps(mapping(geometry)))


def one_room_upload(plan_id="UP_1", **changes):
    """An 8 m x 8 m room with a wall all round it: the smallest plan that gets a verified design."""
    upload = {
        "id": plan_id,
        "area": 64.0,
        "inner": geojson(box(0, 0, 80, 80)),
        "wall": geojson(
            MultiPolygon([box(-2, -2, 82, 82).difference(box(0, 0, 80, 80))])
        ),
        "living": box(0, 0, 80, 80).wkt,
    }
    upload.update(changes)
    return upload


def as_bytes(obj):
    return json.dumps(obj).encode("utf-8")
