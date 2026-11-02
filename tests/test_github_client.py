import httpx
import pytest
from pydantic import SecretStr

from src.github import GITHUB_API_URL, GitHubClient, GitHubError

TOKEN = "example-token"
DIFF = "diff --git a/app.py b/app.py\n+print('hello')\n"
PR_PATH = "/repos/example-org/example-repo/pulls/42"


class FakeGitHub:
    """Stands in for api.github.com: records requests and replies as told."""

    def __init__(self, handler=None):
        self.requests: list[httpx.Request] = []
        self._handler = handler or (lambda request: httpx.Response(200, text=DIFF))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def client_for(fake: FakeGitHub) -> GitHubClient:
    return GitHubClient(SecretStr(TOKEN), transport=httpx.MockTransport(fake))


@pytest.mark.asyncio
async def test_fetch_diff_returns_the_diff_text():
    fake = FakeGitHub()
    async with client_for(fake) as github:
        assert await github.fetch_diff("example-org/example-repo", 42) == DIFF


@pytest.mark.asyncio
async def test_fetch_diff_asks_for_the_pull_request_as_a_diff():
    fake = FakeGitHub()
    async with client_for(fake) as github:
        await github.fetch_diff("example-org/example-repo", 42)

    [request] = fake.requests
    assert request.method == "GET"
    assert str(request.url) == f"{GITHUB_API_URL}{PR_PATH}"
    assert request.headers["Accept"] == "application/vnd.github.diff"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    # GitHub rejects API requests that have no User-Agent.
    assert request.headers["User-Agent"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 422, 500])
async def test_error_status_raises_github_error(status: int):
    fake = FakeGitHub(lambda request: httpx.Response(status, json={"message": "x"}))
    async with client_for(fake) as github:
        with pytest.raises(GitHubError) as caught:
            await github.fetch_diff("example-org/example-repo", 42)

    assert caught.value.status_code == status


@pytest.mark.asyncio
async def test_error_message_never_contains_the_token():
    def echo_auth(request: httpx.Request) -> httpx.Response:
        # A worst case: the server echoes the header back in its error body.
        return httpx.Response(401, text=request.headers["Authorization"])

    async with client_for(FakeGitHub(echo_auth)) as github:
        with pytest.raises(GitHubError) as caught:
            await github.fetch_diff("example-org/example-repo", 42)

    assert TOKEN not in str(caught.value)
    assert TOKEN not in repr(caught.value)


@pytest.mark.asyncio
async def test_network_failure_raises_github_error():
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with client_for(FakeGitHub(fail)) as github:
        with pytest.raises(GitHubError) as caught:
            await github.fetch_diff("example-org/example-repo", 42)

    assert caught.value.status_code is None


@pytest.mark.asyncio
async def test_redirect_for_a_renamed_repository_is_followed():
    new_path = "/repos/example-org/renamed-repo/pulls/42"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == PR_PATH:
            return httpx.Response(
                301, headers={"Location": f"{GITHUB_API_URL}{new_path}"}
            )
        return httpx.Response(200, text=DIFF)

    fake = FakeGitHub(handler)
    async with client_for(fake) as github:
        assert await github.fetch_diff("example-org/example-repo", 42) == DIFF

    assert [r.url.path for r in fake.requests] == [PR_PATH, new_path]
    # Same host, so the token is still sent to the new address.
    assert fake.requests[1].headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repo_name",
    ["example-repo", "a/b/c", "../example-repo", "example-org/..", "/example-repo",
     "example-org/", "example org/repo", "example-org/repo?x=1", ""],
)
async def test_bad_repository_name_is_rejected_without_a_request(repo_name: str):
    # The name ends up in the URL path, so anything but owner/name could
    # point the request somewhere else.
    fake = FakeGitHub()
    async with client_for(fake) as github:
        with pytest.raises(ValueError):
            await github.fetch_diff(repo_name, 42)

    assert fake.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pr_number", [0, -1])
async def test_bad_pull_request_number_is_rejected_without_a_request(pr_number: int):
    fake = FakeGitHub()
    async with client_for(fake) as github:
        with pytest.raises(ValueError):
            await github.fetch_diff("example-org/example-repo", pr_number)

    assert fake.requests == []


@pytest.mark.asyncio
async def test_client_does_not_show_the_token():
    async with client_for(FakeGitHub()) as github:
        assert TOKEN not in repr(github)
        assert TOKEN not in str(vars(github))
