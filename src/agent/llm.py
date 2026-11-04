"""Builds the chat model the agent uses, chosen by `LLM_PROVIDER`.

This is the only module that knows which providers exist. Nodes call
`get_chat_model()` and use the result through LangChain's `BaseChatModel`
interface (`ainvoke`, `with_structured_output`), so switching to a cloud model
means adding a builder here and changing one setting.
"""

from collections.abc import Callable

from langchain_core.language_models import BaseChatModel

from src.config import Settings, get_settings


class LLMConfigError(ValueError):
    """The LLM settings don't describe a model this app can build."""


def _build_ollama(settings: Settings) -> BaseChatModel:
    # Imported here, not at the top, so a provider's package is only needed
    # when that provider is selected.
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=settings.LLM_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        # As repeatable as the model allows, so the same diff gets a similar review.
        temperature=0,
        num_ctx=settings.LLM_NUM_CTX,
        # Passed to the underlying httpx clients. Without it, a stuck Ollama
        # would hold the worker's message forever.
        client_kwargs={"timeout": settings.LLM_TIMEOUT_SECONDS},
    )


_BUILDERS: dict[str, Callable[[Settings], BaseChatModel]] = {
    "ollama": _build_ollama,
}

SUPPORTED_PROVIDERS = tuple(sorted(_BUILDERS))


def get_chat_model(settings: Settings | None = None) -> BaseChatModel:
    """Return a new chat model for the configured provider.

    Raises `LLMConfigError` if `LLM_PROVIDER` names an unknown provider.
    """
    if settings is None:
        settings = get_settings()
    provider = settings.LLM_PROVIDER.strip().lower()

    builder = _BUILDERS.get(provider)
    if builder is None:
        raise LLMConfigError(
            f"Unknown LLM_PROVIDER {settings.LLM_PROVIDER!r}. "
            f"Supported: {', '.join(SUPPORTED_PROVIDERS)}."
        )
    return builder(settings)
