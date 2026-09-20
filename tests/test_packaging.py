from __future__ import annotations

import importlib
import tomllib
from pathlib import Path


def test_declared_console_scripts_are_importable() -> None:
    """Catches package metadata publishing a command before its target exists."""
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    scripts = project["project"].get("scripts", {})

    for target in scripts.values():
        module_name, separator, attribute_name = target.partition(":")
        assert separator == ":"
        assert getattr(importlib.import_module(module_name), attribute_name)
