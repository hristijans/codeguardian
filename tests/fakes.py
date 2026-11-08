"""Test doubles shared by several test files."""

REPORT = "## CodeGuardian review\n\nA fake report.\n"


class FakeGraph:
    """Stands in for the compiled review graph in the worker's tests.

    `ainvoke` records the state it was given and returns a final state with a
    report, or raises `error` if one was set. `on_call` runs first, so a test
    can look at what's stored while the review is under way.
    """

    def __init__(self, report: str = REPORT, error: Exception | None = None, on_call=None):
        self.report = report
        self.error = error
        self.on_call = on_call
        self.calls: list[dict] = []

    async def ainvoke(self, state: dict) -> dict:
        self.calls.append(state)
        if self.on_call is not None:
            await self.on_call(state)
        if self.error is not None:
            raise self.error
        return state | {"final_summary": self.report}
