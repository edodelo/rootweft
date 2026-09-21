from __future__ import annotations

from hashlib import sha256

from rootweft.extract.python import extract_python
from rootweft.models import StructuralLayer
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


def test_python_containment_edges_identify_the_parser_origin() -> None:
    """Catches parser output being mislabeled as a downstream extractor decision."""
    batch = extract_python(scanned("mod.py", "def run():\n    pass\n"))

    assert {edge.origin for edge in batch.edges if edge.relation == "contains"} == {
        "parser"
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


def test_python_uses_every_enclosing_declaration_for_symbol_identity() -> None:
    """Catches colliding local names or incorrect local-method classification."""
    batch = extract_python(
        scanned(
            "pkg/mod.py",
            "def first():\n"
            "    def inner():\n"
            "        pass\n\n"
            "def second():\n"
            "    def inner():\n"
            "        pass\n\n"
            "class Outer:\n"
            "    def method(self):\n"
            "        def local():\n"
            "            pass\n"
            "    class Nested:\n"
            "        def child(self):\n"
            "            pass\n",
        )
    )

    nodes = {node.qualified_name: node for node in batch.nodes}
    assert {
        "pkg.mod.first.inner",
        "pkg.mod.second.inner",
        "Outer.method.local",
        "Outer.Nested.child",
    } <= nodes.keys()
    assert nodes["Outer.method"].kind == "method"
    assert nodes["Outer.method.local"].kind == "function"
    assert nodes["Outer.Nested.child"].kind == "method"


def test_python_normalizes_package_initializer_module_name() -> None:
    """Catches package initializers being emitted as a fictional __init__ module."""
    batch = extract_python(scanned("pkg/__init__.py", "value = 1\n"))

    assert "pkg" in symbol_names(batch)
    assert "pkg.__init__" not in symbol_names(batch)


def test_python_marks_computed_attribute_calls_and_direct_dynamic_imports() -> None:
    """Catches computed attribute bases or __import__ calls being treated as static."""
    batch = extract_python(
        scanned(
            "dynamic.py",
            "def run(obj, name, module, items):\n"
            "    getattr(obj, name)()\n"
            "    factory().run()\n"
            "    items[0].run()\n"
            "    __import__(module)\n",
        )
    )

    dynamic = {
        (reference.name, reference.relation)
        for reference in batch.references
        if reference.dynamic
    }
    assert {
        ("getattr", "calls"),
        ("factory().run", "calls"),
        ("items[...].run", "calls"),
        ("<dynamic import>", "imports"),
    } <= dynamic
    assert (
        sum(
            reference.name == "getattr" and reference.relation == "calls"
            for reference in batch.references
        )
        == 1
    )
    assert (
        sum(
            reference.name == "<dynamic import>" and reference.relation == "imports"
            for reference in batch.references
        )
        == 1
    )


def test_python_keeps_module_less_relative_import_prefixes() -> None:
    """Catches an extra dot being inserted in module-less relative imports."""
    batch = extract_python(
        scanned("pkg/mod.py", "from . import helper\nfrom .. import util\n")
    )

    assert [
        reference.name
        for reference in batch.references
        if reference.relation == "imports"
    ] == [".helper", "..util"]


def test_python_coalesces_overloaded_module_function_for_graph_uniqueness() -> None:
    """Catches overload declarations producing duplicate graph node or edge IDs."""
    batch = extract_python(
        scanned(
            "mod.py",
            "from typing import overload\n\n"
            "@overload\n"
            "def parse(value: int) -> int: ...\n\n"
            "@overload\n"
            "def parse(value: str) -> str: ...\n\n"
            "def parse(value: int | str) -> int | str:\n"
            "    return value\n",
        )
    )

    declarations = [node for node in batch.nodes if node.qualified_name == "mod.parse"]
    assert len(declarations) == 1
    assert declarations[0].evidence.start_line == 4
    StructuralLayer(nodes=batch.nodes, edges=batch.edges)


def test_python_coalesces_overloaded_methods_for_graph_uniqueness() -> None:
    """Catches class overloads emitting duplicate method nodes or contains edges."""
    batch = extract_python(
        scanned(
            "mod.py",
            "from typing import overload\n\n"
            "class Parser:\n"
            "    @overload\n"
            "    def parse(self, value: int) -> int: ...\n\n"
            "    @overload\n"
            "    def parse(self, value: str) -> str: ...\n\n"
            "    def parse(self, value: int | str) -> int | str:\n"
            "        return value\n",
        )
    )

    declarations = [
        node for node in batch.nodes if node.qualified_name == "Parser.parse"
    ]
    assert len(declarations) == 1
    assert declarations[0].evidence.start_line == 5
    StructuralLayer(nodes=batch.nodes, edges=batch.edges)


def test_python_coalesces_ordinary_same_scope_redefinitions() -> None:
    """Catches non-overload redefinitions violating the graph uniqueness contract."""
    batch = extract_python(
        scanned(
            "mod.py",
            "def run():\n    return 1\n\ndef run():\n    return 2\n",
        )
    )

    declarations = [node for node in batch.nodes if node.qualified_name == "mod.run"]
    assert len(declarations) == 1
    assert declarations[0].evidence.start_line == 1
    StructuralLayer(nodes=batch.nodes, edges=batch.edges)


def test_python_syntax_error_keeps_file_node_and_diagnostic() -> None:
    """Catches a parse failure discarding the source file from the graph."""
    batch = extract_python(scanned("broken.py", "def nope(:\n"))

    assert batch.nodes[0].kind == "file"
    assert batch.diagnostics[0].code == "syntax_error"
    assert batch.diagnostics[0].evidence is not None
    assert batch.diagnostics[0].evidence.start_line == 1
