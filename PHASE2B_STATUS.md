# Phase 2b (Laravel <-> Inertia/Vue) status

Branch `feat/laravel-inertia` (from `laravel`), not pushed.

## Done (all committed)
- fix(js) x3: split `}`/`from` clause normalisation (extract.py + resolution.py `_normalize_js_split_from_clause`), named import through barrel `default as X` -> .vue/.svelte file node (resolution.py `origin_node_id`), AST cache schema 19.
- feat(laravel): `laravel_web` resolver (`laravel/web.py`, registered in extract.py): inertia.py (page roots, renders edges, props contract, shared props), ziggy.py (calls_route / references_route), phpprops.py, phpsites.py, webfiles.py; `Route::inertia` route nodes (routes.py/emit.py).
- Fixture `tests/fixtures/laravel_inertia_app` + `tests/test_laravel_inertia.py`; ARCHITECTURE.md updated.

## State
- Full pytest: 8482 passed (before last small ziggy skip-path fix; test_laravel_* rerun green). ruff clean, pyright 637.
- Bench (phase2b-inertia vs phase1-routes): inertia_pages 100% on all 4 Inertia apps; ziggy_routes 99.0/99.3/96.7/99.9% (remaining misses are `route()` inside comments); js_local_imports unchanged; 0 regressions, 12 improved cells.

## Left / next steps
1. `git checkout uv.lock` before any commit; rerun `uv run pytest tests -q`, `ruff`, `pyright` once more.
2. Optional: results JSON in /Users/if12is/if12is/compertme/graphify-laravel-audit/bench/results/phase2b-inertia.json.
3. Not supported: custom resolver functions, variable component names, props via variable, React/Svelte page props, auto-registered Vue components, incremental-build staleness of JS->route edges when only route files change.
