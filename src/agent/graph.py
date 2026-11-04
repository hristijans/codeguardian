"""Assembles the review graph.

    [START] -> analyze_diff -> format_report -> [END]

`StateGraph(PRAnalysisState)` is the blueprint: which nodes exist and which
edges join them. `compile()` checks it and returns the runnable graph, which
the worker calls with `await graph.ainvoke(initial_state)`. The result is the
final state, so `result["final_summary"]` is the Markdown report.
"""

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from src.agent.llm import get_chat_model
from src.agent.nodes import make_analyze_diff_node
from src.agent.report import format_report_node
from src.agent.state import PRAnalysisState


def build_graph(model: BaseChatModel | None = None):
    """Build and compile the review graph.

    Tests pass a fake `model`. In the worker it's left out, and the model comes
    from `get_chat_model()`, which reads `LLM_PROVIDER`. It's built when the
    graph is built, not when this module is imported, so importing it needs no
    settings and no running model.
    """
    builder = StateGraph(PRAnalysisState)
    builder.add_node("analyze_diff", make_analyze_diff_node(model or get_chat_model()))
    builder.add_node("format_report", format_report_node)
    builder.add_edge(START, "analyze_diff")
    builder.add_edge("analyze_diff", "format_report")
    builder.add_edge("format_report", END)
    return builder.compile()
