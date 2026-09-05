"""Transparent peer benchmarking, action queues, Pareto ordering, and sensitivity."""

from __future__ import annotations

from itertools import product
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


def _peer_benchmarks(
    frame: pd.DataFrame,
    minimum_size: int,
    confidence_levels: tuple[str, ...] = ("High",),
) -> pd.DataFrame:
    eligible = frame[
        frame["is_operational"].fillna(False)
        & frame["availability_confidence"].isin(confidence_levels)
        & frame["availability_proxy"].notna()
        & frame["unit_type"].notna()
    ].copy()
    eligible["life_key"] = eligible["expected_life_years"].map(
        lambda value: f"{value:g}" if pd.notna(value) else "UNKNOWN"
    )
    eligible["primary_group"] = eligible["unit_type"] + " | life " + eligible["life_key"]
    primary_sizes = eligible.groupby("primary_group")["unit_no"].transform("size")
    fallback_sizes = eligible.groupby("unit_type")["unit_no"].transform("size")
    eligible["peer_group"] = np.where(
        primary_sizes >= minimum_size,
        eligible["primary_group"],
        np.where(
            fallback_sizes >= minimum_size,
            eligible["unit_type"] + " | all expected lives",
            None,
        ),
    )
    eligible["peer_group_size"] = eligible.groupby("peer_group", dropna=True)[
        "unit_no"
    ].transform("size")
    eligible["peer_median_availability"] = eligible.groupby(
        "peer_group", dropna=True
    )["availability_proxy"].transform("median")
    eligible["availability_peer_percentile"] = eligible.groupby(
        "peer_group", dropna=True
    )["availability_proxy"].rank(method="average", pct=True, ascending=True)
    eligible["availability_vs_peer_median"] = (
        eligible["availability_proxy"] - eligible["peer_median_availability"]
    )
    return eligible[
        [
            "unit_no",
            "peer_group",
            "peer_group_size",
            "peer_median_availability",
            "availability_vs_peer_median",
            "availability_peer_percentile",
        ]
    ]


def _pareto_fronts(frame: pd.DataFrame) -> pd.Series:
    """Assign non-dominated fronts; smaller front numbers have greater urgency."""
    if frame.empty:
        return pd.Series(dtype="Int64")
    objectives = np.column_stack(
        [
            frame["life_consumed_pct"].to_numpy(float),
            -frame["availability_proxy"].to_numpy(float),
            -frame["availability_peer_percentile"].to_numpy(float),
        ]
    )
    remaining = list(range(len(frame)))
    front_by_position: dict[int, int] = {}
    front_number = 1
    while remaining:
        current_front: list[int] = []
        for i in remaining:
            dominated = any(
                j != i
                and np.all(objectives[j] >= objectives[i])
                and np.any(objectives[j] > objectives[i])
                for j in remaining
            )
            if not dominated:
                current_front.append(i)
        for i in current_front:
            front_by_position[i] = front_number
        remaining = [i for i in remaining if i not in current_front]
        front_number += 1
    return pd.Series(
        [front_by_position[i] for i in range(len(frame))],
        index=frame.index,
        dtype="Int64",
    )


def _reason_codes(row: pd.Series, config: dict) -> str:
    reasons: list[str] = []
    if bool(row["in_replacement_program"]):
        reasons.append("CURRENT_REPLACEMENT_PROGRAM")
    else:
        reasons.append("OUTSIDE_REPLACEMENT_PROGRAM")
    if row["lifecycle_band"] == "At or beyond expected life":
        reasons.append("AT_OR_BEYOND_EXPECTED_LIFE")
    if row["lifecycle_band"] == "Due within two years":
        reasons.append("DUE_WITHIN_TWO_YEARS")
    if pd.notna(row["availability_proxy"]) and (
        row["availability_proxy"] < config["availability_threshold"]
    ):
        reasons.append("LOW_AVAILABILITY_PROXY")
    if pd.notna(row["availability_proxy"]) and (
        row["availability_proxy"] < config["severe_availability_threshold"]
    ):
        reasons.append("SEVERE_AVAILABILITY_PROXY")
    if pd.notna(row["availability_peer_percentile"]) and (
        row["availability_peer_percentile"] <= config["peer_percentile_cutoff"]
    ):
        reasons.append("BOTTOM_PEER_QUINTILE")
    if pd.notna(row["availability_peer_percentile"]) and (
        row["availability_peer_percentile"] <= config["peer_strict_cutoff"]
    ):
        reasons.append("BOTTOM_PEER_DECILE")
    if bool(row["high_priority_flag"]):
        reasons.append("HIGH_PRIORITY_SERVICE")
    if pd.isna(row["peer_group"]):
        reasons.append("INSUFFICIENT_PEER_GROUP")
    if not bool(row["valid_lifecycle"]):
        reasons.append("INVALID_LIFECYCLE_DATA")
    if row["availability_confidence"] == "Low":
        reasons.append("INVALID_AVAILABILITY_DATA")
    if bool(row["suspected_source_displacement"]):
        reasons.append("SUSPECTED_SOURCE_DISPLACEMENT")
    return "|".join(reasons)


def _assign_actions(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    result = frame.copy()
    operational = result["is_operational"].fillna(False)
    outside_program = ~result["in_replacement_program"].fillna(False)
    reliable_inputs = result["valid_lifecycle"].fillna(False) & result[
        "availability_confidence"
    ].eq("High")
    lifecycle_urgent = result["lifecycle_urgent"].fillna(False)
    low_availability = result["availability_proxy"].lt(config["availability_threshold"])
    peer_low = result["availability_peer_percentile"].le(
        config["peer_percentile_cutoff"]
    )

    data_review = operational & ~reliable_inputs
    current_program = operational & result["in_replacement_program"].fillna(False)
    replacement = (
        operational
        & outside_program
        & reliable_inputs
        & lifecycle_urgent
        & low_availability
        & peer_low
    )
    reliability = (
        operational
        & outside_program
        & reliable_inputs
        & low_availability
        & ~replacement
    )
    lifecycle_planning = (
        operational
        & outside_program
        & reliable_inputs
        & lifecycle_urgent
        & ~replacement
        & ~reliability
    )

    result["primary_action"] = np.select(
        [data_review, current_program, replacement, reliability, lifecycle_planning],
        [
            "Data quality review",
            "Current program alignment",
            "Replacement condition assessment",
            "Reliability investigation",
            "Lifecycle planning",
        ],
        default="No immediate action",
    )

    overdue = result["lifecycle_band"].eq("At or beyond expected life")
    due_soon = result["lifecycle_band"].eq("Due within two years")
    urgent_signal = (
        result["availability_proxy"].lt(config["severe_availability_threshold"])
        | result["availability_peer_percentile"].le(config["peer_strict_cutoff"])
        | result["high_priority_flag"].fillna(False)
    )
    result["review_tier"] = "Monitor"
    result.loc[data_review, "review_tier"] = "Data review"
    result.loc[lifecycle_planning, "review_tier"] = "Planning"
    result.loc[reliability, "review_tier"] = "Planning"
    result.loc[reliability & urgent_signal, "review_tier"] = "High"
    result.loc[current_program & due_soon, "review_tier"] = "Planning"
    result.loc[current_program & overdue, "review_tier"] = "High"
    result.loc[current_program & overdue & urgent_signal, "review_tier"] = "Urgent"
    result.loc[replacement & due_soon, "review_tier"] = "Planning"
    result.loc[replacement & overdue, "review_tier"] = "High"
    result.loc[replacement & overdue & urgent_signal, "review_tier"] = "Urgent"
    result.loc[data_review, "review_tier"] = "Data review"
    result["reason_codes"] = result.apply(_reason_codes, axis=1, config=config)
    result["pareto_front"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    candidate_rows = result["primary_action"].eq("Replacement condition assessment")
    result.loc[candidate_rows, "pareto_front"] = _pareto_fronts(result[candidate_rows])
    return result


def _sensitivity(frame: pd.DataFrame, expanded_peers: pd.DataFrame, config: dict) -> pd.DataFrame:
    expanded = expanded_peers.rename(
        columns={
            "peer_group": "expanded_peer_group",
            "availability_peer_percentile": "expanded_peer_percentile",
        }
    )[["unit_no", "expanded_peer_group", "expanded_peer_percentile"]]
    work = frame.merge(expanded, on="unit_no", how="left")
    base = (
        work["is_operational"].fillna(False)
        & ~work["in_replacement_program"].fillna(False)
        & work["valid_lifecycle"].fillna(False)
        & work["availability_confidence"].eq("High")
        & work["lifecycle_urgent"].fillna(False)
    )
    records: list[pd.DataFrame] = []
    for availability_threshold, peer_cutoff in product(
        config["availability_sensitivity"],
        [config["peer_strict_cutoff"], config["peer_percentile_cutoff"]],
    ):
        selected = (
            base
            & work["availability_proxy"].lt(availability_threshold)
            & work["availability_peer_percentile"].le(peer_cutoff)
        )
        scenario = work[["unit_no"]].copy()
        scenario["scenario_id"] = (
            f"availability_lt_{availability_threshold:.2f}"
            f"__peer_le_{peer_cutoff:.2f}"
        )
        scenario["availability_threshold"] = availability_threshold
        scenario["peer_percentile_cutoff"] = peer_cutoff
        scenario["selected"] = selected
        records.append(scenario)
    long_result = pd.concat(records, ignore_index=True)
    summary = (
        long_result.groupby("unit_no", as_index=False)["selected"]
        .sum()
        .rename(columns={"selected": "scenarios_selected"})
    )
    summary["stability_pct"] = summary["scenarios_selected"] / len(records)
    expanded_selected = (
        work["is_operational"].fillna(False)
        & ~work["in_replacement_program"].fillna(False)
        & work["valid_lifecycle"].fillna(False)
        & work["availability_confidence"].isin(["High", "Medium"])
        & work["lifecycle_urgent"].fillna(False)
        & work["availability_proxy"].lt(config["availability_threshold"])
        & work["expanded_peer_percentile"].le(config["peer_percentile_cutoff"])
    )
    expanded_summary = pd.DataFrame(
        {
            "unit_no": work["unit_no"],
            "expanded_confidence_selected": expanded_selected,
        }
    )
    return long_result.merge(summary, on="unit_no", how="left").merge(
        expanded_summary, on="unit_no", how="left"
    )


def _summary_by(
    frame: pd.DataFrame,
    field: str,
    display_field: str | None = None,
) -> pd.DataFrame:
    operational = frame[frame["is_operational"].fillna(False)].copy()
    operational[field] = operational[field].fillna("Unknown")
    if display_field is not None:
        operational[display_field] = operational[display_field].fillna("Unknown")
    operational["eligible"] = (
        ~operational["in_replacement_program"].fillna(False)
        & operational["valid_lifecycle"].fillna(False)
        & operational["availability_confidence"].eq("High")
        & operational["availability_peer_percentile"].notna()
    )
    operational["candidate"] = operational["primary_action"].eq(
        "Replacement condition assessment"
    )
    operational["actionable_outside_program"] = (
        ~operational["in_replacement_program"].fillna(False)
        & ~operational["primary_action"].isin(["No immediate action"])
    )
    summary = (
        operational.groupby(field, dropna=False)
        .agg(
            operational_vehicles=("unit_no", "size"),
            eligible_vehicles=("eligible", "sum"),
            replacement_candidates=("candidate", "sum"),
            actionable_outside_program=("actionable_outside_program", "sum"),
            low_availability_vehicles=("low_availability", "sum"),
        )
        .reset_index()
        .assign(
            candidate_rate=lambda data: data["replacement_candidates"]
            / data["eligible_vehicles"].replace(0, np.nan)
        )
    )
    if display_field is not None:
        labels = (
            operational[[field, display_field]]
            .dropna(subset=[display_field])
            .drop_duplicates(subset=[field])
        )
        summary = summary.merge(labels, on=field, how="left")
        ordered = [field, display_field] + [
            column for column in summary.columns if column not in {field, display_field}
        ]
        summary = summary[ordered]
    return summary


def build_shortlist(database_path: Path, config: dict) -> dict[str, int]:
    """Build the review queues and supporting peer/sensitivity marts."""
    with duckdb.connect(str(database_path)) as connection:
        features = connection.execute(
            "SELECT * FROM analytics.vehicle_review_features"
        ).fetchdf()
        peers = _peer_benchmarks(features, config["peer_minimum_size"])
        expanded_peers = _peer_benchmarks(
            features, config["peer_minimum_size"], ("High", "Medium")
        )
        work = features.merge(peers, on="unit_no", how="left")
        actions = _assign_actions(work, config)
        sensitivity = _sensitivity(work, expanded_peers, config)
        stability = sensitivity[
            ["unit_no", "scenarios_selected", "stability_pct", "expanded_confidence_selected"]
        ].drop_duplicates("unit_no")
        actions = actions.merge(stability, on="unit_no", how="left")
        actions["scenarios_selected"] = actions["scenarios_selected"].fillna(0).astype(int)
        actions["stability_pct"] = actions["stability_pct"].fillna(0.0)
        actions["expanded_confidence_selected"] = actions[
            "expanded_confidence_selected"
        ].fillna(False)
        division_summary = _summary_by(actions, "division_key", "division")
        location_summary = _summary_by(actions, "maintenance_location")

        for name, data in (
            ("peer_benchmarks", peers),
            ("shortlist_sensitivity", sensitivity),
            ("vehicle_review_shortlist", actions),
            ("division_summary", division_summary),
            ("location_summary", location_summary),
        ):
            connection.register(f"{name}_frame", data)
            connection.execute(
                f"CREATE OR REPLACE TABLE analytics.{name} AS "
                f"SELECT * FROM {name}_frame"
            )

    counts = actions["primary_action"].value_counts()
    return {
        "replacement_candidates": int(
            counts.get("Replacement condition assessment", 0)
        ),
        "reliability_investigation": int(counts.get("Reliability investigation", 0)),
        "lifecycle_planning": int(counts.get("Lifecycle planning", 0)),
        "data_quality_review": int(counts.get("Data quality review", 0)),
    }
