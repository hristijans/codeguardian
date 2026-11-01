# CodeGuardian

The primary goal of this project is learning. The owner comes from PHP/Laravel and is learning Python, FastAPI, and LangGraph.

- `docs/guidlines.md` is the plan.
- `docs/learning/` is the reference for what has actually been built.

## Documentation is part of every change

Any change that adds or alters functionality must update `docs/learning/` in the same piece of work:

- `unit-N-<topic>.md`: explain the flow, walk through important code and methods, and list known limitations.
- `libraries.md`: for each new dependency, say what it does, why it was chosen, what the alternatives were, and its Laravel equivalent if there is one.
- `decisions.md`: record significant choices and the rejected options. Never rewrite an old entry to change history; add a new one.
- `README.md`: keep the contents and progress tables current.

Explain why, not only what. Describe the real code, and label anything planned or unverified. Follow the conventions in `docs/learning/README.md`.
