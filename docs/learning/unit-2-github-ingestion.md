# Unit 2: GitHub Ingestion

Unit 2 connects the app to GitHub. It receives webhook events, turns them into jobs, and fetches the pull request diff. This file grows as the unit is built, and it only describes what exists so far.

**Files so far:** `src/config.py`, `src/schemas.py`, `.env.example`, `tests/test_schemas.py`, `tests/fixtures/pull_request_opened.json`

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

## Known limitations

- **The local `.env` holds placeholders.** Both values are `change-me` until a real token and webhook exist, which step 2.11 needs. Nothing uses them yet.
- **Nothing uses the schemas yet.** The route, job store, and worker arrive in later steps, and they may show that a field is missing.
- **Status changes aren't checked.** `JobStatus` lists valid states, but nothing stops a jump such as `completed` back to `queued`.
- **Only the GitHub secrets are hidden.** The Postgres and RabbitMQ passwords are still plain strings, so they still print with the settings object.

## Verification record

Checked on 2026-11-01 in the `api` container: `docker compose exec -T api python -m pytest -q` gave 13 passed, and `.env` is ignored by Git. The three new test cases failed before the fields existed.

Step 2.2, same day: 34 passed with no warnings. The schema tests failed first because the models didn't exist. As a check, narrowing `action` to the reviewed actions made 3 tests fail, and the change was then reverted.
