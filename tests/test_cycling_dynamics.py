"""_build_cycling_dynamics_point: session-level power metrics with per-record fallbacks."""
from datetime import datetime, timezone

import pytest

from helpers import assert_valid_points

START = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)


def build(gf, records=(), sessions=()):
    return gf._build_cycling_dynamics_point(list(records), list(sessions), 7, "road_biking", START)


def test_no_dynamics_data_yields_no_point(gf):
    assert build(gf, records=[{"power": 200}], sessions=[{"sport": "cycling"}]) is None


def test_session_metrics(gf):
    session = {
        "avg_left_torque_effectiveness": 78, "avg_right_torque_effectiveness": 80.5,
        "avg_left_pedal_smoothness": 21, "avg_left_pco": -2, "avg_right_pco": 3,
        "avg_left_power_phase": (10.0, 200.0), "avg_right_power_phase_peak": (40.0, 150.0),
        "normalized_power": 245, "training_stress_score": 88.4, "intensity_factor": 0.82,
    }

    point = build(gf, sessions=[session])

    assert_valid_points([point])
    assert point["measurement"] == "CyclingDynamics"
    assert point["time"] == "2026-01-01T10:00:00+00:00"
    assert point["tags"]["ActivitySelector"] == "20260101T100000UTC-road_biking"
    assert point["fields"] == {
        "avg_left_torque_effectiveness": 78.0, "avg_right_torque_effectiveness": 80.5,
        "avg_left_pedal_smoothness": 21.0, "avg_left_pco": -2.0, "avg_right_pco": 3.0,
        "avg_left_power_phase_start": 10.0, "avg_left_power_phase_end": 200.0,
        "avg_right_power_phase_peak_start": 40.0, "avg_right_power_phase_peak_end": 150.0,
        "normalized_power": 245.0, "training_stress_score": 88.4, "intensity_factor": 0.82,
        "ActivityName": "road_biking", "Activity_ID": 7,
    }


@pytest.mark.parametrize("raw, expected", [
    (0x8000 | 4800, 52.0),  # right-side flag set: 48.00% right -> 52% left
    (0x8000 | 5000, 50.0),
    (47, 47.0),  # no flag: stored as-is
    (0, None),
    ("not-a-number", None),
])
def test_left_right_balance(gf, raw, expected):
    point = build(gf, sessions=[{"left_right_balance": raw, "normalized_power": 200}])

    assert point["fields"].get("left_right_balance") == expected


@pytest.mark.parametrize("phase, expected", [
    ((0, 180.0), {"avg_left_power_phase_end": 180.0}),  # zero angles are treated as missing
    ((12.5,), {"avg_left_power_phase_start": 12.5}),  # too short for an end angle
    (15.0, {"avg_left_power_phase_start": 15.0, "avg_left_power_phase_end": 15.0}),  # scalar rather than tuple
])
def test_session_power_phase_shapes(gf, phase, expected):
    point = build(gf, sessions=[{"avg_left_power_phase": phase}])

    assert {k: v for k, v in point["fields"].items() if k.startswith("avg_left_power_phase")} == expected


def test_record_fallback_averages_non_zero_values(gf):
    records = [
        {"left_torque_effectiveness": 70, "left_pco": 0, "right_power_phase": (20.0, 190.0)},
        {"left_torque_effectiveness": 80, "left_pco": 4, "right_power_phase": (30.0, None)},
        {"left_torque_effectiveness": 0, "left_pco": None, "right_power_phase": None},
        {"right_power_phase": (40.0,)},  # too short for an end angle: only counts towards the start
        {"right_power_phase": ()},  # empty: ignored
        {},
    ]

    point = build(gf, records=records)

    assert point["fields"]["avg_left_torque_effectiveness"] == 75.0
    assert point["fields"]["avg_left_pco"] == 4.0
    assert point["fields"]["avg_right_power_phase_start"] == 30.0
    assert point["fields"]["avg_right_power_phase_end"] == 190.0


def test_session_values_take_precedence_over_records(gf):
    point = build(gf, records=[{"left_torque_effectiveness": 50}], sessions=[{"avg_left_torque_effectiveness": 90}])

    assert point["fields"]["avg_left_torque_effectiveness"] == 90.0
