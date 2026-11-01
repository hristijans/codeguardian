# CodeGuardian Learning Reference

This folder is the long-term reference for everything built in CodeGuardian: what exists, how it works, and why it was chosen. The main goal of the project is learning, so these notes are written to be reread later.

`docs/guidlines.md` is the plan for what will be built. This folder describes what has actually been built.

## Contents

| Document | Read it when you want to know |
|---|---|
| [decisions.md](decisions.md) | Why the project went one way and not another |
| [libraries.md](libraries.md) | Why a library is used and what it does |
| [unit-1-infrastructure.md](unit-1-infrastructure.md) | How the Docker setup, config, health checks, and tests work |

## Progress

| Unit | Status | Notes |
|---|---|---|
| 1. Infrastructure | Built | [unit-1-infrastructure.md](unit-1-infrastructure.md) |
| 2. GitHub ingestion and queue | Not started | |
| 3. Single-agent LangGraph | Not started | |
| 4. Multi-agent and checkpointing | Not started | |
| 5. Publishing, approval, and streaming | Not started | |
| 6. CI/CD and evaluation | Not started | |

## How these notes are maintained

Every change that adds or alters functionality updates these notes in the same piece of work.

- **Unit notes:** one `unit-N-<topic>.md` per unit. Each explains the flow, walks through the important code, and lists known limitations.
- **Libraries:** every new dependency gets an entry saying what it does, why it was chosen, and what the alternatives were.
- **Decisions:** every significant choice gets an entry, including the options that were rejected.
- **Laravel comparisons:** included wherever a Laravel concept makes the idea easier to understand.
- **Accuracy:** notes describe the real code. Anything that is planned or has not been verified is labelled that way.
