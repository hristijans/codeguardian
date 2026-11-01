import hashlib
import hmac
from typing import Optional


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
