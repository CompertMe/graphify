"""Read a Blade template into literal references.

No Blade compilation and no name resolution happens here: the scanner returns what
the template *says* (a view expression, a component tag name, a ``wire:`` action)
with its line, and ``viewpass`` ties it to a file or class node. Comments,
``@verbatim``, ``@php`` blocks, ``<?php`` blocks and echo bodies are blanked first
(positions and newlines are kept), so a directive or tag inside them is never read.
An expression that is not a plain literal (a variable, string interpolation) is
returned as written; the evaluator turns it into ``None`` and the caller emits
nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# wire:<event> attributes whose value is a component method call. `wire:model`,
# `wire:key`, `wire:loading`, `wire:target`, ... are not actions.
WIRE_ACTION_EVENTS = frozenset({
    "click", "dblclick", "submit", "change", "input", "keydown", "keyup", "keypress",
    "blur", "focus", "focusin", "focusout", "mouseenter", "mouseleave", "mouseover",
    "mouseout", "mousedown", "mouseup", "scroll", "init", "poll", "intersect",
    "paste", "drop", "dragover", "select", "toggle",
})
_SKIP_X = ("slot", "dynamic-component")
_SKIP_LIVEWIRE = frozenset({"styles", "scripts", "dynamic-component", "is"})


@dataclass
class BladeRef:
    kind: str       # extends include include_first each component x livewire_tag livewire_dir
    #                 wire_action wire_model alpine_action
    line: int
    exprs: list[str] = field(default_factory=list)  # raw PHP view-name expressions
    name: str = ""                                    # tag name / action / property


@dataclass
class BladeScan:
    refs: list[BladeRef] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    yields: list[str] = field(default_factory=list)
    pushes: list[str] = field(default_factory=list)
    stacks: list[str] = field(default_factory=list)


_MASKS = (
    re.compile(r"\{\{--.*?--\}\}", re.S),
    re.compile(r"@verbatim.*?@endverbatim", re.S),
    re.compile(r"<\?(?:php|=).*?(?:\?>|\Z)", re.S),
    re.compile(r"(?<![@\w])@php\b(?!\s*\().*?@endphp", re.S),
    re.compile(r"\{\{.*?\}\}", re.S),
    re.compile(r"\{!!.*?!!\}", re.S),
)
_DIRECTIVE = re.compile(
    r"(?<![@\w])@(extends|includeIf|includeWhen|includeUnless|includeFirst|include|each|"
    r"component|livewire|section|yield|pushOnce|push|prepend|stack|hasSection|sectionMissing)"
    r"[ \t]*\("
)
_X_TAG = re.compile(r"<x-([A-Za-z0-9_][A-Za-z0-9_.:\-]*)")
_LIVEWIRE_TAG = re.compile(r"<livewire:([A-Za-z0-9_][A-Za-z0-9_.:\-]*)")
_WIRE_ATTR = re.compile(
    r"(?<![\w:\-])wire:([a-z]+)((?:\.[\w\-]+)*)\s*=\s*(\"([^\"]*)\"|'([^']*)')"
)
_ALPINE_WIRE = re.compile(r"\$wire\.([A-Za-z_]\w*)\s*\(")
_STRING = re.compile(r"""'((?:[^'\\]|\\.)*)'|"((?:[^"\\${]|\\.)*)\"""", re.S)


def _blank(text: str) -> str:
    for rx in _MASKS:
        text = rx.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    return text


def _closing(masked: str, open_idx: int) -> int | None:
    """Index of the ``)`` matching the ``(`` at ``open_idx`` (strings respected)."""
    depth = 0
    i = open_idx
    n = len(masked)
    while i < n:
        c = masked[i]
        if c in "'\"":
            j = i + 1
            while j < n and masked[j] != c:
                j += 2 if masked[j] == "\\" else 1
            i = j + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                return i if c == ")" else None
        i += 1
    return None


def split_args(expr: str) -> list[str]:
    """Top-level comma separated pieces of an argument list, each stripped."""
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c in "'\"":
            j = i + 1
            while j < n and expr[j] != c:
                j += 2 if expr[j] == "\\" else 1
            cur.append(expr[i:j + 1])
            i = j + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    tail = "".join(cur).strip()
    if tail or out:
        out.append(tail)
    return out


def literal_string(expr: str) -> str | None:
    """The value of a plain ``'...'`` / ``"..."`` literal (no interpolation), else None."""
    m = _STRING.fullmatch(expr.strip())
    if m is None:
        return None
    if m.group(1) is not None:
        return m.group(1).replace("\\'", "'").replace("\\\\", "\\")
    return m.group(2).replace('\\"', '"').replace("\\\\", "\\")


def literal_list(expr: str) -> list[str] | None:
    """``['a', 'b']`` as its literal strings (every element must be a literal)."""
    e = expr.strip()
    if not (e.startswith("[") and e.endswith("]")):
        return None
    vals = [literal_string(p) for p in split_args(e[1:-1]) if p]
    return None if not vals or any(v is None for v in vals) else vals  # type: ignore[return-value]


def parse_actions(value: str) -> list[str]:
    """Component method names called by a ``wire:click``-style expression.

    ``save``, ``save({{ $id }})``, ``a(); b()`` give method names; ``$refresh``,
    ``$set('x', 1)``, ``open = true``, ``$parent.go()`` and ``save{{ $x }}`` give none.
    """
    out: list[str] = []
    for stmt in value.split(";"):
        m = re.match(r"\s*([A-Za-z_]\w*)\s*(\(|$)", stmt)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def scan_blade(text: str) -> BladeScan:
    masked = _blank(text)
    line_starts = [0]
    for m in re.finditer("\n", text):
        line_starts.append(m.end())

    def line_of(pos: int) -> int:
        lo, hi = 0, len(line_starts)
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if line_starts[mid] <= pos:
                lo = mid
            else:
                hi = mid
        return lo + 1

    scan = BladeScan()
    for m in _DIRECTIVE.finditer(masked):
        name = m.group(1)
        open_idx = m.end() - 1
        close = _closing(masked, open_idx)
        if close is None:
            continue
        args = split_args(text[open_idx + 1:close])
        line = line_of(m.start())
        first = args[0] if args else ""
        if name in ("section", "sectionMissing", "hasSection", "yield", "push", "pushOnce",
                    "prepend", "stack"):
            lit = literal_string(first)
            if lit is None:
                continue
            bucket = {
                "section": scan.sections, "yield": scan.yields, "push": scan.pushes,
                "pushOnce": scan.pushes, "prepend": scan.pushes, "stack": scan.stacks,
            }.get(name)
            if bucket is not None and lit not in bucket:
                bucket.append(lit)
        elif name == "extends":
            scan.refs.append(BladeRef("extends", line, [first]))
        elif name in ("include", "includeIf"):
            scan.refs.append(BladeRef("include", line, [first]))
        elif name in ("includeWhen", "includeUnless"):
            if len(args) >= 2:
                scan.refs.append(BladeRef("include", line, [args[1]]))
        elif name == "includeFirst":
            scan.refs.append(BladeRef("include_first", line, list(literal_list(first) or [])))
        elif name == "each":
            exprs = [first]
            if len(args) >= 4:
                exprs.append(args[3])  # the "empty" view
            scan.refs.append(BladeRef("each", line, exprs))
        elif name == "component":
            scan.refs.append(BladeRef("component", line, [first]))
        elif name == "livewire":
            scan.refs.append(BladeRef("livewire_dir", line, [first]))
    for m in _X_TAG.finditer(masked):
        tag = m.group(1)
        if tag.startswith(_SKIP_X):
            continue
        scan.refs.append(BladeRef("x", line_of(m.start()), name=tag.rstrip(".:-")))
    for m in _LIVEWIRE_TAG.finditer(masked):
        tag = m.group(1).rstrip(".:-")
        if tag in _SKIP_LIVEWIRE:
            continue
        scan.refs.append(BladeRef("livewire_tag", line_of(m.start()), name=tag))
    for m in _WIRE_ATTR.finditer(masked):
        event, _mods = m.group(1), m.group(2)
        raw = text[m.start(3) + 1:m.end(3) - 1]
        line = line_of(m.start())
        if event == "model":
            val = raw.strip()
            if re.fullmatch(r"[A-Za-z_][\w.\[\]]*", val):
                scan.refs.append(BladeRef("wire_model", line, name=val))
        elif event in WIRE_ACTION_EVENTS:
            for action in parse_actions(raw):
                scan.refs.append(BladeRef("wire_action", line, name=action))
    for m in _ALPINE_WIRE.finditer(masked):
        scan.refs.append(BladeRef("alpine_action", line_of(m.start()), name=m.group(1)))
    scan.refs.sort(key=lambda r: r.line)
    return scan
