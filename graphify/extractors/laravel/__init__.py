"""Laravel route modelling (opt-in, framework-specific).

The generic PHP extractor sees a route file as imports and ``Foo::class``
references: a route is not a node and its controller *method* is never reached.
This package reads the route definitions themselves and adds, for Laravel
projects only:

* one node per route definition (see ``emit``), contained by its route file;
* ``handled_by`` edges to the controller method / closure / component class;
* ``uses_middleware`` edges to in-corpus middleware classes.

A second pass (``WEB_RESOLVER``, see ``web``) links Laravel to Inertia/Vue: ``renders``
edges to page files, the props contract, and ``route('name')`` -> route node edges.

It plugs in through ``graphify.resolver_registry`` (``LANGUAGE_RESOLVER``) and one
hook in ``extract.extract()`` that tells it the scan root. A corpus that is not a
Laravel project (no ``artisan`` at the root, no ``laravel/framework`` in
``composer.json``) is not touched at all.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from graphify.extractors.laravel.emit import ROUTE_KIND, Emitter, route_name_index
from graphify.extractors.laravel.middleware import build_config
from graphify.extractors.laravel.phpsrc import PhpFile, load_php_file
from graphify.extractors.laravel.project import is_laravel_project
from graphify.extractors.laravel.routes import Ctx, RouteWalker
from graphify.extractors.laravel.symbols import SymbolIndex
from graphify.extractors.laravel.viewpass import run_view_passes
from graphify.resolver_registry import LanguageResolver

__all__ = [
    "LANGUAGE_RESOLVER",
    "RUNTIME_RESOLVER",
    "WEB_RESOLVER",
    "resolve_laravel_web",
    "ROUTE_KIND",
    "clear_scan_context",
    "resolve_laravel_routes",
    "route_name_index",
    "set_scan_context",
]

_ROUTE_FILE = re.compile(r"(^|/)[Rr]outes/[^/]+\.php$")
_scan_root: Path | None = None


def set_scan_context(root: Path | str | None) -> None:
    """Tell the pass which directory is the project root (called by ``extract``)."""
    global _scan_root
    _scan_root = Path(root).resolve() if root is not None else None


def clear_scan_context() -> None:
    set_scan_context(None)


def resolve_laravel_routes(per_file: list, all_nodes: list[dict], all_edges: list[dict]) -> None:
    root = _scan_root
    if root is None or not is_laravel_project(root):
        return
    idx = SymbolIndex(root, all_nodes, all_edges)
    if not idx.php_files:
        _model_views(root, idx, per_file, all_nodes, all_edges)  # a project of Blade files only
        return

    cache: dict[str, PhpFile | None] = {}

    def load(rel: str) -> PhpFile | None:
        if rel not in cache:
            cache[rel] = load_php_file(root / rel, rel)
        return cache[rel]

    def read_bytes(rel: str) -> bytes:
        return (root / rel).read_bytes()

    files = sorted(idx.php_files)
    cfg = build_config(files, load, read_bytes)
    walker = RouteWalker(load, idx.php_files)

    # Loaders first, so a route file they include is walked with its group's context.
    if "bootstrap/app.php" in idx.php_files:
        walker.parse_file("bootstrap/app.php", Ctx())
    for rel in files:
        if "Provider" not in rel.rsplit("/", 1)[-1]:
            continue
        try:
            raw = read_bytes(rel)
        except OSError:
            continue
        if b"Route::" in raw or b"loadRoutesFrom" in raw or b"$router->" in raw:
            walker.parse_file(rel, Ctx())

    # Route files nothing above loaded: walked with an unknown inheritance chain.
    candidates: list[str] = []
    for rel in files:
        if rel in walker.visited or not _ROUTE_FILE.search(rel) or rel.startswith(("tests/", "vendor/")):
            continue
        try:
            raw = read_bytes(rel)
        except OSError:
            continue
        if b"Route::" in raw or b"$router->" in raw:
            candidates.append(rel)
    included: set[str] = set()
    for rel in candidates:
        included |= walker.includes_of(rel)
    orphan_ctx = Ctx(partial="route file is not loaded by any known loader")
    for rel in [c for c in candidates if c not in included] + [c for c in candidates if c in included]:
        walker.parse_file(rel, orphan_ctx)

    fresh = {idx.rel(n["source_file"]) for r in per_file if r for n in r.get("nodes", []) if n.get("source_file")}
    emitter = Emitter(idx, cfg, {n["id"] for n in all_nodes if n.get("id")})
    for rec in walker.routes:
        if rec.rel in fresh:
            emitter.route(rec)
    for panel in cfg.panels:
        if panel.rel in fresh:
            emitter.panel(panel)
    all_nodes.extend(emitter.nodes)
    all_edges.extend(emitter.edges)

    _model_views(root, idx, per_file, all_nodes, all_edges)


def _model_views(root: Path, idx: SymbolIndex, per_file: list, all_nodes: list[dict], all_edges: list[dict]) -> None:
    """Blade / view / Livewire modelling (views.py, viewpass.py). A failure there must not
    take the route nodes with it."""
    try:
        run_view_passes(root, idx, per_file, all_nodes, all_edges)
    except Exception as exc:
        logging.getLogger(__name__).warning("laravel view modelling failed, skipping: %s", exc)


LANGUAGE_RESOLVER = LanguageResolver(
    "laravel_routes", frozenset({".php"}), resolve_laravel_routes
)


def resolve_laravel_web(per_file: list, all_nodes: list[dict], all_edges: list[dict]) -> None:
    """Inertia page edges, the props contract and Ziggy/``route()`` links (see ``web``)."""
    root = _scan_root
    if root is None or not is_laravel_project(root):
        return
    from graphify.extractors.laravel import web

    web.run(root, per_file, all_nodes, all_edges)


# Runs after LANGUAGE_RESOLVER: it needs the route nodes that pass emits.
WEB_RESOLVER = LanguageResolver(
    "laravel_web",
    frozenset({".php", ".vue", ".js", ".jsx", ".ts", ".tsx", ".mjs"}),
    resolve_laravel_web,
)


def resolve_laravel_runtime(per_file: list, all_nodes: list[dict], all_edges: list[dict]) -> None:
    """Commands, scheduler, jobs, events, listeners, observers, container bindings (see ``runtime``)."""
    root = _scan_root
    if root is None or not is_laravel_project(root):
        return
    from graphify.extractors.laravel import runtime

    try:
        runtime.run(root, per_file, all_nodes, all_edges)
    except Exception as exc:  # a wiring pass must never take the extraction down
        logging.getLogger(__name__).warning("laravel runtime modelling failed, skipping: %s", exc)


# Needs the class and method nodes only, so its place after the route passes is conventional.
RUNTIME_RESOLVER = LanguageResolver("laravel_runtime", frozenset({".php"}), resolve_laravel_runtime)
