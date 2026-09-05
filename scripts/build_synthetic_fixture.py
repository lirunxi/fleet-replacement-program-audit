"""Create non-sensitive CSV fixtures for CI transformation tests."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from fleet_analysis.ingest import UNIT_COLUMNS, UNIT_FILE, USAGE_COLUMNS, USAGE_FILE

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build non-sensitive fleet CSV fixtures.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RAW_DIR)
    args = parser.parse_args()
    raw_dir = args.output_dir.resolve()
    raw_dir.mkdir(parents=True, exist_ok=True)
    existing = [path for path in (raw_dir / UNIT_FILE, raw_dir / USAGE_FILE) if path.exists()]
    if existing:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(
            f"Refusing to overwrite existing raw input files: {names}. "
            "This fixture builder is intended for a clean CI checkout."
        )
    unit_rows = []
    usage_rows = []
    for index in range(80):
        unit_no = f"SYN{index:04d}"
        unit = dict.fromkeys(UNIT_COLUMNS, "")
        unit.update(
            {
                "UNIT_NO": unit_no,
                "DIVISION": "SYNTHETIC NORTH" if index < 40 else "SYNTHETIC SOUTH",
                "YEAR": str(2010 + index % 12),
                "MAKE": "TEST MAKE",
                "MODEL": f"MODEL {index % 4}",
                "CATEGORY": "TEST",
                "CATEGORY_DESC": "Synthetic fixture vehicle",
                "CATEGORY_CLASS": "CI",
                "CATEGORY_GROUP": "TEST GROUP",
                "CATEGORY_GROUP_DESC": "Synthetic tests only",
                "UNIT_TYPE": "LIGHT DUTY" if index < 40 else "MEDIUM DUTY",
                "FUEL_PRODUCT": "UNLEADED" if index < 40 else "DIESEL",
                "AGE": str(5 + index % 12),
                "EXPECTED_LIFE(YR)": "10",
                "IN_SERVICE_DATE": "2018-01-01",
                "CURRENT_STATUS_DESCRIPTION": (
                    "REPLACEMENT PROGRAM" if index % 10 == 0 else "ACTIVE UNIT"
                ),
                "HIGH_PRIORITY": "Y" if index % 17 == 0 else "",
                "OWNING_COST_CENTER": "SYNTHETIC",
                "USING_COST_CENTER": "SYNTHETIC",
                "MAINTENENACE_LOCATION_NAME": "SYNTHETIC GARAGE",
                "PARK_LOCATION": "SYN",
                "PARK_LOCATION_NAME": "SYNTHETIC YARD",
                "BILLING_CODE": "CI",
                "MAINTENANCE CLASSIFICATION CODE": "CI",
                "TECH_SPEC": "SYN",
                "TECH_SPEC_DESC": "Synthetic fixture",
            }
        )
        usage = dict.fromkeys(USAGE_COLUMNS, "")
        usage.update(
            {
                "UNIT_NO": unit_no,
                "LAST_FUEL_DATE": "2026-01-01",
                "LAST_WORKORDER_OPEN_DATE": "2026-01-15",
                "M5_LIFE_KM_USAGE(KM)": str(50000 + index * 1000),
                "M5_LIFE_HRS_USAGE(Hrs)": str(2500 + index * 10),
                "M5_YTD_KM_USAGE(KM)": str(5000 + index * 10),
                "M5_YTD_HRS_USAGE(Hrs)": str(250 + index),
                "AVAILABLE HOURS (Hrs)": "1000",
                "DOWNTIME(Hrs)": str(50 + (index % 8) * 50),
                "EXPECT_USAGE(KM)": "12000",
                "EXPECT_USAGE(Hrs)": "800",
                "VEU": "1.0",
            }
        )
        unit_rows.append(unit)
        usage_rows.append(usage)

    pd.DataFrame(unit_rows, columns=UNIT_COLUMNS).to_csv(
        raw_dir / UNIT_FILE, index=False, encoding="utf-8"
    )
    pd.DataFrame(usage_rows, columns=USAGE_COLUMNS).to_csv(
        raw_dir / USAGE_FILE, index=False, encoding="utf-8"
    )
    print(f"Wrote {len(unit_rows)} synthetic unit and usage rows to {raw_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
