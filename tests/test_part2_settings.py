"""T10: settings loader (file, environment, flags)."""

import argparse
import json

import pytest

from othello.io.settings import (
    ConfigError,
    add_config_arguments,
    check_config_values,
    config_from_args,
    dump_config,
    load_config,
    non_default_settings,
    read_settings_file,
    write_config,
)
from othello.models.config import SolverConfig


def parse(argv, env=None):
    parser = argparse.ArgumentParser()
    add_config_arguments(parser)
    return config_from_args(parser.parse_args(argv), env=env or {})


def write_json(tmp_path, data, name="settings.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# -------------------------------------------------------------- defaults


def test_no_layers_gives_the_defaults():
    assert load_config() == SolverConfig()
    assert non_default_settings(load_config()) == {}


def test_default_settings_pass_every_check():
    assert check_config_values(SolverConfig()) == []


def test_real_environment_is_ignored_unless_passed_in(monkeypatch):
    monkeypatch.setenv("OTHELLO_TIME_BUDGET_S", "5")
    assert load_config().time_budget_s == 60.0  # env=None means "do not read it"


# ----------------------------------------------------------------- files


def test_json_file_overrides_defaults(tmp_path):
    path = write_json(tmp_path, {"time_budget_s": 30, "weights": {"crossing": 7}, "cable_transit_policy": "allow"})
    cfg = load_config(file=path)
    assert cfg.time_budget_s == 30
    assert cfg.weights.crossing == 7 and cfg.weights.length == 1.0  # untouched siblings stay
    assert cfg.cable_transit_policy == "allow"


def test_toml_file_works(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('time_budget_s = 12.5\n[weights]\nbend = 2.0\n[limits]\nbeam_width = 5\n', encoding="utf-8")
    cfg = load_config(file=path)
    assert (cfg.time_budget_s, cfg.weights.bend, cfg.limits.beam_width) == (12.5, 2.0, 5)


def test_missing_and_broken_files_are_clear_errors(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(file=tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(file=bad)
    listy = write_json(tmp_path, [1, 2], "list.json")
    with pytest.raises(ConfigError, match="object at the top level"):
        read_settings_file(listy)


def test_env_can_point_at_a_settings_file(tmp_path):
    path = write_json(tmp_path, {"time_budget_s": 22})
    assert load_config(env={"OTHELLO_CONFIG": str(path)}).time_budget_s == 22


def test_explicit_file_beats_the_env_file(tmp_path):
    a = write_json(tmp_path, {"time_budget_s": 11}, "a.json")
    b = write_json(tmp_path, {"time_budget_s": 22}, "b.json")
    assert load_config(file=a, env={"OTHELLO_CONFIG": str(b)}).time_budget_s == 11


# --------------------------------------------------------- environment


def test_environment_variables_override_with_text_converted():
    env = {
        "OTHELLO_TIME_BUDGET_S": "30",
        "OTHELLO_WEIGHTS__CROSSING": "6",
        "OTHELLO_ZONE_LINK_THROUGH_EXCLUDED": "true",
        "OTHELLO_LIMITS__BEAM_WIDTH": "8",
        "PATH": "/ignored",
        "HOME": "/ignored",
    }
    cfg = load_config(env=env)
    assert cfg.time_budget_s == 30.0
    assert cfg.weights.crossing == 6.0
    assert cfg.zone_link_through_excluded is True
    assert cfg.limits.beam_width == 8


# ---------------------------------------------------------------- flags


def test_friendly_flags():
    cfg = parse(["--time-budget", "45", "--transit-policy", "forbid", "--weights", "1", "6", "2", "0.25"])
    assert cfg.time_budget_s == 45
    assert cfg.cable_transit_policy == "forbid"
    assert (cfg.weights.length, cfg.weights.crossing, cfg.weights.overlap, cfg.weights.bend) == (1, 6, 2, 0.25)


def test_set_flag_reaches_any_setting_and_can_repeat():
    cfg = parse(["--set", "weights.crossing=9", "--set", "max_nodes_per_hub=4", "--set", "limits.routes_per_leg=3"])
    assert (cfg.weights.crossing, cfg.max_nodes_per_hub, cfg.limits.routes_per_leg) == (9, 4, 3)


def test_bad_transit_policy_flag_is_refused_by_the_parser():
    with pytest.raises(SystemExit):
        parse(["--transit-policy", "sometimes"])


def test_set_flag_needs_key_equals_value():
    with pytest.raises(ConfigError, match="KEY=VALUE"):
        parse(["--set", "weights.crossing"])


def test_config_flag_reads_a_file(tmp_path):
    path = write_json(tmp_path, {"max_area_per_hub_m2": 90})
    assert parse(["--config", str(path)]).max_area_per_hub_m2 == 90


# ------------------------------------------------------------ priority


def test_priority_file_then_env_then_friendly_then_set(tmp_path):
    path = write_json(tmp_path, {"time_budget_s": 10, "weights": {"crossing": 1, "bend": 1}})
    env = {"OTHELLO_TIME_BUDGET_S": "20", "OTHELLO_WEIGHTS__CROSSING": "2"}
    # file and env only
    cfg = load_config(file=path, env=env)
    assert cfg.time_budget_s == 20 and cfg.weights.crossing == 2 and cfg.weights.bend == 1
    # friendly flag beats env
    cfg = parse(["--config", str(path), "--time-budget", "30"], env=env)
    assert cfg.time_budget_s == 30 and cfg.weights.crossing == 2
    # --set beats everything
    cfg = parse(["--config", str(path), "--time-budget", "30", "--set", "time_budget_s=40"], env=env)
    assert cfg.time_budget_s == 40


# ------------------------------------------------------------- mistakes


def test_unknown_setting_names_are_errors_with_a_hint(tmp_path):
    with pytest.raises(ConfigError, match=r"unknown setting 'time_budget' \(did you mean 'time_budget_s'\?\)"):
        load_config(file=write_json(tmp_path, {"time_budget": 5}))
    with pytest.raises(ConfigError, match=r"unknown setting 'weights.crossng' \(did you mean 'weights.crossing'\?\)"):
        load_config(overrides=["weights.crossng=3"])
    with pytest.raises(ConfigError, match="unknown setting 'timebudget_s'"):
        load_config(env={"OTHELLO_TIMEBUDGET_S": "5"})


def test_a_group_setting_cannot_be_a_plain_value():
    with pytest.raises(ConfigError, match="is a group"):
        load_config(env={"OTHELLO_WEIGHTS": "5"})


def test_text_that_is_not_a_number_is_an_error():
    with pytest.raises(ConfigError, match="time_budget_s"):
        load_config(overrides=["time_budget_s=soon"])


@pytest.mark.parametrize(
    ("setting", "fragment"),
    [
        ("cable_transit_policy=sometimes", "cable_transit_policy must be one of"),
        ("weights.crossing=-1", "weights.crossing cannot be negative"),
        ("time_budget_s=0", "time_budget_s must be above 0"),
        ("final_check_reserve_fraction=1", "final_check_reserve_fraction"),
        ("max_nodes_per_hub=0", "max_nodes_per_hub must be at least 1"),
        ("tolerance_m=0", "tolerance_m must be above 0"),
        ("hub_wall_clearance_m=-0.5", "hub_wall_clearance_m cannot be negative"),
        ("grid_step_m=0", "grid_step_m must be above 0"),
        ("output_precision_decimals=20", "output_precision_decimals"),
        ("limits.beam_width=0", "limits.beam_width must be at least 1"),
        ("parallel_lane_offset_m=0.0005", "parallel_lane_offset_m"),
        ("tiebreak_gap_definition=whatever", "tiebreak_gap_definition"),
    ],
)
def test_impossible_values_are_refused_with_a_reason(setting, fragment):
    with pytest.raises(ConfigError, match=fragment):
        load_config(overrides=[setting])


def test_all_problems_are_listed_together():
    with pytest.raises(ConfigError) as caught:
        load_config(overrides=["time_budget_s=0", "weights.bend=-2"])
    text = str(caught.value)
    assert "time_budget_s must be above 0" in text and "weights.bend cannot be negative" in text


def test_lane_offset_zero_or_clear_of_the_tolerance_is_fine():
    assert load_config(overrides=["parallel_lane_offset_m=0"]).parallel_lane_offset_m == 0
    assert load_config(overrides=["parallel_lane_offset_m=0.05"]).parallel_lane_offset_m == 0.05


# ------------------------------------------------------------ reporting


def test_non_default_settings_lists_only_the_changes():
    cfg = load_config(overrides=["weights.crossing=9", "time_budget_s=15", "cable_transit_policy=allow"])
    assert non_default_settings(cfg) == {
        "weights.crossing": 9.0,
        "time_budget_s": 15.0,
        "cable_transit_policy": "allow",
    }


def test_saved_settings_load_back_to_the_same_thing(tmp_path):
    cfg = load_config(overrides=["weights.crossing=9", "limits.beam_width=7", "zone_link_through_excluded=true"])
    path = tmp_path / "used" / "settings.json"
    write_config(cfg, path)
    assert load_config(file=path) == cfg
    assert json.loads(path.read_text())["weights"]["crossing"] == 9.0


def test_dump_has_every_setting():
    assert set(dump_config(SolverConfig())) == set(SolverConfig.model_fields)


def test_loading_does_not_change_the_defaults():
    load_config(overrides=["weights.crossing=99"])
    assert SolverConfig().weights.crossing == 5.0
