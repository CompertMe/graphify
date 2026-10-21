"""Class-body readers shared by the Laravel data-layer passes (Eloquent, validation, Filament).

Read straight from a tree-sitter-php tree: declared types with their FQN, property
defaults, a method's ``return`` expressions. Names are never resolved here beyond
``PhpFile.class_fqn`` (the one PHP name-resolution rule set); a value that is not a
literal comes back as ``None`` so the caller emits nothing for it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from graphify.extractors.laravel.phpsrc import CLOSURE_TYPES, PhpFile

_TYPE_KINDS = {
    "class_declaration": "class",
    "trait_declaration": "trait",
    "enum_declaration": "enum",
    "interface_declaration": "interface",
}
_FUNCTION_BOUNDARY = CLOSURE_TYPES | {"function_definition", "method_declaration", "anonymous_class", "class_declaration"}


@dataclass
class ClassView:
    node: Any
    name: str
    fqn: str | None
    kind: str
    abstract: bool
    body: Any

    @property
    def members(self) -> list:
        return list(self.body.children) if self.body is not None else []


def walk(root) -> Iterator:
    """Every node under ``root``, depth first, source order (iterative)."""
    stack = [root]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def class_views(f: PhpFile) -> list[ClassView]:
    """Every named class / trait / enum / interface declared in the file."""
    out: list[ClassView] = []
    for n in walk(f.root):
        kind = _TYPE_KINDS.get(n.type)
        if kind is None:
            continue
        name_node = n.child_by_field_name("name")
        if name_node is None:
            continue
        name = f.text(name_node)
        fqn = None
        if f.ns is not None:
            fqn = f"{f.ns}\\{name}" if f.ns else name
        body = n.child_by_field_name("body") or next((c for c in n.children if c.type == "declaration_list"), None)
        abstract = any(c.type == "abstract_modifier" for c in n.children)
        out.append(ClassView(n, name, fqn, kind, abstract, body))
    return out


def method_name(f: PhpFile, member) -> str | None:
    if member.type != "method_declaration":
        return None
    name = member.child_by_field_name("name")
    return f.text(name) if name is not None else None


def find_method(f: PhpFile, cls: ClassView, name: str):
    want = name.lower()
    for m in cls.members:
        mn = method_name(f, m)
        if mn is not None and mn.lower() == want:
            return m
    return None


def property_default(f: PhpFile, cls: ClassView, prop: str):
    """The default-value node of the first ``$prop`` declared in the class body, else None."""
    for m in cls.members:
        if m.type != "property_declaration":
            continue
        for el in m.children:
            if el.type != "property_element":
                continue
            var = next((c for c in el.children if c.type == "variable_name"), None)
            if var is None or f.text(var).lstrip("$") != prop:
                continue
            rest = [c for c in el.children if c.is_named and c.id != var.id]
            return rest[0] if rest else None
    return None


def return_exprs(method) -> list:
    """The expression of every ``return`` directly in a method (not in nested closures)."""
    body = next((c for c in method.children if c.type == "compound_statement"), None)
    out: list = []
    if body is None:
        return out
    stack = list(reversed(body.children))
    while stack:
        n = stack.pop()
        if n.type in _FUNCTION_BOUNDARY:
            continue
        if n.type == "return_statement":
            expr = next((c for c in n.children if c.is_named and c.type != "comment"), None)
            if expr is not None:
                out.append(expr)
            continue
        stack.extend(reversed(n.children))
    return out


def scoped_parts(f: PhpFile, node) -> tuple[str, str] | None:
    """``(scope as written, member name)`` of ``Scope::member`` / ``Scope::member(...)``."""
    if node.type == "scoped_call_expression":
        scope = node.child_by_field_name("scope")
        name = node.child_by_field_name("name")
    elif node.type == "class_constant_access_expression":
        named = [c for c in node.children if c.is_named and c.type != "comment"]
        if len(named) != 2:
            return None
        scope, name = named
    else:
        return None
    if scope is None or name is None or scope.type not in ("name", "qualified_name"):
        return None
    return f.text(scope).strip(), f.text(name)
