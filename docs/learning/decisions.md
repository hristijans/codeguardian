# Decision Log

Newest entries go at the bottom. If a decision changes, add a new entry and mark the old one as replaced, so the history stays readable.

Dates use the format YYYY-MM-DD. "Implemented" shows whether the decision exists in code yet.

## D1: Build the project as a learning reference first (2026-11-01)

**Decision:** Learning is the primary goal, and the system should also review real GitHub PRs.

**Why:** A real target forces realistic problems, such as untrusted input and webhook retries, that a simulation would hide. Learning still comes first, so explanation is part of the work.

**Consequence:** Every unit updates this documentation.

**Implemented:** Yes, as a process.

## D2: Build Unit 2 before LangGraph (2026-11-01)

**Decision:** Unit 2 comes next, and Unit 3 waits for it.

**Why:** The blueprint marked Unit 2 complete, but its code did not exist. Unit 3 plugs into a worker that has to exist first.

**Rejected:** Building the agent first and adding the queue later. That would be possible, but it would leave the agent unconnected to the real flow.

**Implemented:** No.

## D3: Use Ollama behind a provider setting (2026-11-01)

**Decision:** Start with local Ollama, and create chat models in one factory controlled by `LLM_PROVIDER`.

**Why:** Local models cost nothing per run and keep code on your machine, while the factory keeps a cloud model available if local quality is too low.

**Tradeoff:** Local models can be less reliable at structured output, so validation matters.

**Rejected:** Ollama with no way to switch, and a cloud model from the start, which costs money and sends diffs to a third party.

**Implemented:** Settings only. The factory arrives in Unit 3.

## D4: Connect with a fine-grained token (2026-11-01)

**Decision:** Review your own repositories using a fine-grained personal access token and one webhook per repository.

**Why:** It is the quickest way to reach real PRs, and a fine-grained token can be limited to specific repositories and permissions.

**Rejected:** A GitHub App, which needs a private key and installation tokens. It is the better model for reviewing other people's repositories, so the GitHub client is planned to make a later swap easy.

**Implemented:** No.

## D5: Receive local webhooks through smee.io (2026-11-01)

**Decision:** Use a smee.io relay during development.

**Why:** GitHub cannot reach a laptop directly. The relay needs no open ports or public server, and it forwards each delivery to localhost.

**Rejected:** A tunnel, which takes more setup; a deployed server, which brings hosting work forward; and replaying saved payloads only, which never proves the real connection.

**Implemented:** No.

## D6: Post automatically for low risk, ask for high risk (2026-11-01)

**Decision:** LOW and MEDIUM reviews post automatically. HIGH and CRITICAL reviews wait for approval.

**Why:** Routine reviews stay fast, and a human checks the results that matter most.

**Rejected:** Approving every review, which is slow, and never asking, which would remove the Unit 5 approval feature.

**Implemented:** No.

## D7: Force approval when a result can't be trusted (2026-11-01)

**Decision:** Truncated diffs and failed analyses always need approval, and CodeGuardian only posts comments. It never approves or merges a PR.

**Why:** PR text is written by the PR author, so it can try to manipulate the model into reporting low risk. Without this rule, that would skip the human check.

**Status:** Proposed by Claude while correcting the blueprint. It stands unless you change it.

**Implemented:** No.

## D8: Use conditional `interrupt()` for approval (2026-11-01)

**Decision:** An approval node calls `interrupt()`, and a conditional edge sends only risky reviews to it.

**Why:** `interrupt_before` pauses every run at a fixed node, which does not suit a risk-based rule. `interrupt()` also pairs with `Command(resume=...)`, which carries the approval data back into the graph.

**Watch out:** On resume, the node runs again from its first line. Code before `interrupt()` must be safe to run twice.

**Implemented:** No.

## D9: Stream progress with Redis Streams (2026-11-01)

**Decision:** Use Redis Streams for live progress events.

**Why:** Pub/Sub only reaches clients connected at that moment, so someone opening the page late would miss earlier events. A Stream keeps events, so they can be replayed.

**Implemented:** No.

## D10: Keep checkpointer tables out of Alembic (2026-11-01)

**Decision:** `AsyncPostgresSaver` manages its own tables through `setup()`. Alembic manages only application tables.

**Why:** The two would otherwise compete over the same schema. The saver also uses psycopg 3, so it needs a plain `postgresql://` connection string rather than the app's `postgresql+asyncpg://` one.

**Implemented:** No.

## D11: Keep personal data and repository links out of the repo (2026-11-01)

**Decision:** Nothing personal, no real repository links, and no secrets are committed. Repositories to review come from `REVIEW_REPOSITORIES` in the environment first, and from the database once Alembic exists. Tests use placeholders.

**Why:** The repo may become public, and Git history keeps whatever was committed. Treating repositories as configuration also means adding one needs no code change, which is the same idea as Laravel's `config()` over hardcoded values.

**Status:** The rule is yours. The `REVIEW_REPOSITORIES` name and the env-then-database order were Claude's call.

**Implemented:** The rule is. The allowlist arrives in steps 2.13 and 4.10.

## D12: Keep `CLAUDE.md` local (2026-11-01)

**Decision:** `CLAUDE.md` is ignored by Git and stays on your machine.

**Why:** It holds agent instructions rather than project code.

**Consequence:** A fresh clone has no `CLAUDE.md`. The workflow rules stay available in `docs/PLAN.md`. Earlier versions remain in commits that have not been pushed.

**Implemented:** Yes.

## D13: Work around the global git config instead of editing it (2026-11-01)

**Decision:** A local script, `.git/git-safe`, runs git against a filtered copy of the global config. Both stay in `.git/`, out of version control, because the problem belongs to one machine and not to the project.

**Why:** The global file is shared with other projects, so a fix for this project mustn't change it.

**Rejected:** Editing the global file, because it could affect other projects. A `-c` override, because git fails on the bad value before applying it. A repo-level setting, because the global file is still read and still fails. `direnv`, because it isn't installed and would add a dependency for one variable. A one-off copy, because it would drift from the global file. Keeping the script in the repo, because it only serves this machine.

**Implemented:** Yes.

## D14: Require credentials from `.env` (2026-11-01)

**Decision:** Usernames and passwords have no defaults in `src/config.py`, and Compose takes them from `.env`. A missing value stops startup.

**Why:** A default in source code is a committed credential, and it hides a missing setting by connecting anyway.

**Rejected:** Keeping development defaults, because they are the problem this step removes. Generating new passwords for the local `.env`, because Postgres only reads its password when the volume is created, so this would mean deleting the data or changing the password by hand for no gain on local throwaway values.

**Implemented:** Yes.

## D15: Use the owner's own email for commits (2026-11-01)

**Decision:** Commits use the owner's own email address, not GitHub's noreply address.

**Why:** It was the owner's choice, made before the first push.

**Rejected:** GitHub's noreply address, which keeps the real address out of public commit metadata.

**Consequence:** Once pushed, the address is visible in the commit metadata of this repository. This is the one place it appears: it stays out of files, as the no-personal-data rule requires.

**Implemented:** Yes.

## D16: Type GitHub secrets as `SecretStr` (2026-11-01)

**Decision:** `GITHUB_TOKEN` and `GITHUB_WEBHOOK_SECRET` are required `SecretStr` fields.

**Why:** A plain string prints in full wherever the settings object is shown, and a failing test printed the whole object during step 0.9. A GitHub token is a live credential, so it is hidden by default and code has to ask for it on purpose.

**Rejected:** Plain `str`, for that reason. Making them optional until the live test, because the app would start and only fail once a webhook arrived.

**Consequence:** The Postgres and RabbitMQ passwords are still plain strings. They are local values, and they are built into connection URLs.

**Implemented:** Yes.

## D17: An empty repository allowlist reviews nothing (2026-11-01)

**Decision:** `REVIEW_REPOSITORIES` defaults to empty, and empty means no repository is reviewed.

**Why:** Each review sends code to an LLM, so a missing setting should leave the app idle.

**Rejected:** Treating empty as "review everything", which is the riskier failure. Making the setting required, because the app can run safely without it. A `list[str]` field, because pydantic-settings reads it as JSON from the environment, and a comma-separated value is easier to write.

**Implemented:** Yes.

## D18: Store each job as one JSON string in Redis (2026-11-02)

**Decision:** `src/cache.py` saves each job as a JSON string under `job:{job_id}` with `SET ... EX 86400`, and reads it back through `JobStatusDetails`.

**Why:** The whole record is always read and written together, and one `SET` stores the value and its expiry at once. Pydantic already turns the model into JSON and back, with validation on the way out.

**Rejected:** A Redis hash per job, which allows updating one field without rewriting the rest, but stores every value as a string and needs a separate `EXPIRE` call. The RedisJSON module, which the standard Redis image doesn't include. Postgres, which is the place for lasting results but is more than short-lived status needs.

**Implemented:** Yes. The route doesn't call it yet.

## D19: Deduplicate on the delivery ID with `SET NX GET` (2026-11-02)

**Decision:** The webhook route claims `delivery:{X-GitHub-Delivery}` with one `SET NX GET EX 86400` after all filters, before saving the job. A repeat gets a 200 with the original job ID. A failed save releases the claim.

**Why:** One command makes the check and the claim atomic, so two copies of a delivery can't both create a job, and the winner's job ID comes back without a second read. Claiming before saving means the claim decides who creates the job.

**Rejected:** Checking with `GET` and then setting, which leaves a race between the two commands. `SET NX` followed by a separate `GET`, which has a smaller race if the claim expires between them. Deduplicating on the head commit SHA, which would also skip legitimate new events for the same commit and is a different question from "have I seen this delivery?". Storing the claim inside the job record, which can't be looked up by delivery ID. Returning 202 or 409 for a repeat: 202 says new work was accepted, and 409 would show the redelivery as failed in GitHub's log.

**Implemented:** Yes.

## D20: Publish a self-contained message and require a broker ack (2026-11-02)

**Decision:** The route saves the job, then publishes a `ReviewJobMessage` (job ID, repository, PR number, head SHA) as a persistent JSON message to the durable `pr_analysis_queue` through the default exchange. Anything but a `Basic.Ack` raises `PublishError`. A failed publish marks the job `failed` and releases the delivery claim.

**Why:** The message carries what the worker needs to fetch the diff, so a missing Redis record doesn't strand it. With confirms on, an unroutable message comes back without an error, so only an explicit ack proves the broker took it. Saving first means the worker never sees a job without a record.

**Rejected:** Sending only the job ID, which ties the worker to Redis, which isn't durable. Sending the whole `JobStatusDetails`, whose status and timestamps go stale. Publishing before saving, which lets a fast worker find no record. Ignoring the publish result, which loses jobs silently. Retrying the publish or an outbox table, which would survive broker outages but is more than this step needs; it's recorded as a known limitation. A named exchange, which only matters once a message must reach several queues.

**Implemented:** Yes.

## D21: Type the job ID in the path as a UUID (2026-11-02)

**Decision:** `GET /api/v1/jobs/{job_id}` declares `job_id: uuid.UUID`, so anything that isn't a UUID gets a 422, and the lookup uses `str(job_id)`.

**Why:** Job IDs are always `uuid4` values, so the type states the rule and FastAPI enforces it. Converting back with `str()` gives the stored lowercase form, so the ID's case doesn't matter.

**Rejected:** A plain `str`, which sends any value to Redis and makes an uppercase ID a 404. Returning 404 for a malformed ID, which hides the difference between "no such job" and "that isn't a job ID".

**Implemented:** Yes.

## D22: One small GitHub client class with a single error type (2026-11-02)

**Decision:** `GitHubClient` wraps an `httpx.AsyncClient`, fetches diffs with the `application/vnd.github.diff` media type, adds the token per request, follows redirects, checks the repository name and PR number before building the URL, and raises `GitHubError` with an optional status code for every failure. Tests replace the network with `httpx.MockTransport`.

**Why:** One class keeps authentication in one place, so a GitHub App can replace the token later. One error type gives the worker one thing to catch, and the status code still lets it tell a 404 from a 500. Keeping response bodies out of error messages stops them from echoing request details into job records and logs.

**Rejected:** A GitHub SDK such as PyGithub or githubkit, which would be more to learn and hide the HTTP that this unit is meant to show. Separate exception classes for each status, which the worker doesn't need yet. Fetching the PR as JSON and then its `diff_url`, which is two requests instead of one. `respx` for mocking, see [libraries.md](libraries.md).

**Implemented:** Yes. Nothing calls it until the worker in step 2.10.

## D23: Ack after processing, with one retry for unexpected errors (2026-11-02)

**Decision:** The worker consumes `pr_analysis_queue` with `prefetch_count=1` and acks each message only after the job's final status is saved. Problems with the job itself (`GitHubError`, `ValueError`) mark it `failed` and are acked. An unreadable message is rejected without requeueing. Any other exception requeues the message once, and if the message was already redelivered, it is dropped and the job marked `failed` on a best-effort basis. A record that is missing or already `completed` or `failed` is skipped, and a `processing` record is run again.

**Why:** Acking after the work means a worker that stops partway through loses nothing, since the broker hands the message on. That makes repeat deliveries normal, so the status check makes them harmless. Job failures are permanent and retrying them wastes calls. A single retry covers brief outages without letting a broken message loop forever, and RabbitMQ's `redelivered` flag tracks the retry without extra state.

**Rejected:** Acking on arrival (`no_ack`), which loses the job if the worker stops. Requeueing every failure, which loops a broken message endlessly. Counting attempts in Redis or in a message header, which allows more retries but needs state that `redelivered` gives for free. A quorum queue's delivery count, which would mean deleting and redeclaring the queue (see D20's notes on queue settings). A dead-letter exchange, which is the better home for dropped messages but isn't needed until there is something to inspect them with. aio-pika's `message.process(requeue=True, reject_on_redelivered=True)`, which does the same in one line but hides the rules and leaves no place to mark the job `failed` before the reject. No prefetch limit, which lets one worker grab every waiting message.

**Implemented:** Yes.

## D24: Run the smee relay as a Compose service under a profile (2026-11-02)

**Decision:** The relay is a `smee` service in `docker-compose.yml`, using `node:22-alpine` and `npx --yes smee-client@5.0.0`, in the `live` profile, reading `SMEE_URL` from `.env` inside the container and posting to `http://api:8000/api/v1/webhooks/github/pr`.

**Why:** The command is written down and repeatable, nothing needs installing on the host, and the profile keeps it out of the normal stack and the tests. Reading the URL inside the container keeps Compose working for anyone without `SMEE_URL`.

**Rejected:** Running `npx smee-client` on the host, which works but leaves the command only in the docs and needs Node on the host. ngrok or Cloudflare Tunnel, which keep the exact body bytes but need an account or a binary. A custom image with smee-client installed, which saves the download on first start but is one more thing to build. Weakening the signature check to accept re-serialised JSON, which would undo the reason for checking the raw bytes.

**Implemented:** Yes. Whether real GitHub payloads survive the relay is checked in step 2.11.

## D25: Reach host Ollama through `host-gateway`, and use `llama3.2` (2026-11-02)

**Decision:** Ollama keeps running on the host. The `api` and `worker` services map `host.docker.internal` to `host-gateway`, Ollama listens on `0.0.0.0:11434` through a systemd drop-in, and the model is `llama3.2` (3B). Connection checks live in `tests/test_ollama.py` under an `ollama` marker.

**Why:** Ollama on the host keeps its models across container rebuilds and is already installed. `host-gateway` is Docker's own way to give a Linux container the host's address, and it matches what Docker Desktop does by default. The 3B model ran a review-sized reply in under a minute on this CPU, where the 7B one would take several. The marker lets CI skip checks that need a local model.

**Rejected:** An `ollama` service in Compose, which would duplicate the models on disk, keep them in a volume, and need GPU passthrough set up if a GPU is ever used. `network_mode: host`, which removes the container's network isolation and breaks the service names (`redis`, `rabbitmq`) the app uses. Binding Ollama to the bridge address `172.17.0.1` only, which keeps it off the local network but breaks `ollama` and `localhost:11434` on the host unless every shell sets `OLLAMA_HOST`. `llama3.2:1b`, which is faster but weaker at following structured-output instructions. `mistral`, which is about 2.5 times slower here. Checking Ollama in `/health`, which would make the API report unhealthy whenever the LLM is down, though webhooks still work.

**Implemented:** Yes. The local-network exposure is a known limitation, not yet fixed.

## D26: One LLM factory with a builder per provider, enforced by a test (2026-11-03)

**Decision:** `src/agent/llm.py` has `get_chat_model(settings=None)`, which looks up `LLM_PROVIDER` (ignoring case and spaces) in a dictionary of builder functions and returns a `BaseChatModel`. An unknown provider raises `LLMConfigError`. Each builder imports its provider package inside the function. The Ollama builder sets `temperature=0` and a timeout from a new `LLM_TIMEOUT_SECONDS` setting (default 300). A test fails if any other file in `src/` imports a provider package.

**Why:** Nodes depend only on LangChain's interface, so a cloud model is one builder and one setting away. Imports inside builders keep each provider's package optional. The timeout matters because the worker holds its message until the job ends, and a hung call would hold it forever. A test is the only way to keep "nodes never import a provider" true over time.

**Rejected:** `langchain.chat_models.init_chat_model("ollama:llama3.2")`, which does the same job in one call but needs the full `langchain` package and hides the mapping this step is meant to show. An `if`/`elif` chain, which works but mixes the lookup with the building. `Literal["ollama"]` on `LLM_PROVIDER` in `Settings`, which would fail at startup but would put the provider list in two places. Caching one model with `lru_cache`, which saves nothing worth having, since building one is cheap. Checking the model exists when it's built (`validate_model_on_init=True`), which would make every build call Ollama.

**Implemented:** Yes. No node uses it until step 3.4.

## D27: A small, flat report schema that is strict on shape and lenient on case (2026-11-03)

**Decision:** `CodeAuditReport` has a required `summary` and `overall_risk_score`, and two lists of the same `Finding` model, which defaults to empty. Levels are `StrEnum`s, normalized to uppercase before validation. `line` is optional and at least 1. Unknown keys are ignored. Every field has a description, because the schema is sent to the model. The graph state is the curriculum's `TypedDict`, with `operator.add` reducers on both finding lists, and it stores findings as JSON-mode dicts.

**Why:** The reply is untrusted, so a wrong shape or an invented level must fail. Lowercase levels and extra keys are harmless, and failing a whole review over them would waste a minute of CPU time each time. A short, flat schema suits a 3B model. Dicts in the state keep it plain JSON for Unit 4's checkpoints.

**Rejected:** `extra="forbid"`, which would reject harmless extra keys. Separate finding models for syntax and security issues, which have no different fields yet. A `confidence` or `cwe` field, which a small model would mostly guess. Severity as a free string, which the report couldn't sort or count reliably. A Pydantic model for the graph state, which LangGraph supports but which the curriculum doesn't ask for, and which validates every update. Storing `Finding` objects in the state, which wouldn't be plain JSON for checkpoints.

**Implemented:** Yes. Nothing uses the schema until step 3.4.

