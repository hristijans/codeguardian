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

**Why it is used:** FastAPI builds on it for requests and responses. It will also validate LLM output in Unit 3, where bad data is a normal occurrence.

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

## Development tools

### smee-client

**What it does:** Connects to a smee.io channel and posts every webhook it receives again to a local URL.

**Why it is used:** GitHub can't post to `localhost`. The relay needs no account, opens no port on the machine, and is the tool GitHub's own webhook guides suggest. It runs only in the `smee` Compose service under the `live` profile, pinned to 5.0.0, through `npx`. It isn't a Python dependency and isn't in `requirements.txt`.

**Alternatives:** ngrok or Cloudflare Tunnel, which expose the local port itself and forward the exact bytes, so signatures always survive. Both need an account or a binary, and expose more than webhooks. GitHub CLI's `gh webhook forward` extension, which needs the `gh` CLI and an admin login. Posting signed test payloads by hand, which the tests already do but which proves nothing about real GitHub events.

**Caveat:** It rebuilds the request body with `JSON.stringify`, so it doesn't forward GitHub's bytes exactly (see the Unit 2 notes).

**Laravel equivalent:** None built in. Laravel developers usually reach for Expose or ngrok.

