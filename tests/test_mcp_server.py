"""Read-only MCP adapter behaviour through the official SDK client."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

pytest.importorskip("mcp")

from mcp import Client, StdioServerParameters  # noqa: E402

from rootweft.cli import main  # noqa: E402
from rootweft.mcp_server import (  # noqa: E402
    MAX_RESULTS,
    READ_ONLY_TOOLS,
    create_mcp_server,
)
from rootweft.pipeline import BuildOptions, build_structural_graph  # noqa: E402
from rootweft.scanner import ScanLimits  # noqa: E402
from rootweft.serialization import dump_graph  # noqa: E402

EXPECTED_TOOLS = {
    "graph_info",
    "search_nodes",
    "get_node",
    "get_neighbors",
    "shortest_path",
    "explain_relation",
}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def graph_path(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    write(
        repo / "app.py",
        "def helper():\n    return 1\n\n\ndef run():\n    return helper()\n",
    )
    for index in range(12):
        write(repo / f"many/item_{index:02}.py", f"def item_{index:02}():\n    pass\n")
    document = build_structural_graph(BuildOptions(repo, ScanLimits(), True))
    path = tmp_path / "graph.json"
    dump_graph(document, path)
    return path


def call(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    async def run() -> Any:
        async with Client(server) as client:
            return await client.call_tool(name, arguments)

    return anyio.run(run)


def structured(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content
    payload = result.structured_content
    assert isinstance(payload, dict)
    return payload


def test_server_exposes_only_read_tools(graph_path: Path) -> None:
    server = create_mcp_server(graph_path)

    async def names() -> set[str]:
        async with Client(server) as client:
            listed = await client.list_tools()
            for tool in listed.tools:
                assert tool.annotations is not None
                assert tool.annotations.read_only_hint is True
                assert tool.annotations.destructive_hint is False
                assert tool.annotations.open_world_hint is False
                assert "path" not in json.dumps(tool.input_schema).lower().replace(
                    "shortest_path", ""
                ).replace("path found", "")
            return {tool.name for tool in listed.tools}

    tools = anyio.run(names)
    assert tools == EXPECTED_TOOLS == set(READ_ONLY_TOOLS)
    joined = " ".join(sorted(tools))
    for forbidden in ("build", "write", "provider", "export", "scan", "shell"):
        assert forbidden not in joined


def test_tool_inputs_accept_no_filesystem_paths(graph_path: Path) -> None:
    server = create_mcp_server(graph_path)

    async def schemas() -> dict[str, Any]:
        async with Client(server) as client:
            return {
                tool.name: tool.input_schema
                for tool in (await client.list_tools()).tools
            }

    for name, schema in anyio.run(schemas).items():
        properties = set(schema.get("properties", {}))
        assert not properties & {"path", "file", "graph", "graph_path", "root", "url"}
        for spec in schema.get("properties", {}).values():
            if spec.get("type") == "string" and "enum" not in spec:
                assert spec.get("maxLength", 10**9) <= 512, name
            if spec.get("type") == "integer":
                assert "maximum" in spec, name


def test_every_tool_answers_from_the_loaded_graph(graph_path: Path) -> None:
    server = create_mcp_server(graph_path)
    info = structured(call(server, "graph_info", {}))
    assert info["layer"] == "structural"
    assert info["schema_version"] == "rootweft.graph.v1"
    assert info["node_count"] > 0

    run = structured(call(server, "search_nodes", {"query": "run"}))["nodes"]
    helper = structured(call(server, "search_nodes", {"query": "helper"}))["nodes"]
    run_id = next(node["id"] for node in run if node["kind"] == "function")
    helper_id = next(node["id"] for node in helper if node["kind"] == "function")

    node = structured(call(server, "get_node", {"node_id": run_id}))
    assert node["node"]["name"] == "run"

    neighbors = structured(
        call(server, "get_neighbors", {"node_id": run_id, "direction": "out"})
    )
    calls = [edge for edge in neighbors["edges"] if edge["relation"] == "calls"]
    assert calls and calls[0]["target"] == helper_id

    path = structured(
        call(server, "shortest_path", {"source_id": run_id, "target_id": helper_id})
    )
    assert path["found"] is True

    explained = structured(
        call(server, "explain_relation", {"edge_id": calls[0]["id"]})
    )
    assert explained["source"]["id"] == run_id
    assert explained["target"]["id"] == helper_id


def test_missing_node_is_an_error_not_an_empty_result(graph_path: Path) -> None:
    server = create_mcp_server(graph_path)
    missing = call(server, "get_node", {"node_id": "0" * 64})
    assert missing.is_error
    assert "not found" in missing.content[0].text

    empty = structured(call(server, "search_nodes", {"query": "zz-no-such-name"}))
    assert empty["nodes"] == [] and empty["total"] == 0


def test_results_are_paginated_and_capped(graph_path: Path) -> None:
    server = create_mcp_server(graph_path, max_results=5)
    first = structured(call(server, "search_nodes", {"query": "item", "limit": 5}))
    assert len(first["nodes"]) == 5 and first["total"] > 5
    second = structured(
        call(server, "search_nodes", {"query": "item", "limit": 5, "offset": 5})
    )
    assert {n["id"] for n in first["nodes"]}.isdisjoint(
        {n["id"] for n in second["nodes"]}
    )
    too_many = call(server, "search_nodes", {"query": "item", "limit": 6})
    assert too_many.is_error
    over_schema = call(
        server, "search_nodes", {"query": "item", "limit": MAX_RESULTS + 1}
    )
    assert over_schema.is_error
    oversized = call(server, "search_nodes", {"query": "x" * 600})
    assert oversized.is_error


def test_instructions_treat_repository_text_as_untrusted(graph_path: Path) -> None:
    server = create_mcp_server(graph_path)

    async def instructions() -> str | None:
        async with Client(server) as client:
            return client.instructions

    text = anyio.run(instructions) or ""
    assert "untrusted" in text.lower()
    assert "read-only" in text.lower()


def test_corrupt_graph_fails_before_transport(tmp_path: Path, capsys) -> None:
    broken = tmp_path / "graph.json"
    broken.write_text("{not json", encoding="utf-8")
    assert main(["mcp", str(broken)]) == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "graph" in captured.err


def test_newer_schema_fails_before_transport(tmp_path: Path, capsys) -> None:
    newer = tmp_path / "graph.json"
    newer.write_text('{"schema_version":"rootweft.graph.v99"}', encoding="utf-8")
    assert main(["mcp", str(newer)]) == 4
    assert capsys.readouterr().out == ""


def test_server_module_opens_no_network(graph_path: Path, monkeypatch) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    server = create_mcp_server(graph_path)
    assert structured(call(server, "graph_info", {}))["node_count"] > 0


def test_stdio_protocol_keeps_stdout_clean(graph_path: Path) -> None:
    """Runs the real console module as a subprocess through the SDK stdio client."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "rootweft", "mcp", str(graph_path)],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )

    async def session() -> tuple[set[str], dict[str, Any]]:
        # Any non-protocol byte on stdout would break the SDK's JSON-RPC framing.
        async with Client(params) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            info = await client.call_tool("graph_info", {})
            missing = await client.call_tool("get_node", {"node_id": "0" * 64})
            assert missing.is_error
            return names, info.structured_content or {}

    names, info = anyio.run(session)
    assert names == EXPECTED_TOOLS
    assert info["node_count"] > 0


def test_stdio_corrupt_graph_exits_without_protocol_output(tmp_path: Path) -> None:
    broken = tmp_path / "graph.json"
    broken.write_text("[]", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "rootweft", "mcp", str(broken)],
        input=b"",
        capture_output=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        check=False,
    )
    assert completed.returncode == 4
    assert completed.stdout == b""
    assert b"graph" in completed.stderr


def test_server_does_not_import_decision_providers() -> None:
    code = (
        "import sys, rootweft.mcp_server;"
        "print(any(m.startswith('rootweft.decide') for m in sys.modules))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        check=True,
    )
    assert completed.stdout.strip() == "False"
