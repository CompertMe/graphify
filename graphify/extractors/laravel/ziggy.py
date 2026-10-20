"""Named-route references: ``route('tenant.branches.index')`` -> the route node.

Ziggy exposes Laravel's ``route()`` helper to JavaScript, so a ``.vue``/``.js``/
``.ts`` file names a route by the same string PHP and Blade use. Every call whose
name is a string *literal* and exists in the project's route table becomes one
edge to the route node(s) carrying that name (``route_name_index``):

* ``calls_route`` for ``route('x')`` / ``to_route('x')`` / ``redirect()->route('x')``
  / ``Redirect::route('x')`` / ``URL::route('x')`` and Blade ``route('x')``;
* ``references_route`` for ``route().current('x')`` (a comparison, not a call).

The source is the enclosing function (JS) or method/closure (PHP) when the graph
has a node for it, else the file; a Vue template ``:href="route('x')"`` belongs to
the component, i.e. the file. A name no route defines, a computed name
(``route(`a.${b}`)``, ``route(name)``) and ``route().current()`` wildcards get no
edge. One name on several route nodes (defined twice, e.g. per verb or per
branch of an ``if``) links all of them as ``INFERRED`` with ``ambiguous`` set.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from graphify.extractors.laravel.phpsites import (
    Owners,
    function_name,
    iter_calls,
    method_name,
    scope_name,
)
from graphify.extractors.laravel.phpsrc import PhpFile, args_of, line_of, named, positional, str_value
from graphify.extractors.laravel.webfiles import FileIndex

CALLS = "calls_route"
REFERENCES = "references_route"

_NAME = re.compile(r"^[\w.\-]+$")
_TEMPLATE_CALL = re.compile(r"(?<![\w.$])route\(\s*(['\"`])([\w.\-]+)\1")
_TEMPLATE_CURRENT = re.compile(r"(?<![\w.$])route\(\s*\)\s*\.current\(\s*(['\"`])([\w.\-]+)\1")
_BLADE_CALL = re.compile(r"(?<![\w.$>:\\])(?:to_)?route\(\s*(['\"])([\w.\-]+)\1")
_BLADE_COMMENT = re.compile(r"\{\{--.*?--\}\}", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
# Built or vendored output, matched on whole path segments (`Pages/Public/` is source).
_SKIP_ROOTS = ("public/", "vendor/", "storage/", "bootstrap/ssr/", "bootstrap/cache/")


@dataclass
class RouteRef:
    rel: str
    line: int
    name: str
    relation: str
    owner: str | None  # node id; None -> the file node


def skip_js(rel: str) -> bool:
    low = rel.lower()
    if "ziggy" in low or low.endswith(".min.js") or "/node_modules/" in "/" + low:
        return True
    return low.startswith(_SKIP_ROOTS)


# ── JS / Vue / TS ─────────────────────────────────────────────────────────


def scan_js(path: Path, rel: str, text: str, files: FileIndex) -> list[RouteRef]:
    if "route" not in text:
        return []
    out: list[RouteRef] = []
    from graphify.extractors.resolution import _parse_js_tree

    parsed = _parse_js_tree(path)
    if parsed is not None:
        src, root = parsed
        out.extend(_script_refs(rel, src, root, files))
    if rel.lower().endswith(".vue"):
        out.extend(_template_refs(rel, text))
    return out


def _script_refs(rel: str, src: bytes, root, files: FileIndex) -> list[RouteRef]:
    def text(n) -> str:
        return src[n.start_byte:n.end_byte].decode("utf-8", "replace")

    out: list[RouteRef] = []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.children))
        if node.type != "call_expression":
            continue
        fn = node.child_by_field_name("function")
        if fn is None:
            continue
        relation = None
        if fn.type == "identifier" and text(fn) == "route":
            relation = CALLS
        elif fn.type == "member_expression" and _is_route_current(fn, text):
            relation = REFERENCES
        if relation is None:
            continue
        name = _literal_arg(node, text)
        if name is None or not _NAME.match(name):
            continue
        owner = _js_owner(rel, node, files, text)
        out.append(RouteRef(rel, node.start_point[0] + 1, name, relation, owner))
    return out


def _is_route_current(member, text) -> bool:
    prop = member.child_by_field_name("property")
    obj = member.child_by_field_name("object")
    if prop is None or text(prop) != "current" or obj is None or obj.type != "call_expression":
        return False
    inner = obj.child_by_field_name("function")
    args = obj.child_by_field_name("arguments")
    return (
        inner is not None and inner.type == "identifier" and text(inner) == "route"
        and args is not None and not [c for c in args.children if c.is_named]
    )


def _literal_arg(call, text) -> str | None:
    args = call.child_by_field_name("arguments")
    first = next((c for c in (args.children if args is not None else []) if c.is_named), None)
    if first is None:
        return None
    if first.type == "string":
        raw = text(first)
        return raw[1:-1] if len(raw) >= 2 and "\\" not in raw else None
    if first.type == "template_string":
        if any(c.type == "template_substitution" for c in first.children):
            return None
        raw = text(first)
        return raw[1:-1] if len(raw) >= 2 and "\\" not in raw else None
    return None


def _js_owner(rel: str, node, files: FileIndex, text) -> str | None:
    """Innermost named declaration around ``node`` that has a graph node."""
    cur = node.parent
    while cur is not None:
        t = cur.type
        name_node = None
        lines = [cur.start_point[0] + 1]
        if t in ("function_declaration", "generator_function_declaration", "method_definition"):
            name_node = cur.child_by_field_name("name")
        elif t == "variable_declarator":
            name_node = cur.child_by_field_name("name")
            if cur.parent is not None:
                lines.append(cur.parent.start_point[0] + 1)
                if cur.parent.parent is not None and cur.parent.parent.type == "export_statement":
                    lines.append(cur.parent.parent.start_point[0] + 1)
        elif t == "pair":
            value = cur.child_by_field_name("value")
            if value is not None and value.type in ("arrow_function", "function_expression", "function"):
                name_node = cur.child_by_field_name("key")
        if name_node is not None and name_node.type in ("identifier", "property_identifier"):
            hit = files.symbol_at(rel, lines, [text(name_node)])
            if hit is not None:
                return hit["id"]
        cur = cur.parent
    return None


def _template_refs(rel: str, text: str) -> list[RouteRef]:
    """``route('x')`` written in a Vue template (or ``<style>``-free markup)."""
    from graphify.extractors.resolution import _VUE_SCRIPT_RE

    def blank(m: re.Match) -> str:
        return re.sub(r"[^\r\n]", " ", m.group(0))

    markup = _VUE_SCRIPT_RE.sub(blank, text)
    markup = _HTML_COMMENT.sub(blank, markup)
    out: list[RouteRef] = []
    for rx, relation in ((_TEMPLATE_CALL, CALLS), (_TEMPLATE_CURRENT, REFERENCES)):
        for m in rx.finditer(markup):
            line = markup.count("\n", 0, m.start()) + 1
            out.append(RouteRef(rel, line, m.group(2), relation, None))
    return out


# ── PHP / Blade ───────────────────────────────────────────────────────────


def scan_php(f: PhpFile, owners: Owners) -> list[RouteRef]:
    out: list[RouteRef] = []
    for call in iter_calls(f.root):
        fn = function_name(f, call)
        meth = method_name(f, call)
        if fn in ("route", "to_route"):
            pass
        elif meth == "route" and _is_redirect_receiver(f, call):
            pass
        else:
            continue
        args = args_of(call)
        arg = named(args, "name")
        pos = positional(args)
        node = arg.node if arg is not None else (pos[0].node if pos else None)
        name = str_value(f, node)
        if name is None or not _NAME.match(name):
            continue
        out.append(RouteRef(f.rel, line_of(call), name, CALLS, owners.of(f, call)))
    return out


def _is_redirect_receiver(f: PhpFile, call) -> bool:
    """``redirect()->route(...)``, ``Redirect::route(...)``, ``URL::route(...)``."""
    if call.type == "scoped_call_expression":
        return scope_name(f, call) in ("Redirect", "URL")
    obj = call.child_by_field_name("object")
    return obj is not None and function_name(f, obj) == "redirect" and not positional(args_of(obj))


def scan_blade(rel: str, text: str) -> list[RouteRef]:
    if "route(" not in text:
        return []
    cleaned = _BLADE_COMMENT.sub(lambda m: re.sub(r"[^\r\n]", " ", m.group(0)), text)
    out: list[RouteRef] = []
    for m in _BLADE_CALL.finditer(cleaned):
        line = cleaned.count("\n", 0, m.start()) + 1
        out.append(RouteRef(rel, line, m.group(2), CALLS, None))
    return out
