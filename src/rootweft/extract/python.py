"""Static structural extraction for Python source without executing it."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from rootweft.extract.base import ExtractionBatch, Reference
from rootweft.ids import evidence_fingerprint, stable_id
from rootweft.models import Diagnostic, Edge, Evidence, Node
from rootweft.scanner import ScannedFile


def extract_python(file: ScannedFile) -> ExtractionBatch:
    """Extract declarations and unresolved references from a scanned Python file."""
    extractor = _PythonExtractor(file)
    extractor.add_file()
    try:
        tree = ast.parse(file.text, filename=file.path, type_comments=True)
    except SyntaxError as error:
        extractor.add_syntax_error(error)
        return extractor.batch()
    extractor.add_module()
    extractor.visit(tree)
    return extractor.batch()


@dataclass
class _PythonExtractor(ast.NodeVisitor):
    file: ScannedFile
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    _parents: list[Node] = field(default_factory=list)
    _classes: list[str] = field(default_factory=list)

    @property
    def module_name(self) -> str:
        path = self.file.path
        if path.endswith(".pyi"):
            path = path[:-4]
        elif path.endswith(".py"):
            path = path[:-3]
        return path.replace("/", ".")

    @property
    def current_source(self) -> Node:
        return self._parents[-1]

    def add_file(self) -> None:
        self.nodes.append(
            self._node("file", self.file.path, self.file.path, 1, self._last_line)
        )

    def add_module(self) -> None:
        module = self._node(
            "module", self.module_name, self.module_name, 1, self._last_line
        )
        self.nodes.append(module)
        self._contains(self.nodes[0], module, module.evidence)
        self._parents.append(module)

    @property
    def _last_line(self) -> int:
        return max(1, self.file.text.count("\n") + (not self.file.text.endswith("\n")))

    def batch(self) -> ExtractionBatch:
        return ExtractionBatch(
            nodes=tuple(self.nodes),
            edges=tuple(self.edges),
            references=tuple(self.references),
            diagnostics=tuple(self.diagnostics),
        )

    def add_syntax_error(self, error: SyntaxError) -> None:
        line = max(1, error.lineno or 1)
        self.diagnostics.append(
            Diagnostic(
                code="syntax_error",
                message="Python source could not be parsed",
                evidence=Evidence(self.file.path, line, line),
            )
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified_name = ".".join((*self._classes, node.name))
        symbol = self._add_symbol("class", node.name, qualified_name, node)
        self._parents.append(symbol)
        self._classes.append(node.name)
        self.generic_visit(node)
        self._classes.pop()
        self._parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = "method" if self._classes else "function"
        qualified_name = ".".join((*self._classes, node.name))
        if not self._classes:
            qualified_name = f"{self.module_name}.{node.name}"
        symbol = self._add_symbol(kind, node.name, qualified_name, node)
        self._parents.append(symbol)
        self.generic_visit(node)
        self._parents.pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._reference(alias.name, "imports", node, dynamic=False)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        prefix = "." * node.level + (node.module or "")
        for alias in node.names:
            name = f"{prefix}.{alias.name}" if prefix else alias.name
            self._reference(name, "imports", node, dynamic=False)

    def visit_Call(self, node: ast.Call) -> None:
        dynamic_import = self._dynamic_import_name(node)
        if dynamic_import is not None:
            name, dynamic = dynamic_import
            self._reference(name, "imports", node, dynamic=dynamic)
        else:
            name, dynamic = self._call_target(node.func)
            self._reference(name, "calls", node, dynamic=dynamic)
        self.generic_visit(node)

    def _add_symbol(
        self,
        kind: str,
        name: str,
        qualified_name: str,
        node: ast.AST,
    ) -> Node:
        evidence = self._evidence(node)
        symbol = self._node(
            kind, name, qualified_name, evidence.start_line, evidence.end_line
        )
        self.nodes.append(symbol)
        self._contains(self.current_source, symbol, evidence)
        return symbol

    def _node(
        self, kind: str, name: str, qualified_name: str, start_line: int, end_line: int
    ) -> Node:
        evidence = Evidence(self.file.path, start_line, end_line)
        source = self._source_lines(start_line, end_line)
        return Node(
            id=stable_id("python", self.file.path, kind, qualified_name),
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            language="python",
            evidence=evidence,
            metadata={"evidence_fingerprint": evidence_fingerprint(source)},
        )

    def _contains(self, source: Node, target: Node, evidence: Evidence) -> None:
        self.edges.append(
            Edge(
                id=stable_id("python", "contains", source.id, target.id),
                source=source.id,
                target=target.id,
                relation="contains",
                origin="extractor",
                status="accepted",
                evidence=evidence,
            )
        )

    def _reference(
        self, name: str, relation: str, node: ast.AST, *, dynamic: bool
    ) -> None:
        self.references.append(
            Reference(
                source_id=self.current_source.id,
                name=name,
                relation=relation,
                evidence=self._evidence(node),
                dynamic=dynamic,
            )
        )

    def _evidence(self, node: ast.AST) -> Evidence:
        start_line = getattr(node, "lineno", 1)
        end_line = getattr(node, "end_lineno", start_line)
        return Evidence(self.file.path, start_line, end_line)

    def _source_lines(self, start_line: int, end_line: int) -> str:
        return "\n".join(self.file.text.splitlines()[start_line - 1 : end_line])

    def _dynamic_import_name(self, node: ast.Call) -> tuple[str, bool] | None:
        if self._attribute_name(node.func) != "importlib.import_module":
            return None
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node.args[0].value, True
        return "<dynamic import>", True

    def _call_target(self, node: ast.expr) -> tuple[str, bool]:
        if isinstance(node, ast.Name):
            return node.id, node.id == "getattr"
        if isinstance(node, ast.Attribute):
            return self._attribute_name(node) or "<computed call>", False
        if isinstance(node, ast.Subscript):
            base = self._attribute_name(node.value) or "<computed>"
            return f"{base}[...]", True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
        ):
            return "getattr", True
        return "<computed call>", True

    def _attribute_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._attribute_name(node.value)
            return f"{parent}.{node.attr}" if parent else None
        return None
