"""Service-container bindings and facades (see ``runtime``).

* ``binds``: abstract -> concrete for ``$this->app->bind|singleton|scoped|instance(A::class,
  B::class | fn () => new B | fn ($app) => $app->make(B::class))`` (and the ``*If`` forms),
  ``app()->bind``, ``App::bind`` and the ``$bindings`` / ``$singletons`` arrays of a service
  provider. ``EXTRACTED``: the statement names both sides. The abstract side is the in-corpus
  class, else the external stub of the imported vendor class (``Fortify\\Contracts\\...``),
  else (a string alias like ``'reports'``) the registering method itself, with the alias in
  ``abstract``. An abstract bound to several different concretes keeps every edge and says so
  in ``alternatives``; ``conditional`` marks a binding nested in an ``if`` / ``match``.
  A closure that does anything but create one class, or a self-binding
  (``singleton(A::class)``), gives no edge.
* ``facade_for``: an in-corpus facade (a class declaring ``getFacadeAccessor()``) ->
  the class its accessor names (``X::class``) or, for a string alias, the one concrete the
  alias is bound to (``EXTRACTED``); when the accessor is an abstract bound to exactly one
  readable concrete, a second edge to that concrete is ``INFERRED`` (a test or another
  environment may rebind it).

Not modelled: contextual binding (``when()->needs()->give()``), ``extend``, ``alias()``,
bindings made by a package, and resolving ``$this->contract->method()`` to the concrete
method (the typed-receiver call edge still points at the contract's method).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from graphify.extractors.laravel.phpread import (
    arg_node,
    call_name,
    class_decls,
    closure_returned_class,
    container_receiver,
    facade_of,
    is_conditional,
    iter_calls_in,
    methods_of,
    new_class_fqn,
    property_value_node,
    string_class_fqn,
    type_refs,
)
from graphify.extractors.laravel.phpsrc import (
    CLOSURE_TYPES,
    PhpFile,
    array_items,
    class_const_raw,
    line_of,
    str_value,
)
from graphify.extractors.laravel.runtime import Loc, Runtime

_WANT = re.compile(rb"bind\(|bindIf\(|singleton\(|singletonIf\(|scoped\(|scopedIf\(|instance\(|\$bindings|\$singletons|getFacadeAccessor")
_BIND = frozenset({"bind", "bindif", "singleton", "singletonif", "scoped", "scopedif", "instance"})


@dataclass
class Binding:
    key: str                  # lower-cased abstract FQN, or the alias as written
    abstract: str             # as written (FQN or alias)
    is_class: bool
    concrete: str | None      # FQN of the concrete class, None when it cannot be read
    rel: str
    line: int
    owner: str | None
    kind: str
    conditional: bool


@dataclass
class FacadeRec:
    nid: str
    accessor: str
    is_class: bool
    rel: str
    line: int


class Container:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.bindings: list[Binding] = []
        self.facades: list[FacadeRec] = []

    @staticmethod
    def wants(raw: bytes) -> bool:
        return bool(_WANT.search(raw))

    # ── collect ────────────────────────────────────────────────────────────
    def collect(self, f: PhpFile) -> None:
        if b"getFacadeAccessor" in f.src:
            self._facades(f)
        for decl in class_decls(f):
            if decl.node.type != "class_declaration":
                continue
            refs = type_refs(f, decl.node)
            if decl.name.endswith("ServiceProvider") or any(r.rsplit("\\", 1)[-1].endswith("ServiceProvider") for r in refs):
                owner = self.rt.klass(decl.fqn) if decl.fqn else None
                for prop, kind in (("bindings", "bind"), ("singletons", "singleton")):
                    node = property_value_node(f, decl.node, prop)
                    self._property_bindings(f, node, owner, kind)
        for call in iter_calls_in(f.root):
            name = call_name(f, call)
            if name is None or name.lower() not in _BIND:
                continue
            if call.type == "scoped_call_expression":
                if facade_of(f, call.child_by_field_name("scope")) != "app":
                    continue
            elif call.type != "member_call_expression" or not container_receiver(f, call):
                continue
            self._site(f, call, name.lower())

    def _abstract(self, f: PhpFile, node) -> tuple[str, str, bool] | None:
        """``(key, written, is_class)`` of the abstract argument."""
        if node is None:
            return None
        raw = class_const_raw(f, node)
        if raw:
            fqn = f.class_fqn(raw)
            return (fqn.lower(), fqn, True) if fqn else None
        lit = str_value(f, node)
        if not lit:
            return None
        fqn = string_class_fqn(lit)
        if fqn and "\\" in fqn:
            return fqn.lower(), fqn, True
        return lit, lit, False

    def _concrete(self, f: PhpFile, node, kind: str) -> str | None:
        if node is None:
            return None
        if node.type in CLOSURE_TYPES:
            return closure_returned_class(f, node)
        if kind == "instance":
            return new_class_fqn(f, node)
        raw = class_const_raw(f, node)
        if raw:
            return f.class_fqn(raw)
        fqn = string_class_fqn(str_value(f, node))
        return fqn if fqn and "\\" in fqn else None

    def _site(self, f: PhpFile, call, kind: str) -> None:
        abstract = self._abstract(f, arg_node(call, 0, "abstract"))
        if abstract is None or arg_node(call, 1, "concrete") is None:
            return  # unreadable, or a self-binding
        concrete = self._concrete(f, arg_node(call, 1, "concrete"), kind.removesuffix("if"))
        key, written, is_class = abstract
        self.bindings.append(Binding(
            key, written, is_class, concrete, f.rel, line_of(call), self.rt.owner(f, call),
            kind, is_conditional(call),
        ))

    def _property_bindings(self, f: PhpFile, node, owner: str | None, kind: str) -> None:
        for k, v in array_items(node) or []:
            if k is None:
                continue
            abstract = self._abstract(f, k)
            if abstract is None:
                continue
            key, written, is_class = abstract
            raw = class_const_raw(f, v)
            concrete = f.class_fqn(raw) if raw else None
            self.bindings.append(Binding(key, written, is_class, concrete, f.rel, line_of(k), owner, kind, False))

    def _facades(self, f: PhpFile) -> None:
        for decl in class_decls(f):
            if decl.node.type != "class_declaration" or decl.fqn is None:
                continue
            m = methods_of(f, decl.node).get("getfacadeaccessor")
            nid = self.rt.klass(decl.fqn)
            if m is None or nid is None:
                continue
            body = m.child_by_field_name("body")
            rets = [s for s in (body.children if body is not None else []) if s.type == "return_statement"]
            if len(rets) != 1:
                continue
            expr = next((c for c in rets[0].children if c.is_named), None)
            abstract = self._abstract(f, expr)
            if abstract is not None:
                key, written, is_class = abstract
                self.facades.append(FacadeRec(nid, written, is_class, f.rel, line_of(m)))

    # ── emit ───────────────────────────────────────────────────────────────
    def finish(self) -> None:
        rt = self.rt
        by_key: dict[str, list[Binding]] = {}
        for b in self.bindings:
            by_key.setdefault(b.key, []).append(b)
        for key in sorted(by_key):
            group = by_key[key]
            readable = {b.concrete.lower() for b in group if b.concrete}
            alternatives = len(readable) + sum(1 for b in group if not b.concrete)
            for b in group:
                if b.rel not in rt.fresh or not b.concrete:
                    continue
                concrete = rt.klass(b.concrete)
                if concrete is None:
                    continue
                abstract = rt.target(b.abstract) if b.is_class else None
                src = abstract or b.owner
                extra: dict = {"abstract": b.abstract, "binding": b.kind}
                if b.conditional:
                    extra["conditional"] = True
                if alternatives > 1:
                    extra["alternatives"] = alternatives
                rt.edge(src, concrete, "binds", "EXTRACTED", Loc(b.rel), b.line, "container_binding", **extra)
        self._facade_edges(by_key)

    def _unique_concrete(self, by_key: dict[str, list[Binding]], key: str) -> str | None:
        group = by_key.get(key, [])
        if not group or any(not b.concrete for b in group):
            return None
        concretes = {b.concrete.lower() for b in group if b.concrete}
        if len(concretes) != 1:
            return None
        return self.rt.klass(next(b.concrete for b in group if b.concrete))

    def _facade_edges(self, by_key: dict[str, list[Binding]]) -> None:
        rt = self.rt
        for fac in sorted(self.facades, key=lambda x: (x.rel, x.line)):
            rt.annotate(fac.nid, facade_accessor=fac.accessor)
            if fac.rel not in rt.fresh:
                continue
            loc = Loc(fac.rel)
            if fac.is_class:
                target = rt.target(fac.accessor)
                rt.edge(fac.nid, target, "facade_for", "EXTRACTED", loc, fac.line, "facade_accessor", accessor=fac.accessor)
                concrete = self._unique_concrete(by_key, fac.accessor.lower())
                if concrete and concrete != target:
                    rt.edge(fac.nid, concrete, "facade_for", "INFERRED", loc, fac.line, "facade_binding",
                            accessor=fac.accessor, via="binding")
            else:
                concrete = self._unique_concrete(by_key, fac.accessor)
                rt.edge(fac.nid, concrete, "facade_for", "EXTRACTED", loc, fac.line, "facade_alias",
                        accessor=fac.accessor, via="alias binding")


__all__ = ["Binding", "Container", "FacadeRec"]
