"""Tests for `format_report_node` in `src/agent/report.py`."""

import pytest

from src.agent.report import format_report_node


def finding(**overrides) -> dict:
    base = {
        "file": "app/users.py",
        "line": 11,
        "severity": "HIGH",
        "title": "SQL injection",
        "explanation": "The username is put into the query text.",
        "suggestion": "Use a parameterized query.",
    }
    return base | overrides


def make_state(**overrides) -> dict:
    state = {
        "analysis_summary": "Changes a user lookup.",
        "overall_risk_score": "HIGH",
        "syntax_issues": [],
        "security_vulnerabilities": [finding()],
        "diff_truncated": False,
    }
    return state | overrides


async def report(**overrides) -> str:
    update = await format_report_node(make_state(**overrides))
    return update["final_summary"]


@pytest.mark.asyncio
async def test_node_returns_only_final_summary():
    update = await format_report_node(make_state())

    assert list(update) == ["final_summary"]


@pytest.mark.asyncio
async def test_report_shows_risk_summary_and_finding_details():
    text = await report()

    assert "HIGH" in text
    assert "Changes a user lookup." in text
    assert "SQL injection" in text
    assert "`app/users.py:11`" in text
    assert "The username is put into the query text." in text
    assert "Use a parameterized query." in text


@pytest.mark.asyncio
async def test_security_findings_come_before_other_issues():
    text = await report(syntax_issues=[finding(title="Off by one", severity="LOW")])

    assert text.index("Security") < text.index("Other issues")
    assert text.index("SQL injection") < text.index("Off by one")


@pytest.mark.asyncio
async def test_findings_are_listed_most_severe_first():
    text = await report(
        security_vulnerabilities=[
            finding(title="Minor one", severity="LOW"),
            finding(title="Worst one", severity="CRITICAL"),
            finding(title="Middle one", severity="MEDIUM"),
        ]
    )

    assert text.index("Worst one") < text.index("Middle one") < text.index("Minor one")


@pytest.mark.asyncio
async def test_missing_line_and_suggestion_are_left_out():
    text = await report(security_vulnerabilities=[finding(line=None, suggestion=None)])

    assert "`app/users.py`" in text
    assert "app/users.py:" not in text
    assert "Suggestion" not in text


@pytest.mark.asyncio
async def test_no_findings_says_so_and_has_no_empty_sections():
    text = await report(security_vulnerabilities=[], overall_risk_score="LOW")

    assert "No problems found" in text
    assert "Security" not in text
    assert "Other issues" not in text


@pytest.mark.asyncio
async def test_truncated_review_says_it_is_partial():
    text = await report(diff_truncated=True)

    assert "partial" in text.lower()


@pytest.mark.asyncio
async def test_complete_review_does_not_say_partial():
    assert "partial" not in (await report()).lower()


@pytest.mark.asyncio
async def test_truncated_review_with_no_findings_is_not_called_clean():
    text = await report(security_vulnerabilities=[], diff_truncated=True, overall_risk_score="LOW")

    assert "No problems found" not in text
    assert "partial" in text.lower()


@pytest.mark.asyncio
async def test_report_says_it_is_automated():
    assert "automated" in (await report()).lower()


# The model's text comes from an untrusted diff and will be posted on GitHub.


@pytest.mark.asyncio
async def test_mentions_are_neutralized():
    text = await report(security_vulnerabilities=[finding(explanation="Ask @some-user and @org/team.")])

    assert "@some-user" not in text and "@org/team" not in text
    assert "some-user" in text


@pytest.mark.asyncio
async def test_html_is_escaped():
    text = await report(analysis_summary="<img src=x onerror=alert(1)> <!-- hidden -->")

    assert "<img" not in text and "<!--" not in text


@pytest.mark.asyncio
async def test_file_path_cannot_break_out_of_its_code_span():
    text = await report(security_vulnerabilities=[finding(file="a`b](http://evil.example)`")])

    line = next(l for l in text.split("\n") if "SQL injection" in l)
    # Only the two backticks that open and close the code span.
    assert line.count("`") == 2


@pytest.mark.asyncio
async def test_a_title_with_newlines_stays_on_one_line():
    text = await report(security_vulnerabilities=[finding(title="One\n## Fake heading")])

    assert "\n## Fake heading" not in text
