"""Export verified report data, create fixed Plotly evidence, and render Quarto."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from playwright.sync_api import sync_playwright


def _records_json(frame: pd.DataFrame) -> str:
    return frame.to_json(orient="records", date_format="iso", double_precision=10)


def _html_table(
    frame: pd.DataFrame,
    *,
    compact: bool = False,
    percent_columns: tuple[str, ...] = (),
    column_labels: dict[str, str] | None = None,
) -> str:
    """Render stakeholder-friendly tables without leaking dataframe conventions."""
    display = frame.copy()
    percentages = {
        column
        for column in display.columns
        if column == "percentage" or column.endswith(("_pct", "_rate", "_percentage"))
    } | set(percent_columns)

    def format_value(value: object, column: str) -> str:
        if pd.isna(value):
            return "—"
        if isinstance(value, (bool, np.bool_)):
            return "Yes" if value else "No"
        if column in percentages:
            return f"{float(value):.1%}"
        if column in {"p_value", "adjusted_p_value"}:
            return f"{float(value):.4g}"
        if isinstance(value, (int, np.integer)):
            return f"{int(value):,}"
        if isinstance(value, (float, np.floating)):
            numeric = float(value)
            return f"{int(numeric):,}" if numeric.is_integer() else f"{numeric:.2f}"
        return str(value)

    for column in display.columns:
        display[column] = display[column].map(lambda value, name=column: format_value(value, name))

    labels = {
        "unit_no": "Unit ID",
        "eligible_denominator": "Eligible",
        "excluded_records": "Excluded",
        "percentage": "Share",
        "coverage_pct": "Coverage",
        "ci_lower": "95% CI lower",
        "ci_upper": "95% CI upper",
        "p_value": "P-value",
        "adjusted_p_value": "Adjusted p-value",
        "model_n": "Model n",
    }
    labels.update(column_labels or {})
    display = display.rename(
        columns=lambda column: labels.get(column, column.replace("_", " ").title())
    )
    classes = "report-table compact" if compact else "report-table"
    return display.to_html(index=False, classes=classes, border=0, escape=True)


def _style_figure(figure: go.Figure, title: str, height: int = 400) -> go.Figure:
    figure.update_layout(
        title={
            "text": title,
            "x": 0.02,
            "xanchor": "left",
            "y": 0.98,
            "yanchor": "top",
            "font": {"size": 20},
            "pad": {"b": 10},
        },
        template="plotly_white",
        height=height,
        margin={"l": 68, "r": 32, "t": 96, "b": 62},
        font={"family": "Segoe UI, Arial, sans-serif", "color": "#263746", "size": 14},
        colorway=["#155E9A", "#E3A21A", "#7A8793", "#B23A48", "#4D7C8A"],
        legend={
            "orientation": "h",
            "x": 0.5,
            "xanchor": "center",
            "yanchor": "bottom",
            "y": 1.01,
            "font": {"size": 12},
        },
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        hoverlabel={"font": {"family": "Segoe UI, Arial, sans-serif"}},
    )
    figure.update_xaxes(
        showline=True,
        linecolor="#7A8793",
        linewidth=1,
        gridcolor="#E5EBF0",
        zeroline=False,
        title_standoff=14,
    )
    figure.update_yaxes(
        showline=True,
        linecolor="#7A8793",
        linewidth=1,
        gridcolor="#E5EBF0",
        zeroline=False,
        title_standoff=14,
    )
    return figure


def _program_figure(alignment: pd.DataFrame) -> go.Figure:
    pivot = alignment.pivot(
        index="lifecycle_band", columns="in_replacement_program", values="vehicle_count"
    ).fillna(0)
    order = [
        "At or beyond expected life",
        "Due within two years",
        "More than two years remaining",
        "Unknown lifecycle",
    ]
    pivot = pivot.reindex(order).fillna(0)
    figure = go.Figure()
    figure.add_bar(
        y=pivot.index,
        x=pivot.get(False, pd.Series(0, index=pivot.index)),
        name="Outside program",
        orientation="h",
        marker_color="#7A8793",
    )
    figure.add_bar(
        y=pivot.index,
        x=pivot.get(True, pd.Series(0, index=pivot.index)),
        name="In replacement program",
        orientation="h",
        marker_color="#155E9A",
    )
    figure.update_layout(barmode="stack", xaxis_title="Vehicles", yaxis_title="")
    return _style_figure(figure, "Operational fleet by lifecycle band and program status")


def _effect_figure(results: pd.DataFrame) -> go.Figure:
    data = results[results["hypothesis_id"].isin(["H1", "H1S"])].copy()
    data["label"] = data["hypothesis_id"].map(
        {"H1": "All operational", "H1S": "Outside program only"}
    )
    figure = go.Figure(
        go.Scatter(
            x=data["effect_estimate"] * 100,
            y=data["label"],
            mode="markers",
            marker={"size": 11, "color": "#155E9A"},
            error_x={
                "type": "data",
                "symmetric": False,
                "array": (data["ci_upper"] - data["effect_estimate"]) * 100,
                "arrayminus": (data["effect_estimate"] - data["ci_lower"]) * 100,
            },
            hovertemplate="%{y}: %{x:.1f} percentage points<extra></extra>",
        )
    )
    figure.add_vline(x=0, line_color="#7A8793", line_dash="dash")
    figure.add_vrect(x0=-5, x1=5, fillcolor="#E8EEF3", opacity=0.45, line_width=0)
    figure.update_xaxes(title="Median availability-proxy difference (percentage points)")
    figure.update_yaxes(title="")
    return _style_figure(
        figure, "Availability difference for at/beyond-life vehicles (95% BCa CI)"
    )


def _odds_figure(results: pd.DataFrame) -> go.Figure:
    rows = []
    for hypothesis, default_label in (
        ("H2", "Lifecycle urgent → program enrollment"),
        ("H3", "Low availability → program enrollment"),
        ("H5", "High priority → low availability"),
    ):
        record = results.loc[results["hypothesis_id"].eq(hypothesis)]
        if not record.empty:
            row = record.iloc[0]
            label = default_label
            if hypothesis == "H3":
                label += (
                    " (exploratory stratified sensitivity)"
                    if "Exploratory stratified" in row.effect_measure
                    else " (adjusted)"
                )
            rows.append((label, row.effect_estimate, row.ci_lower, row.ci_upper))
    forest = pd.DataFrame(rows, columns=["label", "estimate", "lower", "upper"])
    figure = go.Figure(
        go.Scatter(
            x=forest["estimate"],
            y=forest["label"],
            mode="markers",
            marker={"size": 11, "color": "#155E9A"},
            error_x={
                "type": "data",
                "symmetric": False,
                "array": forest["upper"] - forest["estimate"],
                "arrayminus": forest["estimate"] - forest["lower"],
            },
            hovertemplate="%{y}: OR %{x:.2f}<extra></extra>",
        )
    )
    figure.add_vline(x=1, line_color="#7A8793", line_dash="dash")
    figure.update_xaxes(type="log", title="Odds ratio (log scale)")
    figure.update_yaxes(title="")
    return _style_figure(figure, "Association estimates with 95% confidence intervals", 450)


def _availability_figure(frame: pd.DataFrame) -> go.Figure:
    data = frame[
        frame["is_operational"].fillna(False)
        & frame["availability_confidence"].eq("High")
        & frame["availability_proxy"].notna()
    ].copy()
    data["Overdue status"] = np.where(
        data["lifecycle_band"].eq("At or beyond expected life"),
        "At or beyond expected life",
        "Not beyond expected life",
    )
    figure = go.Figure()
    for label, color in (
        ("At or beyond expected life", "#B23A48"),
        ("Not beyond expected life", "#155E9A"),
    ):
        subset = data.loc[data["Overdue status"].eq(label), "availability_proxy"]
        figure.add_box(
            y=subset,
            name=label,
            marker_color=color,
            boxpoints="outliers",
            boxmean=True,
        )
    figure.update_yaxes(title="Availability proxy", tickformat=".0%")
    figure.update_xaxes(title="")
    return _style_figure(figure, "Availability-proxy distribution by lifecycle status")


def _funnel_figure(hotspots: pd.DataFrame) -> go.Figure:
    data = hotspots.sort_values("eligible_count")
    figure = go.Figure()
    figure.add_scatter(
        x=data["eligible_count"],
        y=data["raw_rate"],
        mode="markers+text",
        text=data["division"],
        textposition="top center",
        name="Division",
        marker={
            "size": 10,
            "color": np.where(
                data["hotspot_classification"].eq("Within expected variation"),
                "#155E9A",
                "#B23A48",
            ),
        },
        hovertemplate="%{text}<br>n=%{x}<br>rate=%{y:.1%}<extra></extra>",
    )
    for field, label, color, dash in (
        ("control_95_upper", "95% upper limit", "#E3A21A", "dash"),
        ("control_998_upper", "99.8% upper limit", "#B23A48", "dot"),
    ):
        figure.add_scatter(
            x=data["eligible_count"],
            y=data[field],
            mode="lines",
            name=label,
            line={"color": color, "dash": dash},
        )
    figure.update_xaxes(title="Eligible vehicles")
    figure.update_yaxes(title="Low-availability rate", tickformat=".0%", rangemode="tozero")
    return _style_figure(figure, "Division low-availability funnel plot")


def _quality_figure(quality: pd.DataFrame) -> go.Figure:
    data = quality.sort_values("coverage_pct")
    figure = go.Figure(
        go.Bar(
            x=data["coverage_pct"],
            y=data["issue"],
            orientation="h",
            marker_color="#155E9A",
            text=data["coverage_pct"].map(lambda value: f"{value:.1%}"),
            textposition="auto",
        )
    )
    figure.update_xaxes(title="Records passing check", tickformat=".0%", range=[0, 1])
    figure.update_yaxes(title="")
    return _style_figure(figure, "Analytical coverage by data-quality check")


def _figure_images(figures: list[go.Figure], asset_dir: Path) -> list[str]:
    """Export Plotly figures to lightweight images for the self-contained report."""
    asset_dir.mkdir(parents=True, exist_ok=True)
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    names = [
        "program_alignment",
        "availability_effect",
        "odds_ratio_forest",
        "availability_distribution",
        "division_funnel",
        "data_quality_coverage",
    ]
    output: list[str] = []
    with sync_playwright() as playwright:
        launch_options = {"headless": True}
        if chrome.exists():
            launch_options["executable_path"] = str(chrome)
        browser = playwright.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1050, "height": 580})
        for name, figure in zip(names, figures, strict=True):
            html_path = asset_dir / f"{name}.html"
            png_path = asset_dir / f"{name}.png"
            figure.write_html(
                html_path,
                full_html=True,
                include_plotlyjs="inline",
                config={"displaylogo": False, "responsive": False},
            )
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            page.locator(".plotly-graph-div").screenshot(path=str(png_path))
            output.append(
                f'<img class="report-figure" src="report_assets/{name}.png" '
                f'alt="{name.replace("_", " ")}">'
            )
        browser.close()
    return output


def _quarto_executable(project_root: Path) -> str:
    candidates = [
        shutil.which("quarto"),
        str(project_root / ".tools" / "quarto" / "bin" / "quarto.cmd"),
        str(project_root / ".tools" / "quarto" / "bin" / "quarto.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise FileNotFoundError(
        "Quarto CLI 1.10.18 was not found. Run scripts/bootstrap.ps1 after installing Quarto."
    )


def render_dashboard(database_path: Path, project_root: Path, output_path: Path) -> dict:
    """Create the data-driven Quarto source and render a standalone dashboard."""
    with duckdb.connect(str(database_path), read_only=True) as connection:
        vehicles = connection.execute(
            """
            select
                unit_no, division, unit_type, category_description, fuel,
                maintenance_location, current_status, in_replacement_program,
                is_operational, high_priority_flag, age, expected_life_years, life_consumed_pct,
                years_overdue, remaining_life_years, lifecycle_band,
                availability_proxy, availability_confidence, peer_group,
                peer_group_size, peer_median_availability,
                availability_peer_percentile, primary_action, review_tier,
                pareto_front, scenarios_selected, stability_pct,
                expanded_confidence_selected, reason_codes
            from analytics.vehicle_review_shortlist
            """
        ).fetchdf()
        overview = connection.execute(
            "select * from analytics.fleet_overview"
        ).fetchdf()
        alignment = connection.execute(
            "select * from analytics.program_alignment"
        ).fetchdf()
        quality = connection.execute(
            "select * from analytics.data_quality_summary"
        ).fetchdf()
        results = connection.execute(
            "select * from analytics.hypothesis_results order by hypothesis_id"
        ).fetchdf()
        terms = connection.execute(
            "select * from analytics.regression_terms"
        ).fetchdf()
        hotspots = connection.execute(
            "select * from analytics.hotspot_rates order by eligible_count desc"
        ).fetchdf()
        division = connection.execute(
            "select * from analytics.division_summary order by replacement_candidates desc"
        ).fetchdf()
        location = connection.execute(
            "select * from analytics.location_summary order by replacement_candidates desc"
        ).fetchdf()
        sensitivity = connection.execute(
            """
            select availability_threshold, peer_percentile_cutoff,
                   count(*) filter (where selected) as selected_vehicles
            from analytics.shortlist_sensitivity
            group by 1, 2 order by 1, 2
            """
        ).fetchdf()

    candidate_count = int(
        vehicles["primary_action"].eq("Replacement condition assessment").sum()
    )
    urgent_count = int(
        (
            vehicles["primary_action"].eq("Replacement condition assessment")
            & vehicles["review_tier"].eq("Urgent")
        ).sum()
    )
    actionable_count = int(
        (
            ~vehicles["in_replacement_program"].fillna(False)
            & ~vehicles["primary_action"].eq("No immediate action")
        ).sum()
    )
    primary_candidates = vehicles["primary_action"].eq(
        "Replacement condition assessment"
    )
    expanded_candidates = vehicles["expanded_confidence_selected"].fillna(False)
    confidence_sensitivity = pd.DataFrame(
        [
            {
                "comparison": "Primary: High confidence",
                "selected_vehicles": int(primary_candidates.sum()),
            },
            {
                "comparison": "Expanded: High + Medium confidence",
                "selected_vehicles": int(expanded_candidates.sum()),
            },
            {
                "comparison": "Selected under both definitions",
                "selected_vehicles": int((primary_candidates & expanded_candidates).sum()),
            },
            {
                "comparison": "Added only under expanded definition",
                "selected_vehicles": int((~primary_candidates & expanded_candidates).sum()),
            },
            {
                "comparison": "Primary candidates not stable under expansion",
                "selected_vehicles": int((primary_candidates & ~expanded_candidates).sum()),
            },
        ]
    )
    h2 = results[results["hypothesis_id"].eq("H2")].iloc[0]
    aligned = h2["effect_estimate"] > 1 and h2["p_value"] < h2["alpha"]
    thesis = (
        "The current program is strongly aligned with lifecycle timing, but a "
        "multi-signal exception process identifies outside-program vehicles for "
        "condition review."
        if aligned
        else "The observed snapshot does not support a clear lifecycle-alignment "
        "claim; management should validate program rules before using the exception queue."
    )
    operational_count = int(
        overview.loc[overview["metric"].eq("Operational vehicles"), "numerator"].iloc[0]
    )

    build_dir = project_root / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    explorer_vehicles = vehicles[
        ~vehicles["in_replacement_program"].fillna(False)
        & ~vehicles["primary_action"].eq("No immediate action")
    ].copy()
    vehicle_json = _records_json(explorer_vehicles).replace("</", "<\\/")
    division_rate_json = _records_json(
        division[
            ["division", "eligible_vehicles", "replacement_candidates", "candidate_rate"]
        ]
    ).replace("</", "<\\/")
    figures = _figure_images(
        [
            _program_figure(alignment),
            _effect_figure(results),
            _odds_figure(results),
            _availability_figure(vehicles),
            _funnel_figure(hotspots),
            _quality_figure(quality),
        ],
        build_dir / "report_assets",
    )
    environment = Environment(
        loader=FileSystemLoader(str(project_root / "reports")),
        undefined=StrictUndefined,
        autoescape=False,
    )
    template = environment.get_template("fleet_dashboard.qmd.j2")
    queue_columns = [
        "unit_no",
        "division",
        "unit_type",
        "lifecycle_band",
        "availability_proxy",
        "availability_peer_percentile",
        "review_tier",
        "reason_codes",
    ]

    def queue_table(action: str) -> str:
        return _html_table(
            vehicles.loc[vehicles["primary_action"].eq(action), queue_columns].head(40),
            compact=True,
            percent_columns=("availability_proxy", "availability_peer_percentile"),
        )

    rendered = template.render(
        thesis=thesis,
        fleet_count=len(vehicles),
        operational_count=operational_count,
        candidate_count=candidate_count,
        urgent_count=urgent_count,
        actionable_count=actionable_count,
        vehicle_json=vehicle_json,
        division_rate_json=division_rate_json,
        h2_statement=h2["result_statement"],
        overview_table=_html_table(overview),
        hypothesis_table=_html_table(
            results[
                [
                    "hypothesis_id",
                    "business_question",
                    "effect_measure",
                    "effect_estimate",
                    "ci_lower",
                    "ci_upper",
                    "p_value",
                    "adjusted_p_value",
                    "business_interpretation",
                ]
            ]
        ),
        regression_table=_html_table(terms, compact=True),
        quality_table=_html_table(quality),
        division_table=_html_table(division.drop(columns="division_key").head(20)),
        location_table=_html_table(location.head(20)),
        reliability_table=queue_table("Reliability investigation"),
        lifecycle_table=queue_table("Lifecycle planning"),
        data_review_table=queue_table("Data quality review"),
        sensitivity_table=_html_table(
            sensitivity,
            percent_columns=("availability_threshold", "peer_percentile_cutoff"),
        ),
        confidence_sensitivity_table=_html_table(confidence_sensitivity),
        program_figure=figures[0],
        effect_figure=figures[1],
        odds_figure=figures[2],
        availability_figure=figures[3],
        funnel_figure=figures[4],
        quality_figure=figures[5],
    )
    source_path = project_root / "build" / "fleet_dashboard.qmd"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(rendered, encoding="utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    quarto = _quarto_executable(project_root)
    quarto_environment = os.environ.copy()
    quarto_home = project_root / "build" / "quarto_home"
    quarto_home.mkdir(parents=True, exist_ok=True)
    quarto_environment["LOCALAPPDATA"] = str(quarto_home)
    quarto_environment["APPDATA"] = str(quarto_home)
    version = subprocess.run(
        [quarto, "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=quarto_environment,
    ).stdout.strip()
    if version != "1.10.18":
        raise RuntimeError(f"Expected Quarto 1.10.18, found {version}")
    subprocess.run(
        [
            quarto,
            "render",
            source_path.name,
            "--output",
            output_path.name,
        ],
        cwd=source_path.parent,
        env=quarto_environment,
        check=True,
    )
    return {
        "output": str(output_path),
        "candidate_count": candidate_count,
        "actionable_count": actionable_count,
        "quarto_version": version,
    }
