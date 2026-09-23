"""Public command behavior, including errors that must not destroy artifacts."""

import json
import socket
from pathlib import Path

import httpx
import pytest

from rootweft.serialization import load_graph


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in tuple(__import__("os").environ):
        if name.startswith("ROOTWEFT_"):
            monkeypatch.delenv(name)
    source = tmp_path / "repo"
    source.mkdir()
    (source / "app.py").write_text(
        "def target():\n    pass\n\ndef caller():\n    target()\n",
        encoding="utf-8",
    )
    return source


def run(*args):
    from rootweft.cli import main

    return main([str(arg) for arg in args])


def test_build_machine_stdout_is_deterministic_json(repo, capsys):
    output = repo.parent / "graph.json"
    assert run("build", repo, "--output", output, "--json") == 0
    first = capsys.readouterr()
    graph = json.loads(first.out)
    assert graph["schema_version"] == "rootweft.graph.v1"
    assert len(graph["structural"]["nodes"]) == 4
    assert first.err == ""
    assert output.read_bytes() == first.out.rstrip().encode()
    assert run("build", repo, "--output", output, "--json") == 0
    assert capsys.readouterr().out == first.out


def test_query_commands_return_real_graph_results(repo, capsys):
    assert run("build", repo, "--json") == 0
    graph = json.loads(capsys.readouterr().out)
    nodes = {n["name"]: n["id"] for n in graph["structural"]["nodes"]}
    edge = next(e for e in graph["structural"]["edges"] if e["relation"] == "calls")
    cases = [
        (("search", "target"), "total", 1),
        (("node", nodes["target"]), "layer", "structural"),
        (("neighbors", nodes["caller"], "--relation", "calls"), "total", 1),
        (("path", nodes["caller"], nodes["target"]), "found", True),
        (("explain", edge["id"]), "layer", "structural"),
        (("stats",), "node_count", 4),
    ]
    for args, key, value in cases:
        assert run(args[0], "graph.json", *args[1:], "--json") == 0
        result = capsys.readouterr()
        assert json.loads(result.out)[key] == value
        assert result.err == ""


@pytest.mark.parametrize(
    "args", [[], ["unknown"], ["search"], ["stats", "--limit", "bad"]]
)
def test_usage_returns_two_without_system_exit(args, capsys):
    assert run(*args) == 2
    result = capsys.readouterr()
    assert result.out == ""
    assert result.err


def test_configuration_precedence(repo, monkeypatch, capsys):
    (repo / ".rootweft.toml").write_text('[rootweft]\noutput="project.json"\n')
    assert run("build", repo, "--json") == 0
    assert Path("project.json").exists()
    monkeypatch.setenv("ROOTWEFT_OUTPUT", "environment.json")
    assert run("build", repo, "--json") == 0
    assert Path("environment.json").exists()
    assert run("build", repo, "--output", "flag.json", "--json") == 0
    assert Path("flag.json").exists()
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "config", ['max_files="nope"', "include_markdown=7", "output=42", "["]
)
def test_bad_project_configuration_is_usage_error(repo, config, capsys):
    (repo / ".rootweft.toml").write_text(config)
    assert run("build", repo, "--json") == 2
    assert not Path("graph.json").exists()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("flags", [[], ["--provider", "typesafe"], ["--enable-remote"]])
def test_remote_needs_both_explicit_flags(repo, monkeypatch, capsys, flags):
    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setenv("ROOTWEFT_PROVIDER", "typesafe")
    monkeypatch.setenv("ROOTWEFT_ENABLE_REMOTE", "true")
    (repo / ".rootweft.toml").write_text('provider="typesafe"\nenable_remote=true\n')
    assert run("build", repo, *flags, "--json") == (2 if flags else 0)
    result = capsys.readouterr()
    if not flags:
        assert json.loads(result.out)["adjudication"]["decisions"] == []


def test_dry_run_egress_never_constructs_provider_or_opens_sockets(
    repo, monkeypatch, capsys
):
    def forbidden(*args, **kwargs):
        pytest.fail("dry run constructed remote capability")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr("rootweft.decide.TypeSafeProvider.__init__", forbidden)
    assert (
        run(
            "build",
            repo,
            "--provider",
            "typesafe",
            "--enable-remote",
            "--dry-run-egress",
            "--json",
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert set(result) == {
        "candidate_count",
        "question_count",
        "paths",
        "estimated_bytes",
    }
    assert not Path("graph.json").exists()


@pytest.mark.parametrize("required,code", [(False, 0), (True, 5)])
def test_failed_remote_keeps_valid_structural_output(
    repo, monkeypatch, capsys, required, code
):
    # The network boundary fails; the real provider and adjudication service run.
    (repo / "README.md").write_text("target\n")
    (repo / "other.py").write_text("def target():\n    pass\n")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")

    def fail(*args, **kwargs):
        raise httpx.ConnectError("private remote body")

    monkeypatch.setattr(httpx.Client, "send", fail)
    flags = ["--require-remote"] if required else []
    assert (
        run(
            "build",
            repo,
            "--provider",
            "typesafe",
            "--enable-remote",
            "--max-retries",
            "0",
            *flags,
            "--json",
        )
        == code
    )
    result = capsys.readouterr()
    graph = load_graph(Path("graph.json"))
    assert len(graph.structural.nodes) == 8
    assert graph.adjudication.diagnostics[-1].code == "remote_failure"
    assert json.loads(result.out)["structural"] == graph.structural.to_dict()
    assert "private remote body" not in result.out + result.err


def test_missing_provider_credentials_is_configuration_error(repo, monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert run("build", repo, "--provider", "typesafe", "--enable-remote") == 2
    assert not Path("graph.json").exists()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("content", ["not json", '{"schema_version":"future"}'])
def test_corrupt_or_future_graph_is_exit_four(repo, content, capsys):
    Path("graph.json").write_text(content)
    assert run("stats", "--json") == 4
    assert capsys.readouterr().out == ""


def test_build_and_output_failures_preserve_existing_graph(repo, monkeypatch, capsys):
    Path("graph.json").write_bytes(b"old graph")
    assert run("build", repo / "missing", "--json") == 3
    assert Path("graph.json").read_bytes() == b"old graph"

    def fail(*args):
        raise OSError("replacement denied")

    monkeypatch.setattr("rootweft.serialization.os.replace", fail)
    assert run("build", repo, "--json") == 3
    assert Path("graph.json").read_bytes() == b"old graph"
    assert not list(Path.cwd().glob(".graph.json.*.tmp"))
    assert capsys.readouterr().out == ""


def test_unexpected_internal_error_is_redacted(repo, monkeypatch, capsys):
    import rootweft.cli as cli

    def fail(*args):
        raise RuntimeError("secret internal state")

    monkeypatch.setattr(cli, "build_structural_graph", fail)
    assert run("build", repo, "--json") == 70
    result = capsys.readouterr()
    assert result.out == ""
    assert "secret internal state" not in result.err


def test_provider_check_requires_gate_and_checks_fixed_model(repo, monkeypatch, capsys):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    assert run("provider-check", "--provider", "typesafe", "--json") == 2
    capsys.readouterr()

    def respond(client, request, **kwargs):
        payload = json.loads(request.content)
        assert payload["model"] == "jev-1.13.0"
        qid = next(iter(payload["questions"]))
        return httpx.Response(
            200,
            request=request,
            json={
                "model": "jev-1.13.0",
                "answers": {qid: {"type": "noul", "noul": 1}},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    monkeypatch.setattr(httpx.Client, "send", respond)
    assert (
        run("provider-check", "--provider", "typesafe", "--enable-remote", "--json")
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["model"] == "jev-1.13.0"


def test_export_html_writes_standalone_artifact(repo, capsys):
    assert run("build", repo, "--json") == 0
    capsys.readouterr()
    assert (
        run(
            "export-html",
            "graph.json",
            "--output",
            "map.html",
            "--max-visible",
            "2",
            "--json",
        )
        == 0
    )
    assert Path("map.html").is_file()
    assert json.loads(capsys.readouterr().out)["output"] == "map.html"
