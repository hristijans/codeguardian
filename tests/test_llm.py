"""Tests for the LLM factory in `src/agent/llm.py`.

Most of these build the model object without calling it, so they need no
running Ollama. The last test makes a real call and carries the `ollama`
marker.
"""

import ast
from pathlib import Path

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama

from src.agent.llm import SUPPORTED_PROVIDERS, LLMConfigError, get_chat_model
from src.config import Settings

SRC = Path(__file__).resolve().parent.parent / "src"

# Packages that talk to one specific provider. Only the factory may import them.
PROVIDER_PACKAGES = {"langchain_ollama", "langchain_openai", "langchain_anthropic", "ollama"}


def with_settings(settings: Settings, **changes) -> Settings:
    return settings.model_copy(update=changes)


def test_ollama_provider_returns_configured_chat_ollama(settings: Settings):
    model = get_chat_model(with_settings(settings, LLM_PROVIDER="ollama"))

    assert isinstance(model, ChatOllama)
    assert model.model == settings.LLM_MODEL
    assert model.base_url == settings.OLLAMA_BASE_URL
    # Reviews should be as repeatable as the model allows.
    assert model.temperature == 0


def test_timeout_reaches_the_http_client(settings: Settings):
    model = get_chat_model(with_settings(settings, LLM_TIMEOUT_SECONDS=42.0))

    assert model.client_kwargs["timeout"] == 42.0


def test_context_window_is_set_explicitly(settings: Settings):
    # Ollama cuts a prompt longer than its window without an error, so the
    # window is a setting rather than whatever Ollama defaults to.
    model = get_chat_model(with_settings(settings, LLM_NUM_CTX=2048))

    assert model.num_ctx == 2048


def test_returned_model_is_the_generic_type(settings: Settings):
    # Nodes only rely on the LangChain interface, not on ChatOllama.
    assert isinstance(get_chat_model(settings), BaseChatModel)


@pytest.mark.parametrize("name", ["Ollama", " ollama ", "OLLAMA"])
def test_provider_name_ignores_case_and_spaces(settings: Settings, name: str):
    assert isinstance(get_chat_model(with_settings(settings, LLM_PROVIDER=name)), ChatOllama)


@pytest.mark.parametrize("name", ["openai", "", "olama"])
def test_unknown_provider_is_rejected(settings: Settings, name: str):
    with pytest.raises(LLMConfigError) as error:
        get_chat_model(with_settings(settings, LLM_PROVIDER=name))

    message = str(error.value)
    assert repr(name) in message
    for supported in SUPPORTED_PROVIDERS:
        assert supported in message


def test_defaults_to_app_settings():
    # With no argument it reads the cached app settings, like a FastAPI dependency.
    assert isinstance(get_chat_model(), BaseChatModel)


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_only_the_factory_imports_provider_packages():
    offenders = [
        str(path.relative_to(SRC.parent))
        for path in SRC.rglob("*.py")
        if path != SRC / "agent" / "llm.py" and imported_modules(path) & PROVIDER_PACKAGES
    ]

    assert offenders == []


def test_import_guard_sees_the_factory_itself():
    # If this failed, the guard above would pass for the wrong reason.
    assert "langchain_ollama" in imported_modules(SRC / "agent" / "llm.py")


@pytest.mark.ollama
@pytest.mark.asyncio
async def test_factory_model_answers_a_prompt(settings: Settings):
    # A copy with a 10-token limit keeps the call to a few seconds.
    model = get_chat_model(settings).model_copy(update={"num_predict": 10})

    reply = await model.ainvoke("Reply with the word ready.")

    assert reply.content.strip()
