from rootweft.extract.base import Reference
from rootweft.models import Evidence, Node
from rootweft.resolver import resolve_references


def node(path: str, kind: str, name: str, qualified: str) -> Node:
    return Node(f"{path}:{kind}:{name}", kind, name, qualified, "python", Evidence(path, 1, 1))


def test_exact_same_file_call_and_explicit_import_are_accepted() -> None:
    source = node("app.py", "module", "app", "app")
    helper = node("app.py", "function", "helper", "app.helper")
    imported = node("lib.py", "module", "lib", "lib")
    refs = [
        Reference(source.id, "helper", "calls", Evidence("app.py", 3, 3)),
        Reference(source.id, "lib", "imports", Evidence("app.py", 2, 2)),
    ]
    edges, candidates = resolve_references((source, helper, imported), refs)
    assert [(edge.relation, edge.target, edge.status) for edge in edges] == [
        ("calls", helper.id, "accepted"),
        ("imports", imported.id, "accepted"),
    ] or [(edge.relation, edge.target, edge.status) for edge in edges] == [
        ("imports", imported.id, "accepted"),
        ("calls", helper.id, "accepted"),
    ]
    assert candidates == ()


def test_ambiguous_cross_file_call_is_bounded_and_stable() -> None:
    source = node("app.py", "module", "app", "app")
    helpers = (node("a.py", "function", "helper", "a.helper"), node("b.py", "function", "helper", "b.helper"), node("c.py", "function", "helper", "c.helper"))
    ref = Reference(source.id, "helper", "calls", Evidence("app.py", 5, 5))
    edges, candidates = resolve_references((source, *helpers), (ref,), max_candidates=2)
    assert edges == ()
    assert len(candidates) == 1
    assert candidates[0].options == tuple(sorted((helpers[0].id, helpers[1].id)))


def test_dynamic_and_unbound_references_do_not_become_edges() -> None:
    source = node("app.py", "module", "app", "app")
    target = node("app.py", "function", "helper", "app.helper")
    refs = (
        Reference(source.id, "helper", "calls", Evidence("app.py", 2, 2), dynamic=True),
        Reference(source.id, "unknown", "calls", Evidence("app.py", 3, 3)),
    )
    assert resolve_references((source, target), refs) == ((), ())


def test_repeated_calls_have_distinct_edge_ids() -> None:
    source = node("app.py", "module", "app", "app")
    target = node("app.py", "function", "helper", "app.helper")
    refs = (Reference(source.id, "helper", "calls", Evidence("app.py", 2, 2)), Reference(source.id, "helper", "calls", Evidence("app.py", 3, 3)))
    edges, _ = resolve_references((source, target), refs)
    assert len({edge.id for edge in edges}) == 2
