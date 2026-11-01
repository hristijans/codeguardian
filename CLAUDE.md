# CodeGuardian

The primary goal of this project is learning. The owner comes from PHP/Laravel and is learning Python, FastAPI, and LangGraph.

- `docs/PLAN.md` is the living plan: current status and what comes next.
- `docs/guidlines.md` is the curriculum: the overall design and goals.
- `docs/learning/` is the reference for what has actually been built.

## Start every task here

1. Read `docs/PLAN.md` before starting any task, and work from its "Current focus".
2. Mark a step 🔄 when starting it, and update the status in the same change as the work.
3. Mark a step ✅ only when its code is done, its checks were actually run and passed, and `docs/learning/` is updated.
4. Add work that isn't in the plan as a new step before doing it.
5. If the plan and the code disagree, trust the code and correct the plan.
6. Keep "Current focus" accurate, and add one line to the session log at the end of each working session.

Only `docs/PLAN.md` tracks status. Don't record it in other documents.

## Documentation is part of every change

Any change that adds or alters functionality must update `docs/learning/` in the same piece of work:

- `unit-N-<topic>.md`: explain the flow, walk through important code and methods, and list known limitations.
- `libraries.md`: for each new dependency, say what it does, why it was chosen, what the alternatives were, and its Laravel equivalent if there is one.
- `decisions.md`: record significant choices and the rejected options. Never rewrite an old entry to change history; add a new one.
- `README.md`: keep the contents table current.

Explain why, not only what. Describe the real code, and label anything planned or unverified. Follow the conventions in `docs/learning/README.md`.
