"""The Inertia/Ziggy pass (``laravel_web``) and the view/Blade pass must coexist.

Both read Blade text and both emit ``renders``. They must not emit the same edge twice,
and the Blade ``route('x')`` scan must not bring back the legacy Blade stub nodes the
view pass removes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_php")

from graphify.extract import collect_files, extract  # noqa: E402


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def result(tmp_path: Path) -> dict:
    _write(tmp_path, "artisan", "<?php\n")
    _write(tmp_path, "composer.json", '{"require": {"laravel/framework": "^11.0"}}')
    _write(tmp_path, "routes/web.php", """<?php
use App\\Http\\Controllers\\PageController;
use Illuminate\\Support\\Facades\\Route;

Route::get('/home', [PageController::class, 'home'])->name('home');
Route::view('/about', 'about')->name('about');
Route::inertia('/dash', 'Dash')->name('dash');
""")
    _write(tmp_path, "app/Http/Controllers/PageController.php", """<?php
namespace App\\Http\\Controllers;

use Inertia\\Inertia;

class PageController
{
    public function home()
    {
        return view('home');
    }

    public function dash()
    {
        return Inertia::render('Dash', ['a' => 1]);
    }
}
""")
    _write(tmp_path, "resources/views/home.blade.php",
           "@extends('layouts.app')\n@include('partials.nav')\n<a href=\"{{ route('about') }}\">x</a>\n")
    _write(tmp_path, "resources/views/about.blade.php", "<a href=\"{{ route('home') }}\">h</a>\n")
    _write(tmp_path, "resources/views/layouts/app.blade.php", "@yield('c')\n")
    _write(tmp_path, "resources/views/partials/nav.blade.php", "<a href=\"{{ route('home') }}\">h</a>\n")
    _write(tmp_path, "resources/js/Pages/Dash.vue", "<script setup>\ndefineProps(['a'])\n</script>\n<template><div/></template>\n")
    root = tmp_path.resolve()
    return extract(collect_files(root), root=root, parallel=False)


def test_no_duplicate_edges_across_passes(result):
    seen: dict[tuple, int] = {}
    for e in result["edges"]:
        key = (e["source"], e["target"], e["relation"], e.get("source_location"))
        seen[key] = seen.get(key, 0) + 1
    assert [k for k, n in seen.items() if n > 1] == []


def test_both_passes_contribute_and_no_legacy_stubs_return(result):
    nodes = {n["id"]: n for n in result["nodes"]}
    rels = {(nodes[e["source"]]["source_file"], nodes[e["target"]]["label"], e["relation"])
            for e in result["edges"] if e["source"] in nodes and e["target"] in nodes}
    # view pass
    assert ("resources/views/home.blade.php", "partials/nav.blade.php", "includes") in rels or any(
        r[0] == "resources/views/home.blade.php" and r[2] == "includes" for r in rels
    )
    assert any(r[0] == "app/Http/Controllers/PageController.php" and r[2] == "renders" and "home" in r[1] for r in rels)
    # web pass: Blade route('about') -> route node, Inertia render -> page file
    assert any(r[0] == "resources/views/home.blade.php" and r[2] == "calls_route" for r in rels)
    assert any(r[0] == "app/Http/Controllers/PageController.php" and r[2] == "renders" and "Dash" in r[1] for r in rels)
    # no sourceless stub left for a Blade view name
    stubs = [n for n in result["nodes"] if not n.get("source_file") and any(
        s in str(n.get("label", "")) for s in ("layouts.app", "partials.nav", "layouts/app"))]
    assert stubs == []
    assert not any(n.get("_drop") for n in result["nodes"])
    assert not any(e.get("_drop") for e in result["edges"])
