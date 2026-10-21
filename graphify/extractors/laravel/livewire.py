"""Livewire components: which classes are one, what they are called, single-file components.

* A class is a component when its ``extends`` resolves (PHP name rules) to
  ``Livewire\\Component`` / ``Livewire\\Volt\\Component``, or to another in-corpus component.
* Its names are the explicit ``Livewire::component('name', Class::class)`` registrations
  plus the autodiscovery convention: a class under the configured class namespace
  (``App\\Livewire`` / ``App\\Http\\Livewire`` or ``config/livewire.php``) is named by its
  kebab-cased relative path, ``App\\Livewire\\Foo\\BarBaz`` -> ``foo.bar-baz``. A class
  elsewhere (a module) has no convention name: Livewire cannot find it without a
  registration, so graphify does not guess one.
* A Livewire 4 single-file component (``⚡name.blade.php``: a ``new class extends
  Component`` header followed by the template) is read as a component of its own whose
  methods are the anonymous class's methods.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from graphify.extractors.laravel.phpsrc import PhpFile, line_of
from graphify.extractors.laravel.views import iter_nodes, kebab

LIVEWIRE_BASES = frozenset({"Livewire\\Component", "Livewire\\Volt\\Component"})
DEFAULT_CLASS_NAMESPACES = ("App\\Livewire", "App\\Http\\Livewire")
SFC_MARK = "⚡"
_CLASS_NS_CFG = re.compile(rb"""['"]class_namespace['"]\s*=>\s*['"]([^'"]+)['"]""")


@dataclass
class ClassDecl:
    name: str
    fqn: str
    parent: str | None        # resolved FQN of the written ``extends``
    start: int
    end: int
    render: tuple[int, int] | None = None   # line range of its render() method


def read_classes(f: PhpFile) -> list[ClassDecl]:
    if f.ns is None:
        return []
    out: list[ClassDecl] = []
    for n in iter_nodes(f.root):
        if n.type != "class_declaration":
            continue
        name_node = n.child_by_field_name("name")
        if name_node is None:
            continue
        name = f.text(name_node)
        parent = None
        for c in n.children:
            if c.type == "base_clause":
                ref = next((x for x in c.children if x.type in ("name", "qualified_name")), None)
                parent = f.class_fqn(f.text(ref)) if ref is not None else None
        render = None
        body = n.child_by_field_name("body") or next((c for c in n.children if c.type == "declaration_list"), None)
        for m in (body.children if body is not None else []):
            if m.type == "method_declaration":
                mn = m.child_by_field_name("name")
                if mn is not None and f.text(mn).lower() == "render":
                    render = (line_of(m), m.end_point[0] + 1)
        out.append(ClassDecl(name, f"{f.ns}\\{name}" if f.ns else name, parent,
                             line_of(n), n.end_point[0] + 1, render))
    return out


def class_namespace_config(raw: bytes | None) -> str | None:
    m = _CLASS_NS_CFG.search(raw or b"")
    return m.group(1).decode().replace("\\\\", "\\").strip("\\") if m else None


def auto_name(fqn: str, class_namespaces: list[str]) -> str | None:
    """``App\\Livewire\\Foo\\BarBaz`` -> ``foo.bar-baz`` for a configured class namespace."""
    low = fqn.lower()
    for ns in class_namespaces:
        if low.startswith(ns.lower() + "\\"):
            tail = fqn[len(ns) + 1:].split("\\")
            return ".".join(kebab(seg) for seg in tail)
    return None


def component_fqns(decls: dict[str, ClassDecl]) -> set[str]:
    """FQNs of the component classes among ``decls`` (extends chain, to a fixpoint)."""
    comps: set[str] = set()
    changed = True
    while changed:
        changed = False
        for fqn, d in decls.items():
            if fqn not in comps and d.parent and (d.parent in LIVEWIRE_BASES or d.parent in comps):
                comps.add(fqn)
                changed = True
    return comps


@dataclass
class Sfc:
    name: str                    # the component name its file path spells
    class_line: int
    methods: list[tuple[str, int]] = field(default_factory=list)


def sfc_name(rel: str) -> str | None:
    """``resources/views/components/foo/⚡bar.blade.php`` -> ``foo.bar`` (``pages::`` for pages/)."""
    parts = rel.split("/")
    base = parts[-1]
    if not base.startswith(SFC_MARK) or not base.endswith(".blade.php"):
        return None
    stem = base[len(SFC_MARK):-len(".blade.php")]
    dirs = parts[:-1]
    for marker, prefix in (("components", ""), ("pages", "pages::")):
        if marker in dirs and "views" in dirs[:dirs.index(marker)]:
            tail = dirs[dirs.index(marker) + 1:]
            return prefix + ".".join([*tail, stem])
    return stem


def read_sfc(f: PhpFile) -> Sfc | None:
    """The ``new class extends Component { ... }`` of a single-file component, else None."""
    found: list = []
    for n in iter_nodes(f.root):
        if n.type != "anonymous_class":
            continue
        base = next((c for c in n.children if c.type == "base_clause"), None)
        ref = next((x for x in base.children if x.type in ("name", "qualified_name")), None) if base else None
        if ref is not None and f.class_fqn(f.text(ref)) in LIVEWIRE_BASES:
            found.append(n)
    if len(found) != 1:
        return None
    node = found[0]
    name = sfc_name(f.rel)
    if name is None:
        return None
    sfc = Sfc(name, line_of(node))
    body = node.child_by_field_name("body") or next(
        (c for c in node.children if c.type == "declaration_list"), None
    )
    for m in (body.children if body is not None else []):
        if m.type == "method_declaration":
            mn = m.child_by_field_name("name")
            if mn is not None:
                sfc.methods.append((f.text(mn), line_of(m)))
    return sfc
