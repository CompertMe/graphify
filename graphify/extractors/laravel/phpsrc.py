"""PHP source helpers shared by the Laravel route and middleware readers.

Everything here reads facts out of a tree-sitter-php tree: literal strings,
``Foo::class`` references, call chains and argument lists. A value that is not a
literal comes back as ``None`` so callers can mark the result partial instead of
guessing. Class names are never resolved here: ``PhpFile.class_fqn`` hands the
written name to ``_php_fqn_from_raw``, the one PHP name-resolution rule set.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from graphify.extractors.base import _read_source_bytes, _read_text
from graphify.extractors.resolution import _php_fqn_from_raw

_FACADE_NAMES = frozenset({"route"})
_ROUTER_VARS = frozenset({"router"})
_PATH_HELPERS = {
    "base_path": "",
    "app_path": "app",
    "config_path": "config",
    "database_path": "database",
    "resource_path": "resources",
    "public_path": "public",
    "storage_path": "storage",
    "lang_path": "lang",
}
_SELF_NAMES = frozenset({"self", "static", "parent"})
_INCLUDE_TYPES = frozenset({
    "require_expression", "require_once_expression",
    "include_expression", "include_once_expression",
})
CLOSURE_TYPES = frozenset({"anonymous_function", "arrow_function", "anonymous_function_creation_expression"})

_parser = None


def get_parser():
    """One shared tree-sitter PHP parser, or None when the grammar is missing."""
    global _parser
    if _parser is None:
        try:
            import tree_sitter_php as tsphp
            from tree_sitter import Language, Parser
        except ImportError:
            return None
        lang_fn = getattr(tsphp, "language_php", None) or getattr(tsphp, "language", None)
        if lang_fn is None:
            return None
        _parser = Parser(Language(lang_fn()))
    return _parser


@dataclass
class Arg:
    name: str | None
    node: Any


@dataclass
class Call:
    """One link of a ``Route::a()->b()`` chain."""
    name: str
    args: list[Arg]
    line: int
    node: Any


@dataclass
class PhpFile:
    rel: str                       # root-relative posix path
    src: bytes
    tree: Any
    ns: str | None                 # None: several namespaces in one file (never guess)
    uses: dict[str, str] = field(default_factory=dict)  # lower alias -> FQN

    @property
    def root(self):
        return self.tree.root_node

    def text(self, node) -> str:
        return _read_text(node, self.src)

    def class_fqn(self, raw: str) -> str | None:
        """FQN of a class name as written in this file (PHP name-resolution rules)."""
        if self.ns is None:
            return None
        return _php_fqn_from_raw(raw, self.ns, self.uses)


def load_php_file(abs_path: Path, rel: str) -> PhpFile | None:
    parser = get_parser()
    if parser is None:
        return None
    try:
        src = _read_source_bytes(abs_path, warn=False)
        tree = parser.parse(src)
    except Exception:
        return None
    f = PhpFile(rel=rel, src=src, tree=tree, ns="")
    namespaces: list[str] = []
    uses: dict[str, str] = {}

    def use_clause(clause, prefix: str) -> None:
        target = alias = None
        saw_as = False
        for c in clause.children:
            if c.type in ("function", "const"):
                return  # not a class import
            if c.type == "as":
                saw_as = True
            elif c.type in ("qualified_name", "name"):
                if saw_as:
                    alias = f.text(c)
                elif target is None:
                    target = f.text(c)
        if not target:
            return
        fqn = (f"{prefix}\\{target}" if prefix else target).lstrip("\\")
        key = (alias or fqn.rsplit("\\", 1)[-1]).strip().lower()
        if key:
            uses.setdefault(key, fqn)

    def walk(n) -> None:
        if n.type == "namespace_definition":
            for c in n.children:
                if c.type == "namespace_name":
                    namespaces.append(f.text(c))
                    break
        elif n.type == "namespace_use_declaration":
            prefix = ""
            group = None
            for c in n.children:
                if c.type == "namespace_name":
                    prefix = f.text(c)
                elif c.type == "namespace_use_group":
                    group = c
                elif c.type == "namespace_use_clause":
                    use_clause(c, "")
            if group is not None:
                for c in group.children:
                    if c.type == "namespace_use_clause":
                        use_clause(c, prefix)
            return
        for child in n.children:
            walk(child)

    walk(tree.root_node)
    if len(set(namespaces)) > 1:
        f.ns = None
    else:
        f.ns = namespaces[0] if namespaces else ""
    f.uses = uses
    return f


def line_of(node) -> int:
    return node.start_point[0] + 1


# ── literals ───────────────────────────────────────────────────────────────

def _unescape_single(s: str) -> str:
    return s.replace("\\\\", "\x00").replace("\\'", "'").replace("\x00", "\\")


def _unescape_double(s: str) -> str:
    out = s.replace("\\\\", "\x00").replace('\\"', '"').replace("\\$", "$")
    out = out.replace("\\n", "\n").replace("\\t", "\t")
    return out.replace("\x00", "\\")


def str_value(f: PhpFile, node) -> str | None:
    """The literal string value of ``node``, else None (interpolation, variables, calls)."""
    if node is None:
        return None
    t = node.type
    if t == "string":
        text = f.text(node)
        return _unescape_single(text[1:-1]) if len(text) >= 2 else None
    if t == "encapsed_string":
        for c in node.children:
            if c.type not in ('"', "string_content", "escape_sequence", "string_value"):
                return None
        text = f.text(node)
        return _unescape_double(text[1:-1]) if len(text) >= 2 else None
    if t == "parenthesized_expression":
        inner = [c for c in node.children if c.is_named]
        return str_value(f, inner[0]) if len(inner) == 1 else None
    if t == "binary_expression":
        op = node.child_by_field_name("operator")
        if op is not None and f.text(op) == ".":
            left = str_value(f, node.child_by_field_name("left"))
            right = str_value(f, node.child_by_field_name("right"))
            if left is not None and right is not None:
                return left + right
    return None


def value_with_config(f: PhpFile, node) -> tuple[str | None, list[str]]:
    """Like ``str_value``, but a ``config('key', 'default')`` call stands for its literal
    default. Returns ``(value, [config keys whose default was used])``; the value is a
    default, not a fact about the running app, so callers must flag it ``partial``.
    """
    if node is None:
        return None, []
    val = str_value(f, node)
    if val is not None:
        return val, []
    if node.type == "parenthesized_expression":
        inner = [c for c in node.children if c.is_named]
        return value_with_config(f, inner[0]) if len(inner) == 1 else (None, [])
    if node.type == "function_call_expression":
        fn = node.child_by_field_name("function")
        pos = positional(args_of(node))
        if fn is not None and f.text(fn) == "config" and len(pos) == 2:
            key, default = str_value(f, pos[0].node), str_value(f, pos[1].node)
            if key is not None and default is not None:
                return default, [key]
        return None, []
    if node.type == "binary_expression":
        op = node.child_by_field_name("operator")
        if op is not None and f.text(op) == ".":
            left, lkeys = value_with_config(f, node.child_by_field_name("left"))
            right, rkeys = value_with_config(f, node.child_by_field_name("right"))
            if left is not None and right is not None:
                return left + right, lkeys + rkeys
    return None, []


def uri_template(f: PhpFile, node) -> str:
    """A route uri that is not a plain literal, with each runtime part as ``{dynamic}``."""
    if node is None:
        return "{dynamic}"
    val, _keys = value_with_config(f, node)
    if val is not None:
        return val
    if node.type == "encapsed_string":  # "/{finding}/$action": text kept, interpolations dynamic
        out: list[str] = []
        for c in node.children:
            if c.type == "string_content":
                out.append(_unescape_double(f.text(c)))
            elif c.is_named:
                out.append("{dynamic}")
        return "".join(out)
    if node.type == "binary_expression":
        op = node.child_by_field_name("operator")
        if op is not None and f.text(op) == ".":
            return (
                uri_template(f, node.child_by_field_name("left"))
                + uri_template(f, node.child_by_field_name("right"))
            )
    return "{dynamic}"


def class_const_raw(f: PhpFile, node) -> str | None:
    """The class name as written in ``Foo::class`` / ``\\A\\B::class``, else None."""
    if node is None or node.type != "class_constant_access_expression":
        return None
    named = [c for c in node.children if c.is_named]
    if len(named) != 2 or f.text(named[1]).lower() != "class":
        return None
    raw = f.text(named[0]).strip()
    if not raw or raw.lower() in _SELF_NAMES or raw.startswith("$"):
        return None
    return raw


def class_ref(f: PhpFile, node) -> str | None:
    """FQN of a ``Foo::class`` expression under PHP name-resolution rules."""
    raw = class_const_raw(f, node)
    return f.class_fqn(raw) if raw else None


# ── arguments / arrays ─────────────────────────────────────────────────────

def args_of(call_node) -> list[Arg]:
    args = call_node.child_by_field_name("arguments")
    out: list[Arg] = []
    if args is None:
        return out
    for a in args.children:
        if a.type != "argument":
            continue
        name = None
        value = None
        for c in a.children:
            if not c.is_named:
                continue
            if c.type == "name" and a.child_by_field_name("name") is not None and c.id == a.child_by_field_name("name").id:
                name = c
                continue
            value = c
        out.append(Arg(name=name.text.decode() if name is not None else None, node=value))
    return out


def positional(args: list[Arg]) -> list[Arg]:
    return [a for a in args if a.name is None]


def named(args: list[Arg], key: str) -> Arg | None:
    for a in args:
        if a.name == key:
            return a
    return None


def array_items(node) -> list[tuple[object | None, object]] | None:
    """``[(key_node|None, value_node)]`` of an array literal, None for anything else
    (or an array with a spread, whose members are unknown)."""
    if node is None or node.type != "array_creation_expression":
        return None
    items: list[tuple[object | None, object]] = []
    for el in node.children:
        if el.type != "array_element_initializer":
            continue
        named_children = [c for c in el.children if c.is_named]
        if any(c.type == "variadic_unpacking" for c in named_children):
            return None
        if len(named_children) == 1:
            items.append((None, named_children[0]))
        elif len(named_children) == 2:
            items.append((named_children[0], named_children[1]))
        else:
            return None
    return items


def string_list(f: PhpFile, node) -> list[str] | None:
    """A literal string or an array of literal strings (``only(['index'])``)."""
    s = str_value(f, node)
    if s is not None:
        return [s]
    items = array_items(node)
    if items is None:
        return None
    out: list[str] = []
    for _k, v in items:
        sv = str_value(f, v)
        if sv is None:
            return None
        out.append(sv)
    return out


# ── call chains ────────────────────────────────────────────────────────────

def _is_route_scope(f: PhpFile, node) -> bool:
    if node is None:
        return False
    if node.type in ("name", "qualified_name"):
        last = f.text(node).rsplit("\\", 1)[-1].strip().lower()
        return last in _FACADE_NAMES
    return False


def flatten_chain(f: PhpFile, node, *, any_variable: bool = False) -> list[Call] | None:
    """``Route::a(..)->b(..)->c(..)`` as ``[a, b, c]`` in call order.

    None when the chain is not rooted in the ``Route`` facade (or a ``$router``
    variable): a method call on anything else is not a route definition.
    ``any_variable`` accepts a chain rooted in any variable (``$panel->id()->path()``).
    """
    calls: list[Call] = []
    cur = node
    while cur is not None:
        if cur.type == "member_call_expression":
            name_node = cur.child_by_field_name("name")
            if name_node is None:
                return None
            calls.append(Call(f.text(name_node), args_of(cur), line_of(cur), cur))
            cur = cur.child_by_field_name("object")
        elif cur.type == "scoped_call_expression":
            if not _is_route_scope(f, cur.child_by_field_name("scope")):
                return None
            name_node = cur.child_by_field_name("name")
            if name_node is None:
                return None
            calls.append(Call(f.text(name_node), args_of(cur), line_of(cur), cur))
            break
        elif cur.type == "variable_name":
            if not any_variable and f.text(cur).lstrip("$").lower() not in _ROUTER_VARS:
                return None
            break
        else:
            return None
    else:
        return None
    if not calls:
        return None
    calls.reverse()
    # A chain's own line is where it starts, whichever call is the definition.
    start = line_of(cur) if cur is not None else calls[0].line
    calls[0].line = start
    return calls


# ── paths ──────────────────────────────────────────────────────────────────

def path_value(f: PhpFile, node) -> str | None:
    """Root-relative posix path named by a path expression, else None.

    ``__DIR__.'/x.php'``, ``dirname(__DIR__).'/x'``, ``base_path('routes/x.php')``
    and string concatenations of those; anything dynamic is None.
    """
    raw = _path_string(f, node)
    if raw is None:
        return None
    raw = raw.replace("\\", "/")
    norm = posixpath.normpath(raw.lstrip("/") or ".")
    if norm == ".":
        return ""
    if norm == ".." or norm.startswith("../"):
        return None
    return norm


def _path_string(f: PhpFile, node) -> str | None:
    if node is None:
        return None
    t = node.type
    if t in ("string", "encapsed_string"):
        return str_value(f, node)
    if t == "parenthesized_expression":
        inner = [c for c in node.children if c.is_named]
        return _path_string(f, inner[0]) if len(inner) == 1 else None
    if t == "name" and f.text(node) == "__DIR__":
        return "/" + posixpath.dirname(f.rel) if posixpath.dirname(f.rel) else "/"
    if t == "binary_expression":
        op = node.child_by_field_name("operator")
        if op is None or f.text(op) != ".":
            return None
        left = _path_string(f, node.child_by_field_name("left"))
        right = _path_string(f, node.child_by_field_name("right"))
        if left is None or right is None:
            return None
        return left + right
    if t == "function_call_expression":
        fn = node.child_by_field_name("function")
        name = f.text(fn) if fn is not None else ""
        args = positional(args_of(node))
        if name == "dirname" and args:
            inner = _path_string(f, args[0].node)
            if inner is None:
                return None
            levels = 1
            if len(args) > 1:
                lv = args[1].node
                if lv is None or lv.type != "integer":
                    return None
                levels = int(f.text(lv))
            for _ in range(levels):
                inner = posixpath.dirname(inner.rstrip("/")) or "/"
            return inner
        base = _PATH_HELPERS.get(name)
        if base is not None:
            tail = ""
            if args:
                tail = _path_string(f, args[0].node)
                if tail is None:
                    return None
            return "/" + "/".join(p for p in (base, tail.strip("/")) if p)
    return None


def is_include(node) -> bool:
    return node.type in _INCLUDE_TYPES


def include_target(f: PhpFile, node) -> str | None:
    """Root-relative path a ``require``/``include`` expression names."""
    inner = [c for c in node.children if c.is_named]
    return path_value(f, inner[0]) if len(inner) == 1 else None


_OWN_PROPERTY = re.compile(r"(?:\$this->|(?:static|self)::\$)(\w+)")


def own_property_name(f: PhpFile, node) -> str | None:
    """``name`` for ``$this->name``, ``static::$name`` or ``self::$name``."""
    if node is None:
        return None
    m = _OWN_PROPERTY.fullmatch(f.text(node).replace(" ", ""))
    return m.group(1) if m else None


def property_string(f: PhpFile, name: str) -> str | None:
    """Literal default of the first ``$name`` class property in this file."""
    found: list[str | None] = []

    def walk(n) -> None:
        if found:
            return
        if n.type == "property_element":
            var = next((c for c in n.children if c.type == "variable_name"), None)
            if var is not None and f.text(var).lstrip("$") == name:
                default = [c for c in n.children if c.is_named and c.id != var.id]
                found.append(str_value(f, default[0]) if default else None)
                return
        for c in n.children:
            walk(c)

    walk(f.root)
    return found[0] if found else None
