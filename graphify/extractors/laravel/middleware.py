"""Middleware alias / group / global tables read from Laravel's own config files.

Sources (all literal PHP read from the corpus, nothing executed):

* ``bootstrap/app.php`` -- ``$middleware->alias([...])``, ``->web(append:, prepend:,
  remove:)``, ``->api(...)``, ``->group('x', [...])``, ``->appendToGroup`` /
  ``->prependToGroup``, and the global ``->append`` / ``->prepend`` / ``->use``.
* ``app/Http/Kernel.php`` -- ``$middleware``, ``$middlewareGroups``,
  ``$middlewareAliases`` and the older ``$routeMiddleware``.
* Service providers calling the router directly (``aliasMiddleware``,
  ``middlewareGroup``, ``pushMiddlewareToGroup``, ``prependMiddlewareToGroup``).
* Filament panel providers (``->middleware([...])``, ``->authMiddleware([...])``).

Class names go through ``PhpFile.class_fqn`` (the PHP name-resolution rules); an
entry that is not a literal is dropped from the table, never guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from graphify.extractors.laravel.phpsrc import (
    Arg,
    PhpFile,
    args_of,
    array_items,
    class_const_raw,
    flatten_chain,
    named,
    positional,
    str_value,
)

Entry = tuple[str, str]  # ("class", FQN) | ("str", raw alias/group string)

_ROUTER_ONLY = frozenset({"aliasMiddleware", "middlewareGroup", "pushMiddlewareToGroup", "prependMiddlewareToGroup"})
_CONFIG_CALLS = frozenset({
    "alias", "web", "api", "group", "append", "prepend", "use", "appendToGroup", "prependToGroup",
})
_KERNEL_PROPS = {
    "middleware": "global",
    "middlewareGroups": "groups",
    "middlewareAliases": "aliases",
    "routeMiddleware": "aliases",
}


def entries_of(f: PhpFile, node) -> tuple[list[Entry], bool]:
    """Middleware entries named by ``node`` and whether every part was a literal.

    Accepts ``'alias'``, ``'alias:param'``, ``Foo::class``, ``Foo::class.':param'``
    and (nested) arrays of those.
    """
    if node is None:
        return [], False
    s = str_value(f, node)
    if s is not None:
        return ([("str", s)] if s else []), True
    raw = class_const_raw(f, node)
    if raw is not None:
        fqn = f.class_fqn(raw)
        return ([("class", fqn)], True) if fqn else ([], False)
    if node.type == "binary_expression":
        op = node.child_by_field_name("operator")
        if op is not None and f.text(op) == ".":
            left_raw = class_const_raw(f, node.child_by_field_name("left"))
            right = str_value(f, node.child_by_field_name("right"))
            if left_raw is not None and right is not None:
                fqn = f.class_fqn(left_raw)
                return ([("class", fqn)], True) if fqn else ([], False)
        return [], False
    items = array_items(node)
    if items is None:
        return [], False
    out: list[Entry] = []
    ok = True
    for _k, v in items:
        sub, sub_ok = entries_of(f, v)
        out.extend(sub)
        ok = ok and sub_ok
    return out, ok


@dataclass
class Panel:
    """A Filament panel: one routable surface with its own middleware stacks."""
    rel: str
    line: int
    panel_id: str | None
    path: str | None
    domain: str | None
    middleware: list[str] = field(default_factory=list)
    auth_middleware: list[str] = field(default_factory=list)
    partial: bool = False


@dataclass
class MiddlewareConfig:
    aliases: dict[str, str] = field(default_factory=dict)
    groups: dict[str, list[Entry]] = field(default_factory=dict)
    global_middleware: list[str] = field(default_factory=list)
    panels: list[Panel] = field(default_factory=list)

    def expand(self, entry: Entry, via: str | None = None, _seen: frozenset = frozenset()) -> list[tuple[str, str | None]]:
        """``[(class FQN, via)]`` an entry stands for; ``via`` names the alias/group
        that introduced it (None for a class written directly)."""
        kind, val = entry
        if kind == "class":
            return [(val, via)]
        key = val.split(":", 1)[0]
        if key in self.groups and key not in _seen:
            out: list[tuple[str, str | None]] = []
            for member in self.groups[key]:
                out.extend(self.expand(member, via or key, _seen | {key}))
            return out
        if key in self.aliases:
            return [(self.aliases[key], via or key)]
        return []


class _Builder:
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.base: dict[str, list[Entry]] = {}
        self.prepend: dict[str, list[Entry]] = {}
        self.append: dict[str, list[Entry]] = {}
        self.remove: dict[str, list[Entry]] = {}
        self.global_: list[str] = []
        self.panels: list[Panel] = []

    def finish(self) -> MiddlewareConfig:
        names = sorted(set(self.base) | set(self.prepend) | set(self.append))
        groups: dict[str, list[Entry]] = {}
        for g in names:
            gone = set(self.remove.get(g, []))
            members = [*self.prepend.get(g, []), *self.base.get(g, []), *self.append.get(g, [])]
            groups[g] = [m for m in members if m not in gone]
        return MiddlewareConfig(self.aliases, groups, list(self.global_), list(self.panels))

    # -- registration helpers -------------------------------------------------

    def _classes(self, f: PhpFile, node) -> list[str]:
        entries, _ok = entries_of(f, node)
        return [v for k, v in entries if k == "class"]

    def _alias_target(self, f: PhpFile, node) -> str | None:
        raw = class_const_raw(f, node)
        if raw is not None:
            return f.class_fqn(raw)
        s = str_value(f, node)
        if s:
            return s.lstrip("\\")
        return None

    def add_alias_pairs(self, f: PhpFile, node) -> None:
        items = array_items(node)
        for k, v in items or []:
            key = str_value(f, k) if k is not None else None
            target = self._alias_target(f, v)
            if key and target:
                self.aliases[key] = target

    def add_group_pairs(self, f: PhpFile, node) -> None:
        for k, v in array_items(node) or []:
            key = str_value(f, k) if k is not None else None
            if key:
                entries, _ok = entries_of(f, v)
                self.base[key] = entries

    # -- call handlers --------------------------------------------------------

    def handle_call(self, f: PhpFile, name: str, obj, args: list[Arg]) -> None:
        pos = positional(args)
        receiver_is_var = obj is not None and obj.type == "variable_name"
        if name in _ROUTER_ONLY:
            if name == "aliasMiddleware" and len(pos) >= 2:
                key = str_value(f, pos[0].node)
                target = self._alias_target(f, pos[1].node)
                if key and target:
                    self.aliases[key] = target
            elif name == "middlewareGroup" and len(pos) >= 2:
                key = str_value(f, pos[0].node)
                if key:
                    self.base[key] = entries_of(f, pos[1].node)[0]
            elif name in ("pushMiddlewareToGroup", "prependMiddlewareToGroup") and len(pos) >= 2:
                key = str_value(f, pos[0].node)
                if key:
                    bucket = self.append if name == "pushMiddlewareToGroup" else self.prepend
                    bucket.setdefault(key, []).extend(entries_of(f, pos[1].node)[0])
            return
        if not receiver_is_var or name not in _CONFIG_CALLS:
            return
        if name == "alias" and pos:
            self.add_alias_pairs(f, pos[0].node)
        elif name in ("web", "api"):
            for slot, bucket in (("append", self.append), ("prepend", self.prepend), ("remove", self.remove)):
                arg = named(args, slot)
                if arg is None:
                    idx = ("append", "prepend", "remove").index(slot)
                    arg = pos[idx] if idx < len(pos) else None
                if arg is not None:
                    bucket.setdefault(name, []).extend(entries_of(f, arg.node)[0])
        elif name == "group" and len(pos) >= 2:
            key = str_value(f, pos[0].node)
            if key:
                self.base[key] = entries_of(f, pos[1].node)[0]
        elif name in ("appendToGroup", "prependToGroup") and len(pos) >= 2:
            key = str_value(f, pos[0].node)
            if key:
                bucket = self.append if name == "appendToGroup" else self.prepend
                bucket.setdefault(key, []).extend(entries_of(f, pos[1].node)[0])
        elif name in ("append", "prepend", "use") and pos:
            self.global_.extend(self._classes(f, pos[0].node))

    def handle_kernel_property(self, f: PhpFile, prop: str, value) -> None:
        kind = _KERNEL_PROPS[prop]
        if kind == "global":
            self.global_.extend(self._classes(f, value))
        elif kind == "aliases":
            self.add_alias_pairs(f, value)
        else:
            self.add_group_pairs(f, value)

    # -- Filament ---------------------------------------------------------------

    def scan_panels(self, f: PhpFile) -> None:
        def visit(node) -> None:
            if node.type == "member_call_expression":
                calls = flatten_chain(f, node, any_variable=True)
                names = {c.name for c in calls or []}
                if calls and ("authMiddleware" in names or ("id" in names and "path" in names)):
                    self.panels.append(self._panel_from(f, calls))
                    return
            for c in node.children:
                visit(c)

        visit(f.root)

    def _panel_from(self, f: PhpFile, calls) -> Panel:
        panel = Panel(rel=f.rel, line=calls[0].line, panel_id=None, path=None, domain=None)
        for c in calls:
            pos = positional(c.args)
            if c.name == "id" and pos:
                panel.panel_id = str_value(f, pos[0].node)
            elif c.name == "path" and pos:
                panel.path = str_value(f, pos[0].node)
                if panel.path is None:
                    panel.partial = True
            elif c.name == "domain" and pos:
                panel.domain = str_value(f, pos[0].node)
            elif c.name in ("middleware", "authMiddleware") and pos:
                entries, ok = entries_of(f, pos[0].node)
                classes = [v for k, v in entries if k == "class"]
                (panel.middleware if c.name == "middleware" else panel.auth_middleware).extend(classes)
                panel.partial = panel.partial or not ok
        return panel


def build_config(
    rels: list[str],
    load: Callable[[str], "PhpFile | None"],
    read_bytes: Callable[[str], bytes],
) -> MiddlewareConfig:
    """Read the middleware tables out of the config files present in ``rels``."""
    b = _Builder()
    for rel in sorted(rels):
        base = rel.rsplit("/", 1)[-1]
        is_bootstrap = rel == "bootstrap/app.php"
        is_kernel = base == "Kernel.php" and "/Http/" in f"/{rel}"
        is_provider = "Provider" in base
        if not (is_bootstrap or is_kernel or is_provider):
            continue
        try:
            raw = read_bytes(rel)
        except OSError:
            continue
        wants = (
            is_bootstrap
            or (is_kernel and b"middleware" in raw.lower())
            or (is_provider and any(
                tok in raw for tok in (
                    b"aliasMiddleware", b"middlewareGroup", b"MiddlewareToGroup", b"PanelProvider",
                )
            ))
        )
        if not wants:
            continue
        f = load(rel)
        if f is None:
            continue

        def visit(node, f=f, is_kernel=is_kernel) -> None:
            if node.type == "member_call_expression":
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    b.handle_call(f, f.text(name_node), node.child_by_field_name("object"), args_of(node))
            elif is_kernel and node.type == "property_element":
                var = next((c for c in node.children if c.type == "variable_name"), None)
                if var is not None:
                    prop = f.text(var).lstrip("$")
                    if prop in _KERNEL_PROPS:
                        value = next((c for c in node.children if c.is_named and c.id != var.id), None)
                        if value is not None:
                            b.handle_kernel_property(f, prop, value)
            for c in node.children:
                visit(c)

        visit(f.root)
        if is_provider and b"PanelProvider" in raw:
            b.scan_panels(f)
    return b.finish()


__all__ = ["Entry", "MiddlewareConfig", "Panel", "build_config", "entries_of"]
