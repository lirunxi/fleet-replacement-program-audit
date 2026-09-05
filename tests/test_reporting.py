import pandas as pd

from fleet_analysis.reporting import _html_table, _odds_figure


def test_h3_forest_label_matches_stratified_effect_measure() -> None:
    results = pd.DataFrame(
        [
            {
                "hypothesis_id": "H3",
                "effect_measure": (
                    "Exploratory stratified common odds ratio for program enrollment"
                ),
                "effect_estimate": 1.86,
                "ci_lower": 1.31,
                "ci_upper": 2.65,
            }
        ]
    )
    figure = _odds_figure(results)
    labels = list(figure.data[0].y)
    assert labels == [
        "Low availability → program enrollment (exploratory stratified sensitivity)"
    ]


def test_html_table_formats_units_percentages_and_headers_for_stakeholders() -> None:
    frame = pd.DataFrame(
        [
            {
                "unit_no": "001100",
                "age": 5.0,
                "availability_proxy": 0.75,
                "coverage_pct": 1.0,
                "percentage": 0.92,
            }
        ]
    )
    html = _html_table(frame, percent_columns=("availability_proxy",))
    assert "Unit ID" in html
    assert "Availability Proxy" in html
    assert "75.0%" in html
    assert "100.0%" in html
    assert "92.0%" in html
    assert ">5<" in html
    assert "500.0%" not in html
