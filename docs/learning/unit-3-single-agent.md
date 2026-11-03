# Unit 3: Single-Agent Code Review

Unit 3 replaces the worker's simulated review with a LangGraph agent that reviews the diff with a local LLM. This file grows as the unit is built, and it only describes what exists so far.

**Files so far:** `docker-compose.yml`, `tests/test_ollama.py`, `pytest.ini`, `src/agent/llm.py`, `tests/test_llm.py`, `src/agent/schemas.py`, `src/agent/state.py`, `tests/test_agent_state.py`, `src/agent/nodes.py`, `tests/test_agent_nodes.py`, `tests/fixtures/diffs/`

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

## The review the model returns: `src/agent/schemas.py`

The LLM's answer has to be data the app can use, not free text. `CodeAuditReport` is a Pydantic model that describes that answer:

```text
CodeAuditReport
├── summary                   str, required
├── overall_risk_score        RiskLevel, required: LOW | MEDIUM | HIGH | CRITICAL
├── syntax_issues             list[Finding], default []
└── security_vulnerabilities  list[Finding], default []

Finding
├── file         str, required
├── line         int ≥ 1, or null (default null)
├── severity     Severity, required: LOW | MEDIUM | HIGH | CRITICAL
├── title        str, required
├── explanation  str, required
└── suggestion   str or null (default null)
```

### The schema is part of the prompt

From step 3.4, the node will call `model.with_structured_output(CodeAuditReport)`. With its default `method="json_schema"`, `ChatOllama` turns the class into a JSON schema with `CodeAuditReport.model_json_schema()` and sends it as Ollama's `format` option, which constrains the reply to JSON in that shape. The reply is then parsed back into a `CodeAuditReport`, so validation still runs. So every `Field(description=...)` is text the model reads, and that's why every field has one. A test checks that none is missing. This is different from a Laravel FormRequest, whose rules only check input after it arrives. Here the same class also tells the sender what to send.

The fields are kept few and flat on purpose. A 3B model handles a short schema better than a deep one.

### What is accepted and what is rejected

The reply is untrusted, so it's validated like any request body, and anything that doesn't fit raises `ValidationError`:

- **Required fields.** A report needs `summary` and `overall_risk_score`. A finding needs `file`, `severity`, `title`, and `explanation`. The lists default to empty, so a clean review can leave them out.
- **Levels are a fixed set.** `Severity` and `RiskLevel` are `StrEnum`s, the same kind as `JobStatus` in Unit 2. `"SEVERE"` or `"URGENT"` is rejected.
- **Levels ignore case.** Small models often write `"high"`. A `field_validator` with `mode="before"` runs before Pydantic checks the type, and turns any string into `value.strip().upper()`. A value that is still unknown afterwards fails the enum check as usual. The `@classmethod` under `@field_validator` is the form Pydantic expects: a validator belongs to the class, not to an instance.
- **`line` must be 1 or more, or null.** `ge=1` means "greater than or equal to 1". Line numbers start at 1, so a `0` is a sign the model made the number up.
- **Unknown keys are ignored.** Pydantic's default `extra="ignore"` drops a key such as `confidence` instead of failing the whole review over it.

Two level enums with the same values look like duplication. They're kept apart because they mean different things, one finding and the whole pull request, and the schema shows the model both names.

## The graph state: `src/agent/state.py`

LangGraph passes one state dictionary through the graph. `PRAnalysisState` is a `TypedDict`, which is a type hint for a plain `dict` with known keys. At runtime it is just a dict: nothing is validated, unlike a Pydantic model. It holds the job's details, the diff, the findings, the risk score, and the finished report, the fields the curriculum lists. Step 3.4 added one more, `analysis_summary`, for the model's own short summary, because `final_summary` is the finished Markdown report that `format_report_node` writes.

### Reducers

A node doesn't return the whole state. It returns only the keys it changed, and LangGraph merges them in. For most keys, the new value replaces the old one. A key annotated with a **reducer** is combined with the old value instead:

```python
syntax_issues: Annotated[list[dict[str, Any]], operator.add]
security_vulnerabilities: Annotated[list[dict[str, Any]], operator.add]
```

`Annotated[type, extra]` attaches extra information to a type hint without changing the type. LangGraph reads the extra, `operator.add`, which is the function behind `+`. For lists, `+` joins them, so `old + new` keeps every finding.

This matters when nodes run in parallel, as the security and quality agents will in Unit 4. `tests/test_agent_state.py` builds a small graph where `START` leads to two nodes, `a` and `b`, which run in the same step:

- Both add a finding to `security_vulnerabilities`, and the result holds the initial finding plus both new ones.
- Both write `overall_risk_score`, which has no reducer, and LangGraph raises `InvalidUpdateError`: it can't tell which write should win, so it refuses to guess.

The nearest Laravel idea is how `array_merge_recursive` and `array_merge` treat the same key differently, except here you choose the rule for each key.

### Findings are stored as dicts

The lists hold plain dictionaries, made with `finding.model_dump(mode="json")`, not `Finding` objects. `mode="json"` turns the enums into plain strings such as `"HIGH"`. Plain JSON keeps the state easy to store, and Unit 4 saves every state to Postgres as a checkpoint. The node that calls the LLM does this conversion, in step 3.4.

## The analysis node: `src/agent/nodes.py`

`analyze_diff_node` is the step that asks the model for a review. It reads the diff from the state, sends it with a system prompt, gets back a validated `CodeAuditReport`, and returns the parts of the state it owns.

```text
state ──▶ empty diff? ──yes──▶ LOW report, no model call
              │ no
              ▼
        build_messages(state) ──▶ reviewer.ainvoke(...) ──▶ CodeAuditReport
                                         │ bad reply                │
                                         ▼                          ▼
                                   AnalysisError       _to_update(report) ──▶ {analysis_summary, overall_risk_score,
                                                                               syntax_issues, security_vulnerabilities}
```

### Passing the model in: `make_analyze_diff_node(model)`

A LangGraph node is called with the state and nothing else, so the node can't take the model as an argument. Instead, `make_analyze_diff_node(model)` takes the model and returns the node, an inner function that can still see `model` after the outer function returns. That inner function is a **closure**. In PHP terms it's `function () use ($model) { ... }`.

```python
def make_analyze_diff_node(model: BaseChatModel) -> Node:
    reviewer = model.with_structured_output(CodeAuditReport)

    async def analyze_diff_node(state: PRAnalysisState) -> dict[str, Any]:
        ...
        report = await reviewer.ainvoke(build_messages(state))
        return _to_update(report)

    return analyze_diff_node
```

The graph builder (step 3.7) will call `make_analyze_diff_node(get_chat_model())`, and tests call it with a fake. This is constructor injection without a class. `with_structured_output` runs once, when the node is built, not on every review. `Node` is a type alias for "an async function from state to a dict".

### The messages

`build_messages(state)` returns two messages:

1. A `SystemMessage` with `SYSTEM_PROMPT`: the reviewer's job, how to read a diff, and the rules for the report.
2. A `HumanMessage` with the repository, the PR number, the title between `<title>` tags, and the diff, unchanged, between `<diff>` tags.

The tags mark where the pull request's own text starts and ends. Step 3.5 builds on them with the rule that nothing inside them is an instruction. The title is inside tags too, because the PR's author writes it just like the diff.

### How the prompt was chosen

The prompt was tried against `llama3.2` with the two fixture diffs, one SQL injection and one clean change. These were single runs at `temperature=0`, so they show a direction, not a measurement:

| Prompt | SQL injection diff | Clean diff |
|---|---|---|
| A one-line system prompt (step 3.3) | 1 finding, `CRITICAL`, 23 s | not tried |
| First version, with a "Look for" checklist of vulnerability types and "clear code-quality problems" | injection rated `MEDIUM`, plus 4 low-value findings, one of them false, 387 tokens, 85 s | not tried |
| The same, telling it to skip style and docs | injection `MEDIUM`, plus 3 vague findings | **6 invented findings**, each copied from the checklist (path traversal, template injection, ...) |
| Final: no checklist, "most pull requests have none, and empty lists are a good answer" | 1 finding, `HIGH`, 90 tokens, 46 s | 2 low-grade false positives |

The lesson: a small model treats a list of examples in the prompt as a list of things to report. Saying that "no problems" is a normal answer helped more than describing what to look for. Shorter replies also make reviews faster, because on this CPU almost all the time is spent writing the reply, about 5 tokens per second. Reading the prompt took 0.2 seconds of the 85.

`line` was `null` in every run, even with the prompt explaining how to count from the `@@` header. Step 3.11 is planned to number the added lines in code, before the diff is sent.

### What the node returns

`_to_update(report)` turns the report into a state update with `model_dump(mode="json")`, so the findings are plain dicts with plain-string levels (see "Findings are stored as dicts"). It returns only the four keys this node owns. Returning `job_id` or `diff_text` too would overwrite them for nothing, and a test checks that it doesn't.

**The overall risk is never below the worst finding.** The model once rated a PR `MEDIUM` overall while its own finding was `HIGH`. `_overall_risk` takes the highest of the model's overall level and every finding's severity. Levels are strings, so plain `max()` would compare them alphabetically, and `"MEDIUM"` would beat `"CRITICAL"`. `max(levels, key=_LEVEL_ORDER.index)` compares their positions in `RiskLevel` instead, which lists them from LOW to CRITICAL. A `StrEnum` keeps its members in the order they're defined. The rule only raises the level: a model that says `HIGH` with only `LOW` findings keeps `HIGH`.

### Failures

- **An empty diff** (or one with only whitespace) skips the model and returns a `LOW` report with a fixed summary. That can happen when a PR's commits cancel each other out. It isn't verified which other PRs GitHub returns an empty diff for: a rename or a permission change still has header lines, so it isn't empty. Either way, a minute of CPU time would be wasted on nothing.
- **An unusable reply** raises `AnalysisError`. When `ChatOllama`'s parser can't turn the reply into a `CodeAuditReport`, it raises `OutputParserException`, which LangChain makes a subclass of `ValueError`. The node catches that, and a plain `ValidationError` too, and raises its own error. `AnalysisError` is also a `ValueError`, so the worker's existing rule (D23) marks the job `failed` without retrying. Running the same diff again at `temperature=0` would most likely give the same bad reply.
- **The message only names the error type.** The model's reply can quote the diff, and the message ends up in the job record and the logs. The original exception is kept as the cause through `raise ... from error`, so a traceback still shows it.
- **A slow or unreachable model** isn't caught here. The timeout from step 3.2 raises an `httpx` error, which the worker treats as unexpected and retries once.

## Tests: `tests/test_agent_nodes.py`

**The fake model.** LangChain's own fake chat models raise `NotImplementedError` for `with_structured_output`, so the tests have a small `FakeStructuredModel`. Its `with_structured_output(schema)` returns a `RunnableLambda` (a plain function wrapped so it has `ainvoke`) that records the messages, then parses the canned JSON text into the schema, raising `OutputParserException` as the real parser does. Because the canned reply is text, the tests go through the same validation as a real reply. The fake doesn't inherit from `BaseChatModel`. Python only needs it to have the methods the node calls, which is called *duck typing*.

The fixture diffs in `tests/fixtures/diffs/` are made up, with placeholder paths.

Seventeen tests, counting each parametrized case. All but one use the fake:

- The SQL injection reply becomes one security finding with `HIGH` risk, and the clean reply becomes empty lists with `LOW`.
- Levels written in lowercase come out uppercase, as plain `str`, not enums.
- The update has exactly the four keys.
- The risk rule: `MEDIUM` with a `HIGH` finding becomes `HIGH`, `LOW` with a `CRITICAL` one becomes `CRITICAL`, `HIGH` with only `LOW` stays `HIGH`, and no findings keeps the model's level.
- The model is asked once, for `CodeAuditReport`. The messages are system then human, the diff is unchanged between `<diff>` and `</diff>`, the title is included, and the system prompt explains `@@` line numbers.
- Text that isn't JSON, a missing risk score, and an unknown level each raise `AnalysisError`, which is a `ValueError`, without the reply in its message.
- An empty or whitespace-only diff makes no model call.

The last test, under the `ollama` marker, runs the real model on the SQL injection diff and expects at least one security finding and a risk of `MEDIUM` or higher. It takes about 30 seconds. It doesn't test the clean diff, because the model's false positives there would make it fail.

## Known limitations

- **Ollama is reachable from the local network.** It listens on all interfaces, the firewall is inactive, and Ollama's API has no authentication. Anyone on the same network can run, pull, or delete models. The fix is a firewall rule that allows only Docker's networks to reach port 11434. It isn't applied yet.
- **The listen address lives outside the repository.** A fresh machine needs the systemd drop-in by hand. These notes are the only record of it.
- **`host-gateway` points at `docker0`.** The Compose network has its own bridge (`172.25.0.1` here), but traffic to `172.17.0.1` still reaches the host, because both are the host's own addresses. That was verified by the tests passing, not traced through the routing.
- **Speed was measured once, with one prompt.** Real diffs are longer, and the prompt part grows with them.
- **Only one provider exists.** The factory's shape for a second provider is untested. A cloud provider would also need its API key in the settings as a `SecretStr`.
- **The import guard only sees direct imports.** Code that loads a module by name, such as `importlib.import_module("langchain_ollama")`, or that gets a provider class through another module, would pass it.
- **The state isn't validated.** `PRAnalysisState` is only a type hint. A node that returns a misspelled key, or a `Finding` object instead of a dict, isn't caught at runtime.
- **The model leaves `line` empty.** In the step 3.3 try and all three prompt versions in step 3.4, `llama3.2` gave `line: null`, even with the prompt explaining the `@@` headers. Step 3.11 is planned to number the lines in code.
- **False positives on clean code.** The final prompt still gave the clean fixture diff two low-grade findings. Review quality is judged in step 3.9, and a bigger model may be the real fix.
- **The prompt was tuned on two small diffs, one run each.** That's enough to spot the checklist problem, not to show the prompt is good. A repeatable comparison needs the evaluation suite in Unit 6.
- **The diff isn't yet treated as untrusted.** It's between markers, but the prompt doesn't yet say that nothing inside them is an instruction, and there's no size limit. Both are step 3.5.
- **Text fields have no length limits.** A model could return a very long `explanation`. Whether limits are needed is left until real reviews in step 3.9.
- **Configuration errors appear late.** A wrong `LLM_PROVIDER` raises on the first `get_chat_model()` call, not when the app starts, and a wrong model name only on the first LLM call.

## Verification record

Step 3.1, on 2026-11-02: the four tests in `tests/test_ollama.py` failed first with `Temporary failure in name resolution`. After adding `extra_hosts` and recreating `api` and `worker`, both containers resolved `host.docker.internal` to `172.17.0.1`, and the four tests passed in 5.3 seconds. A deliberate break, `LLM_MODEL=not-a-pulled-model`, failed the two model tests and left the two connection tests passing. From the worker container, `llama3.2` answered a 49-token review prompt with 300 tokens at 6.0 tokens/s, 54.1 seconds in total, and named the SQL injection. Full suite: 155 passed, and 151 passed with `-m "not ollama"`.

Step 3.2, on 2026-11-03: `tests/test_llm.py` failed first on `No module named 'langchain_core'`. After adding `langchain-core` and `langchain-ollama` (1.7.4 and 1.1.0 installed) and rebuilding, its 13 tests passed. Six deliberate breaks each failed at least one test: removing `temperature=0`, removing the timeout, removing `.strip().lower()`, falling back to Ollama for unknown providers, hardcoding the model name, and importing `ChatOllama` in `src/worker.py`. Running with `LLM_MODEL=not-a-pulled-model` failed only the real-call test. Full suite: 168 passed, and 163 passed with `-m "not ollama"`.

Step 3.3, on 2026-11-03: `tests/test_agent_state.py` failed first on the missing `langgraph` package, then on the missing `src.agent.schemas`. After adding `langgraph` (1.2.19) and writing the two modules, its 20 tests passed. Seven deliberate breaks each failed at least one test: removing the reducer from `security_vulnerabilities`, removing the level normalizing, removing `ge=1`, making `summary` optional, removing one field description, setting `extra="forbid"`, and dropping the `CRITICAL` level. An informal live check, not part of the tests: `llama3.2` with `with_structured_output(CodeAuditReport)` and a one-line system prompt returned a valid report for a three-line SQL injection diff in 23 seconds, with risk `HIGH`, one `CRITICAL` finding, and `line: null`. Full suite: 188 passed, and 183 passed with `-m "not ollama"`.

Step 3.4, on 2026-11-03: `tests/test_agent_nodes.py` failed first on the missing `src.agent.nodes`, then its tests passed, including the real-model test in 109 seconds. Three prompt versions were compared on the two fixture diffs (see "How the prompt was chosen"). The overall-risk rule was added test-first after one run showed `MEDIUM` overall with a `HIGH` finding: its two new cases failed, then passed. Nine deliberate breaks each failed at least one test: checking the empty diff without `.strip()`, dumping without `mode="json"`, removing the risk rule, using plain `max()`, catching only `ValidationError`, putting the error text in the message, removing the `<diff>` markers, removing the `@@` hint, and returning an extra key. The first try at the `@@` break missed, because the edit left one `@@` in the prompt; with the whole hint removed, the test failed. Full suite: 205 passed in 35 seconds, the real-model test taking 30, and 199 passed with `-m "not ollama"`.
