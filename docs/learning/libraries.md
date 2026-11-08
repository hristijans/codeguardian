# Libraries

This covers each dependency in `requirements.txt`: what it does, why it is here, and its closest Laravel equivalent.

Versions in `requirements.txt` are minimums, not exact pins, so a rebuild can install newer releases.

## Web layer

### FastAPI

**What it does:** Web framework for routing, validating requests, injecting dependencies, and generating API docs at `/docs`.

**Why it is used:** It is async-native, which suits a service that spends most of its time waiting on Postgres, Redis, RabbitMQ, and LLM calls. Type hints drive its validation and docs.

**Laravel equivalent:** Routing and controllers. `Depends()` plays a similar role to controller injection from the service container, but you declare each dependency per route.

**Alternatives:** Flask has a larger legacy ecosystem but was not designed around async. Django is a much larger framework than this service needs.

### Uvicorn

**What it does:** ASGI server that runs the FastAPI app. The `[standard]` extra adds faster event-loop and HTTP components, plus the file watching used by `--reload`.

**Why it is used:** FastAPI is only an application. It needs a server to accept connections.

**Laravel equivalent:** Roughly PHP-FPM with Nginx, or `php artisan serve` when using `--reload` in development. The key difference is that Uvicorn keeps one long-running process, so the app stays in memory between requests. Octane is the closer comparison.

## Validation and configuration

### Pydantic

**What it does:** Validates data against classes built from type hints.

**Why it is used:** FastAPI builds on it for requests and responses. It also validates the LLM's reply in Unit 3 (`CodeAuditReport`), where bad data is a normal occurrence.

**Laravel equivalent:** FormRequest validation, plus API Resources for output.

### pydantic-settings

**What it does:** Loads configuration from environment variables and `.env` into a typed class.

**Why it is used:** A wrong or missing value fails when settings load, with a clear error, instead of surfacing later as a strange bug. For example, `POSTGRES_PORT=abc` fails immediately because the field is an `int`.

**Laravel equivalent:** `env()` and `config/*.php`, with types added.

## Data stores and messaging

### SQLAlchemy (with the `asyncio` extra)

**What it does:** Database toolkit and ORM. Unit 1 only uses it to run `SELECT 1`.

**Why it is used:** It is the standard Python choice, and it supports async sessions.

**Laravel equivalent:** Eloquent, with one major difference. Eloquent is an active record ORM, where models save themselves. SQLAlchemy uses a unit of work, where a session tracks changes and you commit them explicitly.

**Alternatives:** SQLModel is a simpler layer on top of SQLAlchemy. Raw asyncpg has no ORM.

### asyncpg

**What it does:** Async PostgreSQL driver that SQLAlchemy uses underneath.

**Why it is used:** A synchronous driver would block the event loop while waiting on the database. The `+asyncpg` part of the connection string selects this driver.

**Laravel equivalent:** `pdo_pgsql`.

### redis (redis-py)

**What it does:** Redis client. The async version comes from `redis.asyncio`.

**Why it is used:** The separate `aioredis` package was merged into `redis-py`, so `redis-py` is the one to use. The code keeps the familiar name with `import redis.asyncio as aioredis`.

**Laravel equivalent:** The `Redis` facade.

**Where it is used:** The `/health` check, and the job store in `src/cache.py`, which uses `set(..., ex=...)`, `get`, `delete`, and `set(..., nx=True, get=True)` for the delivery claim. The app shares one client, created in the FastAPI lifespan. `get` returns bytes, which Pydantic's `model_validate_json` accepts directly, so the client doesn't need `decode_responses=True`.

### aio-pika

**What it does:** Async RabbitMQ client that speaks AMQP 0-9-1.

**Why it is used:** It gives direct control over queues and acknowledgements, and it supports automatic reconnection through `connect_robust`.

**Laravel equivalent:** Laravel queues hide most of this. With aio-pika you handle the queue, the message, and the acknowledgement yourself, which is part of the learning value.

**Where it is used:** The `/health` check, and `src/queue.py`, which declares the durable queue and publishes persistent messages. The app shares one robust connection and channel, created in the FastAPI lifespan. Channels have publisher confirms on by default, and an unroutable message is returned rather than raised, so the code checks for `Basic.Ack` itself.

**`pamqp`:** `src/queue.py` imports `pamqp.commands.Basic` to recognise the broker's ack. `pamqp` is the AMQP frame library underneath aio-pika, through `aiormq`, so it's installed with aio-pika and isn't listed in `requirements.txt`. It's a direct import of an indirect dependency, so an aio-pika upgrade could in principle move it.

## HTTP and testing

### httpx

**What it does:** HTTP client with both sync and async support.

**Why it is used:** Tests call the app with it, and `GitHubClient` in `src/github.py` uses it to call the GitHub API. It was already installed, and its async client fits the rest of the app.

**Mocking:** The GitHub client tests use `httpx.MockTransport`, which is built into httpx and replaces the network with a function. The alternative was `respx`, a separate library that patches httpx and matches routes. It reads more neatly for many routes, but one endpoint doesn't need another dependency.

**Laravel equivalent:** The `Http` facade, and test methods such as `$this->getJson()`.

### pytest

**What it does:** Test runner. Plain functions and `assert` statements replace test classes and assertion methods.

**Why it is used:** It is the Python standard, and its fixtures are a clean way to share setup.

**Laravel equivalent:** Pest is the closest match. Fixtures are similar to `beforeEach`, but a test requests each one by naming it as a parameter.

### pytest-asyncio

**What it does:** Lets pytest run `async def` tests and fixtures.

**Why it is used:** pytest cannot await a coroutine by itself. Without this plugin, an async test would not run properly. The project needs version 1.0 or newer, and sets its loop scope in `pytest.ini`.

**Laravel equivalent:** None, because normal PHP code does not have this async model.

## LLM

### langchain-core

**What it does:** The base layer of LangChain: message types (`HumanMessage`, `AIMessage`), the `BaseChatModel` interface every chat model implements, and the "Runnable" methods on it such as `ainvoke`, `astream`, and `with_structured_output`.

**Why it is used:** It gives the agent one interface for every provider, so nodes don't change when the model does. LangGraph, used since step 3.7, builds on the same package. Listing it directly, instead of relying on `langchain-ollama` to pull it in, is because `src/agent/llm.py` imports it directly.

**Alternatives:** Calling the Ollama HTTP API with `httpx`, as `tests/test_ollama.py` does. That's fine for a connection check, but structured output, retries, and swapping providers would all have to be written by hand. The full `langchain` package, which adds chains and agents this project gets from LangGraph instead.

**Laravel equivalent:** The interface side of a driver-based manager, like `Illuminate\Contracts\Cache\Repository`. Laravel's AI SDK or Prism plays a similar role for LLMs.

**Where it is used:** `src/agent/llm.py`, for the `BaseChatModel` return type.

### langchain-ollama

**What it does:** Provides `ChatOllama`, a `BaseChatModel` that talks to an Ollama server. Underneath it uses the official `ollama` Python package (0.6.3 installed with it), which uses `httpx`.

**Why it is used:** It's LangChain's maintained Ollama integration and supports structured output, which step 3.4 needs.

**Alternatives:** The older `ChatOllama` in `langchain-community`, which is deprecated in favour of this package. The `ollama` package on its own, which has no LangChain interface. Ollama's OpenAI-compatible endpoint through `langchain-openai`, which would make a later move to a cloud model look smaller, but loses Ollama-specific options such as `num_ctx`.

**Laravel equivalent:** One concrete driver behind the manager, like the Redis cache driver.

**Where it is used:** Only `src/agent/llm.py`. A test in `tests/test_llm.py` fails if any other file in `src/` imports it.

### langgraph

**What it does:** Builds an LLM workflow as a graph. Nodes are functions that read the state and return changes, and edges decide which node runs next. It merges each node's changes into the state, runs nodes in parallel when the edges allow, and in Unit 4 saves the state between steps (`langgraph-checkpoint`, 4.3.1, is installed with it).

**Why it is used:** It's the subject of the curriculum, and its explicit graph makes each step of a review visible and testable. It's added in step 3.3, earlier than the graph itself in step 3.7, because the reducer tests need a real graph to show that the state merges findings correctly.

**Alternatives:** A plain `async` function calling the model, which is enough for one step but has no parallel nodes, checkpoints, or human-in-the-loop pauses, all of which Units 4 and 5 need. LangChain's older `AgentExecutor`, which is replaced by LangGraph. CrewAI or AutoGen, which hide more of the flow behind agent roles.

**Laravel equivalent:** No direct match. It's closest to a job pipeline or `Bus::chain`, where each job hands its result on, but with a shared state, branching, and parallel steps.

**Where it is used:** `src/agent/graph.py` builds the two-node `StateGraph`, and `tests/test_agent_state.py` uses it to test the reducers. `src/agent/state.py` defines the state and doesn't import it.

## Development tools

### smee-client

**What it does:** Connects to a smee.io channel and posts every webhook it receives again to a local URL.

**Why it is used:** GitHub can't post to `localhost`. The relay needs no account, opens no port on the machine, and is the tool GitHub's own webhook guides suggest. It runs only in the `smee` Compose service under the `live` profile, pinned to 5.0.0, through `npx`. It isn't a Python dependency and isn't in `requirements.txt`.

**Alternatives:** ngrok or Cloudflare Tunnel, which expose the local port itself and forward the exact bytes, so signatures always survive. Both need an account or a binary, and expose more than webhooks. GitHub CLI's `gh webhook forward` extension, which needs the `gh` CLI and an admin login. Posting signed test payloads by hand, which the tests already do but which proves nothing about real GitHub events.

**Caveat:** It rebuilds the request body with `JSON.stringify`, so it doesn't forward GitHub's bytes exactly (see the Unit 2 notes).

**Laravel equivalent:** None built in. Laravel developers usually reach for Expose or ngrok.

