# graphify for Laravel

This fork adds a Laravel layer to graphify. On a Laravel project the graph now holds the framework's own wiring:

- routes, middleware and abilities
- views, Blade components, Livewire and Inertia pages
- Eloquent relations, models, tables and validation rules
- jobs, events, the scheduler and container bindings
- gates and policies, config keys and translation keys

The original graphify saw a Laravel app as plain PHP classes, so almost all of this was missing.

**Results on 6 real Laravel apps (before / after, with every number measured):**
https://compertme.github.io/graphify/

- Code: this repository, branch [`laravel`](https://github.com/CompertMe/graphify/tree/laravel)
- Laravel layer source: [`graphify/extractors/laravel/`](graphify/extractors/laravel/)
- Upstream: generic PHP/JS fixes offered as Graphify-Labs/graphify PRs #4626–#4632, Laravel proposal #4633

---

## 1. Install the CLI (once per machine)

You need `git` and [`uv`](https://docs.astral.sh/uv/getting-started/installation/). uv brings its own Python 3.10+.

```bash
uv tool install --force "git+https://github.com/CompertMe/graphify@laravel"
```

Windows (PowerShell) uses the same command. With pipx instead of uv:

```bash
pipx install --force "git+https://github.com/CompertMe/graphify@laravel"
```

Check that the Laravel build is the one installed (macOS/Linux, uv):

```bash
"$(uv tool dir)/graphifyy/bin/python" -c "import graphify.extractors.laravel; print('graphify Laravel layer: OK')"
```

> **Install this build before you use `/graphify` in any AI assistant.** The graphify skill
> installs the official package from PyPI by itself when it finds no graphify on the machine,
> and that package has no Laravel layer. If that happened, run the install command above again.

### Update to the latest version

```bash
uv tool install --force --refresh "git+https://github.com/CompertMe/graphify@laravel"
```

`--refresh` matters: without it uv can reuse its cached copy of the branch.

---

## 2. Set up a project (once per project)

Run everything from the **Laravel root**, the folder that contains `artisan`. The Laravel layer turns on only when the scanned folder has `artisan` or a `composer.json` that requires `laravel/framework`. Scanning a parent folder or a sub-folder gives the plain PHP graph.

### Connect your AI assistant

Pick the line for the tool you use. `--project` writes the files into the repository, so the whole team gets them.

| Assistant | Command |
|---|---|
| Claude Code | `graphify install --project` |
| Cursor | `graphify cursor install` |
| Codex | `graphify install --platform codex --project` |
| Gemini CLI | `graphify install --platform gemini` |
| GitHub Copilot CLI | `graphify install --platform copilot` |
| VS Code Copilot Chat | `graphify vscode install` |
| Any agent that reads `.agents/skills` | `graphify install --platform agents --project` |

Each one writes the instruction file its tool reads, for example:

- Claude Code: `.claude/skills/graphify/`, `CLAUDE.md` and search hooks
- Cursor: `.cursor/rules/graphify.mdc`
- Codex: `AGENTS.md`

### Build the graph

```bash
graphify update .
```

This parses the code locally. It makes no LLM calls and sends no data out. It writes three files to `graphify-out/`:

- `graph.json`: the graph
- `graph.html`: open it in a browser
- `GRAPH_REPORT.md`: a summary

Add the cache folder to `.gitignore`:

```gitignore
/graphify-out/cache
```

Inside an assistant you can also type `/graphify .`. That additionally runs an optional semantic pass over docs and images, using your assistant's model.

### Keep it current

- The Claude Code hooks (and `graphify hook install` git hooks) rebuild after changes. An incremental rebuild of a Laravel project gives the same Laravel graph as a full one.
- To force a full rebuild: `graphify update . --force`.

---

## 3. Ask the graph

```bash
graphify explain "POST /branches"          # a route: controller method, middleware, abilities, callers
graphify explain "BranchController"        # a class and everything connected to it
graphify path "GET /holidays" "Holidays/Index.vue"
graphify query "which jobs run after an invoice is created?"
```

If a name matches several nodes, `explain` lists the ids; pass one of them instead.

Relations the Laravel layer adds:

| Area | Relations |
|---|---|
| Routing | `handled_by` (route → controller method / Livewire / closure), `uses_middleware`, `checks_ability` |
| Views | `renders` (PHP → Blade or Inertia page), `extends`, `includes`, `uses_component`, `binds_method` (`wire:*` → component method) |
| Front end | `calls_route`, `references_route` (`route('name')` in Vue/JS/Blade/PHP → route) |
| Data | `eloquent_has_many`, `eloquent_belongs_to`, … (one per relation type), `casts_to`, `uses_model`, `maps_to_table`, `foreign_key_to`, `validates_table`, `validates_with`, `filament_resource_for`, `filament_page`, `filament_relation_manager` |
| Runtime | `dispatches`, `dispatches_event`, `listens_to`, `observes`, `schedules`, `invokes_command`, `notifies`, `sends_mail`, `binds`, `facade_for` |
| Access and keys | `checks_ability`, `policy_for`, `reads_config`, `uses_translation` |

Edges read straight from source are `EXTRACTED`. Edges that rely on a Laravel convention, such as a table name from the model name or policy and listener discovery, are `INFERRED`.

---

## 4. Views decided at run time (`.graphifyrc`)

Some apps pick view folders at run time. A theme system calling `View::replaceNamespace(...)` is the usual case, and graphify cannot read that from the source. Declare the mapping in `.graphifyrc` at the Laravel root:

```ini
# namespace -> directories (root-relative, comma separated, first match wins)
laravel.view_namespace.theme-app=resources/themes/app/default/resources/views
laravel.view_namespace.theme-guest=resources/themes/guest/default/resources/views
# anonymous component directories: <x-name> (no prefix) and <x-prefix::name>
laravel.component_path=resources/views/components
laravel.component_path.layout=resources/themes/app/default/resources/views/components/layout
```

Edges resolved through this file are marked `INFERRED` with `via: graphifyrc`. Most projects don't need it.

---

## 5. For AI agents (copy this into any assistant)

```text
Set up graphify with Laravel support for this project.
1. If `graphify` is missing or is not the Laravel build, install it:
   uv tool install --force --refresh "git+https://github.com/CompertMe/graphify@laravel"
   Do NOT install the PyPI package "graphifyy": it has no Laravel layer.
   Verify: "$(uv tool dir)/graphifyy/bin/python" -c "import graphify.extractors.laravel"
2. cd to the Laravel root (the folder with `artisan`).
3. Connect this assistant with the matching command:
   Claude Code: graphify install --project · Cursor: graphify cursor install ·
   Codex: graphify install --platform codex --project · other agents: graphify install --platform agents --project
4. Build: graphify update .   and add /graphify-out/cache to .gitignore.
5. Before reading code to answer a question about this project, query the graph first:
   graphify explain "<route, class or page>", graphify path "<A>" "<B>", graphify query "<question>".
   Read source files only for the lines the graph points to.
6. After changing code, run: graphify update .
```

---

## Limits

- Only literal names are followed. Dynamic view, page, route or table names and `$variable::class` are skipped by design.
- A Blade `@include` that only resolves through a directory added at run time stays unresolved unless `.graphifyrc` maps it.
- Package-registered routes (Fortify, Passport) are not modelled as routes.

Full details: [ARCHITECTURE.md](ARCHITECTURE.md), Laravel sections.
