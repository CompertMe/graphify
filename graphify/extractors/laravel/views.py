"""View-name resolution: ``'ns::a.b'`` -> the Blade file node it names.

Everything a name can depend on is read from the project's own source:

* default roots: ``resources/views`` plus literal ``paths`` of ``config/view.php``;
* namespaces: ``loadViewsFrom(path, 'ns')`` and ``View::addNamespace/prependNamespace/
  replaceNamespace('ns', path)`` with literal paths (``__DIR__``, ``dirname``,
  ``base_path``, ``resource_path``, ... as in ``phpsrc.path_value``). A published override
  ``<root>/vendor/<ns>/`` wins over the namespace directories, as in Laravel;
* anonymous components: ``Blade::anonymousComponentPath(path[, prefix])``, the
  ``components`` folder of every root, ``<x-ns::a>`` through a namespace's ``components``
  folder; ``a/b.blade.php``, ``a/b/index.blade.php`` and ``a/b/b.blade.php`` all name
  ``<x-a.b>``;
* class components: ``Blade::component(...)``, ``Blade::componentNamespace(...)`` and the
  ``App\\View\\Components`` convention (resolved by the pass, which owns the class index);
* view-name helpers: a function whose body is ``return "<prefix>{$area}::{$view}";``
  (compertme-social's ``theme_view($view, $area)``) is recognised from its source, so
  ``theme_view('auth.login', 'guest')`` reads as ``theme-guest::auth.login``;
* ``.graphifyrc`` ``laravel.view_namespace.*`` / ``laravel.component_path*`` for what only
  the running application knows (see ``viewconfig``).

Fail-closed: a name that matches zero files, or several distinct files, resolves to
``None``; a namespace registered with a path that is not a literal is never resolved
through its other registrations.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Iterator

from graphify.extractors.laravel.phpsrc import (
    CLOSURE_TYPES,
    Arg,
    PhpFile,
    _path_string,
    args_of,
    array_items,
    class_const_raw,
    named,
    positional,
    str_value,
    typed_nodes,
)
from graphify.extractors.laravel.viewconfig import LaravelViewOptions

_ASSIGNMENTS = frozenset({"assignment_expression", "augmented_assignment_expression"})
_REGISTRATION_NODES = frozenset({"function_definition", "member_call_expression", "scoped_call_expression"})
BLADE = ".blade.php"
_FACADES = {
    "view": frozenset({"Illuminate\\Support\\Facades\\View", "View"}),
    "blade": frozenset({"Illuminate\\Support\\Facades\\Blade", "Blade"}),
    "livewire": frozenset({"Livewire\\Livewire", "Livewire"}),
}
_NS_METHODS = frozenset({"addnamespace", "prependnamespace", "replacenamespace"})
_NAME = re.compile(r"[\w\-]+(?:[./][\w\-]+)*")


def iter_nodes(root) -> Iterator:
    """Every node under ``root``, depth first (iterative: PHP trees can be deep)."""
    stack = [root]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def studly(segment: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in re.split(r"[-_ ]+", segment) if w)


def kebab(segment: str) -> str:
    return re.sub(r"(.)(?=[A-Z])", r"\1-", segment).lower()


@dataclass(frozen=True)
class ViewHelper:
    """``function f($view, $area) { return "pre{$area}::{$view}"; }`` as data."""
    name: str                 # lower case
    view_index: int
    area_index: int | None    # None: the namespace is fixed (``fixed_ns``)
    prefix: str = ""
    fixed_ns: str | None = None

    def view_name(self, args: list[str | None]) -> str | None:
        view = args[self.view_index] if self.view_index < len(args) else None
        if view is None:
            return None
        if self.area_index is None:
            return f"{self.fixed_ns}::{view}"
        area = args[self.area_index] if self.area_index < len(args) else None
        if area is None:
            return None
        return f"{self.prefix}{area}::{view}"


@dataclass
class Registrations:
    view_ns: dict[str, list[str]] = field(default_factory=dict)
    tainted_ns: set[str] = field(default_factory=set)
    anon_paths: list[tuple[str, str | None]] = field(default_factory=list)
    blade_aliases: dict[str, str] = field(default_factory=dict)       # alias -> class FQN
    blade_namespaces: dict[str, list[str]] = field(default_factory=dict)  # prefix -> PHP namespaces
    livewire_names: dict[str, set[str]] = field(default_factory=dict)  # name -> class FQNs
    helpers: dict[str, ViewHelper] = field(default_factory=dict)
    view_paths: list[str] = field(default_factory=list)               # config/view.php

    def add_ns(self, ns: str, dirs: list[str], *, prepend: bool = False) -> None:
        cur = self.view_ns.setdefault(ns, [])
        new = [d for d in dirs if d not in cur]
        self.view_ns[ns] = new + cur if prepend else cur + new


def _scope_is(f: PhpFile, scope, kind: str) -> bool:
    if scope is None or scope.type not in ("name", "qualified_name"):
        return False
    fqn = f.class_fqn(f.text(scope))
    return fqn in _FACADES[kind]


def _call_parts(f: PhpFile, node):
    """``(is_static, scope_node | object_node, lower name)`` of a method/static call."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    name = f.text(name_node).lower()
    if node.type == "scoped_call_expression":
        return True, node.child_by_field_name("scope"), name
    return False, node.child_by_field_name("object"), name


_FUNCTION_LIKE = ("method_declaration", "function_definition", *CLOSURE_TYPES)


def _single_assignment(f: PhpFile, var):
    """The right-hand side of the one ``$var = ...`` before ``var`` in its function, else None."""
    scope = var.parent
    while scope is not None and scope.type not in _FUNCTION_LIKE:
        scope = scope.parent
    if scope is None:
        return None
    name = f.text(var)
    found = []
    for n in typed_nodes(scope, _ASSIGNMENTS):
        left = n.child_by_field_name("left")
        if left is None or left.type != "variable_name" or f.text(left) != name:
            continue
        inner = n.parent
        while inner is not None and inner.type not in _FUNCTION_LIKE:
            inner = inner.parent
        if inner is None or inner.id != scope.id:
            continue  # assigned inside a nested closure: a different variable
        if n.type == "augmented_assignment_expression" or n.start_byte >= var.start_byte:
            return None
        found.append(n.child_by_field_name("right"))
    return found[0] if len(found) == 1 else None


def _local_string(f: PhpFile, node, depth: int = 0) -> str | None:
    """Like ``phpsrc``'s path expressions, but a local variable stands for its one assignment."""
    if node is None or depth > 3:
        return None
    if node.type == "variable_name":
        return _local_string(f, _single_assignment(f, node), depth + 1)
    if node.type == "binary_expression":
        op = node.child_by_field_name("operator")
        if op is not None and f.text(op) == ".":
            left = _local_string(f, node.child_by_field_name("left"), depth + 1)
            right = _local_string(f, node.child_by_field_name("right"), depth + 1)
            return left + right if left is not None and right is not None else None
        return None
    return _path_string(f, node)


def local_path(f: PhpFile, node) -> str | None:
    """``path_value`` that also follows a once-assigned local variable."""
    raw = _local_string(f, node)
    if raw is None:
        return None
    norm = posixpath.normpath(raw.replace("\\", "/").lstrip("/") or ".")
    if norm == ".":
        return ""
    return None if norm == ".." or norm.startswith("../") else norm


def _dir_list(f: PhpFile, node) -> list[str] | None:
    """One path or an array of paths as root-relative dirs; None if any is not literal."""
    p = local_path(f, node)
    if p is not None:
        return [p]
    items = array_items(node)
    if not items:
        return None
    out: list[str] = []
    for _k, v in items:
        pv = local_path(f, v)
        if pv is None:
            return None
        out.append(pv)
    return out


def _helper_of(f: PhpFile, fn) -> ViewHelper | None:
    name_node = fn.child_by_field_name("name")
    params_node = fn.child_by_field_name("parameters")
    body = fn.child_by_field_name("body")
    if name_node is None or params_node is None or body is None:
        return None
    params: list[str] = []
    for p in params_node.children:
        if p.type in ("simple_parameter", "property_promotion_parameter", "variadic_parameter"):
            var = next((c for c in p.children if c.type == "variable_name"), None)
            params.append(f.text(var).lstrip("$") if var is not None else "")
    ret = [c for c in body.children if c.type == "return_statement"]
    if not ret:
        return None
    expr = next((c for c in ret[-1].children if c.is_named), None)
    if expr is None or expr.type != "encapsed_string":
        return None
    parts: list[tuple[str, str]] = []
    for c in expr.children:
        if c.type == "string_content":
            if parts and parts[-1][0] == "t":
                parts[-1] = ("t", parts[-1][1] + f.text(c))
            else:
                parts.append(("t", f.text(c)))
        elif c.type == "variable_name":
            parts.append(("v", f.text(c).lstrip("$")))
        elif c.type not in ('"', "{", "}"):
            return None
    name = f.text(name_node).lower()
    try:
        if len(parts) == 4 and [k for k, _ in parts] == ["t", "v", "t", "v"] and parts[2][1] == "::":
            return ViewHelper(name, params.index(parts[3][1]), params.index(parts[1][1]), prefix=parts[0][1])
        if len(parts) == 3 and [k for k, _ in parts] == ["v", "t", "v"] and parts[1][1] == "::":
            return ViewHelper(name, params.index(parts[2][1]), params.index(parts[0][1]))
        if len(parts) == 2 and [k for k, _ in parts] == ["t", "v"] and parts[0][1].endswith("::"):
            return ViewHelper(name, params.index(parts[1][1]), None, fixed_ns=parts[0][1][:-2])
    except ValueError:
        return None
    return None


def collect_registrations(f: PhpFile, reg: Registrations) -> None:
    for n in typed_nodes(f.root, _REGISTRATION_NODES):
        if n.type == "function_definition":
            helper = _helper_of(f, n)
            if helper is not None:
                reg.helpers.setdefault(helper.name, helper)
            continue
        parts = _call_parts(f, n)
        if parts is None:
            continue
        static, target, name = parts
        args = args_of(n)
        pos = positional(args)
        if name == "loadviewsfrom" and not static and len(pos) >= 2:
            ns = str_value(f, pos[1].node)
            if ns is not None:
                _register_ns(f, reg, ns, pos[0].node)
        elif name in _NS_METHODS and len(pos) >= 2:
            ok = _scope_is(f, target, "view") if static else (
                target is not None and "view" in f.text(target).lower()
            )
            ns = str_value(f, pos[0].node)
            if ok and ns is not None:
                _register_ns(f, reg, ns, pos[1].node, prepend=name == "prependnamespace")
        elif static and _scope_is(f, target, "blade"):
            _blade_call(f, reg, name, pos, args)
        elif static and _scope_is(f, target, "livewire") and name == "component" and len(pos) == 2:
            lname = str_value(f, pos[0].node)
            raw = class_const_raw(f, pos[1].node)
            fqn = f.class_fqn(raw) if raw else None
            if lname and fqn:
                reg.livewire_names.setdefault(lname, set()).add(fqn)


def _register_ns(f: PhpFile, reg: Registrations, ns: str, path_node, *, prepend: bool = False) -> None:
    dirs = _dir_list(f, path_node)
    if dirs is None:
        reg.tainted_ns.add(ns)
        return
    reg.add_ns(ns, dirs, prepend=prepend)


def _blade_call(f: PhpFile, reg: Registrations, name: str, pos: list[Arg], args: list[Arg]) -> None:
    if name == "anonymouscomponentpath" and pos:
        d = local_path(f, pos[0].node)
        named_prefix = named(args, "prefix")
        prefix_arg = pos[1].node if len(pos) > 1 else (named_prefix.node if named_prefix is not None else None)
        prefix = str_value(f, prefix_arg) if prefix_arg is not None else None
        if d is not None and (prefix_arg is None or prefix is not None):
            reg.anon_paths.append((d, prefix or None))
    elif name == "componentnamespace" and len(pos) == 2:
        ns, prefix = str_value(f, pos[0].node), str_value(f, pos[1].node)
        if ns and prefix:
            reg.blade_namespaces.setdefault(prefix, []).append(ns.strip("\\"))
    elif name == "component" and pos:
        _blade_alias(f, reg, pos[0].node, pos[1].node if len(pos) > 1 else None)
    elif name == "components" and pos and len(pos) == 1:
        items = array_items(pos[0].node)
        for k, v in items or []:
            if k is not None:
                _blade_alias(f, reg, v, k)
            else:
                _blade_alias(f, reg, v, None)


def _blade_alias(f: PhpFile, reg: Registrations, a, b) -> None:
    ra, rb = class_const_raw(f, a), class_const_raw(f, b) if b is not None else None
    if ra and (b is None or str_value(f, b) is not None):  # (Class::class[, 'alias'])
        fqn = f.class_fqn(ra)
        alias = str_value(f, b) if b is not None else kebab(ra.rsplit("\\", 1)[-1])
    elif rb and str_value(f, a) is not None:              # ('alias', Class::class)
        fqn, alias = f.class_fqn(rb), str_value(f, a)
    else:
        return
    if fqn and alias:
        reg.blade_aliases.setdefault(alias, fqn)


@dataclass(frozen=True)
class Resolved:
    rel: str
    via: str | None = None     # "graphifyrc" when only the project's own option got here


class ViewResolver:
    def __init__(
        self, blade_files: set[str], reg: Registrations, opts: LaravelViewOptions,
        roots: list[str] | None = None,
    ):
        self.blade = blade_files
        self.reg = reg
        self.opts = opts
        self.roots = roots or ["resources/views"]

    def _has(self, rel: str) -> bool:
        return rel in self.blade

    def view(self, name: str) -> Resolved | None:
        """The Blade file ``name`` renders (Laravel finder order), else None."""
        if not _valid(name):
            return None
        if "::" in name:
            ns, rest = name.split("::", 1)
            if not ns or "::" in rest:
                return None
            return self._ns_file(ns, rest.replace(".", "/") + BLADE)
        rel = name.replace(".", "/") + BLADE
        for root in self.roots:
            if self._has(f"{root}/{rel}"):
                return Resolved(f"{root}/{rel}")
        return None

    def _ns_dirs(self, ns: str) -> list[tuple[str, str | None]]:
        out: list[tuple[str, str | None]] = [(f"{r}/vendor/{ns}", None) for r in self.roots]
        if ns not in self.reg.tainted_ns:
            out += [(d, None) for d in self.reg.view_ns.get(ns, [])]
        out += [(d, "graphifyrc") for d in self.opts.view_namespaces.get(ns, [])]
        return out

    def _ns_file(self, ns: str, rel: str) -> Resolved | None:
        dirs = self._ns_dirs(ns)
        published = [d for d, _v in dirs[:len(self.roots)] if self._has(f"{d}/{rel}")]
        if published:
            return Resolved(f"{published[0]}/{rel}")
        found: list[Resolved] = []
        for d, via in dirs[len(self.roots):]:
            if via is None and self._has(f"{d}/{rel}"):
                if f"{d}/{rel}" not in {r.rel for r in found}:
                    found.append(Resolved(f"{d}/{rel}"))
        if len(found) == 1:
            return found[0]
        if found:
            return None  # same name in several registered directories: never guess
        for d, via in dirs[len(self.roots):]:  # the project's own mapping: its order wins
            if via == "graphifyrc" and self._has(f"{d}/{rel}"):
                return Resolved(f"{d}/{rel}", "graphifyrc")
        return None

    def anonymous_component(self, name: str) -> Resolved | None:
        prefix = None
        if "::" in name:
            prefix, name = name.split("::", 1)
            if not prefix or "::" in name:
                return None
        if not _valid(name):
            return None
        dirs: list[tuple[str, str | None]]
        if prefix is None:
            dirs = [(d, None) for d, p in self.reg.anon_paths if p is None]
            dirs += [(f"{r}/components", None) for r in self.roots]
            dirs += [(d, "graphifyrc") for d, p in self.opts.component_paths if p is None]
        else:
            dirs = [(d, None) for d, p in self.reg.anon_paths if p == prefix]
            dirs += [(d, "graphifyrc") for d, p in self.opts.component_paths if p == prefix]
            if prefix not in self.reg.tainted_ns:
                dirs += [(f"{d}/components", None) for d, _v in self._ns_dirs(prefix)[:len(self.roots)]]
                dirs += [(f"{d}/components", None) for d in self.reg.view_ns.get(prefix, [])]
                dirs += [(f"{d}/components", "graphifyrc") for d in self.opts.view_namespaces.get(prefix, [])]
        base = name.replace(".", "/")
        last = base.rsplit("/", 1)[-1]
        found: dict[str, str | None] = {}
        for d, via in dirs:
            for cand in (f"{d}/{base}{BLADE}", f"{d}/{base}/index{BLADE}", f"{d}/{base}/{last}{BLADE}"):
                if self._has(cand):
                    found[cand] = via if cand not in found else (found[cand] and via)
                    break
        if len(found) != 1:
            return None
        rel, via = next(iter(found.items()))
        return Resolved(rel, via)


def _valid(name: str) -> bool:
    if "::" in name:
        ns, _, rest = name.partition("::")
        return bool(_NAME.fullmatch(ns.replace("/", ""))) and bool(_NAME.fullmatch(rest)) and ".." not in rest
    return bool(_NAME.fullmatch(name)) and ".." not in name
