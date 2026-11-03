"""Tests for the review report schema and the graph state."""

import json
import operator
from typing import Annotated, get_args, get_origin, get_type_hints

import pytest
from langgraph.errors import InvalidUpdateError
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from src.agent.schemas import CodeAuditReport, Finding, RiskLevel, Severity
from src.agent.state import PRAnalysisState

SQL_INJECTION_REPLY = {
    "summary": "The new query builds SQL from user input.",
    "overall_risk_score": "HIGH",
    "syntax_issues": [],
    "security_vulnerabilities": [
        {
            "file": "app/users.py",
            "line": 12,
            "severity": "HIGH",
            "title": "SQL injection",
            "explanation": "The username is put into the query with an f-string.",
            "suggestion": "Use a parameterized query.",
        }
    ],
}


# --- CodeAuditReport ---------------------------------------------------------


def test_report_validates_a_model_reply():
    # The LLM's structured output arrives as JSON text.
    report = CodeAuditReport.model_validate_json(json.dumps(SQL_INJECTION_REPLY))

    assert report.overall_risk_score is RiskLevel.HIGH
    [finding] = report.security_vulnerabilities
    assert finding.severity is Severity.HIGH
    assert finding.line == 12
    assert report.syntax_issues == []


def test_clean_report_needs_only_summary_and_risk():
    report = CodeAuditReport.model_validate({"summary": "Looks fine.", "overall_risk_score": "LOW"})

    assert report.syntax_issues == []
    assert report.security_vulnerabilities == []


@pytest.mark.parametrize("value", ["high", "High", " HIGH "])
def test_levels_ignore_case_and_spaces(value: str):
    # Small models often write levels in lowercase.
    finding = Finding.model_validate(SQL_INJECTION_REPLY["security_vulnerabilities"][0] | {"severity": value})
    report = CodeAuditReport.model_validate({"summary": "s", "overall_risk_score": value})

    assert finding.severity is Severity.HIGH
    assert report.overall_risk_score is RiskLevel.HIGH


def test_line_is_optional():
    data = SQL_INJECTION_REPLY["security_vulnerabilities"][0] | {"line": None}

    assert Finding.model_validate(data).line is None


@pytest.mark.parametrize(
    "change",
    [
        {"overall_risk_score": "SEVERE"},
        {"summary": None},
        {"security_vulnerabilities": [{"title": "no other fields"}]},
        {"security_vulnerabilities": [SQL_INJECTION_REPLY["security_vulnerabilities"][0] | {"line": 0}]},
        {"security_vulnerabilities": [SQL_INJECTION_REPLY["security_vulnerabilities"][0] | {"severity": "URGENT"}]},
        {"security_vulnerabilities": "SQL injection on line 12"},
    ],
)
def test_bad_replies_are_rejected(change: dict):
    with pytest.raises(ValidationError):
        CodeAuditReport.model_validate(SQL_INJECTION_REPLY | change)


def test_missing_summary_is_rejected():
    data = {k: v for k, v in SQL_INJECTION_REPLY.items() if k != "summary"}

    with pytest.raises(ValidationError):
        CodeAuditReport.model_validate(data)


def test_unknown_keys_are_ignored():
    report = CodeAuditReport.model_validate(SQL_INJECTION_REPLY | {"confidence": 0.9})

    assert not hasattr(report, "confidence")


def test_json_schema_guides_the_model():
    # `with_structured_output` sends this schema to the model, so the
    # descriptions and allowed values are part of the prompt.
    schema = CodeAuditReport.model_json_schema()
    finding = schema["$defs"]["Finding"]

    assert set(schema["required"]) == {"summary", "overall_risk_score"}
    assert schema["$defs"]["RiskLevel"]["enum"] == ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    assert all(prop.get("description") for prop in schema["properties"].values())
    assert all(prop.get("description") for prop in finding["properties"].values())


def test_findings_dump_to_plain_dicts_for_the_state():
    report = CodeAuditReport.model_validate(SQL_INJECTION_REPLY)

    [dumped] = report.model_dump(mode="json")["security_vulnerabilities"]

    # Plain strings, so the state can be stored as JSON (checkpoints, Unit 4).
    assert dumped["severity"] == "HIGH"
    assert type(dumped["severity"]) is str


# --- PRAnalysisState ---------------------------------------------------------


def test_list_fields_have_add_reducers():
    hints = get_type_hints(PRAnalysisState, include_extras=True)

    for field in ("syntax_issues", "security_vulnerabilities"):
        assert get_origin(hints[field]) is Annotated
        assert get_args(hints[field])[1] is operator.add


def test_state_has_the_expected_fields():
    assert set(get_type_hints(PRAnalysisState)) == {
        "job_id",
        "repo_name",
        "pr_number",
        "head_sha",
        "title",
        "diff_text",
        "diff_truncated",
        "syntax_issues",
        "security_vulnerabilities",
        "analysis_summary",
        "overall_risk_score",
        "final_summary",
    }


def finding(title: str) -> dict:
    return SQL_INJECTION_REPLY["security_vulnerabilities"][0] | {"title": title}


def build_fan_out_graph(update_a: dict, update_b: dict):
    """START runs nodes `a` and `b` in the same step, then both end."""
    graph = StateGraph(PRAnalysisState)
    graph.add_node("a", lambda state: update_a)
    graph.add_node("b", lambda state: update_b)
    graph.add_edge(START, "a")
    graph.add_edge(START, "b")
    graph.add_edge("a", END)
    graph.add_edge("b", END)
    return graph.compile()


@pytest.mark.asyncio
async def test_reducer_combines_findings_from_parallel_nodes():
    app = build_fan_out_graph(
        {"security_vulnerabilities": [finding("from a")]},
        {"security_vulnerabilities": [finding("from b")]},
    )

    result = await app.ainvoke({"security_vulnerabilities": [finding("initial")]})

    titles = sorted(f["title"] for f in result["security_vulnerabilities"])
    assert titles == ["from a", "from b", "initial"]


@pytest.mark.asyncio
async def test_field_without_reducer_rejects_two_writes_in_one_step():
    # This is why the list fields need reducers: without one, LangGraph
    # can't tell which of two parallel writes should win.
    app = build_fan_out_graph({"overall_risk_score": "LOW"}, {"overall_risk_score": "HIGH"})

    with pytest.raises(InvalidUpdateError):
        await app.ainvoke({"job_id": "example-job-id"})
