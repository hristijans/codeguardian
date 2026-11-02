import hashlib
import hmac
import re
from typing import Optional

import httpx
from pydantic import SecretStr


def verify_signature(
    body: bytes, signature_header: Optional[str], secret: str
) -> bool:
    """Check a webhook's X-Hub-Signature-256 header against its raw body.

    Pass the bytes exactly as received, before any parsing. An empty secret
    always fails, because anyone could compute a signature with it.
    """
    if not secret or not signature_header:
        return False

    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"

    # compare_digest takes the same time wherever the values differ. Bytes are
    # used because it raises TypeError on non-ASCII strings.
    return hmac.compare_digest(expected.encode(), signature_header.encode())


GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"

# Owners use letters, digits, and hyphens, and repository names add dots and
# underscores. This is looser than GitHub's exact rules on purpose: its job is
# to keep slashes, `?`, spaces, and the like out of the URL path.
_REPO_NAME = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")


class GitHubError(Exception):
    """A GitHub API call failed.

    `status_code` is the HTTP status, or None when no response arrived. The
    message never includes the response body, which could echo the request.
    """

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubClient:
    """A small GitHub REST API client authenticated with a token.

    Only this class knows how requests are authenticated, so a GitHub App can
    replace the token later without changing its callers. Use it with
    `async with`, which closes its connection pool afterwards. `transport`
    lets tests answer requests without the network.
    """

    def __init__(
        self,
        token: SecretStr,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._token = token
        self._http = httpx.AsyncClient(
            base_url=GITHUB_API_URL,
            headers={
                "User-Agent": "codeguardian",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
            # GitHub answers a renamed repository with a 301. httpx doesn't
            # follow redirects unless told to, and it drops the Authorization
            # header if a redirect leads to a different host.
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
            transport=transport,
        )

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._http.aclose()

    async def fetch_diff(self, repo_name: str, pr_number: int) -> str:
        """Return a pull request's diff as unified diff text.

        Webhooks don't include the diff, so it is fetched here. This is the
        pull request as it is now, which may be newer than the webhook's
        head commit.
        """
        if not _REPO_NAME.fullmatch(repo_name) or repo_name.split("/")[1] in {".", ".."}:
            raise ValueError("Repository name must be owner/name")
        if pr_number < 1:
            raise ValueError("Pull request number must be positive")

        try:
            response = await self._http.get(
                f"/repos/{repo_name}/pulls/{pr_number}",
                headers={
                    "Accept": "application/vnd.github.diff",
                    # Built for each request rather than set once on the
                    # client, so a GitHub App's short-lived token could be
                    # fetched fresh here without changing anything else.
                    "Authorization": f"Bearer {self._token.get_secret_value()}",
                },
            )
        except httpx.HTTPError as error:
            # Only the exception type: its text can include the request URL.
            raise GitHubError(
                f"Could not reach GitHub: {type(error).__name__}"
            ) from None

        if response.is_error:
            raise GitHubError(
                f"GitHub returned {response.status_code} for {repo_name}#{pr_number}",
                status_code=response.status_code,
            )
        return response.text
