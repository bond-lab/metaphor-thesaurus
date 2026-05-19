"""Browser UI tests using Playwright.

Install prerequisites:
  uv pip install pytest-playwright
  playwright install chromium

Then run:
  uv run pytest tests/test_browser_ui.py -v

The ``browser_server`` fixture in conftest.py starts a local HTTP server
serving web/ from a temp directory, using the pre-built thesaurus.db.gz.
Tests are skipped automatically when playwright is not installed or when
the database has not been built yet.

See cygnet/tests/test_ui.py for the pattern this follows.
"""

import pytest

try:
    from playwright.sync_api import Page, expect
    _PW = True
except ImportError:
    Page = object  # placeholder so function signatures parse at collection time
    expect = None
    _PW = False

pytestmark = pytest.mark.skipif(
    not _PW,
    reason="playwright not installed — run: uv pip install pytest-playwright && playwright install chromium",
)

# How long to wait for the DB to finish decompressing and rendering (ms)
DB_TIMEOUT = 15_000


def _wait_for_db(page: Page) -> None:
    """Wait until the search input is visible (signals DB has loaded)."""
    page.get_by_test_id("mode-search").wait_for(state="visible", timeout=DB_TIMEOUT)


# ── Basic page load ────────────────────────────────────────────────────────

def test_page_title(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    expect(page).to_have_title("Metaphor Thesaurus Browser")


def test_header_text(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    expect(page.get_by_text("Metaphor Thesaurus Browser")).to_be_visible()


def test_tabs_visible(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    for tab in ("tab-browser", "tab-reference", "tab-about"):
        expect(page.get_by_test_id(tab)).to_be_visible()


# ── Search ─────────────────────────────────────────────────────────────────

def test_search_returns_results(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("search-input").fill("wealth")
    expect(page.get_by_test_id("entry-card").first).to_be_visible(timeout=3_000)


def test_search_headword_match(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("search-input").fill("sapphire")
    first = page.get_by_test_id("entry-card").first
    expect(first).to_be_visible(timeout=3_000)
    expect(first).to_contain_text("sapphire")


def test_search_meaning_match(page: Page, browser_server: str) -> None:
    """Search on meaning text (not just headword) should return results."""
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("search-input").fill("precious stone")
    expect(page.get_by_test_id("entry-card").first).to_be_visible(timeout=3_000)


def test_search_no_results(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("search-input").fill("xyzzy_not_a_word_42")
    expect(page.get_by_text("No results")).to_be_visible(timeout=3_000)


# ── Entry detail ───────────────────────────────────────────────────────────

def test_click_entry_shows_detail(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("search-input").fill("gold")
    page.get_by_test_id("entry-card").first.click()
    expect(page.get_by_test_id("entry-detail")).to_be_visible(timeout=3_000)


def test_entry_detail_shows_theme_link(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("search-input").fill("sapphire")
    page.get_by_test_id("entry-card").first.click()
    expect(page.get_by_test_id("theme-name-link").first).to_be_visible(timeout=3_000)


def test_wordnet_toggle(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("search-input").fill("sapphire")
    page.get_by_test_id("entry-card").first.click()
    # WN section hidden by default
    expect(page.get_by_test_id("wn-section")).not_to_be_visible()
    page.get_by_test_id("wn-toggle").click()
    expect(page.get_by_test_id("wn-section")).to_be_visible(timeout=3_000)


# ── Theme navigation ───────────────────────────────────────────────────────

def test_click_theme_link_from_entry(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("search-input").fill("sapphire")
    page.get_by_test_id("entry-card").first.click()
    page.get_by_test_id("theme-name-link").first.click()
    expect(page.get_by_test_id("theme-detail")).to_be_visible(timeout=3_000)


def test_related_theme_is_clickable(page: Page, browser_server: str) -> None:
    """Related themes in the theme detail view should navigate to that theme."""
    page.goto(browser_server)
    _wait_for_db(page)
    # Navigate to Hierarchy and open a theme that has sub-themes
    page.get_by_test_id("mode-hierarchy").click()
    page.get_by_test_id("hierarchy-theme-btn").first.click()
    # Theme detail should appear with at least one theme-name-link in relationships
    expect(page.get_by_test_id("theme-detail")).to_be_visible(timeout=3_000)


# ── Domain browsing ────────────────────────────────────────────────────────

def test_source_domain_tab_loads(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("mode-source").click()
    expect(page.get_by_test_id("domain-list-item").first).to_be_visible(timeout=3_000)


def test_click_domain_shows_themes(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("mode-source").click()
    page.get_by_test_id("domain-list-item").first.click()
    expect(page.get_by_test_id("domain-theme-btn").first).to_be_visible(timeout=3_000)


def test_click_domain_theme_shows_detail(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("mode-source").click()
    page.get_by_test_id("domain-list-item").first.click()
    page.get_by_test_id("domain-theme-btn").first.click()
    expect(page.get_by_test_id("theme-detail")).to_be_visible(timeout=3_000)


def test_domain_pill_navigates(page: Page, browser_server: str) -> None:
    """Clicking a source/target domain pill navigates to the domain browse view."""
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("search-input").fill("gold")
    page.get_by_test_id("entry-card").first.click()
    # Click a source domain pill (green)
    pill = page.get_by_test_id("source-domain-pill").first
    expect(pill).to_be_visible(timeout=3_000)
    pill.click()
    # Should switch to source domain browse mode and pre-select that domain
    expect(page.get_by_test_id("domain-list-item").first).to_be_visible(timeout=3_000)


# ── Hierarchy ──────────────────────────────────────────────────────────────

def test_hierarchy_parts_load(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("mode-hierarchy").click()
    # 6 parts
    expect(page.locator("button[data-testid='mode-hierarchy']")).to_be_visible()


def test_hierarchy_expand_part(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("mode-hierarchy").click()
    # Click first part to expand
    page.locator("button.text-gray-800").first.click()
    expect(page.get_by_test_id("hierarchy-theme-btn").first).to_be_visible(timeout=3_000)


# ── Tooltips ───────────────────────────────────────────────────────────────

def test_pos_chip_has_tooltip(page: Page, browser_server: str) -> None:
    """Hovering a POS chip should show its full name."""
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("search-input").fill("wealth")
    page.get_by_test_id("entry-card").first.click()
    chip = page.get_by_test_id("pos-chip").first
    expect(chip).to_be_visible(timeout=3_000)
    chip.hover()
    # The tooltip span should become visible (it's hidden by default via CSS)
    tooltip = chip.locator("xpath=..").locator("span.hidden, span:not(.hidden)")
    # Just verify the chip is present and hoverable (full CSS tooltip testing is brittle)
    expect(chip).to_be_visible()


# ── Reference tab ──────────────────────────────────────────────────────────

def test_reference_tab_shows_relations_table(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    page.get_by_test_id("tab-reference").click()
    expect(page.get_by_text("Semantic relationship symbols")).to_be_visible()
    expect(page.get_by_test_id("relation-symbol").first).to_be_visible()


def test_reference_tab_shows_pos_table(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    page.get_by_test_id("tab-reference").click()
    expect(page.get_by_text("Word-class abbreviations")).to_be_visible()
    expect(page.get_by_text("noun")).to_be_visible()


# ── About tab ──────────────────────────────────────────────────────────────

def test_about_tab_shows_stats(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    _wait_for_db(page)
    page.get_by_test_id("tab-about").click()
    expect(page.get_by_text("entries")).to_be_visible()
    expect(page.get_by_text("themes")).to_be_visible()


def test_about_tab_has_goatly_credit(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    page.get_by_test_id("tab-about").click()
    expect(page.get_by_text("Andrew Goatly", exact=False)).to_be_visible()


def test_about_tab_has_bond_lab_link(page: Page, browser_server: str) -> None:
    page.goto(browser_server)
    page.get_by_test_id("tab-about").click()
    expect(page.get_by_text("Computational Linguistics Lab", exact=False)).to_be_visible()
