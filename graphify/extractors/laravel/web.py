"""The Laravel <-> Inertia/Vue linking pass (``laravel_web`` resolver).

Runs after the route pass (``resolve_laravel_routes``) so the route nodes and
their names exist. It reads, for a Laravel project only:

* ``Inertia::render`` / ``inertia()`` / ``Route::inertia`` -> ``renders`` edges to
  the page file, plus the props contract (see ``inertia``);
* ``route('name')`` in Vue/JS/TS, PHP and Blade -> ``calls_route`` /
  ``references_route`` edges to the route node (see ``ziggy``).

Edges are emitted for files extracted in this run (like the route pass); the
page-level contract is computed from every PHP file that renders, so it does not
depend on which side changed. Whatever cannot be read as a literal is skipped.
"""
from __future__ import annotations

from pathlib import Path

from graphify.extractors.laravel import inertia, ziggy
from graphify.extractors.laravel.emit import ROUTE_KIND, route_name_index
from graphify.extractors.laravel.phpsites import Owners
from graphify.extractors.laravel.phpsrc import PhpFile, load_php_file
from graphify.extractors.laravel.symbols import SymbolIndex
from graphify.extractors.laravel.webfiles import FileIndex

RENDERS = "renders"


def run(root: Path, per_file: list, all_nodes: list[dict], all_edges: list[dict]) -> None:
    idx = SymbolIndex(root, all_nodes, all_edges)
    files = FileIndex(idx, all_nodes)
    if not files.files:
        return
    fresh = {idx.rel(n["source_file"]) for r in per_file if r for n in r.get("nodes", []) if n.get("source_file")}
    owners = Owners(idx, files)
    pages = inertia.PageResolver(root, files)
    names = route_name_index(all_nodes)
    emitted: dict[tuple[str, str, str], dict] = {}
    new_edges: list[dict] = []

    def add_edge(src: str, tgt: str, relation: str, conf: str, fnode: dict, line: int, context: str, **extra) -> dict:
        key = (src, tgt, relation)
        prior = emitted.get(key)
        if prior is not None:
            return prior
        edge = {
            "source": src, "target": tgt, "relation": relation, "confidence": conf,
            "source_file": fnode["source_file"], "source_location": f"L{line}",
            "weight": 1.0, "context": context,
        }
        edge.update(extra)
        emitted[key] = edge
        new_edges.append(edge)
        return edge

    # ---- PHP: renders, shared props, route() ----
    page_use: dict[str, inertia.PageUse] = {}
    shared: set[str] = set()
    shared_marks: list[tuple[str, list[str]]] = []
    php_refs: list[ziggy.RouteRef] = []
    cache: dict[str, PhpFile | None] = {}

    def load(rel: str) -> PhpFile | None:
        if rel not in cache:
            cache[rel] = load_php_file(root / rel, rel)
        return cache[rel]

    for rel in sorted(idx.php_files):
        if rel.startswith(("vendor/", "tests/")):
            continue
        try:
            raw = (root / rel).read_bytes()
        except OSError:
            continue
        wants_inertia = b"nertia" in raw
        wants_route = rel in fresh and (b"route(" in raw or b"::route" in raw)
        if not (wants_inertia or wants_route):
            continue
        f = load(rel)
        if f is None:
            continue
        if wants_inertia:
            _php_inertia(f, owners, files, pages, fresh, page_use, shared, shared_marks, add_edge)
        if wants_route:
            php_refs.extend(ziggy.scan_php(f, owners))
        cache.pop(rel, None)

    # ---- Route::inertia route nodes ----
    for n in list(all_nodes):
        meta = n.get("metadata")
        if not isinstance(meta, dict) or meta.get("kind") != ROUTE_KIND or "inertia_page" not in meta:
            continue
        page = meta["inertia_page"]
        target = pages.resolve(page)
        rel = idx.rel(n.get("source_file", ""))
        if target is None:
            continue
        pnode = files.file_node(target)
        fnode = files.file_node(rel)
        if pnode is None or fnode is None:
            continue
        props = list(meta.get("inertia_props") or [])
        partial = bool(meta.get("inertia_props_partial"))
        use = page_use.setdefault(target, inertia.PageUse())
        use.names.add(page)
        use.passed.extend(k for k in props if k not in use.passed)
        use.partial |= partial
        use.renderers += 1
        if rel in fresh:
            line = _line(n)
            add_edge(
                n["id"], pnode["id"], RENDERS, "EXTRACTED", fnode, line, "inertia_route",
                page=page, props=props, **({"props_partial": True} if partial else {}),
            )

    # ---- JS / Vue / TS: route() ----
    refs: list[ziggy.RouteRef] = list(php_refs)
    for rel in files.js_files():
        if rel not in fresh or ziggy.skip_js(rel):
            continue
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        refs.extend(ziggy.scan_js(path, rel, text, files))
    for rel in sorted(r for r in files.files if r.endswith(".blade.php") and r in fresh):
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        refs.extend(ziggy.scan_blade(rel, text))

    for ref in refs:
        targets = names.get(ref.name)
        fnode = files.file_node(ref.rel)
        if not targets or fnode is None:
            continue
        src = ref.owner or fnode["id"]
        context = "ziggy_current" if ref.relation == ziggy.REFERENCES else "route_name"
        for tid in targets:
            if tid == src:
                continue
            extra: dict[str, object] = {"route": ref.name}
            if len(targets) > 1:
                extra["ambiguous"] = len(targets)
            add_edge(
                src, tid, ref.relation, "EXTRACTED" if len(targets) == 1 else "INFERRED",
                fnode, ref.line, context, **extra,
            )

    # ---- props contract on page / middleware nodes ----
    replace: dict[str, dict] = {}
    for rel, use in page_use.items():
        pnode = files.file_node(rel)
        if pnode is None:
            continue
        declared = inertia.declared_props(root / rel)
        meta = inertia.contract_report(declared, use, shared)
        replace[pnode["id"]] = _with_meta(pnode, meta)
    for cls_id, keys in shared_marks:
        node = idx.nodes.get(cls_id)
        if node is not None:
            replace[cls_id] = _with_meta(node, {"inertia_shared_props": keys})
    if replace:
        for i, n in enumerate(all_nodes):
            hit = replace.get(n.get("id", ""))
            if hit is not None and n.get("source_file") == hit.get("source_file"):
                all_nodes[i] = hit
    all_edges.extend(new_edges)


def _line(node: dict) -> int:
    loc = str(node.get("source_location") or "")
    return int(loc[1:]) if loc[:1] == "L" and loc[1:].isdigit() else 1


def _with_meta(node: dict, extra: dict) -> dict:
    meta = node.get("metadata")
    merged = dict(meta) if isinstance(meta, dict) else {}
    merged.update(extra)
    return {**node, "metadata": merged}


def _php_inertia(f, owners, files, pages, fresh, page_use, shared, shared_marks, add_edge) -> None:
    found = inertia.scan_shared(f, owners)
    if found is not None:
        cls_id, keys = found
        shared.update(keys)
        if cls_id:
            shared_marks.append((cls_id, keys))
    fnode = files.file_node(f.rel)
    for site in inertia.scan_renders(f, owners):
        target = pages.resolve(site.page)
        if target is None:
            continue
        pnode = files.file_node(target)
        if pnode is None:
            continue
        use = page_use.setdefault(target, inertia.PageUse())
        use.names.add(site.page)
        use.passed.extend(k for k in site.props if k not in use.passed)
        use.partial |= site.partial
        use.renderers += 1
        if f.rel in fresh and fnode is not None:
            src = site.owner or fnode["id"]
            edge = add_edge(
                src, pnode["id"], RENDERS, "EXTRACTED", fnode, site.line, "inertia_render",
                page=site.page,
            )
            props = edge.setdefault("props", [])
            props.extend(k for k in site.props if k not in props)
            if site.partial:
                edge["props_partial"] = True
