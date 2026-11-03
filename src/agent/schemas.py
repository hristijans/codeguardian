"""The structured review the LLM must return.

`with_structured_output(CodeAuditReport)` sends this model's JSON schema to the
LLM, so the field descriptions below are part of the prompt. Whatever comes
back is validated here, and a reply that doesn't fit is rejected rather than
trusted.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskLevel(StrEnum):
    """The same four levels as `Severity`, rating the pull request as a whole."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


def _normalize_level(value: Any) -> Any:
    # Small models often write "high" or " High". Anything that still isn't a
    # known level fails the enum check afterwards.
    return value.strip().upper() if isinstance(value, str) else value


class Finding(BaseModel):
    file: str = Field(description="Path of the changed file, as shown in the diff.")
    line: int | None = Field(
        default=None,
        ge=1,
        description="Line number in the new version of the file, or null if unknown.",
    )
    severity: Severity = Field(description="How serious the problem is.")
    title: str = Field(description="A short name for the problem, such as 'SQL injection'.")
    explanation: str = Field(description="Why this is a problem, in one or two sentences.")
    suggestion: str | None = Field(default=None, description="How to fix it, or null.")

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, value: Any) -> Any:
        return _normalize_level(value)


class CodeAuditReport(BaseModel):
    summary: str = Field(description="Two or three sentences on what the change does and how safe it is.")
    overall_risk_score: RiskLevel = Field(description="The risk of merging this change as it is.")
    syntax_issues: list[Finding] = Field(
        default_factory=list,
        description="Bugs, syntax errors, and code-quality problems. Empty if none.",
    )
    security_vulnerabilities: list[Finding] = Field(
        default_factory=list,
        description="Security problems, such as injection or leaked secrets. Empty if none.",
    )

    @field_validator("overall_risk_score", mode="before")
    @classmethod
    def _normalize_risk(cls, value: Any) -> Any:
        return _normalize_level(value)
