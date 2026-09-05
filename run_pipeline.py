"""Command-line orchestration for the fleet replacement-program audit."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from fleet_analysis.config import ProjectPaths, load_config  # noqa: E402
from fleet_analysis.ingest import ingest  # noqa: E402
from fleet_analysis.reporting import render_dashboard  # noqa: E402
from fleet_analysis.shortlist import build_shortlist  # noqa: E402
from fleet_analysis.statistics import run_statistical_analysis  # noqa: E402


def _venv_executable(name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    executable = PROJECT_ROOT / ".venv" / ("Scripts" if os.name == "nt" else "bin") / (
        name + suffix
    )
    if executable.exists():
        return executable
    fallback = shutil.which(name)
    if fallback:
        return Path(fallback)
    raise FileNotFoundError(
        f"{executable} was not found and {name} is not on PATH. "
        "Run scripts/bootstrap.ps1 first."
    )


def run_dbt(database_path: Path, config: dict, command: str = "build") -> None:
    """Run dbt with the analytical thresholds supplied from the single config file."""
    environment = os.environ.copy()
    environment["FLEET_DB_PATH"] = str(database_path.resolve())
    variables = {
        "availability_threshold": config["availability_threshold"],
        "planning_window_years": config["planning_window_years"],
    }
    subprocess.run(
        [
            str(_venv_executable("dbt")),
            command,
            "--project-dir",
            str(PROJECT_ROOT),
            "--profiles-dir",
            str(PROJECT_ROOT),
            "--vars",
            json.dumps(variables),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=True,
    )


def export_report_data(database_path: Path, processed_dir: Path) -> None:
    """Export private, reproducible analytical extracts used for local QA."""
    processed_dir.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database_path), read_only=True) as connection:
        connection.execute(
            """
            copy (
                select * from analytics.vehicle_review_shortlist
                where primary_action <> 'No immediate action'
            ) to ? (format parquet, compression zstd)
            """,
            [str(processed_dir / "actionable_vehicle_review.parquet")],
        )
        connection.execute(
            """
            copy analytics.hypothesis_results to ?
            (header true, delimiter ',', quote '"')
            """,
            [str(processed_dir / "hypothesis_results.csv")],
        )


def reconcile(database_path: Path, report_path: Path | None = None) -> dict[str, int]:
    """Assert end-to-end row, action, sensitivity, and report invariants."""
    with duckdb.connect(str(database_path), read_only=True) as connection:
        raw_units = connection.execute("select count(*) from raw.fleet_units").fetchone()[0]
        raw_usage = connection.execute("select count(*) from raw.fleet_usage").fetchone()[0]
        unique_units = connection.execute(
            "select count(distinct trim(unit_no)) from raw.fleet_units"
        ).fetchone()[0]
        unique_usage = connection.execute(
            "select count(distinct trim(unit_no)) from raw.fleet_usage"
        ).fetchone()[0]
        vehicle_count = connection.execute("select count(*) from core.vehicle").fetchone()[0]
        final_count = connection.execute(
            "select count(*) from analytics.vehicle_review_shortlist"
        ).fetchone()[0]
        joined_count = connection.execute(
            "select count(*) from core.vehicle_metrics"
        ).fetchone()[0]
        program_candidates = connection.execute(
            """
            select count(*) from analytics.vehicle_review_shortlist
            where in_replacement_program
              and primary_action = 'Replacement condition assessment'
            """
        ).fetchone()[0]
        missing_reasons = connection.execute(
            """
            select count(*) from analytics.vehicle_review_shortlist
            where primary_action <> 'No immediate action'
              and coalesce(reason_codes, '') = ''
            """
        ).fetchone()[0]
        wrong_scenarios = connection.execute(
            """
            select count(*) from (
                select unit_no, count(*) as scenario_count
                from analytics.shortlist_sensitivity
                group by unit_no
                having count(*) <> 6
            )
            """
        ).fetchone()[0]
        invalid_availability = connection.execute(
            """
            select count(*) from core.vehicle_metrics
            where availability_proxy is not null
              and (availability_proxy < 0 or availability_proxy > 1)
            """
        ).fetchone()[0]

    if raw_units == 5640 and raw_usage == 5635:
        if unique_units != 5610 or unique_usage != 5610:
            raise AssertionError(
                "Current source snapshot does not reconcile to 5,610 unique IDs per file."
            )
    assertions = {
        "vehicle_matches_join": vehicle_count == joined_count,
        "vehicle_matches_shortlist": vehicle_count == final_count,
        "program_candidates_zero": program_candidates == 0,
        "actionable_reasons_complete": missing_reasons == 0,
        "six_scenarios_per_vehicle": wrong_scenarios == 0,
        "availability_in_range": invalid_availability == 0,
    }
    failures = [name for name, passed in assertions.items() if not passed]
    if failures:
        raise AssertionError(f"Reconciliation checks failed: {failures}")

    if report_path is not None:
        text = report_path.read_text(encoding="utf-8")
        prohibited = [
            "RAW_Fleet_Units.csv",
            "RAW_Fleet_Usage.csv",
            str(PROJECT_ROOT.resolve()),
            str(Path.home().resolve()),
        ]
        leaked = [value for value in prohibited if value.casefold() in text.casefold()]
        if leaked:
            raise AssertionError(f"Report contains prohibited local-source text: {leaked}")
        if "<html" not in text.lower():
            raise AssertionError("Rendered report is not an HTML document")

    return {
        "raw_units": raw_units,
        "raw_usage": raw_usage,
        "unique_units": unique_units,
        "unique_usage": unique_usage,
        "analytical_vehicles": final_count,
    }


def run_all(paths: ProjectPaths, config: dict) -> dict:
    """Build to temporary artifacts and promote only a completely verified result."""
    paths.build_dir.mkdir(parents=True, exist_ok=True)
    paths.docs_dir.mkdir(parents=True, exist_ok=True)
    temp_report = paths.build_dir / "fleet_replacement_report.tmp.html"
    for artifact in (paths.temp_db_path, temp_report):
        if artifact.exists():
            artifact.unlink()

    output: dict[str, object] = {}
    output["ingest"] = ingest(paths.raw_dir, paths.temp_db_path)
    run_dbt(paths.temp_db_path, config, "build")
    output["shortlist"] = build_shortlist(paths.temp_db_path, config)
    output["analysis"] = run_statistical_analysis(paths.temp_db_path, config)
    export_report_data(paths.temp_db_path, paths.processed_dir)
    output["report"] = render_dashboard(
        paths.temp_db_path, paths.root, temp_report
    )
    output["reconciliation"] = reconcile(paths.temp_db_path, temp_report)

    os.replace(paths.temp_db_path, paths.db_path)
    os.replace(temp_report, paths.docs_dir / "index.html")
    return output


def run_tests(paths: ProjectPaths, config: dict) -> None:
    subprocess.run(
        [
            str(_venv_executable("ruff")),
            "check",
            "src",
            "tests",
            "scripts",
            "run_pipeline.py",
        ],
        cwd=paths.root,
        check=True,
    )
    subprocess.run(
        [str(_venv_executable("pytest"))],
        cwd=paths.root,
        check=True,
    )
    if paths.db_path.exists():
        run_dbt(paths.db_path, config, "test")
        reconcile(paths.db_path, paths.docs_dir / "index.html")
        subprocess.run(
            [sys.executable, "scripts/verify_dashboard.py"],
            cwd=paths.root,
            check=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fleet replacement-program audit pipeline"
    )
    parser.add_argument(
        "stage",
        choices=["all", "ingest", "transform", "analyze", "shortlist", "render", "test"],
    )
    args = parser.parse_args()
    paths = ProjectPaths.from_root(PROJECT_ROOT)
    config = load_config(PROJECT_ROOT / "analysis_config.yml")

    if args.stage == "all":
        result = run_all(paths, config)
    elif args.stage == "ingest":
        result = ingest(paths.raw_dir, paths.db_path)
    elif args.stage == "transform":
        run_dbt(paths.db_path, config, "build")
        result = {"transform": "complete"}
    elif args.stage == "shortlist":
        result = build_shortlist(paths.db_path, config)
    elif args.stage == "analyze":
        result = run_statistical_analysis(paths.db_path, config)
    elif args.stage == "render":
        temporary = paths.build_dir / "fleet_replacement_report.tmp.html"
        result = render_dashboard(paths.db_path, paths.root, temporary)
        reconcile(paths.db_path, temporary)
        os.replace(temporary, paths.docs_dir / "index.html")
    else:
        run_tests(paths, config)
        result = {"tests": "complete"}
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
