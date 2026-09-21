from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

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
