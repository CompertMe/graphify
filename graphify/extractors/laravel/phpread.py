"""AST readers shared by the Laravel runtime-wiring passes (console, dispatch, container).

Everything here reads facts from a tree-sitter-php tree through ``PhpFile`` (so a class
name is always resolved by ``PhpFile.class_fqn`` -> ``_php_fqn_from_raw``, the one PHP
name-resolution rule set). A value that is not a literal comes back as ``None``: the
caller emits no edge for it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from graphify.extractors.laravel.phpsrc import (
    CLOSURE_TYPES,
    Arg,
    PhpFile,
    array_items,
    class_const_raw,
    line_of,
    str_value,
    typed_nodes,
)
from graphify.extractors.laravel.views import iter_nodes

_CALL_TYPES = frozenset({"function_call_expression", "scoped_call_expression", "member_call_expression"})
TYPE_DECLS = frozenset({"class_declaration", "trait_declaration", "interface_declaration", "enum_declaration"})
FACADE_NS = "illuminate\\support\\facades\\"


@dataclass
class ClassDecl:
    node: Any
    name: str
    fqn: str | None          # None: several namespaces in one file


def class_decls(f: PhpFile) -> list[ClassDecl]:
    out: list[ClassDecl] = []
    for n in typed_nodes(f.root, TYPE_DECLS):
        name = n.child_by_field_name("name")
        if name is None:
            continue
        text = f.text(name)
        fqn = None if f.ns is None else (f"{f.ns}\\{text}" if f.ns else text)
        out.append(ClassDecl(n, text, fqn))
    return out


def class_body(node):
    return node.child_by_field_name("body") or next((c for c in node.children if c.type == "declaration_list"), None)


def methods_of(f: PhpFile, cls_node) -> dict[str, Any]:
    """``{lower name: method_declaration node}`` declared directly in the class body."""
    body = class_body(cls_node)
    out: dict[str, Any] = {}
    for m in (body.children if body is not None else []):
        if m.type == "method_declaration":
            name = m.child_by_field_name("name")
            if name is not None:
                out.setdefault(f.text(name).lower(), m)
    return out


def property_value_node(f: PhpFile, cls_node, name: str):
    """Default-value node of the ``$name`` property declared in the class body."""
    body = class_body(cls_node)
    for m in (body.children if body is not None else []):
        if m.type != "property_declaration":
            continue
        for el in m.children:
            if el.type != "property_element":
                continue
            var = next((c for c in el.children if c.type == "variable_name"), None)
            if var is None or f.text(var).lstrip("$") != name:
                continue
            rest = [c for c in el.children if c.is_named and c.id != var.id]
            return rest[0] if rest else None
    return None


def has_property(f: PhpFile, cls_node, name: str) -> bool:
    body = class_body(cls_node)
    for m in (body.children if body is not None else []):
        if m.type == "property_declaration":
            for el in m.children:
                var = next((c for c in el.children if c.type == "variable_name"), None) if el.type == "property_element" else None
                if var is not None and f.text(var).lstrip("$") == name:
                    return True
    return False


def type_refs(f: PhpFile, cls_node) -> list[str]:
    """Raw names written in ``extends`` / ``implements`` / trait ``use`` of a type."""
    out: list[str] = []
    for c in cls_node.children:
        if c.type in ("base_clause", "class_interface_clause"):
            out.extend(f.text(x) for x in c.children if x.type in ("name", "qualified_name"))
    body = class_body(cls_node)
    for m in (body.children if body is not None else []):
        if m.type == "use_declaration":
            out.extend(f.text(x) for x in m.children if x.type in ("name", "qualified_name"))
    return out


# ── attributes ─────────────────────────────────────────────────────────────

def attributes(f: PhpFile, node) -> list[tuple[str, list[Arg]]]:
    """``[(attribute FQN, arguments)]`` of ``#[...]`` groups on a class or method node."""
    out: list[tuple[str, list[Arg]]] = []
    for c in node.children:
        if c.type != "attribute_list":
            continue
        for group in c.children:
            if group.type != "attribute_group":
                continue
            for attr in group.children:
                if attr.type != "attribute":
                    continue
                name = next((x for x in attr.children if x.type in ("name", "qualified_name")), None)
                if name is None:
                    continue
                fqn = f.class_fqn(f.text(name))
                if fqn:
                    out.append((fqn, _attr_args(attr)))
    return out


def _attr_args(attr) -> list[Arg]:
    args = next((c for c in attr.children if c.type == "arguments"), None)
    out: list[Arg] = []
    for a in (args.children if args is not None else []):
        if a.type != "argument":
            continue
        name = None
        value = None
        label = a.child_by_field_name("name")
        for c in a.children:
            if not c.is_named:
                continue
            if label is not None and c.id == label.id:
                name = c.text.decode()
                continue
            value = c
        out.append(Arg(name=name, node=value))
    return out


# ── names and class references ─────────────────────────────────────────────

def name_node_text(f: PhpFile, node) -> str | None:
    if node is not None and node.type in ("name", "qualified_name"):
        return f.text(node).strip()
    return None


def new_class_fqn(f: PhpFile, node) -> str | None:
    """FQN of ``new Foo(...)``; None for ``new static``, ``new $cls``, anonymous classes."""
    if node is None or node.type != "object_creation_expression":
        return None
    for c in node.children:
        if c.type in ("name", "qualified_name"):
            return f.class_fqn(f.text(c))
        if c.type in ("variable_name", "relative_scope", "anonymous_class", "member_access_expression"):
            return None
        if c.type == "arguments":
            break
    return None


def class_arg_fqn(f: PhpFile, node) -> str | None:
    """FQN of a ``new Foo`` or ``Foo::class`` expression (after unwrapping parentheses)."""
    while node is not None and node.type == "parenthesized_expression":
        inner = [c for c in node.children if c.is_named]
        node = inner[0] if len(inner) == 1 else None
    if node is None:
        return None
    if node.type == "object_creation_expression":
        return new_class_fqn(f, node)
    raw = class_const_raw(f, node)
    return f.class_fqn(raw) if raw else None


def string_class_fqn(value: str | None) -> str | None:
    """A literal string naming a class (``'App\\Jobs\\X'``), else None."""
    if not value or not all(p.isidentifier() for p in value.strip("\\").split("\\")):
        return None
    return value.strip("\\")


def enclosing_type(f: PhpFile, node) -> str | None:
    """FQN of the class/trait around ``node``."""
    cur = node.parent
    while cur is not None:
        if cur.type in TYPE_DECLS:
            name = cur.child_by_field_name("name")
            if name is None or f.ns is None:
                return None
            return f"{f.ns}\\{f.text(name)}" if f.ns else f.text(name)
        cur = cur.parent
    return None


def class_ref_in_scope(f: PhpFile, node, scope_node) -> str | None:
    """FQN of the class a scope expression names: ``Foo``, ``\\A\\Foo``, ``self`` / ``static``."""
    if scope_node is None:
        return None
    if scope_node.type in ("name", "qualified_name"):
        return f.class_fqn(f.text(scope_node))
    if scope_node.type == "relative_scope" and f.text(scope_node).lower() in ("self", "static"):
        return enclosing_type(f, node)
    return None


def facade_of(f: PhpFile, scope_node) -> str | None:
    """Lower-cased facade name (``bus``, ``event``) when the scope is that Laravel facade."""
    raw = name_node_text(f, scope_node)
    if raw is None:
        return None
    fqn = f.class_fqn(raw)
    if not fqn:
        return None
    low = fqn.lower()
    if low.startswith(FACADE_NS):
        return low[len(FACADE_NS):]
    if "\\" not in low and f.ns == "" and raw.lstrip("\\").lower() == low:
        return low  # global alias (`Bus::` in a namespace-less file such as routes/console.php)
    return None


# ── call chains ────────────────────────────────────────────────────────────

def call_name(f: PhpFile, call) -> str | None:
    if call.type == "function_call_expression":
        fn = call.child_by_field_name("function")
        if fn is not None and fn.type in ("name", "qualified_name"):
            return f.text(fn).rsplit("\\", 1)[-1].strip().lower()
        return None
    name = call.child_by_field_name("name")
    return f.text(name) if name is not None else None


def call_args(call) -> list[Arg]:
    args = call.child_by_field_name("arguments")
    out: list[Arg] = []
    for a in (args.children if args is not None else []):
        if a.type != "argument":
            continue
        label = a.child_by_field_name("name")
        name = None
        value = None
        for c in a.children:
            if not c.is_named:
                continue
            if label is not None and c.id == label.id:
                name = c.text.decode()
                continue
            value = c
        out.append(Arg(name=name, node=value))
    return out


def positional_args(call) -> list:
    return [a.node for a in call_args(call) if a.name is None]


def arg_node(call, index: int, name: str | None = None):
    """Positional argument ``index`` or the named argument ``name``."""
    pos = positional_args(call)
    if index < len(pos):
        return pos[index]
    if name is not None:
        for a in call_args(call):
            if a.name == name:
                return a.node
    return None


def chain_after(call) -> list:
    """Member calls applied to ``call``'s result, in order: ``a()->b()->c()`` from ``a``."""
    out: list = []
    cur = call
    while True:
        parent = cur.parent
        if parent is not None and parent.type == "member_call_expression":
            obj = parent.child_by_field_name("object")
            if obj is not None and obj.id == cur.id:
                out.append(parent)
                cur = parent
                continue
        return out


def chain_root(node):
    """Innermost receiver of a ``a::b()->c()->d()`` chain (the first call or variable)."""
    cur = node
    while cur is not None and cur.type == "member_call_expression":
        nxt = cur.child_by_field_name("object")
        if nxt is None:
            break
        cur = nxt
    return cur


def array_values(node) -> list | None:
    """Value nodes of an array literal, None for anything else (or a spread)."""
    items = array_items(node)
    return None if items is None else [v for _k, v in items]


def closure_returned_class(f: PhpFile, closure) -> str | None:
    """Class a closure creates: ``fn () => new B``, ``function () { return new B; }`` and
    the same with ``$app->make(B::class)`` / ``app(B::class)`` / ``resolve(B::class)``."""
    if closure is None or closure.type not in CLOSURE_TYPES:
        return None
    if closure.type == "arrow_function":
        body = closure.child_by_field_name("body")
        return _created_class(f, body)
    body = closure.child_by_field_name("body")
    if body is None:
        return None
    stmts = [c for c in body.children if c.is_named and c.type != "comment"]
    if len(stmts) != 1 or stmts[0].type != "return_statement":
        return None
    expr = next((c for c in stmts[0].children if c.is_named), None)
    return _created_class(f, expr)


def _created_class(f: PhpFile, expr) -> str | None:
    while expr is not None and expr.type == "parenthesized_expression":
        inner = [c for c in expr.children if c.is_named]
        expr = inner[0] if len(inner) == 1 else None
    if expr is None:
        return None
    if expr.type == "object_creation_expression":
        return new_class_fqn(f, expr)
    if expr.type in ("function_call_expression", "member_call_expression", "scoped_call_expression"):
        name = call_name(f, expr)
        if name is not None and name.lower() in ("make", "app", "resolve", "get"):
            arg = arg_node(expr, 0)
            raw = class_const_raw(f, arg) if arg is not None else None
            if raw and (expr.type == "function_call_expression" or container_receiver(f, expr)):
                return f.class_fqn(raw)
    return None


def container_receiver(f: PhpFile, call) -> bool:
    """Is the call made on the service container: ``$this->app->``, ``$app->``, ``app()->``, ``App::``?"""
    if call.type == "scoped_call_expression":
        return facade_of(f, call.child_by_field_name("scope")) == "app"
    obj = call.child_by_field_name("object")
    if obj is None:
        return False
    text = f.text(obj).replace(" ", "")
    return text in ("$this->app", "$app", "$this->container", "$container", "app()", "\\app()")


def enclosing_callable(node):
    cur = node.parent
    while cur is not None:
        if cur.type in CLOSURE_TYPES or cur.type in ("method_declaration", "function_definition"):
            return cur
        cur = cur.parent
    return None


def is_conditional(node) -> bool:
    """Is ``node`` nested in an ``if`` / ``switch`` / ``match`` / ternary inside its callable?"""
    cur = node.parent
    while cur is not None and cur.type not in ("method_declaration", "function_definition") and cur.type not in CLOSURE_TYPES:
        if cur.type in ("if_statement", "else_clause", "else_if_clause", "switch_statement", "match_expression",
                        "conditional_expression", "try_statement", "catch_clause"):
            return True
        cur = cur.parent
    return False


def param_types(f: PhpFile, callable_node) -> dict[str, list[str]]:
    """``{variable name: [FQN of each named type]}`` of a method / closure's parameters."""
    out: dict[str, list[str]] = {}
    params = callable_node.child_by_field_name("parameters")
    if params is None:
        params = next((c for c in callable_node.children if c.type == "formal_parameters"), None)
    for p in (params.children if params is not None else []):
        if p.type not in ("simple_parameter", "property_promotion_parameter"):
            continue
        var = next((c for c in p.children if c.type == "variable_name"), None)
        if var is None:
            continue
        names: list[str] = []
        for t in p.children:
            if t.type in ("named_type", "union_type", "optional_type", "name", "qualified_name"):
                for x in iter_nodes(t):
                    if x.type in ("name", "qualified_name") and (x.parent is None or x.parent.type != "qualified_name"):
                        names.append(f.text(x))
        out[f.text(var).lstrip("$")] = [n for n in (f.class_fqn(x) for x in names) if n]
    return out


def iter_calls_in(root) -> Iterator:
    yield from typed_nodes(root, _CALL_TYPES)


__all__ = [
    "ClassDecl", "attributes", "arg_node", "array_values", "call_args", "call_name", "chain_after",
    "chain_root", "class_arg_fqn", "class_body", "class_decls", "class_ref_in_scope", "closure_returned_class",
    "enclosing_callable", "enclosing_type", "facade_of", "has_property", "is_conditional", "iter_calls_in",
    "line_of", "methods_of", "name_node_text", "new_class_fqn", "param_types", "positional_args",
    "property_value_node", "str_value", "string_class_fqn", "type_refs",
]
