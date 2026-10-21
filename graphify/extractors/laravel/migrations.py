"""Database tables read from Laravel migrations (``database/migrations/*.php``).

Every ``Schema::create('t', fn (Blueprint $table) => ...)`` / ``Schema::table('t', ...)``
inside an ``up()`` method is replayed, in migration-file order, onto one ``TableDef`` per
table: the columns (type, nullable, unique, ...), the foreign keys, and the migration
files that created / altered it. ``down()`` is ignored; ``dropIfExists`` / ``drop``
remove a table, ``rename`` moves it, ``dropColumn`` / ``renameColumn`` / ``dropForeign``
edit it.

Foreign keys:

* ``foreign('c')->references('id')->on('t')`` and ``foreignId('c')->constrained('t')``
  name the table: ``explicit``.
* ``foreignId('client_id')->constrained()`` is Laravel's convention (plural of the column
  name without ``_id``): ``convention``.
* ``foreignIdFor(Client::class)`` takes the table of that model: ``model`` (the caller
  resolves the model's table; until then ``model`` is the FQN written in the source).

Not modelled: a table name that is not a literal (``Schema::create($name, ...)``), raw
``DB::statement('CREATE TABLE ...')``, composite foreign keys, Blueprint macros, and
columns added by a package's ``Schema::table`` helper.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from graphify.extractors.laravel.inflect import plural, snake
from graphify.extractors.laravel.phpsrc import (
    CLOSURE_TYPES,
    Arg,
    PhpFile,
    args_of,
    class_ref,
    line_of,
    positional,
    str_value,
    typed_nodes,
)

_EXPR_STATEMENT = frozenset({"expression_statement"})
_SCHEMA_CALLS = frozenset({"scoped_call_expression", "member_call_expression"})

_MIGRATION_DIR = re.compile(r"(^|/)migrations/(?:[^/]+/)*[^/]+\.php$")   # also database/migrations/tenant/
_SCHEMA_FACADES = frozenset({"illuminate\\support\\facades\\schema", "schema"})

# Blueprint method -> column type for single-name column definitions.
_COLUMN_METHODS = frozenset({
    "bigInteger", "binary", "boolean", "char", "date", "dateTime", "dateTimeTz", "decimal", "double",
    "enum", "float", "foreignId", "foreignUuid", "foreignUlid", "geography", "geometry", "id",
    "increments", "integer", "ipAddress", "json", "jsonb", "longText", "macAddress", "mediumInteger",
    "mediumText", "set", "smallInteger", "string", "text", "time", "timeTz", "timestamp", "timestampTz",
    "tinyInteger", "tinyText", "ulid", "unsignedBigInteger", "unsignedInteger", "unsignedMediumInteger",
    "unsignedSmallInteger", "unsignedTinyInteger", "uuid", "year", "bigIncrements", "mediumIncrements",
    "smallIncrements", "tinyIncrements", "vector", "rememberToken", "softDeletes", "softDeletesTz",
    "foreignIdFor", "computed", "unsignedDecimal", "tinyText", "mediumText", "lineString", "point",
    "polygon", "multiPoint", "multiPolygon", "geometryCollection", "multiLineString",
})
_MORPHS = {
    "morphs": ("bigInteger", True), "nullableMorphs": ("bigInteger", True), "uuidMorphs": ("uuid", False),
    "nullableUuidMorphs": ("uuid", False), "ulidMorphs": ("ulid", False), "nullableUlidMorphs": ("ulid", False),
}
_NULLABLE_MORPHS = frozenset({"nullableMorphs", "nullableUuidMorphs", "nullableUlidMorphs"})


@dataclass
class Column:
    name: str
    type: str
    nullable: bool = False
    unique: bool = False
    index: bool = False
    primary: bool = False
    default: str | None = None
    values: list[str] | None = None

    def as_meta(self) -> dict:
        out: dict = {"name": self.name, "type": self.type}
        for key in ("nullable", "unique", "index", "primary"):
            if getattr(self, key):
                out[key] = True
        if self.default is not None:
            out["default"] = self.default
        if self.values:
            out["values"] = list(self.values)
        return out


@dataclass
class ForeignKey:
    column: str
    table: str | None             # referenced table, or None until `model` resolves
    ref_column: str = "id"
    via: str = "explicit"         # explicit | convention | model
    model: str | None = None      # FQN written in foreignIdFor(X::class)
    on_delete: str | None = None
    rel: str = ""
    line: int = 0

    def as_meta(self) -> dict:
        out: dict = {"column": self.column, "table": self.table, "references": self.ref_column, "via": self.via}
        if self.on_delete:
            out["on_delete"] = self.on_delete
        return out


@dataclass
class TableDef:
    name: str
    columns: dict[str, Column] = field(default_factory=dict)
    fks: list[ForeignKey] = field(default_factory=list)
    create: tuple[str, int] | None = None                      # (migration rel, line)
    alters: list[tuple[str, int]] = field(default_factory=list)
    migrations: list[str] = field(default_factory=list)

    @property
    def anchor(self) -> tuple[str, int]:
        if self.create is not None:
            return self.create
        return self.alters[0]


def is_migration_file(rel: str) -> bool:
    return bool(_MIGRATION_DIR.search(rel)) and not rel.startswith(("vendor/", "tests/"))


def migration_order(rels: list[str]) -> list[str]:
    """Laravel runs migrations by file name; ties (different folders) by path."""
    return sorted(rels, key=lambda r: (posixpath.basename(r), r))


def _is_schema(f: PhpFile, scope) -> bool:
    if scope is None or scope.type not in ("name", "qualified_name"):
        return False
    fqn = f.class_fqn(f.text(scope))
    return fqn is not None and fqn.lower() in _SCHEMA_FACADES


@dataclass
class _Call:
    name: str
    args: list[Arg]
    line: int


def _chain(f: PhpFile, expr, var: str | None = None):
    """``(root, [calls in order])`` of ``root->a()->b()`` / ``Scope::a()->b()``."""
    calls: list[_Call] = []
    cur = expr
    while cur is not None:
        if cur.type == "member_call_expression":
            name = cur.child_by_field_name("name")
            if name is None:
                return None
            calls.append(_Call(f.text(name), args_of(cur), line_of(cur)))
            cur = cur.child_by_field_name("object")
        elif cur.type == "scoped_call_expression":
            name = cur.child_by_field_name("name")
            if name is None:
                return None
            calls.append(_Call(f.text(name), args_of(cur), line_of(cur)))
            calls.reverse()
            return cur.child_by_field_name("scope"), calls
        elif cur.type == "variable_name":
            calls.reverse()
            return cur, calls
        else:
            return None
    return None


def _enclosing_method(node) -> str | None:
    cur = node.parent
    while cur is not None:
        if cur.type == "method_declaration":
            nm = cur.child_by_field_name("name")
            return nm.text.decode().lower() if nm is not None else None
        cur = cur.parent
    return None


def _strings(f: PhpFile, args: list[Arg]) -> list[str] | None:
    """Literal strings of positional arguments, flattening one array argument."""
    out: list[str] = []
    for a in positional(args):
        s = str_value(f, a.node)
        if s is not None:
            out.append(s)
            continue
        if a.node is not None and a.node.type == "array_creation_expression":
            items: list[str] = []
            for el in a.node.children:
                if el.type != "array_element_initializer":
                    continue
                named = [c for c in el.children if c.is_named]
                sv = str_value(f, named[-1]) if named else None
                if sv is None:
                    items = []
                    break
                items.append(sv)
            out.extend(items)
    return out


class _Replay:
    def __init__(self, tables: dict[str, TableDef]):
        self.tables = tables

    # ── schema-level operations ────────────────────────────────────────────

    def create(self, rel: str, name: str, line: int, closure, f: PhpFile) -> None:
        t = self.tables.get(name)
        if t is None or t.create is not None:
            t = TableDef(name)          # a re-create replaces (drop + create, or a dev reset)
            self.tables[name] = t
        t.create = (rel, line)
        self._note(t, rel)
        self.blueprint(t, rel, closure, f)

    def alter(self, rel: str, name: str, line: int, closure, f: PhpFile) -> None:
        t = self.tables.get(name)
        if t is None:
            t = TableDef(name)          # altered but created elsewhere (package / raw SQL)
            self.tables[name] = t
        t.alters.append((rel, line))
        self._note(t, rel)
        self.blueprint(t, rel, closure, f)

    @staticmethod
    def _note(t: TableDef, rel: str) -> None:
        if rel not in t.migrations:
            t.migrations.append(rel)

    # ── Blueprint closure ──────────────────────────────────────────────────

    def blueprint(self, t: TableDef, rel: str, closure, f: PhpFile) -> None:
        params = closure.child_by_field_name("parameters")
        var = None
        if params is not None:
            for p in params.children:
                v = next((c for c in p.children if c.type == "variable_name"), None)
                if v is not None:
                    var = f.text(v)
                    break
        if var is None:
            return
        body = closure.child_by_field_name("body") or next(
            (c for c in closure.children if c.type == "compound_statement"), None
        )
        if body is None:
            return
        for n in typed_nodes(body, _EXPR_STATEMENT):
            expr = next((c for c in n.children if c.is_named), None)
            if expr is None or expr.type != "member_call_expression":
                continue
            ch = _chain(f, expr)
            if ch is None:
                continue
            root, calls = ch
            if root is None or root.type != "variable_name" or f.text(root) != var or not calls:
                continue
            self.statement(t, rel, f, calls)

    def statement(self, t: TableDef, rel: str, f: PhpFile, calls: list[_Call]) -> None:
        head, mods = calls[0], calls[1:]
        name, args = head.name, head.args
        lit = _strings(f, args)
        if name in ("dropColumn", "dropColumns"):
            for col in lit or []:
                t.columns.pop(col, None)
            return
        if name == "dropConstrainedForeignId" and lit:
            t.columns.pop(lit[0], None)
            t.fks = [k for k in t.fks if k.column != lit[0]]
            return
        if name == "renameColumn" and lit and len(lit) == 2 and lit[0] in t.columns:
            col = t.columns.pop(lit[0])
            col.name = lit[1]
            t.columns[lit[1]] = col
            for k in t.fks:
                if k.column == lit[0]:
                    k.column = lit[1]
            return
        if name == "dropForeign" and lit:
            for item in lit:
                t.fks = [
                    k for k in t.fks
                    if k.column != item and f"{t.name}_{k.column}_foreign" != item
                ]
            return
        if name == "dropTimestamps":
            t.columns.pop("created_at", None)
            t.columns.pop("updated_at", None)
            return
        if name == "dropSoftDeletes":
            t.columns.pop("deleted_at", None)
            return
        if name == "dropRememberToken":
            t.columns.pop("remember_token", None)
            return
        if name in ("dropMorphs", "dropNullableMorphs") and lit:
            t.columns.pop(f"{lit[0]}_type", None)
            t.columns.pop(f"{lit[0]}_id", None)
            return
        if name == "foreign" and lit and len(lit) == 1:
            self.foreign(t, rel, lit[0], mods, f, head.line)
            return
        if name in ("primary", "unique", "index"):
            col = t.columns.get(lit[0]) if lit and len(lit) == 1 else None   # composite keys flag nothing
            if col is not None:
                setattr(col, name, True)
            return
        if name == "timestamps" or name == "timestampsTz" or name == "nullableTimestamps":
            for c in ("created_at", "updated_at"):
                t.columns[c] = Column(c, "timestamp", nullable=True)
            return
        if name in _MORPHS:
            typ, _ = _MORPHS[name]
            base = lit[0] if lit else None
            if base:
                nullable = name in _NULLABLE_MORPHS
                t.columns[f"{base}_type"] = Column(f"{base}_type", "string", nullable=nullable, index=True)
                t.columns[f"{base}_id"] = Column(f"{base}_id", typ, nullable=nullable, index=True)
            return
        if name not in _COLUMN_METHODS:
            return
        self.column(t, rel, f, head, mods, lit)

    def column(self, t: TableDef, rel: str, f: PhpFile, head: _Call, mods: list[_Call], lit: list[str] | None) -> None:
        name = head.name
        if name == "id":
            col_name = lit[0] if lit else "id"
        elif name == "rememberToken":
            col_name = "remember_token"
        elif name in ("softDeletes", "softDeletesTz"):
            col_name = lit[0] if lit else "deleted_at"
        elif name == "foreignIdFor":
            model = class_ref(f, positional(head.args)[0].node) if positional(head.args) else None
            if model is None:
                return
            explicit = None
            if len(positional(head.args)) > 1:
                explicit = str_value(f, positional(head.args)[1].node)
            col_name = explicit or _model_fk_column(model)
        else:
            if not lit:
                return
            col_name = lit[0]
        ctype = {"id": "bigInteger", "rememberToken": "string", "softDeletes": "timestamp",
                 "softDeletesTz": "timestamp", "foreignId": "bigInteger", "foreignIdFor": "bigInteger",
                 "foreignUuid": "uuid", "foreignUlid": "ulid", "increments": "integer",
                 "bigIncrements": "bigInteger"}.get(name, name)
        col = Column(col_name, ctype)
        if name in ("softDeletes", "softDeletesTz", "rememberToken"):
            col.nullable = True
        if name in ("id", "increments", "bigIncrements", "mediumIncrements", "smallIncrements", "tinyIncrements"):
            col.primary = True
        if name == "enum" and lit and len(head.args) > 1:
            vals = head.args[1].node
            if vals is not None and vals.type == "array_creation_expression":
                col.values = [
                    s for el in vals.children if el.type == "array_element_initializer"
                    for s in [str_value(f, next((c for c in el.children if c.is_named), None))] if s is not None
                ]
        changing = False
        fk: ForeignKey | None = None
        if name in ("foreignId", "foreignUuid", "foreignUlid"):
            fk = ForeignKey(col_name, None, via="convention", rel=rel, line=head.line)
        elif name == "foreignIdFor":
            model = class_ref(f, positional(head.args)[0].node)
            fk = ForeignKey(col_name, None, via="model", model=model, rel=rel, line=head.line)
        for m in mods:
            mn = m.name
            if mn == "nullable":
                col.nullable = not (m.args and m.args[0].node is not None and f.text(m.args[0].node).lower() == "false")
            elif mn == "unique":
                col.unique = True
            elif mn == "index":
                col.index = True
            elif mn == "primary":
                col.primary = True
            elif mn == "default" and m.args:
                col.default = str_value(f, m.args[0].node) or (f.text(m.args[0].node) if m.args[0].node is not None else None)
            elif mn == "change":
                changing = True
            elif fk is not None:
                self.fk_modifier(fk, mn, m, f, col_name)
        if changing and col_name in t.columns:
            old = t.columns[col_name]
            old.type = col.type
            old.nullable = col.nullable
            old.unique = old.unique or col.unique
            return
        t.columns[col_name] = col
        # foreignId() alone is just a column; constrained() / references()->on() make the key.
        if fk is not None and _has_constrained(mods) and (fk.table is not None or fk.via == "model"):
            t.fks = [k for k in t.fks if k.column != fk.column]
            t.fks.append(fk)

    def fk_modifier(self, fk: ForeignKey, name: str, m: _Call, f: PhpFile, col_name: str) -> None:
        lit = _strings(f, m.args)
        if name == "constrained":
            table = lit[0] if lit else None
            ref = lit[1] if lit and len(lit) > 1 else None
            for a in m.args:
                if a.name == "table" and a.node is not None:
                    table = str_value(f, a.node)
                if a.name == "column" and a.node is not None:
                    ref = str_value(f, a.node)
            if ref:
                fk.ref_column = ref
            if table:
                fk.table, fk.via = table, "explicit"
            elif fk.via == "convention":
                stem = col_name[: -len("_" + fk.ref_column)] if col_name.endswith("_" + fk.ref_column) else col_name
                fk.table = plural(stem)
        elif name == "references" and lit:
            fk.ref_column = lit[0]
        elif name == "on" and lit:
            fk.table, fk.via = lit[0], "explicit"
        elif name == "onDelete" and lit:
            fk.on_delete = lit[0]
        elif name == "cascadeOnDelete":
            fk.on_delete = "cascade"
        elif name == "nullOnDelete":
            fk.on_delete = "set null"
        elif name == "restrictOnDelete":
            fk.on_delete = "restrict"

    def foreign(self, t: TableDef, rel: str, column: str, mods: list[_Call], f: PhpFile, line: int) -> None:
        fk = ForeignKey(column, None, via="explicit", rel=rel, line=line)
        for m in mods:
            self.fk_modifier(fk, m.name, m, f, column)
        if fk.table:
            t.fks = [k for k in t.fks if k.column != column]
            t.fks.append(fk)


def _has_constrained(mods: list[_Call]) -> bool:
    return any(m.name in ("constrained", "references") for m in mods)


def _model_fk_column(model_fqn: str) -> str:
    """``foreignIdFor(Client::class)`` -> ``client_id`` (snake of the class basename + ``_id``)."""
    return f"{snake(model_fqn.rsplit(chr(92), 1)[-1])}_id"


def schema_ops(f: PhpFile):
    """``(kind, table, line, closure | None, extra)`` of every Schema call in an ``up()`` method."""
    ops = []
    for n in typed_nodes(f.root, _SCHEMA_CALLS):
        ch = _chain(f, n)
        if ch is None:
            continue
        root, calls = ch
        if not _is_schema(f, root) or not calls:
            continue
        # `Schema::connection('x')->create(...)`: the operation is the last call, and this
        # node is that chain's outermost expression only when no member call wraps it.
        if n.type == "scoped_call_expression" and calls[0].name == "connection" and len(calls) == 1:
            continue
        if n.parent is not None and n.parent.type == "member_call_expression" and n.parent.child_by_field_name("object") == n:
            continue                    # an inner link of a longer chain; the outer node handles it
        op = calls[-1]
        if _enclosing_method(n) == "down":
            continue
        lit = _strings(f, op.args[:1])
        table = lit[0] if lit else None
        closure = None
        for a in op.args[1:]:
            if a.node is not None and a.node.type in CLOSURE_TYPES:
                closure = a.node
        ops.append((op.name, table, op.line, closure, op.args))
    return ops


def read_migrations(files: list[tuple[str, PhpFile]]) -> dict[str, TableDef]:
    """Replay ``files`` (already in migration order) into one ``TableDef`` per live table."""
    tables: dict[str, TableDef] = {}
    replay = _Replay(tables)
    for rel, f in files:
        for kind, table, line, closure, args in schema_ops(f):
            if table is None:
                continue
            if kind == "create" and closure is not None:
                replay.create(rel, table, line, closure, f)
            elif kind == "table" and closure is not None:
                replay.alter(rel, table, line, closure, f)
            elif kind in ("drop", "dropIfExists"):
                tables.pop(table, None)
            elif kind == "rename":
                to = str_value(f, args[1].node) if len(args) > 1 else None
                if to and table in tables:
                    t = tables.pop(table)
                    t.name = to
                    tables[to] = t
    return tables
