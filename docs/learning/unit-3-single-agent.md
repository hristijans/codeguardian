# Unit 3: Single-Agent Code Review

Unit 3 replaces the worker's simulated review with a LangGraph agent that reviews the diff with a local LLM. This file grows as the unit is built, and it only describes what exists so far.

**Files so far:** `docker-compose.yml`, `tests/test_ollama.py`, `pytest.ini`, `src/agent/llm.py`, `tests/test_llm.py`

## Reaching Ollama from the containers

Ollama runs on the host, not in Compose, so it can use the machine's memory directly and its models stay put when containers are rebuilt. The app reaches it at `OLLAMA_BASE_URL`, which defaults to `http://host.docker.internal:11434`. Two things had to change before that address worked from inside a container.

```text
api / worker container ──host.docker.internal──▶ 172.17.0.1 (host, docker0) ──▶ Ollama on *:11434
```

### 1. The name: `extra_hosts`

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

**Docker Desktop**, on macOS and Windows, defines `host.docker.internal` in every container. **Docker on Linux doesn't.** Without this setting, the name doesn't resolve: the first run of the tests failed with `Temporary failure in name resolution`, and each failure took several seconds while the DNS lookup timed out.

`host-gateway` is a special value that Docker replaces with the host's address on its default bridge network. Here that's `172.17.0.1`, the `docker0` interface. Compose writes the mapping into the container's `/etc/hosts`, the same file that maps names on any Linux machine. `getent hosts host.docker.internal` inside the container shows it.

Both `api` and `worker` have the setting. The worker is what will call the model, and the API gets it so the checks can run from `docker compose exec api pytest` like every other test.

### 2. The address Ollama listens on: `OLLAMA_HOST`

Ollama listened on `127.0.0.1:11434` by default. That's the host's loopback, which only accepts connections from the host itself. A container's request arrives at `172.17.0.1`, a different address, so it would be refused even once the name resolved.

The fix is outside the repository, in a systemd drop-in for the Ollama service, `/etc/systemd/system/ollama.service.d/override.conf`:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

A drop-in is a small file that adds to or overrides a unit without editing the unit file itself, so a package upgrade that replaces the unit keeps the change. `sudo systemctl edit ollama` creates one through an editor. Writing the file directly works too, but then `sudo systemctl daemon-reload` is needed so systemd notices it. `sudo systemctl restart ollama` applies it, and `ss -ltn | grep 11434` shows the result: `*:11434`, all interfaces.

Laravel Sail has the same problem with Xdebug calling back to the host, and solves it with the same `extra_hosts` line.

**This opens Ollama to the local network.** `0.0.0.0` means every interface, and the host's firewall (`ufw`) is inactive, so any device on the same network can use the Ollama API. The API has no authentication, and it can pull and delete models as well as run them. See "Known limitations".

## The model: `llama3.2`

`LLM_MODEL` is `llama3.2`, which Ollama resolves to `llama3.2:latest`, the 3-billion-parameter version, about 2 GB. The machine has no GPU that Ollama uses, so the model runs on the CPU.

Measured from inside the worker container on 2026-11-02, with a short SQL injection snippet as the prompt and a 300-token limit:

| Model | Generation speed | Notes |
|---|---|---|
| `llama3.2:1b` | about 9 tokens/s (from the host) | Fast, but small models are weak at structured output |
| `llama3.2` (3B) | about 6 tokens/s | 300 tokens in about 54 seconds, found the SQL injection |
| `mistral` (7B) | about 2.4 tokens/s (from the host) | Several minutes per review |

A token is roughly three quarters of a word. About a minute per review is slow for a person waiting, but the worker runs in the background, and the unit tests use a mocked LLM. How good the reviews are is a question for step 3.9, which runs real reviews.

## Tests: `tests/test_ollama.py`

Four checks, all from inside the `api` container:

- `host.docker.internal` resolves.
- `GET /api/version` answers.
- The configured model is pulled. `GET /api/tags` lists the models, and Ollama adds `:latest` to a name pulled without a tag, so the test does the same.
- A short prompt gets a non-empty reply through `POST /api/chat`. `num_predict: 10` limits the reply to 10 tokens and `temperature: 0` makes it as repeatable as the model allows. The test takes about 5 seconds, mostly loading the model.

They call Ollama's HTTP API with `httpx` directly. The LangChain integration comes in step 3.2, and these checks are about the connection, not the library.

**The `ollama` marker.** The file sets `pytestmark = pytest.mark.ollama`, which marks every test in it. The marker is registered in `pytest.ini`, because pytest warns about unknown markers. `pytest -m "not ollama"` skips them. The CI pipeline in Unit 6 will need that, because it won't have a local model. Locally they run with everything else.

## Choosing the model: `src/agent/llm.py`

The agent's nodes need a chat model, but they shouldn't care which company or program runs it. `src/agent/llm.py` is the one place that turns settings into a model object:

```python
from src.agent.llm import get_chat_model

model = get_chat_model()                 # reads the cached app settings
reply = await model.ainvoke("Review this diff ...")
```

### How it works

```python
_BUILDERS: dict[str, Callable[[Settings], BaseChatModel]] = {
    "ollama": _build_ollama,
}

def get_chat_model(settings: Settings | None = None) -> BaseChatModel:
    if settings is None:
        settings = get_settings()
    provider = settings.LLM_PROVIDER.strip().lower()
    builder = _BUILDERS.get(provider)
    if builder is None:
        raise LLMConfigError(...)
    return builder(settings)
```

- **A dictionary of builders.** Each provider is a small function that takes the settings and returns a model. Adding a cloud provider later means one new function and one new dictionary entry. The type `Callable[[Settings], BaseChatModel]` reads "a function that takes `Settings` and returns a `BaseChatModel`". `SUPPORTED_PROVIDERS` is built from the dictionary's keys, so the error message can't drift from what's really supported.
- **`BaseChatModel` is the return type.** It's LangChain's common interface for chat models. `ainvoke`, `astream`, and `with_structured_output` (used from step 3.4) work the same on every provider, so the nodes only use those. This is like type-hinting an interface in PHP, such as `Illuminate\Contracts\Cache\Repository`, instead of the Redis implementation.
- **The provider name is forgiving.** `" Ollama "` works, because `.strip().lower()` runs first. A wrong name raises `LLMConfigError`, a subclass of `ValueError`, with the bad value and the supported list: `Unknown LLM_PROVIDER 'openai'. Supported: ollama.`
- **Settings are optional.** With no argument, it uses `get_settings()`, the same cached settings the app uses. Tests pass their own copy with `settings.model_copy(update={...})`, which changes one field without touching the environment.
- **A new model each call.** The function doesn't cache. Building a `ChatOllama` is cheap, since it only sets up HTTP clients and doesn't contact the server.

### The Ollama builder

```python
def _build_ollama(settings: Settings) -> BaseChatModel:
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=settings.LLM_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        temperature=0,
        client_kwargs={"timeout": settings.LLM_TIMEOUT_SECONDS},
    )
```

- **The import is inside the function.** Python runs an import when the line is reached, not when the file loads. So `langchain_ollama` is only needed if Ollama is selected, and a future cloud provider's package wouldn't have to be installed for someone using Ollama. The cost is that a missing package shows up on the first call, not at startup.
- **`temperature=0`** makes the model pick its most likely next token each time, so the same diff gets a similar review. It isn't fully deterministic: CPU threading and model updates can still change the output.
- **`client_kwargs`** go to the `ollama` package's HTTP clients, which are built on `httpx`, so `timeout` is the same `httpx` timeout used elsewhere. `ChatOllama` passes them to both its sync and async clients. Without a timeout, a stuck Ollama would hold the worker's message forever, because the worker acks only after the job finishes (D23). `LLM_TIMEOUT_SECONDS` defaults to 300, about five times the measured review time.
- **Nothing is checked at build time.** `ChatOllama` doesn't contact the server when it's created (it has a `validate_model_on_init` option, left off). A wrong model name or an unreachable server shows up on the first call. `tests/test_ollama.py` checks those separately.

### "Only through `LLM_PROVIDER`": how it's enforced

The plan's goal for this step is that nodes get a model only through the factory. A comment can't enforce that, so `tests/test_llm.py` has a guard test. It reads every `.py` file under `src/` with Python's `ast` module, which parses code into a tree without running it, collects the top-level package of every `import` and `from ... import`, and fails if any file other than `src/agent/llm.py` imports a provider package (`langchain_ollama`, `langchain_openai`, `langchain_anthropic`, or `ollama`).

A second test checks that the guard does find `langchain_ollama` in `llm.py`. Without it, a bug in the guard, such as missing imports inside functions, would let the first test pass without checking anything. `ast.walk` visits every node in the tree, so imports inside functions are found too.

## Tests: `tests/test_llm.py`

Thirteen tests, counting each parametrized case. Twelve build the model without calling it, so they need no Ollama:

- Ollama is selected, with the configured model, URL, `temperature` 0, and the timeout in `client_kwargs`.
- The result is a `BaseChatModel`.
- `"Ollama"`, `" ollama "`, and `"OLLAMA"` all work.
- `"openai"`, `""`, and `"olama"` raise `LLMConfigError`, and the message names the bad value and the supported providers.
- With no argument, the app settings are used.
- The import guard and its self-check.

The last test, under the `ollama` marker, makes a real call through the factory's model. `model_copy(update={"num_predict": 10})` makes a copy limited to 10 tokens. `.bind(num_predict=10)` doesn't work for this. `ChatOllama` reads options such as `num_predict` from its own fields, and passes extra keywords from `bind` straight to the `ollama` client, which raised `TypeError: AsyncClient.chat() got an unexpected keyword argument 'num_predict'` when tried. The copy produced exactly 10 output tokens.

## Known limitations

- **Ollama is reachable from the local network.** It listens on all interfaces, the firewall is inactive, and Ollama's API has no authentication. Anyone on the same network can run, pull, or delete models. The fix is a firewall rule that allows only Docker's networks to reach port 11434. It isn't applied yet.
- **The listen address lives outside the repository.** A fresh machine needs the systemd drop-in by hand. These notes are the only record of it.
- **`host-gateway` points at `docker0`.** The Compose network has its own bridge (`172.25.0.1` here), but traffic to `172.17.0.1` still reaches the host, because both are the host's own addresses. That was verified by the tests passing, not traced through the routing.
- **Speed was measured once, with one prompt.** Real diffs are longer, and the prompt part grows with them.
- **Only one provider exists.** The factory's shape for a second provider is untested. A cloud provider would also need its API key in the settings as a `SecretStr`.
- **The import guard only sees direct imports.** Code that loads a module by name, such as `importlib.import_module("langchain_ollama")`, or that gets a provider class through another module, would pass it.
- **Configuration errors appear late.** A wrong `LLM_PROVIDER` raises on the first `get_chat_model()` call, not when the app starts, and a wrong model name only on the first LLM call.

## Verification record

Step 3.1, on 2026-11-02: the four tests in `tests/test_ollama.py` failed first with `Temporary failure in name resolution`. After adding `extra_hosts` and recreating `api` and `worker`, both containers resolved `host.docker.internal` to `172.17.0.1`, and the four tests passed in 5.3 seconds. A deliberate break, `LLM_MODEL=not-a-pulled-model`, failed the two model tests and left the two connection tests passing. From the worker container, `llama3.2` answered a 49-token review prompt with 300 tokens at 6.0 tokens/s, 54.1 seconds in total, and named the SQL injection. Full suite: 155 passed, and 151 passed with `-m "not ollama"`.

Step 3.2, on 2026-11-03: `tests/test_llm.py` failed first on `No module named 'langchain_core'`. After adding `langchain-core` and `langchain-ollama` (1.7.4 and 1.1.0 installed) and rebuilding, its 13 tests passed. Six deliberate breaks each failed at least one test: removing `temperature=0`, removing the timeout, removing `.strip().lower()`, falling back to Ollama for unknown providers, hardcoding the model name, and importing `ChatOllama` in `src/worker.py`. Running with `LLM_MODEL=not-a-pulled-model` failed only the real-call test. Full suite: 168 passed, and 163 passed with `-m "not ollama"`.
