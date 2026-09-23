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
import stat
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path, PureWindowsPath
from typing import Any

from rootweft import __version__
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
from rootweft.skill_installer import (
    SkillInstallError,
    SkillTarget,
    install_skill,
    mcp_config_snippet,
    uninstall_skill,
)
from rootweft.viewer import render_viewer

_SKILL_COMMANDS = {"install-skill", "uninstall-skill"}
_MCP_CLIENTS = ("codex", "claude", "gemini", "hermes", "generic")


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
    parser = argparse.ArgumentParser(
        prog="rootweft",
        description=(
            "Deterministic structural knowledge graphs for Python, JavaScript "
            "and TypeScript repositories. Offline unless --provider and "
            "--enable-remote are both given to build."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"rootweft {__version__}"
    )
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
        item = sub.add_parser(command, help=_HELP[command])
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
    for command in ("install-skill", "uninstall-skill"):
        item = sub.add_parser(command, help=_HELP[command])
        item.add_argument(
            "--target", required=True, choices=[target.value for target in SkillTarget]
        )
        item.add_argument("--scope", choices=("project", "user"), default="project")
        item.add_argument(
            "--dir", dest="skill_dir", help="hermes only: explicit skills directory"
        )
        item.add_argument(
            "--dry-run", action="store_true", help="report the plan; change nothing"
        )
        if command == "install-skill":
            item.add_argument(
                "--no-backup",
                dest="backup",
                action="store_false",
                help="refuse instead of backing up foreign content",
            )
    item = sub.add_parser("mcp-config", help=_HELP["mcp-config"])
    item.add_argument("client", choices=_MCP_CLIENTS)
    item.add_argument("graph", help="graph JSON artifact the server will load")
    return parser


_HELP = {
    "build": "scan a repository and write a graph artifact",
    "search": "search node names",
    "node": "show one node",
    "neighbors": "list edges around a node",
    "path": "shortest directed path between two nodes",
    "explain": "explain one edge",
    "stats": "graph statistics",
    "export-html": "write a standalone offline HTML viewer",
    "provider-check": "check remote provider credentials (network)",
    "mcp": "serve one graph read-only over MCP stdio (extra: rootweft[mcp])",
    "install-skill": "install the Rootweft Agent Skill for a harness",
    "uninstall-skill": "remove files installed by install-skill",
    "mcp-config": "print an MCP client configuration snippet (writes nothing)",
}


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
        config_key = (
            "html_output" if key == "output" and args.command == "export-html" else key
        )
        environment_key = "ROOTWEFT_" + config_key.upper()
        project_sourced = (
            value is None
            and environment_key not in os.environ
            and config_key in project
        )
        if key == "graph":
            value = getattr(args, "graph_option", None) or value
        if value is None:
            value = os.environ.get(environment_key, project.get(config_key, default))
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
        if key == "output" and project_sourced and args.command == "build":
            result[key] = str(_project_output(root, str(value)))
            result["project_output"] = value
    return result


def _project_output(root: Path, configured: str) -> Path:
    """Repository configuration may write only beneath its real .rootweft dir."""
    relative = Path(configured)
    if (
        relative.is_absolute()
        or PureWindowsPath(configured).drive
        or ".." in relative.parts
        or ".." in PureWindowsPath(configured).parts
        or ":" in configured
    ):
        raise ConfigurationError("project output must stay within .rootweft")
    parts = relative.parts
    if parts and parts[0] == ".rootweft":
        parts = parts[1:]
    if not parts:
        raise ConfigurationError("project output must name a file")
    canonical_root = root.resolve(strict=True)
    designated = canonical_root / ".rootweft"
    target = designated.joinpath(*parts)
    current = canonical_root
    for part in (".rootweft", *parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ConfigurationError(
                "project output cannot use links or reparse points"
            )
    if not target.parent.resolve().is_relative_to(designated):
        raise ConfigurationError("project output must stay within .rootweft")
    return target


def _check_export_destination(source: Path, output: Path) -> None:
    if source.resolve() == output.resolve() or (
        source.exists() and output.exists() and source.samefile(output)
    ):
        raise ConfigurationError("HTML output must differ from the source graph")


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


def _provider_class(
    args: argparse.Namespace,
) -> type[TypeSafeProvider]:
    return (
        TypeSafeProvider
        if args.provider == "typesafe"
        else OpenRouterExperimentalProvider
    )


def _provider(args: argparse.Namespace, policy: DecisionPolicy) -> DecisionProvider:
    return _provider_class(args)(policy=policy)


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
        factory = _provider_class(args) if args.provider else None
        _emit(
            {
                "provider": None if factory is None else factory.name,
                "destination": None if factory is None else factory._endpoint,
                **preview_egress(document, policy).to_dict(),
            }
        )
        return 0
    code = 0
    try:
        document = adjudicate(
            document, provider, policy, require_remote=args.require_remote
        )
    except RemoteRequiredError as error:
        document = error.graph
        code = 5
    output = Path(settings["output"])
    if "project_output" in settings:
        output = _project_output(Path(args.root), settings["project_output"])
    dump_graph(document, output)
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


def _skill(args: argparse.Namespace) -> int:
    directory = Path(args.skill_dir) if args.skill_dir else None
    if args.command == "install-skill":
        result = install_skill(
            args.target,
            args.scope,
            directory=directory,
            dry_run=args.dry_run,
            backup=args.backup,
        ).to_dict()
    else:
        result = uninstall_skill(
            args.target, args.scope, directory=directory, dry_run=args.dry_run
        ).to_dict()
    _emit(result)
    return 0


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
            missing = error.name or ""
            if missing != "rootweft.mcp_server" and missing.split(".")[0] not in {
                "mcp",
                "pydantic",
            }:
                raise
            raise ConfigurationError(
                'MCP support requires the optional extra: pip install "rootweft[mcp]"'
            ) from None
        return int(module.main([settings["graph"]]))
    if command == "export-html":
        _check_export_destination(Path(settings["graph"]), Path(settings["output"]))
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
        _check_export_destination(Path(settings["graph"]), Path(settings["output"]))
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
        if args.command in _SKILL_COMMANDS:
            return _skill(args)
        if args.command == "mcp-config":
            sys.stdout.write(mcp_config_snippet(args.client, Path(args.graph)))
            return 0
        return _dispatch(args, _settings(args))
    except (ConfigurationError, ProviderConfigurationError, SkillInstallError) as error:
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
