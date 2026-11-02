# Unit 2: GitHub Ingestion

Unit 2 connects the app to GitHub. It receives webhook events, turns them into jobs, and fetches the pull request diff. This file grows as the unit is built, and it only describes what exists so far.

**Files so far:** `src/config.py`, `src/schemas.py`, `src/github.py`, `src/main.py`, `.env.example`, `tests/test_schemas.py`, `tests/test_signature.py`, `tests/test_webhooks.py`, `tests/fixtures/pull_request_opened.json`, `src/cache.py`, `tests/test_cache.py`, `tests/conftest.py`, `src/queue.py`, `tests/test_queue.py`

## GitHub settings

`src/config.py` has two new fields:

```python
GITHUB_TOKEN: SecretStr
GITHUB_WEBHOOK_SECRET: SecretStr
```

**`GITHUB_TOKEN`** lets the app call the GitHub API. It is a fine-grained personal access token, limited to chosen repositories, with Pull requests read and write and Contents read.

**`GITHUB_WEBHOOK_SECRET`** is a shared value you also enter on GitHub. GitHub uses it to sign each webhook, and the app will use it to check that signature in step 2.3.

Neither has a default, so the app won't start without them, matching the rule for other credentials in [decisions.md](decisions.md).

### `SecretStr`

`SecretStr` is a Pydantic type that wraps a string and hides it when printed:

```python
settings.GITHUB_TOKEN                     # SecretStr('**********')
settings.GITHUB_TOKEN.get_secret_value()  # the real value
```

The hiding covers `print`, logs, and error output that show the object. The real value takes a deliberate `get_secret_value()` call, which also makes every use easy to search for. The nearest Laravel idea is `#[\SensitiveParameter]`, which hides a value from stack traces, or the `$hidden` property on a model.

It protects against accidents only. Any code in the app can still read the value.

## Schemas: `src/schemas.py`

A Pydantic `BaseModel` is a class that validates data when it is created. It covers the same ground as a Laravel FormRequest for input and an API Resource for output, but the rules come from the type hints, so `number: int` is both the type and the validation rule.

### `PRWebhookPayload`

This describes the incoming webhook, using nested models that follow GitHub's JSON:

```python
class PRWebhookPayload(BaseModel):
    action: str
    repository: GitHubRepository
    pull_request: GitHubPullRequest
```

So the code reads `payload.pull_request.head.sha`, and a missing `sha` fails validation instead of causing a `KeyError` later. It's the same idea as Laravel's `'pull_request.head.sha' => 'required|string'`, except the result is a typed object.

Three choices shape it:

- **Only the needed fields are declared.** Pydantic ignores undeclared fields by default, so GitHub adding a field can't break the app.
- **`action` is a plain `str`.** It would be tempting to allow only `opened`, `reopened`, and `synchronize`, but then a `closed` event would fail validation and GitHub would see an error. Skipping an action is the route's decision, in step 2.4, and a skipped event is still a valid one.
- **It only fits `pull_request` events.** A `ping` has no `pull_request` key and would fail, so the route has to check the event type before validating.

### Job models

`JobStatus` lists the four states as a `StrEnum`, an enum whose members are also strings. `JobStatus.QUEUED == "queued"` is true, and text from JSON converts straight back to a member. It's like a PHP backed enum, `enum JobStatus: string`. An unknown status is rejected.

`JobResponse` is the short reply to a webhook. `JobStatusDetails` is the full record: the job's status, what it reviews, its timestamps, and an optional `error`. Later steps will store it in Redis and return it from the jobs endpoint.

`error: Optional[str] = None` means the value may be missing. `Optional[str]` allows `None`, and the `= None` is what makes the field not required. The type alone doesn't.

`model_dump_json()` turns a model into JSON, and `model_validate_json()` validates JSON back into a model, restoring the enum and `datetime` types. Redis only stores text, so step 2.5 depends on this round trip.

### Tests

`tests/fixtures/pull_request_opened.json` is a sample event with placeholder names. It was written by hand from memory of GitHub's format, with only a subset of the real fields, so it isn't a captured event. Step 2.11 is the first check against a real one.

## Signature verification: `src/github.py`

The webhook URL is public, so anyone could post a fake pull request event to it. GitHub proves an event is genuine by signing it:

1. You and GitHub share `GITHUB_WEBHOOK_SECRET`.
2. GitHub computes an HMAC-SHA256 of the request body using that secret, and sends it in the `X-Hub-Signature-256` header as `sha256=<hex>`.
3. The app computes the same value and compares the two.

An HMAC is a hash that also needs a key. Without the secret, nobody can produce the right value for a body, and changing one byte of the body changes the result completely. Laravel has the same tool in `hash_hmac('sha256', $request->getContent(), $secret)`, and `hash_equals()` is the PHP version of `hmac.compare_digest`.

### Walking through `verify_signature`

```python
def verify_signature(body: bytes, signature_header: Optional[str], secret: str) -> bool:
    if not secret or not signature_header:
        return False

    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"

    return hmac.compare_digest(expected.encode(), signature_header.encode())
```

**`body` is `bytes`, on purpose.** The signature covers the exact bytes GitHub sent. `{"a":1}` and `{"a": 1}` mean the same JSON but have different signatures, so parsing and re-encoding would break a valid signature. The check has to come first for a second reason too: code shouldn't parse data it doesn't yet trust. This is like using `$request->getContent()` rather than `$request->all()`. In Python, `str` is text and `bytes` is raw data, and `.encode()` converts one to the other. PHP strings are bytes all along, so this split is new.

**`if not secret` blocks an empty secret.** `GITHUB_WEBHOOK_SECRET=""` still counts as set, so the required-field check lets it through. HMAC with an empty key works fine, and since anyone could compute it, every forged request would pass.

**`compare_digest` instead of `==`.** A normal comparison stops at the first different character, so a wrong guess that matches further along takes slightly longer. An attacker can measure that across many requests and work out the value piece by piece. `compare_digest` takes the same time wherever the values differ.

**Comparing bytes rather than strings.** `compare_digest` raises `TypeError` on a string with non-ASCII characters, and the header comes from the sender. Comparing strings would turn a junk header into a crash instead of a rejection.

**The whole string is compared, prefix included.** That rejects a missing prefix and `sha1=` without extra branches.

The function returns `True` or `False` and knows nothing about HTTP. Turning `False` into a 401 is the route's job, in step 2.4.

### Tests

`tests/test_signature.py` uses the worked example from GitHub's documentation as its main case, and the value was also confirmed with `openssl`. The other expected signatures were computed with `openssl` too. Taking them from `verify_signature` itself would only show the function agrees with itself.

## Webhook route: `POST /api/v1/webhooks/github/pr`

The route in `src/main.py` runs these checks in a fixed order, and the order is the design:

| Order | Check | If it fails |
|---|---|---|
| 1 | Is the signature valid over the raw bytes? | 401, nothing parsed |
| 2 | Is this a `pull_request` event? | 200 `{"status": "ignored"}` |
| 3 | Does the body fit `PRWebhookPayload`? | 422 `Invalid payload` |
| 4 | Is the action `opened`, `reopened`, or `synchronize`, and is the repository on the allowlist? | 200 `{"status": "ignored"}` |
| 5 | Is there an `X-GitHub-Delivery` header? | 400 `Missing delivery ID` |
| 6 | Has this delivery already created a job? | 200 `{"status": "duplicate", "job_id": ...}` |
| 7 | Accepted: the job is saved as `queued` and published to RabbitMQ | 202 with `job_id` and `queued` |

If saving or publishing fails, the claim is released and the request fails with a 500. A failed publish also marks the job `failed`.

**Why the body isn't a function parameter.** The usual FastAPI style is `payload: PRWebhookPayload` in the signature, like type-hinting a FormRequest in a Laravel controller. FastAPI would then parse and validate the body before your code runs. That is the wrong order here, because the signature must be checked first, on the original bytes. So the route takes `request: Request`, calls `await request.body()`, and calls `model_validate_json` itself. Laravel's nearest pattern is verifying the signature in middleware before the controller reads the input.

**Headers as parameters.** `x_hub_signature_256: Optional[str] = Header(default=None)` makes FastAPI read the `X-Hub-Signature-256` header. It matches on the name, turning underscores into hyphens and ignoring case. A missing header gives `None`, and `verify_signature` turns that into a rejection, so the response is 401 and not FastAPI's automatic 422.

**Why ignored events get a 200.** They are valid events that need no work, and an error status would mark them as failed in GitHub's webhook delivery log. The route's default of 202 means work was accepted, so these branches change it through `response.status_code`, as `/health` does for its 503.

**Why 422 hides the details.** The response says only `Invalid payload`. The sender already proved it holds the secret, but there's no need to return validation details, and the same reasoning applies to `str(e)` in the health route's known limitation.

`from None` after `raise` drops the original `ValidationError` from the exception chain, so it doesn't appear as the cause of the `HTTPException`.

### Repository allowlist

`REVIEW_REPOSITORIES` in `.env` lists the repositories to review as comma-separated `owner/name` values:

```
REVIEW_REPOSITORIES="example-org/example-repo,example-org/another-repo"
```

It is configuration rather than code, so adding a repository needs no code change, and no repository name is committed. Real values belong only in the local `.env`. After editing `.env`, run `docker compose up -d` so the container picks up the change, because Compose passes values in when it creates the container.

```python
@property
def review_repositories(self) -> set[str]:
    return {
        name.strip().lower()
        for name in self.REVIEW_REPOSITORIES.split(",")
        if name.strip()
    }
```

**The field is a `str`, with a property to parse it.** A `list[str]` field looks more natural, but pydantic-settings reads complex types from the environment as JSON, so `a/b,c/d` would crash at startup. A `@property` makes a method read like an attribute, as with `postgres_dsn`. It's like a Laravel accessor, and it's easy to mistake for a stored field: it is recalculated on each access.

**The `{... for ... if ...}` is a set comprehension.** It builds a set in one expression, like `collect(explode(',', $value))->map(...)->filter()` in Laravel. The `if` drops blanks from values such as a trailing comma. A set also makes `in` a fast lookup and removes duplicates.

**Empty means nothing is reviewed.** If the setting is missing, the app reviews nothing, rather than everything. This is failing closed: a forgotten setting leaves the app idle, where the opposite mistake would send every signed event to the LLM.

**Both sides are lowercased.** GitHub treats `Example-Org/Repo` and `example-org/repo` as the same repository, so the property and the route both lowercase before comparing.

**Unlisted repositories get a 200.** The event is valid and simply needs no work, so it follows the same rule as ignored actions.

### Tests

`tests/test_webhooks.py` sends real HTTP requests through the app with `httpx`. The `webhook_secret` fixture swaps in a test secret with `dependency_overrides`, and `autouse=True` applies it to every test in the file.

The order has its own tests. A bad signature on a body that isn't JSON must give 401, because a 422 would mean untrusted data was parsed. A body with unusual spacing must give 202, which would fail if the route re-encoded it before checking.

## Job store: `src/cache.py`

The job store keeps the current status of each job in Redis, so the API can answer "what happened to job X?" while the worker does the review elsewhere. The webhook route saves each accepted job here, and step 2.8 will read them back through the API.

```python
JOB_TTL_SECONDS = 24 * 60 * 60

def job_key(job_id: str) -> str:
    return f"job:{job_id}"

class JobStore:
    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def save(self, job: JobStatusDetails) -> None:
        await self._redis.set(
            job_key(job.job_id), job.model_dump_json(), ex=JOB_TTL_SECONDS
        )

    async def get(self, job_id: str) -> Optional[JobStatusDetails]:
        raw = await self._redis.get(job_key(job_id))
        if raw is None:
            return None
        return JobStatusDetails.model_validate_json(raw)
```

**One JSON string per job.** `model_dump_json()` turns the Pydantic model into JSON, including the `datetime` fields and the `JobStatus` enum, and `model_validate_json()` turns it back and validates it. Reading it back through the model means a corrupt or outdated record fails loudly, rather than reaching the API as a half-filled dict. In Laravel terms, this is `Cache::put("job:$id", $job->toJson(), now()->addDay())` with a typed DTO on the way out.

**The expiry is set in the same command.** `set(..., ex=...)` stores the value and its expiry at once. A separate `expire` call afterwards could be skipped if the app crashed in between, leaving a key that never expires. Every save restarts the 24 hours, so a job expires a day after its last change, not a day after it was created.

**The client is passed in.** `JobStore` takes a Redis client instead of creating one. The app will create one client and share it, and the tests pass their own. It's the same idea as Laravel's constructor injection: the class says what it needs, and something else builds it.

**`job_key` is a function.** The `job:` prefix is used in the store, in the tests, and later by anything that inspects Redis, so it lives in one place. The `job:{job_id}:stream` key in Unit 5 will follow the same scheme.

**`_redis` has a leading underscore.** Python has no `private`. The underscore is a convention meaning "internal, don't use from outside", which tools and readers respect but the language doesn't enforce.

### Tests

`tests/test_cache.py` runs against the real Redis container, not a fake, because the expiry is the behaviour under test and a fake would only show that the fake works. The `make_job` fixture builds records with random IDs and deletes their keys afterwards, so tests don't see each other's data and nothing is left in the Redis the running app uses. A fixture can hand back a function, as `make_job` does. That is pytest's version of a Laravel model factory.

## Delivery deduplication

Every webhook carries an `X-GitHub-Delivery` header, an ID that is unique to each delivery. GitHub's docs say a redelivery, sent by hand from the webhook settings or through the API, reuses the original ID. That hasn't been checked against real GitHub yet, and step 2.11 will confirm it. GitHub doesn't retry failed deliveries on its own.

Without deduplication, each redelivery would create a second job and a second review of the same event. The route prevents that by claiming the delivery ID before it saves the job.

```python
job_id = str(uuid.uuid4())
existing_job_id = await store.claim_delivery(x_github_delivery, job_id)
if existing_job_id is not None:
    response.status_code = status.HTTP_200_OK
    return {"status": "duplicate", "job_id": existing_job_id}
```

### The claim: `SET NX GET`

```python
existing = await self._redis.set(
    delivery_key(delivery_id), job_id, nx=True, get=True, ex=JOB_TTL_SECONDS
)
```

This one Redis command does three things:

- **`nx=True`** sets `delivery:{id}` only if it doesn't exist yet.
- **`get=True`** returns the value that was there before, or `None` if there was none.
- **`ex=...`** gives the claim the same 24 hours as the job.

So `None` means "this request won", and anything else is the job ID that won earlier. Redis runs one command at a time, so if two copies of a delivery arrive at the same moment, exactly one gets `None`. A separate "check, then set" would leave a gap in which both copies see no claim and both create a job. That gap is a race condition. Laravel's `Cache::add()`, which only stores a key that doesn't exist, is the same idea, and so is `Cache::lock()`.

`NX` together with `GET` needs Redis 7.0 or later. The Compose image is `redis:7-alpine`, which was 7.4 when this was checked.

A repeat must leave the claim alone. Without `NX`, `GET` would still report the earlier job, but the repeat's job ID would overwrite the claim. That job is never saved, so a third delivery would be pointed at a job that doesn't exist. `test_losing_claim_does_not_replace_the_winner` covers this. It was added after that exact break went unnoticed by the first set of tests.

### Order inside the route

**The claim comes after every filter.** A claim means "this delivery created a job". If ignored events claimed their IDs too, the claim would mean something weaker, and `test_ignored_event_does_not_claim_its_delivery` guards against that.

**A missing delivery ID is a 400.** GitHub always sends one, and the request has already passed the signature check, so a missing header points to a broken sender rather than an attacker. Ignored events don't need one, because they create nothing.

**A duplicate gets a 200, not a 202.** Nothing new was accepted. The body still names the original job, so whoever redelivered can follow it.

**A failed save releases the claim.** If saving the job raises, the `except` block deletes the claim and re-raises, so the request still fails with a 500. Without the release, the delivery would stay claimed by a job that doesn't exist, and a redelivery would be told it was a duplicate. `raise` with no argument re-raises the exception currently being handled, like `throw $e;` inside a PHP `catch`.

### One Redis client for the app: `lifespan`

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.redis = aioredis.from_url(get_settings().redis_url)
    yield
    await app.state.redis.aclose()

app = FastAPI(..., lifespan=lifespan)
```

FastAPI runs the code before `yield` once at startup and the code after it once at shutdown. One client holds a pool of connections that all requests share, rather than each request connecting on its own as the `/health` check does. `app.state` is a place to hang shared objects on the app. The nearest Laravel idea is binding a singleton in a service provider's `register` method.

`@asynccontextmanager` turns a generator function, one that uses `yield`, into an object with a start and an end, which is what FastAPI expects for `lifespan`. pytest fixtures with `yield` work the same way.

### The store as a dependency

```python
def get_job_store(request: Request) -> JobStore:
    return JobStore(request.app.state.redis)
```

The route asks for `store: JobStore = Depends(get_job_store)`, the same way it asks for settings. Tests replace it with `app.dependency_overrides[get_job_store]`, which matters here because `httpx`'s `ASGITransport` doesn't run the lifespan, so `app.state.redis` doesn't exist in tests. Instead, each test passes a store built on its own Redis client. That also keeps each client on the event loop of the test that created it, since pytest-asyncio gives every test a new loop.

The lifespan is covered by a manual check instead, recorded below.

### Tests

The tests use the real Redis container. `deliver()` sends a fresh `test-<uuid>` delivery ID unless a test passes its own. Passing `delivery=None` leaves the header out. An autouse fixture overrides the store and, afterwards, deletes every `delivery:test-*` claim along with the job it points to. `redis_client` moved to `tests/conftest.py`, because both test files use it.

`test_failed_save_releases_the_claim` uses a `FailingStore` subclass whose `save` raises. `ASGITransport` passes app errors through to the test rather than turning them into a 500, so the test expects the exception itself.

## Publishing jobs: `src/queue.py`

Once a job is saved, the route publishes a message to RabbitMQ, and the worker in step 2.10 will take it from there. This is the part Laravel hides behind `dispatch(new ReviewJob(...))`. Here the queue, the message, and the broker's answer are all handled in code.

### The queue: durable

```python
QUEUE_NAME = "pr_analysis_queue"

async def declare_queue(channel, name=QUEUE_NAME):
    return await channel.declare_queue(name, durable=True)
```

**Declaring is idempotent.** `declare_queue` creates the queue if it's missing and does nothing if it already exists with the same settings, so it is safe to call every time the app starts. Both the API and the worker will declare it, so neither depends on the other starting first.

**Declare before publishing.** A message sent to a queue that doesn't exist is dropped, so the lifespan declares the queue before the app takes any requests.

**`durable=True` keeps the queue itself across a broker restart.** It says nothing about the messages in it, which is the next part.

**Settings can't change once the queue exists.** RabbitMQ rejects a declaration whose settings differ from the existing queue's, with `PRECONDITION_FAILED - inequivalent arg 'durable'`. That happened during verification: a deliberate `durable=False` break stopped the dev server from starting. Changing a queue's settings later means deleting the queue first, and any messages in it go with it. The durability test uses this rule: it declares the test queue again with `durable=False`, and the broker's refusal proves the queue really is durable.

### The message: persistent JSON

```python
amqp_message = aio_pika.Message(
    body=message.model_dump_json().encode(),
    content_type="application/json",
    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    message_id=message.job_id,
)
```

**`PERSISTENT` writes the message to disk**, so it survives a broker restart, as long as the queue is durable too. Either one alone loses the message. Verification checked this for real: one persistent and one transient message went into a durable queue, RabbitMQ was restarted, and only the persistent one was still there.

**The body is a `ReviewJobMessage`**, a new model in `src/schemas.py` with `job_id`, `repo_name`, `pr_number`, and `head_sha`. That's everything the worker needs to fetch the diff, so the worker doesn't depend on the Redis record still existing. The job's status deliberately isn't in the message, because it would be out of date as soon as the worker changed it. AMQP bodies are bytes, hence `.encode()`.

**`message_id` is the job ID**, so a message can be traced back to its job in RabbitMQ's management UI.

### The broker's answer: check for an ack

```python
result = await self._channel.default_exchange.publish(
    amqp_message, routing_key=self._queue_name
)
if not isinstance(result, Basic.Ack):
    raise PublishError(...)
```

**The default exchange** routes a message to the queue whose name equals the routing key. RabbitMQ always sends messages through an exchange, and this built-in one is the simplest. Named exchanges come in when one message must reach several queues.

**Publisher confirms.** aio-pika turns on confirms for each channel by default, so `publish` waits until the broker answers. `Basic.Ack` means the broker has taken responsibility for the message. For a persistent message on a durable queue, that means it has been written to disk.

**An unroutable message doesn't raise.** This was checked before the code was written. If no queue matches the routing key, the broker sends the message back, and `publish` returns that returned message instead of raising. A plain `await publish(...)` would treat that as success, and the job would be lost silently. So anything other than an ack raises `PublishError`, and `test_publishing_to_a_missing_queue_raises` proves it.

`Basic` comes from `pamqp`, the AMQP library underneath aio-pika. See [libraries.md](libraries.md).

### In the route: save, then publish

The route saves the job first and publishes second, so a worker can never receive a job that has no record.

If publishing fails, the job is marked `failed`, with an `error` naming the exception type, and the claim is released. The request then fails with a 500. A job left as `queued` with no message would wait forever. Releasing the claim lets a redelivery create a new job, so the failed job stays behind as a record of the attempt.

### One connection for the app

The lifespan opens one RabbitMQ connection and one channel at startup, next to the Redis client, and `get_job_publisher` builds a `JobPublisher` on that channel. Unlike the Redis client, `connect_robust` connects straight away, so the API won't start without RabbitMQ. Compose already starts the API only once RabbitMQ is healthy.

**`connect_robust` reconnects by itself, but not instantly.** Verification restarted RabbitMQ under a running server and sent a delivery every half second. Every delivery failed with a 500 (`ChannelInvalidStateError`) for about 12.5 seconds, then they succeeded again with no intervention. Each failed request marked its job `failed` and released its claim, as designed.

### Tests

`tests/test_queue.py` runs against the real RabbitMQ container. The `test_queue` fixture in `tests/conftest.py` declares a durable queue with a unique `test-<uuid>` name and deletes it afterwards. The webhook tests send the route's messages to that queue too, through a `get_job_publisher` override, because a running worker would take test messages from the real queue. The `drain` helper takes every message off a queue with `queue.get(fail=False)`, which returns `None` when the queue is empty, instead of waiting.

`while (message := await queue.get(fail=False)) is not None:` uses `:=`, the walrus operator. It assigns and tests in one expression, like `while (($message = $queue->get()) !== null)` in PHP.

## Known limitations

- **The local `.env` holds placeholders.** Both values are `change-me` until a real token and webhook exist, which step 2.11 needs. `change-me` is a public value, so once the route exists, anyone reaching it could sign with it until a real secret is set.
- **Nothing consumes the queue yet.** Accepted jobs are published to `pr_analysis_queue`, but the worker arrives in step 2.10, so messages wait there. Step 2.8 adds the API to look a job up.
- **A broker outage fails deliveries.** While RabbitMQ is down or reconnecting, about 12.5 seconds for a restart in testing, every accepted delivery fails with a 500, and GitHub doesn't retry on its own. Each one has to be redelivered by hand. Retrying the publish, or saving messages to send later (the outbox pattern), would fix this, and neither is planned yet.
- **A second failure can leave a stuck job.** If marking a job `failed` after a failed publish itself fails, for example because Redis also went down, the job stays `queued` with no message, the claim isn't released, and the new error replaces the original one.
- **The module name `queue.py` matches the standard library's `queue`.** `src.queue` is safe as long as code is run as part of the `src` package, for example `python -m src.worker`. Running a file inside `src/` directly would put `src/` on the import path, and `import queue` anywhere, including inside the standard library, would then find this file.
- **The local allowlist is empty.** `REVIEW_REPOSITORIES` is `""` in the local `.env`, so every event is ignored until a real repository is added for step 2.11.
- **The allowlist only reads from the environment.** Step 4.10 adds the database as a source.
- **A misspelled name fails silently.** The event is ignored with a 200, and nothing warns that the value doesn't match a real repository.
- **A valid request can be replayed.** The signature covers only the body, not the headers or a timestamp, so a captured request stays valid. Deduplication catches an exact copy for 24 hours, but the `X-GitHub-Delivery` header isn't signed, so a replay with a new ID gets through. It handles redeliveries, not attacks.
- **The same commit can be reviewed twice.** Deduplication is per delivery. Two different events for the same head commit, such as a quick close and reopen, each create a job.
- **Redis and RabbitMQ are both required to accept a job.** If either is down, the route fails with a 500, and the delivery has to be sent again once it's back.
- **The worker doesn't use the schemas yet.** The route and job store now use `JobStatusDetails`, and the worker in step 2.10 may show that a field is missing.
- **Status changes aren't checked.** `JobStatus` lists valid states, but nothing stops a jump such as `completed` back to `queued`.
- **Saves can overwrite each other.** `save` replaces the whole record, so if two writers load, change, and save the same job, the later save wins and the earlier change is lost. Only the worker changes a job after it is created, so this isn't expected to happen yet.
- **Redis isn't durable storage.** If Redis restarts without persistence, or a job is older than 24 hours, the record is gone. Lasting results belong in Postgres, which Units 3 and 4 add.
- **Only the GitHub secrets are hidden.** The Postgres and RabbitMQ passwords are still plain strings, so they still print with the settings object.

## Verification record

Checked on 2026-11-01 in the `api` container: `docker compose exec -T api python -m pytest -q` gave 13 passed, and `.env` is ignored by Git. The three new test cases failed before the fields existed.

Step 2.7, on 2026-11-02: 101 passed. The new tests failed first, because `src/queue.py` didn't exist. Before writing the publisher, a probe showed that with confirms on, an unroutable publish returns a returned message rather than raising. Six deliberate breaks were each caught and then reverted: a non-durable queue, transient messages, not checking for an ack, the route not publishing, a failed publish leaving the job `queued`, and a failed publish keeping the claim. Two live checks were run. First, a persistent and a transient message were published to a durable test queue and RabbitMQ was restarted: only the persistent one was left. Second, a temporary server was sent a delivery every half second across a RabbitMQ restart: it failed with 500 for about 12.5 seconds, then recovered by itself, and every successful publish reached `pr_analysis_queue`. All test jobs, claims, and messages were deleted afterwards.

A correction to the step 2.6 record: its check for leftovers searched only `*test-*` keys, and job keys don't contain that. Step 2.7 found 18 leftover fixture jobs and a `delivery:None` claim in Redis. They came from step 2.6's deliberate-break runs, which bypass the claims that the test cleanup relies on. They were deleted, and two normal runs then left no job keys. The step 2.7 checks counted every `job:*` and `delivery:*` key.

Step 2.6, same day: 92 passed. The new tests failed first, because `claim_delivery` and `get_job_store` didn't exist. Seven deliberate breaks were each caught and then reverted: never detecting duplicates, not releasing the claim after a failed save, no missing-ID check, not saving the job, a claim without an expiry, a claim without `NX`, and claiming before the filters. The `NX` break passed the first set of tests, which is why `test_losing_claim_does_not_replace_the_winner` was added. A temporary server in the `api` container, with a test allowlist, checked the lifespan: the first signed delivery gave 202, the repeat gave 200 `duplicate` with the same job ID, both keys had a TTL of 86400, and shutdown completed. The test keys were deleted, and no `test-*` keys were left in Redis.

Step 2.5, same day: 80 passed, and the new tests failed first because `src/cache.py` didn't exist. Five deliberate breaks were each caught and then reverted: no expiry, a 12-hour expiry, a different key prefix, no check for a missing key, and storing a Python repr instead of JSON. No `job:*` keys were left in Redis after the run.

Step 2.13, on 2026-11-01: 73 passed with no warnings. Four deliberate breaks were each caught and then reverted: no allowlist check, treating an empty list as allow-all, case-sensitive matching, and not trimming spaces.

Step 2.4, same day: 65 passed with no warnings. Five deliberate breaks were each caught and then reverted: no signature check, re-encoding the body, no event filter, no action filter, and a fixed job ID. In the running API, a signed event returned 202, a forged one 401, and a `push` event 200.

Step 2.3, same day: 46 passed with no warnings. Four deliberate breaks were each caught and then reverted: no empty-secret check, comparing strings, ignoring the prefix, and using SHA-1.

Step 2.2, same day: 34 passed with no warnings. The schema tests failed first because the models didn't exist. As a check, narrowing `action` to the reviewed actions made 3 tests fail, and the change was then reverted.
