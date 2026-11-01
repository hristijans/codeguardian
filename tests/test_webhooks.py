import hashlib
import hmac
import json
from pathlib import Path
from typing import Optional

import pytest
from httpx import AsyncClient, Response
from pydantic import SecretStr

from src.config import Settings, get_settings
from src.main import app

URL = "/api/v1/webhooks/github/pr"
SECRET = "example-webhook-secret"
FIXTURE = Path(__file__).parent / "fixtures" / "pull_request_opened.json"


@pytest.fixture(autouse=True)
def webhook_secret(settings: Settings):
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={
            "GITHUB_WEBHOOK_SECRET": SecretStr(SECRET),
            "REVIEW_REPOSITORIES": "example-org/example-repo",
        }
    )
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def raw_payload() -> dict:
    return json.loads(FIXTURE.read_text())


def sign(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def deliver(
    client: AsyncClient,
    body: bytes,
    event: Optional[str] = "pull_request",
    signature: Optional[str] = None,
) -> Response:
    headers = {"Content-Type": "application/json"}
    headers["X-Hub-Signature-256"] = signature or sign(body)
    if event is not None:
        headers["X-GitHub-Event"] = event
    return await client.post(URL, content=body, headers=headers)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["opened", "reopened", "synchronize"])
async def test_reviewed_action_is_accepted_as_a_queued_job(
    async_client: AsyncClient, raw_payload: dict, action: str
):
    raw_payload["action"] = action

    response = await deliver(async_client, json.dumps(raw_payload).encode())

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "queued"
    assert data["job_id"]


@pytest.mark.asyncio
async def test_each_delivery_gets_its_own_job_id(
    async_client: AsyncClient, raw_payload: dict
):
    body = json.dumps(raw_payload).encode()

    first = await deliver(async_client, body)
    second = await deliver(async_client, body)

    assert first.json()["job_id"] != second.json()["job_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["closed", "labeled", "edited"])
async def test_unreviewed_action_creates_no_job(
    async_client: AsyncClient, raw_payload: dict, action: str
):
    raw_payload["action"] = action

    response = await deliver(async_client, json.dumps(raw_payload).encode())

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


@pytest.mark.asyncio
async def test_ping_is_acknowledged_without_a_job(async_client: AsyncClient):
    # A ping has no pull_request key, so it must never reach the payload model.
    body = json.dumps({"zen": "Example.", "hook_id": 1}).encode()

    response = await deliver(async_client, body, event="ping")

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["push", "issues", None])
async def test_other_events_create_no_job(
    async_client: AsyncClient, raw_payload: dict, event: Optional[str]
):
    # Even a full pull request body is skipped when the event type differs.
    body = json.dumps(raw_payload).encode()

    response = await deliver(async_client, body, event=event)

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


@pytest.mark.asyncio
async def test_signature_is_checked_over_the_exact_bytes_received(
    async_client: AsyncClient, raw_payload: dict
):
    # Re-encoding this body would lose the spacing and break the signature.
    body = json.dumps(raw_payload, indent=4, sort_keys=True).encode() + b"\n"

    response = await deliver(async_client, body)

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_bad_signature_is_rejected(
    async_client: AsyncClient, raw_payload: dict
):
    body = json.dumps(raw_payload).encode()

    response = await deliver(async_client, body, signature="sha256=" + "0" * 64)

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_signature_is_rejected(
    async_client: AsyncClient, raw_payload: dict
):
    response = await async_client.post(
        URL,
        content=json.dumps(raw_payload).encode(),
        headers={"X-GitHub-Event": "pull_request"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bad_signature_is_rejected_before_the_body_is_parsed(
    async_client: AsyncClient,
):
    # 401 rather than 422 shows that untrusted data was never parsed.
    response = await deliver(
        async_client, b"not json", signature="sha256=" + "0" * 64
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bad_signature_is_rejected_for_ignored_events(
    async_client: AsyncClient,
):
    response = await deliver(
        async_client, b"{}", event="ping", signature="sha256=" + "0" * 64
    )

    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [b"not json", b"{}", b'{"action": "opened"}'], ids=["text", "empty", "partial"]
)
async def test_signed_pull_request_with_a_bad_body_is_rejected(
    async_client: AsyncClient, body: bytes
):
    response = await deliver(async_client, body)

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid payload"}


@pytest.mark.asyncio
async def test_repository_not_on_the_list_creates_no_job(
    async_client: AsyncClient, raw_payload: dict
):
    raw_payload["repository"]["full_name"] = "example-org/another-repo"

    response = await deliver(async_client, json.dumps(raw_payload).encode())

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


@pytest.mark.asyncio
async def test_repository_match_ignores_case(
    async_client: AsyncClient, raw_payload: dict
):
    raw_payload["repository"]["full_name"] = "Example-Org/Example-Repo"

    response = await deliver(async_client, json.dumps(raw_payload).encode())

    assert response.status_code == 202


@pytest.mark.asyncio
async def test_empty_list_reviews_nothing(
    async_client: AsyncClient, raw_payload: dict, settings: Settings
):
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={
            "GITHUB_WEBHOOK_SECRET": SecretStr(SECRET),
            "REVIEW_REPOSITORIES": "",
        }
    )

    response = await deliver(async_client, json.dumps(raw_payload).encode())

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
