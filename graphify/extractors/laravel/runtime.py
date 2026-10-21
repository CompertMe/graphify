"""Laravel runtime wiring: what the framework connects at run time, not in an import.

The route pass models HTTP entry points. This pass models the other half of the wiring
(``laravel_runtime`` resolver, same Laravel gate, runs after ``laravel_web``):

* ``console``: Artisan commands (``$signature`` / ``$name`` / ``#[AsCommand]`` / closure
  commands), the scheduler (``schedules``) and ``Artisan::call`` (``invokes_command``);
* ``dispatch``: queued jobs (``dispatches``), events (``dispatches_event``), listener and
  subscriber maps (``listens_to``), notifications (``notifies``), mail (``sends_mail``)
  and Eloquent observers (``observes``);
* ``container``: service-container bindings (``binds``) and facades (``facade_for``).

Every class name is resolved by ``PhpFile.class_fqn`` (``_php_fqn_from_raw``) and tied to a
node through ``SymbolIndex.resolve_class``: this module has no resolver of its own. A name
that is dynamic, resolves to no in-corpus class, or matches several, gets no edge.
``EXTRACTED`` is a fact the statement spells out; ``INFERRED`` is a convention or a
discovery rule (auto-discovered listeners, a job under ``Jobs/`` whose ``dispatch`` comes
from a trait we cannot see, a facade's second hop through a binding).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from graphify.extractors.laravel.phpread import (
    class_decls,
    methods_of,
    type_refs,
)
from graphify.extractors.laravel.phpsites import Owners
from graphify.extractors.laravel.phpsrc import PhpFile, load_php_file
from graphify.extractors.laravel.symbols import SymbolIndex
from graphify.extractors.laravel.webfiles import FileIndex

_SKIP_PREFIXES = ("vendor/", "tests/", "node_modules/")
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Loc:
    """A file reference for an edge recorded before the file's tree was dropped."""

    rel: str


@dataclass
class ClassFacts:
    """What a class declares and inherits, from source (not from graph edges, which an
    incremental rebuild may not carry for unchanged files)."""

    nid: str
    fqn: str
    vendor: frozenset[str]            # lower-cased FQNs of vendor ancestors / traits / interfaces
    methods: frozenset[str]           # lower-cased methods declared in its own body
    ancestors: tuple[str, ...] = ()   # in-corpus ancestor class node ids (nearest first)


@dataclass
class Runtime:
    root: Path
    idx: SymbolIndex
    files: FileIndex
    owners: Owners
    fresh: set[str]
    all_nodes: list[dict]
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    _emitted: dict[tuple[str, str, str], dict] = field(default_factory=dict)
    _facts: dict[str, ClassFacts | None] = field(default_factory=dict)
    _busy: set[str] = field(default_factory=set)
    meta: dict[str, dict] = field(default_factory=dict)
    ids: set[str] = field(default_factory=set)
    stubs: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.ids = {n["id"] for n in self.all_nodes if n.get("id")}
        for n in self.all_nodes:
            nid = n.get("id")
            label = str(n.get("label", ""))
            if nid and not n.get("source_file") and "\\" in label:
                self.stubs.setdefault(label.lstrip("\\").lower(), []).append(nid)

    # ── lookups ────────────────────────────────────────────────────────────
    def klass(self, fqn: str | None) -> str | None:
        return self.idx.resolve_class(fqn)

    def stub(self, fqn: str | None) -> str | None:
        """The one external stub node (a vendor class the corpus imports) named ``fqn``."""
        if not fqn:
            return None
        hits = self.stubs.get(fqn.lstrip("\\").lower(), [])
        return hits[0] if len(hits) == 1 else None

    def target(self, fqn: str | None) -> str | None:
        """In-corpus class node, else the external stub of a vendor class."""
        return self.klass(fqn) or self.stub(fqn)

    def owner(self, f: PhpFile, node) -> str | None:
        return self.owners.of(f, node)

    def facts(self, nid: str | None) -> ClassFacts | None:
        if not nid:
            return None
        if nid in self._facts:
            return self._facts[nid]
        if nid in self._busy:
            return None  # inheritance cycle
        self._busy.add(nid)
        try:
            out = self._read_facts(nid)
        finally:
            self._busy.discard(nid)
        self._facts[nid] = out
        return out

    def _read_facts(self, nid: str) -> ClassFacts | None:
        node = self.idx.nodes.get(nid)
        if node is None or not node.get("source_file"):
            return None
        rel = self.idx.rel(node["source_file"])
        f = load_php_file(self.root / rel, rel)
        if f is None or f.ns is None:
            return None
        label = str(node.get("label", ""))
        decl = next((d for d in class_decls(f) if d.name == label), None)
        if decl is None or decl.fqn is None:
            return None
        vendor: set[str] = set()
        ancestors: list[str] = []
        for raw in type_refs(f, decl.node):
            fqn = f.class_fqn(raw)
            if not fqn:
                continue
            parent = self.klass(fqn)
            if parent is None:
                vendor.add(fqn.lower())
                continue
            ancestors.append(parent)
            pf = self.facts(parent)
            if pf is not None:
                vendor |= pf.vendor
                ancestors.extend(a for a in pf.ancestors if a not in ancestors)
        methods = frozenset(methods_of(f, decl.node))
        return ClassFacts(nid, decl.fqn, frozenset(vendor), methods, tuple(ancestors))

    def declares(self, nid: str, method: str) -> bool:
        """Does the class (or an in-corpus ancestor / trait) declare ``method``?"""
        return self.idx.lookup_method(nid, method) is not None

    # ── emit ───────────────────────────────────────────────────────────────
    def edge(
        self, src: str | None, tgt: str | None, relation: str, conf: str, f: PhpFile | Loc, line: int, context: str,
        **extra,
    ) -> dict | None:
        """One edge per (source, target, relation): the first site wins, unless a later
        ``EXTRACTED`` site replaces an ``INFERRED`` one."""
        if not src or not tgt or src == tgt:
            return None
        fnode = self.files.file_node(f.rel)
        if fnode is None:
            return None
        edge = {
            "source": src, "target": tgt, "relation": relation, "confidence": conf,
            "source_file": fnode["source_file"], "source_location": f"L{line}",
            "weight": 1.0, "context": context,
        }
        edge.update(extra)
        key = (src, tgt, relation)
        prior = self._emitted.get(key)
        if prior is not None:
            if prior["confidence"] == "INFERRED" and conf == "EXTRACTED":
                prior.clear()
                prior.update(edge)
            return prior
        self._emitted[key] = edge
        self.edges.append(edge)
        return edge

    def new_id(self, *parts: str) -> str:
        from graphify.extractors.base import _make_id

        base = _make_id(*parts)
        nid, n = base, 1
        while nid in self.ids:
            n += 1
            nid = f"{base}_{n}"
        self.ids.add(nid)
        return nid

    def add_node(self, f: PhpFile, nid: str, label: str, line: int, meta: dict) -> dict | None:
        fnode = self.files.file_node(f.rel)
        if fnode is None:
            return None
        node = {
            "id": nid, "label": label, "file_type": "code", "source_file": fnode["source_file"],
            "source_location": f"L{line}", "metadata": meta,
        }
        self.nodes.append(node)
        self.edge(fnode["id"], nid, "contains", "EXTRACTED", f, line, meta.get("kind", "runtime"))
        return node

    def annotate(self, nid: str, **meta) -> None:
        self.meta.setdefault(nid, {}).update(meta)


def run(root: Path, per_file: list, all_nodes: list[dict], all_edges: list[dict]) -> None:
    from graphify.extractors.laravel import console, container, dispatch

    idx = SymbolIndex(root, all_nodes, all_edges)
    if not idx.php_files:
        return
    files = FileIndex(idx, all_nodes)
    fresh = {idx.rel(n["source_file"]) for r in per_file if r for n in r.get("nodes", []) if n.get("source_file")}
    rt = Runtime(root, idx, files, Owners(idx, files), fresh, all_nodes)
    cons = console.Console(rt)
    cont = container.Container(rt)
    disp = dispatch.Dispatch(rt)
    deferred: dict[str, PhpFile] = {}

    for rel in sorted(idx.php_files):
        if rel.startswith(_SKIP_PREFIXES):
            continue
        try:
            raw = (root / rel).read_bytes()
        except OSError:
            continue
        is_fresh = rel in fresh
        want_cons = cons.wants(raw)
        want_cont = cont.wants(raw)
        want_disp = is_fresh and disp.wants(rel, raw)
        want_defer = is_fresh and cons.wants_sites(raw)
        if not (want_cons or want_cont or want_disp or want_defer):
            continue
        f = load_php_file(root / rel, rel)
        if f is None:
            continue
        if want_cons:
            cons.collect(f)
        if want_cont:
            cont.collect(f)
        if want_disp:
            disp.scan(f)
        if want_defer:
            deferred[rel] = f
    disp.finish()
    cont.finish()
    cons.finish(deferred)

    if rt.meta:
        for n in all_nodes:
            extra = rt.meta.get(n.get("id", ""))
            if extra and n.get("source_file"):
                # In place: an incremental rebuild resolves over a scratch list that shares
                # these node dicts with the real one.
                merged = dict(n["metadata"]) if isinstance(n.get("metadata"), dict) else {}
                merged.update(extra)
                n["metadata"] = merged
    all_nodes.extend(rt.nodes)
    all_edges.extend(rt.edges)


__all__ = ["ClassFacts", "Loc", "Runtime", "run"]
