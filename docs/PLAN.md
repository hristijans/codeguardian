# CodeGuardian Project Plan

**Start every task here.** This file is the single source of truth for project status and for what comes next.

- [guidlines.md](guidlines.md) is the curriculum: the overall design and goals.
- [learning/](learning/README.md) is the reference: how and why the built code works.

Status key: ✅ Done · 🔄 In progress · 🔍 Built, not verified · ⬜ Not started · 🚧 Blocked

## Current focus

**Next step: 0.5 Fix engine cleanup in `check_postgres`.** The Unit 1 gaps need closing before Unit 2 starts.

## How to use this plan

1. Read "Current focus" before starting any task.
2. Mark a step 🔄 when starting it, and update the status in the same change as the work.
3. Mark a step ✅ only when its code is done, its checks were actually run and passed, and `docs/learning/` is updated.
4. Add work that isn't listed as a new step before doing it.
5. If the plan and the code disagree, trust the code and correct the plan.
6. Update "Current focus" whenever it changes, and add one line to the session log at the end of each working session.

Later units may be split into smaller steps when they are reached.

## Phase 0: Finish Unit 1

The Unit 1 files (`docker-compose.yml`, `Dockerfile`, `src/config.py`, `src/main.py`, and the test harness) are verified as running. The remaining steps close the gaps that verification exposed.

| ID | Step | Done when | Status |
|---|---|---|---|
| 0.1 | Add `.gitignore` and stop tracking `.idea/` | `.env`, caches, and `.idea/` are ignored, and `.idea/` is removed from the index | ✅ |
| 0.2 | Run the stack and tests | Compose services are healthy, and the pytest result is recorded (3 passed, `/health` 200) | ✅ |
| 0.3 | Make the `/health` test enforce the milestone | Test requires 200 with all services healthy inside Compose | ✅ |
| 0.4 | Check `pytest-asyncio` compatibility | `event_loop` fixture removed or confirmed safe, and the `asyncio_default_fixture_loop_scope` warning is gone | ✅ |
| 0.5 | Fix engine cleanup in `check_postgres` | Cleanup also runs when the check fails, and pytest shows no unclosed connection `ResourceWarning` | ⬜ |
| 0.6 | First commit | History exists on `main` (made outside this plan, and includes `.idea/`) | ✅ |
| 0.7 | Commit the Phase 0 work | Steps 0.1 to 0.5 are committed, after the user's go-ahead | ⬜ |
| 0.8 | Fix empty `gpg.format` in `~/.gitconfig` | `git log` works without an override | ⬜ |
| 0.9 | Remove local dev credentials from code and Compose | `src/config.py` has no password defaults, Compose reads `.env`, and `.env.example` has placeholders | ⬜ |
| 0.10 | Choose a commit email before pushing | Your decision on using GitHub's noreply address is made before the first push | ⬜ |

## Unit 2: GitHub ingestion and queue

| ID | Step | Done when | Status |
|---|---|---|---|
| 2.1 | Add `GITHUB_TOKEN` and `GITHUB_WEBHOOK_SECRET` to config and `.env.example` | Settings load, and secrets stay out of git | ⬜ |
| 2.2 | Payload and job schemas in `src/schemas.py` | Validation tests pass with a sample payload | ⬜ |
| 2.3 | Signature verification | Good signature passes and a bad one is rejected, checked over the raw body | ⬜ |
| 2.4 | Webhook route with event filtering | 202 for accepted actions, no job for others, `ping` handled | ⬜ |
| 2.13 | Repository allowlist from env | `REVIEW_REPOSITORIES` decides which repos are reviewed, others create no job, and nothing is hardcoded | ⬜ |
| 2.5 | Redis job store `src/cache.py` | Records save and read back with a 24-hour expiry | ⬜ |
| 2.6 | Delivery deduplication | A repeated `X-GitHub-Delivery` creates no second job | ⬜ |
| 2.7 | RabbitMQ publisher `src/queue.py` | Persistent message reaches the durable queue | ⬜ |
| 2.8 | `GET /api/v1/jobs/{job_id}` | Returns a job, and 404 when unknown | ⬜ |
| 2.9 | GitHub client `src/github.py` | Diff fetch works against a mocked API | ⬜ |
| 2.10 | Worker `src/worker.py` plus Compose service | Job moves queued → processing → completed or failed, with ack after processing | ⬜ |
| 2.11 | Live test through smee.io | A real PR event creates a job and the diff is fetched | ⬜ |
| 2.12 | Learning notes `unit-2-*.md` | Notes, libraries, and decisions are updated | ⬜ |

## Unit 3: Single-agent LangGraph

| ID | Step | Done when | Status |
|---|---|---|---|
| 3.1 | Ollama access from containers (`extra_hosts`) and chosen model | A test prompt gets a reply from inside a container | ⬜ |
| 3.2 | LLM factory `src/agent/llm.py` | Nodes get a model only through `LLM_PROVIDER` | ⬜ |
| 3.3 | State and report schemas | State has reducers, and `CodeAuditReport` validates | ⬜ |
| 3.4 | `analyze_diff_node` with mocked-LLM tests | SQL injection and clean diffs give the right structured output | ⬜ |
| 3.5 | Diff limits and prompt-injection handling | Oversized and instruction-bearing diffs behave as designed | ⬜ |
| 3.6 | `format_report_node` | Markdown report is produced | ⬜ |
| 3.7 | Graph assembly `src/agent/graph.py` | Graph runs start to end with a mocked LLM | ⬜ |
| 3.8 | Worker integration | A job's report is stored, with failures marked `failed` | ⬜ |
| 3.9 | Real review with Ollama | A real PR diff gets a stored review, with quality noted | ⬜ |
| 3.10 | Learning notes `unit-3-*.md` | Notes, libraries, and decisions are updated | ⬜ |

## Unit 4: Multi-agent and checkpointing

| ID | Step | Done when | Status |
|---|---|---|---|
| 4.1 | Alembic setup for application tables | Migration runs up and down | ⬜ |
| 4.10 | Reviewed repositories table | The allowlist can come from the database, with env as the fallback | ⬜ |
| 4.2 | `AsyncPostgresSaver` with psycopg 3 | Checkpoints appear in Postgres | ⬜ |
| 4.3 | Supervisor router | Routing tests pass for different diffs | ⬜ |
| 4.4 | Security agent | Finds OWASP cases with a mocked LLM | ⬜ |
| 4.5 | Quality and performance agent | Finds sample issues with a mocked LLM | ⬜ |
| 4.6 | Parallel execution with reducers | Both agents' findings survive in state | ⬜ |
| 4.7 | Synthesis and risk score | Conflicts are resolved and a score is set | ⬜ |
| 4.8 | Thread resumption | A thread reloads by `thread_id` | ⬜ |
| 4.9 | Learning notes `unit-4-*.md` | Notes, libraries, and decisions are updated | ⬜ |

## Unit 5: Publishing, approval, and streaming

| ID | Step | Done when | Status |
|---|---|---|---|
| 5.1 | `publish_pr_review_node` | Posts a COMMENT only, with no duplicate on retry | ⬜ |
| 5.2 | Risk routing and `approval_gate_node` | Risky, truncated, or failed reviews pause, and others post | ⬜ |
| 5.3 | `POST /api/v1/jobs/{job_id}/approve` | Approval resumes, and rejection never posts | ⬜ |
| 5.4 | Progress events in Redis Streams | Nodes append events with an expiry | ⬜ |
| 5.5 | SSE endpoint | A late client sees earlier events, then live ones | ⬜ |
| 5.6 | Decide whether WebSockets are still needed | Decision recorded | ⬜ |
| 5.7 | First live posted review | A review appears on a real PR | ⬜ |
| 5.8 | Learning notes `unit-5-*.md` | Notes, libraries, and decisions are updated | ⬜ |

## Unit 6: CI/CD and evaluation

| ID | Step | Done when | Status |
|---|---|---|---|
| 6.1 | ruff and mypy | Clean locally | ⬜ |
| 6.2 | CI pipeline `.github/workflows/ci.yml` | Lint and tests pass with service containers | ⬜ |
| 6.3 | Multi-stage production image | Image builds and starts | ⬜ |
| 6.4 | Tracing and token monitoring | Traces show latency and token use | ⬜ |
| 6.5 | Golden dataset of 50 cases | Includes prompt-injection cases | ⬜ |
| 6.6 | Evaluation harness | Precision, recall, and hallucination rate are reported | ⬜ |
| 6.7 | Learning notes `unit-6-*.md` | Notes, libraries, and decisions are updated | ⬜ |

## Open questions

- Which repository should be the first live test? Needed by 2.11. Set it in `.env` only, and don't name it in this repo.
- Is Ollama installed, and does the machine run `llama3.2` well? Needed by 3.1.
- Should the smee client run on the host or as a dev Compose service? Decide at 2.11.

## Session log

- 2026-11-01: Blueprint corrected, learning docs created, plan added. Next: step 0.1.
- 2026-11-01: Added `.gitignore` and untracked `.idea/` (step 0.1). Next: step 0.2.
- 2026-11-01: Verified the running stack: 3 tests passed and `/health` returned 200 (step 0.2). Found an unclosed Postgres connection, added to step 0.5. Next: step 0.3.
- 2026-11-01: Untracked `CLAUDE.md`, audited the repo and history for personal data, and added the no-hardcoding rule with steps 0.9, 0.10, 2.13, and 4.10. Next: step 0.3.
- 2026-11-01: `/health` test now enforces all services healthy, and a new test covers a failed service: 4 passed, and the strict test fails against a broken Redis (step 0.3). Next: step 0.4.
- 2026-11-01: Removed the unused `event_loop` fixture, added `pytest.ini`, and raised the `pytest-asyncio` minimum to 1.0.0: 4 passed, loop-scope warning gone (step 0.4). Next: step 0.5.
