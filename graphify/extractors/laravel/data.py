"""Laravel data-layer modelling (``laravel_data`` resolver): Eloquent, migrations, validation, Filament.

Runs for a Laravel project only (same gate and scan root as the route pass) and adds,
for the files extracted in this run:

Eloquent (``eloquent``)
    * ``eloquent_<type>`` model -> related model for each relation method
      (``eloquent_has_one``, ``eloquent_has_many``, ``eloquent_belongs_to``, ``eloquent_belongs_to_many``,
      ``eloquent_has_one_through``, ``eloquent_has_many_through``, ``eloquent_morph_one``,
      ``eloquent_morph_many``, ``eloquent_morph_to_many``, ``eloquent_morphed_by_many``), ``EXTRACTED``;
      edge metadata ``eloquent_type`` (``belongsTo``), ``method`` and the literal keys
      (``foreign_key``, ``pivot_table``, ...); the method node carries ``metadata.eloquent_relation``.
      ``morphTo`` has no static target: method metadata only. A through relation also links the
      intermediate model (``eloquent_through``), ``->using(Pivot::class)`` links the pivot model
      (``eloquent_pivot``).
    * ``casts_to`` model -> cast / enum class, metadata ``attribute``.
    * ``uses_model`` calling method -> model for ``Model::where()`` style static calls the model
      does not declare (the Eloquent builder forwards them); metadata ``methods``.
    * ``maps_to_table`` model -> table node: ``protected $table`` is ``EXTRACTED`` (also when an
      in-corpus ancestor declares it), otherwise the Laravel naming convention
      (snake_case plural of the class name) is ``INFERRED``.

Migrations (``migrations``)
    One ``table:<name>`` node per table (label = table name; ``metadata.kind == "db_table"`` with
    ``columns``, ``foreign_keys``, ``migrations``), contained by the migration that creates it, plus
    ``alters_table`` from the migrations that alter it and ``foreign_key_to`` table -> table.

Validation (``validation``)
    ``validates_table`` rule owner -> table for ``exists:`` / ``unique:`` / ``Rule::exists`` /
    ``Rule::unique``, ``validates_with`` -> enum (``Rule::enum``, ``new Enum``) or in-corpus custom rule
    class, metadata ``fields`` / ``rules``.

Filament (``filament``)
    ``filament_resource_for`` resource -> model, ``filament_page`` resource -> page class (metadata
    ``key``, ``route``), ``filament_relation_manager`` resource -> relation manager class.

Fail-closed everywhere: a class that does not resolve through the PHP name rules to exactly one
in-corpus declaration, a table the migrations do not define, a non-literal name: no edge.
The Phase-0 generic ``references_constant`` edge on a pair this pass types is replaced by the
typed edge (the graph keeps one edge per node pair).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from graphify.extractors.laravel import eloquent, filament, migrations, validation
from graphify.extractors.laravel.inflect import table_for_class
from graphify.extractors.laravel.phpdecl import ClassView, class_views, walk
from graphify.extractors.laravel.phpsites import Owners
from graphify.extractors.laravel.phpsrc import PhpFile, line_of, load_php_file
from graphify.extractors.laravel.symbols import SymbolIndex
from graphify.extractors.laravel.webfiles import FileIndex
from graphify.resolver_registry import LanguageResolver

MODEL_BASES = frozenset({
    "illuminate\\database\\eloquent\\model",
    "illuminate\\foundation\\auth\\user",
    "illuminate\\database\\eloquent\\relations\\pivot",
    "illuminate\\database\\eloquent\\relations\\morphpivot",
    "illuminate\\notifications\\databasenotification",
    "laravel\\sanctum\\personalaccesstoken",
    "spatie\\permission\\models\\role",
    "spatie\\permission\\models\\permission",
    "stancl\\tenancy\\database\\models\\tenant",
    "stancl\\tenancy\\database\\models\\domain",
})
PIVOT_BASES = frozenset({
    "illuminate\\database\\eloquent\\relations\\pivot",
    "illuminate\\database\\eloquent\\relations\\morphpivot",
})
RULES_OWNER_BASES = frozenset({
    "illuminate\\foundation\\http\\formrequest",
    "livewire\\component",
    "livewire\\form",
    "livewire\\volt\\component",
})
TABLE_PREFIX = "table:"
_STATIC_SCOPE = re.compile(rb"([A-Za-z_][A-Za-z0-9_\\]*)\s*::")
_SELF_SCOPES = frozenset({"self", "static", "parent"})


def table_id(name: str) -> str:
    return f"{TABLE_PREFIX}{name}"


class ClassGraph:
    """Which in-corpus classes reach which framework base, read from the resolved ``inherits`` edges.

    ``_resolve_php_type_references`` already tied each ``extends`` to the in-corpus class or to
    an FQN-labelled external stub, by the PHP name rules; nothing is resolved a second time here.
    """

    def __init__(self, all_nodes: list[dict], all_edges: list[dict]):
        self.stub_label: dict[str, str] = {
            n["id"]: str(n["label"]).lstrip("\\").lower()
            for n in all_nodes
            if n.get("id") and not n.get("source_file") and "\\" in str(n.get("label", ""))
        }
        self.parents: dict[str, list[str]] = {}
        for e in all_edges:
            if e.get("relation") == "inherits" and e.get("source") and e.get("target"):
                self.parents.setdefault(e["source"], []).append(e["target"])
        self._memo: dict[str, frozenset[str]] = {}

    def bases(self, nid: str) -> frozenset[str]:
        """Lower-cased FQNs of the external classes ``nid`` extends, through in-corpus ancestors."""
        if nid in self._memo:
            return self._memo[nid]
        self._memo[nid] = frozenset()       # cycle guard
        out: set[str] = set()
        for p in self.parents.get(nid, []):
            label = self.stub_label.get(p)
            if label is not None:
                out.add(label)
            else:
                out |= self.bases(p)
        self._memo[nid] = frozenset(out)
        return self._memo[nid]

    def is_a(self, nid: str, bases: frozenset[str]) -> bool:
        return bool(self.bases(nid) & bases)

    def in_corpus_parents(self, nid: str) -> list[str]:
        return [p for p in self.parents.get(nid, []) if p not in self.stub_label]


class DataPass:
    def __init__(self, root: Path, idx: SymbolIndex, per_file: list, all_nodes: list[dict], all_edges: list[dict]):
        self.root = root
        self.idx = idx
        self.all_nodes = all_nodes
        self.all_edges = all_edges
        self.fresh = {
            idx.rel(n["source_file"]) for r in per_file if r for n in r.get("nodes", []) if n.get("source_file")
        }
        self.files = FileIndex(idx, all_nodes)
        self.owners = Owners(idx, self.files)
        self.graph = ClassGraph(all_nodes, all_edges)
        self.cache: dict[str, PhpFile | None] = {}
        self.nodes: list[dict] = []
        self.edges: dict[tuple[str, str, str], dict] = {}
        self.class_nodes: dict[str, list[dict]] = {}     # rel -> class nodes declared there
        for n in all_nodes:
            sf = n.get("source_file")
            if n.get("id") and sf and n.get("_callable_class"):
                rel = idx.rel(sf)
                if rel in idx.php_files:
                    self.class_nodes.setdefault(rel, []).append(n)
        self.models = {
            n["id"] for nodes in self.class_nodes.values() for n in nodes if self.graph.is_a(n["id"], MODEL_BASES)
        }
        self.model_names = {
            str(idx.nodes[m].get("label", "")).lower().encode() for m in self.models if m in idx.nodes
        }
        self.tables: dict[str, migrations.TableDef] = {}
        self._table_memo: dict[str, tuple[str, str] | None] = {}
        self._superseded: set[tuple[str, str]] = set()
        self.model_edges: dict[tuple[str, str], dict] = {}

    # ── plumbing ────────────────────────────────────────────────────────────

    def load(self, rel: str) -> PhpFile | None:
        if rel not in self.cache:
            self.cache[rel] = load_php_file(self.root / rel, rel)
        return self.cache[rel]

    def _read(self, rel: str) -> bytes:
        try:
            return (self.root / rel).read_bytes()
        except OSError:
            return b""

    def sf(self, rel: str) -> str | None:
        fnode = self.idx.file_node(rel)
        return fnode["source_file"] if fnode else None

    def edge(
        self, src: str, tgt: str, relation: str, conf: str, rel: str, line: int, context: str, **extra
    ) -> dict | None:
        sf = self.sf(rel)
        if sf is None or src == tgt:
            return None
        key = (src, tgt, relation)
        prior = self.edges.get(key)
        if prior is not None:
            return prior
        e = {
            "source": src, "target": tgt, "relation": relation, "confidence": conf,
            "source_file": sf, "source_location": f"L{line}", "weight": 1.0, "context": context,
        }
        e.update({k: v for k, v in extra.items() if v is not None})
        self.edges[key] = e
        return e

    @staticmethod
    def add_to(e: dict | None, key: str, *values: str) -> None:
        """Append to a sorted-unique list field of an aggregated edge."""
        if e is None:
            return
        cur = e.setdefault(key, [])
        for v in values:
            if v is not None and v not in cur:
                cur.append(v)
        cur.sort()

    def supersede(self, src: str, tgt: str) -> None:
        """Note that a typed edge replaces the Phase-0 generic ``references_constant`` on the pair.

        Applied by ``apply_supersede`` once the whole pass succeeded, so a failure half-way
        never removes an edge without its replacement.
        """
        self._superseded.add((src, tgt))

    def apply_supersede(self) -> None:
        generic: dict[tuple[str, str], list[dict]] = {}
        for e in self.all_edges:
            if e.get("relation") == "references_constant":
                generic.setdefault((e.get("source", ""), e.get("target", "")), []).append(e)
        for pair in self._superseded:
            for e in generic.get(pair, []):
                e["_drop"] = True

    def class_nid(self, fqn: str | None) -> str | None:
        return self.idx.resolve_class(fqn)

    def view_of(self, f: PhpFile, node: dict) -> ClassView | None:
        label = str(node.get("label", ""))
        hit = [v for v in class_views(f) if v.name == label]
        return hit[0] if len(hit) == 1 else None

    def method_node(self, cls_nid: str, name: str, rel: str) -> dict | None:
        nid = self.idx.lookup_method(cls_nid, name)
        node = self.idx.nodes.get(nid) if nid else None
        if node is None or self.idx.rel(node.get("source_file", "")) != rel:
            return None
        return node

    @staticmethod
    def meta(node: dict) -> dict | None:
        m = node.get("metadata")
        if m is None:
            m = node["metadata"] = {}
        return m if isinstance(m, dict) else None

    # ── model -> table ──────────────────────────────────────────────────────

    def _explicit_table(self, nid: str, seen: set[str]) -> tuple[str | None, bool]:
        """``(table, computed)``: the literal ``$table`` of the class or an in-corpus ancestor;
        ``computed`` is True when ``getTable()`` is overridden (no convention can be assumed)."""
        if nid in seen:
            return None, False
        seen.add(nid)
        node = self.idx.nodes.get(nid)
        if node is not None:
            rel = self.idx.rel(node.get("source_file", ""))
            f = self.load(rel)
            view = self.view_of(f, node) if f is not None else None
            if view is not None and f is not None:
                if eloquent.declares_table_method(f, view):
                    return None, True
                explicit = eloquent.read_explicit_table(f, view)
                if explicit is not None:
                    return explicit, False
        for p in self.graph.in_corpus_parents(nid):
            table, computed = self._explicit_table(p, seen)
            if table is not None or computed:
                return table, computed
        return None, False

    def model_table(self, nid: str) -> tuple[str, str] | None:
        """``(table name, confidence)`` of a model class, else None."""
        if nid in self._table_memo:
            return self._table_memo[nid]
        result: tuple[str, str] | None = None
        table, computed = self._explicit_table(nid, set())
        if table is not None:
            result = (table, "EXTRACTED")
        elif not computed and not self.graph.is_a(nid, PIVOT_BASES):
            node = self.idx.nodes.get(nid)
            rel = self.idx.rel(node.get("source_file", "")) if node else ""
            f = self.load(rel) if node else None
            view = self.view_of(f, node) if f is not None and node else None
            if view is not None and not view.abstract and view.kind == "class":
                result = (table_for_class(view.name), "INFERRED")
        self._table_memo[nid] = result
        return result

    # ── passes ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        self.read_tables()
        self.emit_tables()
        for rel in sorted(self.idx.php_files):
            if rel.startswith("vendor/"):
                continue
            nodes = self.class_nodes.get(rel, [])
            raw = self._read(rel) if rel in self.fresh else b""
            if not raw:
                continue
            wants_static = bool(self.models) and b"::" in raw
            wants_rules = b"ules" in raw or b"alidat" in raw
            has_special = any(
                n["id"] in self.models or self.graph.is_a(n["id"], filament.RESOURCE_BASES)
                or self.graph.is_a(n["id"], RULES_OWNER_BASES)
                for n in nodes
            )
            if not (has_special or wants_static or wants_rules):
                continue
            f = self.load(rel)
            if f is None:
                continue
            for n in nodes:
                if n["id"] in self.models:
                    self.eloquent(rel, f, n)
                if self.graph.is_a(n["id"], filament.RESOURCE_BASES):
                    self.filament(rel, f, n)
                if self.graph.is_a(n["id"], RULES_OWNER_BASES) and wants_rules:
                    self.request_rules(rel, f, n)
            if wants_static and self._mentions_model(raw):
                self.statics(rel, f)
            if wants_rules:
                self.call_site_rules(rel, f)

    def _mentions_model(self, raw: bytes) -> bool:
        names = self.model_names
        return any(m.group(1).rsplit(b"\\", 1)[-1].lower() in names for m in _STATIC_SCOPE.finditer(raw))

    # ── migrations ──────────────────────────────────────────────────────────

    def read_tables(self) -> None:
        rels = migrations.migration_order([r for r in self.idx.php_files if migrations.is_migration_file(r)])
        loaded: list[tuple[str, PhpFile]] = []
        for rel in rels:
            if b"Schema" not in self._read(rel):
                continue
            f = self.load(rel)
            if f is not None:
                loaded.append((rel, f))
        self.tables = migrations.read_migrations(loaded)
        # `foreignIdFor(Client::class)`: the key's table is the model's table.
        for t in self.tables.values():
            for fk in t.fks:
                if fk.via == "model" and fk.table is None and fk.model:
                    nid = self.class_nid(fk.model)
                    mt = self.model_table(nid) if nid and nid in self.models else None
                    if mt is not None:
                        fk.table = mt[0]
                        fk.via = "model" if mt[1] == "EXTRACTED" else "model_convention"
        for t in self.tables.values():
            t.fks = [k for k in t.fks if k.table is not None]

    def emit_tables(self) -> None:
        for name in sorted(self.tables):
            t = self.tables[name]
            if not any(r in self.fresh for r in t.migrations):
                continue
            rel, line = t.anchor
            fnode = self.idx.file_node(rel)
            if fnode is None:
                continue
            meta = {
                "kind": "db_table",
                "name": name,
                "columns": [c.as_meta() for c in t.columns.values()],
                "foreign_keys": [k.as_meta() for k in t.fks],
                "migrations": list(t.migrations),
            }
            tid = table_id(name)
            self.nodes.append({
                "id": tid, "label": name, "file_type": "code", "source_file": fnode["source_file"],
                "source_location": f"L{line}", "metadata": meta,
            })
            if t.create is not None:
                self.edge(fnode["id"], tid, "contains", "EXTRACTED", rel, line, "migration_create")
            for arel, aline in t.alters:
                afn = self.idx.file_node(arel)
                if afn is not None and afn["id"] != fnode["id"]:
                    self.edge(afn["id"], tid, "alters_table", "EXTRACTED", arel, aline, "migration_alter")
            for fk in t.fks:
                if fk.table not in self.tables:
                    continue
                conf = "EXTRACTED" if fk.via in ("explicit", "model") else "INFERRED"
                e = self.edge(
                    tid, table_id(fk.table), "foreign_key_to", conf, fk.rel, fk.line, "migration_foreign_key",
                    references=fk.ref_column, via=fk.via, on_delete=fk.on_delete,
                )
                self.add_to(e, "columns", fk.column)

    # ── Eloquent ────────────────────────────────────────────────────────────

    def eloquent(self, rel: str, f: PhpFile, node: dict) -> None:
        view = self.view_of(f, node)
        if view is None:
            return
        cid = node["id"]
        model_meta: dict = {}
        for r in eloquent.read_relations(f, view):
            self.relation(rel, f, cid, node, r)
        casts = eloquent.read_casts(f, view)
        if casts:
            model_meta["casts"] = {c.attribute: (c.fqn or c.primitive) for c in casts}
        for c in casts:
            tgt = self.class_nid(c.fqn) if c.fqn else None
            if tgt is None:
                continue
            e = self.edge(cid, tgt, "casts_to", "EXTRACTED", rel, c.line, "eloquent_cast")
            self.add_to(e, "attributes", c.attribute)
            self.supersede(cid, tgt)
        mt = self.model_table(cid)
        if mt is not None:
            model_meta["table"] = mt[0]
            model_meta["table_source"] = "explicit" if mt[1] == "EXTRACTED" else "convention"
            if mt[0] in self.tables:
                line = line_of(view.node)
                self.edge(
                    cid, table_id(mt[0]), "maps_to_table", mt[1], rel, line, "eloquent_table",
                    via=None if mt[1] == "EXTRACTED" else "convention",
                )
        if model_meta:
            meta = self.meta(node)
            if meta is not None:
                meta["eloquent"] = model_meta

    def relation(self, rel: str, f: PhpFile, cid: str, cls_node: dict, r: eloquent.Relation) -> None:
        mnode = self.method_node(cid, r.method, rel)
        info: dict = {"type": r.kind}
        if r.related:
            info["related"] = r.related
        info.update(r.keys)
        if r.through:
            info["through"] = r.through
        if mnode is not None:
            meta = self.meta(mnode)
            if meta is not None:
                meta["eloquent_relation"] = info
        if r.related is None:
            return
        tgt = self.class_nid(r.related)
        if tgt is None:
            return
        targets = [(tgt, f"eloquent_{r.suffix}", r.keys)]
        if r.through and (thr := self.class_nid(r.through)) is not None:
            targets.append((thr, "eloquent_through", {}))
        if r.pivot_model and (piv := self.class_nid(r.pivot_model)) is not None:
            targets.append((piv, "eloquent_pivot", {}))
        for target, relation, keys in targets:
            # Method -> model: one edge per relation method, whatever else links the two models.
            if mnode is not None:
                self.edge(
                    mnode["id"], target, relation, "EXTRACTED", rel, r.line, "eloquent_method",
                    eloquent_type=r.kind, method=r.method, **keys,
                )
                self.supersede(mnode["id"], target)
            self.model_link(cid, target, relation, rel, r.line, r.kind, r.method, keys)

    def model_link(
        self, src: str, tgt: str, relation: str, rel: str, line: int, kind: str, method: str, keys: dict[str, str]
    ) -> None:
        """Model -> model summary edge. The graph keeps one edge per node pair, whatever its
        direction, so the first relation in source order names the pair and every further
        relation between the two models (an inverse ``hasMany``, a through relation) is listed
        in ``also`` with the model it starts from."""
        pair = (src, tgt) if src < tgt else (tgt, src)
        prior = self.model_edges.get(pair)
        if prior is None:
            e = self.edge(src, tgt, relation, "EXTRACTED", rel, line, "eloquent_relation",
                          eloquent_type=kind, method=method, **keys)
            if e is not None:
                self.model_edges[pair] = e
                self.supersede(src, tgt)
            return
        label = str(self.idx.nodes.get(src, {}).get("label", src))
        entry = {"relation": relation, "from": label, "eloquent_type": kind, "method": method, **keys}
        also = prior.setdefault("also", [])
        same_as_primary = (
            prior["relation"] == relation and prior.get("method") == method and prior["source"] == src
        )
        if entry not in also and not same_as_primary:
            also.append(entry)
            self.supersede(src, tgt)

    def statics(self, rel: str, f: PhpFile) -> None:
        for n in walk(f.root):
            if n.type != "scoped_call_expression":
                continue
            scope = n.child_by_field_name("scope")
            name = n.child_by_field_name("name")
            if scope is None or name is None or scope.type not in ("name", "qualified_name"):
                continue
            text = f.text(scope).strip()
            if text.lower() in _SELF_SCOPES:
                continue
            nid = self.class_nid(f.class_fqn(text))
            if nid is None or nid not in self.models:
                continue
            method = f.text(name)
            if self.idx.lookup_method(nid, method) is not None:
                continue                     # declared on the model: the call resolver owns it
            owner = self.owners.of(f, n)
            if owner is None or owner == nid:
                continue
            e = self.edge(owner, nid, "uses_model", "EXTRACTED", rel, line_of(n), "eloquent_static_call")
            self.add_to(e, "methods", method)
            self.supersede(owner, nid)

    # ── validation ──────────────────────────────────────────────────────────

    def request_rules(self, rel: str, f: PhpFile, node: dict) -> None:
        view = self.view_of(f, node)
        if view is None:
            return
        for method, hits in validation.rules_method_hits(f, view):
            owner = self.owners.of(f, method.child_by_field_name("name") or method)
            self.rule_edges(rel, f, owner, hits)

    def call_site_rules(self, rel: str, f: PhpFile) -> None:
        for call, hits in validation.call_site_hits(f):
            owner = self.owners.of(f, call)
            self.rule_edges(rel, f, owner, hits)

    def rule_edges(self, rel: str, f: PhpFile, owner: str | None, hits: list[validation.Hit]) -> None:
        if owner is None:
            return
        for h in hits:
            if h.kind in ("table", "model"):
                table, conf = h.table, "EXTRACTED"
                if h.kind == "model":
                    nid = self.class_nid(h.fqn)
                    mt = self.model_table(nid) if nid and nid in self.models else None
                    if mt is None:
                        continue
                    table, conf = mt
                if table is None or table not in self.tables:
                    continue
                e = self.edge(
                    owner, table_id(table), "validates_table", conf, rel, h.line, "validation_rule",
                    via=None if conf == "EXTRACTED" else "convention",
                )
                self.add_to(e, "fields", h.field or "")
                self.add_to(e, "rules", h.rule or "")
                if e is not None:
                    e["fields"] = [x for x in e["fields"] if x]
                    e["rules"] = [x for x in e["rules"] if x]
            else:
                tgt = self.class_nid(h.fqn)
                if tgt is None:
                    continue
                e = self.edge(
                    owner, tgt, "validates_with", "EXTRACTED", rel, h.line, "validation_rule",
                    rule_kind="enum" if h.kind == "enum" else "custom_rule",
                )
                self.add_to(e, "fields", h.field or "")
                if e is not None:
                    e["fields"] = [x for x in e["fields"] if x]
                self.supersede(owner, tgt)

    # ── Filament ────────────────────────────────────────────────────────────

    def filament(self, rel: str, f: PhpFile, node: dict) -> None:
        view = self.view_of(f, node)
        if view is None:
            return
        cid = node["id"]
        facts = filament.read_resource(f, view)
        if facts.model:
            model = self.class_nid(facts.model)
            if model is not None and self.edge(
                cid, model, "filament_resource_for", "EXTRACTED", rel, facts.model_line, "filament_resource"
            ):
                self.supersede(cid, model)
        for ref in facts.pages:
            tgt = self.class_nid(ref.fqn)
            if tgt is None:
                continue
            e = self.edge(cid, tgt, "filament_page", "EXTRACTED", rel, ref.line, "filament_resource", route=ref.route)
            self.add_to(e, "keys", ref.key or "")
            if e is not None:
                e["keys"] = [x for x in e["keys"] if x]
                self.supersede(cid, tgt)
        for ref in facts.relations:
            tgt = self.class_nid(ref.fqn)
            if tgt is None:
                continue
            if self.edge(cid, tgt, "filament_relation_manager", "EXTRACTED", rel, ref.line, "filament_resource"):
                self.supersede(cid, tgt)


def resolve_laravel_data(per_file: list, all_nodes: list[dict], all_edges: list[dict]) -> None:
    """The ``laravel_data`` resolver: a no-op unless the scan root is a Laravel project."""
    from graphify.extractors import laravel   # the scan root is set on the package by ``extract()``
    from graphify.extractors.laravel.project import is_laravel_project

    root = laravel._scan_root
    if root is None or not is_laravel_project(root):
        return
    run(root, per_file, all_nodes, all_edges)


DATA_RESOLVER = LanguageResolver("laravel_data", frozenset({".php"}), resolve_laravel_data)


def run(root: Path, per_file: list, all_nodes: list[dict], all_edges: list[dict]) -> None:
    idx = SymbolIndex(root, all_nodes, all_edges)
    if not idx.php_files:
        return
    p = DataPass(root, idx, per_file, all_nodes, all_edges)
    try:
        p.run()
    except Exception as exc:  # a data-layer failure must not take the rest of the build with it
        logging.getLogger(__name__).warning("laravel data modelling failed, skipping: %s", exc)
        return
    p.apply_supersede()
    all_nodes.extend(p.nodes)
    all_edges.extend(p.edges.values())
