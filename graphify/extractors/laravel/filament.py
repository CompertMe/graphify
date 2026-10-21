"""Filament resources: the model, the pages and the relation managers they declare.

A resource is a class whose ``extends`` chain reaches ``Filament\\Resources\\Resource``
(decided by the caller from the resolved ``inherits`` edges). Read from its body:

* ``protected static ?string $model = Patient::class;`` -> the model;
* ``getPages()``: ``'index' => Pages\\ListPatients::route('/')`` (or ``ListPatients::class``)
  -> page classes, with the array key and the route as written;
* ``getRelations()``: ``RelationManagers\\VisitsRelationManager::class``, also inside
  ``RelationGroup::make('label', [A::class, B::class])`` -> relation manager classes.

Class names go through ``PhpFile.class_fqn`` (the PHP name-resolution rules), so
``use App\\Filament\\Resources\\XResource\\Pages;`` followed by ``Pages\\ListX::route()`` and
the namespace-relative ``RelationManagers\\VisitsRelationManager::class`` both resolve to
the class the source means. Not modelled: ``getModel()`` overrides, pages registered from
a trait / ``parent::getPages()`` spreads, relation managers built with ``::make()``, and
cluster / nested-resource registration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from graphify.extractors.laravel.phpdecl import ClassView, find_method, property_default, return_exprs, scoped_parts
from graphify.extractors.laravel.phpsrc import PhpFile, args_of, class_ref, line_of, positional, str_value

RESOURCE_BASES = frozenset({"filament\\resources\\resource"})


@dataclass
class PageRef:
    fqn: str
    line: int
    key: str | None
    route: str | None


@dataclass
class ResourceFacts:
    model: str | None
    model_line: int
    pages: list[PageRef]
    relations: list[PageRef]


def _entry_class(f: PhpFile, node) -> tuple[str, str | None] | None:
    """``(FQN, route literal)`` of ``X::class`` / ``X::route('/path')``."""
    if node is None:
        return None
    fqn = class_ref(f, node)
    if fqn:
        return fqn, None
    if node.type == "scoped_call_expression":
        parts = scoped_parts(f, node)
        if parts is not None and parts[1].lower() == "route":
            fq = f.class_fqn(parts[0])
            if fq is None:
                return None
            args = positional(args_of(node))
            return fq, (str_value(f, args[0].node) if args else None)
    return None


def _array_entries(f: PhpFile, expr) -> list[tuple[str | None, Any]]:
    if expr is None or expr.type != "array_creation_expression":
        return []
    out: list[tuple[str | None, Any]] = []
    for el in expr.children:
        if el.type != "array_element_initializer":
            continue
        named = [c for c in el.children if c.is_named]
        if len(named) == 2:
            out.append((str_value(f, named[0]), named[1]))
        elif len(named) == 1 and named[0].type != "variadic_unpacking":
            out.append((None, named[0]))
    return out


def _collect_classes(f: PhpFile, expr, out: list[PageRef]) -> None:
    for key, value in _array_entries(f, expr):
        entry = _entry_class(f, value)
        if entry is not None:
            out.append(PageRef(entry[0], line_of(value), key, entry[1]))
            continue
        if value.type == "array_creation_expression":
            _collect_classes(f, value, out)
        elif value.type == "scoped_call_expression":        # RelationGroup::make('label', [A::class, ...])
            for a in positional(args_of(value)):
                if a.node is not None and a.node.type == "array_creation_expression":
                    _collect_classes(f, a.node, out)


def read_resource(f: PhpFile, cls: ClassView) -> ResourceFacts:
    model_node = property_default(f, cls, "model")
    model = class_ref(f, model_node) if model_node is not None else None
    pages: list[PageRef] = []
    relations: list[PageRef] = []
    for name, sink in (("getPages", pages), ("getRelations", relations)):
        m = find_method(f, cls, name)
        if m is None:
            continue
        for expr in return_exprs(m):
            _collect_classes(f, expr, sink)
    return ResourceFacts(model, line_of(model_node) if model_node is not None else 0, pages, relations)


__all__ = ["RESOURCE_BASES", "PageRef", "ResourceFacts", "read_resource"]
