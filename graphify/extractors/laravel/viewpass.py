"""Blade / view / Livewire modelling for a Laravel project (see ``views`` for resolution).

Edges (all ``source_file`` = the file that contains the reference, ``source_location`` the
line; ``context`` names the form):

* blade file -> blade file: ``extends`` (``@extends``), ``includes`` (``@include`` and its
  ``If/When/Unless/First`` forms, ``@each``), ``uses_component`` (``@component``);
* blade file -> component: ``uses_component`` for ``<x-name>`` (the component *class* node
  when one exists, else the anonymous component's blade file), for ``<livewire:name>`` /
  ``@livewire(...)`` (the Livewire class node, or the single-file component node);
* PHP method / closure / class (or route node for ``Route::view``) -> blade file: ``renders``
  for ``view()``, ``View::make``, ``->view()``/``->markdown()``, ``Content(view:)``, ``loadView``,
  ``$view`` properties and view-name helpers;
* Livewire class -> its blade view: ``renders`` (``livewire_view``), ``EXTRACTED`` when
  ``render()`` returns exactly one literal view, ``INFERRED`` for the ``livewire.<name>``
  convention of a class without ``render()``;
* blade file -> Livewire method: ``binds_method`` for ``wire:click="save"``-style actions
  and ``$wire.save()``, only when exactly one component owns the view.

``EXTRACTED``: the name is written literally in the source and resolves by Laravel's fixed
rules. ``INFERRED``: a convention (Livewire view by name), a view owned only through that
convention, or a ``.graphifyrc`` mapping (``via: "graphifyrc"``). A name that is dynamic,
unresolvable, or matches several files gets no edge.

Blade metadata (not nodes): ``metadata.blade`` on the blade file node lists the literal
``sections`` / ``yields`` / ``pushes`` / ``stacks``; ``metadata.livewire`` lists the owner
component and the ``wire:model`` properties as written.

The legacy per-file Blade extractor's edges to fake stubs are flagged ``_drop`` for every
Blade file this pass handles; ``extract()`` removes the flagged records.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from graphify.extractors.base import _make_id
from graphify.extractors.laravel.blade import literal_string, scan_blade, split_args
from graphify.extractors.laravel.livewire import (
    DEFAULT_CLASS_NAMESPACES,
    SFC_MARK,
    ClassDecl,
    Sfc,
    auto_name,
    class_namespace_config,
    component_fqns,
    read_classes,
    read_sfc,
)
from graphify.extractors.laravel.phpsrc import PhpFile, array_items, load_php_file, path_value, str_value
from graphify.extractors.laravel.symbols import SymbolIndex, _loc_line
from graphify.extractors.laravel.viewcalls import ViewCall, find_view_calls
from graphify.extractors.laravel.viewconfig import load_view_options
from graphify.extractors.laravel.views import (
    Registrations,
    Resolved,
    ViewHelper,
    ViewResolver,
    collect_registrations,
    iter_nodes,
    studly,
)

ROUTE_KIND = "laravel_route"
_REG_PREFILTER = re.compile(
    rb"loadViewsFrom|addNamespace|prependNamespace|replaceNamespace|anonymousComponentPath|"
    rb"Blade::|Livewire::component|::\{\$"
)
_PHP_PREFILTER = re.compile(rb"(?i)view|markdown|livewire|content")
_EXTENDS = re.compile(rb"\bextends\s+\\?([\w\\]+)")
_LEGACY_RELATIONS = frozenset({"extends", "includes", "uses_component", "binds_method"})
_VIEW_FORMS = frozenset({"view()", "View::make", "view()->make"})
_CLASS_CONST = re.compile(r"\\?([A-Za-z_][\w\\]*)::class")


@dataclass
class FileFacts:
    calls: list[ViewCall] = field(default_factory=list)
    classes: list[ClassDecl] = field(default_factory=list)


@dataclass
class Owner:
    kind: str                       # "class" | "sfc"
    key: str                        # class node id | sfc component id
    conf: str                       # EXTRACTED | INFERRED
    methods: dict[str, str] = field(default_factory=dict)  # sfc only: lower name -> node id
    fqn: str = ""


def _blade_expr(expr: str, helpers: dict[str, ViewHelper]) -> str | None:
    """A Blade directive argument as a view name: a literal or a view-name helper call."""
    lit = literal_string(expr)
    if lit is not None:
        return lit
    m = re.fullmatch(r"\\?([A-Za-z_]\w*)\s*\((.*)\)", expr.strip(), re.S)
    if m and m.group(1).lower() in helpers:
        return helpers[m.group(1).lower()].view_name([literal_string(a) for a in split_args(m.group(2))])
    return None


def _studly_path(name: str) -> str:
    return "\\".join(studly(seg) for seg in name.split("."))


def _read(root: Path, rel: str) -> bytes | None:
    try:
        return (root / rel).read_bytes()
    except OSError:
        return None


def _config_roots(f: PhpFile) -> list[str] | None:
    """``paths`` of ``config/view.php`` when every entry is a literal path."""
    for n in iter_nodes(f.root):
        if n.type == "return_statement":
            expr = next((c for c in n.children if c.is_named), None)
            for k, v in array_items(expr) or []:
                if k is not None and str_value(f, k) == "paths":
                    items = array_items(v)
                    paths = [path_value(f, x) for _k, x in items or []]
                    return paths if paths and all(p for p in paths) else None  # type: ignore[return-value]
            return None
    return None


class ViewPass:
    def __init__(self, root: Path, idx: SymbolIndex, per_file: list, all_nodes: list[dict], all_edges: list[dict]):
        self.root = root
        self.idx = idx
        self.all_nodes = all_nodes
        self.all_edges = all_edges
        self.fresh = {
            idx.rel(n["source_file"]) for r in per_file if r for n in r.get("nodes", []) if n.get("source_file")
        }
        self.ids = {n["id"] for n in all_nodes if n.get("id")}
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self._seen: set[tuple[str, str, str]] = set()
        self.blade_nodes: dict[str, dict] = {}
        self.callables: dict[str, list[tuple[str, int, str, bool]]] = {}
        self.route_nodes: list[dict] = []
        self.legacy_stubs: dict[str, list[dict]] = {}   # blade rel -> legacy extractor's stub nodes
        for n in all_nodes:
            sf = n.get("source_file")
            if not sf or not n.get("id"):
                continue
            rel = idx.rel(sf)
            if rel.lower().endswith(".blade.php"):
                if str(n.get("label", "")) == posixpath.basename(rel):
                    if n.get("source_location") in (None, "", "L1"):
                        self.blade_nodes.setdefault(rel, n)
                elif n.get("source_location") is None:
                    self.legacy_stubs.setdefault(rel, []).append(n)
            elif n.get("_callable"):
                line = _loc_line(n.get("source_location"))
                if line is not None:
                    self.callables.setdefault(rel, []).append(
                        (str(n.get("label", "")), line, n["id"], bool(n.get("_callable_class")))
                    )
            meta = n.get("metadata")
            if isinstance(meta, dict) and meta.get("kind") == ROUTE_KIND and meta.get("view"):
                self.route_nodes.append(n)

    # ── plumbing ────────────────────────────────────────────────────────────

    def edge(self, src: str, tgt: str, relation: str, conf: str, sf: str, line: int, context: str, **extra) -> None:
        if src == tgt or (src, tgt, relation) in self._seen:
            return
        self._seen.add((src, tgt, relation))
        e = {
            "source": src, "target": tgt, "relation": relation, "confidence": conf,
            "source_file": sf, "source_location": f"L{line}", "weight": 1.0, "context": context,
        }
        e.update({k: v for k, v in extra.items() if v is not None})
        self.edges.append(e)

    def new_id(self, *parts: str) -> str:
        base = _make_id(*parts)
        nid, n = base, 1
        while nid in self.ids:
            n += 1
            nid = f"{base}_{n}"
        self.ids.add(nid)
        return nid

    def load(self, rel: str) -> PhpFile | None:
        return load_php_file(self.root / rel, rel)

    # ── source node of a PHP reference ──────────────────────────────────────

    def source_for(self, rel: str, owners: list[tuple]) -> str | None:
        cands = self.callables.get(rel, [])
        for o in owners:
            nid = None
            if o[0] == "method":
                hit = [c for c in cands if c[0] == f".{o[1]}()" and o[2] <= c[1] <= o[3] and not c[3]]
                nid = hit[0][2] if len(hit) == 1 else None
            elif o[0] == "function":
                hit = [c for c in cands if c[0] == f"{o[1]}()" and o[2] <= c[1] <= o[3]]
                nid = hit[0][2] if len(hit) == 1 else None
            elif o[0] == "closure":
                nid = self.idx.closure_at(rel, o[1])
            elif o[0] == "class":
                hit = [c for c in cands if c[3] and c[0] == o[1] and o[2] <= c[1] <= o[3]]
                nid = hit[0][2] if len(hit) == 1 else None
            if nid:
                return nid
        fnode = self.idx.file_node(rel)
        return fnode["id"] if fnode else None

    # ── the pass ────────────────────────────────────────────────────────────

    def run(self) -> None:
        idx, root = self.idx, self.root
        if not self.blade_nodes:
            return
        opts = load_view_options(root)
        reg = Registrations()
        raws: dict[str, bytes] = {}
        files = sorted(idx.php_files)
        for rel in files:
            raw = _read(root, rel)
            if raw is None:
                continue
            raws[rel] = raw
            if _REG_PREFILTER.search(raw):
                f = self.load(rel)
                if f is not None:
                    collect_registrations(f, reg)
        roots = ["resources/views"]
        if "config/view.php" in raws:
            f = self.load("config/view.php")
            cfg_roots = _config_roots(f) if f is not None else None
            if cfg_roots:
                roots = cfg_roots
        resolver = ViewResolver(set(self.blade_nodes), reg, opts, roots)
        helpers = reg.helpers

        facts: dict[str, FileFacts] = {}

        def read_facts(rel: str) -> None:
            f = self.load(rel)
            if f is not None:
                facts[rel] = FileFacts(find_view_calls(f, helpers), read_classes(f))

        for rel in files:
            raw = raws.get(rel)
            if raw is not None and _PHP_PREFILTER.search(raw):
                read_facts(rel)
        # A component that extends an in-corpus component can live in a file that never
        # mentions a view: chase `extends <known component>` to a fixpoint.
        while True:
            decls = {d.fqn: d for ff in facts.values() for d in ff.classes}
            known = {fqn.rsplit("\\", 1)[-1].encode() for fqn in component_fqns(decls)}
            more = [
                rel for rel in files
                if rel not in facts and rel in raws
                and any(m.group(1).rsplit(b"\\", 1)[-1] in known for m in _EXTENDS.finditer(raws[rel]))
            ]
            if not more:
                break
            for rel in more:
                read_facts(rel)
                facts.setdefault(rel, FileFacts())

        # ── Livewire components ────────────────────────────────────────────
        by_fqn: dict[str, list[tuple[str, ClassDecl]]] = {}
        for rel, ff in facts.items():
            for d in ff.classes:
                by_fqn.setdefault(d.fqn, []).append((rel, d))
        decls = {fqn: v[0][1] for fqn, v in by_fqn.items() if len(v) == 1}
        class_ns: list[str] = list(DEFAULT_CLASS_NAMESPACES)
        cfg_raw = raws.get("config/livewire.php")
        cfg_ns = class_namespace_config(cfg_raw)
        if cfg_ns:
            class_ns.insert(0, cfg_ns)
        components: dict[str, tuple[str, str, ClassDecl]] = {}   # fqn -> (class nid, rel, decl)
        for fqn in sorted(component_fqns(decls)):
            nid = idx.resolve_class(fqn)
            if nid:
                components[fqn] = (nid, by_fqn[fqn][0][0], decls[fqn])
        names: dict[str, set[str]] = {}     # component name -> {class nid | sfc id}
        comp_names: dict[str, list[str]] = {}
        for fqn, (nid, _rel, _d) in components.items():
            cn = [auto_name(fqn, class_ns)] if auto_name(fqn, class_ns) else []
            cn += sorted(n for n, fqns in reg.livewire_names.items() if fqn in fqns)
            comp_names[fqn] = [n for n in cn if n]
            for n in comp_names[fqn]:
                names.setdefault(n, set()).add(nid)
        class_nid_by_fqn = {fqn: c[0] for fqn, c in components.items()}

        # single-file components
        sfcs: dict[str, tuple[Sfc, str, str]] = {}   # rel -> (sfc, component id, file nid)
        for rel in sorted(self.blade_nodes):
            if not posixpath.basename(rel).startswith(SFC_MARK):
                continue
            f = self.load(rel)
            sfc = read_sfc(f) if f is not None else None
            if sfc is None:
                continue
            fnode = self.blade_nodes[rel]
            cid = f"{fnode['id']}_component"
            if rel not in self.fresh and cid not in self.ids:
                continue  # unchanged file whose component node is not in this graph
            sfcs[rel] = (sfc, cid, fnode["id"])
            names.setdefault(sfc.name, set()).add(cid)

        def view_of(rel: str, d: ClassDecl, fqn: str) -> tuple[Resolved, str, int] | None:
            """The one view a component renders: ``(file, confidence, line)``."""
            calls = [
                c for c in facts[rel].calls
                if c.form in _VIEW_FORMS and any(
                    o[0] == "method" and o[1].lower() == "render" and d.render == (o[2], o[3]) for o in c.owners
                )
            ]
            if d.render is not None:
                if not calls:
                    return None
                got: dict[str, Resolved] = {}
                for c in calls:
                    for nm in c.names:
                        r = resolver.view(nm) if nm is not None else None
                        if r is None:
                            return None
                        got[r.rel] = r
                if len(got) != 1:
                    return None
                r = next(iter(got.values()))
                return r, ("INFERRED" if r.via else "EXTRACTED"), calls[0].line
            found: dict[str, Resolved] = {}
            for nm in comp_names.get(fqn, []):
                r = resolver.view(f"livewire.{nm}")
                if r is not None:
                    found[r.rel] = r
            if len(found) != 1:
                return None
            return next(iter(found.values())), "INFERRED", d.start

        owners: dict[str, list[Owner]] = {}
        for fqn, (nid, rel, d) in sorted(components.items()):
            v = view_of(rel, d, fqn)
            if v is None:
                continue
            res, conf, line = v
            owners.setdefault(res.rel, []).append(Owner("class", nid, conf, fqn=fqn))
            if rel in self.fresh:
                fnode = idx.file_node(rel)
                target = self.blade_nodes.get(res.rel)
                if fnode and target:
                    self.edge(nid, target["id"], "renders", conf, fnode["source_file"], line, "livewire_view",
                              view=res.rel, via=res.via)
        for rel, (sfc, cid, _fid) in sfcs.items():
            owners.setdefault(rel, []).append(
                Owner("sfc", cid, "EXTRACTED",
                      {m.lower(): _make_id(cid, m) for m, _l in sfc.methods})
            )

        # ── PHP -> view ────────────────────────────────────────────────────
        for rel in sorted(facts):
            if rel not in self.fresh:
                continue
            fnode = idx.file_node(rel)
            if fnode is None:
                continue
            for call in facts[rel].calls:
                src = self.source_for(rel, call.owners)
                if src is None:
                    continue
                for nm in call.names:
                    r = resolver.view(nm) if nm is not None else None
                    target = self.blade_nodes.get(r.rel) if r else None
                    if r is None or target is None:
                        continue
                    self.edge(src, target["id"], "renders", "INFERRED" if r.via else "EXTRACTED",
                              fnode["source_file"], call.line, "view_call", form=call.form, view=nm, via=r.via)
        for node in self.route_nodes:
            rel = idx.rel(node["source_file"])
            if rel not in self.fresh:
                continue
            r = resolver.view(str(node["metadata"]["view"]))
            target = self.blade_nodes.get(r.rel) if r else None
            if r is not None and target is not None:
                line = int(str(node.get("source_location", "L1"))[1:] or 1)
                self.edge(node["id"], target["id"], "renders", "INFERRED" if r.via else "EXTRACTED",
                          node["source_file"], line, "route_view", view=node["metadata"]["view"], via=r.via)

        # ── Blade files ────────────────────────────────────────────────────
        def component_target(tag: str) -> tuple[str, str | None] | None:
            if "::" in tag:
                prefix, rest = tag.split("::", 1)
                cands = [f"{ns}\\{_studly_path(rest)}" for ns in reg.blade_namespaces.get(prefix, [])]
            else:
                cands = ([reg.blade_aliases[tag]] if tag in reg.blade_aliases else []) + [
                    "App\\View\\Components\\" + _studly_path(tag)
                ]
            for c in cands:
                nid = idx.resolve_class(c)
                if nid:
                    return nid, None
            r = resolver.anonymous_component(tag)
            target = self.blade_nodes.get(r.rel) if r else None
            return (target["id"], r.via) if target and r else None

        def livewire_target(name: str) -> str | None:
            for cand in (name, name + ".index"):
                hit = names.get(cand)
                if hit:
                    return next(iter(hit)) if len(hit) == 1 else None
            return None

        for rel in sorted(self.blade_nodes):
            if rel not in self.fresh:
                continue
            fnode = self.blade_nodes[rel]
            sf = fnode["source_file"]
            try:
                text = (root / rel).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scan = scan_blade(text)
            self._blade_metadata(fnode, scan)
            own = owners.get(rel, [])
            owner = own[0] if len(own) == 1 else None
            sfc_entry = sfcs.get(rel)
            if sfc_entry is not None:
                self._emit_sfc(rel, fnode, sfc_entry)
            models: list[str] = []
            for ref in scan.refs:
                k = ref.kind
                if k in ("extends", "include", "each", "component", "include_first"):
                    if k == "include_first":   # runtime picks the first existing view: only its first is certain
                        names_ = ref.exprs[:1] or [None]
                    else:
                        names_ = [_blade_expr(e, helpers) for e in ref.exprs] or [None]
                    relation = {"extends": "extends", "component": "uses_component"}.get(k, "includes")
                    for nm in names_:
                        r = resolver.view(nm) if nm is not None else None
                        target = self.blade_nodes.get(r.rel) if r else None
                        if r is not None and target is not None:
                            self.edge(fnode["id"], target["id"], relation, "INFERRED" if r.via else "EXTRACTED",
                                      sf, ref.line, f"blade_{k}", view=nm, via=r.via)
                elif k == "x":
                    t = component_target(ref.name)
                    if t is not None:
                        self.edge(fnode["id"], t[0], "uses_component", "INFERRED" if t[1] else "EXTRACTED",
                                  sf, ref.line, "blade_x_component", component=ref.name, via=t[1])
                elif k in ("livewire_tag", "livewire_dir"):
                    tgt = None
                    comp_name = ref.name
                    if k == "livewire_dir":
                        expr = ref.exprs[0] if ref.exprs else ""
                        m = _CLASS_CONST.fullmatch(expr.strip())
                        if m:
                            tgt = class_nid_by_fqn.get(m.group(1))
                            comp_name = m.group(1)
                        else:
                            lit = literal_string(expr)
                            comp_name = lit or ""
                            tgt = livewire_target(lit) if lit else None
                    else:
                        tgt = livewire_target(ref.name)
                    if tgt is not None:
                        self.edge(fnode["id"], tgt, "uses_component", "EXTRACTED", sf, ref.line,
                                  "blade_livewire", component=comp_name)
                elif k in ("wire_action", "alpine_action") and owner is not None:
                    mid = (owner.methods.get(ref.name.lower()) if owner.kind == "sfc"
                           else idx.lookup_method(owner.key, ref.name))
                    if mid:
                        self.edge(fnode["id"], mid, "binds_method", owner.conf, sf, ref.line,
                                  "wire_action" if k == "wire_action" else "alpine_wire", action=ref.name)
                elif k == "wire_model" and owner is not None and ref.name not in models:
                    models.append(ref.name)
            if owner is not None:
                info: dict = {}
                if owner.kind == "class":
                    info["component"] = owner.fqn
                if models:
                    info["models"] = models
                if info:
                    meta = fnode.setdefault("metadata", {})
                    if isinstance(meta, dict):
                        meta["livewire"] = info
        self._drop_legacy()

    # ── pieces ──────────────────────────────────────────────────────────────

    @staticmethod
    def _blade_metadata(fnode: dict, scan) -> None:
        info = {
            k: sorted(v) for k, v in (
                ("sections", scan.sections), ("yields", scan.yields),
                ("pushes", scan.pushes), ("stacks", scan.stacks),
            ) if v
        }
        if info:
            meta = fnode.setdefault("metadata", {})
            if isinstance(meta, dict):
                meta["blade"] = info

    def _emit_sfc(self, rel: str, fnode: dict, entry: tuple[Sfc, str, str]) -> None:
        sfc, cid, fid = entry
        sf = fnode["source_file"]
        self.ids.add(cid)
        self.nodes.append({
            "id": cid, "label": sfc.name, "file_type": "code", "source_file": sf,
            "source_location": f"L{sfc.class_line}",
            "metadata": {"kind": "livewire_component", "single_file": True, "name": sfc.name},
        })
        self.edge(fid, cid, "contains", "EXTRACTED", sf, sfc.class_line, "single_file_component")
        for name, line in sfc.methods:
            mid = _make_id(cid, name)
            self.ids.add(mid)
            self.nodes.append({
                "id": mid, "label": f".{name}()", "file_type": "code", "source_file": sf,
                "source_location": f"L{line}",
            })
            self.edge(cid, mid, "method", "EXTRACTED", sf, line, "single_file_component")

    def _drop_legacy(self) -> None:
        """Flag the legacy extractor's stub nodes and edges of every Blade file handled here."""
        stub_ids: dict[str, set[str]] = {}   # blade file node id -> its stub ids
        for rel, stubs in self.legacy_stubs.items():
            fnode = self.blade_nodes.get(rel)
            if rel in self.fresh and fnode is not None:
                stub_ids[fnode["id"]] = {n["id"] for n in stubs}
                for n in stubs:
                    n["_drop"] = True
        if not stub_ids:
            return
        for e in self.all_edges:
            targets = stub_ids.get(e.get("source", ""))
            if (
                targets and e.get("target") in targets and e.get("relation") in _LEGACY_RELATIONS
                and e.get("source_location") is None
            ):
                e["_drop"] = True


def run_view_passes(
    root: Path, idx: SymbolIndex, per_file: list, all_nodes: list[dict], all_edges: list[dict]
) -> None:
    p = ViewPass(root, idx, per_file, all_nodes, all_edges)
    p.run()
    all_nodes.extend(p.nodes)
    all_edges.extend(p.edges)
