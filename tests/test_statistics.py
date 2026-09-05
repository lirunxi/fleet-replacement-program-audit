import numpy as np
import pandas as pd
from statsmodels.stats.multitest import multipletests

from fleet_analysis.statistics import (
    _bca_median_difference,
    _cliffs_delta,
    _fit_logit,
    _odds_ratio,
    _permutation_median_difference,
    _stratified_h3_fallback,
)


def test_resampling_is_reproducible_and_zero_difference_interval_contains_zero() -> None:
    a = np.array([0.60, 0.70, 0.80, 0.90, 0.95])
    b = a.copy()
    first = _permutation_median_difference(a, b, 1000, 42)
    second = _permutation_median_difference(a, b, 1000, 42)
    interval = _bca_median_difference(a, b, 1000, 42)
    assert first == second
    assert first[0] == 0
    assert interval[0] <= 0 <= interval[1]


def test_known_difference_has_expected_sign_and_valid_cliffs_delta() -> None:
    low = np.array([0.1, 0.2, 0.3, 0.4])
    high = np.array([0.7, 0.8, 0.9, 1.0])
    effect, _ = _permutation_median_difference(low, high, 1000, 7)
    delta = _cliffs_delta(low, high)
    assert effect < 0
    assert -1 <= delta <= 1
    assert delta == -1


def test_odds_ratio_matches_hand_calculation() -> None:
    estimate, lower, upper = _odds_ratio(20, 10, 10, 20)
    assert estimate == 4
    assert lower < estimate < upper


def test_benjamini_hochberg_adjusted_values_are_monotonic_when_sorted() -> None:
    p_values = np.array([0.001, 0.01, 0.03, 0.20])
    adjusted = multipletests(p_values, method="fdr_bh")[1]
    assert np.all(np.diff(adjusted) >= 0)


def test_regression_sign_matches_controlled_data_and_sparse_fallback_is_safe() -> None:
    generator = np.random.default_rng(11)
    x = generator.normal(size=1200)
    probability = 1 / (1 + np.exp(-(-0.8 + 1.5 * x)))
    y = generator.binomial(1, probability)
    data = pd.DataFrame({"y": y, "x": x})
    model, terms, note = _fit_logit("y ~ x", data, "T", "controlled")
    assert model is not None
    assert note == "ok"
    assert next(term for term in terms if term["term"] == "x")["coefficient"] > 0
    assert {term["model_n"] for term in terms} == {len(data)}

    separated = pd.DataFrame({"y": [0, 0, 0, 0], "x": [0, 1, 2, 3]})
    model, terms, note = _fit_logit("y ~ x", separated, "T", "sparse")
    assert model is None
    assert terms == []
    assert "omitted" in note.lower()


def test_stratified_fallback_returns_a_positive_common_association() -> None:
    rows = []
    for stratum in ("A", "B"):
        for low, program, count in ((1, 1, 12), (1, 0, 8), (0, 1, 5), (0, 0, 15)):
            rows.extend(
                {
                    "lifecycle_band": stratum,
                    "unit_type_group": "TYPE",
                    "division_group": "DIVISION",
                    "high_priority_numeric": 0,
                    "low_availability_flag": low,
                    "program_flag": program,
                }
                for _ in range(count)
            )
    result = _stratified_h3_fallback(pd.DataFrame(rows))
    assert result is not None
    assert result["included_n"] == len(rows)
    assert result["odds_ratio"] > 1
    assert result["ci_lower"] < result["odds_ratio"] < result["ci_upper"]
    assert 0 <= result["heterogeneity_p_value"] <= 1
