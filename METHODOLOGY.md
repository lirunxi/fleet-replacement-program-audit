# Analytical Methodology

This document describes how the fleet replacement audit is constructed, which records are eligible for each analysis, and how uncertainty is handled. The design deliberately separates management triage from statistical inference: observed vehicle conditions determine the review queues, while statistical tests support broader conclusions about the fleet and the current program.

## 1. Scope and decision boundary

The project addresses two questions:

1. Is the existing replacement program aligned with observable lifecycle and reliability evidence?
2. Which operational vehicles outside the program deserve condition review first?

The analysis does not predict a true replacement outcome because the source contains no historical record of confirmed replacement need, inspection findings or post-replacement results. It also does not estimate savings because maintenance and acquisition costs are unavailable.

## 2. Source handling and provenance

Both CSV sources are ingested as text before any field is interpreted. The raw tables add:

- source filename and row number;
- source-file SHA-256;
- ingestion timestamp;
- raw-row hash; and
- duplicate classification.

Exact duplicate rows collapse to one analytical record while retaining duplicate counts. If the same vehicle identifier contains conflicting versions, every version is preserved in an exception table and the vehicle is excluded from decision outputs.

For the current snapshot, the two sources reconcile as follows:

| Check | Fleet units | Fleet usage |
|---|---:|---:|
| Raw rows | 5,640 | 5,635 |
| Unique vehicle identifiers | 5,610 | 5,610 |
| Conflicting duplicate identifiers | 0 | 0 |

## 3. Cleaning and analytical eligibility

dbt staging models preserve raw values and create separate normalized or typed fields. Invalid values become null and retain a reason code. Suspected displaced values are never reassigned automatically.

Key rules include:

- Category keys use trimmed, case-insensitive normalization.
- Seven known vehicle statuses are mapped through a controlled seed.
- `Active`, `Redeployed` and `Replacement Program` are operational statuses.
- Only a literal trimmed `Y` is treated as verified high priority.
- Age is accepted from 0 through 80 years.
- Expected life is accepted from 1 through 50 years.
- Availability requires available hours above zero and downtime between zero and available hours.
- Alphabetic tokens in numeric usage fields are flagged as possible row displacement.
- Known statuses, fuels or unit types in unrelated unit fields are flagged as semantic contamination.

Availability confidence is assigned as follows:

| Level | Definition | Use in the analysis |
|---|---|---|
| High | Availability inputs are valid and no usage-row displacement is detected | Primary vehicle review and inference |
| Medium | Availability inputs are valid, but another usage field contains semantic contamination | Robustness analysis only |
| Low | Available hours or downtime is invalid | Data-quality review |

Usage, expected usage and VEU do not affect any vehicle action because their business definitions and source alignment are not sufficiently reliable.

## 4. Derived measures

The core vehicle metrics are:

```text
life_consumed_pct    = age / expected_life
remaining_life_years = expected_life - age
years_overdue         = max(age - expected_life, 0)
availability_proxy    = 1 - downtime_hours / available_hours
```

Lifecycle bands are mutually exclusive:

- `At or beyond expected life`: age is greater than or equal to expected life.
- `Due within two years`: expected life minus age is above zero and no more than two years.
- `More than two years remaining`: more than two years remain.
- `Unknown lifecycle`: age or expected life is invalid.

The term **availability proxy** is used throughout because no authoritative business definition was supplied with the snapshot.

## 5. Peer benchmarking

Peer comparisons use operational vehicles with High-confidence availability.

The primary peer group is:

```text
unit_type + expected_life_year
```

If this group contains fewer than 30 eligible vehicles, the calculation falls back to `unit_type`. If the fallback also contains fewer than 30 vehicles, no peer percentile is assigned.

For each eligible vehicle, the pipeline calculates group size, median availability, difference from the peer median and ascending availability percentile. Lower percentiles indicate weaker availability relative to peers. Division is intentionally excluded from the peer definition because division performance is an analytical outcome rather than a characteristic used to normalize performance away.

## 6. Vehicle review logic

### Replacement condition assessment

An operational vehicle outside the current program enters this queue only when it has:

- valid lifecycle data;
- High-confidence availability;
- reached expected life or is due within two years;
- availability below 80%; and
- availability in the bottom 20% of an eligible peer group.

### Action precedence

Each vehicle receives one primary action in this order:

1. **Data quality review** — evidence is insufficient for reliable triage.
2. **Current program alignment** — the vehicle is already in the replacement program.
3. **Replacement condition assessment** — the complete multi-signal rule is met.
4. **Reliability investigation** — availability is low, but the complete replacement rule is not met.
5. **Lifecycle planning** — the vehicle is overdue or due soon but availability is at least 80%.
6. **No immediate action** — no review condition is met.

Every applicable reason is retained, even when action precedence assigns a different primary queue.

### Review tiers

Replacement-condition candidates are classified as:

- **Urgent:** at or beyond expected life and at least one of availability below 60%, bottom-decile peer performance or verified high-priority status.
- **High:** at or beyond expected life and meets the full rule, but not the Urgent rule.
- **Planning:** due within two years and meets the full rule.

High-priority status can escalate a qualifying vehicle; it cannot independently create a replacement-condition recommendation.

## 7. Ordering and threshold sensitivity

The framework does not use a weighted score. Candidates are ordered by Pareto front across:

- greater life-consumed percentage;
- lower availability; and
- lower peer percentile.

A vehicle is on the first front when no other candidate is at least as urgent on all three dimensions and strictly more urgent on at least one. Removing the first front and repeating the comparison produces later fronts. Pareto position affects ordering only; it does not determine admission to the queue.

The replacement rule is recalculated under six scenarios:

```text
Availability threshold: 75%, 80%, 85%
Peer cutoff: bottom 10%, bottom 20%
```

Vehicle stability is the number of scenarios in which it is selected divided by six. The pipeline also compares the primary High-confidence population with an expanded High-plus-Medium-confidence population.

## 8. Statistical analysis plan

Statistical tests address fleet-level business questions and do not select individual vehicles.

Global conventions:

- two-sided tests;
- significance level of 0.05;
- 95% confidence intervals;
- complete-case populations disclosed for every analysis;
- no imputation of lifecycle or availability;
- 10,000 deterministic permutation iterations;
- 10,000 deterministic BCa bootstrap iterations; and
- Benjamini–Hochberg false-discovery-rate correction within families of division or location comparisons.

Effect size and interval width are interpreted before p-values. A non-significant result is described as insufficient evidence to reject the no-difference hypothesis, not evidence that the groups are identical.

### H1 — Lifecycle stage and availability

**Question:** Do operational vehicles at or beyond expected life have lower availability?

**Population:** Operational vehicles with valid lifecycle data and High-confidence availability.

**Methods:** Difference in medians, label-permutation test, BCa bootstrap interval and Cliff's delta. A five-percentage-point difference is the predefined threshold for operational materiality. The analysis is repeated after excluding vehicles already in the replacement program.

**Purpose:** Determine whether lifecycle age is accompanied by a practically important fleet-wide reliability difference.

### H2 — Lifecycle urgency and program enrollment

**Question:** Are lifecycle-urgent vehicles more likely to be in the existing replacement program?

**Population:** Operational vehicles with valid lifecycle data.

**Methods:** A binary 2×2 comparison and complete lifecycle-band contingency table, with chi-square tests, odds ratio, confidence interval and Cramér's V.

**Purpose:** Audit whether the current program is aligned with its apparent lifecycle rationale.

### H3 — Low availability and program enrollment

**Question:** Are low-availability vehicles more likely to be enrolled after accounting for lifecycle and fleet mix?

**Population:** Operational vehicles with High-confidence availability.

**Methods:** Unadjusted risk difference and odds ratio, followed by an explanatory binomial logistic regression:

```text
in_replacement_program
~ lifecycle_band
+ low_availability
+ unit_type
+ division
+ high_priority
```

Divisions with fewer than 30 eligible vehicles are pooled into `Other`. HC3 robust standard errors are primary; division-clustered standard errors are a sensitivity check. Convergence, sparse cells and separation are checked before coefficients are interpreted.

In the current data, the specified model exhibits separation after case-insensitive category normalization. Its unstable adjusted coefficient is therefore omitted. A clearly labelled exploratory Mantel–Haenszel sensitivity stratifies the 2×2 relationship by lifecycle band, unit type, division and priority, with a 0.5 correction for sparse cells. This supports interpretation but does not replace the failed primary adjusted model.

### H4 — Division-level low availability

**Question:** Do divisions differ in low-availability rates after accounting for vehicle mix?

**Population:** Operational vehicles with High-confidence availability.

**Methods:** Global chi-square test and an explanatory binomial model:

```text
low_availability
~ lifecycle_band
+ unit_type
+ division
```

A likelihood-ratio test evaluates the complete division term. Adjusted marginal rates, Wilson intervals and a funnel plot compare observed with expected cases while showing denominator uncertainty.

**Purpose:** Distinguish large divisions with many cases from divisions with unusually high rates.

### H5 — High-priority status and low availability

**Question:** Is verified high-priority status associated with low availability?

**Population:** Operational vehicles with High-confidence availability, comparing a literal `Y` with a blank or otherwise unverified priority flag. Contaminated nonblank priority values are excluded.

**Methods:** Risk difference, odds ratio and interval. Fisher's exact test is used when expected cell counts are below five; otherwise the chi-square test is used.

**Purpose:** Test whether the existing priority marker is consistent with observable reliability evidence. The finding is treated as exploratory because the field definition is not documented.

## 9. Interpretation safeguards

- The program flag is an existing management decision, not a ground-truth label proving that replacement was correct.
- Regression is explanatory rather than predictive; accuracy and ROC-AUC would answer the wrong question.
- Statistical significance does not imply operational importance.
- A division outlier is a prompt for investigation, not evidence of poor management.
- Missingness and contamination are reported as coverage, not silently imputed.
- No causal claim is made from the cross-sectional snapshot.

## 10. Future validation design

A future garage-level randomized pilot could test whether the review process improves decision efficiency:

- **Randomization unit:** maintenance garage, limiting contamination between reviewers.
- **Treatment:** analytical shortlist plus a structured condition-review checklist.
- **Control:** current review process.
- **Primary outcome:** confirmed actionable intervention per inspected vehicle within 90 days.
- **Secondary outcomes:** inspection hours per actionable finding, six-month downtime and emergency work orders.
- **Analysis:** intention-to-treat comparison with garage-clustered uncertainty.

A power calculation should be completed only after management supplies the current intervention-confirmation rate and the smallest improvement worth acting on.
