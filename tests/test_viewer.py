import base64
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

import pytest

from rootweft.models import (
    SCHEMA_VERSION,
    AdjudicationLayer,
    Candidate,
    Edge,
    Evidence,
    GraphDocument,
    Node,
    StructuralLayer,
)


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.content = {}
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        self.current = attrs.get("id") or (tag if tag in {"script", "style"} else None)

    def handle_data(self, data):
        if self.current:
            self.content[self.current] = self.content.get(self.current, "") + data

    def handle_endtag(self, tag):
        self.current = None


def hostile_graph():
    names = json.loads(
        (Path(__file__).parent / "fixtures/security/hostile_names.json").read_text()
    )
    names.append("huge" * 20_000)
    evidence = Evidence("src/app.py", 2, 3)
    nodes = tuple(
        Node(str(i), "function", name, name, "python", evidence)
        for i, name in enumerate(names)
    )
    return GraphDocument(
        SCHEMA_VERSION,
        "extract.v1",
        "policy.v1",
        StructuralLayer(
            nodes, (Edge("edge", "0", "1", "calls", "static", "accepted", evidence),)
        ),
        AdjudicationLayer((Candidate("candidate", "0", "mentions", evidence, ("2",)),)),
    )


def render(document, output, **kwargs):
    from rootweft.viewer import render_viewer

    render_viewer(document, output, **kwargs)
    return output.read_text(encoding="utf-8")


def test_hostile_graph_is_encoded_and_csp_hashes_match_real_resources(tmp_path):
    graph = hostile_graph()
    html = render(graph, tmp_path / "viewer.html", max_visible=2)
    page = Page(html)
    decoded = json.loads(base64.b64decode(page.content["graph-data"]))
    assert (
        decoded["structural"]["nodes"][0]["name"]
        == "</script><script>globalThis.pwned=true</script>"
    )
    assert len(decoded["structural"]["nodes"][-1]["name"]) == 80000
    assert "<svg" not in html
    assert "javascript:" not in html
    assert "\x1b" not in html and "\u202e" not in html
    assert not any(tag in {"svg", "iframe", "img", "link"} for tag, _ in page.elements)
    assert not any("src" in attrs or "href" in attrs for _, attrs in page.elements)
    csp = next(
        attrs["content"]
        for tag, attrs in page.elements
        if attrs.get("http-equiv") == "Content-Security-Policy"
    )
    for kind in ("script", "style"):
        digest = base64.b64encode(
            hashlib.sha256(page.content[kind].encode()).digest()
        ).decode()
        assert f"{kind}-src 'sha256-{digest}'" in csp
    assert "default-src 'none'" in csp and "connect-src 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_viewer_atomic_failure_preserves_previous_artifact(tmp_path, monkeypatch):
    output = tmp_path / "viewer.html"
    output.write_bytes(b"previous viewer")

    def fail(*args):
        raise OSError("replace failed")

    monkeypatch.setattr("os.replace", fail)
    with pytest.raises(OSError):
        render(hostile_graph(), output)
    assert output.read_bytes() == b"previous viewer"
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("limit", [0, -1, True, 10001])
def test_invalid_viewer_budget_does_not_touch_existing_output(tmp_path, limit):
    output = tmp_path / "viewer.html"
    output.write_bytes(b"previous viewer")
    with pytest.raises(ValueError):
        render(hostile_graph(), output, max_visible=limit)
    assert output.read_bytes() == b"previous viewer"


@pytest.mark.skipif(
    not os.environ.get("ROOTWEFT_BROWSER_EXECUTABLE") or not shutil.which("node"),
    reason="set ROOTWEFT_BROWSER_EXECUTABLE and NODE_PATH to run browser checks",
)
def test_browser_filters_caps_hostile_text_and_keyboard_expansion(tmp_path):
    """Catch broken CSP, unsafe DOM, uncapped lists, and lost keyboard focus."""
    output = tmp_path / "viewer.html"
    render(hostile_graph(), output, max_visible=2)
    script = r"""
const {chromium} = require("playwright");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch({
    executablePath:process.env.ROOTWEFT_BROWSER_EXECUTABLE, headless:true
  });
  try {
    const page = await browser.newPage();
    const errors = [], requests = [];
    page.on("pageerror", error => errors.push(error.message));
    page.on("request", request => {
      if (!request.url().startsWith("file:")) requests.push(request.url());
    });
    await page.goto(process.argv[2]);
    assert.equal(await page.locator("#results button").count(), 2);
    assert.match(await page.locator("#results-status").textContent(), /Showing 2 of 7/);
    assert.equal(await page.evaluate(() => globalThis.pwned), undefined);
    assert.equal(await page.locator("svg, iframe, img").count(), 0);
    await page.locator("#results button").first().click();
    assert.match(await page.locator("#detail").textContent(), /src\/app.py:2/);
    await page.locator("#relation").selectOption("calls");
    assert.match(await page.locator("#results-status").textContent(), /Showing 2 of 2/);
    await page.locator("#layer").selectOption("review");
    assert.equal(await page.locator("#results button").count(), 0);
    assert.match(await page.locator("#results-status").textContent(), /broaden/);
    await page.locator("#relation").selectOption("mentions");
    assert.equal(await page.locator("#results button").count(), 2);
    await page.locator("#results button").first().click();
    const link = page.locator(".relation-link").first();
    await link.focus(); await page.keyboard.press("Enter");
    assert.equal(await page.evaluate(() => document.activeElement.id), "detail-name");
    await page.locator("#reset").click();
    assert.equal(await page.evaluate(() => document.activeElement.id), "search");
    await page.locator("#search").fill("huge");
    assert.equal(await page.locator("#results button").count(), 1);
    assert.ok((await page.locator("#results .name").textContent()).length < 300);
    await page.locator("#search").fill("red");
    assert.equal(await page.locator("#results .name").textContent(), "red");
    await page.locator("#search").fill("safe");
    assert.equal(await page.locator("#results .name").textContent(), "safeevillabel");
    await page.setViewportSize({width:375,height:812});
    assert.equal(await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth
    ), true);
    assert.deepEqual(errors, []); assert.deepEqual(requests, []);
    console.log("browser behavior verified");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(
        ["node", "-", output.as_uri()],
        input=script,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def dense_graph(candidate_count=600, option_count=16):
    evidence = Evidence("src/app.py", 1, 1)
    nodes = tuple(
        Node(str(i), "function", f"symbol{i}", None, "python", evidence)
        for i in range(option_count + 1)
    )
    candidates = tuple(
        Candidate(
            f"candidate{i}",
            "0",
            "calls",
            evidence,
            tuple(str(j) for j in range(1, option_count + 1)),
        )
        for i in range(candidate_count)
    )
    return GraphDocument(
        SCHEMA_VERSION,
        "v1",
        "v1",
        StructuralLayer(nodes),
        AdjudicationLayer(candidates),
    )


@pytest.mark.parametrize("kind", ["options", "label", "records"])
def test_direct_viewer_rejects_excessive_document_before_output(tmp_path, kind):
    graph = dense_graph(1, 1)
    if kind == "options":
        graph = dense_graph(1, 10000)
    elif kind == "label":
        graph = replace(
            graph,
            structural=StructuralLayer(
                (replace(graph.structural.nodes[0], name="x" * 1_000_000),)
            ),
        )
    else:
        graph = dense_graph(20001, 1)
    output = tmp_path / "viewer.html"
    output.write_bytes(b"keep viewer")
    with pytest.raises(ValueError):
        render(graph, output)
    assert output.read_bytes() == b"keep viewer"
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.skipif(
    not os.environ.get("ROOTWEFT_BROWSER_EXECUTABLE") or not shutil.which("node"),
    reason="set ROOTWEFT_BROWSER_EXECUTABLE and NODE_PATH to run browser checks",
)
def test_browser_candidate_expansion_has_aggregate_budget(tmp_path):
    output = tmp_path / "viewer.html"
    render(dense_graph(), output, max_visible=500)
    script = r"""
const {chromium} = require("playwright");
const assert = require("node:assert/strict");
(async () => {
  const browser = await chromium.launch({
    executablePath:process.env.ROOTWEFT_BROWSER_EXECUTABLE, headless:true
  });
  try {
    const page = await browser.newPage();
    await page.goto(process.argv[2]);
    await page.locator("#results button").first().click();
    const rows = await page.locator(".relations li").count();
    const targets = await page.locator(".relation-link").count();
    assert.ok(rows + targets <= 500, `${rows} rows plus ${targets} targets`);
    assert.match(await page.locator("#detail").textContent(), /limited|budget/i);
    assert.ok(await page.locator("#detail *").count() < 2500);
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exit(1);});
"""
    result = subprocess.run(
        ["node", "-", output.as_uri()],
        input=script,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
