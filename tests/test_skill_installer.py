"""Agent Skill installation is explicit, reversible and never edits configs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rootweft.cli import main
from rootweft.skill_installer import (
    MANIFEST_NAME,
    SkillInstallError,
    SkillTarget,
    canonical_skill_files,
    install_skill,
    mcp_config_snippet,
    skill_destination,
    uninstall_skill,
)

REPO_ROOT = Path(__file__).parents[1]


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def places(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    home = tmp_path / "home"
    project.mkdir()
    home.mkdir()
    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        (project / name).write_text(f"# {name}\n", encoding="utf-8")
    (home / ".codex").mkdir()
    (home / ".codex/config.toml").write_text("model = 'x'\n", encoding="utf-8")
    return project, home


def test_repository_mirror_matches_packaged_skill() -> None:
    packaged = canonical_skill_files()
    mirror = REPO_ROOT / "skills/rootweft/SKILL.md"
    assert set(packaged) == {"SKILL.md"}
    assert mirror.read_bytes() == packaged["SKILL.md"]


def test_skill_has_agent_skill_frontmatter() -> None:
    text = canonical_skill_files()["SKILL.md"].decode("utf-8")
    assert text.startswith("---\nname: rootweft\ndescription: ")
    assert "--enable-remote" in text  # tells agents never to add it


@pytest.mark.parametrize(
    ("target", "relative"),
    [
        (SkillTarget.AGENTS, ".agents/skills/rootweft"),
        (SkillTarget.CLAUDE, ".claude/skills/rootweft"),
        (SkillTarget.HERMES, ".hermes/skills/rootweft"),
        (SkillTarget.CURSOR, ".cursor/skills/rootweft"),
    ],
)
def test_project_destinations(places, target, relative) -> None:
    project, home = places
    assert skill_destination(target, "project", project=project, home=home) == (
        project / relative
    )
    assert skill_destination(target, "user", project=project, home=home) == (
        home / relative
    )


def test_hermes_accepts_selected_directory(places, tmp_path) -> None:
    project, home = places
    chosen = tmp_path / "hermes-skills"
    assert (
        skill_destination(
            SkillTarget.HERMES, "user", project=project, home=home, directory=chosen
        )
        == chosen / "rootweft"
    )


def test_dry_run_changes_nothing(places) -> None:
    project, home = places
    before = snapshot(project.parent)
    result = install_skill(
        SkillTarget.CLAUDE, "project", project=project, home=home, dry_run=True
    )
    assert result.dry_run and result.changed
    assert snapshot(project.parent) == before
    assert not (project / ".claude").exists()


def test_install_writes_skill_and_manifest_only(places) -> None:
    project, home = places
    before = snapshot(project.parent)
    result = install_skill(SkillTarget.AGENTS, "project", project=project, home=home)
    destination = project / ".agents/skills/rootweft"
    assert result.destination == destination
    assert (destination / "SKILL.md").read_bytes() == canonical_skill_files()[
        "SKILL.md"
    ]
    manifest = json.loads((destination / MANIFEST_NAME).read_text("utf-8"))
    assert set(manifest["files"]) == {"SKILL.md"}
    after = snapshot(project.parent)
    changed = {key for key in after if before.get(key) != after[key]}
    assert changed == {
        "project/.agents/skills/rootweft/SKILL.md",
        f"project/.agents/skills/rootweft/{MANIFEST_NAME}",
    }


def test_reinstall_is_idempotent(places) -> None:
    project, home = places
    install_skill(SkillTarget.CURSOR, "project", project=project, home=home)
    second = install_skill(SkillTarget.CURSOR, "project", project=project, home=home)
    assert not second.changed
    assert second.backup is None


def test_backup_before_replacement(places) -> None:
    project, home = places
    destination = project / ".claude/skills/rootweft"
    destination.mkdir(parents=True)
    (destination / "SKILL.md").write_text("user edited\n", encoding="utf-8")
    (destination / "notes.txt").write_text("keep\n", encoding="utf-8")
    result = install_skill(
        SkillTarget.CLAUDE, "project", project=project, home=home, stamp="20260923"
    )
    assert result.backup == project / ".claude/skills/rootweft.backup-20260923"
    assert (result.backup / "SKILL.md").read_text("utf-8") == "user edited\n"
    assert (result.backup / "notes.txt").read_text("utf-8") == "keep\n"
    assert (destination / "SKILL.md").read_bytes() == canonical_skill_files()[
        "SKILL.md"
    ]


def test_replacement_without_backup_is_refused(places) -> None:
    project, home = places
    destination = project / ".claude/skills/rootweft"
    destination.mkdir(parents=True)
    (destination / "SKILL.md").write_text("user edited\n", encoding="utf-8")
    with pytest.raises(SkillInstallError):
        install_skill(
            SkillTarget.CLAUDE, "project", project=project, home=home, backup=False
        )
    assert (destination / "SKILL.md").read_text("utf-8") == "user edited\n"


def test_uninstall_removes_only_manifest_files(places) -> None:
    project, home = places
    install_skill(SkillTarget.CLAUDE, "project", project=project, home=home)
    destination = project / ".claude/skills/rootweft"
    (destination / "mine.md").write_text("user file\n", encoding="utf-8")
    result = uninstall_skill(SkillTarget.CLAUDE, "project", project=project, home=home)
    assert sorted(result.removed) == [MANIFEST_NAME, "SKILL.md"]
    assert (destination / "mine.md").read_text("utf-8") == "user file\n"
    assert not (destination / "SKILL.md").exists()


def test_uninstall_keeps_modified_files(places) -> None:
    project, home = places
    install_skill(SkillTarget.AGENTS, "user", project=project, home=home)
    destination = home / ".agents/skills/rootweft"
    (destination / "SKILL.md").write_text("changed\n", encoding="utf-8")
    result = uninstall_skill(SkillTarget.AGENTS, "user", project=project, home=home)
    assert result.kept == ("SKILL.md",)
    assert (destination / "SKILL.md").read_text("utf-8") == "changed\n"


def test_uninstall_without_manifest_removes_nothing(places) -> None:
    project, home = places
    destination = project / ".cursor/skills/rootweft"
    destination.mkdir(parents=True)
    (destination / "SKILL.md").write_bytes(canonical_skill_files()["SKILL.md"])
    with pytest.raises(SkillInstallError):
        uninstall_skill(SkillTarget.CURSOR, "project", project=project, home=home)
    assert (destination / "SKILL.md").exists()


def test_uninstall_dry_run_changes_nothing(places) -> None:
    project, home = places
    install_skill(SkillTarget.HERMES, "user", project=project, home=home)
    before = snapshot(project.parent)
    result = uninstall_skill(
        SkillTarget.HERMES, "user", project=project, home=home, dry_run=True
    )
    assert sorted(result.removed) == [MANIFEST_NAME, "SKILL.md"]
    assert snapshot(project.parent) == before


def test_symlinked_destination_is_refused(places, tmp_path) -> None:
    project, home = places
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".claude").mkdir()
    try:
        (project / ".claude/skills").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    with pytest.raises(SkillInstallError):
        install_skill(SkillTarget.CLAUDE, "project", project=project, home=home)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("client", ["codex", "claude", "gemini", "hermes", "generic"])
def test_mcp_snippets_name_the_real_command(client, tmp_path) -> None:
    graph = tmp_path / "graph.json"
    snippet = mcp_config_snippet(client, graph)
    assert "rootweft" in snippet
    assert '"mcp"' in snippet or "'mcp'" in snippet or " mcp " in snippet
    assert str(graph.resolve()).replace("\\", "\\\\") in snippet or (
        graph.resolve().as_posix() in snippet
    )
    if client in {"claude", "gemini", "generic"}:
        start = snippet.index("{")
        json.loads(snippet[start:])


def test_cli_install_and_uninstall(places, monkeypatch, capsys) -> None:
    project, home = places
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    before = snapshot(project.parent)
    assert main(["install-skill", "--target", "claude", "--dry-run"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["dry_run"] is True
    assert snapshot(project.parent) == before
    assert main(["install-skill", "--target", "claude"]) == 0
    installed = json.loads(capsys.readouterr().out)
    assert Path(installed["destination"]) == project / ".claude/skills/rootweft"
    assert (project / "CLAUDE.md").read_text("utf-8") == "# CLAUDE.md\n"
    assert main(["uninstall-skill", "--target", "claude"]) == 0
    assert not (project / ".claude/skills/rootweft/SKILL.md").exists()


def test_cli_mcp_config_prints_snippet(tmp_path, capsys) -> None:
    assert main(["mcp-config", "codex", str(tmp_path / "graph.json")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("[mcp_servers.rootweft]")
