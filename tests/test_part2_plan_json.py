"""T21: reading an uploaded plan file (JSON in the ResPlan layout)."""

import json
import math

import pytest
from shapely.geometry import MultiPolygon, box, mapping

from othello.service.plan_json import (
    MAX_COORDINATES,
    PlanUploadError,
    parse_plan_json,
    plan_from_json,
    record_from_json,
    record_to_json,
)


def geojson(geometry):
    return json.loads(json.dumps(mapping(geometry)))


def good_upload(**changes):
    """An 8 m x 8 m room with a wall all round it, as an upload dictionary."""
    upload = {
        "id": "UP_1",
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


# --------------------------------------------------- reading the text


def test_a_good_upload_becomes_a_floor_plan():
    plan = plan_from_json(as_bytes(good_upload()))
    assert (
        plan.plan_id == "UP_1"
        and list(plan.rooms) == ["R01"]
        and list(plan.nodes) == ["N1"]
    )
    assert plan.scale == pytest.approx(10.0)


def test_geojson_wkt_features_and_nulls_can_be_mixed():
    upload = good_upload(
        living={
            "type": "Feature",
            "properties": {},
            "geometry": geojson(box(0, 0, 80, 80)),
        },
        window=None,
        door="",
        bedroom=None,
    )
    record = record_from_json(upload)
    assert (
        record["living"].equals(box(0, 0, 80, 80))
        and record["window"].is_empty
        and record["door"].is_empty
    )
    assert plan_from_json(upload).plan_id == "UP_1"


def test_a_byte_order_mark_and_a_parsed_dictionary_are_accepted():
    assert parse_plan_json(b"\xef\xbb\xbf" + as_bytes({"a": 1})) == {"a": 1}
    assert plan_from_json(good_upload()).plan_id == "UP_1"


@pytest.mark.parametrize(
    "data, message",
    [
        (b"", "empty"),
        (b"not json", "not valid JSON"),
        (b"\xff\xfe\x00", "UTF-8"),
        (b"[1, 2]", "one JSON object"),
        (b'"text"', "one JSON object"),
        (b'{"area": NaN}', "NaN"),
        (b'{"area": Infinity}', "Infinity"),
    ],
)
def test_text_that_is_not_a_json_object_is_refused_with_a_reason(data, message):
    with pytest.raises(PlanUploadError, match=message):
        parse_plan_json(data)


def test_deeply_nested_json_is_refused_not_crashed_on():
    with pytest.raises(PlanUploadError, match="nested too deeply"):
        parse_plan_json(b"[" * 100000)


def test_a_file_over_the_size_limit_is_refused():
    with pytest.raises(PlanUploadError, match="limit is 100"):
        parse_plan_json(b" " * 101 + b"{}", max_bytes=100)


# --------------------------------------------------- the fields


@pytest.mark.parametrize("missing", ["inner", "area"])
def test_required_fields_are_required(missing):
    upload = good_upload()
    del upload[missing]
    with pytest.raises(PlanUploadError, match=f"missing required field.*{missing}"):
        record_from_json(upload)


@pytest.mark.parametrize("area", [0, -5, True, "64", float("inf"), None])
def test_area_must_be_a_positive_finite_number(area):
    with pytest.raises(PlanUploadError):
        record_from_json(good_upload(area=area))


def test_wall_depth_must_be_a_number_when_given():
    assert record_from_json(good_upload(wall_depth=4.5))["wall_depth"] == 4.5
    with pytest.raises(PlanUploadError, match="wall_depth"):
        record_from_json(good_upload(wall_depth="thick"))


def test_unknown_fields_are_ignored():
    record = record_from_json(
        good_upload(graph={"nodes": []}, neighbor=[1], net_area=3, hello="x")
    )
    assert not {"graph", "neighbor", "hello", "net_area"} & set(record)


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "Point", "coordinates": [1, 2]},
        {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        "POINT (1 2)",
        "LINESTRING (0 0, 1 1)",
        12,
        [1, 2],
        {"type": "Polygon"},
        {"type": "Polygon", "coordinates": "x"},
        "POLYGON ((0 0, 1 0",
        {"coordinates": []},
    ],
)
def test_a_shape_that_is_not_a_polygon_or_is_unreadable_is_refused(bad):
    with pytest.raises(PlanUploadError):
        record_from_json(good_upload(wall=bad))


def test_an_empty_inner_boundary_is_refused():
    with pytest.raises(PlanUploadError, match="inner"):
        record_from_json(good_upload(inner={"type": "MultiPolygon", "coordinates": []}))


def test_non_finite_coordinates_are_refused():
    bad = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, math.inf], [0, 0]]]}
    with pytest.raises(PlanUploadError):
        record_from_json(good_upload(wall=bad))


def test_too_many_coordinates_are_refused_before_shapely_builds_them():
    ring = [[i, 0] for i in range(MAX_COORDINATES // 2 + 10)] + [[0, 1], [0, 0]]
    huge = {"type": "Polygon", "coordinates": [ring, ring]}
    with pytest.raises(PlanUploadError, match="coordinate numbers"):
        record_from_json(good_upload(wall=huge))
    with pytest.raises(PlanUploadError, match="coordinate numbers"):
        record_from_json(
            good_upload(
                wall="POLYGON ((" + ",".join(["0 0"] * (MAX_COORDINATES + 5)) + "))"
            )
        )


def test_too_many_parts_are_refused():
    parts = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[i, 0], [i + 0.5, 0], [i + 0.5, 1], [i, 0]]] for i in range(5001)
        ],
    }
    with pytest.raises(PlanUploadError, match="parts"):
        record_from_json(good_upload(wall=parts))


# --------------------------------------------------- the plan id


@pytest.mark.parametrize(
    "given, expected",
    [
        ("UP_1", "UP_1"),
        (14433, "14433"),
        ("../../etc/passwd", "etc_passwd"),
        ("a b/c", "a_b_c"),
        (None, "uploaded_plan"),
        (True, "uploaded_plan"),
        ([1], "uploaded_plan"),
    ],
)
def test_the_plan_id_can_never_climb_out_of_a_folder(given, expected):
    assert record_from_json(good_upload(id=given))["id"] == expected


def test_a_very_long_plan_id_is_shortened():
    assert len(record_from_json(good_upload(id="x" * 500))["id"]) == 100


# --------------------------------------------------- reader problems are upload problems


def test_a_plan_the_reader_cannot_use_is_reported_as_a_bad_upload():
    upload = good_upload(
        inner=geojson(box(0, 0, 0.0, 0.0).buffer(0))
    )  # empty after buffering
    with pytest.raises(PlanUploadError):
        plan_from_json(upload)


def test_the_error_carries_the_failure_code_for_the_failure_note():
    try:
        parse_plan_json(b"nope")
    except PlanUploadError as problem:
        assert problem.code == "BAD_INPUT" and problem.stage == "load" and problem.hint


# --------------------------------------------------- real plans


def test_the_upload_writer_and_reader_give_back_the_same_plan():
    pytest.importorskip("shapely")
    from othello.reader.load_plan import build_floor_plan_from_resplan

    record = {
        "id": 7,
        "area": 64.0,
        "wall_depth": 4.0,
        "inner": box(0, 0, 80, 80),
        "wall": MultiPolygon([box(-2, -2, 82, 82).difference(box(0, 0, 80, 80))]),
        "living": box(0, 0, 80, 80),
    }
    first = build_floor_plan_from_resplan(record)
    second = plan_from_json(as_bytes(record_to_json(record)))
    assert first.plan_id == second.plan_id == "7"
    assert (
        first.free_space_m.equals(second.free_space_m) and first.nodes == second.nodes
    )
