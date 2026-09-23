"""Rootweft commands with explicit remote capabilities and stable JSON output.

Query grammar is COMMAND GRAPH [QUERY/ID]. Non-secret defaults may be set in
the project .rootweft.toml (top-level or [rootweft]) and ROOTWEFT_* variables.
Provider selection and remote permission are exclusively command-line flags.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rootweft.decide import (
    DecisionPolicy,
    DecisionProvider,
    NoulQuestion,
    OpenRouterExperimentalProvider,
    ProviderConfigurationError,
    ProviderError,
    RemoteRequiredError,
    TypeSafeProvider,
    adjudicate,
    preview_egress,
)
from rootweft.errors import CorruptGraphError, IncompatibleSchemaError
from rootweft.pipeline import BuildOptions, GraphLimits, build_structural_graph
from rootweft.query import GraphIndex
from rootweft.scanner import ScanLimits
from rootweft.serialization import canonical_json, dump_graph, load_graph
from rootweft.viewer import render_viewer


class ConfigurationError(ValueError):
    """Invalid local options; messages contain no raw user input."""


class BuildError(ValueError):
    """A scan/build failed before committing output."""


_DEFAULTS: dict[str, Any] = {
    "graph": "graph.json",
    "output": "graph.json",
    "include_markdown": True,
    "max_files": 10_000,
    "max_file_bytes": 2_000_000,
    "max_total_bytes": 100_000_000,
    "max_nodes": 100_000,
    "max_edges": 200_000,
    "max_candidates": 20_000,
    "max_diagnostics": 1_000,
    "max_candidate_options": 16,
    "limit": 20,
    "offset": 0,
    "max_depth": 8,
    "max_visible": 500,
    "max_questions": 32,
    "max_payload_bytes": 64_000,
    "max_response_bytes": 256_000,
    "max_cost_usd": 0.10,
    "max_retries": 2,
    "timeout_seconds": 15.0,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rootweft")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in (
        "build",
        "search",
        "node",
        "neighbors",
        "path",
        "explain",
        "stats",
        "export-html",
        "provider-check",
        "mcp",
    ):
        item = sub.add_parser(command)
        item.add_argument("--json", action="store_true", help="emit canonical JSON")
        if command == "build":
            item.add_argument("root", nargs="?", default=".")
            item.add_argument("--output")
            item.add_argument(
                "--include-markdown",
                action=argparse.BooleanOptionalAction,
                default=None,
            )
            for key in (
                "max_files",
                "max_file_bytes",
                "max_total_bytes",
                "max_nodes",
                "max_edges",
                "max_candidates",
                "max_diagnostics",
                "max_candidate_options",
            ):
                item.add_argument("--" + key.replace("_", "-"), type=int)
            item.add_argument("--dry-run-egress", action="store_true")
            item.add_argument("--require-remote", action="store_true")
        elif command != "provider-check":
            item.add_argument("graph", nargs="?", help="graph JSON artifact")
            item.add_argument("--graph", dest="graph_option", help=argparse.SUPPRESS)
        if command in {"build", "provider-check"}:
            item.add_argument(
                "--provider", choices=("typesafe", "openrouter-experimental")
            )
            item.add_argument("--enable-remote", action="store_true")
            for key in (
                "max_questions",
                "max_payload_bytes",
                "max_response_bytes",
                "max_retries",
            ):
                item.add_argument("--" + key.replace("_", "-"), type=int)
            for key in ("max_cost_usd", "timeout_seconds"):
                item.add_argument("--" + key.replace("_", "-"), type=float)
        if command == "search":
            item.add_argument("query")
            item.add_argument("--kind")
        if command in {"node", "neighbors"}:
            item.add_argument("node_id")
        if command == "explain":
            item.add_argument("edge_id")
        if command == "path":
            item.add_argument("source")
            item.add_argument("target")
            item.add_argument("--max-depth", type=int)
        if command in {"path", "neighbors"}:
            item.add_argument("--relation")
        if command in {"search", "neighbors"}:
            item.add_argument("--limit", type=int)
            item.add_argument("--offset", type=int)
        if command == "neighbors":
            item.add_argument(
                "--direction", choices=("in", "out", "both"), default="both"
            )
        if command == "export-html":
            item.add_argument("--output")
            item.add_argument("--max-visible", type=int)
    return parser


def _settings(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root) if args.command == "build" else Path.cwd()
    config = root / ".rootweft.toml"
    project: dict[str, Any] = {}
    try:
        if config.exists():
            raw = tomllib.loads(config.read_text(encoding="utf-8"))
            project = raw.get("rootweft", raw)
            if not isinstance(project, dict):
                raise ConfigurationError("invalid project configuration")
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        raise ConfigurationError("cannot read project configuration") from None
    defaults = dict(_DEFAULTS)
    if args.command == "export-html":
        defaults["output"] = "graph.html"
    result = {}
    for key, default in defaults.items():
        value = getattr(args, key, None)
        if key == "graph":
            value = getattr(args, "graph_option", None) or value
        if value is None:
            value = os.environ.get("ROOTWEFT_" + key.upper(), project.get(key, default))
        try:
            if isinstance(default, bool):
                if isinstance(value, str) and value.lower() in {
                    "true",
                    "false",
                    "1",
                    "0",
                }:
                    value = value.lower() in {"true", "1"}
                if type(value) is not bool:
                    raise ValueError
            elif isinstance(default, int):
                if isinstance(value, str):
                    value = int(value)
                if type(value) is not int or value < (
                    0 if key in {"offset", "max_retries", "max_depth"} else 1
                ):
                    raise ValueError
            elif isinstance(default, float):
                if isinstance(value, bool):
                    raise ValueError
                value = float(value)
            elif not isinstance(value, str) or not value:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise ConfigurationError(f"invalid {key} setting") from None
        result[key] = value
    return result


def _policy(settings: dict[str, Any]) -> DecisionPolicy:
    return DecisionPolicy(
        **{
            key: settings[key]
            for key in (
                "max_questions",
                "max_payload_bytes",
                "max_response_bytes",
                "max_cost_usd",
                "max_retries",
                "timeout_seconds",
            )
        }
    )


def _provider(args: argparse.Namespace, policy: DecisionPolicy) -> DecisionProvider:
    factory = (
        TypeSafeProvider
        if args.provider == "typesafe"
        else OpenRouterExperimentalProvider
    )
    return factory(policy=policy)


def _emit(value: Any) -> None:
    # ASCII escapes keep arbitrary labels safe on terminals, independent of locale.
    print(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )


def _build(args: argparse.Namespace, settings: dict[str, Any]) -> int:
    policy = _policy(settings)
    provider = None
    if args.provider and not args.dry_run_egress:
        provider = _provider(args, policy)
    scan = ScanLimits(
        **{
            key: settings[key]
            for key in ("max_files", "max_file_bytes", "max_total_bytes")
        }
    )
    budget = GraphLimits(
        **{
            key: settings[key]
            for key in (
                "max_nodes",
                "max_edges",
                "max_candidates",
                "max_diagnostics",
                "max_candidate_options",
            )
        }
    )
    try:
        document = build_structural_graph(
            BuildOptions(Path(args.root), scan, settings["include_markdown"], budget)
        )
    except ValueError:
        raise BuildError("repository scan or build failed") from None
    if args.dry_run_egress:
        _emit(preview_egress(document, policy).to_dict())
        return 0
    code = 0
    try:
        document = adjudicate(
            document, provider, policy, require_remote=args.require_remote
        )
    except RemoteRequiredError as error:
        document = error.graph
        code = 5
    dump_graph(document, Path(settings["output"]))
    if args.json:
        # Preserve the canonical artifact bytes, including Unicode names.
        payload = canonical_json(document).decode("utf-8")
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(payload.encode("utf-8") + b"\n")
            sys.stdout.buffer.flush()
        else:
            print(payload)
    else:
        _emit(
            {
                "output": settings["output"],
                **GraphIndex.from_document(document).graph_stats(),
            }
        )
    if document.adjudication.diagnostics:
        print(
            "rootweft: remote adjudication unavailable; structural graph saved",
            file=sys.stderr,
        )
    return code


def _dispatch(args: argparse.Namespace, settings: dict[str, Any]) -> int:
    command = args.command
    if command in {"build", "provider-check"}:
        if bool(args.provider) != args.enable_remote:
            raise ConfigurationError(
                "remote calls require --provider and --enable-remote"
            )
        if (
            command == "provider-check" or getattr(args, "require_remote", False)
        ) and not args.provider:
            raise ConfigurationError(
                "remote calls require --provider and --enable-remote"
            )
    if command == "build":
        return _build(args, settings)
    if command == "provider-check":
        provider = _provider(args, _policy(settings))
        provider.decide(
            "Local provider connectivity check; no repository content.",
            (NoulQuestion("check", "Is this a connectivity check?"),),
        )
        _emit({"ok": True, "provider": provider.name, "model": provider.model})
        return 0
    if command == "mcp":
        try:
            module = importlib.import_module("rootweft.mcp_server")
        except ModuleNotFoundError as error:
            if error.name != "rootweft.mcp_server":
                raise
            raise ConfigurationError(
                "MCP support is not installed in this version"
            ) from None
        return int(module.main([settings["graph"]]))
    document = load_graph(Path(settings["graph"]))
    try:
        index = GraphIndex.from_document(document)
    except ValueError:
        raise CorruptGraphError("invalid graph relationships") from None
    result: dict[str, Any]
    if command == "search":
        result = index.search_nodes(
            args.query,
            limit=settings["limit"],
            offset=settings["offset"],
            kind=args.kind,
        )
    elif command == "node":
        result = index.get_node(args.node_id)
    elif command == "neighbors":
        result = index.neighbors(
            args.node_id,
            relation=args.relation,
            direction=args.direction,
            limit=settings["limit"],
            offset=settings["offset"],
        )
    elif command == "path":
        result = index.shortest_path(
            args.source,
            args.target,
            max_depth=settings["max_depth"],
            relation=args.relation,
        )
    elif command == "explain":
        result = index.explain_edge(args.edge_id)
    elif command == "export-html":
        render_viewer(document, Path(settings["output"]), settings["max_visible"])
        result = {"output": settings["output"]}
    else:
        result = index.graph_stats()
    _emit(result)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run a command without leaking SystemExit or provider error bodies."""
    try:
        try:
            args = _parser().parse_args(argv)
        except SystemExit as error:
            return int(error.code or 0)
        return _dispatch(args, _settings(args))
    except (ConfigurationError, ProviderConfigurationError) as error:
        print(f"rootweft: {error}", file=sys.stderr)
        return 2
    except (CorruptGraphError, IncompatibleSchemaError):
        print("rootweft: cannot read supported graph artifact", file=sys.stderr)
        return 4
    except (BuildError, OSError):
        print("rootweft: repository build or output operation failed", file=sys.stderr)
        return 3
    except ProviderError:
        print("rootweft: remote operation unavailable", file=sys.stderr)
        return 5
    except ValueError:
        print("rootweft: invalid query or option", file=sys.stderr)
        return 2
    except Exception:
        print("rootweft: unexpected internal error", file=sys.stderr)
        return 70
