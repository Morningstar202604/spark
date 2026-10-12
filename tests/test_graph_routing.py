"""LangGraph routing boundary tests.

Covers state machine routing edge cases for the ReAct loop:

* Max rounds enforcement (boundary at exactly N rounds)
* Empty tool_results routing to END
* Error state propagation
* State dict serialization
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

# Force classic path to avoid LangGraph dependency in import
os.environ["SPARK_USE_LANGGRAPH"] = "0"


def test_route_tools_not_in_graph_module():
    """Import graph module and verify _route_tools function exists."""
    from spark.core import graph

    assert hasattr(graph, "_route_tools")
    assert hasattr(graph, "build_react_graph")
    assert hasattr(graph, "ReActState")


def test_route_tools_below_max_rounds_with_tool_results_continues():
    """Below max rounds AND with tool results → should continue (return 'llm')."""
    from spark.core.graph import _route_tools

    state = {
        "rounds": 5,
        "__max_rounds": 30,
        "tool_results": [MagicMock()],
    }
    assert _route_tools(state) == "llm"


def test_route_tools_at_max_rounds_ends():
    """At threshold → should stop (return END)."""
    from langgraph.graph import END

    from spark.core.graph import _route_tools

    state = {"rounds": 30, "__max_rounds": 30, "tool_results": [MagicMock()]}
    assert _route_tools(state) == END


def test_route_tools_over_max_rounds_ends():
    """Over threshold → should stop."""
    from langgraph.graph import END

    from spark.core.graph import _route_tools

    state = {"rounds": 31, "__max_rounds": 30, "tool_results": [MagicMock()]}
    assert _route_tools(state) == END


def test_route_tools_empty_tool_results_goes_to_end():
    """If no tool_results, route to END regardless of round count."""
    from langgraph.graph import END

    from spark.core.graph import _route_tools

    state = {"rounds": 0, "__max_rounds": 30, "tool_results": []}
    assert _route_tools(state) == END


def test_route_tools_no_results_field_goes_to_end():
    """No tool_results key → END."""
    from langgraph.graph import END

    from spark.core.graph import _route_tools

    state = {"rounds": 0, "__max_rounds": 30}
    assert _route_tools(state) == END


def test_route_tools_missing_max_rounds_defaults():
    """If __max_rounds not in state, should default to 30."""
    from spark.core.graph import _route_tools

    state = {"rounds": 29, "tool_results": [MagicMock()]}
    assert _route_tools(state) == "llm"


def test_route_tools_error_state_ends():
    """Error state → should end even with tool_results."""
    from langgraph.graph import END

    from spark.core.graph import _route_tools

    state = {
        "rounds": 0,
        "__max_rounds": 30,
        "_error": "Network failure",
        "tool_results": [MagicMock()],
    }
    assert _route_tools(state) == END


def test_route_tools_zero_max_rounds_ends_immediately():
    """With max_rounds=0, should immediately END."""
    from langgraph.graph import END

    from spark.core.graph import _route_tools

    state = {"rounds": 0, "__max_rounds": 0, "tool_results": [MagicMock()]}
    assert _route_tools(state) == END


def test_graph_factory_creates_graph():
    """build_react_graph should return a compiled graph."""
    from spark.core.graph import build_react_graph

    provider = MagicMock()
    provider.stream = MagicMock()
    registry = MagicMock()
    registry.schemas = MagicMock(return_value=[])
    workdir = Path("/tmp")
    cfg = MagicMock()

    g = build_react_graph(
        provider=provider,
        registry=registry,
        workdir=workdir,
        cfg=cfg,
    )
    assert hasattr(g, "nodes")


def test_react_state_is_dict():
    """ReActState should be a dict subclass."""
    from spark.core.graph import ReActState

    state = ReActState()
    state["messages"] = []
    state["rounds"] = 0
    assert state["rounds"] == 0
