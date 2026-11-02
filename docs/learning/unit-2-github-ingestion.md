# Unit 2: GitHub Ingestion

Unit 2 connects the app to GitHub. It receives webhook events, turns them into jobs, and fetches the pull request diff. This file grows as the unit is built, and it only describes what exists so far.

**Files so far:** `src/config.py`, `src/schemas.py`, `src/github.py`, `src/main.py`, `.env.example`, `tests/test_schemas.py`, `tests/test_signature.py`, `tests/test_webhooks.py`, `tests/fixtures/pull_request_opened.json`, `src/cache.py`, `tests/test_cache.py`

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
| 5 | Accepted | 202 with `job_id` and `queued` |

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

The job store keeps the current status of each job in Redis, so the API can answer "what happened to job X?" while the worker does the review elsewhere. It isn't connected to the route yet. Step 2.6 starts saving accepted jobs, and step 2.8 reads them back.

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

## Known limitations

- **The local `.env` holds placeholders.** Both values are `change-me` until a real token and webhook exist, which step 2.11 needs. `change-me` is a public value, so once the route exists, anyone reaching it could sign with it until a real secret is set.
- **Accepted jobs go nowhere yet.** The route returns a new `job_id` with status `queued`, but nothing is saved or queued, so that ID can't be looked up and the status is only a promise. The job store from step 2.5 exists but isn't called yet. Steps 2.6 and 2.7 connect it.
- **The local allowlist is empty.** `REVIEW_REPOSITORIES` is `""` in the local `.env`, so every event is ignored until a real repository is added for step 2.11.
- **The allowlist only reads from the environment.** Step 4.10 adds the database as a source.
- **A misspelled name fails silently.** The event is ignored with a 200, and nothing warns that the value doesn't match a real repository.
- **Repeated deliveries get a second job ID.** Step 2.6 adds deduplication.
- **A valid request can be replayed.** The signature doesn't cover a timestamp, so a captured request stays valid. Step 2.6 will catch repeats of the same delivery ID.
- **Nothing uses the schemas yet.** The route, job store, and worker arrive in later steps, and they may show that a field is missing.
- **Status changes aren't checked.** `JobStatus` lists valid states, but nothing stops a jump such as `completed` back to `queued`.
- **Saves can overwrite each other.** `save` replaces the whole record, so if two writers load, change, and save the same job, the later save wins and the earlier change is lost. Only the worker changes a job after it is created, so this isn't expected to happen yet.
- **Redis isn't durable storage.** If Redis restarts without persistence, or a job is older than 24 hours, the record is gone. Lasting results belong in Postgres, which Units 3 and 4 add.
- **Only the GitHub secrets are hidden.** The Postgres and RabbitMQ passwords are still plain strings, so they still print with the settings object.

## Verification record

Checked on 2026-11-01 in the `api` container: `docker compose exec -T api python -m pytest -q` gave 13 passed, and `.env` is ignored by Git. The three new test cases failed before the fields existed.

Step 2.5, on 2026-11-02: 80 passed, and the new tests failed first because `src/cache.py` didn't exist. Five deliberate breaks were each caught and then reverted: no expiry, a 12-hour expiry, a different key prefix, no check for a missing key, and storing a Python repr instead of JSON. No `job:*` keys were left in Redis after the run.

Step 2.13, on 2026-11-01: 73 passed with no warnings. Four deliberate breaks were each caught and then reverted: no allowlist check, treating an empty list as allow-all, case-sensitive matching, and not trimming spaces.

Step 2.4, same day: 65 passed with no warnings. Five deliberate breaks were each caught and then reverted: no signature check, re-encoding the body, no event filter, no action filter, and a fixed job ID. In the running API, a signed event returned 202, a forged one 401, and a `push` event 200.

Step 2.3, same day: 46 passed with no warnings. Four deliberate breaks were each caught and then reverted: no empty-secret check, comparing strings, ignoring the prefix, and using SHA-1.

Step 2.2, same day: 34 passed with no warnings. The schema tests failed first because the models didn't exist. As a check, narrowing `action` to the reviewed actions made 3 tests fail, and the change was then reverted.
