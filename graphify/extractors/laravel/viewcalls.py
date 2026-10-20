"""PHP code that renders a view: ``view('a.b')`` and its siblings.

Reads one PHP tree and returns a ``ViewCall`` per site: the literal view name(s), the
line, the form and the chain of enclosing callables (nearest first) so the pass can
source the edge at the method or closure node. A name that is not a literal (a
variable, an interpolation, an argument the view-name helper cannot fill) is returned
as ``None`` and produces no edge, but stays in the result so a caller can tell that a
``render()`` is not a single known view.

Forms: ``view()``, ``View::make``, ``view()->make``, ``->layout()`` (Livewire), ``->view()``/``->markdown()`` (mailables,
notifications, ``response()->view``), ``loadView`` (PDF renderers), ``new Content(view:,
markdown:, html:, text:)``, a ``$view`` class property, and a call to a view-name helper
(``theme_view``). ``Route::view`` is modelled from the route node, not here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from graphify.extractors.laravel.phpsrc import (
    CLOSURE_TYPES,
    PhpFile,
    args_of,
    array_items,
    line_of,
    named,
    positional,
    str_value,
)
from graphify.extractors.laravel.views import _FACADES, ViewHelper, iter_nodes

_CONTENT = "Illuminate\\Mail\\Mailables\\Content"
_CLASS_LIKE = ("class_declaration", "trait_declaration", "enum_declaration", "interface_declaration")
_MEMBER_NAMES = frozenset({"view", "markdown", "loadview", "layout"})  # layout: Livewire's ->layout('x')


@dataclass
class ViewCall:
    line: int
    form: str
    names: list[str | None]
    owners: list[tuple] = field(default_factory=list)  # ("method", name, start, end) | ("function", ..)
    #                                                    | ("closure", line) | ("class", name)


class _ArgsHolder:
    """``new X(...)`` has no ``arguments`` field in the grammar: present its list as one."""

    def __init__(self, arguments):
        self._arguments = arguments

    def child_by_field_name(self, name: str):
        return self._arguments if name == "arguments" else None


def owners_of(f: PhpFile, node) -> list[tuple]:
    out: list[tuple] = []
    cur = node.parent
    while cur is not None:
        t = cur.type
        if t in ("method_declaration", "function_definition"):
            name = cur.child_by_field_name("name")
            if name is not None:
                out.append(
                    ("method" if t == "method_declaration" else "function", f.text(name),
                     line_of(cur), cur.end_point[0] + 1)
                )
        elif t in CLOSURE_TYPES:
            out.append(("closure", line_of(cur)))
        elif t in _CLASS_LIKE:
            name = cur.child_by_field_name("name")
            if name is not None:
                out.append(("class", f.text(name), line_of(cur), cur.end_point[0] + 1))
        cur = cur.parent
    return out


def eval_name(f: PhpFile, node, helpers: dict[str, ViewHelper]) -> str | None:
    """A literal view name, or the name a view-name helper call spells out."""
    if node is None:
        return None
    s = str_value(f, node)
    if s is not None:
        return s
    if node.type == "function_call_expression":
        fn = node.child_by_field_name("function")
        helper = helpers.get(f.text(fn).lstrip("\\").lower()) if fn is not None else None
        if helper is not None:
            args = args_of(node)
            if any(a.name is not None for a in args):
                return None
            return helper.view_name([str_value(f, a.node) for a in args])
    return None


def _names(f: PhpFile, node, helpers) -> list[str | None]:
    items = array_items(node)
    if items is not None:
        return [eval_name(f, v, helpers) for _k, v in items]
    return [eval_name(f, node, helpers)]


def _is_view_factory_call(f: PhpFile, node) -> bool:
    """``view()`` with no arguments: the view factory (``view()->make('x')``)."""
    if node is None or node.type != "function_call_expression":
        return False
    fn = node.child_by_field_name("function")
    return fn is not None and f.text(fn).lstrip("\\").lower() == "view" and not args_of(node)


def find_view_calls(f: PhpFile, helpers: dict[str, ViewHelper]) -> list[ViewCall]:
    calls: list[ViewCall] = []

    def add(node, form: str, names: list[str | None]) -> None:
        if names:
            calls.append(ViewCall(line_of(node), form, names, owners_of(f, node)))

    for n in iter_nodes(f.root):
        t = n.type
        if t == "function_call_expression":
            fn = n.child_by_field_name("function")
            fname = f.text(fn).lstrip("\\").lower() if fn is not None else ""
            args = args_of(n)
            if fname == "view" and args:
                first = named(args, "view") or (positional(args)[0] if positional(args) else None)
                if first is not None:
                    add(n, "view()", [eval_name(f, first.node, helpers)])
            elif fname in helpers:
                add(n, "view_helper", [eval_name(f, n, helpers)])
        elif t == "scoped_call_expression":
            name_node = n.child_by_field_name("name")
            scope = n.child_by_field_name("scope")
            if name_node is None or scope is None:
                continue
            name = f.text(name_node).lower()
            pos = positional(args_of(n))
            if name == "make" and pos and scope.type in ("name", "qualified_name") \
                    and f.class_fqn(f.text(scope)) in _FACADES["view"]:
                add(n, "View::make", [eval_name(f, pos[0].node, helpers)])
            elif name == "loadview" and pos:
                add(n, "loadView", [eval_name(f, pos[0].node, helpers)])
        elif t == "member_call_expression":
            name_node = n.child_by_field_name("name")
            if name_node is None:
                continue
            name = f.text(name_node).lower()
            pos = positional(args_of(n))
            if not pos:
                continue
            if name in _MEMBER_NAMES:
                add(n, "->" + name + "()", _names(f, pos[0].node, helpers))
            elif name == "make" and _is_view_factory_call(f, n.child_by_field_name("object")):
                add(n, "view()->make", [eval_name(f, pos[0].node, helpers)])
        elif t == "object_creation_expression":
            cls = next((c for c in n.children if c.type in ("name", "qualified_name")), None)
            if cls is None or f.class_fqn(f.text(cls)) != _CONTENT:
                continue
            arg_list = next((c for c in n.children if c.type == "arguments"), None)
            args = args_of(_ArgsHolder(arg_list)) if arg_list is not None else []
            names: list[str | None] = []
            pos = positional(args)
            if pos:
                names.append(eval_name(f, pos[0].node, helpers))
            for key in ("view", "markdown", "html", "text"):
                arg = named(args, key)
                if arg is not None:
                    names.append(eval_name(f, arg.node, helpers))
            add(n, "Content", names)
        elif t == "property_declaration":
            owner = owners_of(f, n)
            if not owner or owner[0][0] != "class":
                continue
            for el in n.children:
                if el.type != "property_element":
                    continue
                var = next((c for c in el.children if c.type == "variable_name"), None)
                if var is None or f.text(var).lstrip("$") != "view":
                    continue
                default = [c for c in el.children if c.is_named and c.id != var.id]
                val = str_value(f, default[0]) if default else None
                if val is not None:
                    calls.append(ViewCall(line_of(el), "view_property", [val], owner))
    return calls
