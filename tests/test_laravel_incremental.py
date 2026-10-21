"""An incremental (changed-files) rebuild of a Laravel project equals a full rebuild.

The Laravel passes link files to one another through names that live elsewhere
(``route('x')`` in a Vue file -> the route named ``x`` in a route file, a
``Schedule::command('sig')`` -> the command class that declares ``sig``). A hook
rebuild re-extracts only the changed files, so an edge whose two ends sit in
different files used to be (re)computed only when the file that *owns* the edge
changed: a stale edge survived the rename of its target, and a new target never
reached the unchanged file that already named it. ``_rebuild_code`` therefore
re-extracts every file the Laravel passes read whenever a Laravel-relevant file
changes (the AST cache makes the unchanged ones cheap), and the result is the
graph a ``--force`` build of the same tree produces.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_php")

from graphify.watch import _rebuild_code  # noqa: E402

FILES = {
    "artisan": "#!/usr/bin/env php\n<?php\n",
    "composer.json": json.dumps({"require": {"laravel/framework": "^11.0"}}),
    "routes/web.php": (
        "<?php\n\n"
        "use App\\Http\\Controllers\\PageController;\n"
        "use Illuminate\\Support\\Facades\\Route;\n\n"
        "Route::get('/pages', [PageController::class, 'index'])->name('pages.index');\n"
    ),
    "app/Http/Controllers/PageController.php": (
        "<?php\n\nnamespace App\\Http\\Controllers;\n\nuse Inertia\\Inertia;\n\n"
        "class PageController\n{\n    public function index()\n    {\n"
        "        return Inertia::render('Home', ['title' => 'x']);\n    }\n\n"
        "    public function show()\n    {\n        return 'ok';\n    }\n}\n"
    ),
    "resources/js/Pages/Home.vue": (
        "<script setup>\nconst url = route('pages.show')\nconst list = route('pages.index')\n</script>\n"
        "<template><div>{{ url }}</div></template>\n"
    ),
    "resources/js/Pages/Other.vue": (
        "<script setup>\nconst x = 1\n</script>\n<template><div>{{ x }}</div></template>\n"
    ),
    "app/Console/Commands/SyncThings.php": (
        "<?php\n\nnamespace App\\Console\\Commands;\n\nuse Illuminate\\Console\\Command;\n\n"
        "class SyncThings extends Command\n{\n    protected $signature = 'things:sync';\n\n"
        "    public function handle(): int\n    {\n        return 0;\n    }\n}\n"
    ),
    "routes/console.php": (
        "<?php\n\nuse Illuminate\\Support\\Facades\\Schedule;\n\n"
        "Schedule::command('things:sync')->daily();\n"
    ),
}


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _edit(root: Path, rel: str, old: str, new: str) -> None:
    text = (root / rel).read_text(encoding="utf-8")
    assert old in text, (rel, old)
    _write(root, rel, text.replace(old, new, 1))


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    for rel, text in FILES.items():
        _write(root, rel, text)
    return root


def _rebuild(root: Path, changed: list[str] | None = None) -> dict:
    assert _rebuild_code(
        root,
        changed_paths=[Path(p) for p in changed] if changed is not None else None,
        no_cluster=True,
        acquire_lock=False,
        force=True,
    ) is True
    return json.loads((root / "graphify-out" / "graph.json").read_text(encoding="utf-8"))


def _shape(graph: dict) -> tuple[set[str], set[str]]:
    drop = ("community", "community_name")
    nodes = {json.dumps({k: v for k, v in n.items() if k not in drop}, sort_keys=True) for n in graph["nodes"]}
    edges = {
        json.dumps({k: v for k, v in e.items() if k not in drop}, sort_keys=True)
        for e in graph.get("links", graph.get("edges", []))
    }
    return nodes, edges


def _relations(graph: dict, relation: str, source_suffix: str) -> set[tuple[str, str]]:
    labels = {n["id"]: n.get("label", "") for n in graph["nodes"]}
    return {
        (labels[e["source"]], labels[e["target"]])
        for e in graph.get("links", graph.get("edges", []))
        if e["relation"] == relation and e.get("source_file", "").endswith(source_suffix)
    }


def _assert_matches_full_build(root: Path, incremental: dict) -> None:
    fresh_root = root.parent / "fresh"
    shutil.copytree(root, fresh_root, ignore=shutil.ignore_patterns("graphify-out"))
    full = _rebuild(fresh_root)
    inc_nodes, inc_edges = _shape(incremental)
    full_nodes, full_edges = _shape(full)
    assert sorted(inc_nodes - full_nodes) == [] and sorted(full_nodes - inc_nodes) == [], "node sets differ"
    assert sorted(inc_edges - full_edges) == [] and sorted(full_edges - inc_edges) == [], "edge sets differ"


def test_fixture_models_the_edges_the_scenarios_rely_on(tmp_path):
    graph = _rebuild(_project(tmp_path))
    assert _relations(graph, "calls_route", "Home.vue"), "Home.vue -> route edge missing from the fixture graph"
    assert any(n.get("metadata", {}).get("kind") == "laravel_schedule" for n in graph["nodes"])
    # The props contract is stored on the page node by swapping it for an annotated copy.
    assert any("inertia_props_passed" in (n.get("metadata") or {}) for n in graph["nodes"])


def test_route_uri_change(tmp_path):
    root = _project(tmp_path)
    _rebuild(root)
    _edit(root, "routes/web.php", "'/pages'", "'/pages-x'")
    _assert_matches_full_build(root, _rebuild(root, ["routes/web.php"]))


def test_route_rename_drops_the_edge_of_an_unchanged_vue_file(tmp_path):
    root = _project(tmp_path)
    before = _rebuild(root)
    assert any("pages.index" in tgt or "pages" in tgt for _src, tgt in _relations(before, "calls_route", "Home.vue"))
    _edit(root, "routes/web.php", "->name('pages.index')", "->name('pages.list')")
    incremental = _rebuild(root, ["routes/web.php"])
    assert _relations(incremental, "calls_route", "Home.vue") == set()
    _assert_matches_full_build(root, incremental)


def test_new_route_reaches_an_unchanged_vue_file_that_already_names_it(tmp_path):
    root = _project(tmp_path)
    before = _rebuild(root)
    assert len(_relations(before, "calls_route", "Home.vue")) == 1      # pages.show is undefined yet
    _edit(
        root, "routes/web.php", "->name('pages.index');",
        "->name('pages.index');\nRoute::get('/pages/{id}', [PageController::class, 'show'])->name('pages.show');",
    )
    incremental = _rebuild(root, ["routes/web.php"])
    assert len(_relations(incremental, "calls_route", "Home.vue")) == 2
    _assert_matches_full_build(root, incremental)


def test_route_call_added_to_a_vue_file(tmp_path):
    root = _project(tmp_path)
    _rebuild(root)
    _edit(root, "resources/js/Pages/Other.vue", "const x = 1", "const x = route('pages.index')")
    incremental = _rebuild(root, ["resources/js/Pages/Other.vue"])
    assert _relations(incremental, "calls_route", "Other.vue")
    _assert_matches_full_build(root, incremental)


def test_schedule_added_to_the_console_routes(tmp_path):
    root = _project(tmp_path)
    _rebuild(root)
    _edit(root, "routes/console.php", "->daily();", "->daily();\nSchedule::command('things:sync')->hourly();")
    _assert_matches_full_build(root, _rebuild(root, ["routes/console.php"]))


def test_command_signature_change_unlinks_the_unchanged_schedule(tmp_path):
    root = _project(tmp_path)
    before = _rebuild(root)
    assert _relations(before, "handled_by", "console.php") or _relations(before, "schedules", "console.php") or any(
        e.get("source_file", "").endswith("console.php") and e["relation"] not in ("contains",)
        for e in before.get("links", before.get("edges", []))
    )
    _edit(root, "app/Console/Commands/SyncThings.php", "'things:sync'", "'things:sync2'")
    _assert_matches_full_build(root, _rebuild(root, ["app/Console/Commands/SyncThings.php"]))


def test_command_signature_restored_relinks_the_unchanged_schedule(tmp_path):
    root = _project(tmp_path)
    _edit(root, "app/Console/Commands/SyncThings.php", "'things:sync'", "'things:sync2'")
    _rebuild(root)
    _edit(root, "app/Console/Commands/SyncThings.php", "'things:sync2'", "'things:sync'")
    _assert_matches_full_build(root, _rebuild(root, ["app/Console/Commands/SyncThings.php"]))


def test_deleted_route_file_is_evicted_like_a_full_build(tmp_path):
    root = _project(tmp_path)
    _write(root, "routes/extra.php", (
        "<?php\n\nuse App\\Http\\Controllers\\PageController;\nuse Illuminate\\Support\\Facades\\Route;\n\n"
        "Route::get('/extra', [PageController::class, 'show'])->name('pages.show');\n"
    ))
    _rebuild(root)
    (root / "routes" / "extra.php").unlink()
    _assert_matches_full_build(root, _rebuild(root, ["routes/extra.php"]))


def test_widening_is_limited_to_laravel_projects_and_laravel_relevant_changes(tmp_path):
    from graphify.extractors.laravel.project import widen_incremental_targets

    root = _project(tmp_path)
    code = sorted(p for p in root.rglob("*") if p.suffix in {".php", ".vue", ".md"})
    (root / "NOTES.md").write_text("# notes\n", encoding="utf-8")
    code.append(root / "NOTES.md")
    notes = [root / "NOTES.md"]
    # A Markdown-only change does not wake the Laravel passes.
    assert widen_incremental_targets(root, notes, code, notes) == notes
    # A change to a route file re-extracts every PHP/Vue file, not the Markdown one.
    routes = [root / "routes" / "web.php"]
    widened = widen_incremental_targets(root, routes, code, routes)
    assert {p.name for p in widened} >= {"web.php", "Home.vue", "SyncThings.php", "console.php"}
    assert "NOTES.md" not in {p.name for p in widened}
    # A deletion counts as a change even though nothing is left to extract.
    assert widen_incremental_targets(root, [], code, [root / "routes" / "gone.php"])
    # A PHP project that is not a Laravel app is left alone.
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.php").write_text("<?php\nfunction a() {}\n", encoding="utf-8")
    (plain / "b.php").write_text("<?php\nfunction b() {}\n", encoding="utf-8")
    only_a = [plain / "a.php"]
    assert widen_incremental_targets(plain, only_a, [plain / "a.php", plain / "b.php"], only_a) == only_a
