from pathlib import Path

import pandas as pd

from fleet_analysis.ingest import add_provenance


def test_duplicate_classification_preserves_all_source_rows(tmp_path: Path) -> None:
    path = tmp_path / "example.csv"
    path.write_text("UNIT_NO,VALUE\nA,1\nA,1\nB,1\nB,2\nC,3\n", encoding="utf-8")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)

    result = add_provenance(frame, path, "2026-09-04T00:00:00+00:00")

    assert result.loc[result["unit_no"].eq("A"), "_duplicate_type"].eq(
        "exact_duplicate"
    ).all()
    assert result.loc[result["unit_no"].eq("B"), "_duplicate_type"].eq(
        "conflicting_duplicate"
    ).all()
    assert result.loc[result["unit_no"].eq("C"), "_duplicate_type"].eq("unique").all()
    assert result["_source_row_number"].tolist() == [2, 3, 4, 5, 6]
    assert result["_raw_row_hash"].str.len().eq(64).all()
