"""Structural JS/TS extraction using installed grammar wheels, without execution.

Named declarations, generators, variable-bound functions/arrows, class methods,
class function fields, and default exports are supported. Anonymous callbacks,
object methods, and computed declaration names do not create invented symbols;
their runtime references remain attached to the enclosing named scope. Types and
JSX element names are not calls. Import references name the source module, not
individual imported bindings. All dynamic imports remain unresolved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

import tree_sitter_javascript
import tree_sitter_typescript
from tree_sitter import Language, Parser
from tree_sitter import Node as SyntaxNode

from rootweft.extract.base import ExtractionBatch, Reference
from rootweft.ids import evidence_fingerprint, stable_id
from rootweft.models import Diagnostic, Edge, Evidence, Node
from rootweft.scanner import ScannedFile

_FUNCTIONS = {
    "function_declaration",
    "generator_function_declaration",
    "function_signature",
}
_FUNCTION_VALUES = {"arrow_function", "function_expression", "generator_function"}
_STATIC_NAMES = {
    "identifier",
    "property_identifier",
    "type_identifier",
    "private_property_identifier",
}


def extract_javascript(file: ScannedFile) -> ExtractionBatch:
    """Parse one JS/JSX/MJS/CJS, TS, or TSX file with a fresh parser."""
    suffix = PurePosixPath(file.path).suffix.casefold()
    if suffix == ".tsx":
        language = Language(tree_sitter_typescript.language_tsx())
    elif suffix == ".ts":
        language = Language(tree_sitter_typescript.language_typescript())
    elif suffix in {".js", ".jsx", ".mjs", ".cjs"}:
        language = Language(tree_sitter_javascript.language())
    else:
        raise ValueError("unsupported JavaScript/TypeScript suffix")
    source = file.text.encode("utf-8")
    root = Parser(language).parse(source).root_node
    extractor = _Extractor(
        file, source, "typescript" if suffix in {".ts", ".tsx"} else "javascript"
    )
    file_node = extractor.add_node("file", file.path, file.path, extractor.whole_file)
    if root.has_error:
        # One content-free diagnostic bounds output even for badly damaged input.
        pending = [root]
        fault = root
        while pending:
            candidate = pending.pop()
            if candidate.is_error or candidate.is_missing:
                fault = candidate
                break
            pending.extend(reversed(candidate.children))
        return ExtractionBatch(
            nodes=(file_node,),
            diagnostics=(
                Diagnostic(
                    code="syntax_error",
                    message="JavaScript/TypeScript source could not be parsed",
                    evidence=extractor.evidence(fault),
                ),
            ),
        )
    module = extractor.add_node(
        "module",
        extractor.module_name,
        extractor.module_name,
        extractor.whole_file,
        file_node,
    )
    pending_nodes = [(child, module) for child in reversed(root.named_children)]
    while pending_nodes:
        syntax, parent = pending_nodes.pop()
        symbol = extractor.declaration(syntax, parent)
        current = symbol or parent
        extractor.references_for(syntax, current)
        pending_nodes.extend(
            (child, current) for child in reversed(syntax.named_children)
        )
    return ExtractionBatch(
        nodes=tuple(extractor.nodes),
        edges=tuple(extractor.edges),
        references=tuple(extractor.references),
    )


@dataclass
class _Extractor:
    file: ScannedFile
    source: bytes
    language: str
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    symbols: dict[tuple[str, str], Node] = field(default_factory=dict)

    @property
    def module_name(self) -> str:
        return str(PurePosixPath(self.file.path).with_suffix("")).replace("/", ".")

    @property
    def whole_file(self) -> Evidence:
        return Evidence(self.file.path, 1, max(1, len(self.file.text.splitlines())))

    def text(self, node: SyntaxNode) -> str:
        return self.source[node.start_byte : node.end_byte].decode("utf-8")

    def evidence(self, node: SyntaxNode) -> Evidence:
        start = node.start_point.row + 1
        end = node.end_point.row + (node.end_point.column != 0)
        return Evidence(self.file.path, start, max(start, end))

    def add_node(
        self,
        kind: str,
        name: str,
        qualified: str,
        evidence: Evidence,
        parent: Node | None = None,
    ) -> Node:
        key = kind, qualified
        if key in self.symbols:
            return self.symbols[key]
        lines = self.file.text.splitlines()[evidence.start_line - 1 : evidence.end_line]
        symbol = Node(
            id=stable_id(self.language, self.file.path, kind, qualified),
            kind=kind,
            name=name,
            qualified_name=qualified,
            language=self.language,
            evidence=evidence,
            metadata={"evidence_fingerprint": evidence_fingerprint("\n".join(lines))},
        )
        self.symbols[key] = symbol
        self.nodes.append(symbol)
        if parent is not None:
            self.edges.append(
                Edge(
                    id=stable_id(self.language, "contains", parent.id, symbol.id),
                    source=parent.id,
                    target=symbol.id,
                    relation="contains",
                    origin="parser",
                    status="accepted",
                    evidence=evidence,
                )
            )
        return symbol

    def declaration(self, node: SyntaxNode, parent: Node) -> Node | None:
        kind: str | None = None
        name_node = node.child_by_field_name("name")
        name: str | None = None
        if node.type in _FUNCTIONS:
            kind = "function"
        elif node.type in {"class_declaration", "class"}:
            # Class expressions are only named here when they are default exports.
            if node.type == "class_declaration" or self.default_export(node):
                kind = "class"
        elif (
            node.type in {"method_definition", "method_signature"}
            and node.parent is not None
            and node.parent.type == "class_body"
        ):
            kind = "method"
        elif node.type in {
            "variable_declarator",
            "field_definition",
            "public_field_definition",
        }:
            value = node.child_by_field_name("value")
            if value is not None and value.type in _FUNCTION_VALUES:
                kind = "function" if node.type == "variable_declarator" else "method"
                name_node = name_node or node.child_by_field_name("property")
        elif node.type in _FUNCTION_VALUES and self.default_export(node):
            kind = "function"
        if kind is None:
            return None
        if name_node is not None and name_node.type in _STATIC_NAMES:
            name = self.text(name_node)
        elif name_node is None and self.default_export(node):
            name = "default"
        if name is None:
            return None
        qualified = (
            name
            if kind == "class" and parent.kind == "module"
            else f"{parent.qualified_name}.{name}"
        )
        return self.add_node(kind, name, qualified, self.evidence(node), parent)

    @staticmethod
    def default_export(node: SyntaxNode) -> bool:
        parent = node.parent
        return (
            parent is not None
            and parent.type == "export_statement"
            and any(child.type == "default" for child in parent.children)
        )

    def references_for(self, node: SyntaxNode, parent: Node) -> None:
        if node.type in {"import_statement", "export_statement"}:
            source = node.child_by_field_name("source")
            if source is not None:
                name = self.literal(source)
                self.reference(
                    name or "<dynamic import>",
                    "imports",
                    node,
                    parent,
                    dynamic=name is None,
                )
        elif node.type in {"call_expression", "new_expression"}:
            target = node.child_by_field_name(
                "function" if node.type == "call_expression" else "constructor"
            )
            if target is None:
                return
            if target.type == "import":
                args = node.child_by_field_name("arguments")
                argument = (
                    args.named_children[0] if args and args.named_children else None
                )
                name = self.literal(argument) if argument is not None else None
                self.reference(
                    name or "<dynamic import>", "imports", node, parent, dynamic=True
                )
            else:
                name, dynamic = self.call_target(target)
                optional = node.child_by_field_name("optional_chain") is not None
                self.reference(name, "calls", node, parent, dynamic=dynamic or optional)

    def literal(self, node: SyntaxNode) -> str | None:
        if node.type not in {"string", "template_string"}:
            return None
        # Escaped/computed text is kept unresolved rather than guessing a module.
        if any(
            child.type in {"template_substitution", "escape_sequence"}
            for child in node.named_children
        ):
            return None
        return self.text(node)[1:-1]

    def call_target(self, node: SyntaxNode) -> tuple[str, bool]:
        if node.type in _STATIC_NAMES | {"this", "super"}:
            return self.text(node), False
        if node.type in {"member_expression", "subscript_expression"}:
            base = node.child_by_field_name("object")
            base_name, dynamic = (
                self.call_target(base) if base else ("<computed>", True)
            )
            if node.type == "subscript_expression":
                return f"{base_name}[...]", True
            prop = node.child_by_field_name("property")
            if prop is None:
                return "<computed call>", True
            optional = node.child_by_field_name("optional_chain") is not None
            return f"{base_name}.{self.text(prop)}", dynamic or optional
        if node.type == "call_expression":
            callee = node.child_by_field_name("function")
            name = self.call_target(callee)[0] if callee else "<computed>"
            return f"{name}()", True
        return "<computed call>", True

    def reference(
        self, name: str, relation: str, node: SyntaxNode, parent: Node, *, dynamic: bool
    ) -> None:
        self.references.append(
            Reference(
                source_id=parent.id,
                name=name,
                relation=relation,
                evidence=self.evidence(node),
                dynamic=dynamic,
            )
        )
