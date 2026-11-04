"""Tests for the assembled review graph in `src/agent/graph.py`."""

import pytest

from src.agent.diff import limit_diff
from src.agent.graph import build_graph
from tests.test_agent_nodes import (
    CLEAN_REPLY,
    DIFFS,
    SQL_INJECTION_REPLY,
    FakeStructuredModel,
    make_state,
)


def initial_state(diff_name: str = "sql_injection.diff", **overrides) -> dict:
    """What the worker will hand the graph: the job's fields, and no results yet."""
    state = make_state(diff_name, **overrides)
    for key in ("syntax_issues", "security_vulnerabilities"):
        state.pop(key)
    return state


@pytest.mark.asyncio
async def test_graph_runs_from_diff_to_markdown_report():
    graph = build_graph(FakeStructuredModel(SQL_INJECTION_REPLY))

    result = await graph.ainvoke(initial_state())

    assert result["overall_risk_score"] == "HIGH"
    assert result["security_vulnerabilities"][0]["title"] == "SQL injection"
    report = result["final_summary"]
    assert report.startswith("## CodeGuardian review")
    assert "`app/users.py:11`" in report


@pytest.mark.asyncio
async def test_clean_diff_gives_a_clean_report():
    graph = build_graph(FakeStructuredModel(CLEAN_REPLY))

    result = await graph.ainvoke(initial_state("clean.diff"))

    assert result["security_vulnerabilities"] == [] and result["syntax_issues"] == []
    assert "No problems found" in result["final_summary"]


@pytest.mark.asyncio
async def test_empty_diff_skips_the_model_but_still_gets_a_report():
    model = FakeStructuredModel(CLEAN_REPLY)

    result = await build_graph(model).ainvoke(initial_state(diff_name=None))

    assert model.calls == []
    assert "no text changes" in result["final_summary"]


@pytest.mark.asyncio
async def test_oversized_diff_gives_a_partial_report():
    big = "".join(f"+line {i:04d}\n" for i in range(2000))
    limited = limit_diff(big, max_chars=500)
    model = FakeStructuredModel(CLEAN_REPLY)

    result = await build_graph(model).ainvoke(
        initial_state(diff_name=None, diff_text=limited.text, diff_truncated=limited.truncated)
    )

    assert "truncated" in model.calls[0][1].content.lower()
    assert "Partial review" in result["final_summary"]
    assert "No problems found" not in result["final_summary"]


@pytest.mark.asyncio
async def test_prompt_injection_diff_still_reports_the_finding():
    # The fake model answers as an obedient-to-the-schema reviewer; this checks
    # the injection text only reaches the model inside its block.
    model = FakeStructuredModel(SQL_INJECTION_REPLY)

    result = await build_graph(model).ainvoke(initial_state("prompt_injection.diff"))

    assert (DIFFS / "prompt_injection.diff").read_text().count("Ignore all previous") == 1
    assert model.calls[0][1].content.count("Ignore all previous instructions") == 1
    assert result["security_vulnerabilities"]


@pytest.mark.asyncio
async def test_model_is_called_once_per_review():
    model = FakeStructuredModel(CLEAN_REPLY)

    await build_graph(model).ainvoke(initial_state())

    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_each_run_starts_with_fresh_findings():
    graph = build_graph(FakeStructuredModel(SQL_INJECTION_REPLY))

    first = await graph.ainvoke(initial_state())
    second = await graph.ainvoke(initial_state())

    assert len(first["security_vulnerabilities"]) == 1
    assert len(second["security_vulnerabilities"]) == 1


@pytest.mark.asyncio
async def test_an_unusable_reply_fails_the_run_with_analysis_error():
    from src.agent.nodes import AnalysisError

    graph = build_graph(FakeStructuredModel("not json"))

    with pytest.raises(AnalysisError):
        await graph.ainvoke(initial_state())


def test_graph_has_the_two_nodes_in_order():
    drawn = build_graph(FakeStructuredModel(CLEAN_REPLY)).get_graph()

    edges = {(e.source, e.target) for e in drawn.edges}
    assert edges == {
        ("__start__", "analyze_diff"),
        ("analyze_diff", "format_report"),
        ("format_report", "__end__"),
    }


def test_default_model_comes_from_the_factory(monkeypatch):
    fake = FakeStructuredModel(CLEAN_REPLY)
    monkeypatch.setattr("src.agent.graph.get_chat_model", lambda: fake)

    build_graph()

    assert fake.schemas  # the analysis node asked the factory's model for structured output


@pytest.mark.ollama
@pytest.mark.asyncio
async def test_real_model_graph_produces_a_report_for_the_sql_injection():
    from src.agent.llm import get_chat_model

    result = await build_graph(get_chat_model()).ainvoke(initial_state())

    assert result["security_vulnerabilities"], result
    assert result["final_summary"].startswith("## CodeGuardian review")
    assert "Security vulnerabilities" in result["final_summary"]
