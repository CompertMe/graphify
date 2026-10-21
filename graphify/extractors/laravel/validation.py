"""Validation rules read from source: which tables, enums and custom rule classes they name.

Sites (the rule *array* is what is read):

* ``rules()`` of a ``FormRequest`` (or Livewire component / form) class: its ``return``
  expressions, ``array_merge(...)`` arguments, and a returned variable's array assignments;
* ``$request->validate([...])`` / ``validateWithBag($bag, [...])`` / ``$this->validate([...])``;
* ``Validator::make($data, [...])`` and ``validator($data, [...])``.

Per rule value (a string, an array of rules, or nested arrays):

* ``'exists:branches,id'`` / ``'unique:clients,email,...'`` inside a ``|``-joined string or an
  array of strings -> a table hit; the table may be a model FQCN (``exists:App\\Models\\User``),
  or ``connection.table``;
* ``Rule::exists('t'[, 'col'])`` / ``Rule::unique('t')`` (with any chained ``->ignore()``,
  ``->where()``...) -> a table hit; ``Rule::exists(Branch::class)`` -> a model hit;
* ``Rule::enum(X::class)`` / ``new Enum(X::class)`` -> an enum hit;
* ``new MyRule(...)`` -> a custom-rule hit (the caller keeps only in-corpus classes).

A string that is built with a variable (``'unique:users,email,'.$id``, ``"unique:users,$col"``)
is read up to the first dynamic part; a table name the dynamic part might have cut short is
dropped. Anything else (``$rules`` pulled from a property, ``Rule::when(...)`` closures,
macro rules, Livewire ``#[Validate]`` attributes) gives nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from graphify.extractors.laravel.phpdecl import ClassView, find_method, return_exprs, walk
from graphify.extractors.laravel.phpsrc import (
    PhpFile,
    args_of,
    class_ref,
    line_of,
    positional,
    str_value,
)

RULE_FACADES = frozenset({"illuminate\\validation\\rule"})
ENUM_RULES = frozenset({"illuminate\\validation\\rules\\enum"})
VALIDATOR_FACADES = frozenset({"illuminate\\support\\facades\\validator", "validator"})
_TABLE_RULES = frozenset({"exists", "unique"})


@dataclass
class Hit:
    kind: str                    # "table" | "model" | "enum" | "rule"
    line: int
    field: str | None
    rule: str | None = None      # "exists" | "unique" (table / model hits)
    table: str | None = None
    column: str | None = None
    fqn: str | None = None       # model / enum / custom-rule FQN


def _concat_prefix(f: PhpFile, node) -> tuple[str, bool] | None:
    """``(literal text, complete)`` of a string expression; text stops at the first dynamic part."""
    if node is None:
        return None
    val = str_value(f, node)
    if val is not None:
        return val, True
    t = node.type
    if t == "parenthesized_expression":
        inner = [c for c in node.children if c.is_named]
        return _concat_prefix(f, inner[0]) if len(inner) == 1 else None
    if t == "encapsed_string":
        parts: list[str] = []
        for c in node.children:
            if c.type == "string_content":
                parts.append(f.text(c))
            elif c.type in ('"', "escape_sequence"):
                continue
            else:
                return "".join(parts), False
        return "".join(parts), True
    if t == "binary_expression":
        op = node.child_by_field_name("operator")
        if op is None or f.text(op) != ".":
            return None
        left = _concat_prefix(f, node.child_by_field_name("left"))
        if left is None:
            return None
        if not left[1]:
            return left
        right = _concat_prefix(f, node.child_by_field_name("right"))
        if right is None:
            return left[0], False
        return left[0] + right[0], right[1]
    return None


def _table_hit(rule: str, argline: str, line: int, field: str | None) -> Hit | None:
    """The ``table[,column]`` part of ``exists:`` / ``unique:`` as a hit."""
    parts = argline.split(",")
    target = parts[0].strip()
    if not target:
        return None
    column = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    if "\\" in target:
        return Hit("model", line, field, rule, fqn=target.lstrip("\\"), column=column)
    if "." in target:                       # connection.table (or schema.table)
        target = target.rsplit(".", 1)[-1]
    return Hit("table", line, field, rule, table=target, column=column)


def _string_rules(text: str, complete: bool, line: int, field: str | None) -> list[Hit]:
    hits: list[Hit] = []
    pieces = text.split("|")
    for i, piece in enumerate(pieces):
        last = i == len(pieces) - 1
        name, sep, rest = piece.partition(":")
        name = name.strip().lower()
        if name not in _TABLE_RULES or not sep:
            continue
        cut = last and not complete         # the dynamic part may have shortened this piece
        if cut and "," not in rest:
            continue                        # `exists:bran` + dynamic: the table name may be cut
        hit = _table_hit(name, rest, line, field)
        if hit is not None:
            if cut and len(rest.split(",")) < 3:
                hit.column = None           # `unique:users,em` + dynamic: the column may be cut
            hits.append(hit)
    return hits


def _rule_facade_call(f: PhpFile, node):
    """``(method, args)`` when ``node`` is ``Rule::method(...)`` (a chain is peeled to its base)."""
    cur = node
    while cur is not None and cur.type == "member_call_expression":
        cur = cur.child_by_field_name("object")
    if cur is None or cur.type != "scoped_call_expression":
        return None
    scope = cur.child_by_field_name("scope")
    name = cur.child_by_field_name("name")
    if scope is None or name is None or scope.type not in ("name", "qualified_name"):
        return None
    fqn = f.class_fqn(f.text(scope))
    if fqn is None or fqn.lower() not in RULE_FACADES:
        return None
    return f.text(name), args_of(cur), line_of(cur)


def _creation_args(node) -> list:
    """Positional argument value nodes of ``new X(...)`` (its ``arguments`` is not a named field)."""
    args = next((c for c in node.children if c.type == "arguments"), None)
    out = []
    for a in args.children if args is not None else []:
        if a.type == "argument" and a.child_by_field_name("name") is None:
            val = next((c for c in a.children if c.is_named), None)
            if val is not None:
                out.append(val)
    return out


def _visit(f: PhpFile, node, field: str | None, out: list[Hit]) -> None:
    if node is None:
        return
    t = node.type
    if t == "array_creation_expression":
        for el in node.children:
            if el.type == "array_element_initializer":
                named = [c for c in el.children if c.is_named]
                if named:
                    _visit(f, named[-1], field, out)
        return
    if t in ("string", "encapsed_string", "binary_expression", "parenthesized_expression"):
        pre = _concat_prefix(f, node)
        if pre is not None:
            out.extend(_string_rules(pre[0], pre[1], line_of(node), field))
        return
    if t == "object_creation_expression":
        named = [c for c in node.children if c.type in ("name", "qualified_name")]
        if not named:
            return
        fqn = f.class_fqn(f.text(named[0]))
        if fqn is None:
            return
        if fqn.lower() in ENUM_RULES:
            args = _creation_args(node)
            enum = class_ref(f, args[0]) if args else None
            if enum:
                out.append(Hit("enum", line_of(node), field, fqn=enum))
        else:
            out.append(Hit("rule", line_of(node), field, fqn=fqn))
        return
    call = _rule_facade_call(f, node)
    if call is not None:
        name, args, line = call
        pos = positional(args)
        lname = name.lower()
        if lname in _TABLE_RULES and pos:
            first = pos[0].node
            col = str_value(f, pos[1].node) if len(pos) > 1 else None
            model = class_ref(f, first)
            if model:
                out.append(Hit("model", line, field, lname, fqn=model, column=col))
            else:
                tbl = str_value(f, first)
                if tbl:
                    if "\\" in tbl:
                        out.append(Hit("model", line, field, lname, fqn=tbl.lstrip("\\"), column=col))
                    else:
                        out.append(Hit("table", line, field, lname, table=tbl.rsplit(".", 1)[-1], column=col))
        elif lname == "enum" and pos:
            enum = class_ref(f, pos[0].node)
            if enum:
                out.append(Hit("enum", line, field, fqn=enum))


def _visit_rules_array(f: PhpFile, arr, out: list[Hit]) -> None:
    """Entries ``'field' => rule(s)`` of a rules array."""
    for el in arr.children:
        if el.type != "array_element_initializer":
            continue
        named = [c for c in el.children if c.is_named]
        if len(named) == 2:
            _visit(f, named[1], str_value(f, named[0]), out)
        elif len(named) == 1 and named[0].type == "array_creation_expression":
            _visit_rules_array(f, named[0], out)      # [ [..], [..] ] merged lists


def arrays_of(f: PhpFile, method) -> list:
    """The rule arrays a ``rules()`` method returns (literal, ``array_merge``, via a variable)."""
    arrays: list = []
    var_names: set[str] = set()

    def take(expr) -> None:
        if expr is None:
            return
        if expr.type == "array_creation_expression":
            arrays.append(expr)
        elif expr.type == "function_call_expression":
            fn = expr.child_by_field_name("function")
            if fn is not None and f.text(fn).lstrip("\\").lower() == "array_merge":
                for a in positional(args_of(expr)):
                    take(a.node)
        elif expr.type == "variable_name":
            var_names.add(f.text(expr))
        elif expr.type == "parenthesized_expression":
            inner = [c for c in expr.children if c.is_named]
            if len(inner) == 1:
                take(inner[0])

    for e in return_exprs(method):
        take(e)
    if var_names:
        for n in walk(method):
            if n.type != "assignment_expression":
                continue
            left = n.child_by_field_name("left")
            right = n.child_by_field_name("right")
            if left is not None and left.type == "variable_name" and f.text(left) in var_names:
                take(right)
    return arrays


def rules_method_hits(f: PhpFile, cls: ClassView) -> list[tuple[Any, list[Hit]]]:
    """``[(method node, hits)]`` for the class's ``rules()`` method."""
    m = find_method(f, cls, "rules")
    if m is None:
        return []
    hits: list[Hit] = []
    for arr in arrays_of(f, m):
        _visit_rules_array(f, arr, hits)
    return [(m, hits)] if hits else []


def _facade_scope(f: PhpFile, call, names: frozenset[str]) -> bool:
    scope = call.child_by_field_name("scope")
    if scope is None or scope.type not in ("name", "qualified_name"):
        return False
    fqn = f.class_fqn(f.text(scope))
    return fqn is not None and fqn.lower() in names


def call_site_hits(f: PhpFile) -> list[tuple[Any, list[Hit]]]:
    """``[(call node, hits)]`` for ``->validate([...])``, ``Validator::make(...)``, ``validator(...)``."""
    out: list[tuple[Any, list[Hit]]] = []
    for n in walk(f.root):
        arr = None
        if n.type == "member_call_expression":
            name = n.child_by_field_name("name")
            nm = f.text(name).lower() if name is not None else ""
            pos = positional(args_of(n))
            if nm == "validate" and pos:
                arr = pos[0].node
            elif nm == "validatewithbag" and len(pos) > 1:
                arr = pos[1].node
        elif n.type == "scoped_call_expression":
            name = n.child_by_field_name("name")
            pos = positional(args_of(n))
            if name is not None and f.text(name).lower() == "make" and len(pos) > 1 and _facade_scope(
                f, n, VALIDATOR_FACADES
            ):
                arr = pos[1].node
        elif n.type == "function_call_expression":
            fn = n.child_by_field_name("function")
            pos = positional(args_of(n))
            if fn is not None and f.text(fn).lstrip("\\").lower() == "validator" and len(pos) > 1:
                arr = pos[1].node
        if arr is None or arr.type != "array_creation_expression":
            continue
        hits: list[Hit] = []
        _visit_rules_array(f, arr, hits)
        if hits:
            out.append((n, hits))
    return out
