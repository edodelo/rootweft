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
    extractor._module_shadowed = _module_rebindings(tree)
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
    _declarations: list[_DeclarationScope] = field(default_factory=list)
    _scope_bindings: list[frozenset[str]] = field(default_factory=list)
    _module_shadowed: frozenset[str] = frozenset()
    _symbols: dict[tuple[str, str], Node] = field(default_factory=dict)

    @property
    def module_name(self) -> str:
        path = self.file.path
        if path.endswith("/__init__.pyi"):
            path = path[: -len("/__init__.pyi")]
        elif path.endswith("/__init__.py"):
            path = path[: -len("/__init__.py")]
        elif path.endswith(".pyi"):
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
        qualified_name = self._qualified_name(node.name, kind="class")
        symbol = self._add_symbol("class", node.name, qualified_name, node)
        self._parents.append(symbol)
        self._declarations.append(_DeclarationScope(symbol, "class"))
        self.generic_visit(node)
        self._declarations.pop()
        self._parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        collector = _BindingCollector()
        collector.visit(node.body)
        self._scope_bindings.append(
            frozenset(_parameter_names(node.args) | collector.bound)
        )
        self.generic_visit(node)
        self._scope_bindings.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = (
            "method"
            if self._declarations and self._declarations[-1].kind == "class"
            else "function"
        )
        qualified_name = self._qualified_name(node.name, kind=kind)
        symbol = self._add_symbol(kind, node.name, qualified_name, node)
        self._parents.append(symbol)
        self._declarations.append(_DeclarationScope(symbol, kind))
        self._scope_bindings.append(_function_bindings(node))
        self.generic_visit(node)
        self._scope_bindings.pop()
        self._declarations.pop()
        self._parents.pop()

    def _qualified_name(self, name: str, *, kind: str) -> str:
        if self._declarations:
            return f"{self._declarations[-1].node.qualified_name}.{name}"
        if kind == "class":
            return name
        return f"{self.module_name}.{name}"

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._reference(alias.name, "imports", node, dynamic=False)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        prefix = "." * node.level + (node.module or "")
        for alias in node.names:
            name = f"{prefix}.{alias.name}" if node.module else f"{prefix}{alias.name}"
            self._reference(
                name,
                "imports",
                node,
                dynamic=alias.name == "*",
                import_kind="symbol",
            )

    def visit_Call(self, node: ast.Call) -> None:
        dynamic_import = self._dynamic_import_name(node)
        if dynamic_import is not None:
            name, dynamic = dynamic_import
            self._reference(name, "imports", node, dynamic=dynamic)
        else:
            name, dynamic = self._call_target(node.func)
            root_name = _callee_root_name(node.func)
            shadowed = root_name is not None and (
                root_name in self._module_shadowed
                or any(root_name in scope for scope in self._scope_bindings)
            )
            self._reference(name, "calls", node, dynamic=dynamic, shadowed=shadowed)
        if self._is_getattr_wrapper(node.func):
            self._visit_getattr_wrapper_children(node)
        else:
            self.generic_visit(node)

    def _add_symbol(
        self,
        kind: str,
        name: str,
        qualified_name: str,
        node: ast.AST,
    ) -> Node:
        key = (kind, qualified_name)
        existing = self._symbols.get(key)
        if existing is not None:
            return existing
        evidence = self._evidence(node)
        symbol = self._node(
            kind, name, qualified_name, evidence.start_line, evidence.end_line
        )
        self._symbols[key] = symbol
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
                origin="parser",
                status="accepted",
                evidence=evidence,
            )
        )

    def _reference(
        self,
        name: str,
        relation: str,
        node: ast.AST,
        *,
        dynamic: bool,
        import_kind: str = "module",
        shadowed: bool = False,
    ) -> None:
        self.references.append(
            Reference(
                source_id=self.current_source.id,
                name=name,
                relation=relation,
                evidence=self._evidence(node),
                dynamic=dynamic,
                import_kind=import_kind,
                shadowed=shadowed,
            )
        )

    def _evidence(self, node: ast.AST) -> Evidence:
        start_line = getattr(node, "lineno", 1)
        end_line = getattr(node, "end_lineno", start_line)
        return Evidence(self.file.path, start_line, end_line)

    def _source_lines(self, start_line: int, end_line: int) -> str:
        return "\n".join(self.file.text.splitlines()[start_line - 1 : end_line])

    def _dynamic_import_name(self, node: ast.Call) -> tuple[str, bool] | None:
        callee = self._attribute_name(node.func)
        if callee not in {"importlib.import_module", "__import__"}:
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
            name = self._attribute_name(node)
            return (
                (name, False)
                if name is not None
                else (self._expression_name(node), True)
            )
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

    def _is_getattr_wrapper(self, node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
        )

    def _visit_getattr_wrapper_children(self, node: ast.Call) -> None:
        getter = node.func
        assert isinstance(getter, ast.Call)
        for child in (*getter.args, *(keyword.value for keyword in getter.keywords)):
            self.visit(child)
        for child in (*node.args, *(keyword.value for keyword in node.keywords)):
            self.visit(child)

    def _expression_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return f"{self._expression_name(node.value)}.{node.attr}"
        if isinstance(node, ast.Subscript):
            return f"{self._expression_name(node.value)}[...]"
        if isinstance(node, ast.Call):
            return f"{self._expression_name(node.func)}()"
        return "<computed>"

    def _attribute_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._attribute_name(node.value)
            return f"{parent}.{node.attr}" if parent else None
        return None


@dataclass(frozen=True)
class _DeclarationScope:
    node: Node
    kind: str


def _function_bindings(node: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    """Names assigned within a Python function's lexical scope."""
    collector = _BindingCollector()
    for statement in node.body:
        collector.visit(statement)
    return frozenset(
        (_parameter_names(node.args) | collector.bound) - collector.global_names
    )


def _parameter_names(args: ast.arguments) -> set[str]:
    parameters = {
        argument.arg for argument in (*args.posonlyargs, *args.args, *args.kwonlyargs)
    }
    if args.vararg is not None:
        parameters.add(args.vararg.arg)
    if args.kwarg is not None:
        parameters.add(args.kwarg.arg)
    return parameters


def _module_rebindings(tree: ast.Module) -> frozenset[str]:
    collector = _ModuleBindingCollector()
    for statement in tree.body:
        collector.visit(statement)
    return frozenset(collector.bound)


def _callee_root_name(node: ast.expr) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


class _BindingCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.bound: set[str] = set()
        self.global_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.bound.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.bound.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.bound.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bound.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.bound.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.bound.add(alias.asname or alias.name)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            self.bound.add(node.name)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.bound.update(node.names)


class _ModuleBindingCollector(_BindingCollector):
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return
