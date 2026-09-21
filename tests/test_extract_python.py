from __future__ import annotations

from hashlib import sha256

from rootweft.extract.python import extract_python
from rootweft.scanner import ScannedFile


def scanned(path: str, text: str) -> ScannedFile:
    return ScannedFile(path, "python", text, sha256(text.encode()).hexdigest())


def symbol_names(batch: object) -> set[str]:
    return {
        node.qualified_name or node.name  # type: ignore[union-attr]
        for node in batch.nodes  # type: ignore[union-attr]
    }


def only(batch: object, *, name: str, relation: str) -> object:
    matches = [
        reference
        for reference in batch.references  # type: ignore[union-attr]
        if reference.name == name and reference.relation == relation
    ]
    assert len(matches) == 1
    return matches[0]


def test_python_extracts_qualified_symbols_and_static_calls() -> None:
    """Catches missing structural symbols or a call attached to the wrong span."""
    batch = extract_python(
        scanned("pkg/mod.py", "class A:\n    def run(self):\n        helper()\n")
    )

    assert symbol_names(batch) >= {"pkg.mod", "A", "A.run"}
    reference = only(batch, name="helper", relation="calls")
    assert reference.evidence.start_line == 3  # type: ignore[union-attr]
    assert reference.dynamic is False  # type: ignore[union-attr]


def test_python_extracts_file_module_containment_and_imports() -> None:
    """Catches structural nodes being disconnected or imports being omitted."""
    batch = extract_python(
        scanned(
            "pkg/mod.py",
            "import os.path\n"
            "from tools.helpers import format_value\n\n"
            "def run():\n"
            "    return format_value()\n",
        )
    )

    nodes = {node.qualified_name or node.name: node for node in batch.nodes}
    assert {node.kind for node in batch.nodes} >= {"file", "module", "function"}
    assert {(edge.source, edge.target, edge.relation) for edge in batch.edges} >= {
        (nodes["pkg/mod.py"].id, nodes["pkg.mod"].id, "contains"),
        (nodes["pkg.mod"].id, nodes["pkg.mod.run"].id, "contains"),
    }
    assert {
        (reference.name, reference.relation, reference.dynamic)
        for reference in batch.references
    } >= {
        ("os.path", "imports", False),
        ("tools.helpers.format_value", "imports", False),
    }


def test_python_marks_computed_calls_and_dynamic_imports_unresolved() -> None:
    """Catches dynamic targets being emitted as falsely resolvable static references."""
    batch = extract_python(
        scanned(
            "dynamic.py",
            "import importlib\n\n"
            "def run(obj, name, key, module):\n"
            "    getattr(obj, name)()\n"
            "    obj[key]()\n"
            "    importlib.import_module('plugin')\n"
            "    importlib.import_module(module)\n",
        )
    )

    dynamic = {
        (reference.name, reference.relation)
        for reference in batch.references
        if reference.dynamic
    }
    assert dynamic >= {
        ("getattr", "calls"),
        ("obj[...]", "calls"),
        ("plugin", "imports"),
        ("<dynamic import>", "imports"),
    }


def test_python_node_identifiers_ignore_line_number_changes() -> None:
    """Catches stable IDs changing when a declaration is merely moved."""
    original = extract_python(scanned("pkg/mod.py", "def run():\n    pass\n"))
    moved = extract_python(scanned("pkg/mod.py", "\n\n\ndef run():\n    pass\n"))

    def identifiers(batch: object) -> dict[str, str]:
        return {
            node.qualified_name or node.name: node.id  # type: ignore[union-attr]
            for node in batch.nodes  # type: ignore[union-attr]
        }

    assert identifiers(original) == identifiers(moved)


def test_python_syntax_error_keeps_file_node_and_diagnostic() -> None:
    """Catches a parse failure discarding the source file from the graph."""
    batch = extract_python(scanned("broken.py", "def nope(:\n"))

    assert batch.nodes[0].kind == "file"
    assert batch.diagnostics[0].code == "syntax_error"
    assert batch.diagnostics[0].evidence is not None
    assert batch.diagnostics[0].evidence.start_line == 1
