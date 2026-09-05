"""Browser acceptance checks for the standalone Quarto dashboard."""

from __future__ import annotations

import contextlib
import csv
import os
import sys
from pathlib import Path

import duckdb
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT_ROOT / "docs" / "index.html"
database_override = os.environ.get("FLEET_DB_PATH")
DATABASE_PATH = Path(database_override) if database_override else Path(
    "data/fleet_analytics.duckdb"
)
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = PROJECT_ROOT / DATABASE_PATH


def main() -> int:
    if not REPORT_PATH.exists():
        raise FileNotFoundError("Run python run_pipeline.py render first.")
    with duckdb.connect(str(DATABASE_PATH), read_only=True) as connection:
        expected_fleet = connection.execute(
            "select count(*) from analytics.vehicle_review_shortlist"
        ).fetchone()[0]
        expected_operational = connection.execute(
            "select count(*) from analytics.vehicle_review_shortlist where is_operational"
        ).fetchone()[0]
        expected_candidates = connection.execute(
            """
            select count(*) from analytics.vehicle_review_shortlist
            where primary_action = 'Replacement condition assessment'
            """
        ).fetchone()[0]
        expected_urgent = connection.execute(
            """
            select count(*) from analytics.vehicle_review_shortlist
            where primary_action = 'Replacement condition assessment'
              and review_tier = 'Urgent'
            """
        ).fetchone()[0]
        expected_candidate_tiers = dict(
            connection.execute(
                """
                select review_tier, count(*)
                from analytics.vehicle_review_shortlist
                where primary_action = 'Replacement condition assessment'
                group by review_tier
                """
            ).fetchall()
        )
        expected_actionable = connection.execute(
            """
            select count(*) from analytics.vehicle_review_shortlist
            where not in_replacement_program
              and primary_action <> 'No immediate action'
            """
        ).fetchone()[0]
        expected_unit = connection.execute(
            """
            select unit_no from analytics.vehicle_review_shortlist
            where primary_action = 'Replacement condition assessment'
            order by unit_no limit 1
            """
        ).fetchone()[0]

    console_errors: list[str] = []
    page_errors: list[str] = []
    request_failures: list[str] = []
    with sync_playwright() as playwright:
        chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        options = {"headless": True}
        if chrome.exists():
            options["executable_path"] = str(chrome)
        browser = playwright.chromium.launch(**options)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on(
            "pageerror",
            lambda error: page_errors.append(f"{error}\n{error.stack}"),
        )
        page.on(
            "requestfailed",
            lambda request: request_failures.append(
                f"{request.url}: {request.failure or 'request failed'}"
            ),
        )
        page.goto(REPORT_PATH.resolve().as_uri(), wait_until="load")
        page.get_by_text("Fleet Replacement Program Audit", exact=True).first.wait_for()
        headline_values = [
            int(value.replace(",", ""))
            for value in page.locator(".value-card .value").all_text_contents()
        ]
        if headline_values != [
            expected_fleet,
            expected_operational,
            expected_candidates,
            expected_urgent,
        ]:
            raise AssertionError(
                f"Headline cards do not reconcile to DuckDB: {headline_values}"
            )
        with page.expect_download() as report_download_info:
            page.get_by_text("Download offline report", exact=True).click()
        report_download = report_download_info.value
        report_download_path = PROJECT_ROOT / "build" / "fleet_replacement_report.html"
        report_download.save_as(report_download_path)
        if report_download_path.stat().st_size < 1_000_000:
            raise AssertionError("Downloaded standalone report is unexpectedly small.")
        downloaded_page = browser.new_page(viewport={"width": 1280, "height": 900})
        downloaded_page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        downloaded_page.on(
            "pageerror",
            lambda error: page_errors.append(f"{error}\n{error.stack}"),
        )
        downloaded_page.goto(report_download_path.resolve().as_uri(), wait_until="load")
        downloaded_page.get_by_text(
            "Fleet Replacement Program Audit", exact=True
        ).first.wait_for()
        downloaded_page.locator("#tab-vehicle-review-explorer").click()
        downloaded_page.get_by_label("Primary action").wait_for()
        downloaded_page.close()
        page.locator("#report-download-status").evaluate(
            "element => { element.textContent = ''; }"
        )
        decision_layout = page.locator(".decision-note").evaluate(
            "element => ({clientHeight: element.clientHeight, "
            "scrollHeight: element.scrollHeight})"
        )
        if decision_layout["scrollHeight"] > decision_layout["clientHeight"] + 1:
            raise AssertionError("Executive decision callout clips its text.")
        decision_card_layout = page.locator(".decision-card .card-body").evaluate(
            "element => ({clientHeight: element.clientHeight, "
            "scrollHeight: element.scrollHeight})"
        )
        if decision_card_layout["scrollHeight"] > decision_card_layout["clientHeight"] + 1:
            raise AssertionError("Executive decision card clips its content.")
        if page.locator("#download-standalone-report").bounding_box()["width"] > 400:
            raise AssertionError("Standalone report download control is unexpectedly wide.")
        page.screenshot(
            path=str(PROJECT_ROOT / "docs" / "dashboard-preview.png"),
            full_page=True,
        )
        page.locator("#tab-vehicle-review-explorer").click()
        try:
            page.get_by_label("Primary action").wait_for(timeout=10_000)
        except Exception as exc:
            page_text = page.locator("#vehicle-review-explorer").inner_text()[:2000]
            raise AssertionError(
                f"Explorer controls did not initialize. Console={console_errors}; "
                f"page={page_text}"
            ) from exc
        scatter = page.locator(".scatter-chart")
        scatter.get_by_text(
            "Lifecycle consumed (% of expected life)", exact=True
        ).wait_for()
        scatter.get_by_text("Availability proxy (%)", exact=True).wait_for()
        scatter.get_by_text("80% availability threshold", exact=True).wait_for()
        scatter.get_by_text("Expected life reached", exact=True).wait_for()

        page.get_by_label("Primary action").select_option(
            label="Replacement condition assessment"
        )
        filtered_card = page.locator(".explorer-kpi").filter(
            has_text="Vehicles matching current filters"
        )
        filtered_card.get_by_text(str(expected_candidates), exact=True).wait_for()
        action_chart = page.locator(".browser-chart").nth(0)
        for tier_count in expected_candidate_tiers.values():
            action_chart.get_by_text(str(tier_count), exact=True).wait_for()
        division_chart = page.locator(".browser-chart").nth(1)
        all_tiers_division_text = division_chart.inner_text()
        page.get_by_label("Review tier").select_option(label="Urgent")
        filtered_card.get_by_text(str(expected_candidate_tiers["Urgent"]), exact=True).wait_for()
        if division_chart.inner_text() == all_tiers_division_text:
            raise AssertionError("Division-rate visual did not react to the tier filter.")
        page.get_by_label("Review tier").select_option(label="All")
        filtered_card.get_by_text(str(expected_candidates), exact=True).wait_for()

        page.get_by_label("UNIT_NO").fill(str(expected_unit))
        page.get_by_text(str(expected_unit), exact=True).first.wait_for()
        with page.expect_download() as download_info:
            page.get_by_text("Download filtered CSV", exact=True).click()
        download = download_info.value
        download_path = PROJECT_ROOT / "build" / "browser_filtered_download.csv"
        download.save_as(download_path)
        with download_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if len(rows) != 1 or str(rows[0]["unit_no"]) != str(expected_unit):
            raise AssertionError("Filtered CSV does not match the visible unit filter.")

        page.get_by_text("Reset all filters", exact=True).click()
        page.wait_for_timeout(250)
        reset_count_text = filtered_card.locator("strong").inner_text()
        reset_count = int(reset_count_text.replace(",", ""))
        if reset_count != expected_actionable:
            raise AssertionError(
                f"Reset returned {reset_count:,} rows; expected {expected_actionable:,}."
            )
        page.get_by_label("Primary action").select_option(
            label="Replacement condition assessment"
        )
        filtered_card.get_by_text(str(expected_candidates), exact=True).wait_for()

        page.screenshot(
            path=str(PROJECT_ROOT / "build" / "dashboard-explorer.png"),
            full_page=True,
        )
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(
            path=str(PROJECT_ROOT / "build" / "dashboard-mobile.png"),
            full_page=True,
        )
        browser.close()

    meaningful_errors = [
        error
        for error in console_errors
        if "favicon" not in error.lower() and "failed to load resource" not in error.lower()
    ]
    if meaningful_errors:
        raise AssertionError(f"Browser console errors: {meaningful_errors}")
    meaningful_failures = [
        failure for failure in request_failures if "favicon" not in failure.lower()
    ]
    if page_errors or meaningful_failures:
        raise AssertionError(
            f"Browser runtime errors: page={page_errors}; requests={meaningful_failures}"
        )
    print(
        f"Dashboard verified: {expected_candidates} candidates; "
        f"report/CSV downloads and UNIT_NO search={expected_unit}; no console errors."
    )
    return 0


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(main())
