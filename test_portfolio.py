"""Tests for portfolio-link selection.

When constant.json configures two portfolios they split on emphasis, not medium:

    portfolios.visual     visual + UX   (graphic, brand, motion, print, visual/UI)
    portfolios.research   research + UX (discovery, usability, strategy, product)

Both typically carry web work, so a JD mentioning HTML/WordPress/front-end says nothing
about which to send. An earlier enforce_portfolio keyed off exactly those web signals and
forced the research portfolio onto every visual role that listed web skills -- the
"Graphic Designer / Marketing" case below is that bug, kept as a regression test.

Run: python3 -m pytest test_portfolio.py -q
"""
import pytest

import tailor
from profile import Profile

VISUAL = "visual.example.com"
RESEARCH = "research.example.com"

TWO = {"portfolios": {"visual": VISUAL, "research": RESEARCH}}
ONE = {"portfolios": {"default": "only.example.com"}}


def _prof(constant):
    return Profile.from_dicts(constant, {"skills": [], "experience": [], "projects": []})

# Abridged from the real posting: a visual role whose requirements are dense with web
# technology. Every web keyword here used to force the research portfolio.
TILCO_JD = """Graphic Designer / Marketing. Develop compelling visual content including
logos, motion graphics, and digital media. Design and maintain responsive websites using
HTML, CSS, JavaScript, and content management systems like WordPress or Drupal. Create
wireframes, prototypes, and UI designs. Conduct user research and usability testing.
Solid understanding of PHP, Angular, React, Node.js, Bootstrap, SCSS/SASS."""


def enforce(title, link, jd=TILCO_JD, constant=None):
    profile = _prof(TWO if constant is None else constant)
    return tailor.enforce_portfolio(
        {"job_title": title, "portfolio_link": link}, jd, profile)["portfolio_link"]


def test_visual_title_wins_over_a_web_heavy_jd():
    # The regression: a graphic role listing HTML/WordPress/React still gets the visual site.
    assert enforce("Graphic Designer / Marketing", RESEARCH) == VISUAL


@pytest.mark.parametrize("title", [
    "Graphic Designer",
    "Visual Designer",
    "Motion Designer",
    "Brand Designer",
    "Senior Branding Designer",
    "Art Director",
    "Print Designer",
    "Illustrator",
])
def test_visual_titles_take_the_visual_portfolio(title):
    assert enforce(title, RESEARCH) == VISUAL


@pytest.mark.parametrize("title", [
    "UX Researcher",
    "User Research Lead",
    "Senior Design Researcher",
    "UX Strategist",
    "Product Manager",
    "Information Architect",
])
def test_research_titles_take_the_research_portfolio(title):
    assert enforce(title, RESEARCH) == RESEARCH
    assert enforce(title, VISUAL) == RESEARCH


@pytest.mark.parametrize("title", [
    "Product Designer",
    "UX Designer",
    "Front-End Engineer",
    "Design Engineer",
    "Senior UI/UX Designer",
])
def test_ambiguous_titles_keep_the_models_choice(title):
    # The model read the whole JD; the title regex read nine words. It only overrides
    # when the title is unambiguous.
    assert enforce(title, VISUAL) == VISUAL
    assert enforce(title, RESEARCH) == RESEARCH


def test_a_title_naming_both_crafts_keeps_the_models_choice():
    assert enforce("Visual Design Researcher", VISUAL) == VISUAL
    assert enforce("Visual Design Researcher", RESEARCH) == RESEARCH


def test_missing_title_is_not_a_crash():
    assert tailor.enforce_portfolio(
        {"portfolio_link": VISUAL}, TILCO_JD, _prof(TWO))["portfolio_link"] == VISUAL
    assert tailor.enforce_portfolio(
        {"job_title": None, "portfolio_link": RESEARCH}, TILCO_JD, _prof(TWO))["portfolio_link"] == RESEARCH


def test_web_keywords_alone_never_move_the_link():
    # The whole point of the rewrite: the JD body is not the signal.
    web_jd = "Build responsive websites with HTML, CSS, JavaScript, React and WordPress."
    assert enforce("Graphic Designer", VISUAL, web_jd) == VISUAL
    assert enforce("Product Designer", VISUAL, web_jd) == VISUAL


# ── One portfolio, or none ────────────────────────────────────────────────────

@pytest.mark.parametrize("title", ["Graphic Designer", "UX Researcher", "Product Designer"])
def test_a_single_portfolio_is_forced_for_every_title(title):
    # Nothing to choose between, so a model that invented a URL cannot ship one.
    assert enforce(title, "hallucinated.example.org", constant=ONE) == "only.example.com"


def test_no_configured_portfolio_leaves_the_field_alone():
    out = tailor.enforce_portfolio({"job_title": "Graphic Designer", "portfolio_link": ""},
                                   TILCO_JD, _prof({}))
    assert out["portfolio_link"] == ""


def test_portfolio_links_normalizes_its_shapes():
    assert tailor.portfolio_links(TWO) == {"visual": VISUAL, "research": RESEARCH}
    assert tailor.portfolio_links({"portfolios": "one.example.com"}) == {"default": "one.example.com"}
    assert tailor.portfolio_links({"portfolios": {"visual": "a.example.com"}}) == {"default": "a.example.com"}
    # Same URL twice is one portfolio, not a choice.
    assert tailor.portfolio_links(
        {"portfolios": {"visual": "a.example.com", "research": "a.example.com"}}
    ) == {"default": "a.example.com"}
    # Falls back to content.json's own field.
    assert tailor.portfolio_links({"portfolio_link": "b.example.com"}) == {"default": "b.example.com"}
    assert tailor.portfolio_links({}) == {}
