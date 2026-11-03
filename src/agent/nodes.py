"""The review graph's nodes.

A node is a function that takes the current state and returns only the keys
it changes. Nodes that need the LLM are built by a `make_*` function that
takes the model, so tests can pass a fake one and the graph builder passes the
model from `get_chat_model()`.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from src.agent.schemas import CodeAuditReport, RiskLevel
from src.agent.state import PRAnalysisState

Node = Callable[[PRAnalysisState], Awaitable[dict[str, Any]]]

SYSTEM_PROMPT = """\
You are a senior code reviewer. Review the changes in one GitHub pull request \
for security vulnerabilities and bugs in the code it adds.

How to read the diff:
- Lines starting with "+" are added, lines starting with "-" are removed, and \
lines starting with a space are unchanged context. Review the added lines.
- A header such as "@@ -8,6 +8,7 @@" means the new version of the file starts \
at line 8 there. Count down from it, skipping removed lines, to find the line \
number of a problem. Use null for the line only if you can't work it out.

Rules for the report:
- Report only problems you can see in the added lines. Most pull requests have \
none, and empty lists are a good answer.
- Don't report style, naming, formatting, or missing documentation.
- Put a problem under security_vulnerabilities only if an attacker could \
exploit it. Everything else goes under syntax_issues.
- Rate the overall risk by the most serious problem you found, and LOW if none.
"""

EMPTY_DIFF_SUMMARY = "The pull request has no text changes to review."


class AnalysisError(ValueError):
    """The model's reply couldn't be used as a review.

    A `ValueError`, so the worker marks the job `failed` without retrying it.
    """


def build_messages(state: PRAnalysisState) -> list[BaseMessage]:
    """The system prompt, then the pull request's title and diff."""
    pull_request = (
        f"Repository: {state['repo_name']}\n"
        f"Pull request #{state['pr_number']}\n"
        f"<title>\n{state['title']}\n</title>\n"
        f"<diff>\n{state['diff_text']}\n</diff>"
    )
    return [SystemMessage(SYSTEM_PROMPT), HumanMessage(pull_request)]


def make_analyze_diff_node(model: BaseChatModel) -> Node:
    """Build the node that asks the model to review the diff."""
    reviewer = model.with_structured_output(CodeAuditReport)

    async def analyze_diff_node(state: PRAnalysisState) -> dict[str, Any]:
        if not state["diff_text"].strip():
            # Nothing to review, so don't spend a minute of model time on it.
            return _to_update(CodeAuditReport(summary=EMPTY_DIFF_SUMMARY, overall_risk_score=RiskLevel.LOW))

        try:
            report = await reviewer.ainvoke(build_messages(state))
        except (OutputParserException, ValidationError) as error:
            # The reply can quote the diff, so only the error's type goes into
            # the message, which ends up on the job record.
            raise AnalysisError(f"The model's reply wasn't a valid review ({type(error).__name__}).") from error

        return _to_update(report)

    return analyze_diff_node


# LOW, MEDIUM, HIGH, CRITICAL: an enum lists its members in definition order.
_LEVEL_ORDER = [level.value for level in RiskLevel]


def _overall_risk(report: CodeAuditReport) -> str:
    """The model's overall risk, raised to its most severe finding if lower.

    The prompt asks for this, but a small model doesn't always do it.
    """
    levels = [report.overall_risk_score.value]
    levels += [f.severity.value for f in report.syntax_issues + report.security_vulnerabilities]
    return max(levels, key=_LEVEL_ORDER.index)


def _to_update(report: CodeAuditReport) -> dict[str, Any]:
    # `mode="json"` turns enums into plain strings, so the state stays JSON.
    data = report.model_dump(mode="json")
    return {
        "analysis_summary": data["summary"],
        "overall_risk_score": _overall_risk(report),
        "syntax_issues": data["syntax_issues"],
        "security_vulnerabilities": data["security_vulnerabilities"],
    }
