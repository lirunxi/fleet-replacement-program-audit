"""Project paths and analytical configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    raw_dir: Path
    processed_dir: Path
    build_dir: Path
    db_path: Path
    temp_db_path: Path
    docs_dir: Path
    report_template: Path
    report_source: Path

    @classmethod
    def from_root(cls, root: Path) -> ProjectPaths:
        root = root.resolve()
        raw_override = os.environ.get("FLEET_RAW_DIR")
        database_override = os.environ.get("FLEET_DB_PATH")
        raw_dir = Path(raw_override) if raw_override else root / "data" / "raw"
        db_path = (
            Path(database_override)
            if database_override
            else root / "data" / "fleet_analytics.duckdb"
        )
        if not raw_dir.is_absolute():
            raw_dir = root / raw_dir
        if not db_path.is_absolute():
            db_path = root / db_path
        raw_dir = raw_dir.resolve()
        db_path = db_path.resolve()
        return cls(
            root=root,
            raw_dir=raw_dir,
            processed_dir=root / "data" / "processed",
            build_dir=root / "build",
            db_path=db_path,
            # A second period in the stem is interpreted as a catalog separator by dbt-duckdb.
            temp_db_path=db_path.with_name(f"{db_path.stem}_tmp{db_path.suffix}"),
            docs_dir=root / "docs",
            report_template=root / "reports" / "fleet_dashboard.qmd.j2",
            report_source=root / "build" / "fleet_dashboard.qmd",
        )


def load_config(path: Path) -> dict:
    """Load and minimally validate version-controlled analytical assumptions."""
    with path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    required = {
        "availability_threshold",
        "availability_sensitivity",
        "severe_availability_threshold",
        "peer_percentile_cutoff",
        "peer_strict_cutoff",
        "peer_minimum_size",
        "planning_window_years",
        "alpha",
        "bootstrap_iterations",
        "permutation_iterations",
        "random_seed",
    }
    missing = required - set(config)
    if missing:
        raise ValueError(f"Missing configuration keys: {sorted(missing)}")
    return config
