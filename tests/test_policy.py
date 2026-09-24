from spark.models import ToolCall
from spark.policy import decide


def _call(name: str, **args) -> ToolCall:
    return ToolCall(id="1", name=name, arguments=args)


def test_suggest_prompts_write_and_shell() -> None:
    assert (
        decide("suggest", _call("read_file"), allow_always=set(), readonly_mcp=set())
        == "allow"
    )
    assert (
        decide("suggest", _call("write_file"), allow_always=set(), readonly_mcp=set())
        == "prompt"
    )
    assert (
        decide("suggest", _call("run_shell"), allow_always=set(), readonly_mcp=set())
        == "prompt"
    )


def test_auto_edit_prompts_only_shell() -> None:
    assert (
        decide("auto-edit", _call("write_file"), allow_always=set(), readonly_mcp=set())
        == "allow"
    )
    assert (
        decide("auto-edit", _call("run_shell"), allow_always=set(), readonly_mcp=set())
        == "prompt"
    )


def test_full_auto_allows_builtin() -> None:
    assert (
        decide("full-auto", _call("write_file"), allow_always=set(), readonly_mcp=set())
        == "allow"
    )
    assert (
        decide("full-auto", _call("run_shell"), allow_always=set(), readonly_mcp=set())
        == "allow"
    )


def test_allow_always_and_mcp() -> None:
    from spark.policy import call_signature

    allowed = {call_signature(_call("run_shell", command="echo hi"))}
    assert (
        decide(
            "suggest",
            _call("run_shell", command="echo hi"),
            allow_always=allowed,
            readonly_mcp=set(),
        )
        == "allow"
    )
    # A different command must still prompt, even for the same tool.
    assert (
        decide(
            "suggest",
            _call("run_shell", command="rm -rf x"),
            allow_always=allowed,
            readonly_mcp=set(),
        )
        == "prompt"
    )
    assert (
        decide(
            "auto-edit", _call("mcp__fs__write"), allow_always=set(), readonly_mcp=set()
        )
        == "prompt"
    )
    assert (
        decide(
            "suggest",
            _call("mcp__fs__read"),
            allow_always=set(),
            readonly_mcp={"mcp__fs__read"},
        )
        == "allow"
    )
