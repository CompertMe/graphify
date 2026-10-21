"""Inertia: controller -> page edges and the props contract between them.

``Inertia::render('Tenant/Branches/Index', [...])``, ``inertia('P')`` and
``Route::inertia('/x', 'P')`` name a page by string; the client maps that name to
a file through the ``resolve:`` option of ``createInertiaApp`` (usually
``./Pages/${name}.vue`` plus an ``import.meta.glob('./Pages/**/*.vue')``). This
module reads that mapping from the app entry, resolves every *literal* page name
to the page file node and emits ``renders`` edges from the callable that renders
it. A dynamic name (``Inertia::render($page)``), a name no file matches, or one
that two files match, gets no edge.

Props: the literal keys passed by a render call ride on its ``renders`` edge
(``props``, ``props_partial``); the keys a ``.vue`` page declares through
``defineProps`` (object, array, type-literal generic, a same-file
``interface``/``type``, or the Options-API ``props`` option) are written to the
page node's metadata together with a mismatch report. Shared props are read from
the ``share()`` method of a class extending ``Inertia\\Middleware``. Nothing in
the contract is an edge of its own: the report is metadata.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from graphify.extractors.laravel.phpprops import array_keys, props_of
from graphify.extractors.laravel.phpsites import (
    Owners,
    function_name,
    iter_calls,
    method_name,
    scope_name,
)
from graphify.extractors.laravel.phpsrc import (
    PhpFile,
    args_of,
    line_of,
    named,
    positional,
    str_value,
)
from graphify.extractors.laravel.webfiles import JS_SUFFIXES, FileIndex

_PAGE_EXTS = (".vue", ".tsx", ".jsx", ".ts", ".js", ".svelte")
_DEFAULT_ROOTS = (
    "resources/js/Pages", "resources/js/pages", "resources/ts/Pages", "resources/ts/pages",
)
_ENTRY_STEMS = frozenset({"app", "ssr", "main", "index", "inertia"})
_GLOB = re.compile(r"import\.meta\.glob(?:Eager)?\(\s*\[?\s*(['\"`])([^'\"`]+)\1")
_TEMPLATE = re.compile(r"(?:resolvePageComponent|import|require)\(\s*`([^`]*\$\{[^`]*)`")
_RESOLVE = re.compile(r"\bresolve")
_LINE_COMMENT = re.compile(r"(?m)(?<![:'\"`])//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_EXT_GROUP = re.compile(r"\.(?:\{([\w,]+)\}|(\w+))$")


def _strip_comments(text: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))


@dataclass
class PageRoot:
    directory: str                      # root-relative posix directory
    exts: tuple[str, ...] = _PAGE_EXTS


class PageResolver:
    """Page component name -> root-relative page file path."""

    def __init__(self, root: Path, files: FileIndex):
        self.files = files
        self.roots = self._detect(root)

    def _detect(self, root: Path) -> list[PageRoot]:
        found: dict[str, tuple[str, ...]] = {}
        for rel in self.files.js_files():
            parts = rel.split("/")
            if parts[0] != "resources" or len(parts) > 5:
                continue
            stem = posixpath.splitext(parts[-1])[0].lower()
            if stem not in _ENTRY_STEMS or not rel.endswith(JS_SUFFIXES):
                continue
            try:
                text = _strip_comments((root / rel).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            base = posixpath.dirname(rel)
            for pat, ext in self._patterns(text):
                directory = self._directory(pat, base)
                if directory and self._has_pages(directory):
                    found.setdefault(directory, ext)
        if found:
            return [PageRoot(d, e) for d, e in sorted(found.items())]
        return [PageRoot(d) for d in _DEFAULT_ROOTS if self._has_pages(d)]

    @staticmethod
    def _patterns(text: str) -> list[tuple[str, tuple[str, ...]]]:
        out: list[tuple[str, tuple[str, ...]]] = []
        positions = [m.start() for m in _RESOLVE.finditer(text)]
        for rx in (_GLOB, _TEMPLATE):
            for m in rx.finditer(text):
                # A page mapping sits inside (or right after) a `resolve` option;
                # a locale glob elsewhere in the entry is not one.
                if not any(0 <= m.start() - p <= 700 for p in positions):
                    continue
                pat = m.group(2) if rx is _GLOB else m.group(1)
                out.append((pat, _exts_of(pat)))
        return out

    @staticmethod
    def _directory(pattern: str, base: str) -> str | None:
        cut = len(pattern)
        for marker in ("**", "*", "${"):
            i = pattern.find(marker)
            if i != -1:
                cut = min(cut, i)
        raw = pattern[:cut].rstrip("/")
        if not raw:
            return None
        if raw.startswith("@/"):
            joined = "resources/js/" + raw[2:]
        elif raw.startswith("/"):
            joined = raw.lstrip("/")
        elif raw.startswith("."):
            joined = posixpath.join(base, raw)
        else:
            return None
        norm = posixpath.normpath(joined)
        return None if norm.startswith("..") or norm == "." else norm

    def _has_pages(self, directory: str) -> bool:
        prefix = directory + "/"
        return any(r.startswith(prefix) and r.endswith(_PAGE_EXTS) for r in self.files.files)

    def resolve(self, name: str) -> str | None:
        if not name or "::" in name or "\\" in name or ".." in name.split("/") or name.startswith("/"):
            return None
        hits: set[str] = set()
        for root in self.roots:
            for ext in root.exts:
                cand = f"{root.directory}/{name}{ext}"
                if cand in self.files.files:
                    hits.add(cand)
        return next(iter(hits)) if len(hits) == 1 else None


def _exts_of(pattern: str) -> tuple[str, ...]:
    m = _EXT_GROUP.search(pattern)
    if not m:
        return _PAGE_EXTS
    names = (m.group(1) or m.group(2)).split(",")
    exts = tuple(f".{n}" for n in names if f".{n}" in _PAGE_EXTS)
    return exts or _PAGE_EXTS


# ── PHP render sites ──────────────────────────────────────────────────────


@dataclass
class RenderSite:
    rel: str
    line: int
    page: str
    props: list[str]
    partial: bool
    owner: str | None


def scan_renders(f: PhpFile, owners: Owners) -> list[RenderSite]:
    out: list[RenderSite] = []
    for call in iter_calls(f.root):
        args = args_of(call)
        if function_name(f, call) == "inertia" and positional(args):
            comp_node = positional(args)[0].node
            props_node = positional(args)[1].node if len(positional(args)) > 1 else None
        elif method_name(f, call) == "render" and scope_name(f, call) == "Inertia":
            pos = positional(args)
            comp = named(args, "component")
            comp_node = comp.node if comp is not None else (pos[0].node if pos else None)
            p = named(args, "props")
            props_node = p.node if p is not None else (pos[1].node if len(pos) > 1 else None)
        elif method_name(f, call) == "render" and _is_inertia_helper(f, call):
            pos = positional(args)
            comp_node = pos[0].node if pos else None
            props_node = pos[1].node if len(pos) > 1 else None
        else:
            continue
        page = str_value(f, comp_node)
        if page is None:
            continue
        props, partial = props_of(f, props_node)
        extra, extra_partial = _chained_with(f, call)
        out.append(RenderSite(
            f.rel, line_of(call), page, _merge(props, extra), partial or extra_partial,
            owners.of(f, call),
        ))
    return out


def _is_inertia_helper(f: PhpFile, call) -> bool:
    """``inertia()->render('P')``: the receiver is the zero-argument ``inertia()`` helper."""
    if call.type != "member_call_expression":
        return False
    obj = call.child_by_field_name("object")
    return obj is not None and function_name(f, obj) == "inertia" and not positional(args_of(obj))


def _merge(a: list[str], b: list[str]) -> list[str]:
    return a + [k for k in b if k not in a]


def _chained_with(f: PhpFile, call) -> tuple[list[str], bool]:
    """Keys added by ``->with('k', v)`` / ``->with([...])`` chained on the render call."""
    keys: list[str] = []
    partial = False
    cur = call
    while True:
        parent = cur.parent
        if parent is None or parent.type != "member_call_expression":
            break
        obj = parent.child_by_field_name("object")
        if obj is None or obj.id != cur.id:
            break
        if method_name(f, parent) == "with":
            pos = positional(args_of(parent))
            if len(pos) == 2:
                s = str_value(f, pos[0].node)
                if s is None:
                    partial = True
                else:
                    keys.append(s)
            elif len(pos) == 1:
                got, p = props_of(f, pos[0].node)
                keys.extend(got)
                partial |= p
            else:
                partial = True
        cur = parent
    return keys, partial


# ── shared props ──────────────────────────────────────────────────────────


def scan_shared(f: PhpFile, owners: Owners) -> tuple[str | None, list[str]] | None:
    """``(class node id, keys)`` of ``share()`` in a class extending ``Inertia\\Middleware``."""
    if b"share" not in f.src or b"Middleware" not in f.src:
        return None
    stack = [f.root]
    while stack:
        node = stack.pop()
        if node.type == "class_declaration" and _extends_inertia_middleware(f, node):
            for m in _methods(node):
                name = m.child_by_field_name("name")
                if name is not None and f.text(name) == "share":
                    keys: list[str] = []
                    for ret in _returns(m):
                        _share_keys(f, ret, keys)
                    cls = node.child_by_field_name("name")
                    cls_id = None
                    if cls is not None and f.ns is not None:
                        text = f.text(cls)
                        cls_id = owners.idx.resolve_class(f"{f.ns}\\{text}" if f.ns else text)
                    return cls_id, keys
        stack.extend(node.children)
    return None


def _extends_inertia_middleware(f: PhpFile, cls) -> bool:
    for c in cls.children:
        if c.type == "base_clause":
            for n in c.children:
                if n.type in ("name", "qualified_name"):
                    fqn = f.class_fqn(f.text(n))
                    return fqn is not None and fqn.lower() == "inertia\\middleware"
    return False


def _methods(cls) -> list:
    body = cls.child_by_field_name("body")
    return [c for c in (body.children if body is not None else []) if c.type == "method_declaration"]


def _returns(method) -> list:
    out = []
    stack = [method]
    while stack:
        n = stack.pop()
        if n.type == "return_statement":
            out.extend(c for c in n.children if c.is_named)
            continue
        if n.type in ("anonymous_function", "arrow_function"):
            continue
        stack.extend(n.children)
    return out


def _share_keys(f: PhpFile, node, keys: list[str]) -> None:
    if node.type == "array_creation_expression":
        array_keys(f, node, keys)  # a spread (`...parent::share()`) is ignored on purpose
    elif node.type == "function_call_expression":
        fn = node.child_by_field_name("function")
        if fn is not None and f.text(fn).lstrip("\\").lower() == "array_merge":
            for a in positional(args_of(node)):
                _share_keys(f, a.node, keys)


# ── page defineProps ──────────────────────────────────────────────────────


def declared_props(path: Path) -> list[str] | None:
    """Prop names a ``.vue`` page declares, or None when it declares none this way
    (no ``defineProps``/``props`` option, a type imported from another file, ...)."""
    if path.suffix.lower() != ".vue":
        return None
    from graphify.extractors.resolution import _parse_js_tree

    parsed = _parse_js_tree(path)
    if parsed is None:
        return None
    src, root = parsed

    def text(n) -> str:
        return src[n.start_byte:n.end_byte].decode("utf-8", "replace")

    stack = [root]
    options: list = []
    while stack:
        n = stack.pop()
        if n.type == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is not None and text(fn) == "defineProps":
                return _define_props_keys(n, root, text)
        elif n.type == "export_statement":
            options.append(n)
        stack.extend(reversed(n.children))
    for exp in options:
        got = _options_props(exp, text)
        if got is not None:
            return got
    return None


def _define_props_keys(call, root, text) -> list[str] | None:
    targs = next((c for c in call.children if c.type == "type_arguments"), None)
    if targs is not None:
        members = [c for c in targs.children if c.is_named]
        if len(members) != 1:
            return None
        return _type_keys(members[0], root, text)
    args = call.child_by_field_name("arguments")
    first = next((c for c in (args.children if args is not None else []) if c.is_named), None)
    if first is None:
        return None
    return _value_keys(first, text)


def _value_keys(node, text) -> list[str] | None:
    if node.type == "array":
        out: list[str] = []
        for c in node.children:
            if c.type == "string":
                out.append(text(c).strip("'\"` "))
            elif c.is_named and c.type != "comment":
                return None
        return out
    if node.type == "object":
        out = []
        for c in node.children:
            if c.type == "pair":
                key = c.child_by_field_name("key")
                name = _key_name(key, text)
                if name is None:
                    return None
                out.append(name)
            elif c.type in ("shorthand_property_identifier", "method_definition"):
                key = c if c.type == "shorthand_property_identifier" else c.child_by_field_name("name")
                name = _key_name(key, text)
                if name is None:
                    return None
                out.append(name)
            elif c.is_named and c.type != "comment":
                return None  # spread: members unknown
        return out
    return None


def _key_name(key, text) -> str | None:
    if key is None:
        return None
    if key.type in ("property_identifier", "identifier", "shorthand_property_identifier"):
        return text(key)
    if key.type == "string":
        return text(key).strip("'\"` ")
    return None


def _type_keys(node, root, text) -> list[str] | None:
    if node.type == "object_type":
        return _signature_keys(node, text)
    if node.type == "type_identifier":
        name = text(node)
        stack = [root]
        while stack:
            n = stack.pop()
            if n.type == "interface_declaration":
                ident = n.child_by_field_name("name")
                body = n.child_by_field_name("body")
                if ident is not None and text(ident) == name and body is not None:
                    return _signature_keys(body, text)
            elif n.type == "type_alias_declaration":
                ident = n.child_by_field_name("name")
                value = n.child_by_field_name("value")
                if ident is not None and text(ident) == name and value is not None:
                    return _type_keys(value, root, text) if value.type == "object_type" else None
            stack.extend(n.children)
    return None


def _signature_keys(body, text) -> list[str] | None:
    out: list[str] = []
    for c in body.children:
        if c.type in ("property_signature", "method_signature"):
            name = _key_name(c.child_by_field_name("name"), text)
            if name is None:
                return None
            out.append(name)
        elif c.type in ("index_signature", "call_signature", "construct_signature"):
            return None
    return out


def _options_props(export, text) -> list[str] | None:
    """``props`` of ``export default { props: ... }`` / ``defineComponent({ props })``."""
    target = None
    for c in export.children:
        if c.type in ("object", "call_expression"):
            target = c
    if target is None:
        return None
    obj = target
    if target.type == "call_expression":
        args = target.child_by_field_name("arguments")
        obj = next((c for c in (args.children if args is not None else []) if c.type == "object"), None)
    if obj is None:
        return None
    for c in obj.children:
        if c.type == "pair" and _key_name(c.child_by_field_name("key"), text) == "props":
            value = c.child_by_field_name("value")
            return _value_keys(value, text) if value is not None else None
    return None


# ── contract report ───────────────────────────────────────────────────────


@dataclass
class PageUse:
    names: set[str] = field(default_factory=set)
    passed: list[str] = field(default_factory=list)
    partial: bool = False
    renderers: int = 0


def contract_report(
    declared: list[str] | None, use: PageUse, shared: set[str],
) -> dict[str, object]:
    """Page-node metadata: what is declared, what is passed, and where they disagree."""
    meta: dict[str, object] = {"inertia_page": sorted(use.names)[0] if len(use.names) == 1 else sorted(use.names)}
    meta["inertia_props_passed"] = list(use.passed)
    if use.partial:
        meta["inertia_props_passed_partial"] = True
    if declared is None:
        return meta
    meta["inertia_props_declared"] = list(declared)
    known = set(declared)
    undeclared = [k for k in use.passed if k not in known]
    if undeclared:
        meta["inertia_props_undeclared"] = undeclared
    if not use.partial and use.renderers:
        passed = set(use.passed)
        unused = [k for k in declared if k not in passed and k not in shared and k != "errors"]
        if unused:
            meta["inertia_props_unused"] = unused
    return meta
