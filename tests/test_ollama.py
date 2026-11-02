"""Checks that the containers can reach the local Ollama server.

These need Ollama running on the host with the configured model pulled, so
they carry the `ollama` marker. `pytest -m "not ollama"` skips them, for
example in CI, which has no local model.
"""

import socket
from urllib.parse import urlparse

import httpx
import pytest

from src.config import Settings

pytestmark = pytest.mark.ollama


def test_ollama_host_resolves(settings: Settings):
    # On Linux, `host.docker.internal` exists inside a container only if
    # Compose maps it with `extra_hosts`.
    host = urlparse(settings.OLLAMA_BASE_URL).hostname
    assert socket.gethostbyname(host)


@pytest.mark.asyncio
async def test_ollama_server_answers(settings: Settings):
    async with httpx.AsyncClient(base_url=settings.OLLAMA_BASE_URL, timeout=5) as client:
        response = await client.get("/api/version")

    assert response.status_code == 200
    assert response.json()["version"]


@pytest.mark.asyncio
async def test_configured_model_is_pulled(settings: Settings):
    async with httpx.AsyncClient(base_url=settings.OLLAMA_BASE_URL, timeout=5) as client:
        response = await client.get("/api/tags")

    names = {model["name"] for model in response.json()["models"]}
    # Ollama adds `:latest` to a name pulled without a tag.
    wanted = settings.LLM_MODEL if ":" in settings.LLM_MODEL else f"{settings.LLM_MODEL}:latest"
    assert wanted in names


@pytest.mark.asyncio
async def test_model_replies_to_a_prompt(settings: Settings):
    # A short reply keeps this to a few seconds on a CPU. Loading the model
    # into memory the first time takes most of it.
    async with httpx.AsyncClient(base_url=settings.OLLAMA_BASE_URL, timeout=120) as client:
        response = await client.post(
            "/api/chat",
            json={
                "model": settings.LLM_MODEL,
                "messages": [{"role": "user", "content": "Reply with the word ready."}],
                "stream": False,
                "options": {"num_predict": 10, "temperature": 0},
            },
        )

    assert response.status_code == 200
    reply = response.json()["message"]["content"]
    assert reply.strip()
