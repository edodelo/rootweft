from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

import rootweft.extract.javascript as javascript
from rootweft.extract.javascript import extract_javascript
from rootweft.models import StructuralLayer
from rootweft.scanner import ScannedFile

FIXTURES = Path(__file__).parent / "fixtures" / "web"


def scanned(path: str, text: str) -> ScannedFile:
    language = "typescript" if path.endswith((".ts", ".tsx")) else "javascript"
    return ScannedFile(path, language, text, sha256(text.encode()).hexdigest())


@pytest.mark.parametrize("suffix", [".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"])
def test_dialects_extract_declarations_imports_and_runtime_calls(suffix: str) -> None:
    """Catches wrong grammar selection and calls invented from JSX/type names."""
    source = (FIXTURES / f"sample{suffix}").read_text(encoding="utf-8")
    batch = extract_javascript(scanned(f"src/app{suffix}", source))

    assert not batch.diagnostics
    assert any(n.kind == "function" and n.name == "run" for n in batch.nodes)
    assert [(r.name, r.dynamic) for r in batch.references if r.relation == "calls"] == [
        ("helper", False)
    ]
    assert [
        (r.name, r.dynamic) for r in batch.references if r.relation == "imports"
    ] == [("./helper", False)]
    assert {n.language for n in batch.nodes} == {
        "typescript" if suffix in {".ts", ".tsx"} else "javascript"
    }


def test_exported_nested_and_variable_functions_keep_lexical_scopes() -> None:
    """Catches declarations lost through export wrappers or scope collisions."""
    batch = extract_javascript(
        scanned(
            "src/app.js",
            """export function run() {
  function local() { helper(); }
}
export const arrow = () => helper();
const assigned = function hidden() { helper(); };
export class Box {
  run() { function local() { helper(); } }
  field = () => helper();
  static make() { return new Box(); }
}
export default function () { helper(); }
""",
        )
    )
    nodes = {n.qualified_name: n for n in batch.nodes}
    expected = {
        "src/app.js": "file",
        "src.app": "module",
        "src.app.run": "function",
        "src.app.run.local": "function",
        "src.app.arrow": "function",
        "src.app.assigned": "function",
        "Box": "class",
        "Box.run": "method",
        "Box.run.local": "function",
        "Box.field": "method",
        "Box.make": "method",
        "src.app.default": "function",
    }
    assert {name: n.kind for name, n in nodes.items()} == expected
    assert (
        nodes["Box.run.local"].evidence.start_line,
        nodes["Box.run.local"].evidence.end_line,
    ) == (7, 7)
    assert {e.origin for e in batch.edges} == {"parser"}
    assert {e.relation for e in batch.edges} == {"contains"}
    assert {e.status for e in batch.edges} == {"accepted"}
    assert {(e.source, e.target) for e in batch.edges} >= {
        (nodes["src/app.js"].id, nodes["src.app"].id),
        (nodes["src.app.run"].id, nodes["src.app.run.local"].id),
        (nodes["Box.run"].id, nodes["Box.run.local"].id),
        (nodes["Box"].id, nodes["Box.field"].id),
    }
    assert next(
        r for r in batch.references if r.evidence.start_line == 2
    ).source_id == (nodes["src.app.run.local"].id)


def test_static_imports_reexports_and_dynamic_imports_preserve_uncertainty() -> None:
    """Catches omitted dependency forms and falsely static computed imports."""
    batch = extract_javascript(
        scanned(
            "app.js",
            """import main, {thing as local} from './lib';
import './side';
export {thing as renamed} from './other';
export * from './all';
export * as ns from './ns';
export {local};
import('literal');
import(`template`);
import(`prefix/${name}`);
import(moduleName);
""",
        )
    )
    assert [
        (r.name, r.relation, r.dynamic, r.evidence.start_line) for r in batch.references
    ] == [
        ("./lib", "imports", False, 1),
        ("./side", "imports", False, 2),
        ("./other", "imports", False, 3),
        ("./all", "imports", False, 4),
        ("./ns", "imports", False, 5),
        ("literal", "imports", True, 7),
        ("template", "imports", True, 8),
        ("<dynamic import>", "imports", True, 9),
        ("<dynamic import>", "imports", True, 10),
    ]


def test_call_forms_mark_optional_computed_and_chained_targets_dynamic() -> None:
    """Catches uncertain callees being resolvable and constructors being omitted."""
    batch = extract_javascript(
        scanned(
            "app.js",
            """direct();
obj.run();
obj?.run();
obj.run?.();
obj[key]();
factory().run();
new Box();
new registry[key]();
new ns.Box();
fn?.();
items[0].run();
""",
        )
    )
    assert [(r.name, r.dynamic, r.evidence.start_line) for r in batch.references] == [
        ("direct", False, 1),
        ("obj.run", False, 2),
        ("obj.run", True, 3),
        ("obj.run", True, 4),
        ("obj[...]", True, 5),
        ("factory().run", True, 6),
        ("factory", False, 6),
        ("Box", False, 7),
        ("registry[...]", True, 8),
        ("ns.Box", False, 9),
        ("fn", True, 10),
        ("items[...].run", True, 11),
    ]
    assert {r.relation for r in batch.references} == {"calls"}


@pytest.mark.parametrize("suffix", [".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"])
@pytest.mark.parametrize("broken", ["function broken( {", "function broken() {"])
def test_any_syntax_error_discards_partial_structure(suffix: str, broken: str) -> None:
    """Catches partial symbols surviving ERROR nodes or missing closing tokens."""
    batch = extract_javascript(
        scanned(f"broken{suffix}", "function valid() {}\n" + broken)
    )
    assert [n.kind for n in batch.nodes] == ["file"]
    assert batch.edges == batch.references == ()
    assert len(batch.diagnostics) == 1
    diagnostic = batch.diagnostics[0]
    assert diagnostic.code == "syntax_error"
    assert diagnostic.evidence is not None
    assert diagnostic.evidence.start_line == 2
    assert len(diagnostic.message) < 100
    assert "broken" not in diagnostic.message


def test_unicode_bytes_and_shifted_lines_keep_symbol_ids_stable() -> None:
    """Catches byte/character slicing mistakes and line-derived identities."""
    source = "const label = 'é';\nexport function café() { helper(); }\n"
    original = extract_javascript(scanned("src/app.ts", source))
    moved = extract_javascript(scanned("src/app.ts", "\n\n" + source))
    assert {n.qualified_name: n.id for n in original.nodes} == {
        n.qualified_name: n.id for n in moved.nodes
    }
    assert next(n for n in original.nodes if n.name == "café").evidence.start_line == 2
    assert next(n for n in moved.nodes if n.name == "café").evidence.start_line == 4
    assert extract_javascript(scanned("src/app.ts", source)) == original


def test_typescript_overloads_coalesce_without_turning_types_into_symbols() -> None:
    """Catches duplicate graph IDs and accidental extraction of type-only signatures."""
    batch = extract_javascript(
        scanned(
            "app.ts",
            """interface Api { run(): void; }
type Fn = (x: number) => number;
function parse(x: string): void;
function parse(x: number): void;
function parse(x: unknown) { helper(); }
class Parser {
  run(x: string): void;
  run(x: number): void;
  run(x: unknown) { helper(); }
}
""",
        )
    )
    assert not batch.diagnostics
    assert [
        (n.qualified_name, n.kind)
        for n in batch.nodes
        if n.kind not in {"file", "module"}
    ] == [("app.parse", "function"), ("Parser", "class"), ("Parser.run", "method")]
    assert [r.name for r in batch.references] == ["helper", "helper"]
    StructuralLayer(nodes=batch.nodes, edges=batch.edges)


def test_default_anonymous_class_and_generator_are_declarations() -> None:
    """Catches default anonymous exports and generator declarations being lost."""
    batch = extract_javascript(
        scanned(
            "app.js",
            """export default class { run() {} }
export function* values() { yield helper(); }
""",
        )
    )
    assert {(n.qualified_name, n.kind) for n in batch.nodes} >= {
        ("default", "class"),
        ("default.run", "method"),
        ("app.values", "function"),
    }


def test_computed_methods_and_anonymous_callbacks_do_not_invent_named_symbols() -> None:
    """Defines conservative coverage when declarations have no stable static name."""
    batch = extract_javascript(
        scanned(
            "app.js",
            """class Box { [name]() { helper(); } }
const object = { method() { helper(); } };
items.map(() => helper());
""",
        )
    )
    assert [
        (n.name, n.kind) for n in batch.nodes if n.kind not in {"file", "module"}
    ] == [("Box", "class")]
    assert [r.name for r in batch.references].count("helper") == 3


def test_deep_valid_member_chain_does_not_depend_on_python_recursion_limit() -> None:
    """Catches extraction crashing on a small valid file with deeply nested members."""
    target = "root" + ".member" * 1100
    batch = extract_javascript(scanned("deep.js", target + "();\n"))
    assert not batch.diagnostics
    assert [(r.name, r.dynamic) for r in batch.references] == [(target, False)]
    StructuralLayer(nodes=batch.nodes, edges=batch.edges)


@pytest.mark.parametrize("suffix", [".js", ".ts"])
def test_sibling_block_bindings_have_distinct_stable_ids_and_reference_owners(
    suffix: str,
) -> None:
    """Catches same-spelled lexical bindings being merged across sibling blocks."""
    source = "{ const run = () => left(); }\n{ const run = () => right(); }\n"
    batch = extract_javascript(scanned("blocks" + suffix, source))
    shifted = extract_javascript(scanned("blocks" + suffix, "\n\n" + source))
    functions = [n for n in batch.nodes if n.name == "run"]
    assert len(functions) == 2
    assert len({n.id for n in functions}) == 2
    assert [(r.name, r.source_id) for r in batch.references] == [
        ("left", functions[0].id),
        ("right", functions[1].id),
    ]
    assert [n.id for n in batch.nodes] == [n.id for n in shifted.nodes]
    StructuralLayer(nodes=batch.nodes, edges=batch.edges)


def test_namespaces_separate_bindings_but_reopened_namespace_overloads_coalesce() -> (
    None
):
    """Catches namespaces sharing one binding or reopened overloads being split."""
    source = """namespace Left { export function run(x: string): void; }
namespace Right { export function run() { right(); } }
namespace Left { export function run(x: unknown) { left(); } }
"""
    batch = extract_javascript(scanned("names.ts", source))
    shifted = extract_javascript(scanned("names.ts", "\n" + source))
    functions = {n.qualified_name: n for n in batch.nodes if n.name == "run"}
    assert set(functions) == {"names.Left.run", "names.Right.run"}
    assert [(r.name, r.source_id) for r in batch.references] == [
        ("right", functions["names.Right.run"].id),
        ("left", functions["names.Left.run"].id),
    ]
    assert [n.id for n in batch.nodes] == [n.id for n in shifted.nodes]
    StructuralLayer(nodes=batch.nodes, edges=batch.edges)


def test_static_and_instance_methods_keep_separate_bindings_and_overloads() -> None:
    """Catches static/instance methods and their local declarations sharing IDs."""
    source = """class Box {
  static run(x: string): void;
  static run(x: unknown) { function local() { left(); } }
  run(x: string): void;
  run(x: unknown) { function local() { right(); } }
}
"""
    batch = extract_javascript(scanned("methods.ts", source))
    shifted = extract_javascript(scanned("methods.ts", "\n" + source))
    methods = [n for n in batch.nodes if n.name == "run"]
    locals_ = [n for n in batch.nodes if n.name == "local"]
    assert len(methods) == len(locals_) == 2
    assert len({n.id for n in methods + locals_}) == 4
    assert [(r.name, r.source_id) for r in batch.references] == [
        ("left", locals_[0].id),
        ("right", locals_[1].id),
    ]
    assert {(e.source, e.target) for e in batch.edges} >= {
        (methods[0].id, locals_[0].id),
        (methods[1].id, locals_[1].id),
    }
    assert [n.id for n in batch.nodes] == [n.id for n in shifted.nodes]
    StructuralLayer(nodes=batch.nodes, edges=batch.edges)


@pytest.mark.parametrize("declarations", [100, 1000])
def test_evidence_line_indexing_has_a_file_sized_work_budget(declarations: int) -> None:
    """Catches rescanning the whole input for each declaration's evidence span."""

    class MeasuredText(str):
        scanned_characters = 0

        def splitlines(self, keepends: bool = False) -> list[str]:
            self.scanned_characters += len(self)
            return super().splitlines(keepends)

    source = MeasuredText(
        "".join(f"function item{i}() {{}}\n" for i in range(declarations))
    )
    batch = extract_javascript(scanned("many.js", source))
    functions = [n for n in batch.nodes if n.kind == "function"]
    assert len(functions) == declarations
    assert functions[0].evidence.start_line == 1
    assert functions[-1].evidence.start_line == declarations
    assert (
        functions[0].metadata["evidence_fingerprint"]
        == sha256(b"function item0() {}").hexdigest()
    )
    assert source.scanned_characters <= len(source) * 2


@pytest.mark.parametrize("declarations", [100, 1000])
def test_minified_evidence_hashing_has_a_linear_input_budget(
    declarations: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches rehashing a complete minified line for each declaration on that line."""
    real_fingerprint = javascript.evidence_fingerprint
    hashed_characters = 0

    def measured_fingerprint(text: str) -> str:
        nonlocal hashed_characters
        hashed_characters += len(text)
        return real_fingerprint(text)

    monkeypatch.setattr(javascript, "evidence_fingerprint", measured_fingerprint)
    source = "".join(f"function item{i}() {{}}" for i in range(declarations))
    batch = extract_javascript(scanned("minified.js", source))
    assert len([n for n in batch.nodes if n.kind == "function"]) == declarations
    assert {(n.evidence.start_line, n.evidence.end_line) for n in batch.nodes} == {
        (1, 1)
    }
    assert {n.metadata["evidence_fingerprint"] for n in batch.nodes} == {
        sha256(source.encode()).hexdigest()
    }
    StructuralLayer(nodes=batch.nodes, edges=batch.edges)
    assert hashed_characters <= len(source) + 4 * declarations


@pytest.mark.parametrize("namespace", ["A.B", "A . B", "A /* comment */ . B"])
def test_dotted_and_nested_namespaces_share_one_overloaded_binding(
    namespace: str,
) -> None:
    """Catches equivalent namespace syntax giving an overload two logical IDs."""
    source = (
        f"namespace {namespace} {{ export function run(x: string): void; }}\n"
        "namespace A { export namespace B { "
        "export function run(x: unknown) { helper(); } } }\n"
        "namespace A { export function run() { first(); } }\n"
        "namespace B { export function run() { second(); } }\n"
    )
    batch = extract_javascript(scanned("names.ts", source))
    functions = [n for n in batch.nodes if n.kind == "function"]
    assert not batch.diagnostics
    assert [n.qualified_name for n in functions] == [
        "names.A.B.run",
        "names.A.run",
        "names.B.run",
    ]
    assert [(r.name, r.source_id) for r in batch.references] == [
        ("helper", functions[0].id),
        ("first", functions[1].id),
        ("second", functions[2].id),
    ]
    shifted = extract_javascript(scanned("names.ts", "\n" + source))
    assert [n.id for n in batch.nodes] == [n.id for n in shifted.nodes]
    StructuralLayer(nodes=batch.nodes, edges=batch.edges)
