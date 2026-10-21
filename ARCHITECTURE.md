# Architecture

graphify is a Claude Code skill backed by a Python library. The skill orchestrates the library; the library can be used standalone.

## Pipeline

```
detect()  →  extract()  →  build()  →  cluster()  →  analyze helpers  →  report.generate()  →  export.to_*()
```

Each stage lives in its own module and they communicate through plain Python dicts and NetworkX graphs. While designed to minimize shared state, some stages (like extractors) rely on ambient module-level state around the active scan root. Most stages are a single function; `analyze.py` and `export.py` are sets of sibling functions rather than one entry point.

## Module responsibilities

Signatures below are the real ones - `tests/test_architecture_doc.py` imports every symbol named here, so this table cannot drift from the code.

| Module | Entry point(s) | Input → Output |
|--------|----------------|----------------|
| `detect.py` | `detect(root)` | directory → scan summary dict: `files` grouped by category, plus `total_files`, `total_words`, `warning`, `scan_root`, … |
| `extract.py` | `extract(paths, *, root=None, ...)`, `collect_files(target)` | **list** of file paths → `{nodes, edges}` dict. `collect_files` expands a directory into that list, and lives here, not in `detect.py` |
| `build.py` | `build(extractions)`, `build_from_json(extraction)` | extraction dict(s) → `nx.Graph` |
| `cluster.py` | `cluster(G)` | graph → `{community_id: [node_id, ...]}` (the graph is not mutated) |
| `analyze.py` | `god_nodes(G)`, `surprising_connections(G)`, `suggest_questions(G, communities, community_labels)`, `find_import_cycles(G)`, `graph_diff(G_old, G_new)` | graph → one list/dict per analysis. There is no single `analyze()` entry point |
| `report.py` | `generate(G, communities, cohesion_scores, community_labels, ...)` | graph + analysis → GRAPH_REPORT.md string |
| `export.py` | `to_json`, `to_html`, `to_obsidian`, `to_svg`, `to_graphml`, `to_canvas`, `to_cypher` | graph → graph.json, graph.html, Obsidian vault, graph.svg, … one function per format |
| `wiki.py` | `to_wiki(G, communities, output_dir, ...)` | graph → one markdown article per community + `index.md` |
| `callflow_html.py` | `write_callflow_html(...)` | graphify-out files → Mermaid architecture/call-flow HTML |
| `ingest.py` | `ingest(url, target_dir, ...)` | URL → file saved to corpus dir |
| `cache.py` | `check_semantic_cache(files, root)`, `save_semantic_cache(nodes, edges, ...)` | files → cached nodes / edges / hyperedges + the list of files still needing extraction |
| `security.py` | `validate_url`, `safe_fetch`, `validate_graph_path`, `sanitize_label` | URL / path / label → validated value, or raises |
| `validate.py` | `validate_extraction(data)`, `assert_valid(data)` | extraction dict → **list of schema error strings** (`validate_extraction` returns them; `assert_valid` raises) |
| `serve.py` | `serve(graph_path)`, `serve_http(graph_path, *, host, port, ...)` | graph file path → MCP stdio server / HTTP server |
| `watch.py` | `watch(watch_path, debounce=3.0)`, `check_update(watch_path)` | directory → rebuild on change; `check_update` reports whether a re-extraction is pending |
| `benchmark.py` | `run_benchmark(graph_path)` | graph file → corpus vs subgraph token comparison |

### Calling `extract()` from your own code

`extract()` takes a **list** of paths, and `root` is keyword-only and optional:

```python
from pathlib import Path
from graphify.extract import extract

paths = [Path("src/lib/content.ts"), Path("src/pages/index.astro")]
result = extract(paths, root=Path(".").resolve())   # pass root explicitly
```

Always pass `root`. Node ids and `source_file` values are derived relative to it; when it is omitted, `extract()` infers one from the paths you passed, which is the common parent of *that list* rather than your project root. A single-file call therefore anchors to that file's own directory, and ids can end up carrying path segments from the machine they were extracted on.

## Extraction output schema

Every extractor returns:

```json
{
  "nodes": [
    {"id": "unique_string", "label": "human name", "source_file": "path", "source_location": "L42"}
  ],
  "edges": [
    {"source": "id_a", "target": "id_b", "relation": "calls|imports|uses|...", "confidence": "EXTRACTED|INFERRED|AMBIGUOUS"}
  ]
}
```

`validate.py` enforces this schema before `build()` consumes it.

## Edge direction in graph.json

`graph.json` is written `"directed": false`, but every link is directed: **arc order is the direction** (`source` → `target`, #563). Older canonicalized files carry it in `_src`/`_tgt` markers instead, and those win where present (#2309).

A plain `json_graph.node_link_graph()` load loses this: an undirected graph re-orders each edge's endpoints by node-list position. Load through `paths.load_node_link_graph()` (or apply `paths.restore_arc_direction()` to the raw dict first), which stamps `_src`/`_tgt` on every edge. Then read direction as `data.get("_src", u)` / `data.get("_tgt", v)`, never from `u, v`. Anything that writes graph.json back out pops the markers into arc order first, as `export.to_json()` does.

## Confidence labels

| Label | Meaning |
|-------|---------|
| `EXTRACTED` | Relationship is explicitly stated in the source (e.g., an import statement, a direct call) |
| `INFERRED` | Relationship is a reasonable deduction (e.g., call-graph second pass, co-occurrence in context) |
| `AMBIGUOUS` | Relationship is uncertain; flagged for human review in GRAPH_REPORT.md |

## Adding a new language extractor

1. Add an `extract_<lang>(path: Path) -> dict` function following the existing pattern (tree-sitter parse → walk nodes → collect `nodes` and `edges` → call-graph second pass for INFERRED `calls` edges). New languages go in their own module under `graphify/extractors/` - see `graphify/extractors/MIGRATION.md`; `extract.py` re-exports them while the existing ones are ported out of it.
2. Register the file suffix in `extract()`'s dispatch table and in `collect_files()` (both in `extract.py`).
3. Add the suffix to `CODE_EXTENSIONS` in `detect.py` and `_WATCHED_EXTENSIONS` in `watch.py`.
4. Add the tree-sitter package to `pyproject.toml` dependencies.
5. Add a fixture file to `tests/fixtures/` and tests to `tests/test_languages.py`.

## Security

All external input passes through `graphify/security.py` before use:

- URLs → `validate_url()` (http/https only) + `_NoFileRedirectHandler` (blocks file:// redirects)
- Fetched content → `safe_fetch()` / `safe_fetch_text()` (size cap, timeout)
- Graph file paths → `validate_graph_path()` (must resolve inside `graphify-out/`)
- Node labels → `sanitize_label()` (strips control chars, caps 256 chars)

See `SECURITY.md` for the full threat model.

## Testing

`analyze.suggest_questions()` interleaves candidates across question types before
applying its result limit (seven by default). This keeps ambiguous relationships
from hiding bridge, inferred-relationship, isolation, and low-cohesion signals.
The existing candidate order within each type is preserved; if the limit is smaller
than the number of available types, their generation order determines priority.

One test file per module under `tests/`. Run with:

```bash
pytest tests/ -q
```

The test suite is designed to avoid network access and uncontrolled filesystem effects; most tests are isolated with temporary directories and environment fixtures.

Python nested class ids include their enclosing class id. Same-named nested
classes under different parents retain separate definitions, methods, and
containment edges; top-level class ids are unchanged. AST cache schema 7 prevents
older cached definitions from retaining the collapsed identities.

C# partial types merge across files by their path inside the namespace,
enclosing types first and each with its arity (`` Outer`1.Inner ``), within one
assembly. Nested halves merge only under the same enclosing chain, and `Foo` and
`Foo<T>` stay separate types. AST cache schema 9 retires cached C# results that
lack this path. The merge runs after colliding ids are salted apart by path, so
a half whose file stem collides with another file's (`Spec.cs` and `_Spec.cs`,
`Cfg/Helpers.cs` and `Cfg_Helpers.cs`) still merges and keeps its members.

PHP class names written in source (`Foo::class`, `Foo::BAR` class constants and
enum cases, plus type hints) resolve with PHP's own name-resolution rules in
`_resolve_php_type_references`: `use` alias, same namespace, or a leading-`\`
FQCN, each tying the reference to the one declaration with that FQN (`EXTRACTED`).
For `Foo::class` and type hints the extractor records the name as written on the
edge (`_php_raw`, stripped before output); `Foo::BAR` is parked as a `raw_calls`
entry instead (next paragraph) that the same pass annotates with the scope's
resolved class (`php_scope_nid`). `self`/`static` map to the enclosing class. A name imported
from outside the corpus keeps the shared FQN-labelled external stub; a
`::class` that resolves to neither is dropped, never guessed. Not covered:
`Foo::class` in a method's parameter default, a `new \A\B()` or static call
written with a qualified name (those still resolve by bare name), and
`namespace` blocks that mix several namespaces in one file. AST cache schema 18
retires results without the written name.

R extraction preserves symbol-only bindings such as `%||%` as separate nodes.
When normal ID normalization removes an entire binding name, its UTF-8 bytes
provide a deterministic operator suffix within the same lexical owner. Its double
underscore separators cannot collide with an ordinary normalized binding ID. Local
call resolution stops if malformed scope metadata cycles.

PHP `Scope::MEMBER` accesses (`class_constant_access_expression`, member other than
`class`) are the only ones not emitted by the extractor: each is parked as a
`raw_calls` entry tagged `php_const_ref` and bound exactly once by the
`php_class_constant_refs` resolver (`Foo::class` stays with the paragraph above, so one
access never yields two edges). The scope class is the one `_resolve_php_type_references`
found with the PHP name rules; a member that is a case of that enum gets a
`references_constant` edge to the case node, any other member (PHP class constants are
not nodes) links to the class with the same relation. A same-named type in another
namespace is never picked, and an unresolved name gets no edge. Only a file the name pass
skipped (multi-namespace file, no parser) falls back to a unique unqualified name, and an
ambiguous one binds nothing. `self`/`static` resolve to the enclosing type; `parent` and
`$var::` get no edge. Case names match case-sensitively. `Enum::from/tryFrom/cases()`
(implicit statics with no method node) are `calls` edges to the enum, whose scope is
still found by unique name. AST cache schema 18.

PHP enum cases (`case_of` targets) are excluded from the cross-file call-resolution
indexes, including the case-folded one PHP functions use: cases are case-sensitive
constants and never call targets, so a global helper such as `app()` cannot bind to
`Enum::APP`. Incremental rebuilds forward `case_of` context edges so unchanged enum
files are excluded the same way. Other languages are unchanged (Swift payload cases
and Rust variants are callable).

PHP methods (`method` targets) are excluded from those indexes too: a plain function call
needs a function, and a method call carries a receiver (`$this->`, `Foo::`) that the member-call
resolvers handle. `dispatch(new X)` (the Laravel helper) therefore no longer binds to an
in-corpus static method named `dispatch`.

Laravel routes are modelled by `graphify/extractors/laravel/`, a registry resolver
(`laravel_routes`, PHP suffixes) that runs only for a Laravel project: `artisan` at the
scan root, or `laravel/framework` in `composer.json`. Any other PHP corpus comes out
byte-identical, even if it calls `Route::get`. `extract()` hands the pass its scan root
through `set_scan_context`; the pass reads route files with tree-sitter-php, resolving
every class name with `_php_fqn_from_raw`. It adds one node per route definition
(`metadata.kind == "laravel_route"`; label `GET /branches/{branch}`; metadata `verbs`,
full `uri` after group prefixes, `name` after group `as`/`name()` prefixes, `middleware`
as written after group inheritance minus `withoutMiddleware`, `abilities` from `can:`,
`action`, `partial`), contained by its route file, and two edge relations sourced at the
route: `handled_by` to the controller method (own, trait or in-corpus ancestor; a method
only a vendor class could declare gets no edge), the closure node, or the component class
of a full-page Livewire route, and `uses_middleware` to in-corpus middleware classes.
`EXTRACTED` means the route statement names the target; `INFERRED` marks what a
convention or a config table supplies: `Route::resource` expansion, and middleware
reached through an alias, a group (`web`, `api`, `$middlewareGroups`, `group()`) or the
global stack (`via` names which). Group context is inherited through closures,
`->group(__DIR__.'/x.php')`, `require`, `loadRoutesFrom`, `bootstrap/app.php`
`withRouting(web:, api:, then:)` and route service providers; an inheritance chain that
cannot be read as literals, or a route file nothing loads, still yields the route with
`partial: true` and a `partial_reason`. Filament panel providers become one
`filament_panel` node with `uses_middleware` edges for `->middleware()` and
`->authMiddleware()`. `route_name_index(nodes)` maps a route name to its node ids.
Not modelled: `Route::macro`/package-registered routes, resource
`shallow()`/`parameters()` (flagged `partial`), `creatable()`/`destroyable()`
singletons, `Route::group` string `namespace` beyond a literal, a route file included
from two groups (the first context wins), and Filament resource/page routes.

Order of the Laravel passes: `laravel_routes` first (route nodes), then, still inside
that resolver, the view/Blade/Livewire pass (it reads route metadata such as the `view` of
`Route::view`), then the second registry resolver `laravel_web`. Both later passes only
read route nodes, so both run after the routes exist. The two passes never emit the same
edge: view passes own `renders` to Blade files plus Blade-to-Blade/component/Livewire
edges, `laravel_web` owns `renders` to Inertia page files and `calls_route` /
`references_route`. The Blade text scan of `laravel_web` (`route('x')`) resolves the
Blade file through the same file node the view pass uses, so it adds no stub nodes.

`laravel_web` (`laravel/web.py`, same Laravel gate, runs after `laravel_routes` because it
needs the route nodes), links Laravel to Inertia/Vue:

- **Inertia pages** (`inertia.py`). `Route::inertia('/x', 'P', [...])` is a route node
  (`route_kind: inertia`, `inertia_page`, `inertia_props`). The page root is read from
  the `resolve:` option of the app entry (`resources/js|ts/{app,ssr,main,index}.*`:
  `resolvePageComponent(\`./Pages/${name}.vue\`, import.meta.glob('./Pages/**/*.vue'))`,
  `import(\`./Pages/${name}.vue\`)`); with no readable mapping the roots are
  `resources/{js,ts}/{Pages,pages}`. A literal name in `Inertia::render('P')`,
  `inertia('P')`, `inertia()->render('P')` or a route node becomes a `renders` edge
  (`EXTRACTED`) from the enclosing method, closure (Fortify `loginView(fn () => ...)`)
  or route node to the page *file* node. A dynamic name, a name no file matches, or one
  two files match (`About.vue` and `About.js`) gets no edge.
- **Props contract.** The edge carries `props` (literal keys of the array,
  `compact('a')`, `array_merge`, `->with()`) and `props_partial` when any part was not
  spelled out. The page node gets `inertia_page`, `inertia_props_passed`, and, when it
  declares props (`defineProps` object/array/type literal/same-file `interface` or
  `type`, `withDefaults`, Options-API `props`), `inertia_props_declared`,
  `inertia_props_undeclared` (passed, not declared) and `inertia_props_unused`
  (declared, never passed; skipped when a render call was partial, and `errors` plus
  the keys of `share()` of a class extending `Inertia\Middleware`, recorded as
  `inertia_shared_props` on that class, are not counted). Metadata only, never edges.
- **Named routes** (`ziggy.py`). A string-literal name in `route('x')` (Vue `<script>`
  via the AST, Vue template text, `.js`/`.ts`), PHP `route()`/`to_route()`/
  `redirect()->route()`/`Redirect::route()`/`URL::route()` and Blade text becomes a
  `calls_route` edge to the route node(s) with that name (`route_name_index`);
  `route().current('x')` becomes `references_route`. The source is the enclosing named
  function/method/closure when the graph has a node for it, else the file (a template
  usage belongs to the component). A name defined twice links every definition as
  `INFERRED` with `ambiguous`. Unknown names, computed names, `current('a.*')`
  wildcards, `$request->route('param')` and comments get no edge.

Edges are emitted for files extracted in the current run; the page contract reads every
rendering PHP file. Not modelled: pages resolved through a custom resolver function,
`Inertia::render` through a variable component, props passed through a variable
or `->toArray()`, React/Svelte page props, `ziggy` generated files, and auto-registered
(unimported) Vue components. Template-only component usage needs no pass: a template
component is always imported in `<script>`, which the JS import edges already cover.

JS side, independent of Laravel: a `}` and its `from '...'` on separate lines
(`export {\n  default as Tabs\n}\nfrom './Tabs.vue'`) is joined byte-for-byte before parsing
(tree-sitter ends the statement at `}`), and a named import through a barrel's
`export { default as X } from './X.vue'` resolves to the `.vue`/`.svelte` file node
when the barrel exports that name from exactly one module.

Laravel views, Blade and Livewire are modelled by the same package (`laravel/views.py`,
`viewcalls.py`, `blade.py`, `livewire.py`, driven by `viewpass.py` at the end of the
routes resolver; same gate, same scan root). One resolver maps a view name to the Blade
file node: `resources/views` plus literal `config/view.php` paths, `ns::a.b` through
`loadViewsFrom` / `View::addNamespace|prependNamespace|replaceNamespace` with literal
paths (a published `resources/views/vendor/<ns>/` wins), anonymous components
(`components/a/b.blade.php`, `a/b/index.blade.php`, `a/b/b.blade.php`,
`Blade::anonymousComponentPath(path[, prefix])`, `<x-ns::a>`), class components
(`App\View\Components\A\B`, `Blade::component`, `Blade::componentNamespace`; a class beats
an anonymous view) and view-name helpers recognised from their body
(`function theme_view($view, $area) { return "theme-{$area}::{$view}"; }`). A name that
is dynamic, matches no file, or matches several distinct files gets no edge, and a namespace
registered once with a non-literal path is never resolved. Runtime-decided locations (a
theme system's `replaceNamespace("theme-{$area}", ...)`) are declared by the project in
`.graphifyrc` (`laravel.view_namespace.<ns>=dir[,dir]`, `laravel.component_path[.prefix]=dir`;
first listed directory wins) and the edges that use them are `INFERRED` with
`via: "graphifyrc"`. Edges: blade file -> blade file `extends` / `includes` (`@include` and
its `If/When/Unless/First` forms, `@each`) / `uses_component` (`@component`); blade file ->
component class or anonymous component file `uses_component` (`<x-name>`); blade file ->
Livewire class or single-file component `uses_component` (`<livewire:name>`,
`@livewire('name' | \Fq\Class::class)`); PHP callable (or route node for `Route::view`) ->
blade file `renders` (`view()`, `View::make`, `view()->make`, `->view()` / `->markdown()`,
Livewire's `->layout('x')`, `loadView`, `new Content(view:|markdown:|html:|text:)`, `$view` properties and view-name
helper calls; literal names only); Livewire class -> its view `renders` (`EXTRACTED` when
`render()` returns one literal view, `INFERRED` for the `livewire.<name>` convention of a
class without `render()`); blade file -> Livewire method `binds_method` for
`wire:click|submit|...="save"` (arguments stripped; `$refresh`/`$set`/expressions give
nothing) and `$wire.save()`, only when exactly one component owns the view (`INFERRED`
when ownership came from the convention). A Livewire class is a class whose `extends`
reaches `Livewire\Component`; its names are `Livewire::component` registrations plus
`App\Livewire`-relative kebab-cased paths (a class elsewhere has none). Livewire 4
single-file components (`⚡name.blade.php`) become a `livewire_component` node with
`method` edges. Section/yield/stack names and `wire:model` properties are metadata on the
Blade file node (`metadata.blade`, `metadata.livewire`), not nodes. The legacy Blade
extractor's fake stub targets are flagged (`_drop`) and removed by `extract()` for every
Blade file of a Laravel project; outside one the old output is unchanged. Not modelled:
the `#[Layout]` attribute, `@includeFirst` beyond its first element, Volt functional
components, multi-file Livewire components, `Fortify::*View('name')`,
`Mail::send('view', ...)`, a class constant in `@livewire(...)` imported by `@php use`,
partials included from a Livewire view (they get no owner), `module_path()`-style path
helpers, a theme view that exists in two themes without a `.graphifyrc` choice, and a
component name defined by both a class and a single-file component (ambiguous, no edge).

The Laravel data layer is a registry resolver (runs after `laravel_web`), `laravel_data` (`laravel/data.py`, same
gate and scan root; readers in `eloquent.py`, `migrations.py`, `validation.py`, `filament.py`,
`inflect.py`). Which classes are Eloquent models, FormRequests or Filament resources comes from
the resolved `inherits` edges (an in-corpus chain ending in an FQN-labelled external stub such
as `Illuminate\Database\Eloquent\Model`, `Illuminate\Foundation\Auth\User`, `...\Relations\Pivot`);
every class written in a body goes through `PhpFile.class_fqn` (`_php_fqn_from_raw`) and
`SymbolIndex.resolve_class`, so a name that is not exactly one in-corpus declaration gets no edge.
* Relations: a relation method (`return $this->hasMany(X::class, ...)`, first call on `$this`)
  yields `eloquent_<snake type>` (`eloquent_has_one`, `_has_many`, `_belongs_to`, `_belongs_to_many`,
  `_has_one_through`, `_has_many_through`, `_morph_one`, `_morph_many`, `_morph_to_many`,
  `_morphed_by_many`; `morphTo` has no static target) from the relation method node to the related
  class (`EXTRACTED`, `eloquent_type`, `method`, literal keys such as `foreign_key`, `pivot_table`),
  `eloquent_through` / `eloquent_pivot` to the intermediate / `->using()` model, and the method node
  gets `metadata.eloquent_relation`. The graph keeps one edge per node pair, so the model -> model
  summary edge (same relation names) names the pair by its first relation in source order and lists the
  others (an inverse `hasMany`, a through relation) in `also`; the method-level edges lose nothing.
* `casts_to` model -> class for `$casts` / `casts()` entries `'attr' => X::class` (`attributes`);
  primitives (`'datetime'`) are model metadata (`metadata.eloquent.casts`) only.
* `uses_model` calling method / closure -> model for `Model::where()` style static calls whose
  method no in-corpus model, ancestor or trait declares (`methods`); `self::`/`static::` and calls
  the call resolver already binds are not repeated. Related Phase-0 `references_constant` edges on a
  pair a typed edge covers in the same direction are superseded (`_drop`) at extraction; the opposite
  direction and incremental order are handled by `build.py` (see "One edge per node pair").
* Migrations: every `Schema::create|table|drop|dropIfExists|rename` in an `up()` of a
  `migrations/*.php` file is replayed in file-name order onto one `table:<name>` node per table
  (label = name, `metadata.kind == "db_table"` with `columns`, `foreign_keys`, `migrations`), contained by
  the migration that creates it; `alters_table` from the others. `foreign_key_to` table -> table:
  `references()->on()` / `constrained('t')` `EXTRACTED`, `constrained()` (plural of the column without
  `_id`) and `foreignIdFor(X::class)` `INFERRED` (`via`). A non-literal table name, raw SQL and
  composite keys are not modelled.
* `maps_to_table` model -> table node: `protected $table` (own or in-corpus ancestor) `EXTRACTED`,
  otherwise the naming convention `Str::plural(Str::snake(basename))` `INFERRED`, only when a migration
  defines the table. No convention for abstract models, Pivots or a `getTable()` override. The
  pluraliser (`inflect.py`) ports Doctrine's rule order; irregular words outside its lists are wrong,
  which is why the convention edge is `INFERRED`.
* Validation: in `rules()` of a FormRequest / Livewire component and in `->validate([...])`,
  `Validator::make`, `validator()`: `'exists:t,col'`, `'unique:t'` (also `connection.table` and a model
  FQCN), `Rule::exists|unique('t' | X::class)` -> `validates_table` to the table node (`fields`,
  `rules`; the model route is `INFERRED` when the model's table is by convention); `Rule::enum(X::class)`,
  `new Enum(X::class)` and `new MyRule(...)` -> `validates_with` to the in-corpus class (`rule_kind`).
  A string cut by a variable is read up to the dynamic part; a table name it may have shortened is dropped.
* Filament: a class reaching `Filament\Resources\Resource` -> `filament_resource_for` (its
  `$model`), `filament_page` (`getPages()` entries, `Pages\ListX::route('/')` or `::class`, with `keys`
  and `route`) and `filament_relation_manager` (`getRelations()`, also inside `RelationGroup::make`).
Not modelled: relation methods in traits, relations built from variables or `$this->related()`,
Livewire `#[Validate]` attributes, `Rule::when`/macro rules, Filament `getModel()` and cluster
registration, `Schema` calls in non-`migrations/` files, and a table name stored in config.

Laravel runtime wiring (`laravel/runtime.py`, `console.py`, `dispatch.py`, `container.py`,
helpers in `phpread.py`; registry resolver `laravel_runtime`, same gate, runs after
`laravel_data`) models what the framework connects at run time. Class names go through
`PhpFile.class_fqn` (`_php_fqn_from_raw`) and `SymbolIndex.resolve_class`; whether a class is a
job, an event or a command is read from its own source (`extends` / `implements` / trait
`use`, resolved the same way), never from the graph's `inherits` edges, so an incremental
rebuild sees the same answer. A vendor ancestor (`ShouldQueue`, `Dispatchable`) is judged by its
FQN, which is why `X::dispatch()` works even though the trait it comes from does not resolve.

- **Commands and scheduler** (`console.py`). The class node of a command gets
  `command_name` / `command_signature` / `command_source` metadata (`$signature` before the
  first argument or option, else `$name`, else `#[AsCommand(name:)]`; the class must extend
  something named `*Command` or carry the attribute). `Artisan::command('sig', fn)` is a
  `laravel_command` node with a `handled_by` edge to its closure. One `laravel_schedule` node
  per `Schedule::command|job|call` statement (facade, `$schedule` parameter typed `Schedule`,
  or a variable named `$schedule`; `Console\Kernel::schedule`, `routes/console.php`,
  `withSchedule`), with `frequency` (the chain as written), `options`, and `cron` (the literal
  of `->cron('...')`, or derived from `daily()` / `dailyAt('13:00')` / `everyFiveMinutes()` /
  `weekdays()` ... with `cron_source: literal|derived`; no `cron` when the chain holds a method
  the table does not know). `schedules` goes entry -> command class, closure command, job class or
  closure; a name no in-corpus command declares (vendor), or that two declare, leaves the entry
  with `unresolved` and no edge. `invokes_command`: method/closure -> command for
  `Artisan::call|queue('sig' ...)` and `$this->call|callSilent('sig' | X::class)`.
- **Jobs and events** (`dispatch.py`). `dispatches`: caller -> job for `X::dispatch|dispatchSync|
  dispatchNow|dispatchAfterResponse|dispatchIf|dispatchUnless`, `dispatch(new X)`,
  `dispatch_sync/now`, `Bus::dispatch(new X)`, `Bus::chain|batch([...])` (nested arrays),
  `->chain([...])`, `X::withChain`, `$this->dispatch(new X)` and `Queue::push`. A static
  `X::dispatch()` needs the class (or an in-corpus ancestor / trait) to list the Bus
  `Dispatchable`, `ShouldQueue` or `Queueable`, and the corpus not to define `dispatch` itself;
  a class under `Jobs/` that has some vendor ancestor and calls an unseen `dispatch` is
  `INFERRED` (`via: jobs-directory`). `dispatches_event`: `event(new E)`, `broadcast`,
  `Event::dispatch|until`, `E::dispatch()` with the Events `Dispatchable`. `notifies`
  (`->notify(new N)`, `Notification::send`), `sends_mail` (`Mail::...->send|queue|later(new M)`).
  `listens_to`, listener or closure -> event: `Event::listen(E::class, L::class | [L::class, 'm'] |
  'L@m' | closure)` and `Event::listen(fn (E $e) => ...)`, `$listen` / `events()` / `listens()` of a
  `*ServiceProvider` (`JobPipeline::make([J::class, ...])` makes each job the listener),
  `subscribe(Dispatcher $events)` bodies, `#[ListensTo]`; `EXTRACTED`. Laravel's auto-discovery
  (`handle*` / `__invoke` first parameter typed with the event, in a class under `app/Listeners`)
  is `INFERRED` (`via: auto-discovery`) and skipped where a registration already pairs the two.
  A vendor event is the external stub node of the imported class (no stub, no edge).
  `observes`, observer -> model: `Model::observe(O::class | [..] | new O)` (also `static::`),
  `#[ObservedBy]`.
- **Container** (`container.py`). `binds`: `$this->app|app()|App::` `bind|singleton|scoped|instance`
  (and `*If`) with `A::class` and a concrete given as `B::class`, `fn () => new B`,
  `function () { return $app->make(B::class); }`, plus `$bindings` / `$singletons`; `EXTRACTED`.
  The source is the abstract (in-corpus class, else the external stub of the imported vendor
  class), or the registering method when the abstract is a string alias; the edge says
  `abstract`, `binding`, `conditional` (inside an `if`) and `alternatives` (the abstract has other
  concretes or an unreadable binding). Self-bindings and closures that do more than create one
  class give no edge. `facade_for`: a class declaring `getFacadeAccessor()` -> the class it
  returns (`EXTRACTED`), -> the one concrete a string accessor is bound to (`EXTRACTED`), and a
  second edge to the one concrete an abstract accessor is bound to (`INFERRED`, `via: binding`).
- **Graph collapse.** See "One edge per node pair" below: `build.py` keeps the runtime edge over
  a `Foo::class` mention, and `binds` over `implements` / `inherits` / `mixes_in`.

Not modelled: string event names (`Event::listen('user.created', ...)`), `dispatch($variable)`,
model `$dispatchesEvents`, `withEvents(discover: [...])` directories other than `app/Listeners`,
contextual bindings (`when()->needs()->give()`), `extend` / `alias`, bindings registered by
packages, a `$schedule` that is not typed or named `schedule`, `Schedule::exec`, schedule
frequencies the cron table does not know (the entry keeps `frequency`, no `cron`),
`Artisan::call(X::class)`, `$aliases` of a command, and resolving a call on an interface-typed
property to the bound concrete method (the typed-receiver edge still points at the contract; the
`binds` / `facade_for` edges are what link it to the concrete class).

Laravel authorization and keys (`laravel/authz.py`, `keys.py`, driven by `authzkeys.py`;
registry resolver `laravel_authz_keys`, same gate, runs after `laravel_web` (and last of the Laravel passes) because it reads the route
nodes). Nodes: `laravel_ability` (label `ability:create-branches`, id = slug + hash of the name),
`laravel_config_key` (`config:app.name`), `laravel_translation_key` (`lang:messages.welcome`).
- **Abilities.** A node exists for a literal `Gate::define('x', ...)` (also `Gate::resource`,
  which defines `res.view` etc. for the policy methods that exist) and for a name some check uses that
  equals the string value of exactly one in-corpus enum case (spatie-style `PermissionEnum`; the link
  ability -> case is `references_constant`, `INFERRED`, rule in the edge). `Gate::define($dynamic, ..)`
  and abilities nothing defines get no node. `handled_by` (`EXTRACTED`) goes to the closure, the
  `[C::class, 'm']` / `'C@m'` / invokable method; a closure the extractor has no node for gets none.
  `checks_ability` (`EXTRACTED`, the name is written in the source) goes from the enclosing method /
  closure (the file for Blade) to the ability for `Gate::allows|denies|check|authorize|inspect|any|none`
  (also through `Gate::forUser()`), `->can|cannot|cant|canAny()` on any non-`Route` receiver,
  `$this->authorize()`, `$this->middleware('can:x')` and Blade `@can|@cannot|@canany|@elsecan` /
  `->can()` text; from a route node for the `can:` entries of its effective middleware (`can:x`,
  `->can('x')`). The argument may be a literal, `Enum::CASE->value` or `Enum::CASE`. `Gate`, `Config` and
  `Lang` count only when the written name resolves (PHP name rules) to the Laravel facade or the global
  alias.
- **Policies.** `policy_for` (policy class -> model): `Gate::policy(M::class, P::class)`, the
  `$policies` array and `#[UsePolicy(P::class)]` are `EXTRACTED` (two different policies for one model
  = none); Laravel's `guessPolicyName` convention (deepest `...\Policies\XPolicy` that exists) is
  `INFERRED`. When a check names the model statically (`X::class`, `new X`, a typed parameter of the
  enclosing function; Blade only a qualified `Fq\X::class`; route `can:ability,Fq\X`) and the policy
  declares (or inherits) the ability as a method, `checks_ability` goes to that policy method
  (`INFERRED`, `via` = how the policy was found) instead of an ability node. An unknown model, a model
  without a policy or a missing method gives nothing.
- **Config keys.** `config/**/*.php` and files registered with `$this->mergeConfigFrom(literal path,
  'key')` give one node per literal leaf (`app.name`), contained by the file; a list (no string keys), or
  an array nested deeper than 4 levels, is a leaf; numeric/computed keys are skipped; `env('X', d)`
  names go to `metadata.env` / `env_default`. `config('a.b')`, `config([..])` (`access: set`),
  `Config::get|string|...`, `config()->get()`, `app('config')->get()` and Blade `config('a.b')` give
  `reads_config`: `EXTRACTED` to the exact leaf, `INFERRED` (`resolved_as: parent`) to the nearest leaf
  above the key (`config('app.providers.0')`), `INFERRED` (`resolved_as: file`) to the config file for
  a key naming a whole branch of one file. Two files defining one key = every definition, `ambiguous`.
  One edge per (usage, key): repeated reads collapse onto the first.
- **Translation keys.** `{lang,resources/lang}/<locale>/<group>.php` (nested arrays to `group.a.b`)
  and `loadTranslationsFrom(literal path, 'ns')` (`ns::group.key`) give one node per key across all
  locales (`metadata.locales`, `missing_locales`; source file = the `en` file, else the first locale),
  JSON lang files (`lang/<locale>.json`) one node per string. A key gets a node only when some usage
  reads it: defining every string added +40% nodes on two of the six measured apps (the
  lang files hold more strings than the code reads), so unused keys stay out of the graph (the
  pass logs `translation_keys_defined` / `translation_keys_used`). `__()`, `trans()`, `trans_choice()`,
  `Lang::get|choice|has`, Blade `@lang` give `uses_translation` (`EXTRACTED`); JSON keys win over
  `group.key` as in Laravel. A `group.key`-shaped name no file defines is not linked; the usage file
  records it in `metadata.missing_translation_keys` (and a count; free text such as `__('Save')` is just a
  default-locale string and is not reported). Keys of an array branch (`__('messages.errors')`) are not
  missing and get no edge.
- Edges and nodes are emitted for files extracted in this run, like the route pass; the indexes read every
  file. Not modelled: `Gate::before/after`, `authorizeResource`, `permission:`/`role:` middleware
  (spatie), abilities from `$user->hasPermissionTo()`, class constants as ability names, a model passed
  through a property or a call, `lang/vendor/` overrides, JSON translations of package paths,
  `module_path()` / `config_path()` style paths in `mergeConfigFrom`, `config()` keys built at runtime,
  config values returned by `array_merge(...)` and Vue/JS `$t()` calls.

**Final order of the Laravel passes.** `laravel_routes` (route nodes, then the view/Blade/Livewire
pass inside it) -> `laravel_web` -> `laravel_data` -> `laravel_runtime` -> `laravel_authz_keys`
(`extract.py` registration order). Only `laravel_web` and `laravel_authz_keys` read the route
nodes, so they must come after the routes; `laravel_data` and `laravel_runtime` read class and
method nodes plus source files and are independent of each other and of the authz pass. They never
emit the same relation, with one overlap: `laravel_authz_keys` links an ability to an enum case with
`references_constant`, a pair neither of the others touches.

**Incremental rebuilds.** Every Laravel pass emits an edge only for the files it was handed
(`fresh`), but the edges join files through names declared elsewhere (`route('x')` in a Vue file
and the route named `x`; `Schedule::command('sig')` and the command declaring `sig`; a model and the
migration that creates its table; `config('a.b')` and the config file). A hook rebuild
(`_rebuild_code(changed_paths=...)`) re-extracts only the changed files, which left an unchanged
file's edge stale after its target was renamed and missing after the target appeared.
`laravel.project.widen_incremental_targets` closes that: when a changed or deleted file is PHP, Blade,
Vue or JS/TS in a Laravel project, `extract_targets` becomes every such file of the project (the
per-file AST cache makes the unchanged ones cheap), so the passes run over the same input as a
`--force` build and the old Laravel edges of every one of those files are replaced by reconcile's
ordinary source-file ownership. A change to anything else (Markdown, JSON) and any non-Laravel
project keep the exact changed-files-only behaviour. `tests/test_laravel_incremental.py` compares the
incremental graph with a fresh full build. Not covered: a changed `lang/*.json` or `composer.json`
alone (not code files, so the rebuild is skipped as before).

**One edge per node pair.** The built graph keeps one edge per node pair, and the PHP extractor
adds a plain `references_constant` for every `Foo::class` mention, so two facts about one pair
would otherwise be decided by edge sort order (relation name). Two places handle it, and they
agree on the rule "the wiring edge stays, the plain mention is noted, nothing is lost":

- At extraction, `laravel_data` marks a same-direction `references_constant` that a typed
  `eloquent_*` / `uses_model` / validation / Filament edge covers with `_drop` (it carries the
  mention's line already), and its model -> model summary edge names a pair by its first relation
  and lists the others in `also`.
- In `build_from_json` (`build.py`), `_outranks_class_mention` covers every wiring relation:
  the runtime ones (`schedules`, `invokes_command`, `dispatches`, `dispatches_event`, `notifies`,
  `sends_mail`, `listens_to`, `observes`, `binds`, `facade_for`) and the data ones (`eloquent_*`,
  `filament_*`, `casts_to`, `uses_model`, `maps_to_table`, `foreign_key_to`, `validates_table`,
  `validates_with`). Against `references_constant` on the same pair - in either direction and
  whichever arrives first - the wiring edge is kept and `references_constant` is added to its
  `also_relations`. A `binds` edge likewise beats `implements` / `inherits` / `mixes_in` on its
  pair and records the type link in `also_relations`. This covers what extraction cannot: the
  reverse direction (`Branch` mentions `Client::class` while `Client` -> `Branch` is
  `eloquent_has_many`), and the runtime edges, which the data pass does not see.
