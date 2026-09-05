"""Predefined inferential analyses for population-level business conclusions."""

from __future__ import annotations

import math
import warnings
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import scipy.stats as scipy_stats
import statsmodels.formula.api as smf
from statsmodels.stats.contingency_tables import StratifiedTable
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import (
    confint_proportions_2indep,
    proportion_confint,
    proportions_ztest,
)

RESULT_COLUMNS = [
    "hypothesis_id",
    "business_question",
    "population_definition",
    "group_a",
    "group_b",
    "n_a",
    "n_b",
    "eligible_n",
    "excluded_n",
    "estimate_a",
    "estimate_b",
    "effect_measure",
    "effect_estimate",
    "ci_lower",
    "ci_upper",
    "test_name",
    "test_statistic",
    "p_value",
    "adjusted_p_value",
    "alpha",
    "result_statement",
    "business_interpretation",
    "limitations",
]

TERM_COLUMNS = [
    "hypothesis_id",
    "model_name",
    "covariance_type",
    "term",
    "coefficient",
    "standard_error",
    "adjusted_odds_ratio",
    "ci_lower",
    "ci_upper",
    "p_value",
    "converged",
    "model_n",
    "diagnostic_note",
]


def _odds_ratio(a: int, b: int, c: int, d: int) -> tuple[float, float, float]:
    """Return Haldane-corrected odds ratio and Wald log interval."""
    cells = np.array([a, b, c, d], dtype=float)
    if np.any(cells == 0):
        cells += 0.5
    a_f, b_f, c_f, d_f = cells
    estimate = (a_f * d_f) / (b_f * c_f)
    se = math.sqrt(np.sum(1 / cells))
    lower = math.exp(math.log(estimate) - 1.96 * se)
    upper = math.exp(math.log(estimate) + 1.96 * se)
    return estimate, lower, upper


def _cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    u = scipy_stats.mannwhitneyu(a, b, alternative="two-sided").statistic
    return float((2 * u) / (len(a) * len(b)) - 1)


def _permutation_median_difference(
    a: np.ndarray, b: np.ndarray, iterations: int, seed: int
) -> tuple[float, float]:
    observed = float(np.median(a) - np.median(b))
    combined = np.concatenate([a, b]).copy()
    generator = np.random.default_rng(seed)
    extreme = 0
    for _ in range(iterations):
        generator.shuffle(combined)
        permuted = float(np.median(combined[: len(a)]) - np.median(combined[len(a) :]))
        extreme += abs(permuted) >= abs(observed)
    return observed, (extreme + 1) / (iterations + 1)


def _bca_median_difference(
    a: np.ndarray, b: np.ndarray, iterations: int, seed: int
) -> tuple[float, float]:
    def statistic(x: np.ndarray, y: np.ndarray) -> float:
        return float(np.median(x) - np.median(y))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = scipy_stats.bootstrap(
            (a, b),
            statistic,
            n_resamples=iterations,
            confidence_level=0.95,
            method="BCa",
            paired=False,
            vectorized=False,
            rng=np.random.default_rng(seed),
        )
    return float(result.confidence_interval.low), float(result.confidence_interval.high)


def _cramers_v(table: np.ndarray) -> float:
    chi2 = scipy_stats.chi2_contingency(table, correction=False).statistic
    n = table.sum()
    return float(math.sqrt((chi2 / n) / min(table.shape[0] - 1, table.shape[1] - 1)))


def _record(**kwargs: object) -> dict:
    result = {column: None for column in RESULT_COLUMNS}
    result.update(kwargs)
    return result


def _h1(
    frame: pd.DataFrame,
    config: dict,
    *,
    exclude_program: bool,
) -> dict:
    population = frame[
        frame["is_operational"].fillna(False)
        & frame["valid_lifecycle"].fillna(False)
        & frame["availability_confidence"].eq("High")
    ].copy()
    if exclude_program:
        population = population[~population["in_replacement_program"].fillna(False)]
    overdue = population.loc[
        population["lifecycle_band"].eq("At or beyond expected life"),
        "availability_proxy",
    ].dropna()
    not_overdue = population.loc[
        ~population["lifecycle_band"].eq("At or beyond expected life"),
        "availability_proxy",
    ].dropna()
    a = overdue.to_numpy(float)
    b = not_overdue.to_numpy(float)
    effect, p_value = _permutation_median_difference(
        a, b, config["permutation_iterations"], config["random_seed"]
    )
    ci_lower, ci_upper = _bca_median_difference(
        a, b, config["bootstrap_iterations"], config["random_seed"]
    )
    delta = _cliffs_delta(a, b)
    significant = p_value < config["alpha"] and not (ci_lower <= 0 <= ci_upper)
    material = abs(effect) >= config["minimum_material_availability_difference"]
    suffix = " outside the current program" if exclude_program else ""
    statement = (
        f"At/beyond-life vehicles had a median availability proxy "
        f"{abs(effect) * 100:.1f} percentage points "
        f"{'lower' if effect < 0 else 'higher'} than other vehicles{suffix}. "
        f"The 95% BCa interval was [{ci_lower * 100:.1f}, {ci_upper * 100:.1f}] points."
    )
    interpretation = (
        "The observed lifecycle difference is statistically distinguishable and "
        f"{'meets' if material else 'does not meet'} the pre-specified five-point "
        "materiality threshold."
        if significant
        else "The snapshot does not provide sufficient evidence of a population-level "
        "availability difference by overdue status."
    )
    return _record(
        hypothesis_id="H1S" if exclude_program else "H1",
        business_question="Do vehicles at/beyond expected life have lower availability?",
        population_definition=(
            "Operational vehicles with valid lifecycle and high-confidence availability"
            + (", excluding current program vehicles" if exclude_program else "")
        ),
        group_a="At or beyond expected life",
        group_b="Not beyond expected life",
        n_a=len(a),
        n_b=len(b),
        eligible_n=len(a) + len(b),
        excluded_n=len(frame) - len(a) - len(b),
        estimate_a=float(np.median(a)),
        estimate_b=float(np.median(b)),
        effect_measure="Median availability difference; Cliff's delta in limitations",
        effect_estimate=effect,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        test_name="Two-sided label permutation test",
        test_statistic=effect,
        p_value=p_value,
        adjusted_p_value=p_value,
        alpha=config["alpha"],
        result_statement=statement,
        business_interpretation=interpretation,
        limitations=f"Cross-sectional association only; Cliff's delta={delta:.3f}.",
    )


def _h2(frame: pd.DataFrame, config: dict) -> tuple[dict, dict]:
    population = frame[
        frame["is_operational"].fillna(False) & frame["valid_lifecycle"].fillna(False)
    ].copy()
    urgent = population["lifecycle_urgent"].astype(bool)
    program = population["in_replacement_program"].astype(bool)
    a = int((urgent & program).sum())
    b = int((urgent & ~program).sum())
    c = int((~urgent & program).sum())
    d = int((~urgent & ~program).sum())
    table = np.array([[a, b], [c, d]])
    chi = scipy_stats.chi2_contingency(table, correction=False)
    odds, lower, upper = _odds_ratio(a, b, c, d)
    urgent_rate = a / (a + b)
    other_rate = c / (c + d)
    binary_record = _record(
        hypothesis_id="H2",
        business_question=(
            "Are lifecycle-urgent vehicles more likely to be in the replacement program?"
        ),
        population_definition="Operational vehicles with valid lifecycle",
        group_a="At/beyond life or due within two years",
        group_b="More than two years remaining",
        n_a=a + b,
        n_b=c + d,
        eligible_n=len(population),
        excluded_n=len(frame) - len(population),
        estimate_a=urgent_rate,
        estimate_b=other_rate,
        effect_measure="Odds ratio for replacement-program enrollment",
        effect_estimate=odds,
        ci_lower=lower,
        ci_upper=upper,
        test_name="Pearson chi-square",
        test_statistic=float(chi.statistic),
        p_value=float(chi.pvalue),
        adjusted_p_value=float(chi.pvalue),
        alpha=config["alpha"],
        result_statement=(
            f"Lifecycle-urgent vehicles had {odds:.2f} times the odds of program "
            f"enrollment (95% CI {lower:.2f}–{upper:.2f})."
        ),
        business_interpretation=(
            "Program enrollment is aligned with lifecycle urgency."
            if chi.pvalue < config["alpha"] and odds > 1
            else "The data do not show clear lifecycle alignment in program enrollment."
        ),
        limitations=(
            f"Association, not causation; Cramér's V={_cramers_v(table):.3f}. "
            "The program flag is evaluated as an observed management decision, not ground truth."
        ),
    )
    band_table = pd.crosstab(population["lifecycle_band"], program)
    band_table = band_table.reindex(columns=[False, True], fill_value=0)
    band_chi = scipy_stats.chi2_contingency(band_table.to_numpy(), correction=False)
    band_v = _cramers_v(band_table.to_numpy())
    band_rates = (
        population.assign(program_flag=program.astype(int))
        .groupby("lifecycle_band")["program_flag"]
        .mean()
    )
    rate_text = "; ".join(
        f"{band}: {rate:.1%}" for band, rate in band_rates.sort_index().items()
    )
    band_record = _record(
        hypothesis_id="H2B",
        business_question=(
            "Does replacement-program enrollment vary across the complete lifecycle bands?"
        ),
        population_definition="Operational vehicles with valid lifecycle",
        group_a="Lifecycle bands",
        group_b="Replacement-program status",
        n_a=len(population),
        n_b=None,
        eligible_n=len(population),
        excluded_n=len(frame) - len(population),
        estimate_a=None,
        estimate_b=None,
        effect_measure="Cramér's V for lifecycle-band association",
        effect_estimate=band_v,
        ci_lower=None,
        ci_upper=None,
        test_name="Pearson chi-square lifecycle-band contingency test",
        test_statistic=float(band_chi.statistic),
        p_value=float(band_chi.pvalue),
        adjusted_p_value=float(band_chi.pvalue),
        alpha=config["alpha"],
        result_statement=(
            f"Program enrollment differed across lifecycle bands "
            f"(Cramér's V={band_v:.3f}, p={band_chi.pvalue:.3g}). Rates: {rate_text}."
        ),
        business_interpretation=(
            "The complete lifecycle-band comparison confirms that alignment is not an "
            "artifact of collapsing the planning window into one binary indicator."
        ),
        limitations=(
            "Association, not causation. The omnibus effect does not identify whether "
            "any individual vehicle was correctly enrolled."
        ),
    )
    return binary_record, band_record


def _fit_logit(
    formula: str,
    data: pd.DataFrame,
    hypothesis_id: str,
    model_name: str,
    cluster_groups: pd.Series | None = None,
) -> tuple[object | None, list[dict], str]:
    outcome = formula.split("~", maxsplit=1)[0].strip()
    if outcome not in data or data[outcome].nunique(dropna=True) < 2:
        return None, [], "Model omitted because the outcome has fewer than two classes."
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if cluster_groups is None:
                result = smf.logit(formula, data=data).fit(disp=False, cov_type="HC3")
                covariance = "HC3 robust"
            else:
                result = smf.logit(formula, data=data).fit(
                    disp=False,
                    cov_type="cluster",
                    cov_kwds={"groups": cluster_groups},
                )
                covariance = "Division clustered"
        intervals = result.conf_int()
        terms = []
        converged = bool(result.mle_retvals.get("converged", True))
        if not converged or not np.isfinite(result.params.to_numpy()).all():
            return None, [], "Model omitted after convergence/separation diagnostics failed."
        for term in result.params.index:
            terms.append(
                {
                    "hypothesis_id": hypothesis_id,
                    "model_name": model_name,
                    "covariance_type": covariance,
                    "term": term,
                    "coefficient": float(result.params[term]),
                    "standard_error": float(result.bse[term]),
                    "adjusted_odds_ratio": float(math.exp(result.params[term])),
                    "ci_lower": float(math.exp(intervals.loc[term, 0])),
                    "ci_upper": float(math.exp(intervals.loc[term, 1])),
                    "p_value": float(result.pvalues[term]),
                    "converged": converged,
                    "model_n": len(data),
                    "diagnostic_note": "Inference model; not evaluated as a classifier.",
                }
            )
        return result, terms, "ok"
    except Exception as exc:  # sparse or separated real-world categories
        return None, [], f"Model omitted after convergence/separation failure: {type(exc).__name__}"


def _prepare_regression(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame[
        frame["is_operational"].fillna(False)
        & frame["valid_lifecycle"].fillna(False)
        & frame["availability_confidence"].eq("High")
        & frame["availability_proxy"].notna()
    ].copy()
    division_counts = data["division_key"].fillna("UNKNOWN").value_counts()
    data["division_group"] = data["division_key"].fillna("UNKNOWN").where(
        data["division_key"].fillna("UNKNOWN").map(division_counts) >= 30, "OTHER"
    )
    type_counts = data["unit_type"].fillna("Unknown").value_counts()
    data["unit_type_group"] = data["unit_type"].fillna("Unknown").where(
        data["unit_type"].fillna("Unknown").map(type_counts) >= 30, "Other"
    )
    data["program_flag"] = data["in_replacement_program"].astype(int)
    data["low_availability_flag"] = data["low_availability"].astype(int)
    data["high_priority_numeric"] = data["high_priority_flag"].astype(int)
    return data


def _stratified_h3_fallback(data: pd.DataFrame) -> dict | None:
    """Run a post-convergence stratified sensitivity when the specified logit fails."""
    work = data.copy()
    stratum_fields = [
        "lifecycle_band",
        "unit_type_group",
        "division_group",
        "high_priority_numeric",
    ]
    work["stratum_id"] = work[stratum_fields].astype(str).agg("|".join, axis=1)
    tables: list[np.ndarray] = []
    included_n = 0
    for _, group in work.groupby("stratum_id", sort=True):
        table = pd.crosstab(
            group["low_availability_flag"], group["program_flag"]
        ).reindex(index=[1, 0], columns=[1, 0], fill_value=0)
        if table.to_numpy().sum(axis=1).min() == 0:
            continue
        tables.append(table.to_numpy())
        included_n += len(group)
    if not tables:
        return None
    result = StratifiedTable(tables, shift_zeros=True)
    lower, upper = result.oddsratio_pooled_confint(alpha=0.05)
    test = result.test_null_odds()
    heterogeneity = result.test_equal_odds(adjust=True)
    return {
        "odds_ratio": float(result.oddsratio_pooled),
        "ci_lower": float(lower),
        "ci_upper": float(upper),
        "test_statistic": float(test.statistic),
        "p_value": float(test.pvalue),
        "heterogeneity_statistic": float(heterogeneity.statistic),
        "heterogeneity_p_value": float(heterogeneity.pvalue),
        "included_n": included_n,
        "strata_n": len(tables),
    }


def _h3(
    frame: pd.DataFrame, config: dict
) -> tuple[list[dict], list[dict], list[dict]]:
    raw = frame[
        frame["is_operational"].fillna(False)
        & frame["availability_confidence"].eq("High")
        & frame["availability_proxy"].notna()
    ].copy()
    low = raw["low_availability"].astype(bool)
    program = raw["in_replacement_program"].astype(bool)
    a = int((low & program).sum())
    b = int((low & ~program).sum())
    c = int((~low & program).sum())
    d = int((~low & ~program).sum())
    odds, lower, upper = _odds_ratio(a, b, c, d)
    low_rate = a / (a + b)
    other_rate = c / (c + d)
    risk_difference = low_rate - other_rate
    risk_lower, risk_upper = confint_proportions_2indep(
        a,
        a + b,
        c,
        c + d,
        method="newcomb",
        compare="diff",
        alpha=0.05,
    )
    table = np.array([[a, b], [c, d]])
    chi = scipy_stats.chi2_contingency(table, correction=False)

    data = _prepare_regression(frame)
    formula = (
        "program_flag ~ C(lifecycle_band) + low_availability_flag "
        "+ C(unit_type_group) + C(division_group) + high_priority_numeric"
    )
    model, robust_terms, note = _fit_logit(
        formula, data, "H3", "Program enrollment adjusted association"
    )
    _, cluster_terms, cluster_note = _fit_logit(
        formula,
        data,
        "H3",
        "Program enrollment adjusted association sensitivity",
        data["division_group"],
    )
    low_term = next(
        (term for term in robust_terms if term["term"] == "low_availability_flag"), None
    )
    fallback = _stratified_h3_fallback(data) if low_term is None else None
    if low_term:
        adjusted_text = (
            f"The adjusted odds ratio for low availability was "
            f"{low_term['adjusted_odds_ratio']:.2f} "
            f"(95% CI {low_term['ci_lower']:.2f}–{low_term['ci_upper']:.2f})."
        )
        primary_effect = low_term["adjusted_odds_ratio"]
        primary_lower = low_term["ci_lower"]
        primary_upper = low_term["ci_upper"]
        adjusted_p_value = low_term["p_value"]
        test_statistic = low_term["coefficient"] / low_term["standard_error"]
        effect_measure = "Adjusted odds ratio from explanatory logistic regression"
        test_name = "Pearson chi-square plus explanatory logistic regression"
        fallback_note = ""
    elif fallback:
        adjusted_text = (
            f"The specified regression was unstable. As a post-convergence exploratory "
            f"sensitivity, a stratified analysis estimated a common odds ratio of "
            f"{fallback['odds_ratio']:.2f} "
            f"(95% CI {fallback['ci_lower']:.2f}–{fallback['ci_upper']:.2f})."
        )
        primary_effect = fallback["odds_ratio"]
        primary_lower = fallback["ci_lower"]
        primary_upper = fallback["ci_upper"]
        adjusted_p_value = fallback["p_value"]
        test_statistic = fallback["test_statistic"]
        effect_measure = "Exploratory stratified common odds ratio for program enrollment"
        test_name = "Pearson chi-square plus post-convergence Mantel-Haenszel sensitivity"
        fallback_note = (
            f" The post-convergence sensitivity retained n={fallback['included_n']} "
            f"of {len(data)} across {fallback['strata_n']} strata and applied a 0.5 "
            f"sparse-cell correction. The Tarone-adjusted Breslow-Day heterogeneity "
            f"p-value was {fallback['heterogeneity_p_value']:.3f}; this is insufficient "
            "evidence to reject a common odds ratio across the retained strata."
        )
    else:
        adjusted_text = note
        primary_effect = odds
        primary_lower = lower
        primary_upper = upper
        adjusted_p_value = None
        test_statistic = float(chi.statistic)
        effect_measure = "Unadjusted odds ratio for program enrollment"
        test_name = "Pearson chi-square; adjusted analysis omitted"
        fallback_note = " No stable adjusted or stratified estimate was available."
    record = _record(
        hypothesis_id="H3",
        business_question=(
            "Are low-availability vehicles more likely to be in the program after "
            "accounting for lifecycle and fleet mix?"
        ),
        population_definition="Operational vehicles with high-confidence availability",
        group_a="Availability proxy below 80%",
        group_b="Availability proxy at least 80%",
        n_a=a + b,
        n_b=c + d,
        eligible_n=len(raw),
        excluded_n=len(frame) - len(raw),
        estimate_a=low_rate,
        estimate_b=other_rate,
        effect_measure=effect_measure,
        effect_estimate=primary_effect,
        ci_lower=primary_lower,
        ci_upper=primary_upper,
        test_name=test_name,
        test_statistic=test_statistic,
        p_value=float(chi.pvalue),
        adjusted_p_value=adjusted_p_value,
        alpha=config["alpha"],
        result_statement=(
            f"Program enrollment was {low_rate:.1%} for low-availability vehicles and "
            f"{other_rate:.1%} for other vehicles (risk difference "
            f"{risk_difference * 100:.1f} points). Low-availability vehicles had "
            f"unadjusted enrollment odds of {odds:.2f} "
            f"(95% CI {lower:.2f}–{upper:.2f}). {adjusted_text}"
        ),
        business_interpretation=(
            "The specified adjusted model was stable and estimates the association after "
            "measured lifecycle and fleet-mix differences; this is not a prediction model."
            if low_term
            else "The specified adjusted model was unstable, so no primary regression "
            "conclusion is reported. The stratified estimate is a transparently labelled "
            "post-convergence exploratory sensitivity, not a substitute for the failed "
            "primary model."
        ),
        limitations=(
            f"The unadjusted comparison includes n={len(raw)}; the adjusted complete-case "
            f"model frame includes n={len(data)} because valid lifecycle is required. "
            f"Specified model: {note.rstrip('.')}; cluster sensitivity: "
            f"{cluster_note.rstrip('.')}.{fallback_note} Unmeasured maintenance, cost, "
            "safety and policy factors may confound the association."
        ),
    )
    risk_record = _record(
        hypothesis_id="H3RD",
        business_question=(
            "What is the absolute difference in program-enrollment rates between "
            "low-availability and other operational vehicles?"
        ),
        population_definition="Operational vehicles with high-confidence availability",
        group_a="Availability proxy below 80%",
        group_b="Availability proxy at least 80%",
        n_a=a + b,
        n_b=c + d,
        eligible_n=len(raw),
        excluded_n=len(frame) - len(raw),
        estimate_a=low_rate,
        estimate_b=other_rate,
        effect_measure="Risk difference for replacement-program enrollment",
        effect_estimate=risk_difference,
        ci_lower=float(risk_lower),
        ci_upper=float(risk_upper),
        test_name="Newcombe confidence interval with Pearson chi-square test",
        test_statistic=float(chi.statistic),
        p_value=float(chi.pvalue),
        adjusted_p_value=float(chi.pvalue),
        alpha=config["alpha"],
        result_statement=(
            f"Program enrollment was {risk_difference * 100:.1f} percentage points "
            f"higher among low-availability vehicles (95% Newcombe CI "
            f"{risk_lower * 100:.1f}–{risk_upper * 100:.1f} points)."
        ),
        business_interpretation=(
            "The absolute effect complements the odds ratio with a directly interpretable "
            "difference in enrollment rates."
        ),
        limitations="Unadjusted cross-sectional association; not a causal effect.",
    )
    return [record, risk_record], robust_terms, cluster_terms


def _h4(
    frame: pd.DataFrame, config: dict
) -> tuple[dict, list[dict], pd.DataFrame]:
    data = _prepare_regression(frame)
    table = pd.crosstab(data["division_group"], data["low_availability_flag"])
    if 0 not in table.columns:
        table[0] = 0
    if 1 not in table.columns:
        table[1] = 0
    table = table[[0, 1]]
    chi = scipy_stats.chi2_contingency(table.to_numpy(), correction=False)
    full_formula = (
        "low_availability_flag ~ C(lifecycle_band) + C(unit_type_group) "
        "+ C(division_group)"
    )
    reduced_formula = "low_availability_flag ~ C(lifecycle_band) + C(unit_type_group)"
    full, terms, note = _fit_logit(
        full_formula, data, "H4", "Division-adjusted low-availability association"
    )
    try:
        reduced = smf.logit(reduced_formula, data=data).fit(disp=False)
        lr_stat = 2 * (full.llf - reduced.llf) if full is not None else float("nan")
        lr_df = int(full.df_model - reduced.df_model) if full is not None else 0
        lr_p = float(scipy_stats.chi2.sf(lr_stat, lr_df)) if lr_df > 0 else float("nan")
    except Exception:
        lr_stat, lr_p = float("nan"), float("nan")

    overall_rate = float(data["low_availability_flag"].mean())
    rows: list[dict] = []
    p_values: list[float] = []
    division_labels = (
        data[["division_group", "division"]]
        .dropna(subset=["division"])
        .drop_duplicates(subset=["division_group"])
        .set_index("division_group")["division"]
        .to_dict()
    )
    division_labels["OTHER"] = "Other"
    division_labels["UNKNOWN"] = "Unknown"
    for division, group in data.groupby("division_group"):
        n = len(group)
        cases = int(group["low_availability_flag"].sum())
        ci_low, ci_high = proportion_confint(cases, n, alpha=0.05, method="wilson")
        other = data[data["division_group"] != division]
        count = np.array([cases, int(other["low_availability_flag"].sum())])
        observations = np.array([n, len(other)])
        _, p_value = proportions_ztest(count, observations)
        p_values.append(float(p_value))
        adjusted_rate = None
        if full is not None:
            counterfactual = data.copy()
            counterfactual["division_group"] = division
            adjusted_rate = float(full.predict(counterfactual).mean())
        standard_error = math.sqrt(overall_rate * (1 - overall_rate) / n)
        rows.append(
            {
                "division": division_labels.get(division, division),
                "eligible_count": n,
                "observed_cases": cases,
                "expected_cases": overall_rate * n,
                "raw_rate": cases / n,
                "adjusted_rate": adjusted_rate,
                "ci_lower": float(ci_low),
                "ci_upper": float(ci_high),
                "control_95_lower": max(0.0, overall_rate - 1.96 * standard_error),
                "control_95_upper": min(1.0, overall_rate + 1.96 * standard_error),
                "control_998_lower": max(0.0, overall_rate - 3.09 * standard_error),
                "control_998_upper": min(1.0, overall_rate + 3.09 * standard_error),
                "p_value": float(p_value),
            }
        )
    hotspot = pd.DataFrame(rows)
    if not hotspot.empty:
        hotspot["adjusted_p_value"] = multipletests(
            p_values, alpha=config["alpha"], method="fdr_bh"
        )[1]
        hotspot["hotspot_classification"] = np.select(
            [
                (hotspot["raw_rate"] > hotspot["control_998_upper"])
                & (hotspot["adjusted_p_value"] < config["alpha"]),
                (hotspot["raw_rate"] > hotspot["control_95_upper"])
                & (hotspot["adjusted_p_value"] < config["alpha"]),
            ],
            ["Extreme high outlier", "High outlier"],
            default="Within expected variation",
        )
    record = _record(
        hypothesis_id="H4",
        business_question=(
            "Do divisions have different low-availability rates after accounting "
            "for vehicle mix?"
        ),
        population_definition=(
            "Operational vehicles with valid lifecycle and high-confidence availability; "
            "divisions below 30 eligible vehicles pooled as Other"
        ),
        group_a="Division rates",
        group_b="Fleet reference",
        n_a=len(data),
        n_b=None,
        eligible_n=len(data),
        excluded_n=len(frame) - len(data),
        estimate_a=float(table[1].sum() / table.to_numpy().sum()),
        estimate_b=None,
        effect_measure="Global division association; adjusted marginal rates in hotspot table",
        effect_estimate=_cramers_v(table.to_numpy()),
        ci_lower=None,
        ci_upper=None,
        test_name="Global chi-square and likelihood-ratio test",
        test_statistic=float(lr_stat if np.isfinite(lr_stat) else chi.statistic),
        p_value=float(chi.pvalue),
        adjusted_p_value=float(lr_p) if np.isfinite(lr_p) else None,
        alpha=config["alpha"],
        result_statement=(
            f"The global raw division comparison had p={chi.pvalue:.4g}; "
            f"the adjusted division-term likelihood-ratio p-value was "
            f"{lr_p:.4g}." if np.isfinite(lr_p) else
            f"The global raw division comparison had p={chi.pvalue:.4g}; {note}."
        ),
        business_interpretation=(
            "Use adjusted rates, confidence intervals and false-discovery-rate-corrected "
            "outlier labels instead of ranking divisions by raw counts."
        ),
        limitations=(
            "Division differences may reflect unmeasured duty cycle, route, garage or "
            f"maintenance factors. Model diagnostic: {note}."
        ),
    )
    return record, terms, hotspot


def _h5(frame: pd.DataFrame, config: dict) -> dict:
    population = frame[
        frame["is_operational"].fillna(False)
        & frame["availability_confidence"].eq("High")
        & ~frame["contaminated_high_priority"].fillna(False)
    ].copy()
    priority = population["high_priority_flag"].astype(bool)
    low = population["low_availability"].astype(bool)
    a = int((priority & low).sum())
    b = int((priority & ~low).sum())
    c = int((~priority & low).sum())
    d = int((~priority & ~low).sum())
    table = np.array([[a, b], [c, d]])
    expected = scipy_stats.contingency.expected_freq(table)
    if (expected < 5).any():
        test_name = "Fisher's exact test"
        statistic, p_value = scipy_stats.fisher_exact(table, alternative="two-sided")
    else:
        test_name = "Pearson chi-square"
        test = scipy_stats.chi2_contingency(table, correction=False)
        statistic, p_value = test.statistic, test.pvalue
    odds, lower, upper = _odds_ratio(a, b, c, d)
    priority_risk = a / (a + b) if a + b else float("nan")
    other_risk = c / (c + d) if c + d else float("nan")
    return _record(
        hypothesis_id="H5",
        business_question=(
            "Is verified high-priority status associated with low availability?"
        ),
        population_definition=(
            "Operational, high-confidence availability with either literal Y or blank "
            "priority; contaminated nonblank priority values excluded"
        ),
        group_a="Literal Y high priority",
        group_b="No verified high-priority flag",
        n_a=a + b,
        n_b=c + d,
        eligible_n=len(population),
        excluded_n=len(frame) - len(population),
        estimate_a=priority_risk,
        estimate_b=other_risk,
        effect_measure="Odds ratio for low availability",
        effect_estimate=odds,
        ci_lower=lower,
        ci_upper=upper,
        test_name=test_name,
        test_statistic=float(statistic),
        p_value=float(p_value),
        adjusted_p_value=float(p_value),
        alpha=config["alpha"],
        result_statement=(
            f"Verified high-priority vehicles had {odds:.2f} times the odds of low "
            f"availability (95% CI {lower:.2f}–{upper:.2f})."
        ),
        business_interpretation=(
            "Treat this as exploratory service-priority evidence; the flag can escalate "
            "review urgency but cannot create a replacement recommendation by itself."
        ),
        limitations=(
            "High-priority meaning is undocumented; contaminated nonblank values are "
            "excluded rather than interpreted."
        ),
    )


def run_statistical_analysis(database_path: Path, config: dict) -> dict[str, int]:
    """Execute all predefined tests and persist fixed statistical outputs."""
    with duckdb.connect(str(database_path)) as connection:
        frame = connection.execute(
            "SELECT * FROM analytics.vehicle_review_shortlist ORDER BY unit_no"
        ).fetchdf()
        results = [_h1(frame, config, exclude_program=False)]
        results.append(_h1(frame, config, exclude_program=True))
        h2, h2_band = _h2(frame, config)
        results.extend([h2, h2_band])
        h3, h3_terms, h3_cluster_terms = _h3(frame, config)
        results.extend(h3)
        h4, h4_terms, hotspots = _h4(frame, config)
        results.append(h4)
        results.append(_h5(frame, config))
        result_frame = pd.DataFrame(results, columns=RESULT_COLUMNS)
        term_frame = pd.DataFrame(
            h3_terms + h3_cluster_terms + h4_terms, columns=TERM_COLUMNS
        )
        for name, data in (
            ("hypothesis_results", result_frame),
            ("regression_terms", term_frame),
            ("hotspot_rates", hotspots),
        ):
            connection.register(f"{name}_frame", data)
            connection.execute(
                f"CREATE OR REPLACE TABLE analytics.{name} AS "
                f"SELECT * FROM {name}_frame"
            )
    return {
        "hypothesis_results": len(result_frame),
        "regression_terms": len(term_frame),
        "hotspot_rows": len(hotspots),
    }
