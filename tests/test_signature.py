import pytest

from src.github import verify_signature

# The worked example from GitHub's webhook documentation, also checked with openssl.
BODY = b"Hello, World!"
SECRET = "It's a Secret to Everybody"
VALID_HEADER = (
    "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
)


def test_accepts_github_documented_example():
    assert verify_signature(BODY, VALID_HEADER, SECRET) is True


def test_rejects_changed_body():
    assert verify_signature(b"Hello, World?", VALID_HEADER, SECRET) is False


def test_rejects_wrong_secret():
    assert verify_signature(BODY, VALID_HEADER, "another-secret") is False


def test_signature_covers_exact_bytes_not_json_meaning():
    # Same JSON, different bytes. Parsing before checking would hide this.
    compact = b'{"action":"opened"}'
    spaced = b'{"action": "opened"}'
    # Signature of `compact` under SECRET, computed with openssl.
    header = "sha256=5ccdd8275f57d608741ad8390e42e8696ebfcd85607d9c2ef890769dda8f7568"

    assert verify_signature(compact, header, SECRET) is True
    assert verify_signature(spaced, header, SECRET) is False


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "sha256=",
        "757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17",
        "sha1=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17",
        "sha256=not-a-real-signature",
        "sha256=é",
    ],
)
def test_rejects_missing_or_malformed_header(header):
    assert verify_signature(BODY, header, SECRET) is False


def test_rejects_everything_when_secret_is_empty():
    # Empty secret: anyone could compute this signature, so it must never pass.
    # Computed with openssl using an all-zero key, which HMAC treats as empty.
    forged = "sha256=2bbcfa9524f3218c7a34b30e6936f8b1a4516cb097f1a85a1c7d98b5977ec769"

    assert verify_signature(BODY, forged, "") is False
