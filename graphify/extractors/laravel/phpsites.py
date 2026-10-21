"""Call sites in a PHP file and the graph node that owns each one."""
from __future__ import annotations

from typing import Iterator

from graphify.extractors.laravel.phpsrc import CLOSURE_TYPES, PhpFile, line_of, typed_nodes
from graphify.extractors.laravel.symbols import SymbolIndex
from graphify.extractors.laravel.webfiles import FileIndex

_CALLS = frozenset({"function_call_expression", "scoped_call_expression", "member_call_expression"})
_TYPES = frozenset({"class_declaration", "trait_declaration", "enum_declaration", "interface_declaration"})


def iter_calls(root) -> Iterator:
    """Every call expression under ``root``, in source order."""
    yield from typed_nodes(root, _CALLS)


def function_name(f: PhpFile, call) -> str | None:
    """Lower-cased last segment of a plain function call's name (``\\route`` -> ``route``)."""
    if call.type != "function_call_expression":
        return None
    fn = call.child_by_field_name("function")
    if fn is None or fn.type not in ("name", "qualified_name"):
        return None
    return f.text(fn).rsplit("\\", 1)[-1].strip().lower()


def method_name(f: PhpFile, call) -> str | None:
    if call.type not in ("scoped_call_expression", "member_call_expression"):
        return None
    name = call.child_by_field_name("name")
    return f.text(name) if name is not None else None


def scope_name(f: PhpFile, call) -> str | None:
    """Last segment of the class in ``Foo::bar()``."""
    if call.type != "scoped_call_expression":
        return None
    scope = call.child_by_field_name("scope")
    if scope is None or scope.type not in ("name", "qualified_name"):
        return None
    return f.text(scope).rsplit("\\", 1)[-1].strip()


class Owners:
    """Graph node for the callable (method, function, closure) around a PHP node."""

    def __init__(self, idx: SymbolIndex, files: FileIndex):
        self.idx = idx
        self.files = files

    def of(self, f: PhpFile, node) -> str | None:
        cur = node.parent
        while cur is not None:
            t = cur.type
            if t in CLOSURE_TYPES:
                nid = self.idx.closure_at(f.rel, line_of(cur))
                if nid:
                    return nid
            elif t == "method_declaration":
                nid = self._method(f, cur)
                if nid:
                    return nid
            elif t == "function_definition":
                name = cur.child_by_field_name("name")
                if name is not None:
                    hit = self.files.symbol_at(f.rel, [line_of(cur)], [f.text(name)])
                    if hit is not None:
                        return hit["id"]
            cur = cur.parent
        fnode = self.files.file_node(f.rel)
        return fnode["id"] if fnode else None

    def _method(self, f: PhpFile, method) -> str | None:
        name = method.child_by_field_name("name")
        owner = method.parent
        while owner is not None and owner.type not in _TYPES:
            owner = owner.parent
        if name is None or owner is None or f.ns is None:
            return None
        cls_name = owner.child_by_field_name("name")
        if cls_name is None:
            return None
        cls_text = f.text(cls_name)
        cls = self.idx.resolve_class(f"{f.ns}\\{cls_text}" if f.ns else cls_text)
        if cls is None:
            return None
        nid = self.idx.lookup_method(cls, f.text(name))
        node = self.idx.nodes.get(nid) if nid else None
        if node is None or self.idx.rel(node.get("source_file", "")) != f.rel:
            return None
        return nid
