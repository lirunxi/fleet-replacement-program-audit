import pandas as pd

from fleet_analysis.shortlist import (
    _assign_actions,
    _pareto_fronts,
    _peer_benchmarks,
    _sensitivity,
)


def _base_row(unit_no: str) -> dict:
    return {
        "unit_no": unit_no,
        "is_operational": True,
        "in_replacement_program": False,
        "valid_lifecycle": True,
        "availability_confidence": "High",
        "lifecycle_urgent": True,
        "lifecycle_band": "At or beyond expected life",
        "availability_proxy": 0.79,
        "availability_peer_percentile": 0.20,
        "life_consumed_pct": 1.0,
        "high_priority_flag": False,
        "peer_group": "LIGHT DUTY | life 8",
        "suspected_source_displacement": False,
    }


def test_action_boundaries_and_precedence() -> None:
    rows = []
    exact_life = _base_row("exact_life")
    rows.append(exact_life)

    due_two = _base_row("due_two")
    due_two.update(
        {
            "lifecycle_band": "Due within two years",
            "life_consumed_pct": 0.75,
        }
    )
    rows.append(due_two)

    beyond_window = _base_row("beyond_window")
    beyond_window.update(
        {
            "lifecycle_urgent": False,
            "lifecycle_band": "More than two years remaining",
            "life_consumed_pct": 0.70,
        }
    )
    rows.append(beyond_window)

    threshold_availability = _base_row("availability_80")
    threshold_availability["availability_proxy"] = 0.80
    rows.append(threshold_availability)

    priority_only = _base_row("priority_only")
    priority_only.update(
        {
            "lifecycle_urgent": False,
            "lifecycle_band": "More than two years remaining",
            "availability_proxy": 0.90,
            "availability_peer_percentile": 0.50,
            "high_priority_flag": True,
        }
    )
    rows.append(priority_only)

    program = _base_row("program")
    program["in_replacement_program"] = True
    rows.append(program)

    invalid_lifecycle = _base_row("invalid_lifecycle")
    invalid_lifecycle.update(
        {
            "valid_lifecycle": False,
            "lifecycle_urgent": False,
            "lifecycle_band": "Unknown lifecycle",
        }
    )
    rows.append(invalid_lifecycle)

    low_confidence = _base_row("low_confidence")
    low_confidence["availability_confidence"] = "Low"
    rows.append(low_confidence)

    config = {
        "availability_threshold": 0.80,
        "severe_availability_threshold": 0.60,
        "peer_percentile_cutoff": 0.20,
        "peer_strict_cutoff": 0.10,
    }
    result = _assign_actions(pd.DataFrame(rows), config).set_index("unit_no")

    assert result.loc["exact_life", "primary_action"] == "Replacement condition assessment"
    assert result.loc["exact_life", "review_tier"] == "High"
    assert result.loc["due_two", "primary_action"] == "Replacement condition assessment"
    assert result.loc["due_two", "review_tier"] == "Planning"
    assert result.loc["beyond_window", "primary_action"] == "Reliability investigation"
    assert result.loc["availability_80", "primary_action"] == "Lifecycle planning"
    assert result.loc["priority_only", "primary_action"] == "No immediate action"
    assert result.loc["program", "primary_action"] == "Current program alignment"
    assert result.loc["invalid_lifecycle", "primary_action"] == "Data quality review"
    assert result.loc["low_confidence", "primary_action"] == "Data quality review"
    assert "HIGH_PRIORITY_SERVICE" in result.loc["priority_only", "reason_codes"]
    assert result.loc["exact_life", "pareto_front"] >= 1


def test_pareto_front_does_not_remove_candidates() -> None:
    frame = pd.DataFrame(
        {
            "life_consumed_pct": [1.4, 1.2, 1.1],
            "availability_proxy": [0.50, 0.60, 0.75],
            "availability_peer_percentile": [0.05, 0.10, 0.20],
        }
    )
    fronts = _pareto_fronts(frame)
    assert len(fronts) == len(frame)
    assert fronts.tolist() == [1, 2, 3]


def test_peer_fallback_and_six_scenario_sensitivity_are_complete() -> None:
    rows = []
    for index in range(6):
        row = _base_row(f"vehicle_{index}")
        row.update(
            {
                "unit_type": "LIGHT DUTY",
                "expected_life_years": 8 if index < 3 else 10,
                "availability_proxy": 0.50 + index * 0.05,
            }
        )
        rows.append(row)
    frame = pd.DataFrame(rows)
    peers = _peer_benchmarks(frame, minimum_size=4)
    assert peers["peer_group"].eq("LIGHT DUTY | all expected lives").all()
    enriched = frame.drop(
        columns=["peer_group", "availability_peer_percentile"]
    ).merge(peers, on="unit_no", how="left")
    expanded = _peer_benchmarks(
        frame,
        minimum_size=4,
        confidence_levels=("High", "Medium"),
    )
    config = {
        "availability_threshold": 0.80,
        "availability_sensitivity": [0.75, 0.80, 0.85],
        "peer_percentile_cutoff": 0.20,
        "peer_strict_cutoff": 0.10,
    }
    result = _sensitivity(enriched, expanded, config)
    assert result["scenario_id"].nunique() == 6
    assert result.groupby("unit_no").size().eq(6).all()
    assert result["stability_pct"].between(0, 1).all()
