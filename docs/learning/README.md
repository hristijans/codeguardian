# CodeGuardian Learning Reference

This folder is the long-term reference for everything built in CodeGuardian: what exists, how it works, and why it was chosen. The main goal of the project is learning, so these notes are written to be reread later.

[`docs/guidlines.md`](../guidlines.md) is the curriculum. This folder describes what has actually been built. For current status and the next step, start with [`docs/PLAN.md`](../PLAN.md).

## Contents

| Document | Read it when you want to know |
|---|---|
| [decisions.md](decisions.md) | Why the project went one way and not another |
| [libraries.md](libraries.md) | Why a library is used and what it does |
| [unit-1-infrastructure.md](unit-1-infrastructure.md) | How the Docker setup, config, health checks, and tests work |
| [unit-2-github-ingestion.md](unit-2-github-ingestion.md) | How GitHub events reach the app and become jobs |
| [unit-3-single-agent.md](unit-3-single-agent.md) | How the containers reach the local LLM, how the chat model is chosen, what the review report and graph state look like, how the analysis node and its prompt work, how untrusted diffs and size limits are handled, how the Markdown report is built and cleaned, how the graph is assembled, how the worker runs it and stores the report, and later the full review agent |

## How these notes are maintained

Every change that adds or alters functionality updates these notes in the same piece of work.

- **Unit notes:** one `unit-N-<topic>.md` per unit. Each explains the flow, walks through the important code, and lists known limitations.
- **Libraries:** every new dependency gets an entry saying what it does, why it was chosen, and what the alternatives were.
- **Decisions:** every significant choice gets an entry, including the options that were rejected.
- **Laravel comparisons:** included wherever a Laravel concept makes the idea easier to understand.
- **Accuracy:** notes describe the real code. Anything that is planned or has not been verified is labelled that way.
