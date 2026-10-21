"""Laravel view modelling: Blade edges, PHP -> view ``renders``, Livewire.

The fixture project is tests/fixtures/laravel_views_app. Every expected edge below is
one form of the feature; the negative cases (dynamic names, ambiguous registrations,
a missing view, a theme namespace nobody declared) are as much part of the contract:
no edge beats a wrong edge.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_php")

from graphify.extract import collect_files, extract  # noqa: E402
from graphify.extractors.laravel.blade import literal_string, parse_actions, scan_blade, split_args  # noqa: E402
from graphify.extractors.laravel.views import (  # noqa: E402
    Registrations,
    ViewHelper,
    ViewResolver,
    collect_registrations,
)
from graphify.extractors.laravel.viewconfig import LaravelViewOptions, load_view_options  # noqa: E402
from graphify.extractors.laravel.phpsrc import load_php_file  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
APP = FIXTURES / "laravel_views_app"

V = "resources/views"


def _extract(root: Path) -> dict:
    root = Path(root).resolve()
    return extract(collect_files(root), root=root, parallel=False)


@pytest.fixture(scope="module")
def graph() -> dict:
    return _extract(APP)


def _rows(result: dict, relations: set[str], *, context: str | None = None, source_ext: str | None = None):
    """``(source file, source label, relation, target file, target label, confidence)`` rows."""
    nodes = {n["id"]: n for n in result["nodes"]}
    out = []
    for e in result["edges"]:
        if e["relation"] not in relations:
            continue
        if context is not None and e.get("context") != context:
            continue
        s, t = nodes[e["source"]], nodes[e["target"]]
        if source_ext and not s["source_file"].endswith(source_ext):
            continue
        out.append((s["source_file"], s["label"], e["relation"], t["source_file"], t["label"], e["confidence"]))
    return sorted(out)


# ── PHP -> view ────────────────────────────────────────────────────────────

PHP_RENDERS = sorted([
    ("app/Http/Controllers/ReportController.php", ".show()", "renders", f"{V}/reports/show.blade.php", "show.blade.php", "EXTRACTED"),
    ("app/Http/Controllers/ReportController.php", ".viaFacade()", "renders", f"{V}/reports/partials/summary.blade.php", "summary.blade.php", "EXTRACTED"),
    ("app/Http/Controllers/ReportController.php", ".viaResponse()", "renders", f"{V}/reports/partials/chart.blade.php", "chart.blade.php", "EXTRACTED"),
    ("app/Http/Controllers/ReportController.php", ".namespaced()", "renders", "modules/Blog/Resources/views/index.blade.php", "index.blade.php", "EXTRACTED"),
    ("app/Http/Controllers/ReportController.php", ".pdf()", "renders", f"{V}/reports/pdf.blade.php", "pdf.blade.php", "EXTRACTED"),
    ("app/Http/Controllers/ReportController.php", ".viaFactory()", "renders", f"{V}/reports/partials/chart.blade.php", "chart.blade.php", "EXTRACTED"),
    ("app/Http/Controllers/ReportController.php", "{closure#1}()", "renders", f"{V}/reports/partials/chart.blade.php", "chart.blade.php", "EXTRACTED"),
    ("app/Mail/WelcomeMail.php", ".content()", "renders", f"{V}/emails/welcome.blade.php", "welcome.blade.php", "EXTRACTED"),
    ("app/Mail/WelcomeMail.php", ".content()", "renders", f"{V}/emails/digest.blade.php", "digest.blade.php", "EXTRACTED"),
    ("app/Mail/LegacyMail.php", ".build()", "renders", f"{V}/emails/welcome.blade.php", "welcome.blade.php", "EXTRACTED"),
    ("app/Notifications/InvoiceNotification.php", ".toMail()", "renders", f"{V}/emails/digest.blade.php", "digest.blade.php", "EXTRACTED"),
    ("app/View/Components/Alert.php", ".render()", "renders", f"{V}/components/alert.blade.php", "alert.blade.php", "EXTRACTED"),
    ("app/View/Components/Forms/Input.php", ".render()", "renders", f"{V}/components/forms/input.blade.php", "input.blade.php", "EXTRACTED"),
    ("app/Livewire/Counter.php", ".render()", "renders", f"{V}/livewire/counter.blade.php", "counter.blade.php", "EXTRACTED"),
    ("app/Livewire/Counter.php", ".render()", "renders", f"{V}/layouts/app.blade.php", "app.blade.php", "EXTRACTED"),
    ("app/Livewire/TwinA.php", ".render()", "renders", f"{V}/livewire/twin.blade.php", "twin.blade.php", "EXTRACTED"),
    ("app/Livewire/TwinB.php", ".render()", "renders", f"{V}/livewire/twin.blade.php", "twin.blade.php", "EXTRACTED"),
    ("routes/web.php", "GET /welcome", "renders", f"{V}/welcome.blade.php", "welcome.blade.php", "EXTRACTED"),
    ("routes/web.php", "GET /closure()", "renders", f"{V}/reports/partials/chart.blade.php", "chart.blade.php", "EXTRACTED"),
])


def test_php_renders_edges_are_exactly_the_resolvable_literal_views(graph):
    assert _rows(graph, {"renders"}, context="view_call") + _rows(graph, {"renders"}, context="route_view") == PHP_RENDERS


def test_dynamic_ambiguous_missing_and_undeclared_theme_names_get_no_edge(graph):
    sources = {r[1] for r in _rows(graph, {"renders"}, source_ext=".php")}
    # missing(), dynamic(), ambiguous() (two registrations / a non-literal one) and themed()
    for method in (".missing()", ".dynamic()", ".ambiguous()", ".themed()"):
        assert method not in sources, method
    assert "GET /ghost" not in sources  # Route::view to a view that does not exist
    assert ".render()" in sources       # sanity: render() methods do get edges


def test_view_edges_carry_the_form_and_location(graph):
    nodes = {n["id"]: n for n in graph["nodes"]}
    by = {
        (nodes[e["source"]]["label"], nodes[e["source"]]["source_file"]): e
        for e in graph["edges"] if e["relation"] == "renders" and e.get("context") == "view_call"
    }
    show = by[(".show()", "app/Http/Controllers/ReportController.php")]
    assert show["form"] == "view()" and show["view"] == "reports.show"
    assert show["source_file"] == "app/Http/Controllers/ReportController.php"
    assert show["source_location"] == "L12"
    assert by[(".pdf()", "app/Http/Controllers/ReportController.php")]["form"] == "loadView"
    assert by[(".viaFacade()", "app/Http/Controllers/ReportController.php")]["form"] == "View::make"
    assert by[(".content()", "app/Mail/WelcomeMail.php")]["form"] == "Content"


# ── blade -> blade ─────────────────────────────────────────────────────────

BLADE_SHOW = "resources/views/reports/show.blade.php"
BLADE_TO_BLADE = sorted([
    (BLADE_SHOW, "show.blade.php", "extends", f"{V}/layouts/app.blade.php", "app.blade.php", "EXTRACTED"),
    (BLADE_SHOW, "show.blade.php", "includes", f"{V}/reports/partials/summary.blade.php", "summary.blade.php", "EXTRACTED"),
    (BLADE_SHOW, "show.blade.php", "includes", f"{V}/reports/partials/chart.blade.php", "chart.blade.php", "EXTRACTED"),
    (BLADE_SHOW, "show.blade.php", "includes", "modules/Blog/Resources/views/index.blade.php", "index.blade.php", "EXTRACTED"),
    (BLADE_SHOW, "show.blade.php", "uses_component", f"{V}/components/alert.blade.php", "alert.blade.php", "EXTRACTED"),
])


def test_blade_to_blade_edges(graph):
    got = [
        r for r in _rows(graph, {"extends", "includes", "uses_component"}, source_ext=".blade.php")
        if r[3].endswith(".blade.php") and r[0] == BLADE_SHOW and r[2] != "uses_component"
        or (r[0] == BLADE_SHOW and r[4] == "alert.blade.php")
    ]
    assert got == BLADE_TO_BLADE


def test_blade_include_forms_all_resolve_and_dynamic_ones_do_not(graph):
    nodes = {n["id"]: n for n in graph["nodes"]}
    lines = {
        (nodes[e["target"]]["source_file"], e["context"]): e["source_location"]
        for e in graph["edges"] if e["source"] == _file_id(graph, BLADE_SHOW) and e["relation"] in {"extends", "includes"}
    }
    # @include, @includeIf, @includeWhen, @includeUnless, @includeFirst, @each: summary L9 (first one wins)
    assert lines[(f"{V}/reports/partials/summary.blade.php", "blade_include")] == "L9"
    assert lines[(f"{V}/layouts/app.blade.php", "blade_extends")] == "L1"
    # the commented, verbatim, escaped (@@), dynamic, variable, missing and theme-namespaced
    # references never produced an edge
    targets = {t for t, _c in lines}
    assert not any("commented" in t or "verbatim" in t or "escaped" in t or "missing" in t for t in targets)
    assert not any("auth/login" in t for t in targets)


def _file_id(graph: dict, rel: str) -> str:
    return next(
        n["id"] for n in graph["nodes"]
        if n["source_file"] == rel and n["label"] == Path(rel).name
    )


def test_include_first_uses_only_a_certain_first_choice(graph):
    # @includeFirst(['nope', 'summary']): runtime picks the first view that exists; only a
    # resolvable first element is a fact. Summary is also reached by plain @include, so
    # the evidence is the include count: exactly one edge per (file, target) pair.
    edges = [
        e for e in graph["edges"]
        if e["source"] == _file_id(graph, BLADE_SHOW) and e["relation"] == "includes"
    ]
    assert len({e["target"] for e in edges}) == len(edges)


# ── <x-...> components ─────────────────────────────────────────────────────

X_COMPONENTS = sorted([
    ("button", f"{V}/components/button.blade.php", "button.blade.php"),
    ("forms.field (index.blade.php)", f"{V}/components/forms/field/index.blade.php", "index.blade.php"),
    ("forms.input (class)", "app/View/Components/Forms/Input.php", "Input"),
    ("alert / pkg-alert (class)", "app/View/Components/Alert.php", "Alert"),
    ("panel (panel/panel.blade.php)", f"{V}/components/panel/panel.blade.php", "panel.blade.php"),
    ("ui::tile (anonymousComponentPath prefix)", f"{V}/ui/tile.blade.php", "tile.blade.php"),
    ("badge (anonymousComponentPath($var), once-assigned)", f"{V}/themed/badge.blade.php", "badge.blade.php"),
    ("blog::card (namespace components/)", "modules/Blog/Resources/views/components/card.blade.php", "card.blade.php"),
])


def test_x_components_resolve_to_class_or_anonymous_file(graph):
    got = sorted(
        (r[3], r[4]) for r in _rows(graph, {"uses_component"}, context="blade_x_component")
        if r[0] == BLADE_SHOW
    )
    assert got == sorted((t, label) for _n, t, label in X_COMPONENTS)


def test_class_component_wins_over_a_view_of_the_same_name(graph):
    # `components/alert.blade.php` exists as Alert's view and `<x-alert>` is a class component
    rows = _rows(graph, {"uses_component"}, context="blade_x_component")
    assert (BLADE_SHOW, "show.blade.php", "uses_component", "app/View/Components/Alert.php", "Alert", "EXTRACTED") in rows
    assert not any(r[3] == f"{V}/components/alert.blade.php" and r[0] == BLADE_SHOW for r in rows)


def test_unknown_slot_and_dynamic_components_get_no_edge(graph):
    labels = {r[4] for r in _rows(graph, {"uses_component"}, context="blade_x_component") if r[0] == BLADE_SHOW}
    assert not labels & {"missing-thing", "slot", "dynamic-component"}


# ── Livewire ───────────────────────────────────────────────────────────────

def test_livewire_tags_and_directives_point_at_the_component(graph):
    got = sorted(
        (r[3], r[4]) for r in _rows(graph, {"uses_component"}, context="blade_livewire") if r[0] == BLADE_SHOW
    )
    assert got == sorted([
        ("app/Livewire/Admin/UserTable.php", "UserTable"),
        ("app/Livewire/Child.php", "Child"),
        ("app/Livewire/Counter.php", "Counter"),           # <livewire:counter>, @livewire('counter'), legacy-counter alias
        (f"{V}/components/⚡inline-note.blade.php", "inline-note"),
    ])


LIVEWIRE_VIEW = sorted([
    ("app/Livewire/Admin/UserTable.php", "UserTable", "renders", f"{V}/livewire/admin/user-table.blade.php", "user-table.blade.php", "INFERRED"),
    ("app/Livewire/Child.php", "Child", "renders", f"{V}/livewire/child.blade.php", "child.blade.php", "INFERRED"),
    ("app/Livewire/Counter.php", "Counter", "renders", f"{V}/livewire/counter.blade.php", "counter.blade.php", "EXTRACTED"),
    ("app/Livewire/TwinA.php", "TwinA", "renders", f"{V}/livewire/twin.blade.php", "twin.blade.php", "EXTRACTED"),
    ("app/Livewire/TwinB.php", "TwinB", "renders", f"{V}/livewire/twin.blade.php", "twin.blade.php", "EXTRACTED"),
])


def test_livewire_class_to_view_explicit_render_and_convention(graph):
    assert _rows(graph, {"renders"}, context="livewire_view") == LIVEWIRE_VIEW


def test_livewire_with_dynamic_render_has_no_view(graph):
    classes = {r[1] for r in _rows(graph, {"renders"}, context="livewire_view")}
    assert "Dyn" not in classes


WIRE = sorted([
    ("resources/views/livewire/counter.blade.php", "counter.blade.php", "binds_method", "app/Livewire/Counter.php", ".increment()", "EXTRACTED"),
    ("resources/views/livewire/counter.blade.php", "counter.blade.php", "binds_method", "app/Livewire/Counter.php", ".add()", "EXTRACTED"),
    ("resources/views/livewire/counter.blade.php", "counter.blade.php", "binds_method", "app/Livewire/Counter.php", ".save()", "EXTRACTED"),
    ("resources/views/livewire/admin/user-table.blade.php", "user-table.blade.php", "binds_method", "app/Livewire/Admin/UserTable.php", ".delete()", "INFERRED"),
    ("resources/views/livewire/child.blade.php", "child.blade.php", "binds_method", "app/Livewire/Child.php", ".close()", "INFERRED"),
    ("resources/views/livewire/child.blade.php", "child.blade.php", "binds_method", "app/Livewire/Base.php", ".refreshAll()", "INFERRED"),
    (f"{V}/components/⚡inline-note.blade.php", "⚡inline-note.blade.php", "binds_method", f"{V}/components/⚡inline-note.blade.php", ".store()", "EXTRACTED"),
    (f"{V}/components/⚡inline-note.blade.php", "⚡inline-note.blade.php", "binds_method", f"{V}/components/⚡inline-note.blade.php", ".clear()", "EXTRACTED"),
])


def test_wire_bindings_resolve_to_component_methods(graph):
    # wire:click="increment", "add({{ $n }})", "add(5)", $wire.save() and an <x-button wire:click>
    # all bind; `$refresh`, `$set(...)`, `{{ $dynamic }}`, an unknown method and a view
    # two components render (twin.blade.php) bind nothing.
    assert _rows(graph, {"binds_method"}) == WIRE


def test_wire_model_properties_are_metadata_on_the_owned_view(graph):
    nodes = {(n["source_file"], n["label"]): n for n in graph["nodes"]}
    counter = nodes[("resources/views/livewire/counter.blade.php", "counter.blade.php")]
    assert counter["metadata"]["livewire"] == {"component": "App\\Livewire\\Counter", "models": ["count", "form.title"]}
    twin = nodes[("resources/views/livewire/twin.blade.php", "twin.blade.php")]
    assert "livewire" not in (twin.get("metadata") or {})


def test_single_file_component_is_a_node_with_methods(graph):
    nodes = {n["id"]: n for n in graph["nodes"]}
    sfc = [n for n in graph["nodes"] if (n.get("metadata") or {}).get("kind") == "livewire_component"]
    assert len(sfc) == 1
    comp = sfc[0]
    assert comp["label"] == "inline-note" and comp["metadata"]["single_file"] is True
    methods = sorted(
        nodes[e["target"]]["label"] for e in graph["edges"] if e["source"] == comp["id"] and e["relation"] == "method"
    )
    assert methods == [".clear()", ".store()"]
    parent = [e for e in graph["edges"] if e["target"] == comp["id"] and e["relation"] == "contains"]
    assert len(parent) == 1 and nodes[parent[0]["source"]]["source_file"] == comp["source_file"]


def test_blade_sections_yields_and_stacks_are_metadata(graph):
    nodes = {(n["source_file"], n["label"]): n for n in graph["nodes"]}
    layout = nodes[(f"{V}/layouts/app.blade.php", "app.blade.php")]["metadata"]["blade"]
    assert layout == {"yields": ["content", "sidebar"], "stacks": ["scripts"]}
    show = nodes[(BLADE_SHOW, "show.blade.php")]["metadata"]["blade"]
    assert show == {"sections": ["content", "sidebar"], "pushes": ["scripts"]}


# ── graph hygiene ──────────────────────────────────────────────────────────

def test_no_blade_stub_nodes_or_dangling_blade_edges_remain(graph):
    ids = {n["id"] for n in graph["nodes"]}
    for n in graph["nodes"]:
        if n["source_file"].endswith(".blade.php") and n["label"] != Path(n["source_file"]).name:
            assert n.get("source_location"), f"stub-looking node {n['label']!r}"
        assert "_drop" not in n
    for e in graph["edges"]:
        assert "_drop" not in e
        if e["source_file"].endswith(".blade.php") and e["relation"] != "contains":
            assert e["target"] in ids, e
            assert e["source_location"], e


def test_extraction_is_deterministic():
    first = json.dumps(_extract(APP), sort_keys=True, default=str)
    second = json.dumps(_extract(APP), sort_keys=True, default=str)
    assert first == second


def _incremental(graph: dict, rel: str) -> dict:
    """Re-extract only ``rel`` with the rest of the corpus as read-only context (as a watch rebuild)."""
    markers = ("_callable", "_callable_class")
    ctx_nodes = [
        {k: n.get(k) for k in ("id", "label", "source_file", "file_type", "type")}
        | {m: n[m] for m in markers if n.get(m)}
        for n in graph["nodes"] if n["source_file"] != rel and n.get("_origin") == "ast"
    ]
    ctx_edges = [
        {k: e.get(k) for k in ("source", "target", "relation", "source_file")}
        for e in graph["edges"]
        if e["relation"] in {"contains", "method", "inherits", "imports", "imports_from", "case_of"}
        and e["source_file"] != rel and e.get("_origin") == "ast"
    ]
    root = APP.resolve()
    return extract(
        [root / rel], root=root, parallel=False,
        resolution_context_nodes=ctx_nodes, resolution_context_edges=ctx_edges,
    )


def test_incremental_rebuild_of_a_blade_file_matches_the_full_build(graph):
    rel = "resources/views/reports/show.blade.php"
    fid = _file_id(graph, rel)
    keep = {"extends", "includes", "uses_component", "binds_method", "renders"}
    full = sorted(
        (e["relation"], e["target"], e["confidence"], e["source_location"])
        for e in graph["edges"] if e["source"] == fid and e["relation"] in keep
    )
    part = _incremental(graph, rel)
    got = sorted(
        (e["relation"], e["target"], e["confidence"], e["source_location"])
        for e in part["edges"] if e["source"] == fid and e["relation"] in keep
    )
    assert got == full and got
    assert all(n["label"] == Path(n["source_file"]).name for n in part["nodes"])  # no stub nodes


def test_incremental_rebuild_of_a_livewire_view_still_binds_to_the_unchanged_class(graph):
    rel = "resources/views/livewire/counter.blade.php"
    fid = _file_id(graph, rel)
    full = sorted(e["target"] for e in graph["edges"] if e["source"] == fid and e["relation"] == "binds_method")
    part = _incremental(graph, rel)
    got = sorted(e["target"] for e in part["edges"] if e["source"] == fid and e["relation"] == "binds_method")
    assert got == full and len(got) == 3


# ── .graphifyrc: runtime-decided view locations ────────────────────────────

def test_graphifyrc_view_namespace_resolves_theme_helper_calls(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(APP, root)
    (root / ".graphifyrc").write_text(
        "laravel.view_namespace.theme-guest="
        "resources/themes/guest/default/resources/views,resources/themes/guest/luma/resources/views\n",
        encoding="utf-8",
    )
    result = _extract(root)
    login = "resources/themes/guest/default/resources/views/auth/login.blade.php"
    nodes = {n["id"]: n for n in result["nodes"]}
    themed = [
        e for e in result["edges"]
        if e["relation"] in {"renders", "includes"} and nodes[e["target"]]["source_file"] == login
    ]
    sources = sorted((nodes[e["source"]]["label"], e["confidence"], e["via"]) for e in themed)
    # theme_view('auth.login', 'guest') -> theme-guest::auth.login: the project's own mapping,
    # first listed directory wins, and the edge says so.
    assert sources == [(".themed()", "INFERRED", "graphifyrc"), ("show.blade.php", "INFERRED", "graphifyrc")]
    assert not any("luma" in nodes[e["target"]]["source_file"] for e in result["edges"] if e["relation"] == "renders")


def test_graphifyrc_options_parse(tmp_path):
    (tmp_path / ".graphifyrc").write_text(
        "viz_node_limit=5\n# comment\n"
        "laravel.view_namespace.theme-app=resources/themes/app/default/resources/views\n"
        "laravel.component_path=resources/themes/app/default/resources/views/components\n"
        "laravel.component_path.layout=resources/x/layout, ../escape, /abs\n",
        encoding="utf-8",
    )
    opts = load_view_options(tmp_path)
    assert opts.view_namespaces == {"theme-app": ["resources/themes/app/default/resources/views"]}
    assert opts.component_paths == [
        ("resources/themes/app/default/resources/views/components", None),
        ("resources/x/layout", "layout"),
    ]
    assert load_view_options(tmp_path / "missing").view_namespaces == {}


# ── compatibility: not a Laravel project ───────────────────────────────────

def test_blade_outside_a_laravel_project_keeps_the_legacy_extraction(tmp_path):
    page = tmp_path / "page.blade.php"
    page.write_text("@extends('layouts.app')\n@include('partials.nav')\n<livewire:foo />\n", encoding="utf-8")
    (tmp_path / "composer.json").write_text('{"require": {"slim/slim": "^4"}}', encoding="utf-8")
    result = extract([page], root=tmp_path, cache_root=tmp_path)
    labels = {n["id"]: n["label"] for n in result["nodes"]}
    rel = {(labels[e["source"]], e["relation"], labels[e["target"]]) for e in result["edges"]}
    assert ("page.blade.php", "extends", "layouts.app") in rel
    assert ("page.blade.php", "includes", "partials.nav") in rel
    assert ("page.blade.php", "uses_component", "foo") in rel


def test_laravel_project_drops_the_legacy_stubs_it_replaces(tmp_path):
    (tmp_path / "artisan").write_text("<?php\n", encoding="utf-8")
    views = tmp_path / "resources" / "views"
    views.mkdir(parents=True)
    (views / "page.blade.php").write_text("@extends('layouts.app')\n@include('partials.nav')\n", encoding="utf-8")
    (views / "partials").mkdir()
    (views / "partials" / "nav.blade.php").write_text("nav\n", encoding="utf-8")
    result = extract(collect_files(tmp_path), root=tmp_path, parallel=False)
    labels = {n["id"]: n["label"] for n in result["nodes"]}
    rel = {(labels[e["source"]], e["relation"], labels[e["target"]]) for e in result["edges"] if e["relation"] != "contains"}
    # the unresolvable layout leaves no edge and no fake node; the real include is linked
    assert rel == {("page.blade.php", "includes", "nav.blade.php")}
    assert "layouts.app" not in labels.values() and "partials.nav" not in labels.values()


# ── units ──────────────────────────────────────────────────────────────────

def test_scan_blade_masks_comments_verbatim_php_and_escapes():
    scan = scan_blade(
        "{{-- @include('a') --}}\n@verbatim @include('b') <x-c /> @endverbatim\n"
        "@php\n  $x = '@include(\"d\")';\n@endphp\n@@include('e')\n"
        "@include('real') <x-real.one /> <livewire:real-lw /> <x-slot:name /> <x-dynamic-component />\n"
        "<button wire:click=\"save({{ $id }})\" wire:model.live=\"form.name\" wire:loading.attr=\"disabled\">\n"
    )
    kinds = [(r.kind, r.name or r.exprs) for r in scan.refs]
    assert kinds == [
        ("include", ["'real'"]), ("x", "real.one"), ("livewire_tag", "real-lw"),
        ("wire_action", "save"), ("wire_model", "form.name"),
    ]
    assert [r.line for r in scan.refs] == [7, 7, 7, 8, 8]


def test_split_args_literals_and_actions():
    assert split_args("'a', ['b', 'c'], fn($x) => [1, 2]") == ["'a'", "['b', 'c']", "fn($x) => [1, 2]"]
    assert literal_string("'a.b'") == "a.b" and literal_string('"a.b"') == "a.b"
    assert literal_string('"a.$b"') is None and literal_string("$var") is None and literal_string("'a' . $b") is None
    assert parse_actions("save") == ["save"]
    assert parse_actions("add(5); remove(1)") == ["add", "remove"]
    for none in ("$refresh", "$set('a', 1)", "open = true", "$parent.go()", "save{{ $x }}", "", "a.b()"):
        assert parse_actions(none) == [], none


def _rel(resolved) -> str | None:
    return resolved.rel if resolved is not None else None


def _registrations(tmp_path: Path, php: str) -> Registrations:
    path = tmp_path / "app" / "P.php"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(php, encoding="utf-8")
    f = load_php_file(path, "app/P.php")
    assert f is not None
    reg = Registrations()
    collect_registrations(f, reg)
    return reg


def test_view_helper_is_detected_from_its_body(tmp_path):
    reg = _registrations(tmp_path, """<?php
function theme_view(string $view, ?string $area = null): string { return "theme-{$area}::{$view}"; }
function admin_view($v) { return "admin::{$v}"; }
function other($a) { return "x{$a}"; }
""")
    assert set(reg.helpers) == {"theme_view", "admin_view"}
    theme = reg.helpers["theme_view"]
    assert theme.view_name(["auth.login", "guest"]) == "theme-guest::auth.login"
    assert theme.view_name(["auth.login"]) is None            # area decided at runtime
    assert reg.helpers["admin_view"].view_name(["a.b"]) == "admin::a.b"


def test_registrations_are_read_with_literal_paths_only(tmp_path):
    reg = _registrations(tmp_path, """<?php
namespace App\\Providers;
use Illuminate\\Support\\Facades\\View;
use Illuminate\\Support\\Facades\\Blade;
class P {
    function boot() {
        $this->loadViewsFrom(__DIR__.'/../v', 'a');
        View::prependNamespace('a', base_path('first'));
        $this->loadViewsFrom([__DIR__.'/x', resource_path('y')], 'multi');
        $this->loadViewsFrom($dynamic, 'tainted');
        $this->loadViewsFrom(__DIR__.'/z', 'tainted');
        $this->loadViewsFrom(__DIR__.'/w', $name);
        Blade::anonymousComponentPath(__DIR__.'/c');
        Blade::anonymousComponentPath(__DIR__.'/d', 'pre');
        Blade::anonymousComponentPath($dyn);
        View::addNamespace("theme-{$area}", $path);
    }
}
""")
    assert reg.view_ns == {"a": ["first", "v"], "multi": ["app/x", "resources/y"], "tainted": ["app/z"]}
    assert reg.tainted_ns == {"tainted"}   # one of its registrations is not a literal path
    assert reg.anon_paths == [("app/c", None), ("app/d", "pre")]
    resolver = ViewResolver({"app/z/q.blade.php", "first/q.blade.php"}, reg, LaravelViewOptions())
    assert resolver.view("tainted::q") is None and _rel(resolver.view("a::q")) == "first/q.blade.php"


def test_resolver_published_override_namespace_index_and_ambiguity():
    blade = {
        "resources/views/vendor/pkg/a.blade.php", "vendor-less/pkg-views/a.blade.php",
        "vendor-less/pkg-views/b.blade.php", "resources/views/x/y.blade.php",
        "pkgs/one/same.blade.php", "pkgs/two/same.blade.php",
        "resources/views/components/card/index.blade.php",
        "resources/views/components/tile/tile.blade.php",
    }
    reg = Registrations()
    reg.add_ns("pkg", ["vendor-less/pkg-views"])
    reg.add_ns("twin", ["pkgs/one", "pkgs/two"])
    r = ViewResolver(blade, reg, LaravelViewOptions())
    assert _rel(r.view("pkg::a")) == "resources/views/vendor/pkg/a.blade.php"   # published override wins
    assert _rel(r.view("pkg::b")) == "vendor-less/pkg-views/b.blade.php"
    assert _rel(r.view("x.y")) == "resources/views/x/y.blade.php"
    assert r.view("twin::same") is None                                      # two directories: never guess
    for bad in ("", "a..b", "x.", ".x", "ns::", "::x", "a b", "x/../y", "unknown::a"):
        assert r.view(bad) is None, bad
    assert _rel(r.anonymous_component("card")) == "resources/views/components/card/index.blade.php"
    assert _rel(r.anonymous_component("tile")) == "resources/views/components/tile/tile.blade.php"
    assert r.anonymous_component("nothing") is None


def test_view_helper_dataclass_is_hashable_data():
    assert ViewHelper("v", 0, None, fixed_ns="n").view_name(["a"]) == "n::a"
