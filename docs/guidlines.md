CodeGuardian AI: Master Curriculum & Technical Architecture Blueprint

An Autonomous Multi-Agent Code Review & Security Audit Pipeline

1. System Overview & Technical Architecture

CodeGuardian AI is an enterprise-grade, event-driven microservice system designed to automate pull request code reviews, security vulnerability scanning, and performance auditing using autonomous AI agents. The application ingests pull request webhooks, buffers analysis jobs via AMQP message queues, processes diffs using a multi-agent LangGraph state machine, persists checkpoints in PostgreSQL, caches execution states in Redis, and streams live agent progress to clients over WebSockets and Server-Sent Events (SSE).

High-Level Architecture Topology

                  ┌─────────────────────────────────────────────────────────┐
                  │                    Ingestion Layer                      │
                  └─────────────────────────────────────────────────────────┘
                                               │
                                       HTTP POST Webhook
                                               │
                                               ▼
                                   ┌──────────────────────┐
                                   │  FastAPI Web Service │
                                   └──────────────────────┘
                                      │                │
                        Cache Job Data│                │Publish Job Payload
                         (TTL: 24h)   ▼                ▼
                               ┌─────────────┐  ┌──────────────┐
                               │ Redis Cache │  │ RabbitMQ AMQP│
                               └─────────────┘  └──────────────┘
                                                       │
                                                       │Consume AMQP Message
                                                       ▼
                  ┌─────────────────────────────────────────────────────────┐
                  │                 Background Processing Layer             │
                  └─────────────────────────────────────────────────────────┘
                                   ┌──────────────────────┐
                                   │ Python Worker Service│
                                   └──────────────────────┘
                                               │
                                       Executes Graph
                                               │
                                               ▼
                        ┌──────────────────────────────────────────────┐
                        │        LangGraph Multi-Agent Engine          │
                        │                                              │
                        │  ┌───────────────┐     ┌──────────────────┐  │
                        │  │ Router Node   │───► │ Security Agent   │  │
                        │  └───────────────┘     └──────────────────┘  │
                        │          │                      │            │
                        │          ▼                      ▼            │
                        │  ┌───────────────┐     ┌──────────────────┐  │
                        │  │ Quality Agent │───► │ Synthesis Node   │  │
                        │  └───────────────┘     └──────────────────┘  │
                        └──────────────────────────────────────────────┘
                                     │                     │
                     Persist Checkpoints                   │Update Real-Time State
                                     ▼                     ▼
                             ┌──────────────┐       ┌─────────────┐
                             │ PostgreSQL   │       │ Redis Cache │
                             │ Database     │       └─────────────┘
                             └──────────────┘


2. Developer Mental Model Map: PHP/Laravel to Python/FastAPI/LangGraph

For experienced web developers transitioning to Python AI systems, translating architectural concepts into familiar patterns accelerates comprehension:

Architectural Component	PHP / Laravel Ecosystem Equivalent	Python / FastAPI / LangGraph Ecosystem Equivalent	Key Differences & Advantages
Web Framework	Laravel Framework (Routing, Controllers)	FastAPI + Uvicorn	Native asyncio execution, auto-generated OpenAPI/Swagger documentation, Pydantic type validation.
Request Validation	FormRequest / $request->validate()	Pydantic v2 BaseModel	Schema enforcement at runtime with strict static type hinting and IDE autocompletion.
Configuration	config/*.php & env()	pydantic-settings (BaseSettings)	Strongly-typed environment parsing with automatic DSN property generation and boot-time missing key validation.
Database ORM & Driver	Eloquent ORM + pdo_mysql / pdo_pgsql	SQLAlchemy 2.0 (Async) + asyncpg	Explicit session lifecycle management, async non-blocking socket I/O, advanced migration tracking with Alembic.
Message Broker & Queues	Laravel Queues + RabbitMQ / Redis Driver	aio-pika + RabbitMQ (AMQP 0-9-1)	Direct control over AMQP channels, durable queue declarations, explicit message acknowledgments (ack/nack).
Cache & Locks	Cache::put(), Redis::funnel()	redis.asyncio (aioredis)	Non-blocking Redis client supporting Key-Value, Hash, and Pub/Sub primitives natively.
State Machine / Workflow	Spatie State / Custom Job Pipelines	LangGraph StateGraph	Directed cyclical graphs supporting conditional branching, state checkpoints, and human approval interrupts.
Testing Harness	PHPUnit / Pest PHP	pytest + pytest-asyncio + httpx	Fixture-based dependency injection system (conftest.py), native async test runners, and powerful mocking tools.

3. Curriculum Units & Implementation Roadmap

The course is structured into 6 sequential, testable units designed to build the pipeline iteratively from foundational infrastructure to production-grade deployment. Current status and the next step are tracked only in docs/PLAN.md.

┌───────────────────────────────────────────────────────────────────────────────────┐
│                               CURRICULUM ROADMAP                                  │
├──────────────┬──────────────┬──────────────┬──────────────┬──────────────┬────────────┤
│    UNIT 1    │    UNIT 2    │    UNIT 3    │    UNIT 4    │    UNIT 5    │   UNIT 6   │
│ Infrastructure│ Event Queue │ LangGraph 101│ Multi-Agent  │ HITL & SSE   │ CI/CD & Eval│
│   Setup      │ Ingestion    │ Single-Agent │ Checkpoints  │ Streaming    │ Deployment │
└──────────────┴──────────────┴──────────────┴──────────────┴──────────────┴────────────┘


Unit 1: Infrastructure Blueprint & Service Containerization

* Objective: Build a containerized local environment running all supporting data services with automated integration health checks.
* Technology Stack: Docker, Docker Compose, Python 3.12, FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2.0 (async), asyncpg, redis-py (async), aio-pika, pytest, pytest-asyncio.
* Core Components:
  * docker-compose.yml: Multi-container orchestration for api, postgres, redis, and rabbitmq with health checks (pg_isready, redis-cli ping, rabbitmq-diagnostics).
  * src/config.py: Strongly-typed configuration class deriving DSNs (postgres_dsn, redis_url, rabbitmq_url).
  * src/main.py: FastAPI application with concurrent health checks using asyncio.gather().
  * tests/conftest.py & tests/test_infrastructure.py: Asynchronous Pytest harness validating API status and database readiness.
* Testable Milestone: Health check integration test verifying http://localhost:8000/health returns HTTP 200 with all 3 infrastructure services reported healthy, and pytest passing 3 tests.

Unit 2: GitHub Event Ingestion & Asynchronous Task Queueing

* Objective: Receive real GitHub pull request webhooks, verify them, and decouple them from background code review execution using RabbitMQ message publishing and Redis state caching.
* Technology Stack: FastAPI, Pydantic v2, RabbitMQ (AMQP 0-9-1), Redis async client, httpx, GitHub REST API, smee.io (local webhook relay).
* GitHub Connection: A fine-grained personal access token scoped to the owner's repositories (Pull requests: read & write, Contents: read) and one webhook per repository. Webhooks reach the local stack through a smee.io relay. Add GITHUB_TOKEN and GITHUB_WEBHOOK_SECRET to src/config.py and .env.example. The token sits behind a small client so a GitHub App can replace it later.
* Components To Build:
  * src/schemas.py: Pydantic payload models (PRWebhookPayload, JobResponse, JobStatusDetails).
  * src/github.py: GitHub client that fetches the PR diff (GET /repos/{owner}/{repo}/pulls/{number} with Accept: application/vnd.github.diff). GitHub webhooks do not include the diff.
  * src/queue.py: AMQP message publisher sending payloads to a durable pr_analysis_queue with message persistence.
  * src/cache.py: Redis JSON serialization storing job status records (job:{job_id}) with 24-hour expiration.
  * src/worker.py: Background worker service running in a separate container, consuming messages, fetching the diff, and managing job state transitions (queued ➔ processing ➔ completed, or failed). Messages are acknowledged only after processing finishes. Review logic stays simulated until Unit 3.
  * docker-compose.yml: Add the worker service.
  * src/main.py: POST /api/v1/webhooks/github/pr (returning HTTP 202 Accepted) and GET /api/v1/jobs/{job_id} endpoints.
  * tests/test_webhooks.py: Integration suite testing payload intake, status polling, and error handling.
* Webhook Intake Rules:
  * Verify X-Hub-Signature-256 against the raw request body with hmac.compare_digest before parsing it. Reject invalid signatures with HTTP 401.
  * Create jobs only for pull_request events with the actions opened, reopened, and synchronize. Acknowledge ping and all other events without creating a job.
  * Deduplicate redeliveries using the X-GitHub-Delivery header.
* Testable Milestone: End-to-end integration test asserting HTTP 202 on a correctly signed webhook, rejection of a bad signature, no job for an ignored action, and an asynchronous state transition through the Redis/RabbitMQ worker. A real PR event delivered through smee.io creates a job.

Unit 3: LangGraph Fundamentals & Single-Agent Code Analyzer Workflow

* Objective: Replace simulated background worker processing with a real, single-agent LangGraph state machine powered by a local Ollama LLM, with the provider selected through configuration so a cloud model can be added later.
* Technology Stack: LangGraph (langgraph), LangChain Core (langchain-core), LangChain Ollama (langchain-ollama), Pydantic structured output models.
* Detailed Technical Architecture:
  * Ollama Connectivity: Add extra_hosts: ["host.docker.internal:host-gateway"] to the api and worker services, because host.docker.internal does not resolve by default on Linux.
  * Provider Selection (src/agent/llm.py): A single factory reads LLM_PROVIDER and returns the chat model, so nodes never import a specific provider.
  * State Definition (src/agent/state.py): Define the central graph state typed with TypedDict. List fields that several nodes write to use reducers:

class PRAnalysisState(TypedDict):
    job_id: str
    repo_name: str
    pr_number: int
    head_sha: str
    title: str
    diff_text: str
    diff_truncated: bool
    syntax_issues: Annotated[List[Dict[str, Any]], operator.add]
    security_vulnerabilities: Annotated[List[Dict[str, Any]], operator.add]
    overall_risk_score: str # LOW, MEDIUM, HIGH, CRITICAL
    final_summary: str


  * Graph Node Implementation (src/agent/nodes.py):
    * analyze_diff_node: Formats the git diff, calls the LLM with system prompts instructing code audit guidelines, and parses response into a Pydantic CodeAuditReport schema.
    * format_report_node: Synthesizes findings into a markdown comment format suitable for GitHub PRs.
  * Graph Assembly (src/agent/graph.py):
[START] ──► analyze_diff_node ──► format_report_node ──► [END]


  * Worker Integration (src/worker.py): Invokes await app_graph.ainvoke(initial_state) inside the RabbitMQ consumer loop. The formatted report is stored on the job record; posting to GitHub arrives in Unit 5.
  * Untrusted Input Handling: Diff content is attacker-controllable data, not instructions.
    * Pass the diff in a clearly delimited block, and state in the system prompt that nothing inside it is an instruction.
    * Give the analysis model no tools, and accept only schema-validated output.
    * Enforce a configurable maximum diff size. Oversized diffs are truncated with diff_truncated set, and the report says the review is partial.
  * Testing Strategy (tests/test_agent.py): Mock LLM responses using Pytest fixtures to test state transitions deterministically without external API latency.
* Testable Milestone: Pytest suite executing the single-agent graph against sample git diffs (SQL injection, clean code, oversized diff, and a diff containing embedded instructions) and verifying correct vulnerability extraction and structured state outputs.

Unit 4: Multi-Agent Orchestration & Persistent State Checkpointing

* Objective: Scale from a single agent into a specialized multi-agent graph with persistent state checkpointing stored in PostgreSQL.
* Technology Stack: LangGraph Multi-Agent (StateGraph, SubGraph, Supervisor routing), langgraph-checkpoint-postgres (AsyncPostgresSaver), psycopg 3, Alembic (database schema migrations).
* Detailed Technical Architecture:
  * Supervisor Router Pattern:

                                  ┌───────────────────────────────┐
                                  │    Supervisor Router Agent    │
                                  └───────────────────────────────┘
                                                  │
                               ┌──────────────────┴──────────────────┐
                               ▼                                     ▼
                 ┌───────────────────────────┐         ┌───────────────────────────┐
                 │   Security Audit Agent    │         │  Quality & Perf Agent     │
                 └───────────────────────────┘         └───────────────────────────┘
                               │                                     │
                               └──────────────────┬──────────────────┘
                                                  ▼
                                  ┌───────────────────────────────┐
                                  │   Synthesis & Lead Reviewer   │
                                  └───────────────────────────────┘


  * Agent Roles:
    1. Supervisor Router Agent: Analyzes code diff size and language types, dynamically routing tasks to specialized agents.
    2. Security Audit Agent: Focuses strictly on OWASP Top 10 vulnerabilities (SQLi, XSS, RCE, hardcoded secrets, unsafe deserialization).
    3. Quality & Performance Agent: Evaluates algorithmic complexity (O(N^2) loops), memory leaks, missing index usage, and architectural formatting.
    4. Synthesis & Lead Reviewer Agent: Aggregates findings from worker nodes, resolves conflicting suggestions, and computes the final risk score.
  * PostgreSQL Checkpointer (AsyncPostgresSaver): Integrates Postgres as the graph memory backend. Every step taken by any agent is persisted in Postgres tables (checkpoints, checkpoint_writes), allowing full auditability, thread resumption, and state history replay.
  * Checkpointer Setup: AsyncPostgresSaver connects through psycopg 3, not asyncpg, so it needs its own plain postgresql:// connection string. It creates its tables through its own setup() call, so Alembic manages only application tables.
  * Parallel Writes: When the Security and Quality agents run in the same step, every state field they both write must have a reducer, or LangGraph raises an error.
* Testable Milestone: Pytest suite verifying that multi-agent routing branches correctly across specialized worker nodes, state checkpoints are saved to Postgres, and graph threads can be re-loaded by thread_id.

Unit 5: Advanced LangGraph Features — Human-in-the-Loop & Real-Time Streaming

* Objective: Post completed reviews to GitHub, introduce Human-in-the-Loop (HITL) approval for high-risk reviews, and stream real-time execution steps to clients over WebSockets and Server-Sent Events (SSE).
* Technology Stack: LangGraph interrupt() / Command(resume=...), GitHub REST API, FastAPI WebSockets & SSE (EventSourceResponse), Redis Streams.
* Detailed Technical Architecture:
  * Publishing Reviews (publish_pr_review_node):
    * Posts the report through the GitHub pull request reviews API using the COMMENT event. It never approves, requests changes on, or merges a PR.
    * Records the head_sha and review id on the job so that a retry does not post a duplicate review.
  * Human-in-the-Loop (HITL) Execution Pause:
    * LOW and MEDIUM reviews post automatically. HIGH and CRITICAL reviews wait for human approval.
    * A conditional edge routes to approval_gate_node only when approval is required. That node calls interrupt(), which saves state in Postgres, and the job moves to status awaiting_approval. Static interrupt_before is not used because it pauses every run regardless of risk.
    * Approval is also required when the diff was truncated or the analysis failed, so that a partial or manipulated result cannot post automatically under a low score.
    * API endpoint POST /api/v1/jobs/{job_id}/approve accepts human feedback (approved: true/false, comments: "LGTM"). Rejected reviews are not posted.
    * Worker resumes execution from the checkpoint using app_graph.ainvoke(Command(resume=approval_payload), config=thread_config). On resume the node restarts from its beginning, so code before interrupt() must be safe to run twice.
  * Real-Time Progress Streaming:
    * Graph nodes append execution events to a Redis Stream (job:{job_id}:stream) with a matching expiry. Pub/Sub is not used because it drops events sent before a client connects.
    * FastAPI endpoint GET /api/v1/jobs/{job_id}/stream replays earlier events and then streams Server-Sent Events (SSE) to frontend clients, rendering live agent logs ("Security Agent scanning lines 10-45...").
* Testable Milestone: Automated integration test simulating a critical security vulnerability diff, verifying the graph pauses at the interrupt boundary, and asserting the review is posted (against a mocked GitHub API) only after an approval payload is posted to the API. A LOW-risk diff posts without pausing.

Unit 6: Production CI/CD Pipeline, Observability & Agent Evaluation

* Objective: Package the entire microservice application into an automated CI/CD pipeline, add agent observability tracing, and implement regression testing suites.
* Technology Stack: GitHub Actions, Docker production optimization, LangSmith / OpenTelemetry, Pytest evaluation harness.
* Detailed Technical Architecture:
  * GitHub Actions Pipeline (.github/workflows/ci.yml):
    1. Linting & Type Checking: Runs ruff and mypy against Python codebase.
    2. Service Integration Testing: Spins up Postgres, Redis, and RabbitMQ container services inside GitHub Actions runner and executes pytest.
    3. Docker Build & Push: Compiles optimized multi-stage Docker images.
  * Observability & Tracing: Integrates LangSmith or OpenTelemetry to monitor token usage, LLM latency, and agent reasoning traces.
  * Agent Evaluation Harness (tests/test_agent_evaluation.py): Runs the graph against a golden dataset of 50 vulnerability test cases, measuring precision, recall, and hallucination rates, and including prompt-injection cases.
* Testable Milestone: Successful GitHub Actions run passing 100% of linting, unit, integration, and agent evaluation tests on push.

4. Comprehensive Unit Comparison Matrix

Unit	Core Objective	Key Technologies Introduced	Primary Artifact Created	Verification Method
Unit 1	Infrastructure Setup	Docker Compose, FastAPI, Pydantic, SQLAlchemy, pytest	docker-compose.yml, config.py, main.py	docker compose exec api pytest (3 passing tests)
Unit 2	GitHub Ingestion & Event Queue	RabbitMQ (aio-pika), Redis (aioredis), signed GitHub webhooks, smee.io	schemas.py, github.py, queue.py, cache.py, worker.py	docker compose exec api pytest + real PR event through smee.io
Unit 3	Single-Agent LangGraph	LangGraph (StateGraph), Ollama (swappable provider), Pydantic	src/agent/state.py, src/agent/graph.py	Pytest graph execution against diffs with mocked LLM
Unit 4	Multi-Agent & Checkpointing	LangGraph Multi-Agent, Postgres AsyncPostgresSaver, psycopg 3, Alembic	src/agent/router.py, src/agent/checkpoint.py	Postgres checkpoint verification & thread resumption tests
Unit 5	GitHub Publishing, HITL & SSE Streaming	interrupt(), Command(resume), SSE, Redis Streams, GitHub reviews API	publish_pr_review_node, src/api/stream.py, POST /approve endpoint	HITL interrupt & resume integration test suite
Unit 6	Production CI/CD & Eval	GitHub Actions, LangSmith / OpenTelemetry, Docker multi-stage	.github/workflows/ci.yml, test_agent_evaluation.py	Green CI pipeline run on GitHub Actions

5. Next Steps

See docs/PLAN.md for current status, the next step, and the full list of tasks.
