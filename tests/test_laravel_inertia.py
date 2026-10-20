"""Laravel <-> Inertia/Vue linking: ``renders`` edges, the props contract, ``route()`` links.

Fixture: tests/fixtures/laravel_inertia_app (a mini Inertia + Vue 3 app).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_php")

from graphify.extract import collect_files, extract  # noqa: E402

APP = Path(__file__).parent / "fixtures" / "laravel_inertia_app"


def _extract(root: Path) -> dict:
    return extract(collect_files(root), root=root, parallel=False)


@pytest.fixture(scope="module")
def graph() -> dict:
    return _extract(APP)


def _nodes(g: dict) -> dict:
    return {n["id"]: n for n in g["nodes"]}


def _file(g: dict, rel: str) -> dict:
    hits = [
        n for n in g["nodes"]
        if n.get("source_file") == rel and n.get("source_location") in ("L1", None)
        and rel.endswith("/" + n["label"].lstrip("/")) or n.get("source_file") == rel and n["label"] == rel
    ]
    assert len(hits) == 1, (rel, hits)
    return hits[0]


def _edges(g: dict, relation: str) -> list[dict]:
    return [e for e in g["edges"] if e["relation"] == relation]


def _described(g: dict, relation: str) -> set[tuple[str, str, str]]:
    nodes = _nodes(g)
    return {
        (e["source_file"], nodes[e["source"]]["label"], nodes[e["target"]]["label"])
        for e in _edges(g, relation)
    }


def test_render_edges_come_from_the_enclosing_callable(graph):
    got = _described(graph, "renders")
    assert got == {
        ("app/Http/Controllers/BranchController.php", ".index()", "Index.vue"),
        ("app/Http/Controllers/BranchController.php", ".create()", "Form.vue"),
        ("app/Http/Controllers/BranchController.php", ".show()", "Show.vue"),
        ("app/Providers/FortifyServiceProvider.php", "{closure#1}()", "Login.vue"),
        ("app/Providers/FortifyServiceProvider.php", "{closure#2}()", "Register.vue"),
        ("routes/web.php", "GET /closure-page()", "Page.vue"),
        ("routes/web.php", "GET /about", "About.vue"),
    }
    assert {e["confidence"] for e in _edges(graph, "renders")} == {"EXTRACTED"}


def test_dynamic_missing_and_unresolvable_pages_get_no_edge(graph):
    nodes = _nodes(graph)
    targets = {nodes[e["target"]]["source_file"] for e in _edges(graph, "renders")}
    assert not any("Nope" in t or "Exist" in t for t in targets)
    # `Inertia::render($page)` / `'Tenant/' . $page` in dynamic(), and the missing pages
    sources = {nodes[e["source"]]["label"] for e in _edges(graph, "renders")}
    assert ".dynamic()" not in sources and ".edit()" not in sources and "GET /missing" not in sources


def test_render_edge_carries_literal_prop_keys(graph):
    nodes = _nodes(graph)
    by_target = {nodes[e["target"]]["label"]: e for e in _edges(graph, "renders")}
    assert by_target["Index.vue"]["props"] == ["branches", "filters", "orphan_prop"]
    assert "props_partial" not in by_target["Index.vue"]
    # compact('branch') + ->with('mode', ...)
    assert by_target["Form.vue"]["props"] == ["branch", "mode"]
    # a variable: nothing known, flagged partial rather than guessed
    assert by_target["Show.vue"]["props"] == [] and by_target["Show.vue"]["props_partial"] is True
    assert by_target["About.vue"]["props"] == ["title"]
    assert by_target["Index.vue"]["page"] == "Tenant/Branches/Index"


def _meta(g: dict, rel: str) -> dict:
    return _file(g, rel).get("metadata") or {}


def test_page_metadata_reports_prop_mismatches(graph):
    index = _meta(graph, "resources/js/Pages/Tenant/Branches/Index.vue")
    assert index["inertia_props_declared"] == ["branches", "filters", "stale_prop", "auth"]
    assert index["inertia_props_undeclared"] == ["orphan_prop"]
    # `auth` is a shared prop, so it is not "never passed"; stale_prop really is
    assert index["inertia_props_unused"] == ["stale_prop"]

    form = _meta(graph, "resources/js/Pages/Tenant/Branches/Form.vue")  # interface Props via withDefaults
    assert form["inertia_props_declared"] == ["branch", "mode"]
    assert "inertia_props_undeclared" not in form and "inertia_props_unused" not in form

    show = _meta(graph, "resources/js/Pages/Tenant/Branches/Show.vue")  # array form, partial passing
    assert show["inertia_props_declared"] == ["branch", "extra"]
    assert show["inertia_props_passed_partial"] is True
    assert "inertia_props_unused" not in show  # cannot know what a variable carried

    about = _meta(graph, "resources/js/Pages/About.vue")  # no defineProps: no contract to compare
    assert "inertia_props_declared" not in about and about["inertia_props_passed"] == ["title"]


def test_shared_props_are_recorded_on_the_middleware(graph):
    mw = [n for n in graph["nodes"] if n["label"] == "HandleInertiaRequests" and n["source_file"].endswith("Requests.php")]
    assert len(mw) == 1
    assert mw[0]["metadata"]["inertia_shared_props"] == ["auth", "flash"]


def test_route_inertia_route_node_carries_page_and_props(graph):
    routes = {n["label"]: n for n in graph["nodes"] if (n.get("metadata") or {}).get("kind") == "laravel_route"}
    about = routes["GET /about"]["metadata"]
    assert about["route_kind"] == "inertia" and about["inertia_page"] == "About"
    assert about["inertia_props"] == ["title"] and about["name"] == "about"
    assert "inertia_props" not in routes["GET /missing"]["metadata"]


def test_ziggy_route_calls_link_the_enclosing_function_or_component(graph):
    got = _described(graph, "calls_route")
    assert got >= {
        ("resources/js/Pages/Tenant/Branches/Index.vue", "openBranch()", "GET /branches/{branch}"),
        ("resources/js/Pages/Tenant/Branches/Index.vue", "create()", "GET /branches/create"),
        # template-only usages belong to the component file
        ("resources/js/Pages/Tenant/Branches/Index.vue", "Index.vue", "GET /branches"),
        ("resources/js/Pages/Tenant/Branches/Index.vue", "Index.vue", "GET /"),
        ("resources/js/Pages/Tenant/Branches/Form.vue", "Form.vue", "POST /branches"),
        ("resources/js/Components/BranchCard.vue", "showUrl()", "GET /branches/{branch}"),
        ("resources/js/Components/BranchCard.vue", "BranchCard.vue", "GET /branches/{branch}/edit"),
        ("resources/js/Composables/useNav.js", "useNav()", "GET /"),
        # a `Public/` page directory is source, not the built `public/` output
        ("resources/js/Pages/Public/Landing.vue", "goHome()", "GET /"),
    }
    # PHP: to_route() / redirect()->route() from the enclosing method; Blade text from the view
    assert got >= {
        ("app/Http/Controllers/BranchController.php", ".store()", "GET /branches"),
        ("app/Http/Controllers/BranchController.php", ".dynamic()", "GET /branches/{branch}"),
        ("resources/views/welcome.blade.php", "welcome.blade.php", "GET /branches"),
        ("resources/views/welcome.blade.php", "welcome.blade.php", "POST /branches"),
    }


def test_route_current_is_a_reference_not_a_call(graph):
    got = _described(graph, "references_route")
    assert got == {
        ("resources/js/Pages/Tenant/Branches/Index.vue", "Index.vue", "GET /"),
        ("resources/js/Composables/useNav.js", "useNav()", "GET /"),
    }


def test_ziggy_skips_unknown_computed_commented_and_non_route_calls(graph):
    nodes = _nodes(graph)
    names = {e["route"] for e in _edges(graph, "calls_route")}
    assert "no.such.route" not in names and "not.a.route" not in names and "login" not in names
    # the commented-out route('tenant.branches.create') / route('...edit') and
    # `$request->route('branch')` never become edges
    commented = [
        e for e in _edges(graph, "calls_route")
        if e["source_file"] == "resources/views/welcome.blade.php" and e["route"] == "tenant.branches.create"
    ]
    assert commented == []
    assert not [e for e in _edges(graph, "calls_route") if nodes[e["source"]]["label"] == "BranchController.php"]
    index_edit = [
        e for e in _edges(graph, "calls_route")
        if e["source_file"].endswith("Branches/Index.vue") and e["route"] == "tenant.branches.edit"
    ]
    assert index_edit == []


def test_name_defined_twice_links_both_as_inferred(graph):
    dup = [e for e in _edges(graph, "calls_route") if e["route"] == "dup"]
    assert len(dup) == 2
    assert {e["confidence"] for e in dup} == {"INFERRED"} and {e["ambiguous"] for e in dup} == {2}
    assert len({e["source"] for e in dup}) == 1


def test_named_import_through_barrel_reaches_the_component(graph):
    nodes = _nodes(graph)
    imports = {
        (nodes[e["source"]]["label"], nodes[e["target"]]["label"])
        for e in _edges(graph, "imports") if e["source_file"].endswith("Branches/Index.vue")
    }
    assert ("Index.vue", "Alert.vue") in imports
    ids = set(nodes)
    assert [e for e in graph["edges"] if e["relation"] == "imports" and e["target"] not in ids
            and e["source_file"].endswith(".vue")] == []


def test_edges_are_deterministic():
    a, b = _extract(APP), _extract(APP)
    pick = ("renders", "calls_route", "references_route")
    assert [e for e in a["edges"] if e["relation"] in pick] == [e for e in b["edges"] if e["relation"] in pick]


def test_default_pages_root_without_an_app_entry(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(APP, root)
    (root / "resources/js/app.js").unlink()
    got = _described(_extract(root), "renders")
    assert ("app/Http/Controllers/BranchController.php", ".index()", "Index.vue") in got


def test_page_root_is_read_from_the_resolve_option(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(APP, root)
    pages = root / "resources/js/Pages"
    pages.rename(root / "resources/js/screens")
    (root / "resources/js/app.js").write_text(
        "createInertiaApp({\n"
        "  resolve: (name) => resolvePageComponent(`./screens/${name}.vue`, import.meta.glob('./screens/**/*.vue')),\n"
        "})\n"
    )
    got = _described(_extract(root), "renders")
    assert ("app/Http/Controllers/BranchController.php", ".index()", "Index.vue") in got


def test_ambiguous_page_file_gets_no_edge(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(APP, root)
    shutil.copy(root / "resources/js/Pages/About.vue", root / "resources/js/Pages/About.js")
    (root / "resources/js/app.js").unlink()
    got = _described(_extract(root), "renders")
    assert not [r for r in got if r[2] in ("About.vue", "About.js")]


def test_non_laravel_project_is_untouched(tmp_path):
    root = tmp_path / "plain"
    shutil.copytree(APP, root)
    (root / "artisan").unlink()
    (root / "composer.json").write_text('{"require": {"slim/slim": "^4"}}')
    g = _extract(root)
    assert not _edges(g, "renders") and not _edges(g, "calls_route") and not _edges(g, "references_route")
