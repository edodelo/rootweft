"""ATX heading hierarchy and exact known-symbol mentions in Markdown and MDX.

MDX is treated as Markdown, without evaluating JSX. Fenced blocks and matched
inline code spans are masked while preserving line positions. This is a scoped
structural extractor rather than a complete CommonMark renderer.
"""

from __future__ import annotations

import re
from collections import defaultdict

from rootweft.extract.base import ExtractionBatch, Reference
from rootweft.ids import evidence_fingerprint, stable_id
from rootweft.models import Edge, Evidence, Node
from rootweft.scanner import ScannedFile

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$")
_TICKS = re.compile(r"`+")


def extract_markdown(
    file: ScannedFile, known_symbols: frozenset[str]
) -> ExtractionBatch:
    """Return deterministic headings and one reference for each exact mention."""
    file_node = Node(
        id=stable_id("markdown", file.path, "file", file.path),
        kind="file",
        name=file.path,
        qualified_name=file.path,
        language="markdown",
        evidence=Evidence(file.path, 1, max(1, len(file.text.splitlines()))),
        metadata={"evidence_fingerprint": evidence_fingerprint(file.text)},
    )
    nodes = [file_node]
    edges: list[Edge] = []
    references: list[Reference] = []
    parents = [(0, file_node)]
    occurrences: dict[tuple[str, str], int] = defaultdict(int)
    symbols = sorted(
        (name for name in known_symbols if name), key=lambda name: (-len(name), name)
    )
    pattern = (
        re.compile(
            r"(?<![\w$.])(?:"
            + "|".join(re.escape(s) for s in symbols)
            + r")(?![\w$]|\.[\w$])"
        )
        if symbols
        else None
    )
    visible = _mask_inline(_mask_fences(file.text))
    original_lines = file.text.splitlines()
    for line_number, line in enumerate(visible.splitlines(), start=1):
        heading = _HEADING.match(line)
        original = _HEADING.match(original_lines[line_number - 1])
        if (
            heading is not None
            and original is not None
            and heading.span(1) == original.span(1)
        ):
            level = len(heading[1])
            while parents[-1][0] >= level:
                parents.pop()
            parent = parents[-1][1]
            name = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", original[2] or "").strip()
            key = parent.id, name
            occurrences[key] += 1
            ordinal = occurrences[key]
            qualified = f"{parent.qualified_name}/{name}"
            evidence = Evidence(file.path, line_number, line_number)
            node = Node(
                id=stable_id(
                    "markdown", file.path, "heading", parent.id, name, str(ordinal)
                ),
                kind="heading",
                name=name,
                qualified_name=qualified,
                language="markdown",
                evidence=evidence,
                metadata={
                    "level": level,
                    "evidence_fingerprint": evidence_fingerprint(
                        original_lines[line_number - 1]
                    ),
                },
            )
            nodes.append(node)
            edges.append(
                Edge(
                    id=stable_id("markdown", "contains", parent.id, node.id),
                    source=parent.id,
                    target=node.id,
                    relation="contains",
                    origin="parser",
                    status="accepted",
                    evidence=evidence,
                )
            )
            parents.append((level, node))
        if pattern is not None:
            for mention in pattern.finditer(line):
                references.append(
                    Reference(
                        source_id=parents[-1][1].id,
                        name=mention[0],
                        relation="mentions",
                        evidence=Evidence(file.path, line_number, line_number),
                    )
                )
    return ExtractionBatch(
        nodes=tuple(nodes), edges=tuple(edges), references=tuple(references)
    )


def _blank(text: str) -> str:
    return "".join(char if char in "\r\n" else " " for char in text)


def _mask_fences(text: str) -> str:
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        match = _FENCE.match(line.rstrip("\r\n"))
        if fence is not None:
            lines.append(_blank(line))
            if (
                match
                and match[1][0] == fence[0]
                and len(match[1]) >= len(fence)
                and not match[2].strip()
            ):
                fence = None
        elif match and not (match[1][0] == "`" and "`" in match[2]):
            fence = match[1]
            lines.append(_blank(line))
        else:
            lines.append(line)
    return "".join(lines)


def _mask_inline(text: str) -> str:
    runs = list(_TICKS.finditer(text))
    result = list(text)
    index = 0
    while index < len(runs):
        opener = runs[index]
        before = opener.start() - 1
        while before >= 0 and text[before] == "\\":
            before -= 1
        if (opener.start() - 1 - before) % 2:
            index += 1
            continue
        closing = next(
            (
                i
                for i in range(index + 1, len(runs))
                if len(runs[i][0]) == len(opener[0])
            ),
            None,
        )
        if closing is None:
            index += 1
            continue
        end = runs[closing].end()
        result[opener.start() : end] = _blank(text[opener.start() : end])
        index = closing + 1
    return "".join(result)
