from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from rootweft.extract.markdown import extract_markdown
from rootweft.models import StructuralLayer
from rootweft.scanner import ScannedFile


def scanned(text: str, path: str = "README.md") -> ScannedFile:
    return ScannedFile(path, "markdown", text, sha256(text.encode()).hexdigest())


@pytest.mark.parametrize("suffix", [".md", ".mdx"])
def test_headings_nest_by_level_with_closing_hashes_and_skipped_levels(
    suffix: str,
) -> None:
    """Catches incorrect ATX limits, closing-hash parsing, and containment parents."""
    source = (Path(__file__).parent / "fixtures/web/README.md").read_text(
        encoding="utf-8"
    )
    batch = extract_markdown(scanned(source, "README" + suffix), frozenset())
    headings = {n.name: n for n in batch.nodes if n.kind == "heading"}
    assert set(headings) == {
        "Root",
        "Child",
        "Deep",
        "Four",
        "Five",
        "Six",
        "Sibling",
        "Other",
    }
    by_id = {n.id: n.name for n in batch.nodes}
    assert {(by_id[e.source], by_id[e.target]) for e in batch.edges} == {
        ("README" + suffix, "Root"),
        ("Root", "Child"),
        ("Child", "Deep"),
        ("Deep", "Four"),
        ("Four", "Five"),
        ("Five", "Six"),
        ("Root", "Sibling"),
        ("README" + suffix, "Other"),
    }
    assert headings["Child"].evidence.start_line == 2
    assert {e.origin for e in batch.edges} == {"parser"}
    assert {e.relation for e in batch.edges} == {"contains"}


def test_mentions_match_each_exact_occurrence_once_longest_first() -> None:
    """Catches substring matches, qualified-name overlap, and mention deduplication."""
    batch = extract_markdown(
        scanned(
            "# API\nFoo.run Foo Foo.run Foo.\n"
            "Food preFoo Foo_bar Foo$extra Foo.runExtra x.Foo\n"
            "(Foo.run), $root $rooted pkg.Foo\n"
        ),
        frozenset({"Foo", "Foo.run", "$root", "pkg.Foo"}),
    )
    assert [(r.name, r.evidence.start_line) for r in batch.references] == [
        ("Foo.run", 2),
        ("Foo", 2),
        ("Foo.run", 2),
        ("Foo", 2),
        ("Foo.run", 4),
        ("$root", 4),
        ("pkg.Foo", 4),
    ]
    heading = next(n for n in batch.nodes if n.kind == "heading")
    assert {r.source_id for r in batch.references} == {heading.id}
    assert {r.relation for r in batch.references} == {"mentions"}
    assert not any(r.dynamic for r in batch.references)


@pytest.mark.parametrize("fence", ["```", "````", "~~~", "~~~~~"])
def test_fences_ignore_headings_mentions_short_and_mismatched_closers(
    fence: str,
) -> None:
    """Catches premature fence closing or code content leaking into graph structure."""
    other = "~~~" if fence[0] == "`" else "```"
    source = (
        f"# Public\n{fence}lang\n# Hidden\nFoo\n{fence[:2]}\nFoo\n"
        f"{other}\nFoo\n{fence}\nFoo\n"
    )
    batch = extract_markdown(scanned(source), frozenset({"Foo"}))
    assert [n.name for n in batch.nodes if n.kind == "heading"] == ["Public"]
    assert [(r.name, r.evidence.start_line) for r in batch.references] == [("Foo", 10)]


def test_indented_longer_closing_fence_and_unclosed_fence() -> None:
    """Catches indentation/length handling and leaking an unterminated code block."""
    batch = extract_markdown(
        scanned("   ~~~ text\nFoo\n   ~~~~~\nFoo\n```\nFoo\n"), frozenset({"Foo"})
    )
    assert [(r.name, r.evidence.start_line) for r in batch.references] == [("Foo", 4)]


def test_inline_code_uses_matching_backtick_runs_across_lines() -> None:
    """Catches mentions/ATX headings leaking from inline code and unequal delimiters."""
    batch = extract_markdown(
        scanned(
            "Foo `Foo` ``Foo ` Foo`` Foo\n`start\n# Foo\nFoo` Foo\nunclosed ` Foo\n"
        ),
        frozenset({"Foo"}),
    )
    assert [(r.name, r.evidence.start_line) for r in batch.references] == [
        ("Foo", 1),
        ("Foo", 1),
        ("Foo", 4),
        ("Foo", 5),
    ]
    assert [n.kind for n in batch.nodes] == ["file"]


def test_repeated_heading_ids_are_unique_stable_after_line_shifts() -> None:
    """Catches duplicate heading graph IDs or identities tied to evidence lines."""
    source = "# API\n## Usage\nFoo\n## Usage\nFoo\n# API\n"
    original = extract_markdown(scanned(source), frozenset({"Foo"}))
    shifted = extract_markdown(scanned("\n\n" + source), frozenset({"Foo"}))
    assert [n.id for n in original.nodes] == [n.id for n in shifted.nodes]
    assert [r.evidence.start_line for r in original.references] == [3, 5]
    assert [r.evidence.start_line for r in shifted.references] == [5, 7]
    assert len({r.source_id for r in original.references}) == 2
    StructuralLayer(nodes=original.nodes, edges=original.edges)
    assert extract_markdown(scanned(source), frozenset({"Foo"})) == original


def test_empty_symbol_name_does_not_create_zero_length_mentions() -> None:
    """Catches empty lookup entries matching every position in a document."""
    batch = extract_markdown(scanned("ordinary text"), frozenset({""}))
    assert not batch.references


def test_empty_atx_headings_and_content_hashes_follow_closing_hash_rules() -> None:
    """Catches closing hashes becoming heading text or content hashes being stripped."""
    batch = extract_markdown(
        scanned("# ###\n##\n### C#\n#### Title ### extra\n"), frozenset()
    )
    assert [n.name for n in batch.nodes if n.kind == "heading"] == [
        "",
        "",
        "C#",
        "Title ### extra",
    ]


def test_escaped_backticks_are_prose_but_backslashes_inside_code_are_literal() -> None:
    """Catches escaped prose ticks masking mentions and code escapes leaking them."""
    batch = extract_markdown(
        scanned("\\`Foo\\` Foo\n`Foo\\` Foo\n"), frozenset({"Foo"})
    )
    assert [(r.name, r.evidence.start_line) for r in batch.references] == [
        ("Foo", 1),
        ("Foo", 1),
        ("Foo", 2),
    ]


def test_masking_inline_code_cannot_create_a_heading_opener() -> None:
    """Catches code masking turning ordinary prose into an ATX heading or crash."""
    batch = extract_markdown(
        scanned("`x`# Heading\n# Real\n`start\n# Hidden\nend`\n"),
        frozenset({"Heading"}),
    )
    assert [n.name for n in batch.nodes if n.kind == "heading"] == ["Real"]
    assert [(r.name, r.evidence.start_line) for r in batch.references] == [
        ("Heading", 1)
    ]
    assert batch.references[0].source_id == batch.nodes[0].id
