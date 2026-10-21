"""Eloquent model readers: relation methods, ``$casts`` / ``casts()``, ``$table``.

Pure readers over a tree-sitter-php tree; ``data`` turns the records into nodes/edges.

Relations
---------
A relation is a method of a model (or trait) whose ``return`` is a call chain rooted at
``$this`` and whose *first* call is one of the Eloquent relation builders::

    return $this->belongsTo(Client::class, 'client_id')->withDefault();

The related class is the first argument as ``X::class`` or an FQCN string; keys, the
pivot table and the morph name are read when they are literals (positional or named
arguments, in Eloquent's own parameter order). ``morphTo`` has no static target. A
related class that is not a literal (``$this->hasMany($related)``) yields no record.
The ``->using(Pivot::class)`` link of a many-to-many is read as the pivot model.

Casts
-----
``protected $casts = [...]`` and ``protected function casts(): array { return [...]; }``.
An entry ``'attr' => X::class`` is a class cast (enum, value object, ``Castable``);
``'attr' => 'datetime'`` is a primitive cast and only recorded as model metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from graphify.extractors.laravel.phpdecl import ClassView, find_method, property_default, return_exprs
from graphify.extractors.laravel.phpsrc import (
    Arg,
    PhpFile,
    args_of,
    class_ref,
    line_of,
    str_value,
)

# Eloquent builder name -> (edge relation suffix, positional parameter names).
RELATION_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "hasOne": ("has_one", ("related", "foreignKey", "localKey")),
    "hasMany": ("has_many", ("related", "foreignKey", "localKey")),
    "belongsTo": ("belongs_to", ("related", "foreignKey", "ownerKey", "relation")),
    "belongsToMany": (
        "belongs_to_many",
        ("related", "table", "foreignPivotKey", "relatedPivotKey", "parentKey", "relatedKey", "relation"),
    ),
    "hasOneThrough": (
        "has_one_through", ("related", "through", "firstKey", "secondKey", "localKey", "secondLocalKey"),
    ),
    "hasManyThrough": (
        "has_many_through", ("related", "through", "firstKey", "secondKey", "localKey", "secondLocalKey"),
    ),
    "morphOne": ("morph_one", ("related", "name", "type", "id", "localKey")),
    "morphMany": ("morph_many", ("related", "name", "type", "id", "localKey")),
    "morphTo": ("morph_to", ("name", "type", "id", "ownerKey")),
    "morphToMany": (
        "morph_to_many",
        ("related", "name", "table", "foreignPivotKey", "relatedPivotKey", "parentKey", "relatedKey", "inverse"),
    ),
    "morphedByMany": (
        "morphed_by_many",
        ("related", "name", "table", "foreignPivotKey", "relatedPivotKey", "parentKey", "relatedKey"),
    ),
}
_BY_LOWER = {k.lower(): k for k in RELATION_SPECS}
# argument name -> metadata key on the edge / method node
_KEY_FIELDS = {
    "foreignKey": "foreign_key", "ownerKey": "owner_key", "localKey": "local_key",
    "table": "pivot_table", "foreignPivotKey": "foreign_pivot_key", "relatedPivotKey": "related_pivot_key",
    "parentKey": "parent_key", "relatedKey": "related_key", "firstKey": "first_key",
    "secondKey": "second_key", "secondLocalKey": "second_local_key", "name": "morph_name",
    "type": "morph_type", "id": "morph_id",
}


@dataclass
class Relation:
    method: str
    line: int
    kind: str                    # builder name as written in Eloquent: "belongsTo"
    suffix: str                  # "belongs_to"
    related: str | None          # FQN of the related class
    through: str | None = None   # FQN of the intermediate class (hasOneThrough / hasManyThrough)
    pivot_model: str | None = None  # FQN from ->using(X::class)
    keys: dict[str, str] = field(default_factory=dict)


@dataclass
class Cast:
    attribute: str
    line: int
    fqn: str | None              # class cast target
    primitive: str | None        # 'datetime', 'array', ... when the value is a string


def class_arg(f: PhpFile, node) -> str | None:
    """FQN of a class passed as ``X::class`` or as an FQCN string (``'App\\Models\\X'``)."""
    if node is None:
        return None
    fqn = class_ref(f, node)
    if fqn:
        return fqn
    s = str_value(f, node)
    if s and "\\" in s.strip("\\"):
        return s.strip().lstrip("\\")
    return None


def _peel(f: PhpFile, expr):
    """``[(name, args, line)]`` of a ``$this->a()->b()`` chain in call order, else None."""
    calls = []
    cur = expr
    while cur is not None and cur.type == "member_call_expression":
        name = cur.child_by_field_name("name")
        if name is None:
            return None
        calls.append((f.text(name), args_of(cur), line_of(cur)))
        cur = cur.child_by_field_name("object")
    if cur is None or cur.type != "variable_name" or f.text(cur) != "$this":
        return None
    calls.reverse()
    return calls


def _bind(args: list[Arg], params: tuple[str, ...]) -> dict[str, object]:
    """Argument name -> value node, positional first, then named."""
    bound: dict[str, object] = {}
    pos = 0
    for a in args:
        if a.name is None:
            if pos < len(params):
                bound[params[pos]] = a.node
            pos += 1
        else:
            bound[a.name] = a.node
    return bound


def relation_of(f: PhpFile, method_name: str, expr) -> Relation | None:
    chain = _peel(f, expr)
    if not chain:
        return None
    name, args, line = chain[0]
    kind = _BY_LOWER.get(name.lower())
    if kind is None:
        return None
    suffix, params = RELATION_SPECS[kind]
    bound = _bind(args, params)
    rel = Relation(method=method_name, line=line, kind=kind, suffix=suffix, related=None)
    if "related" in bound:
        rel.related = class_arg(f, bound["related"])
        if rel.related is None:
            return None            # $this->hasMany($class): the target is not in the source
    if "through" in bound:
        rel.through = class_arg(f, bound["through"])
    for arg_name, meta_key in _KEY_FIELDS.items():
        if arg_name in bound and (val := str_value(f, bound[arg_name])) is not None:
            rel.keys[meta_key] = val
    for cname, cargs, _l in chain[1:]:
        if cname.lower() == "using" and cargs:
            rel.pivot_model = class_arg(f, cargs[0].node)
    if kind != "morphTo" and rel.related is None:
        return None
    return rel


def read_relations(f: PhpFile, cls: ClassView) -> list[Relation]:
    out: list[Relation] = []
    for m in cls.members:
        if m.type != "method_declaration":
            continue
        nm = m.child_by_field_name("name")
        if nm is None:
            continue
        found = [r for e in return_exprs(m) if (r := relation_of(f, f.text(nm), e)) is not None]
        # One relation per method; a method returning two different relations is not
        # a relation we can name.
        keys = {(r.kind, r.related, r.through) for r in found}
        if found and len(keys) == 1:
            out.append(found[0])
    return out


def _array_node(f: PhpFile, node):
    if node is not None and node.type == "array_creation_expression":
        return node
    return None


def read_casts(f: PhpFile, cls: ClassView) -> list[Cast]:
    arrays: list = []
    prop = _array_node(f, property_default(f, cls, "casts"))
    if prop is not None:
        arrays.append(prop)
    meth = find_method(f, cls, "casts")
    if meth is not None:
        arrays.extend(a for e in return_exprs(meth) if (a := _array_node(f, e)) is not None)
    out: list[Cast] = []
    for arr in arrays:
        for el in arr.children:
            if el.type != "array_element_initializer":
                continue
            named = [c for c in el.children if c.is_named]
            if len(named) != 2:
                continue
            attr = str_value(f, named[0])
            if attr is None:
                continue
            val = named[1]
            fqn = class_ref(f, val)
            prim = str_value(f, val) if fqn is None else None
            if fqn is None and prim is None:
                continue
            out.append(Cast(attr, line_of(el), fqn, prim))
    return out


def read_explicit_table(f: PhpFile, cls: ClassView) -> str | None:
    """Literal ``protected $table = 'x'``, else None."""
    node = property_default(f, cls, "table")
    return str_value(f, node) if node is not None else None


def declares_table_method(f: PhpFile, cls: ClassView) -> bool:
    """``getTable()`` overridden: the table is computed, so no convention can be assumed."""
    return find_method(f, cls, "getTable") is not None
