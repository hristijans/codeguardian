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
