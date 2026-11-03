"""Tests for the graph nodes in `src/agent/nodes.py`.

The LLM is replaced by `FakeStructuredModel`, which answers with fixed JSON
text, so these run in milliseconds and give the same result every time. The
last test uses the real model and carries the `ollama` marker.
"""

import json
from pathlib import Path

import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, ValidationError

from src.agent.llm import get_chat_model
from src.agent.nodes import AnalysisError, make_analyze_diff_node
from src.agent.schemas import CodeAuditReport

DIFFS = Path(__file__).parent / "fixtures" / "diffs"

SQL_INJECTION_REPLY = {
    "summary": "find_user now builds its SQL query from the username.",
    "overall_risk_score": "HIGH",
    "syntax_issues": [],
    "security_vulnerabilities": [
        {
            "file": "app/users.py",
            "line": 11,
            "severity": "HIGH",
            "title": "SQL injection",
            "explanation": "The username is put into the query with an f-string.",
            "suggestion": "Keep the parameterized query.",
        }
    ],
}

CLEAN_REPLY = {
    "summary": "Adds a docstring and a farewell function.",
    "overall_risk_score": "LOW",
    "syntax_issues": [],
    "security_vulnerabilities": [],
}


class FakeStructuredModel:
    """Stands in for a chat model in the nodes' tests.

    `with_structured_output(schema)` returns a runnable that records the
    messages it gets and parses the canned reply text into `schema`, raising
    `OutputParserException` as `ChatOllama`'s parser does when it doesn't fit.
    """

    def __init__(self, reply: str | dict):
        self.reply = reply if isinstance(reply, str) else json.dumps(reply)
        self.calls: list[list[BaseMessage]] = []
        self.schemas: list[type[BaseModel]] = []

    def with_structured_output(self, schema: type[BaseModel], **kwargs):
        self.schemas.append(schema)

        def answer(messages: list[BaseMessage]) -> BaseModel:
            self.calls.append(messages)
            try:
                return schema.model_validate_json(self.reply)
            except ValidationError as error:
                raise OutputParserException(str(error), llm_output=self.reply) from error

        return RunnableLambda(answer)


def make_state(diff_name: str = "sql_injection.diff", **overrides) -> dict:
    state = {
        "job_id": "example-job-id",
        "repo_name": "example-org/example-repo",
        "pr_number": 42,
        "head_sha": "abc123",
        "title": "Look up users by name",
        "diff_text": (DIFFS / diff_name).read_text() if diff_name else "",
        "diff_truncated": False,
        "syntax_issues": [],
        "security_vulnerabilities": [],
    }
    return state | overrides


# --- Results -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_sql_injection_diff_gives_a_security_finding():
    node = make_analyze_diff_node(FakeStructuredModel(SQL_INJECTION_REPLY))

    update = await node(make_state())

    assert update["overall_risk_score"] == "HIGH"
    assert update["analysis_summary"] == SQL_INJECTION_REPLY["summary"]
    assert update["syntax_issues"] == []
    [finding] = update["security_vulnerabilities"]
    assert finding == SQL_INJECTION_REPLY["security_vulnerabilities"][0]


@pytest.mark.asyncio
async def test_clean_diff_gives_no_findings():
    node = make_analyze_diff_node(FakeStructuredModel(CLEAN_REPLY))

    update = await node(make_state("clean.diff"))

    assert update == {
        "analysis_summary": CLEAN_REPLY["summary"],
        "overall_risk_score": "LOW",
        "syntax_issues": [],
        "security_vulnerabilities": [],
    }


@pytest.mark.asyncio
async def test_findings_are_plain_json_values():
    reply = SQL_INJECTION_REPLY | {"overall_risk_score": "high"}
    reply["security_vulnerabilities"] = [reply["security_vulnerabilities"][0] | {"severity": "high"}]
    node = make_analyze_diff_node(FakeStructuredModel(reply))

    update = await node(make_state())

    # Normalized by the schema, then stored as plain strings, not enums.
    assert update["overall_risk_score"] == "HIGH"
    assert type(update["overall_risk_score"]) is str
    severity = update["security_vulnerabilities"][0]["severity"]
    assert severity == "HIGH"
    assert type(severity) is str


@pytest.mark.asyncio
async def test_node_returns_only_the_keys_it_owns():
    node = make_analyze_diff_node(FakeStructuredModel(CLEAN_REPLY))

    update = await node(make_state("clean.diff"))

    assert set(update) == {"analysis_summary", "overall_risk_score", "syntax_issues", "security_vulnerabilities"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_risk", "severities", "expected"),
    [
        ("MEDIUM", ["HIGH"], "HIGH"),  # seen from llama3.2: a HIGH finding rated MEDIUM overall
        ("LOW", ["LOW", "CRITICAL"], "CRITICAL"),
        ("HIGH", ["LOW"], "HIGH"),  # never lowered
        ("MEDIUM", [], "MEDIUM"),
    ],
)
async def test_overall_risk_is_at_least_the_worst_finding(model_risk, severities, expected):
    finding = SQL_INJECTION_REPLY["security_vulnerabilities"][0]
    reply = CLEAN_REPLY | {
        "overall_risk_score": model_risk,
        "syntax_issues": [finding | {"severity": s} for s in severities],
    }

    update = await make_analyze_diff_node(FakeStructuredModel(reply))(make_state())

    assert update["overall_risk_score"] == expected


# --- What the model is sent --------------------------------------------------


@pytest.mark.asyncio
async def test_model_is_asked_once_for_a_code_audit_report():
    model = FakeStructuredModel(CLEAN_REPLY)

    await make_analyze_diff_node(model)(make_state())

    assert model.schemas == [CodeAuditReport]
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_messages_are_system_prompt_then_the_pull_request():
    model = FakeStructuredModel(CLEAN_REPLY)
    state = make_state()

    await make_analyze_diff_node(model)(state)

    [messages] = model.calls
    assert [type(m) for m in messages] == [SystemMessage, HumanMessage]
    system, human = (m.content for m in messages)
    assert "security" in system.lower()
    # The diff goes in unchanged, between markers, with the title.
    assert state["diff_text"] in human
    assert human.index("<diff>") < human.index(state["diff_text"]) < human.index("</diff>")
    assert state["title"] in human


@pytest.mark.asyncio
async def test_system_prompt_explains_line_numbers():
    # In an informal try at step 3.3, the model left `line` empty.
    model = FakeStructuredModel(CLEAN_REPLY)

    await make_analyze_diff_node(model)(make_state())

    system = model.calls[0][0].content
    assert "@@" in system
    assert "line" in system.lower()


# --- Failures and edge cases -------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "not json at all",
        json.dumps({"summary": "no risk score"}),
        json.dumps(CLEAN_REPLY | {"overall_risk_score": "SEVERE"}),
    ],
)
async def test_unusable_reply_raises_analysis_error(reply: str):
    node = make_analyze_diff_node(FakeStructuredModel(reply))

    with pytest.raises(AnalysisError) as error:
        await node(make_state())

    # The worker marks a job `failed` on ValueError (D23), without retrying.
    assert isinstance(error.value, ValueError)
    # The reply may contain diff content, so it stays out of the message.
    assert reply not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("diff", ["", "   \n\n"])
async def test_empty_diff_skips_the_model(diff: str):
    model = FakeStructuredModel("never used")

    update = await make_analyze_diff_node(model)(make_state(diff_name=None, diff_text=diff))

    assert model.calls == []
    assert update["overall_risk_score"] == "LOW"
    assert update["syntax_issues"] == [] and update["security_vulnerabilities"] == []
    assert update["analysis_summary"]


# --- The real model ----------------------------------------------------------


@pytest.mark.ollama
@pytest.mark.asyncio
async def test_real_model_finds_the_sql_injection():
    node = make_analyze_diff_node(get_chat_model())

    update = await node(make_state())

    assert update["security_vulnerabilities"], update
    assert update["overall_risk_score"] in {"MEDIUM", "HIGH", "CRITICAL"}
