"""Laravel route modelling: route nodes, handled_by and uses_middleware edges.

The fixture project is tests/fixtures/laravel_app (a mini Laravel 11 app covering
every route form); tests/fixtures/laravel_plain_php is a PHP project that merely
contains a ``Route::get`` call and must come out exactly as before.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_php")

from graphify import resolver_registry  # noqa: E402
from graphify.extract import collect_files, extract  # noqa: E402
from graphify.extractors.laravel import ROUTE_KIND, route_name_index  # noqa: E402
from graphify.extractors.laravel.routes import join_path, singularize  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
APP = FIXTURES / "laravel_app"
PLAIN = FIXTURES / "laravel_plain_php"

NO_PARTIAL = None
ORPHAN_REASON = "route file is not loaded by any known loader"

# (source_file, line, label, route name, route_kind, partial reason)
ROUTES = [
    ("app/Providers/AdminPanelProvider.php", 15, "ANY /cp/* (Filament panel admin)", None, "filament_panel", NO_PARTIAL),
    ("modules/Blog/Routes/web.php", 7, "GET /blog/articles", "blog.articles.index", "controller", NO_PARTIAL),
    ("routes/api.php", 7, "GET /api/users", "users.index", "resource", NO_PARTIAL),
    ("routes/api.php", 7, "GET /api/users/{user}", "users.show", "resource", NO_PARTIAL),
    ("routes/api.php", 8, "DELETE /api/tags/{tag}", "tags.destroy", "resource", NO_PARTIAL),
    ("routes/api.php", 8, "GET /api/tags", "tags.index", "resource", NO_PARTIAL),
    ("routes/api.php", 8, "GET /api/tags/{tag}", "tags.show", "resource", NO_PARTIAL),
    ("routes/api.php", 8, "POST /api/tags", "tags.store", "resource", NO_PARTIAL),
    ("routes/api.php", 8, "PUT|PATCH /api/tags/{tag}", "tags.update", "resource", NO_PARTIAL),
    ("routes/api.php", 9, "GET /api/ping", "ping", "closure", NO_PARTIAL),
    ("routes/extra.php", 6, "GET /extra/pages", "extra.pages", "controller", NO_PARTIAL),
    ("routes/included.php", 6, "GET /inc/page", "inc.page", "controller", NO_PARTIAL),
    ("routes/included.php", 9, "GET /inc/deep/shared", "inc.shared", "controller", NO_PARTIAL),
    ("routes/orphan.php", 7, "GET /orphan", "orphan", "controller", ORPHAN_REASON),
    ("routes/required.php", 6, "GET /required", "required", "controller", NO_PARTIAL),
    ("routes/rsp.php", 6, "GET /rsp/ping", "rsp.ping", "controller", NO_PARTIAL),
    ("routes/web.php", 15, "GET /", "home", "controller", NO_PARTIAL),
    ("routes/web.php", 16, "POST /branches", "branches.store", "controller", NO_PARTIAL),
    ("routes/web.php", 17, "GET /branches/{branch}", "branches.show", "controller", NO_PARTIAL),
    ("routes/web.php", 18, "GET /health", None, "invokable", NO_PARTIAL),
    ("routes/web.php", 19, "GET /closure", "closure", "closure", NO_PARTIAL),
    ("routes/web.php", 22, "GET /arrow", None, "closure", NO_PARTIAL),
    ("routes/web.php", 23, "GET /vendor-only", None, "controller", NO_PARTIAL),
    ("routes/web.php", 24, "GET /ghost", None, "controller", NO_PARTIAL),
    ("routes/web.php", 25, "GET /nowhere", None, "controller", NO_PARTIAL),
    ("routes/web.php", 26, "GET /welcome", "welcome", "view", NO_PARTIAL),
    ("routes/web.php", 27, "ANY /old", None, "redirect", NO_PARTIAL),
    ("routes/web.php", 28, "GET|POST /multi", None, "controller", NO_PARTIAL),
    ("routes/web.php", 29, "GET /dashboard", None, "component", NO_PARTIAL),
    ("routes/web.php", 30, "GET /live", "live", "livewire", NO_PARTIAL),
    ("routes/web.php", 33, "GET /pages", None, "controller", NO_PARTIAL),
    ("routes/web.php", 34, "GET /pages/{page}", "pages.show", "controller", NO_PARTIAL),
    ("routes/web.php", 37, "GET /posts", "posts.index", "resource", NO_PARTIAL),
    ("routes/web.php", 37, "GET /posts/create", "posts.create", "resource", NO_PARTIAL),
    ("routes/web.php", 37, "GET /posts/{post}", "posts.show", "resource", NO_PARTIAL),
    ("routes/web.php", 37, "POST /posts", "posts.store", "resource", NO_PARTIAL),
    ("routes/web.php", 37, "PUT|PATCH /posts/{post}", "posts.update", "resource", NO_PARTIAL),
    ("routes/web.php", 38, "GET /branches", "branches.index", "resource", NO_PARTIAL),
    ("routes/web.php", 38, "PUT|PATCH /branches/{branch}", "branches.update", "resource", NO_PARTIAL),
    ("routes/web.php", 39, "GET /profile", "profile.show", "resource", NO_PARTIAL),
    ("routes/web.php", 39, "GET /profile/edit", "profile.edit", "resource", NO_PARTIAL),
    ("routes/web.php", 39, "PUT|PATCH /profile", "profile.update", "resource", NO_PARTIAL),
    ("routes/web.php", 42, "GET /admin/dashboard", "admin.dashboard", "controller", NO_PARTIAL),
    ("routes/web.php", 44, "GET /admin/reports", "admin.reports.index", "controller", NO_PARTIAL),
    ("routes/web.php", 45, "GET /admin/reports/audit", "admin.reports.audit", "controller", NO_PARTIAL),
    ("routes/web.php", 46, "GET /admin/reports/shared", None, "controller", NO_PARTIAL),
    ("routes/web.php", 51, "GET /legacy/x", "legacy.x", "controller", NO_PARTIAL),
    ("routes/web.php", 55, "GET /tracked", None, "controller", NO_PARTIAL),
    ("routes/web.php", 60, "GET /dyn/{dynamic}", None, "controller", "dynamic uri"),
]

CONTROLLERS = "app/Http/Controllers"
# "file:line label" -> (handler label, handler file, confidence). Routes absent
# from this table have no handled_by edge: vendor-only method, class or method
# missing from the corpus, view, redirect, resource actions the controller lacks.
HANDLED = {
    "modules/Blog/Routes/web.php:7 GET /blog/articles": (".index()", "modules/Blog/Http/Controllers/ArticleController.php", "EXTRACTED"),
    "routes/api.php:7 GET /api/users": (".index()", f"{CONTROLLERS}/Api/UserController.php", "INFERRED"),
    "routes/api.php:7 GET /api/users/{user}": (".show()", f"{CONTROLLERS}/Api/UserController.php", "INFERRED"),
    "routes/api.php:8 GET /api/tags": (".index()", f"{CONTROLLERS}/Api/TagController.php", "INFERRED"),
    "routes/api.php:8 GET /api/tags/{tag}": (".show()", f"{CONTROLLERS}/Api/TagController.php", "INFERRED"),
    "routes/api.php:9 GET /api/ping": ("GET /ping()", "routes/api.php", "EXTRACTED"),
    "routes/extra.php:6 GET /extra/pages": (".index()", f"{CONTROLLERS}/PageController.php", "EXTRACTED"),
    "routes/included.php:6 GET /inc/page": (".index()", f"{CONTROLLERS}/PageController.php", "EXTRACTED"),
    "routes/included.php:9 GET /inc/deep/shared": (".shared()", f"{CONTROLLERS}/Controller.php", "EXTRACTED"),
    "routes/orphan.php:7 GET /orphan": (".home()", f"{CONTROLLERS}/PageController.php", "EXTRACTED"),
    "routes/required.php:6 GET /required": (".audit()", f"{CONTROLLERS}/Concerns/AuditsActions.php", "EXTRACTED"),
    "routes/rsp.php:6 GET /rsp/ping": (".home()", f"{CONTROLLERS}/PageController.php", "EXTRACTED"),
    "routes/web.php:15 GET /": (".home()", f"{CONTROLLERS}/PageController.php", "EXTRACTED"),
    "routes/web.php:16 POST /branches": (".store()", f"{CONTROLLERS}/BranchController.php", "EXTRACTED"),
    "routes/web.php:17 GET /branches/{branch}": (".show()", f"{CONTROLLERS}/BranchController.php", "EXTRACTED"),
    "routes/web.php:18 GET /health": (".__invoke()", f"{CONTROLLERS}/InvokableController.php", "EXTRACTED"),
    "routes/web.php:19 GET /closure": ("GET /closure()", "routes/web.php", "EXTRACTED"),
    "routes/web.php:22 GET /arrow": ("GET /arrow()", "routes/web.php", "EXTRACTED"),
    "routes/web.php:28 GET|POST /multi": (".multi()", f"{CONTROLLERS}/PageController.php", "EXTRACTED"),
    "routes/web.php:29 GET /dashboard": ("Dashboard", "app/Livewire/Dashboard.php", "EXTRACTED"),
    "routes/web.php:30 GET /live": ("Dashboard", "app/Livewire/Dashboard.php", "EXTRACTED"),
    "routes/web.php:33 GET /pages": (".index()", f"{CONTROLLERS}/PageController.php", "EXTRACTED"),
    "routes/web.php:34 GET /pages/{page}": (".show()", f"{CONTROLLERS}/PageController.php", "EXTRACTED"),
    "routes/web.php:37 GET /posts": (".index()", f"{CONTROLLERS}/PostController.php", "INFERRED"),
    "routes/web.php:37 GET /posts/create": (".create()", f"{CONTROLLERS}/PostController.php", "INFERRED"),
    "routes/web.php:37 GET /posts/{post}": (".show()", f"{CONTROLLERS}/PostController.php", "INFERRED"),
    "routes/web.php:37 POST /posts": (".store()", f"{CONTROLLERS}/PostController.php", "INFERRED"),
    "routes/web.php:37 PUT|PATCH /posts/{post}": (".update()", f"{CONTROLLERS}/PostController.php", "INFERRED"),
    "routes/web.php:38 GET /branches": (".index()", f"{CONTROLLERS}/BranchController.php", "INFERRED"),
    "routes/web.php:38 PUT|PATCH /branches/{branch}": (".update()", f"{CONTROLLERS}/BranchController.php", "INFERRED"),
    "routes/web.php:39 GET /profile": (".show()", f"{CONTROLLERS}/ProfileController.php", "INFERRED"),
    "routes/web.php:39 GET /profile/edit": (".edit()", f"{CONTROLLERS}/ProfileController.php", "INFERRED"),
    "routes/web.php:39 PUT|PATCH /profile": (".update()", f"{CONTROLLERS}/ProfileController.php", "INFERRED"),
    "routes/web.php:42 GET /admin/dashboard": (".index()", f"{CONTROLLERS}/Admin/DashboardController.php", "EXTRACTED"),
    "routes/web.php:44 GET /admin/reports": (".index()", f"{CONTROLLERS}/Admin/ReportController.php", "EXTRACTED"),
    "routes/web.php:45 GET /admin/reports/audit": (".audit()", f"{CONTROLLERS}/Concerns/AuditsActions.php", "EXTRACTED"),
    "routes/web.php:46 GET /admin/reports/shared": (".shared()", f"{CONTROLLERS}/Controller.php", "EXTRACTED"),
    "routes/web.php:51 GET /legacy/x": (".home()", f"{CONTROLLERS}/PageController.php", "EXTRACTED"),
    "routes/web.php:55 GET /tracked": (".index()", f"{CONTROLLERS}/PageController.php", "EXTRACTED"),
    "routes/web.php:60 GET /dyn/{dynamic}": (".home()", f"{CONTROLLERS}/PageController.php", "EXTRACTED"),
}

# (middleware class label, confidence, via). Every route in a web route file gets
# the `web` group's app member and the global stack; api route files the `api`
# group's. EXTRA lists what the route's own groups/modifiers add (or `withoutMiddleware` removes).
WEB_BASE = {("Locale", "INFERRED", "web"), ("GlobalStamp", "INFERRED", "global")}
API_BASE = {("ApiLog", "INFERRED", "api"), ("GlobalStamp", "INFERRED", "global")}
GLOBAL_ONLY = {("GlobalStamp", "INFERRED", "global")}
EXPLICIT = ("ExplicitMiddleware", "EXTRACTED", None)
ADMIN = ("EnsureAdmin", "INFERRED", "admin")
EXTRA_MIDDLEWARE = {
    "modules/Blog/Routes/web.php:7 GET /blog/articles": {ADMIN},
    "routes/included.php:6 GET /inc/page": {EXPLICIT},
    "routes/included.php:9 GET /inc/deep/shared": {EXPLICIT},
    "routes/web.php:29 GET /dashboard": {EXPLICIT},
    "routes/web.php:42 GET /admin/dashboard": {ADMIN},
    "routes/web.php:44 GET /admin/reports": {ADMIN},
    "routes/web.php:46 GET /admin/reports/shared": {ADMIN},
    "routes/web.php:51 GET /legacy/x": {
        ("TrackVisits", "INFERRED", "visits"),
        ("LegacyGroupMiddleware", "INFERRED", "legacy"),
    },
    "routes/web.php:55 GET /tracked": {
        ("TrackVisits", "INFERRED", "tracked"),
        ("KernelMiddleware", "INFERRED", "kernel.mw"),
    },
}


def _extract(root: Path) -> dict:
    root = Path(root).resolve()
    return extract(collect_files(root), root=root, parallel=False)


@pytest.fixture(scope="module")
def graph() -> dict:
    return _extract(APP)


def _by_id(result: dict) -> dict[str, dict]:
    return {n["id"]: n for n in result["nodes"]}


def _route_nodes(result: dict) -> list[dict]:
    return [n for n in result["nodes"] if (n.get("metadata") or {}).get("kind") == ROUTE_KIND]


def _key(node: dict) -> str:
    return f"{node['source_file']}:{node['source_location'][1:]} {node['label']}"


def _out(result: dict, node: dict, relation: str) -> list[dict]:
    return [e for e in result["edges"] if e["source"] == node["id"] and e["relation"] == relation]


# ── nodes ──────────────────────────────────────────────────────────────────

def test_route_nodes_are_exactly_the_defined_routes(graph):
    got = sorted(
        (n["source_file"], int(n["source_location"][1:]), n["label"],
         n["metadata"].get("name"), n["metadata"]["route_kind"], n["metadata"].get("partial_reason"))
        for n in _route_nodes(graph)
    )
    assert got == sorted(ROUTES)


def test_route_nodes_are_contained_by_their_route_file(graph):
    nodes = _by_id(graph)
    for route in _route_nodes(graph):
        parents = [e for e in graph["edges"] if e["target"] == route["id"] and e["relation"] == "contains"]
        assert len(parents) == 1, route["label"]
        parent = nodes[parents[0]["source"]]
        assert parent["source_file"] == route["source_file"]
        assert parent["label"] == Path(route["source_file"]).name
        assert parents[0]["confidence"] == "EXTRACTED"


def test_route_node_ids_are_unique_and_deterministic(graph):
    ids = [n["id"] for n in _route_nodes(graph)]
    assert len(ids) == len(set(ids))
    again = [n["id"] for n in _route_nodes(_extract(APP))]
    assert again == ids
    assert "routes_web_route_l17_get_branches_branch" in ids


def test_route_metadata(graph):
    by_key = {_key(n): n["metadata"] for n in _route_nodes(graph)}
    show = by_key["routes/web.php:17 GET /branches/{branch}"]
    assert show["verbs"] == ["GET"]
    assert show["uri"] == "/branches/{branch}"
    assert show["name"] == "branches.show"
    assert show["middleware"] == ["web"]
    assert show["action"] == "App\\Http\\Controllers\\BranchController@show"
    assert show["controller"] == "App\\Http\\Controllers\\BranchController"
    assert show["method"] == "show"
    assert "partial" not in show


def test_group_inheritance_prefix_name_and_middleware(graph):
    by_key = {_key(n): n["metadata"] for n in _route_nodes(graph)}
    audit = by_key["routes/web.php:45 GET /admin/reports/audit"]
    assert audit["uri"] == "/admin/reports/audit"
    assert audit["name"] == "admin.reports.audit"
    # `admin` is removed by ->withoutMiddleware('admin'); the rest is inherited in order.
    assert audit["middleware"] == ["web", "auth", "can:view-reports"]
    assert audit["abilities"] == ["view-reports"]
    shared = by_key["routes/web.php:46 GET /admin/reports/shared"]
    assert shared["middleware"] == ["web", "auth", "admin", "can:view-reports"]
    assert "name" not in shared  # inside a name('admin.reports.') group but never named itself


def test_array_group_attributes(graph):
    by_key = {_key(n): n["metadata"] for n in _route_nodes(graph)}
    legacy = by_key["routes/web.php:51 GET /legacy/x"]
    assert legacy["uri"] == "/legacy/x"
    assert legacy["name"] == "legacy.x"
    assert legacy["middleware"] == ["web", "visits", "legacy"]


def test_included_files_inherit_the_including_group(graph):
    by_key = {_key(n): n["metadata"] for n in _route_nodes(graph)}
    page = by_key["routes/included.php:6 GET /inc/page"]
    assert page["uri"] == "/inc/page"
    assert page["name"] == "inc.page"
    assert page["middleware"] == ["web", "App\\Http\\Middleware\\ExplicitMiddleware"]
    deep = by_key["routes/included.php:9 GET /inc/deep/shared"]
    assert deep["uri"] == "/inc/deep/shared"
    # `require __DIR__.'/required.php'` sits at the top level of web.php: only the web group.
    assert by_key["routes/required.php:6 GET /required"]["middleware"] == ["web"]


def test_bootstrap_loaders_provider_loaders_and_module_routes(graph):
    by_key = {_key(n): n["metadata"] for n in _route_nodes(graph)}
    # `then:` closure of bootstrap/app.php: Route::middleware('web')->prefix('extra')->name('extra.')
    assert by_key["routes/extra.php:6 GET /extra/pages"]["uri"] == "/extra/pages"
    assert by_key["routes/extra.php:6 GET /extra/pages"]["name"] == "extra.pages"
    # api: route file gets the `api` prefix and middleware group
    ping = by_key["routes/api.php:9 GET /api/ping"]
    assert ping["uri"] == "/api/ping" and ping["middleware"] == ["api"]
    # RouteServiceProvider: Route::prefix('rsp')->middleware('api')->group(base_path(...))
    rsp = by_key["routes/rsp.php:6 GET /rsp/ping"]
    assert rsp["uri"] == "/rsp/ping" and rsp["middleware"] == ["api"]
    # loadRoutesFrom() in a module provider adds no group of its own
    blog = by_key["modules/Blog/Routes/web.php:7 GET /blog/articles"]
    assert blog["uri"] == "/blog/articles"
    assert blog["name"] == "blog.articles.index"
    assert blog["middleware"] == ["web", "admin"]


def test_view_redirect_and_dynamic_routes_carry_metadata_only(graph):
    by_key = {_key(n): n for n in _route_nodes(graph)}
    view = by_key["routes/web.php:26 GET /welcome"]
    assert view["metadata"]["view"] == "welcome"
    assert view["metadata"]["action"] == "view:welcome"
    redirect = by_key["routes/web.php:27 ANY /old"]
    assert redirect["metadata"]["redirect_to"] == "/new"
    dynamic = by_key["routes/web.php:60 GET /dyn/{dynamic}"]
    assert dynamic["metadata"]["partial"] is True
    assert dynamic["metadata"]["partial_reason"] == "dynamic uri"
    for key in ("routes/web.php:26 GET /welcome", "routes/web.php:27 ANY /old"):
        assert _out(graph, by_key[key], "handled_by") == []


def test_orphan_route_file_is_marked_partial(graph):
    orphan = next(n for n in _route_nodes(graph) if n["label"] == "GET /orphan")
    assert orphan["metadata"]["partial"] is True
    assert orphan["metadata"]["middleware"] == []


def test_filament_panel_node(graph):
    panel = next(n for n in _route_nodes(graph) if n["metadata"]["route_kind"] == "filament_panel")
    assert panel["metadata"]["uri"] == "/cp/*"
    assert panel["metadata"]["middleware"] == [
        "App\\Http\\Middleware\\Locale", "Illuminate\\Session\\Middleware\\StartSession",
    ]
    assert panel["metadata"]["auth_middleware"] == [
        "Filament\\Http\\Middleware\\Authenticate", "App\\Http\\Middleware\\EnsureAdmin",
    ]


# ── handled_by edges ───────────────────────────────────────────────────────

def test_handled_by_edges_are_exactly_the_resolvable_actions(graph):
    nodes = _by_id(graph)
    got: dict[str, tuple[str, str, str]] = {}
    for route in _route_nodes(graph):
        edges = _out(graph, route, "handled_by")
        assert len(edges) <= 1, route["label"]
        if edges:
            target = nodes[edges[0]["target"]]
            got[_key(route)] = (target["label"], target["source_file"], edges[0]["confidence"])
            assert edges[0]["source_file"] == route["source_file"]
            assert edges[0]["source_location"] == route["source_location"]
    assert got == HANDLED


def test_vendor_only_and_missing_methods_get_no_edge(graph):
    by_key = {_key(n): n for n in _route_nodes(graph)}
    for key in (
        "routes/web.php:23 GET /vendor-only",   # callAction lives in vendor/ only
        "routes/web.php:24 GET /ghost",         # no in-corpus class declares it
        "routes/web.php:25 GET /nowhere",       # controller class is not in the corpus
        "routes/api.php:8 POST /api/tags",      # TagController has no store()
    ):
        assert _out(graph, by_key[key], "handled_by") == [], key


def test_old_extraction_never_reached_the_controller_method():
    """Without this pass a route file only imports/references the controller CLASS."""
    root = APP.resolve()
    plain = [r for r in resolver_registry.registered_resolvers() if r.name != "laravel_routes"]
    saved = list(resolver_registry._REGISTRY)
    resolver_registry._REGISTRY[:] = plain
    try:
        old = extract(collect_files(root), root=root, parallel=False)
    finally:
        resolver_registry._REGISTRY[:] = saved
    nodes = _by_id(old)
    from_routes = [
        nodes[e["target"]]["label"] for e in old["edges"]
        if e["source_file"] == "routes/web.php" and nodes.get(e["target"], {}).get("label", "").startswith(".")
    ]
    assert from_routes == []
    assert _route_nodes(old) == []


# ── uses_middleware edges ──────────────────────────────────────────────────

def test_uses_middleware_edges_are_exactly_the_in_corpus_classes(graph):
    nodes = _by_id(graph)
    for route in _route_nodes(graph):
        key = _key(route)
        got = {
            (nodes[e["target"]]["label"], e["confidence"], e.get("via") or e.get("stack"))
            for e in _out(graph, route, "uses_middleware")
        }
        if route["metadata"]["route_kind"] == "filament_panel":
            assert got == {
                ("Locale", "EXTRACTED", "middleware"),
                ("EnsureAdmin", "EXTRACTED", "auth"),
            }
            continue
        if key.startswith("routes/orphan.php"):
            base = GLOBAL_ONLY
        elif key.startswith(("routes/api.php", "routes/rsp.php")):
            base = API_BASE
        else:
            base = WEB_BASE
        assert got == base | EXTRA_MIDDLEWARE.get(key, set()), key


def test_framework_aliases_and_vendor_classes_get_no_edge(graph):
    nodes = _by_id(graph)
    targets = {nodes[e["target"]]["label"] for e in graph["edges"] if e["relation"] == "uses_middleware"}
    # `auth`, `throttle`, StartSession, Filament's Authenticate: vendor, not in the corpus.
    assert not targets & {"Authenticate", "StartSession", "ThrottleRequests"}
    for node in nodes.values():
        if node["id"] in {e["target"] for e in graph["edges"] if e["relation"] == "uses_middleware"}:
            assert node.get("source_file"), "middleware targets must be real nodes"


def test_without_middleware_drops_the_edge(graph):
    by_key = {_key(n): n for n in _route_nodes(graph)}
    nodes = _by_id(graph)
    audit = by_key["routes/web.php:45 GET /admin/reports/audit"]
    labels = {nodes[e["target"]]["label"] for e in _out(graph, audit, "uses_middleware")}
    assert "EnsureAdmin" not in labels


# ── gate: Laravel projects only ────────────────────────────────────────────

def _graph_json(result: dict) -> str:
    return json.dumps({"nodes": result["nodes"], "edges": result["edges"]}, sort_keys=True)


def _without_laravel_pass(root: Path) -> dict:
    saved = list(resolver_registry._REGISTRY)
    resolver_registry._REGISTRY[:] = [r for r in saved if r.name != "laravel_routes"]
    try:
        return _extract(root)
    finally:
        resolver_registry._REGISTRY[:] = saved


def test_non_laravel_php_project_is_unchanged(tmp_path):
    root = tmp_path / "plain"
    shutil.copytree(PLAIN, root)
    with_pass = _extract(root)
    assert _route_nodes(with_pass) == []
    assert _graph_json(with_pass) == _graph_json(_without_laravel_pass(root))


def test_laravel_files_without_a_laravel_marker_are_unchanged(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(APP, root)
    (root / "artisan").unlink()
    (root / "composer.json").unlink()
    with_pass = _extract(root)
    assert _route_nodes(with_pass) == []
    assert _graph_json(with_pass) == _graph_json(_without_laravel_pass(root))


def test_composer_requirement_alone_activates_the_pass(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(APP, root)
    (root / "artisan").unlink()  # composer.json still requires laravel/framework
    assert len(_route_nodes(_extract(root))) == len(ROUTES)


def test_artisan_alone_activates_the_pass(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(APP, root)
    (root / "composer.json").unlink()
    assert len(_route_nodes(_extract(root))) == len(ROUTES)


def test_composer_dev_requirement_activates_the_pass(tmp_path):
    root = tmp_path / "app"
    shutil.copytree(APP, root)
    (root / "artisan").unlink()
    (root / "composer.json").write_text(
        json.dumps({"require-dev": {"laravel/framework": "^11"}}), encoding="utf-8"
    )
    assert len(_route_nodes(_extract(root))) == len(ROUTES)


# ── route name index ───────────────────────────────────────────────────────

def test_route_name_index(graph):
    index = route_name_index(graph["nodes"])
    nodes = _by_id(graph)
    assert nodes[index["branches.show"][0]]["label"] == "GET /branches/{branch}"
    assert nodes[index["admin.reports.audit"][0]]["source_location"] == "L45"
    assert nodes[index["extra.pages"][0]]["source_file"] == "routes/extra.php"
    assert list(index) == sorted(index)
    # Resource and Filament entries without a name are not indexed.
    assert all(len(ids) == 1 for ids in index.values())


# ── helpers ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("word", "singular"),
    [("branches", "branch"), ("posts", "post"), ("categories", "category"), ("addresses", "address"),
     ("people", "person"), ("status", "status"), ("news", "news"), ("boxes", "box"), ("tag", "tag")],
)
def test_singularize(word, singular):
    assert singularize(word) == singular


def test_join_path():
    assert join_path("admin/", "/reports", "", "x") == "admin/reports/x"
    assert join_path("", "") == ""


# ── less common forms, on a synthetic project ──────────────────────────────

_CONTROLLER = """<?php
namespace App\\Http\\Controllers;

class {name}
{{
{methods}
}}
"""


def _write(root: Path, rel: str, text: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _project(tmp_path: Path, routes: str, *, controllers: dict[str, list[str]] | None = None, extra: dict | None = None) -> dict:
    root = tmp_path / "proj"
    _write(root, "artisan", "<?php\n")
    _write(
        root, "bootstrap/app.php",
        "<?php\nreturn Application::configure(basePath: dirname(__DIR__))\n"
        "    ->withRouting(web: __DIR__.'/../routes/web.php')->create();\n",
    )
    for name, methods in (controllers or {"PostController": ["index", "show", "store"]}).items():
        body = "\n".join(f"    public function {m}() {{}}" for m in methods)
        _write(root, f"app/Http/Controllers/{name}.php", _CONTROLLER.format(name=name, methods=body))
    for rel, text in (extra or {}).items():
        _write(root, rel, text)
    _write(root, "routes/web.php", routes)
    return _extract(root)


def _labels(result: dict) -> dict[str, dict]:
    return {n["label"]: n for n in _route_nodes(result)}


def test_pre_definition_modifiers_and_route_level_modifiers(tmp_path):
    result = _project(tmp_path, """<?php
use App\\Http\\Controllers\\PostController;
use Illuminate\\Support\\Facades\\Route;

Route::name('first.')->get('/a', [PostController::class, 'index'])->name('second');
Route::middleware('auth')->prefix('p')->get('/b', [PostController::class, 'show'])->can('view', 'post');
Route::any('/c', [PostController::class, 'store']);
Route::fallback([PostController::class, 'index']);
""")
    routes = _labels(result)
    assert set(routes) == {"GET /a", "GET /p/b", "ANY /c", "GET /{fallbackPlaceholder}"}
    assert routes["GET /a"]["metadata"]["name"] == "first.second"
    b = routes["GET /p/b"]["metadata"]
    assert b["middleware"] == ["web", "auth", "can:view,post"]
    assert b["abilities"] == ["view"]
    assert routes["GET /{fallbackPlaceholder}"]["metadata"]["route_kind"] == "controller"


def test_controller_group_forms_and_string_actions(tmp_path):
    result = _project(tmp_path, """<?php
use App\\Http\\Controllers\\PostController;
use Illuminate\\Support\\Facades\\Route;

Route::group(['controller' => PostController::class, 'prefix' => 'g'], function () {
    Route::get('/one', 'index');
    Route::group(['prefix' => 'inner', 'as' => 'in.'], function () {
        Route::get('/two', 'show')->name('two');
    });
});
Route::namespace('App\\\\Http\\\\Controllers')->group(function () {
    Route::get('/legacy', 'PostController@store');
});
Route::get('/uses', ['uses' => 'App\\\\Http\\\\Controllers\\\\PostController@show', 'as' => 'uses.show']);
Route::get('/leading', '\\\\App\\\\Http\\\\Controllers\\\\PostController@index');
""")
    nodes = _by_id(result)
    routes = _labels(result)
    target = {
        label: nodes[_out(result, node, "handled_by")[0]["target"]]["label"]
        for label, node in routes.items() if _out(result, node, "handled_by")
    }
    assert target == {
        "GET /g/one": ".index()",
        "GET /g/inner/two": ".show()",
        "GET /legacy": ".store()",
        "GET /uses": ".show()",
        "GET /leading": ".index()",
    }
    assert routes["GET /g/inner/two"]["metadata"]["name"] == "in.two"
    assert routes["GET /uses"]["metadata"]["name"] == "uses.show"


def test_resource_forms(tmp_path):
    result = _project(
        tmp_path,
        """<?php
use App\\Http\\Controllers\\PostController;
use Illuminate\\Support\\Facades\\Route;

Route::resource('admin/posts', PostController::class)->only('index', 'show');
Route::resource('posts.comments', PostController::class)->only(['index'])->names('c');
Route::resources(['tags' => PostController::class]);
Route::apiResource('items', PostController::class)->except(['index'])->middleware('auth');
Route::resource('lines', PostController::class)->parameters(['lines' => 'line'])->only(['show']);
""",
    )
    routes = {n["label"]: n["metadata"] for n in _route_nodes(result)}
    assert {k for k in routes if "/admin/" in k} == {"GET /admin/posts", "GET /admin/posts/{post}"}
    assert routes["GET /admin/posts"]["name"] == "posts.index"
    assert routes["GET /posts/{post}/comments"]["name"] == "c.index"
    assert {k for k in routes if "/tags" in k} == {
        "GET /tags", "GET /tags/create", "POST /tags", "GET /tags/{tag}", "GET /tags/{tag}/edit",
        "PUT|PATCH /tags/{tag}", "DELETE /tags/{tag}",
    }
    assert {k for k in routes if "/items" in k} == {
        "POST /items", "GET /items/{item}", "PUT|PATCH /items/{item}", "DELETE /items/{item}",
    }
    assert routes["POST /items"]["middleware"] == ["web", "auth"]
    # `->parameters()` renames the wildcard, which this pass does not model: flagged, not guessed.
    assert routes["GET /lines/{line}"]["partial"] is True
    assert routes["GET /lines/{line}"]["partial_reason"] == "resource parameters()"


def test_router_variable_routes_and_dynamic_groups(tmp_path):
    result = _project(tmp_path, """<?php
use App\\Http\\Controllers\\PostController;
use Illuminate\\Support\\Facades\\Route;

Route::group(['prefix' => 'r'], function ($router) {
    $router->get('/x', [PostController::class, 'index']);
});
Route::prefix($dynamic)->group(function () {
    Route::get('/y', [PostController::class, 'show']);
});
Route::middleware($stack)->get('/z', [PostController::class, 'store']);
Route::prefix(config('modules.blog.route_prefix', 'blog'))->name('blog.')->group(function () {
    Route::get('/', [PostController::class, 'index'])->name('index');
});
Route::get(config('modules.dash.route_prefix', 'dash').'/layout', [PostController::class, 'show']);
foreach (['a', 'b'] as $path) {
    Route::post("/{finding}/{$path}", [PostController::class, 'store']);
}
""")
    routes = {n["label"]: n["metadata"] for n in _route_nodes(result)}
    assert "GET /r/x" in routes and "partial" not in routes["GET /r/x"]
    assert routes["GET /y"]["partial_reason"] == "dynamic prefix"
    assert routes["GET /z"]["partial_reason"] == "dynamic middleware"
    # config('key', 'default'): the default is used for the label, never presented as certain.
    assert routes["GET /blog"]["partial_reason"] == "config-driven prefix (config default used)"
    assert routes["GET /blog"]["name"] == "blog.index"
    assert routes["GET /dash/layout"]["partial_reason"] == "config-driven uri (config default used)"
    assert routes["POST /{finding}/{dynamic}"]["partial_reason"] == "dynamic uri"


def test_class_names_follow_php_name_resolution(tmp_path):
    """A same-named controller in another namespace is never picked."""
    other = _CONTROLLER.format(name="PostController", methods="    public function index() {}").replace(
        "namespace App\\Http\\Controllers;", "namespace App\\Http\\Controllers\\Admin;"
    )
    result = _project(
        tmp_path,
        """<?php
use App\\Http\\Controllers\\Admin\\PostController as AdminPosts;
use Illuminate\\Support\\Facades\\Route;

Route::get('/aliased', [AdminPosts::class, 'index']);
Route::get('/relative', [Admin\\PostController::class, 'index']);
Route::get('/plain', [PostController::class, 'show']);
""",
        controllers={"PostController": ["show"]},
        extra={"app/Http/Controllers/Admin/PostController.php": other},
    )
    nodes = _by_id(result)
    routes = _labels(result)
    got = {
        label: nodes[_out(result, node, "handled_by")[0]["target"]]["source_file"]
        for label, node in routes.items() if _out(result, node, "handled_by")
    }
    # `/plain` is written without a `use` in routes/web.php (global namespace), so it names no class.
    assert got == {
        "GET /aliased": "app/Http/Controllers/Admin/PostController.php",
    }


def test_middleware_tables_from_bootstrap_kernel_and_router_calls(tmp_path):
    mw = "<?php\nnamespace App\\Http\\Middleware;\nclass {name} {{ public function handle($r, $n) {{ return $n($r); }} }}\n"
    result = _project(
        tmp_path,
        """<?php
use App\\Http\\Controllers\\PostController;
use Illuminate\\Support\\Facades\\Route;

Route::middleware(['a1', 'g1:param', 'a2:x,y', 'router.alias'])->get('/m', [PostController::class, 'index']);
""",
        extra={
            "bootstrap/app.php": (
                "<?php\nuse App\\Http\\Middleware\\A1;\nuse App\\Http\\Middleware\\G1A;\nuse App\\Http\\Middleware\\G1B;\n"
                "return Application::configure(basePath: dirname(__DIR__))\n"
                "    ->withRouting(web: __DIR__.'/../routes/web.php')\n"
                "    ->withMiddleware(function ($middleware) {\n"
                "        $middleware->alias(['a1' => A1::class]);\n"
                "        $middleware->appendToGroup('g1', [G1A::class]);\n"
                "        $middleware->prependToGroup('g1', G1B::class);\n"
                "    })->create();\n"
            ),
            "app/Http/Kernel.php": (
                "<?php\nnamespace App\\Http;\nclass Kernel {\n"
                "    protected $routeMiddleware = ['a2' => \\App\\Http\\Middleware\\A2::class];\n}\n"
            ),
            "app/Providers/AppServiceProvider.php": (
                "<?php\nnamespace App\\Providers;\nclass AppServiceProvider {\n"
                "    public function boot($router) { $router->aliasMiddleware('router.alias', \\App\\Http\\Middleware\\RouterAlias::class); }\n}\n"
            ),
            "app/Http/Middleware/A1.php": mw.format(name="A1"),
            "app/Http/Middleware/A2.php": mw.format(name="A2"),
            "app/Http/Middleware/G1A.php": mw.format(name="G1A"),
            "app/Http/Middleware/G1B.php": mw.format(name="G1B"),
            "app/Http/Middleware/RouterAlias.php": mw.format(name="RouterAlias"),
        },
    )
    nodes = _by_id(result)
    (route,) = [n for n in _route_nodes(result) if n["label"] == "GET /m"]
    got = {
        (nodes[e["target"]]["label"], e["confidence"], e.get("via"))
        for e in _out(result, route, "uses_middleware")
    }
    assert got == {
        ("A1", "INFERRED", "a1"),
        ("G1A", "INFERRED", "g1"),
        ("G1B", "INFERRED", "g1"),
        ("A2", "INFERRED", "a2"),
        ("RouterAlias", "INFERRED", "router.alias"),
    }
    assert route["metadata"]["middleware"] == ["web", "a1", "g1:param", "a2:x,y", "router.alias"]


def test_incremental_context_keeps_route_edges_to_unchanged_controllers(tmp_path):
    """Re-extracting only the route file still resolves its controllers from context nodes."""
    root = tmp_path / "proj"
    full = _project(tmp_path, """<?php
use App\\Http\\Controllers\\PostController;
use Illuminate\\Support\\Facades\\Route;

Route::get('/p', [PostController::class, 'index']);
""")
    root = root.resolve()
    route_file = root / "routes/web.php"
    context_nodes = [n for n in full["nodes"] if n.get("source_file") != "routes/web.php"]
    context_edges = [e for e in full["edges"] if e.get("source_file") != "routes/web.php"]
    partial = extract(
        [route_file], root=root, parallel=False,
        resolution_context_nodes=context_nodes, resolution_context_edges=context_edges,
    )
    nodes = _by_id(partial) | _by_id({"nodes": context_nodes})
    (route,) = _route_nodes(partial)
    handled = _out(partial, route, "handled_by")
    assert [nodes[e["target"]]["label"] for e in handled] == [".index()"]
