"""Tests for zotero_arxiv_daily2markdown.construct_email: render_email, get_stars, get_block_html."""

from zotero_arxiv_daily2markdown.construct_email import (
    get_block_html,
    get_empty_html,
    get_stars,
    render_email,
    render_subscription_welcome_email,
    subscription_welcome_subject,
)
from tests.canned_responses import make_sample_paper


def test_render_email_with_papers():
    papers = [make_sample_paper(score=7.5, tldr="A great paper.", affiliations=["MIT"])]
    html = render_email(papers)
    assert "Sample Paper Title" in html
    assert "A great paper." in html
    assert "MIT" in html


def test_render_email_empty_list():
    html = render_email([])
    assert "No Papers Today" in html


def test_render_email_author_truncation():
    authors = [f"Author {i}" for i in range(10)]
    paper = make_sample_paper(authors=authors, score=7.0, tldr="ok")
    html = render_email([paper])
    assert "Author 0" in html
    assert "Author 1" in html
    assert "Author 2" in html
    assert "..." in html
    assert "Author 8" in html
    assert "Author 9" in html
    # Middle authors should be truncated
    assert "Author 5" not in html


def test_render_email_affiliation_truncation():
    affiliations = [f"Uni {i}" for i in range(8)]
    paper = make_sample_paper(affiliations=affiliations, score=7.0, tldr="ok")
    html = render_email([paper])
    assert "Uni 0" in html
    assert "Uni 4" in html
    assert "..." in html
    assert "Uni 7" not in html


def test_render_email_no_affiliations():
    paper = make_sample_paper(affiliations=None, score=7.0, tldr="ok")
    html = render_email([paper])
    assert "Unknown Affiliation" in html


def test_get_stars_low_score():
    assert get_stars(5.0) == ""
    assert get_stars(6.0) == ""


def test_get_stars_high_score():
    stars = get_stars(8.0)
    assert stars.count("full-star") == 5


def test_get_stars_mid_score():
    stars = get_stars(7.0)
    assert "star" in stars
    assert stars.count("full-star") + stars.count("half-star") > 0


def test_get_block_html_contains_all_fields():
    html = get_block_html("Title", "Auth", "3.5", "Summary", "http://pdf.url", "MIT")
    assert "Title" in html
    assert "Auth" in html
    assert "3.5" in html
    assert "Summary" in html
    assert "http://pdf.url" in html
    assert "MIT" in html


def test_get_empty_html():
    html = get_empty_html()
    assert "No Papers Today" in html


def test_render_email_can_include_revision_note():
    paper = make_sample_paper()
    html = render_email([paper], revision_note="昨日修订：2026-05-19")
    assert "昨日修订：2026-05-19" in html


def test_render_subscription_welcome_email_zh_contains_project_and_privacy_copy():
    html = render_subscription_welcome_email("zh")

    assert "订阅成功" in html
    assert "nickelate superconductors" in html
    assert "如果当天没有新增论文" in html
    assert "不会以任何形式向外泄露" in html
    assert "复旦大学本科大四学生" in html
    assert subscription_welcome_subject("zh") == "订阅成功：arXiv Daily 镍基超导日报"


def test_render_subscription_welcome_email_en_contains_project_and_privacy_copy():
    html = render_subscription_welcome_email("en")

    assert "Subscription Confirmed" in html
    assert "nickelate superconductors" in html
    assert "When there are no new papers" in html
    assert "will not be disclosed in any form" in html
    assert "Fudan University" in html
