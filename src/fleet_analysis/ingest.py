"""Validated, text-preserving ingestion into DuckDB."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd

UNIT_FILE = "RAW_Fleet_Units.csv"
USAGE_FILE = "RAW_Fleet_Usage.csv"

UNIT_COLUMNS = [
    "UNIT_NO",
    "DIVISION",
    "YEAR",
    "MAKE",
    "MODEL",
    "CATEGORY",
    "CATEGORY_DESC",
    "CATEGORY_CLASS",
    "CATEGORY_GROUP",
    "CATEGORY_GROUP_DESC",
    "UNIT_TYPE",
    "FUEL_PRODUCT",
    "AGE",
    "EXPECTED_LIFE(YR)",
    "IN_SERVICE_DATE",
    "CURRENT_STATUS_DESCRIPTION",
    "HIGH_PRIORITY",
    "OWNING_COST_CENTER",
    "USING_COST_CENTER",
    "MAINTENENACE_LOCATION_NAME",
    "PARK_LOCATION",
    "PARK_LOCATION_NAME",
    "BILLING_CODE",
    "MAINTENANCE CLASSIFICATION CODE",
    "TECH_SPEC",
    "TECH_SPEC_DESC",
]

USAGE_COLUMNS = [
    "UNIT_NO",
    "LAST_FUEL_DATE",
    "LAST_WORKORDER_OPEN_DATE",
    "M5_LIFE_KM_USAGE(KM)",
    "M5_LIFE_HRS_USAGE(Hrs)",
    "M5_YTD_KM_USAGE(KM)",
    "M5_YTD_HRS_USAGE(Hrs)",
    "AVAILABLE HOURS (Hrs)",
    "DOWNTIME(Hrs)",
    "EXPECT_USAGE(KM)",
    "EXPECT_USAGE(Hrs)",
    "VEU",
]


def _snake(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_hash(row: pd.Series, columns: list[str]) -> str:
    values = [str(row[column]) for column in columns]
    payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_validated_csv(path: Path, expected_columns: list[str]) -> pd.DataFrame:
    """Read a CSV as text and validate the complete, ordered schema."""
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{path.name} is not valid UTF-8") from exc

    frame = pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
        encoding="utf-8",
    )
    actual = frame.columns.tolist()
    if actual != expected_columns:
        missing = sorted(set(expected_columns) - set(actual))
        extra = sorted(set(actual) - set(expected_columns))
        raise ValueError(
            f"Unexpected schema for {path.name}. Missing={missing}; extra={extra}; "
            f"column order matches={actual == expected_columns}"
        )
    if frame.empty:
        raise ValueError(f"{path.name} contains no data rows")
    return frame


def add_provenance(frame: pd.DataFrame, path: Path, ingested_at: str) -> pd.DataFrame:
    """Normalize column names and add immutable lineage and duplicate metadata."""
    original_columns = frame.columns.tolist()
    enriched = frame.copy()
    enriched["_source_file"] = path.name
    enriched["_source_row_number"] = range(2, len(enriched) + 2)
    enriched["_source_sha256"] = _sha256(path)
    enriched["_ingested_at"] = ingested_at
    enriched["_raw_row_hash"] = enriched.apply(
        _row_hash, axis=1, columns=original_columns
    )

    unit_series = enriched["UNIT_NO"].astype(str).str.strip()
    duplicate_count = unit_series.map(unit_series.value_counts())
    distinct_hash_count = enriched.groupby(unit_series)["_raw_row_hash"].transform("nunique")
    enriched["_duplicate_count"] = duplicate_count.astype(int)
    enriched["_duplicate_type"] = "unique"
    enriched.loc[(duplicate_count > 1) & (distinct_hash_count == 1), "_duplicate_type"] = (
        "exact_duplicate"
    )
    enriched.loc[(duplicate_count > 1) & (distinct_hash_count > 1), "_duplicate_type"] = (
        "conflicting_duplicate"
    )
    enriched.rename(columns={column: _snake(column) for column in original_columns}, inplace=True)
    return enriched


def ingest(raw_dir: Path, database_path: Path) -> dict[str, int | str]:
    """Validate both source files and populate the raw DuckDB schema."""
    expected_files = {UNIT_FILE, USAGE_FILE}
    available_files = {path.name for path in raw_dir.glob("*.csv")}
    if not expected_files.issubset(available_files):
        missing = sorted(expected_files - available_files)
        raise FileNotFoundError(f"Missing required raw files: {missing}")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    if database_path.exists():
        database_path.unlink()

    ingested_at = datetime.now(UTC).isoformat()
    run_id = str(uuid.uuid4())
    units_path = raw_dir / UNIT_FILE
    usage_path = raw_dir / USAGE_FILE
    units = add_provenance(
        read_validated_csv(units_path, UNIT_COLUMNS), units_path, ingested_at
    )
    usage = add_provenance(
        read_validated_csv(usage_path, USAGE_COLUMNS), usage_path, ingested_at
    )

    with duckdb.connect(str(database_path)) as connection:
        connection.execute("CREATE SCHEMA raw")
        connection.register("units_frame", units)
        connection.register("usage_frame", usage)
        connection.execute("CREATE TABLE raw.fleet_units AS SELECT * FROM units_frame")
        connection.execute("CREATE TABLE raw.fleet_usage AS SELECT * FROM usage_frame")
        connection.execute(
            """
            CREATE TABLE raw.ingestion_runs (
                run_id VARCHAR,
                source_file VARCHAR,
                source_sha256 VARCHAR,
                ingested_at TIMESTAMPTZ,
                row_count BIGINT,
                column_count BIGINT,
                unique_unit_count BIGINT,
                exact_duplicate_rows BIGINT,
                conflicting_duplicate_rows BIGINT
            )
            """
        )
        for path, frame, original_count in (
            (units_path, units, len(UNIT_COLUMNS)),
            (usage_path, usage, len(USAGE_COLUMNS)),
        ):
            connection.execute(
                "INSERT INTO raw.ingestion_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    run_id,
                    path.name,
                    _sha256(path),
                    ingested_at,
                    len(frame),
                    original_count,
                    int(frame["unit_no"].str.strip().nunique()),
                    int((frame["_duplicate_type"] == "exact_duplicate").sum()),
                    int((frame["_duplicate_type"] == "conflicting_duplicate").sum()),
                ],
            )

    return {
        "run_id": run_id,
        "units_rows": len(units),
        "usage_rows": len(usage),
        "units_unique": int(units["unit_no"].str.strip().nunique()),
        "usage_unique": int(usage["unit_no"].str.strip().nunique()),
    }
