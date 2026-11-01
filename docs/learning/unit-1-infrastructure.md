# Unit 1: Infrastructure

Unit 1 starts Postgres, Redis, RabbitMQ, and a FastAPI service with one command, then reports whether the API can reach all three.

**Files:** `docker-compose.yml`, `Dockerfile`, `.env.example`, `src/config.py`, `src/main.py`, `tests/conftest.py`, `tests/test_infrastructure.py`

## How a health check flows

1. A client calls `GET /health`.
2. FastAPI resolves `Depends(get_settings)` and passes in the settings.
3. Three checks run at the same time, one for each service.
4. The route returns 200 if all three are healthy, or 503 if any are not.

## Docker

### `docker-compose.yml`

It defines four services: `api`, `postgres`, `redis`, and `rabbitmq`.

**Service names are hostnames.** Compose puts the containers on a shared network, so the API reaches the database at the host `postgres`. That is why `POSTGRES_SERVER` is `postgres` and not `localhost`.

**`depends_on` with `condition: service_healthy`.** A plain `depends_on` only waits for a container to start, and Postgres can be started but not yet accepting connections. This condition waits for its health check to pass.

**Health checks.** `pg_isready`, `redis-cli ping`, and `rabbitmq-diagnostics -q ping` each ask the service if it is ready. Compose repeats them at the `interval`, and marks a service unhealthy after the set number of `retries`.

**Volumes.**
- `.:/app` mounts your source code into the container. Combined with `uvicorn --reload`, saved edits apply without rebuilding the image.
- `postgres_data` is a named volume, so database data survives `docker compose down`. Only `docker compose down -v` deletes it.

**Ports.** `"5432:5432"` means host port, then container port. These mappings let tools on your machine connect. Containers talk to each other over the shared network without them. Port `15672` is the RabbitMQ management web UI.

**`command`.** This overrides the Dockerfile's `CMD`, so development adds `--reload` while the image default stays without it.

### `Dockerfile`

```dockerfile
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
```

**Why requirements are copied first:** Docker caches each layer and rebuilds from the first one that changed. Copying source code first would make every code edit reinstall all dependencies.

**`PYTHONUNBUFFERED=1`:** Sends logs out immediately, so they appear in `docker logs` straight away.

**`PYTHONDONTWRITEBYTECODE=1`:** Stops Python writing `.pyc` files, which would otherwise appear in your project through the mounted volume.

**`build-essential`:** Provides compilers in case a dependency has to build native code during install.

## Configuration: `src/config.py`

```python
class Settings(BaseSettings):
    POSTGRES_PORT: int = 5432
```

Each field is a typed setting with a default. Values are read in this order, highest priority first:

1. Real environment variables, such as those set in `docker-compose.yml`
2. The `.env` file
3. The default in the class

`extra="ignore"` means unknown keys in `.env` are skipped instead of raising an error.

### Derived URLs

```python
@property
def postgres_dsn(self) -> str:
```

`@property` lets you write `settings.postgres_dsn` without parentheses. The URLs are built from individual settings, so each one has a single source of truth.

The `postgresql+asyncpg://` prefix tells SQLAlchemy both the database type and which driver to use.

### `@lru_cache()`

```python
@lru_cache()
def get_settings() -> Settings:
    return Settings()
```

`lru_cache` remembers a function's result. The first call builds `Settings`, and later calls return the same object, so `.env` is not reread on every request. This is similar to a singleton in Laravel's container.

FastAPI's `Depends(get_settings)` also makes settings replaceable in tests with `app.dependency_overrides`.

**Laravel comparison:** It is similar to running `config:cache`, because changed values are not picked up until the process restarts.

## Application: `src/main.py`

### Why almost everything is `async`

An `async def` function can pause at each `await` while it waits for a network response. The event loop then runs other work on the same thread.

PHP-FPM normally ties up one worker per request while it waits. Here, one process can handle many waiting requests at once. The catch is that one blocking call stalls everything, which is why this project uses async clients throughout.

### `asyncio.gather`

```python
postgres_task = check_postgres(settings)
redis_task = check_redis(settings)
rabbitmq_task = check_rabbitmq(settings)

postgres_res, redis_res, rabbitmq_res = await asyncio.gather(
    postgres_task, redis_task, rabbitmq_task
)
```

Calling an `async def` function does not run it. It returns a coroutine object, and the `*_task` variables hold these. `gather` runs them concurrently and returns results in the order they were passed in, so total time is close to the slowest check rather than the sum of all three.

Each check catches its own exceptions and returns a dictionary, so one failing service cannot abort the others. That is why `gather` does not need `return_exceptions=True` here.

### Each health check

**Postgres:** Creates an engine, opens a session, and runs `SELECT 1`. An engine manages a connection pool, and a session is one unit of work on top of it.
- `pool_pre_ping=True` tests a pooled connection before use.
- `expire_on_commit=False` stops objects being reloaded after a commit. In async code, an automatic reload could trigger database access where it is not allowed.

**Redis:** Sends `PING`. `socket_timeout=2.0` stops a stuck socket hanging the route.

**RabbitMQ:** Opens and closes a connection, which proves the broker is reachable and the login works.

### Setting the status code

```python
async def health_check(response: Response, ...):
    ...
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
```

Adding a `Response` parameter lets a route change the status code while still returning a dictionary. A 503 tells load balancers and monitoring that the service is not ready.

## Tests

### `tests/conftest.py`

pytest automatically loads `conftest.py`, and its fixtures are available to all tests in that folder.

```python
@pytest_asyncio.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
```

`ASGITransport` sends requests directly to the app in-process, with no real server or network. Code before `yield` is setup, and code after it is teardown. This resembles Laravel's feature tests, which also call the app without a real HTTP server.

### `pytest.ini`

```ini
[pytest]
testpaths = tests
asyncio_default_fixture_loop_scope = function
asyncio_default_test_loop_scope = function
```

This is pytest's project configuration, and pytest picks it up automatically. It is similar in purpose to `phpunit.xml`. `testpaths` says where tests live.

**Event loop scope.** An event loop is the scheduler that runs async code. The scope sets how long one loop lives, and `function` gives every test and fixture a new one. That isolates tests from each other, at a tiny cost in speed.

A connection belongs to the loop that created it. When Unit 2 adds shared async clients, a client created in one loop and used in another will fail with an error such as "attached to a different loop". If a shared fixture is ever needed, it and the tests using it need a matching wider scope.

**Why the old `event_loop` fixture was removed.** Older `pytest-asyncio` versions let you override a fixture of that name to control the loop. Version 1.0 removed that fixture, so the code had become a normal fixture that nothing used. `pytest --setup-show` prints the fixtures each test really sets up, and `event_loop` never appeared. Scope is now set in configuration instead. `requirements.txt` requires `pytest-asyncio>=1.0.0` to match.

### `tests/test_infrastructure.py`

- `test_root_endpoint`: checks that `/` responds as expected.
- `test_settings_loading`: checks the app name and the format of the derived URLs.
- `test_health_check_endpoint`: requires 200 with all three services healthy.
- `test_health_check_reports_failed_service`: breaks Redis on purpose and requires 503, with Redis unhealthy and the other two still healthy.

`test_health_check_endpoint` talks to the real services, so it only passes inside the Compose network. A failure outside Docker is expected.

### Replacing a dependency in a test

```python
broken_settings = settings.model_copy(
    update={"REDIS_HOST": "localhost", "REDIS_PORT": 1}
)
app.dependency_overrides[get_settings] = lambda: broken_settings
try:
    response = await async_client.get("/health")
finally:
    app.dependency_overrides.clear()
```

`model_copy(update=...)` returns a changed copy, so the cached `Settings` object stays intact for other tests. This matters because `lru_cache` shares that one object.

`dependency_overrides` tells FastAPI to call a replacement whenever a route asks for `Depends(get_settings)`. It is similar to `$this->app->instance()` or `$this->mock()` in a Laravel test. The `try/finally` always clears the override, even if the request raises an error, so it can't affect later tests.

Port 1 has nothing listening, so the connection is refused immediately and the test stays fast.

### Why a test needs to be able to fail

The old test accepted 200 or 503, so it passed even during an outage. A test that can't fail gives false confidence. To prove the new test can fail, run it with broken settings:

```bash
docker compose exec -T -e REDIS_HOST=localhost -e REDIS_PORT=1 api \
  python -m pytest tests/test_infrastructure.py::test_health_check_endpoint
```

The `-e` flags set environment variables for that one command, which take priority over everything else in `Settings`. The test then fails with `assert 503 == 200`, which shows it detects an outage.

## Git ignore rules: `.gitignore`

`.gitignore` lists files Git should leave untracked. Here that means secrets, generated caches, virtual environments, and IDE settings.

```gitignore
.env
.env.*
!.env.example
```

Git applies these patterns in order, and a later match wins. `.env.*` ignores variants such as `.env.production`, and the `!` adds `.env.example` back, so the template stays in the repo while real secrets don't. This is the same convention as a Laravel project's `.env` and `.env.example`.

**`.gitignore` only affects untracked files.** A file that is already committed stays tracked even if it matches a pattern. That is why `.idea/` also needed `git rm -r --cached .idea`. The `--cached` flag removes files from Git's index and keeps them on disk, so the IDE settings still work. They do remain in earlier commits.

**A secret that has been committed must be replaced.** Removing it later doesn't remove it from history, so the safe fix is to revoke it and issue a new one. This matters from Unit 2, when `.env` starts holding `GITHUB_TOKEN`.

## Known limitations

- **A new engine per check.** `check_postgres` builds an engine on every call, which is fine for a small health route but not for real database code. Unit 2 onward should share one engine. It also only disposes the engine on success, so a failed check skips the cleanup.
- **An unclosed Postgres connection.** The verified test run prints a `ResourceWarning` for an unclosed asyncpg connection. The likely cause is that `engine.dispose()` runs inside the `async with` block, while the session is still holding its connection. Moving cleanup outside the block is step 0.5, which will confirm this.
- **Errors are returned to the caller.** The health response includes `str(e)`, which can expose internal details on a public endpoint.
- **Passwords aren't URL-encoded.** Characters such as `@` or `/` in a password would break the derived URLs.
- **`sessionmaker` is the older style.** SQLAlchemy 2.0 also offers `async_sessionmaker`, which is the typed choice for async code.
- **`DEBUG` is defined but not used** in the code so far.

## Verification record

Checked on 2026-11-01 against the running Compose stack:

- `docker compose ps`: `postgres`, `redis`, and `rabbitmq` were healthy, and `api` was up. `api` has no health check of its own.
- `GET /health`: returned 200 with all three services healthy.
- `docker compose exec -T api python -m pytest -v`: 3 passed at first, and 4 passed after step 0.3 tightened the health tests.

Versions installed: Python 3.12.16, pytest 9.2.0, pytest-asyncio 1.4.1.post1, FastAPI 0.162.0, SQLAlchemy 2.1.6, Pydantic 2.14.2, redis 8.1.0, aio-pika 10.2.1.

Run tests inside the `api` container. Outside Docker, hostnames such as `postgres` don't resolve.
