"""Turns the review's findings into the Markdown comment for a pull request.

This node doesn't call the model. The findings are already structured, so
laying them out is plain code, which is free, instant, and the same every time.

The text in a finding came from a model that read an untrusted diff, and this
Markdown is meant to be posted on GitHub. So every piece of it is cleaned
first: one line, HTML escaped, `@mentions` defused, and link brackets escaped.
Otherwise a diff could make the bot ping people, hide text in a comment, or
add a link, headings, or a fake "approved" line to the review.
"""

import html
import re
from typing import Any

from src.agent.schemas import Severity
from src.agent.state import PRAnalysisState

_SEVERITY_ORDER = [level.value for level in Severity]  # LOW .. CRITICAL

_MENTION = re.compile(r"@(?=\w)")
_ZERO_WIDTH_SPACE = "​"

NO_PROBLEMS = "No problems found in the added lines."
PARTIAL_NOTE = (
    "> **Partial review:** the diff was too long, so only its first part was "
    "reviewed. The rest was not looked at."
)
FOOTER = "_This is an automated review by a language model. It can miss problems and report ones that aren't there._"


def _inline(text: Any) -> str:
    """Make untrusted text safe to place on one line of Markdown."""
    one_line = " ".join(str(text).split())
    escaped = html.escape(one_line, quote=False)
    escaped = _MENTION.sub("@" + _ZERO_WIDTH_SPACE, escaped)
    return escaped.replace("[", "\\[").replace("]", "\\]")


def _code(text: Any) -> str:
    """A code span the text can't break out of: no backticks inside it."""
    return "`" + " ".join(str(text).replace("`", "").split()) + "`"


def _severity_rank(finding: dict[str, Any]) -> int:
    severity = finding.get("severity")
    return _SEVERITY_ORDER.index(severity) if severity in _SEVERITY_ORDER else -1


def _finding_lines(number: int, finding: dict[str, Any]) -> list[str]:
    where = finding["file"] if finding.get("line") is None else f"{finding['file']}:{finding['line']}"
    lines = [f"{number}. **{_inline(finding['severity'])}**: {_inline(finding['title'])} ({_code(where)})"]
    lines.append(f"   {_inline(finding['explanation'])}")
    if finding.get("suggestion"):
        lines.append(f"   *Suggestion:* {_inline(finding['suggestion'])}")
    return lines


def _section(heading: str, findings: list[dict[str, Any]]) -> list[str]:
    if not findings:
        return []
    # `sorted` is stable, so findings of equal severity keep the model's order.
    ordered = sorted(findings, key=_severity_rank, reverse=True)
    lines = [f"### {heading} ({len(ordered)})", ""]
    for number, finding in enumerate(ordered, start=1):
        lines += _finding_lines(number, finding)
    return lines + [""]


async def format_report_node(state: PRAnalysisState) -> dict[str, Any]:
    security = state["security_vulnerabilities"]
    other = state["syntax_issues"]
    truncated = state["diff_truncated"]

    parts = ["## CodeGuardian review", "", f"**Overall risk:** {_inline(state['overall_risk_score'])}", ""]
    if truncated:
        parts += [PARTIAL_NOTE, ""]
    parts += [_inline(state["analysis_summary"]), ""]

    if not security and not other:
        # A cut-off review can't promise there's nothing wrong in what it didn't read.
        if not truncated:
            parts += [NO_PROBLEMS, ""]
    else:
        parts += _section("Security vulnerabilities", security)
        parts += _section("Other issues", other)

    parts.append(FOOTER)
    return {"final_summary": "\n".join(parts) + "\n"}
