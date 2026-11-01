# Unit 2: GitHub Ingestion

Unit 2 connects the app to GitHub. It receives webhook events, turns them into jobs, and fetches the pull request diff. This file grows as the unit is built, and it only describes what exists so far.

**Files so far:** `src/config.py`, `.env.example`

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

## Known limitations

- **The local `.env` holds placeholders.** Both values are `change-me` until a real token and webhook exist, which step 2.11 needs. Nothing uses them yet.
- **Only the GitHub secrets are hidden.** The Postgres and RabbitMQ passwords are still plain strings, so they still print with the settings object.

## Verification record

Checked on 2026-11-01 in the `api` container: `docker compose exec -T api python -m pytest -q` gave 13 passed, and `.env` is ignored by Git. The three new test cases failed before the fields existed.
