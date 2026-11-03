"""The state that flows through the review graph.

LangGraph passes one state dictionary from node to node. A node returns only
the keys it changes, and LangGraph merges them in. By default a new value
replaces the old one. A field annotated with a reducer, such as `operator.add`,
is combined with the old value instead, so several nodes can add findings to
the same list. That matters in Unit 4, where separate agents run in parallel.
"""

import operator
from typing import Annotated, Any, TypedDict


class PRAnalysisState(TypedDict):
    # The job and the pull request, set once by the worker.
    job_id: str
    repo_name: str
    pr_number: int
    head_sha: str
    title: str
    diff_text: str
    # True when the diff was cut to the size limit, so the review is partial.
    diff_truncated: bool

    # Findings, as `Finding.model_dump(mode="json")` dictionaries rather than
    # models, so the state stays plain JSON for checkpoints in Unit 4.
    syntax_issues: Annotated[list[dict[str, Any]], operator.add]
    security_vulnerabilities: Annotated[list[dict[str, Any]], operator.add]

    # A `RiskLevel` value: LOW, MEDIUM, HIGH, or CRITICAL.
    overall_risk_score: str
    # The finished Markdown report, written by `format_report_node`.
    final_summary: str
