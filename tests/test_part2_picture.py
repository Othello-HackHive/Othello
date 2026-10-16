"""T14: the debug picture (needs matplotlib; skipped without it)."""

import sys

import pytest

pytest.importorskip("matplotlib")

import matplotlib.image as mpimg
import numpy as np
from fixtures import designs
from fixtures.plans import slide5_plan, tiny_plan

from othello.checker import check_design
from othello.io.errors import OutputWriteError
from othello.io.overlay import build_overlay
from othello.io.picture import (
    HUB_COLORS,
    MissingDependencyError,
    draw_overlay,
    hub_color,
    picture_bytes,
    write_picture,
    write_plan_picture,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(scope="module")
def plan():
    return slide5_plan()


def overlay_for(plan, design):
    return build_overlay(plan, design, check_design(plan, design))


def red_pixels(path) -> int:
    """How many pixels in the DRAWING AREA are the exact 'problem red'.

    The legend (right), title (top) and footer (bottom) are left out: the legend
    always shows sample red marks.
    """
    image = mpimg.imread(path)[:, :, :3] * 255
    height, width = image.shape[:2]
    area = image[int(0.10 * height) : int(0.86 * height), int(0.05 * width) : int(0.80 * width)]
    close = (abs(area[:, :, 0] - 230) < 12) & (area[:, :, 1] < 12) & (area[:, :, 2] < 12)
    return int(close.sum())


def non_white_share(path) -> float:
    image = mpimg.imread(path)[:, :, :3]
    return float((image.min(axis=2) < 0.97).mean())


# --------------------------------------------------------------- the file


def test_a_good_plan_gives_a_real_png(plan, tmp_path):
    path = tmp_path / "good.png"
    write_plan_picture(plan, designs.slide5_design(), check_design(plan, designs.slide5_design()), path)
    data = path.read_bytes()
    assert data.startswith(PNG_MAGIC) and len(data) > 20_000
    height, width = mpimg.imread(path).shape[:2]
    assert width == 1300 and 600 < height < 1000
    assert non_white_share(path) > 0.2  # not a blank page


def test_the_picture_can_be_drawn_from_the_overlay_alone(plan, tmp_path):
    path = write_picture(overlay_for(plan, designs.slide5_design()), tmp_path / "from_overlay.png")
    assert path.read_bytes().startswith(PNG_MAGIC)


def test_a_clean_plan_has_nothing_in_problem_red_but_a_broken_one_does(plan, tmp_path):
    write_plan_picture(plan, designs.slide5_design(), check_design(plan, designs.slide5_design()), tmp_path / "ok.png")
    bad = designs.broken_cable_through_wall()
    write_plan_picture(plan, bad, check_design(plan, bad), tmp_path / "bad.png")
    assert red_pixels(tmp_path / "ok.png") == 0
    assert red_pixels(tmp_path / "bad.png") > 100  # the red circle at the wall hit


def test_crossings_and_overlaps_are_drawn_in_red_even_when_valid(plan, tmp_path):
    for name, build in (("cross", designs.slide5_with_crossing), ("overlap", designs.slide5_with_overlap)):
        design = build()
        write_plan_picture(plan, design, check_design(plan, design), tmp_path / f"{name}.png")
        assert red_pixels(tmp_path / f"{name}.png") > 100


def test_no_design_still_draws_the_house(plan, tmp_path):
    path = tmp_path / "none.png"
    write_picture(build_overlay(plan, None, None, notes=["timeout"]), path)
    assert path.read_bytes().startswith(PNG_MAGIC)
    assert non_white_share(path) > 0.1


def test_the_tiny_house_draws(tmp_path):
    plan = tiny_plan()
    design = designs.tiny_design()
    path = write_plan_picture(plan, design, check_design(plan, design), tmp_path / "tiny.png")
    assert path.read_bytes().startswith(PNG_MAGIC)


def test_showing_bends_changes_the_picture(plan):
    overlay = overlay_for(plan, designs.slide5_design())
    assert picture_bytes(overlay) != picture_bytes(overlay, show_bends=True)


def test_the_same_data_gives_the_same_picture(plan):
    overlay = overlay_for(plan, designs.slide5_design())
    assert picture_bytes(overlay) == picture_bytes(overlay)


def test_dpi_controls_the_size(plan):
    overlay = overlay_for(plan, designs.slide5_design())
    assert len(picture_bytes(overlay, dpi=60)) < len(picture_bytes(overlay, dpi=130))


# ------------------------------------------------------------ the details


def test_title_says_valid_or_invalid(plan):
    good = draw_overlay(overlay_for(plan, designs.slide5_design()))
    bad = draw_overlay(overlay_for(plan, designs.broken_cable_through_wall()))
    none = draw_overlay(build_overlay(plan, None, None))
    assert "VALID" in good.texts[0].get_text() and "INVALID" not in good.texts[0].get_text()
    assert "INVALID" in bad.texts[0].get_text()
    assert "NO DESIGN FOUND" in none.texts[0].get_text()


def test_footer_names_the_failed_rules(plan):
    bad = draw_overlay(overlay_for(plan, designs.broken_missing_node()))
    assert any("FAILED: RING CLOSURE" in t.get_text() for t in bad.texts)
    good = draw_overlay(overlay_for(plan, designs.slide5_design()))
    assert any("all 9 rules pass" in t.get_text() for t in good.texts)


def test_hub_colours_repeat_after_six_and_unowned_is_grey():
    assert hub_color(0) == HUB_COLORS[0] and hub_color(6) == HUB_COLORS[0] and hub_color(7) == HUB_COLORS[1]
    assert hub_color(None) not in HUB_COLORS


# ------------------------------------------------------------------ safety


def test_the_picture_is_written_safely_with_no_temp_files(plan, tmp_path):
    path = tmp_path / "deep" / "er" / "p.png"
    write_picture(overlay_for(plan, designs.slide5_design()), path)
    assert [p.name for p in path.parent.iterdir()] == ["p.png"]


def test_an_unwritable_place_gives_an_output_error(plan, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(OutputWriteError):
        write_picture(overlay_for(plan, designs.slide5_design()), blocker / "p.png")


def test_a_missing_matplotlib_gives_a_clear_error(plan, monkeypatch):
    monkeypatch.setitem(sys.modules, "matplotlib.figure", None)
    with pytest.raises(MissingDependencyError, match="pip install matplotlib"):
        draw_overlay(overlay_for(plan, designs.slide5_design()))


def test_pixels_are_really_coloured_by_hub(plan, tmp_path):
    path = tmp_path / "c.png"
    write_picture(overlay_for(plan, designs.slide5_design()), path)
    image = mpimg.imread(path)[:, :, :3] * 255
    blue = np.array([0x1D, 0x4E, 0xD8])  # H1's colour
    orange = np.array([0xD9, 0x77, 0x06])  # H2's colour
    for colour in (blue, orange):
        assert int((np.abs(image - colour).sum(axis=2) < 12).sum()) > 200
