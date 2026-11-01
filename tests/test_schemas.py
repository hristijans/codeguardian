import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.schemas import JobResponse, JobStatus, JobStatusDetails, PRWebhookPayload

FIXTURE = Path(__file__).parent / "fixtures" / "pull_request_opened.json"


@pytest.fixture
def raw_payload() -> dict:
    return json.loads(FIXTURE.read_text())


def make_job(**overrides) -> JobStatusDetails:
    fields = {
        "job_id": "example-job-id",
        "status": JobStatus.QUEUED,
        "repo_name": "example-org/example-repo",
        "pr_number": 42,
        "head_sha": "abc123",
        "title": "Add login form",
        "created_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
    }
    return JobStatusDetails(**(fields | overrides))


def test_payload_reads_the_fields_a_review_needs(raw_payload: dict):
    payload = PRWebhookPayload.model_validate(raw_payload)

    assert payload.action == "opened"
    assert payload.repository.full_name == "example-org/example-repo"
    assert payload.pull_request.number == 42
    assert payload.pull_request.title == "Add login form"
    assert payload.pull_request.head.sha == (
        "0000000000000000000000000000000000000abc"
    )


def test_payload_keeps_only_declared_fields(raw_payload: dict):
    payload = PRWebhookPayload.model_validate(raw_payload)

    assert "sender" not in payload.model_dump()


@pytest.mark.parametrize("action", ["closed", "labeled", "edited"])
def test_payload_accepts_actions_that_are_not_reviewed(
    raw_payload: dict, action: str
):
    # The route decides what to skip. A rejected payload would give GitHub a 422.
    raw_payload["action"] = action

    assert PRWebhookPayload.model_validate(raw_payload).action == action


@pytest.mark.parametrize(
    "path",
    [
        ("action",),
        ("repository",),
        ("repository", "full_name"),
        ("pull_request",),
        ("pull_request", "number"),
        ("pull_request", "title"),
        ("pull_request", "head"),
        ("pull_request", "head", "sha"),
    ],
)
def test_payload_rejects_missing_required_field(raw_payload: dict, path: tuple):
    broken = copy.deepcopy(raw_payload)
    target = broken
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]

    with pytest.raises(ValidationError):
        PRWebhookPayload.model_validate(broken)


def test_job_response_shows_status_as_plain_text():
    response = JobResponse(job_id="example-job-id", status=JobStatus.QUEUED)

    assert response.model_dump(mode="json") == {
        "job_id": "example-job-id",
        "status": "queued",
    }


@pytest.mark.parametrize(
    ("status", "text"),
    [
        (JobStatus.QUEUED, "queued"),
        (JobStatus.PROCESSING, "processing"),
        (JobStatus.COMPLETED, "completed"),
        (JobStatus.FAILED, "failed"),
    ],
)
def test_job_accepts_each_status_as_text(status: JobStatus, text: str):
    assert make_job(status=text).status is status


def test_job_rejects_unknown_status():
    with pytest.raises(ValidationError):
        make_job(status="paused")


def test_job_has_no_error_until_one_is_set():
    assert make_job().error is None


def test_job_survives_a_json_round_trip():
    # Redis stores text, so step 2.5 relies on this.
    job = make_job(status=JobStatus.FAILED, error="Diff fetch failed")

    restored = JobStatusDetails.model_validate_json(job.model_dump_json())

    assert restored == job
    assert restored.status is JobStatus.FAILED
    assert restored.created_at.tzinfo is not None
