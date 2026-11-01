from datetime import datetime
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel


class GitHubRepository(BaseModel):
    full_name: str


class GitHubCommitRef(BaseModel):
    sha: str


class GitHubPullRequest(BaseModel):
    number: int
    title: str
    head: GitHubCommitRef


class PRWebhookPayload(BaseModel):
    """The parts of a GitHub `pull_request` event that a review needs.

    GitHub sends far more than this. Undeclared fields are ignored, and `action`
    stays a plain string so that the route can skip actions it doesn't review.
    """

    action: str
    repository: GitHubRepository
    pull_request: GitHubPullRequest


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus


class JobStatusDetails(BaseModel):
    job_id: str
    status: JobStatus
    repo_name: str
    pr_number: int
    head_sha: str
    title: str
    created_at: datetime
    updated_at: datetime
    error: Optional[str] = None
