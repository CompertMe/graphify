"""Route definitions read from Laravel route files.

``RouteWalker`` walks a route file's PHP tree and yields one ``RouteRec`` per
definition, carrying everything the enclosing groups contribute: prefix, name
prefix, middleware, controller, namespace and domain. Included files
(``->group(__DIR__.'/x.php')``, ``require __DIR__.'/x.php'``,
``loadRoutesFrom``) are walked with the including group's context.

Nothing is guessed. Whatever cannot be read as a literal (a dynamic prefix, a
middleware built at runtime, a group argument that is not a closure or a path)
marks the affected routes ``partial`` instead of inventing a value, and a file no
loader references is walked with an unknown inheritance chain, also ``partial``.
"""
from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from typing import Callable

from graphify.extractors.laravel.middleware import Entry, entries_of
from graphify.extractors.laravel.phpsrc import (
    CLOSURE_TYPES,
    Arg,
    Call,
    PhpFile,
    args_of,
    array_items,
    class_ref,
    flatten_chain,
    include_target,
    is_include,
    line_of,
    named,
    own_property_name,
    path_value,
    positional,
    property_string,
    str_value,
    string_list,
    uri_template,
    value_with_config,
)

_VERBS = frozenset({"get", "post", "put", "patch", "delete", "options", "any"})
_RESOURCES = frozenset({"resource", "apiResource", "singleton", "apiSingleton", "resources", "apiResources"})
_SPECIAL = frozenset({"match", "redirect", "permanentRedirect", "view", "fallback", "livewire"})
_TERMINALS = _VERBS | _RESOURCES | _SPECIAL | {"group"}

_RESOURCE_ACTIONS = (
    ("index", ("GET",), ""),
    ("create", ("GET",), "/create"),
    ("store", ("POST",), ""),
    ("show", ("GET",), "/{w}"),
    ("edit", ("GET",), "/{w}/edit"),
    ("update", ("PUT", "PATCH"), "/{w}"),
    ("destroy", ("DELETE",), "/{w}"),
)
_API_ACTIONS = frozenset({"index", "store", "show", "update", "destroy"})
_SINGLETON_ACTIONS = (
    ("show", ("GET",), ""),
    ("edit", ("GET",), "/edit"),
    ("update", ("PUT", "PATCH"), ""),
)
_GROUP_ATTRS = {
    "prefix": "prefix",
    "as": "name",
    "name": "name",
    "middleware": "middleware",
    "excluded_middleware": "withoutMiddleware",
    "controller": "controller",
    "namespace": "namespace",
    "domain": "domain",
}


_UNCOUNTABLE = frozenset({"news", "series", "species", "information", "equipment", "media", "data", "metadata"})


@dataclass(frozen=True)
class Ctx:
    """What the enclosing groups contribute to a route."""
    prefix: str = ""
    name: str = ""
    middleware: tuple[Entry, ...] = ()
    without: tuple[str, ...] = ()
    controller: str | None = None
    namespace: str = ""
    domain: str | None = None
    partial: str | None = None


@dataclass
class RouteRec:
    rel: str
    line: int
    verbs: list[str]
    uri: str
    name: str | None
    middleware: tuple[Entry, ...]
    kind: str
    action: str
    controller: str | None = None
    method: str | None = None
    class_only: bool = False
    closure_line: int | None = None
    view: str | None = None
    redirect: str | None = None
    domain: str | None = None
    partial: str | None = None
    inferred: bool = False
    resource: str | None = None
    without: tuple[str, ...] = ()


def _partial(ctx: Ctx, reason: str) -> Ctx:
    return ctx if ctx.partial else dataclasses.replace(ctx, partial=reason)


def join_path(*parts: str) -> str:
    return "/".join(p for p in (s.strip("/") for s in parts) if p)


def singularize(word: str) -> str:
    """Approximation of ``Str::singular`` for resource wildcards (documented as such)."""
    w = word
    irregular = {"people": "person", "children": "child", "men": "man", "women": "woman"}
    if w in irregular:
        return irregular[w]
    if w in _UNCOUNTABLE:
        return w
    if w.endswith("ies") and len(w) > 3:
        return w[:-3] + "y"
    if re.search(r"(sses|shes|ches|xes|zes)$", w):
        return w[:-2]
    if w.endswith("ss") or w.endswith("us") or w.endswith("is"):
        return w
    if w.endswith("s") and len(w) > 1:
        return w[:-1]
    return w


def _wildcard(segment: str) -> str:
    return singularize(segment).replace("-", "_")


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class RouteWalker:
    def __init__(self, load: Callable[[str], "PhpFile | None"], corpus: set[str]):
        self._load = load
        self._corpus = corpus
        self.visited: set[str] = set()
        self.routes: list[RouteRec] = []
        self.walked: list[str] = []

    # -- entry points -----------------------------------------------------------

    def parse_file(self, rel: str, ctx: Ctx) -> None:
        """Walk ``rel`` with the inheritance context ``ctx`` (once per file)."""
        if rel in self.visited or rel not in self._corpus:
            return
        self.visited.add(rel)
        f = self._load(rel)
        if f is None:
            return
        self.walked.append(rel)
        self.visit(f, f.root, ctx)

    def includes_of(self, rel: str) -> set[str]:
        """Corpus files ``rel`` pulls in (``require`` or a ``->group(path)``)."""
        f = self._load(rel)
        found: set[str] = set()
        if f is None:
            return found

        def walk(node) -> None:
            if is_include(node):
                target = include_target(f, node)
                if target in self._corpus:
                    found.add(target)  # type: ignore[arg-type]
            elif node.type == "member_call_expression":
                name_node = node.child_by_field_name("name")
                if name_node is not None and f.text(name_node) in ("group", "loadRoutesFrom"):
                    for a in args_of(node):
                        target = path_value(f, a.node)
                        if target in self._corpus:
                            found.add(target)  # type: ignore[arg-type]
            for c in node.children:
                walk(c)

        walk(f.root)
        found.discard(rel)
        return found

    # -- tree walk ---------------------------------------------------------------

    def visit(self, f: PhpFile, node, ctx: Ctx) -> None:
        t = node.type
        if is_include(node):
            target = include_target(f, node)
            if target is not None:
                self.parse_file(target, ctx)
            return
        if t in ("member_call_expression", "scoped_call_expression"):
            if self._visit_call(f, node, ctx):
                return
        for child in node.children:
            self.visit(f, child, ctx)

    def _visit_call(self, f: PhpFile, node, ctx: Ctx) -> bool:
        if node.type == "member_call_expression":
            name_node = node.child_by_field_name("name")
            name = f.text(name_node) if name_node is not None else ""
            if name == "loadRoutesFrom":
                for a in positional(args_of(node)):
                    target = path_value(f, a.node)
                    if target is not None:
                        self.parse_file(target, ctx)
                return True
            if name == "withRouting":
                self._with_routing(f, node)
                obj = node.child_by_field_name("object")
                if obj is not None:
                    self.visit(f, obj, ctx)
                return True
        calls = flatten_chain(f, node)
        if calls is None:
            return False
        self._chain(f, calls, ctx)
        return True

    # -- bootstrap/app.php withRouting -----------------------------------------

    def _with_routing(self, f: PhpFile, node) -> None:
        args = args_of(node)
        api_prefix = "api"
        prefix_arg = named(args, "apiPrefix")
        if prefix_arg is not None:
            api_prefix = str_value(f, prefix_arg.node) or ""
        groups = {
            "web": Ctx(middleware=(("str", "web"),)),
            "api": Ctx(prefix=api_prefix, middleware=(("str", "api"),)),
        }
        for key, ctx in groups.items():
            arg = named(args, key)
            if arg is None:
                continue
            items = array_items(arg.node)
            nodes = [v for _k, v in items] if items is not None else [arg.node]
            for n in nodes:
                target = path_value(f, n)
                if target is None:
                    continue
                self.parse_file(target, ctx)
        for key in ("then", "using"):
            arg = named(args, key)
            if arg is not None and arg.node is not None and arg.node.type in CLOSURE_TYPES:
                self._walk_closure(f, arg.node, Ctx())

    def _walk_closure(self, f: PhpFile, closure, ctx: Ctx) -> None:
        body = closure.child_by_field_name("body")
        if body is not None:
            self.visit(f, body, ctx)

    # -- chains ---------------------------------------------------------------------

    def _chain(self, f: PhpFile, calls: list[Call], ctx: Ctx) -> None:
        t = next((i for i, c in enumerate(calls) if c.name in _TERMINALS), None)
        if t is None:
            return
        term = calls[t]
        is_group = term.name == "group"
        chain_names: list[str | None] = []
        local = ctx
        for c in calls[:t]:
            local = self._modifier(f, local, c, chain_names, as_group=is_group)
        if is_group:
            self._group(f, local, calls, t)
            return
        if term.name in _RESOURCES:
            self.routes.extend(self._resource(f, local, term, calls[t + 1:], calls[0].line))
            return
        post_names: list[str | None] = []
        for c in calls[t + 1:]:
            local = self._modifier(f, local, c, post_names, as_group=False)
        self.routes.append(self._define(f, local, term, chain_names + post_names, calls[0].line))

    def _group(self, f: PhpFile, ctx: Ctx, calls: list[Call], t: int) -> None:
        term = calls[t]
        pos = positional(term.args)
        body = None
        if t == 0 and len(pos) >= 2:
            ctx = self._apply_attrs(f, ctx, pos[0].node)
            body = pos[1].node
        elif pos:
            body = pos[0].node
        if body is None:
            return
        if body.type in CLOSURE_TYPES:
            self._walk_closure(f, body, ctx)
            return
        target = path_value(f, body)
        if target is not None:
            self.parse_file(target, ctx)

    # -- modifiers ---------------------------------------------------------------------

    def _apply_attrs(self, f: PhpFile, ctx: Ctx, node) -> Ctx:
        items = array_items(node)
        if items is None:
            return _partial(ctx, "dynamic group attributes")
        for k, v in items:
            key = str_value(f, k) if k is not None else None
            mod = _GROUP_ATTRS.get(key or "")
            if mod is None:
                continue
            fake = Call(mod, [Arg(None, v)], line_of(v), v)
            ctx = self._modifier(f, ctx, fake, [], as_group=True)
        return ctx

    def _modifier(self, f: PhpFile, ctx: Ctx, c: Call, names: list[str | None], *, as_group: bool) -> Ctx:
        pos = positional(c.args)
        first = pos[0].node if pos else None
        n = c.name
        if n == "prefix":
            val, config_keys = value_with_config(f, first)
            if val is None:
                return _partial(ctx, "dynamic prefix")
            out = dataclasses.replace(ctx, prefix=join_path(ctx.prefix, val))
            return _partial(out, "config-driven prefix (config default used)") if config_keys else out
        if n in ("name", "as"):
            val = str_value(f, first)
            if as_group:
                if val is None:
                    return _partial(ctx, "dynamic route name prefix")
                return dataclasses.replace(ctx, name=ctx.name + val)
            names.append(val)
            return ctx
        if n == "middleware":
            out = ctx
            for a in pos:
                entries, ok = entries_of(f, a.node)
                out = dataclasses.replace(out, middleware=out.middleware + tuple(entries))
                if not ok:
                    out = _partial(out, "dynamic middleware")
            return out
        if n == "withoutMiddleware":
            vals: list[str] = []
            for a in pos:
                entries, ok = entries_of(f, a.node)
                vals.extend(v for _k, v in entries)
                if not ok:
                    ctx = _partial(ctx, "dynamic withoutMiddleware")
            return dataclasses.replace(ctx, without=ctx.without + tuple(vals))
        if n == "can":
            parts = [str_value(f, a.node) for a in pos]
            if parts and all(p is not None for p in parts):
                joined = "can:" + ",".join(p for p in parts if p is not None)
                return dataclasses.replace(ctx, middleware=ctx.middleware + (("str", joined),))
            return _partial(ctx, "dynamic can()")
        if n == "domain":
            # A computed domain (`'{tenant}.'.$domain`) does not change the uri or the
            # middleware: keep the literal pieces, show the runtime part as `{dynamic}`.
            if first is None:
                return _partial(ctx, "dynamic domain")
            return dataclasses.replace(ctx, domain=uri_template(f, first))
        if n == "controller":
            fqn = class_ref(f, first)
            if fqn is None:
                s = str_value(f, first)
                fqn = self._string_class(s, ctx) if s else None
            if fqn is None:
                return _partial(ctx, "dynamic controller")
            return dataclasses.replace(ctx, controller=fqn)
        if n == "namespace":
            val = str_value(f, first)
            prop = own_property_name(f, first)
            if val is None and prop is not None:
                val = property_string(f, prop)
            if val is None:
                return _partial(ctx, "dynamic namespace")
            val = val.strip("\\")
            joined = val if val == "" else (f"{ctx.namespace}\\{val}" if ctx.namespace else val)
            return dataclasses.replace(ctx, namespace=joined)
        return ctx

    # -- definitions --------------------------------------------------------------------

    @staticmethod
    def _string_class(raw: str, ctx: Ctx) -> str:
        if raw.startswith("\\"):
            return raw[1:]
        return f"{ctx.namespace}\\{raw}" if ctx.namespace else raw

    def _uri(self, f: PhpFile, ctx: Ctx, node) -> tuple[str, str | None]:
        """``(full uri, partial reason)``."""
        val, config_keys = value_with_config(f, node)
        if val is None:
            # Literal pieces kept, every runtime part shown as `{dynamic}`.
            return "/" + join_path(ctx.prefix, uri_template(f, node)), "dynamic uri"
        reason = "config-driven uri (config default used)" if config_keys else None
        return "/" + join_path(ctx.prefix, val), reason

    def _define(self, f: PhpFile, ctx: Ctx, term: Call, names: list[str | None], line: int) -> RouteRec:
        n = term.name
        pos = positional(term.args)
        partial = ctx.partial
        verbs: list[str]
        uri_node = pos[0].node if pos else None
        action_node = pos[1].node if len(pos) > 1 else None
        view = redirect = None
        kind: str | None = None
        if n in _VERBS:
            verbs = ["ANY"] if n == "any" else [n.upper()]
        elif n == "match":
            vs = string_list(f, pos[0].node) if pos else None
            verbs = [v.upper() for v in vs] if vs else ["ANY"]
            if not vs:
                partial = partial or "dynamic http verbs"
            uri_node = pos[1].node if len(pos) > 1 else None
            action_node = pos[2].node if len(pos) > 2 else None
        elif n in ("redirect", "permanentRedirect"):
            verbs = ["ANY"]
            kind = "redirect"
            redirect = str_value(f, pos[1].node) if len(pos) > 1 else None
            action_node = None
        elif n == "view":
            verbs = ["GET"]
            kind = "view"
            view = str_value(f, pos[1].node) if len(pos) > 1 else None
            action_node = None
        elif n == "fallback":
            verbs = ["GET"]
            action_node = uri_node
            uri_node = None
        else:  # livewire
            verbs = ["GET"]
            kind = "livewire"

        if n == "fallback":
            uri = "/" + join_path(ctx.prefix, "{fallbackPlaceholder}")
            uri_partial = None
        else:
            uri, uri_partial = self._uri(f, ctx, uri_node)
        partial = partial or uri_partial

        rec = RouteRec(
            rel=f.rel, line=line, verbs=verbs, uri=uri, name=None,
            middleware=ctx.middleware, kind=kind or "unknown", action="", domain=ctx.domain,
            partial=partial, without=ctx.without,
        )
        if names:
            if any(x is None for x in names):
                rec.partial = rec.partial or "dynamic route name"
            else:
                rec.name = ctx.name + "".join(x for x in names if x)
        if kind == "redirect":
            rec.redirect = redirect
            rec.action = f"redirect:{redirect}" if redirect is not None else "redirect"
        elif kind == "view":
            rec.view = view
            rec.action = f"view:{view}" if view is not None else "view"
        elif kind == "livewire":
            fqn = class_ref(f, action_node)
            if fqn is None:
                rec.partial = rec.partial or "dynamic livewire component"
                rec.action = _collapse(f.text(action_node)) if action_node is not None else "livewire"
            else:
                rec.controller = fqn
                rec.class_only = True
                rec.action = f"{fqn} (livewire)"
        else:
            self._action(f, ctx, action_node, rec)
        return rec

    def _action(self, f: PhpFile, ctx: Ctx, node, rec: RouteRec) -> None:
        if node is None:
            rec.kind, rec.action = "unknown", "none"
            return
        if node.type in CLOSURE_TYPES:
            rec.kind, rec.action = "closure", "Closure"
            rec.closure_line = line_of(node)
            return
        if node.type == "array_creation_expression":
            items = array_items(node)
            if items is None:
                rec.kind, rec.action = "unknown", _collapse(f.text(node))
                return
            plain = [v for k, v in items if k is None]
            keyed = {str_value(f, k): v for k, v in items if k is not None}
            if len(plain) == len(items) and 1 <= len(plain) <= 2:
                fqn = class_ref(f, plain[0])
                if fqn is not None:
                    if len(plain) == 2:
                        method = str_value(f, plain[1])
                        if method is not None:
                            rec.kind, rec.controller, rec.method = "controller", fqn, method
                            rec.action = f"{fqn}@{method}"
                            return
                    else:
                        rec.kind, rec.controller, rec.class_only = "invokable", fqn, True
                        rec.action = fqn
                        return
            elif "uses" in keyed:
                inner = keyed["uses"]
                self._action(f, ctx, inner, rec)
                name_val = str_value(f, keyed["as"]) if "as" in keyed else None
                if name_val is not None and rec.name is None:
                    rec.name = ctx.name + name_val
                if "middleware" in keyed:
                    entries, ok = entries_of(f, keyed["middleware"])
                    rec.middleware = rec.middleware + tuple(entries)
                    if not ok:
                        rec.partial = rec.partial or "dynamic middleware"
                return
            rec.kind, rec.action = "unknown", _collapse(f.text(node))
            return
        fqn = class_ref(f, node)
        if fqn is not None:
            rec.kind, rec.controller, rec.class_only = "invokable", fqn, True
            rec.action = fqn
            return
        s = str_value(f, node)
        if s is not None:
            if "@" in s:
                cls, method = s.split("@", 1)
                rec.kind, rec.controller, rec.method = "controller", self._string_class(cls, ctx), method
            elif ctx.controller:
                rec.kind, rec.controller, rec.method = "controller", ctx.controller, s
            else:
                rec.kind, rec.controller, rec.class_only = "invokable", self._string_class(s, ctx), True
            rec.action = f"{rec.controller}@{rec.method}" if rec.method else str(rec.controller)
            return
        rec.kind, rec.action = "unknown", _collapse(f.text(node))

    # -- resources ----------------------------------------------------------------------

    def _resource(self, f: PhpFile, ctx: Ctx, term: Call, post: list[Call], line: int) -> list[RouteRec]:
        pos = positional(term.args)
        n = term.name
        targets: list[tuple[str | None, str | None]] = []  # (resource name, controller FQN)
        partial = ctx.partial
        if n in ("resources", "apiResources"):
            items = array_items(pos[0].node) if pos else None
            if items is None:
                return []
            for k, v in items:
                targets.append((str_value(f, k) if k is not None else None, class_ref(f, v)))
            if len(pos) > 1:
                partial = partial or "resources() options"
        else:
            res = str_value(f, pos[0].node) if pos else None
            ctrl = None
            if len(pos) > 1:
                ctrl = class_ref(f, pos[1].node)
                if ctrl is None:
                    s = str_value(f, pos[1].node)
                    ctrl = self._string_class(s, ctx) if s else None
            targets.append((res, ctrl))
            if len(pos) < 2:
                return []

        only: list[str] | None = None
        except_: list[str] = []
        base_name_override: str | None = None
        local = ctx
        for c in post:
            if c.name in ("only", "except"):
                got = self._string_args(f, c)
                if got is None:
                    partial = partial or f"dynamic {c.name}()"
                elif c.name == "only":
                    only = (only or []) + got
                else:
                    except_ += got
            elif c.name == "names":
                val = str_value(f, c.args[0].node) if c.args else None
                if val is None:
                    partial = partial or "resource names()"
                else:
                    base_name_override = val
            elif c.name in ("parameters", "parameter", "shallow", "creatable", "destroyable"):
                partial = partial or f"resource {c.name}()"
            else:
                local = self._modifier(f, local, c, [], as_group=False)

        singleton = n in ("singleton", "apiSingleton")
        api = n in ("apiResource", "apiResources", "apiSingleton")
        out: list[RouteRec] = []
        for res, ctrl in targets:
            if not res or ctrl is None:
                continue
            prefix_part = res.strip("/").rpartition("/")[0]  # `admin/posts` -> uri prefix `admin`
            leaf = res.strip("/").rsplit("/", 1)[-1]
            segs = leaf.split(".")
            base_uri = "/".join(
                [f"{s}/{{{_wildcard(s)}}}" for s in segs[:-1]] + [segs[-1]]
            )
            name_base = base_name_override or leaf
            wild = _wildcard(segs[-1])
            table = _SINGLETON_ACTIONS if singleton else _RESOURCE_ACTIONS
            for action, verbs, suffix in table:
                if api and action in ("create", "edit"):
                    continue
                if only is not None and action not in only:
                    continue
                if action in except_:
                    continue
                uri = "/" + join_path(local.prefix, prefix_part, base_uri + suffix.replace("{w}", "{" + wild + "}"))
                out.append(RouteRec(
                    rel=f.rel, line=line, verbs=list(verbs), uri=uri,
                    name=f"{local.name}{name_base}.{action}",
                    middleware=local.middleware, kind="resource",
                    action=f"{ctrl}@{action}", controller=ctrl, method=action,
                    domain=local.domain, partial=partial or local.partial, inferred=True,
                    resource=leaf, without=local.without,
                ))
        return out

    @staticmethod
    def _string_args(f: PhpFile, c: Call) -> list[str] | None:
        out: list[str] = []
        for a in positional(c.args):
            got = string_list(f, a.node)
            if got is None:
                return None
            out.extend(got)
        return out


__all__ = ["Ctx", "RouteRec", "RouteWalker", "join_path", "singularize"]
