"""Turn route records into graph nodes and edges.

Node: one per route definition (``kind: laravel_route`` in ``metadata``), labelled
``GET /branches/{branch}``, contained by its route file's node.

Edges (all sourced at the route node, located at the definition line):

* ``handled_by`` -> controller method node, closure node, or component class node.
  ``EXTRACTED`` when the action is written in the route statement; ``INFERRED``
  for ``Route::resource`` style expansion (the method is implied by convention).
* ``uses_middleware`` -> an in-corpus middleware class. ``EXTRACTED`` when the
  route names the class itself, ``INFERRED`` when it came through an alias, a
  middleware group or the global stack (``via`` names which).

No edge is emitted for a class or method that is not declared in the corpus (a
vendor-only controller method, a framework middleware alias).
"""
from __future__ import annotations

from pathlib import Path

from graphify.extractors.base import _file_stem, _make_id
from graphify.extractors.laravel.middleware import Entry, MiddlewareConfig, Panel
from graphify.extractors.laravel.routes import RouteRec
from graphify.extractors.laravel.symbols import SymbolIndex

ROUTE_KIND = "laravel_route"


def _raw(entry: Entry) -> str:
    return entry[1]


def _excluded(entry: Entry, without: set[str]) -> bool:
    kind, val = entry
    if val in without:
        return True
    return kind == "str" and val.split(":", 1)[0] in without


def effective_middleware(rec: RouteRec) -> list[Entry]:
    """Inherited + route-level middleware, minus ``withoutMiddleware``, in order."""
    without = set(rec.without)
    seen: set[Entry] = set()
    out: list[Entry] = []
    for e in rec.middleware:
        if _excluded(e, without) or e in seen:
            continue
        seen.add(e)
        out.append(e)
    return out


def abilities_of(entries: list[Entry]) -> list[str]:
    out: list[str] = []
    for kind, val in entries:
        if kind == "str" and val.startswith("can:"):
            ability = val[4:].split(",", 1)[0].strip()
            if ability and ability not in out:
                out.append(ability)
    return out


class Emitter:
    def __init__(self, idx: SymbolIndex, cfg: MiddlewareConfig, existing_ids: set[str]):
        self.idx = idx
        self.cfg = cfg
        self.ids = set(existing_ids)
        self.nodes: list[dict] = []
        self.edges: list[dict] = []

    def _new_id(self, *parts: str) -> str:
        base = _make_id(*parts)
        nid, n = base, 1
        while nid in self.ids:
            n += 1
            nid = f"{base}_{n}"
        self.ids.add(nid)
        return nid

    def _edge(self, src: str, tgt: str, relation: str, conf: str, sf: str, line: int, context: str, **extra) -> None:
        e = {
            "source": src, "target": tgt, "relation": relation, "confidence": conf,
            "source_file": sf, "source_location": f"L{line}", "weight": 1.0, "context": context,
        }
        e.update(extra)
        self.edges.append(e)

    def route(self, rec: RouteRec) -> None:
        fnode = self.idx.file_node(rec.rel)
        if fnode is None:
            return
        sf = fnode["source_file"]
        rid = self._new_id(_file_stem(Path(rec.rel)), "route", f"l{rec.line}", "-".join(rec.verbs), rec.uri)
        entries = effective_middleware(rec)
        handler, kind = self._handler(rec)

        meta: dict = {
            "kind": ROUTE_KIND,
            "route_kind": kind,
            "verbs": list(rec.verbs),
            "uri": rec.uri,
            "middleware": [_raw(e) for e in entries],
            "action": rec.action,
        }
        if rec.name is not None:
            meta["name"] = rec.name
        if rec.domain:
            meta["domain"] = rec.domain
        if rec.controller:
            meta["controller"] = rec.controller
        if rec.method:
            meta["method"] = rec.method
        if rec.view is not None:
            meta["view"] = rec.view
        if rec.redirect is not None:
            meta["redirect_to"] = rec.redirect
        if rec.resource:
            meta["resource"] = rec.resource
        abilities = abilities_of(entries)
        if abilities:
            meta["abilities"] = abilities
        if rec.partial:
            meta["partial"] = True
            meta["partial_reason"] = rec.partial

        self.nodes.append({
            "id": rid,
            "label": f"{'|'.join(rec.verbs)} {rec.uri}",
            "file_type": "code",
            "source_file": sf,
            "source_location": f"L{rec.line}",
            "metadata": meta,
        })
        self._edge(fnode["id"], rid, "contains", "EXTRACTED", sf, rec.line, "route_definition")
        if handler:
            self._edge(
                rid, handler, "handled_by", "INFERRED" if rec.inferred else "EXTRACTED",
                sf, rec.line, "route_action",
            )
        self._middleware_edges(rid, entries, set(rec.without), sf, rec.line)

    def _handler(self, rec: RouteRec) -> tuple[str | None, str]:
        """``(target node id | None, route_kind)``."""
        idx = self.idx
        if rec.kind == "closure":
            return (idx.closure_at(rec.rel, rec.closure_line) if rec.closure_line else None), "closure"
        if rec.kind in ("view", "redirect", "unknown"):
            return None, rec.kind
        cls = idx.resolve_class(rec.controller)
        if cls is None:
            return None, rec.kind if rec.kind != "invokable" else "invokable"
        if rec.class_only:
            invoke = idx.lookup_method(cls, "__invoke")
            if rec.kind == "livewire":
                return cls, "livewire"
            if invoke:
                return invoke, "invokable"
            return cls, "component"
        if rec.method:
            return idx.lookup_method(cls, rec.method), rec.kind
        return None, rec.kind

    def _middleware_edges(self, rid: str, entries: list[Entry], without: set[str], sf: str, line: int) -> None:
        found: dict[str, tuple[str, str | None]] = {}

        def add(fqn: str, via: str | None) -> None:
            nid = self.idx.resolve_class(fqn)
            if nid is None:
                return
            conf = "EXTRACTED" if via is None else "INFERRED"
            prev = found.get(nid)
            if prev is None or (prev[0] == "INFERRED" and conf == "EXTRACTED"):
                found[nid] = (conf, via)

        for entry in entries:
            for fqn, via in self.cfg.expand(entry):
                if fqn in without or (via is not None and via in without):
                    continue
                add(fqn, via)
        for fqn in self.cfg.global_middleware:
            if fqn not in without:
                add(fqn, "global")
        for nid, (conf, via) in found.items():
            extra = {"via": via} if via else {}
            self._edge(rid, nid, "uses_middleware", conf, sf, line, "route_middleware", **extra)

    def panel(self, panel: Panel) -> None:
        fnode = self.idx.file_node(panel.rel)
        if fnode is None:
            return
        sf = fnode["source_file"]
        path = "/" + (panel.path or "").strip("/")
        uri = path.rstrip("/") + "/*" if path != "/" else "/*"
        name = panel.panel_id or (panel.path or "")
        rid = self._new_id(_file_stem(Path(panel.rel)), "filament_panel", name or f"l{panel.line}")
        meta: dict = {
            "kind": ROUTE_KIND,
            "route_kind": "filament_panel",
            "verbs": ["ANY"],
            "uri": uri,
            "middleware": list(panel.middleware),
            "auth_middleware": list(panel.auth_middleware),
            "action": f"Filament panel {name}".strip(),
        }
        if panel.domain:
            meta["domain"] = panel.domain
        if panel.partial:
            meta["partial"] = True
        self.nodes.append({
            "id": rid,
            "label": f"ANY {uri} (Filament panel {name})".replace("  ", " "),
            "file_type": "code",
            "source_file": sf,
            "source_location": f"L{panel.line}",
            "metadata": meta,
        })
        self._edge(fnode["id"], rid, "contains", "EXTRACTED", sf, panel.line, "route_definition")
        seen: set[str] = set()
        for stack, classes in (("middleware", panel.middleware), ("auth", panel.auth_middleware)):
            for fqn in classes:
                nid = self.idx.resolve_class(fqn)
                if nid is None or nid in seen:
                    continue
                seen.add(nid)
                self._edge(rid, nid, "uses_middleware", "EXTRACTED", sf, panel.line, "route_middleware", stack=stack)


def route_name_index(nodes: list[dict]) -> dict[str, list[str]]:
    """``{route name: [route node ids]}`` over the modelled route nodes.

    The lookup a later phase uses to tie ``route('name')`` in Blade/JS to its
    route node. Ids are sorted so the result is stable; a name defined twice maps
    to every definition rather than silently picking one.
    """
    out: dict[str, list[str]] = {}
    for n in nodes:
        meta = n.get("metadata")
        if not isinstance(meta, dict) or meta.get("kind") != ROUTE_KIND:
            continue
        name = meta.get("name")
        if name and n.get("id"):
            out.setdefault(name, []).append(n["id"])
    return {k: sorted(v) for k, v in sorted(out.items())}
