import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup


def test_committed_report_is_self_contained_and_private_paths_are_absent() -> None:
    report = Path("docs/index.html")
    if not report.exists():
        pytest.skip("Report has not been rendered yet")
    text = report.read_text(encoding="utf-8")
    soup = BeautifulSoup(text, "html.parser")
    assert soup.html is not None
    assert "RAW_Fleet_Units.csv" not in text
    assert "RAW_Fleet_Usage.csv" not in text
    assert str(Path.cwd()).casefold() not in text.casefold()
    assert str(Path.home()).casefold() not in text.casefold()
    external_resources = [
        tag.get(attribute)
        for tag, attribute in [
            *((item, "src") for item in soup.find_all(src=True)),
            *((item, "href") for item in soup.find_all(href=True)),
        ]
        if tag.get(attribute, "").startswith(("http://", "https://"))
        and tag.name in {"script", "link", "img"}
    ]
    assert external_resources == []

    vehicle_data = json.loads(soup.find(id="fleet-vehicle-data").string)
    assert vehicle_data
    assert all(not row["in_replacement_program"] for row in vehicle_data)
    assert all(row["primary_action"] != "No immediate action" for row in vehicle_data)

    division_rates = json.loads(soup.find(id="division-rate-data").string)
    assert division_rates
    division_labels = [row["division"].casefold() for row in division_rates]
    assert len(division_labels) == len(set(division_labels))
    assert all(
        row["candidate_rate"]
        == pytest.approx(row["replacement_candidates"] / row["eligible_vehicles"])
        for row in division_rates
        if row["eligible_vehicles"]
    )
