"""Prop keys of an Inertia render call, read from the PHP literal.

``Inertia::render('P', [...])``, ``inertia('P', compact('a', 'b'))`` and
``Route::inertia('/x', 'P', [...])`` pass props as an array. The keys written as
literals are facts; anything the source does not spell out (a variable, a method
call, a spread, a computed key) makes the result *partial*, never a guess.
"""
from __future__ import annotations

from graphify.extractors.laravel.phpsrc import PhpFile, args_of, positional, str_value


def props_of(f: PhpFile, node) -> tuple[list[str], bool]:
    """``(literal keys in source order, partial)`` for a props expression."""
    if node is None:
        return [], False
    keys: list[str] = []
    partial = _collect(f, node, keys)
    return _unique(keys), partial


def _unique(keys: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _collect(f: PhpFile, node, keys: list[str]) -> bool:
    t = node.type
    if t == "parenthesized_expression":
        inner = [c for c in node.children if c.is_named]
        return _collect(f, inner[0], keys) if len(inner) == 1 else True
    if t == "array_creation_expression":
        return array_keys(f, node, keys)
    if t == "function_call_expression":
        fn = node.child_by_field_name("function")
        name = f.text(fn).lstrip("\\").lower() if fn is not None else ""
        pos = positional(args_of(node))
        if name == "compact":
            partial = False
            for a in pos:
                s = str_value(f, a.node)
                if s is None:
                    partial = True  # compact($names) / compact(['a'])
                else:
                    keys.append(s)
            return partial
        if name == "array_merge":
            partial = False
            for a in pos:
                partial |= _collect(f, a.node, keys)
            return partial
    return True


def array_keys(f: PhpFile, node, keys: list[str]) -> bool:
    """Append the literal string keys of an array literal; True when any member is not one."""
    partial = False
    for el in node.children:
        if el.type != "array_element_initializer":
            continue
        named = [c for c in el.children if c.is_named]
        if any(c.type == "variadic_unpacking" for c in named) or len(named) != 2:
            partial = True
            continue
        key = str_value(f, named[0])
        if key is None:
            partial = True
        else:
            keys.append(key)
    return partial
