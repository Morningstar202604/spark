import pytest
from pathlib import Path
from spark.config import SparkConfig
from spark.models import ToolCall
from spark.sandbox import WorkdirSandbox
from spark.tools.registry import ToolContext, ToolRegistry

def make_registry(tmp_path):
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    return ToolRegistry(ctx)

def _mcp_schema(name, description="test tool"):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": {}},
        },
    }

def test_register_scope_adds_schemas(tmp_path):
    reg = make_registry(tmp_path)
    schemas = [_mcp_schema("mcp__srv1__toolA"), _mcp_schema("mcp__srv1__toolB")]
    reg.register_scope("srv1", schemas)
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "mcp__srv1__toolA" in names
    assert "mcp__srv1__toolB" in names

def test_register_scope_replaces_existing(tmp_path):
    reg = make_registry(tmp_path)
    reg.register_scope("srv1", [_mcp_schema("mcp__srv1__old")])
    reg.register_scope("srv1", [_mcp_schema("mcp__srv1__new")])
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "mcp__srv1__old" not in names
    assert "mcp__srv1__new" in names

def test_unregister_scope_removes_schemas(tmp_path):
    reg = make_registry(tmp_path)
    reg.register_scope("srv1", [_mcp_schema("mcp__srv1__toolA")])
    reg.register_scope("srv2", [_mcp_schema("mcp__srv2__toolX")])
    reg.unregister_scope("srv1")
    names = {s["function"]["name"] for s in reg.schemas()}
    assert "mcp__srv1__toolA" not in names
    assert "mcp__srv2__toolX" in names

def test_unregister_unknown_scope_no_error(tmp_path):
    reg = make_registry(tmp_path)
    reg.register_scope("srv1", [_mcp_schema("mcp__srv1__toolA")])
    reg.unregister_scope("nonexistent")  # not raise

def test_stale_tools_detects_removed(tmp_path):
    reg = make_registry(tmp_path)
    reg.register_scope("srv1", [_mcp_schema("mcp__srv1__toolA")])
    stale = reg.stale_tools(["mcp__srv1__toolA", "mcp__srv1__toolB"])
    assert "mcp__srv1__toolB" in stale
    assert "mcp__srv1__toolA" not in stale

def test_execute_stale_mcp_returns_error(tmp_path):
    import asyncio
    reg = make_registry(tmp_path)
    # Don't register any MCP tools — they're all stale
    result = asyncio.run(reg.execute(ToolCall(id="x", name="mcp__ghost__tool", arguments={})))
    assert not result.ok
    assert "Stale" in result.payload["error"]

def test_revision_bumps_on_scope_change(tmp_path):
    reg = make_registry(tmp_path)
    r0 = reg.schema_revision
    reg.register_scope("srv1", [_mcp_schema("mcp__srv1__toolA")])
    assert reg.schema_revision > r0
