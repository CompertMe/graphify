# Phase 2a status: Laravel views / Blade / Livewire (branch feat/laravel-views)

## Done
- New modules in graphify/extractors/laravel/: blade.py (template scanner), views.py (registrations + ViewResolver),
  viewcalls.py (PHP view()/View::make/->view/->markdown/->layout/loadView/Content/$view finder),
  livewire.py (component discovery, naming, single-file components), viewpass.py (orchestrator + emission),
  viewconfig.py (.graphifyrc laravel.view_namespace.* / laravel.component_path[.prefix]).
- Hooks: __init__.py calls run_view_passes via _model_views (also for Blade-only projects); extract.py drops records
  flagged `_drop` (legacy Blade stub nodes/edges) after the resolvers; rcfile.py docstring; ARCHITECTURE.md paragraph.
- Edges: extends/includes/uses_component (blade), renders (PHP/route -> blade, Livewire class -> view), binds_method
  (wire:* and $wire.x() -> component method). Metadata on blade file nodes (blade.sections/yields/pushes/stacks,
  livewire.component/models). Single-file components `⚡x.blade.php` -> livewire_component node + method nodes.
- Tests: tests/test_laravel_views.py (30 tests) + fixture tests/fixtures/laravel_views_app.

## State
- Full pytest: 8491 passed, 15 skipped, 1 failed (the known tests/test_skill_no_backtick_substitution.py failure).
- ruff clean; pyright 637 errors (= baseline).
- Bench (scratch-views-all, before the last small edits: local-variable anonymousComponentPath, ->layout()):
  php_view_refs social 54% (rest need theme mapping), others 97-100%; blade_includes 94-100%; blade_x_components
  cyberportal 99.8% (misses are x-tags inside {{-- --}} comments), social 0% before variable-path fix;
  livewire_view social 94%, dent 100%; blade_edges_real 100% everywhere; stub_nodes down.
  With a .graphifyrc in a corpus copy (corpus-rc/social) social reached 100% php_view_refs / x_components / livewire_view.
  Only "regression": noise_pct social 6.0 -> 6.2 (denominator shrank: 431 stub nodes removed).

## Left
1. Final clean bench with the final code: `python3 -I bench.py --corpus <copy> --graphify-cmd "uv run --project <worktree> graphify"
   --label phase2a-views`, then `python3 -I compare.py results/phase1-routes.json results/phase2a-views.json`
   (copy at <scratchpad>/views-agent/corpus; rc variant corpus-rc/social). Do not run concurrently with pytest.
2. `graphify explain` outputs: social Livewire component, cyberportal blade component (run in the corpus copy's dir).
3. Restore uv.lock before each commit (`git checkout uv.lock`); do not push.
4. Final report: design, compare table, explain outputs, unsupported forms (see ARCHITECTURE.md paragraph).
