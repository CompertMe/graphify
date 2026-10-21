"""Laravel data-layer modelling: Eloquent relations / casts / statics, migrations -> tables,
validation rules, Filament resources.

The fixture project is tests/fixtures/laravel_data_app. Each expected edge is one form of the
feature; the negative rows (a same-named non-model class, a table no migration defines,
a dynamic table name, a ``getTable()`` override, a page that does not exist) are as much part of
the contract: no edge beats a wrong edge.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_php")

from graphify.extract import collect_files, extract  # noqa: E402
from graphify.extractors.laravel.inflect import plural, snake, table_for_class  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
APP = FIXTURES / "laravel_data_app"
MIG = "database/migrations"


def _extract(root: Path) -> dict:
    root = Path(root).resolve()
    return extract(collect_files(root), root=root, parallel=False)


@pytest.fixture(scope="module")
def graph() -> dict:
    return _extract(APP)


def _label(n: dict) -> str:
    return n["label"]


def _edges(g: dict, *relations: str):
    nodes = {n["id"]: n for n in g["nodes"]}
    for e in g["edges"]:
        if e["relation"] in relations:
            yield e, nodes[e["source"]], nodes[e["target"]]


def _pairs(g: dict, *relations: str) -> set[tuple[str, str, str, str]]:
    """``(relation, source label, target label, confidence)``."""
    return {(e["relation"], _label(s), _label(t), e["confidence"]) for e, s, t in _edges(g, *relations)}


def _edge(g: dict, relation: str, src: str, tgt: str) -> dict:
    hits = [e for e, s, t in _edges(g, relation) if _label(s) == src and _label(t) == tgt]
    assert len(hits) == 1, (relation, src, tgt, hits)
    return hits[0]


def _node(g: dict, node_id: str) -> dict:
    return next(n for n in g["nodes"] if n["id"] == node_id)


# ── inflection ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,table", [
    ("Client", "clients"), ("Branch", "branches"), ("Person", "people"), ("Category", "categories"),
    ("Address", "addresses"), ("Status", "statuses"), ("BranchManager", "branch_managers"),
    ("Company", "companies"), ("Box", "boxes"), ("Quiz", "quizzes"), ("Child", "children"),
    ("Leaf", "leaves"), ("Analysis", "analyses"), ("Equipment", "equipment"), ("User", "users"),
    ("Day", "days"), ("Index", "indices"), ("Mouse", "mice"), ("Hero", "heros"),
])
def test_table_for_class(model, table):
    assert table_for_class(model) == table


def test_snake_and_plural_basics():
    assert snake("HTTPLog") == "h_t_t_p_log"
    assert snake("already_snake") == "already_snake"
    assert plural("invoice_line") == "invoice_lines"
    assert plural("person") == "people"


# ── Eloquent relations ──────────────────────────────────────────────────────

MODEL_RELATIONS = {
    ("eloquent_belongs_to", "Branch", "Client"),
    ("eloquent_has_many", "Branch", "Employee"),
    ("eloquent_belongs_to", "Branch", "User"),
    ("eloquent_belongs_to_many", "Branch", "Tag"),
    ("eloquent_pivot", "Branch", "BranchTag"),
    ("eloquent_has_one", "Client", "Person"),
    ("eloquent_morph_many", "Client", "Comment"),
    ("eloquent_has_many_through", "Client", "Employee"),
    ("eloquent_morph_to_many", "Comment", "Tag"),
    ("eloquent_morph_many", "HasAuditTrail", "Comment"),      # a trait's relation method, from the trait
}


def test_model_to_model_relation_edges(graph):
    got = {
        (r, s, t) for r, s, t, c in _pairs(graph, *{r for r, _, _ in MODEL_RELATIONS})
        if c == "EXTRACTED" and not s.startswith(".")          # model-sourced, not method-sourced
    }
    # A model pair that also has the inverse relation (Branch<->Client ...) is one edge: the
    # first relation in source order names it and the rest are listed in `also` (next test).
    assert got == MODEL_RELATIONS


def test_model_pair_lists_the_other_relations_in_also(graph):
    e = _edge(graph, "eloquent_belongs_to", "Branch", "Client")
    assert e["method"] == "client"
    assert e["eloquent_type"] == "belongsTo"
    assert e["foreign_key"] == "client_id"
    also = {(a["relation"], a["from"], a["method"]) for a in e["also"]}
    assert ("eloquent_has_many", "Client", "branches") in also
    assert ("eloquent_through", "Client", "employees") in also      # the intermediate model is linked
    tag = _edge(graph, "eloquent_belongs_to_many", "Branch", "Tag")
    assert tag["pivot_table"] == "branch_tag"
    assert tag["foreign_pivot_key"] == "branch_id" and tag["related_pivot_key"] == "tag_id"
    assert {(a["relation"], a["from"]) for a in tag["also"]} == {("eloquent_morphed_by_many", "Tag")}


def test_every_relation_method_has_its_own_typed_edge(graph):
    """Method -> related model, one per relation method, so an inverse pair loses nothing."""
    got = {(r, s, t) for r, s, t, _c in _pairs(graph, *[
        "eloquent_has_one", "eloquent_has_many", "eloquent_belongs_to", "eloquent_belongs_to_many",
        "eloquent_has_one_through", "eloquent_has_many_through", "eloquent_morph_one", "eloquent_morph_many",
        "eloquent_morph_to_many", "eloquent_morphed_by_many", "eloquent_through", "eloquent_pivot",
    ]) if s.startswith(".")}
    assert got == {
        ("eloquent_has_many", ".branches()", "Branch"),        # Client::branches and User::branches
        ("eloquent_has_one", ".contact()", "Person"),
        ("eloquent_has_many_through", ".employees()", "Employee"),
        ("eloquent_through", ".employees()", "Branch"),
        ("eloquent_has_one_through", ".headOfficeStaff()", "Employee"),
        ("eloquent_through", ".headOfficeStaff()", "Branch"),
        ("eloquent_morph_many", ".comments()", "Comment"),
        ("eloquent_belongs_to", ".client()", "Client"),
        ("eloquent_belongs_to", ".manager()", "User"),
        ("eloquent_has_many", ".employees()", "Employee"),
        ("eloquent_belongs_to_many", ".tags()", "Tag"),
        ("eloquent_pivot", ".tags()", "BranchTag"),
        ("eloquent_belongs_to", ".branch()", "Branch"),
        ("eloquent_belongs_to", ".mentor()", "Employee"),
        ("eloquent_morph_to_many", ".tags()", "Tag"),
        ("eloquent_morph_one", ".pinnedTag()", "Tag"),
        ("eloquent_morphed_by_many", ".branches()", "Branch"),
        ("eloquent_morph_many", ".auditNotes()", "Comment"),
    }


def test_relation_method_carries_metadata(graph):
    meth = next(
        n for n in graph["nodes"]
        if n["label"] == ".client()" and n["source_file"] == "app/Models/Branch.php"
    )
    assert meth["metadata"]["eloquent_relation"] == {
        "type": "belongsTo", "related": "App\\Models\\Client", "foreign_key": "client_id",
    }
    morph = next(n for n in graph["nodes"] if n["label"] == ".owner()")
    assert morph["metadata"]["eloquent_relation"] == {"type": "morphTo"}       # no static target
    assert not [e for e, s, _t in _edges(graph, "eloquent_morph_to") if _label(s) in (".owner()", "Branch")]


def test_relation_defined_in_a_trait(graph):
    e = _edge(graph, "eloquent_morph_many", ".auditNotes()", "Comment")
    assert e["source_file"] == "app/Models/Concerns/HasAuditTrail.php" and e["morph_name"] == "commentable"
    assert _edge(graph, "eloquent_morph_many", "HasAuditTrail", "Comment")["method"] == "auditNotes"


def test_non_literal_related_class_gives_nothing(graph):
    assert not [1 for _e, s, _t in _edges(graph, "eloquent_has_many") if _label(s) == ".dynamic()"]


def test_phase0_reference_is_replaced_not_duplicated(graph):
    """The typed edge supersedes the generic references_constant on the same node pair."""
    generic = {(_label(s), _label(t)) for _e, s, t in _edges(graph, "references_constant")}
    assert (".client()", "Client") not in generic
    assert ("Branch", "BranchType") not in generic          # cast target
    assert ("BranchResource", "Branch") not in generic      # $model
    # A page's `$resource = BranchResource::class` is the inverse reference on the same node pair.
    assert ("ListBranches", "BranchResource") not in generic


# ── casts ───────────────────────────────────────────────────────────────────

def test_casts_to_enum_from_property_and_method(graph):
    assert ("casts_to", "Client", "ClientStatus", "EXTRACTED") in _pairs(graph, "casts_to")     # $casts
    assert ("casts_to", "Branch", "BranchType", "EXTRACTED") in _pairs(graph, "casts_to")       # casts()
    assert _edge(graph, "casts_to", "Client", "ClientStatus")["attributes"] == ["status"]
    assert len(list(_edges(graph, "casts_to"))) == 2          # 'array' / 'datetime' are primitives


def test_model_metadata_records_casts_and_table(graph):
    meta = next(n for n in graph["nodes"] if n["label"] == "Client" and n["source_file"] == "app/Models/Client.php")
    assert meta["metadata"]["eloquent"] == {
        "casts": {"status": "App\\Enums\\ClientStatus", "meta": "array", "joined_at": "datetime"},
        "table": "clients", "table_source": "convention",
    }


# ── tables ──────────────────────────────────────────────────────────────────

def test_one_node_per_table_with_deterministic_id(graph):
    tables = {n["id"]: n for n in graph["nodes"] if (n.get("metadata") or {}).get("kind") == "db_table"}
    assert set(tables) == {
        "table:addresses", "table:audits", "table:branch_tag", "table:branches", "table:categories", "table:clients",
        "table:comments", "table:people", "table:shared_records", "table:staff_members", "table:tags",
        "table:users",
    }                                  # `dyn_a` / `dyn_b` (non-literal names) are not modelled
    assert tables["table:branches"]["label"] == "branches"
    assert tables["table:branches"]["source_file"] == f"{MIG}/2024_01_02_000001_create_branches_table.php"


def test_columns_replayed_across_migrations_up_only(graph):
    cols = {c["name"]: c for c in _node(graph, "table:branches")["metadata"]["columns"]}
    assert "size" not in cols                      # dropColumn in a later migration
    assert "opened_at" not in cols and cols["opens_on"]["nullable"]       # renameColumn
    assert "removed_in_down" not in cols           # down() is ignored
    assert cols["code"]["unique"] and cols["code"]["nullable"]            # added by Schema::table
    assert {"owner_type", "owner_id", "created_at", "updated_at", "category_id"} <= set(cols)
    clients = {c["name"]: c for c in _node(graph, "table:clients")["metadata"]["columns"]}
    assert clients["email"]["unique"] and clients["status"]["default"] == "active"
    assert {"deleted_at", "created_at"} <= set(clients)


def test_migrations_listed_on_the_table(graph):
    assert _node(graph, "table:branches")["metadata"]["migrations"] == [
        f"{MIG}/2024_01_02_000001_create_branches_table.php",
        f"{MIG}/2024_02_01_000001_alter_branches_table.php",
    ]
    assert not [n for n in graph["nodes"] if n["id"] == "table:never_created"]       # dropIfExists in down()
    alters = {(s["source_file"], _label(t)) for _e, s, t in _edges(graph, "alters_table")}
    assert alters == {(f"{MIG}/2024_02_01_000001_alter_branches_table.php", "branches")}
    contained = {(s["source_file"], _label(t)) for _e, s, t in _edges(graph, "contains") if t["id"].startswith("table:")}
    assert (f"{MIG}/2024_01_02_000001_create_branches_table.php", "branches") in contained


def test_foreign_key_edges_and_confidence(graph):
    fks = _pairs(graph, "foreign_key_to")
    assert ("foreign_key_to", "audits", "users", "INFERRED") in fks               # tenant/ sub-folder, Schema::connection()
    assert ("foreign_key_to", "branches", "users", "EXTRACTED") in fks            # constrained('users')
    assert ("foreign_key_to", "staff_members", "branches", "EXTRACTED") in fks    # foreign()->references()->on()
    assert ("foreign_key_to", "branches", "clients", "INFERRED") in fks           # constrained(): plural of client
    assert ("foreign_key_to", "branch_tag", "tags", "INFERRED") in fks
    assert ("foreign_key_to", "branches", "categories", "INFERRED") in fks        # foreignIdFor(Category::class)
    assert ("foreign_key_to", "addresses", "people", "INFERRED") in fks           # ... and Person's table is by convention
    assert not [1 for r, s, t, _c in fks if s == t]                               # self reference: node metadata only
    e = _edge(graph, "foreign_key_to", "branches", "users")
    assert e["on_delete"] == "set null" and e["columns"] == ["manager_id"]
    meta = {k["column"]: k for k in _node(graph, "table:staff_members")["metadata"]["foreign_keys"]}
    assert meta["mentor_id"]["table"] == "staff_members"


def test_model_maps_to_table(graph):
    m = _pairs(graph, "maps_to_table")
    assert ("maps_to_table", "Employee", "staff_members", "EXTRACTED") in m              # protected $table
    assert ("maps_to_table", "SharedRecord", "shared_records", "EXTRACTED") in m         # inherited $table
    assert ("maps_to_table", "BaseModel", "shared_records", "EXTRACTED") in m
    for model, table in [
        ("Client", "clients"), ("Branch", "branches"), ("User", "users"), ("Person", "people"),
        ("Category", "categories"), ("Address", "addresses"), ("Tag", "tags"), ("Comment", "comments"),
    ]:
        assert ("maps_to_table", model, table, "INFERRED") in m, model
    models_with_edge = {s for r, s, _t, _c in m}
    assert "Ghost" not in models_with_edge          # `ghosts` is defined by no migration
    assert "Computed" not in models_with_edge       # getTable() override
    assert "BranchTag" not in models_with_edge      # a Pivot has no convention table here
    assert len(m) == 11


# ── model statics ───────────────────────────────────────────────────────────

def test_undeclared_static_calls_link_the_caller_to_the_model(graph):
    got = {
        (_label(s), _label(t), tuple(e.get("methods", [])))
        for e, s, t in _edges(graph, "uses_model") if s["source_file"].endswith("BranchController.php")
    }
    assert got == {
        (".index()", "Branch", ("active", "query")),
        (".show()", "Branch", ("findOrFail",)),
        (".store()", "Client", ("firstOrCreate",)),
        (".store()", "Branch", ("create",)),
        (".cases()", "Ghost", ("all",)),
        (".cases()", "User", ("where",)),
        ("{closure#1}()", "Client", ("all",)),
    }
    assert {e["confidence"] for e, _s, _t in _edges(graph, "uses_model")} == {"EXTRACTED"}


def test_statics_not_linked_when_declared_foreign_or_not_a_model(graph):
    uses = {(s["source_file"], _label(s), _label(t)) for _e, s, t in _edges(graph, "uses_model")}
    # `Branch::fromCode()` is declared on the model: the call resolver owns that edge.
    assert not [u for u in uses if u[1] == ".byCode()"]
    # `Branch` / `Client` in SupportController name a plain class / a missing class, not the models.
    assert not [u for u in uses if u[0].endswith("SupportController.php")]
    # ClientStatus is an enum.
    assert not [1 for _e, _s, t in _edges(graph, "uses_model") if _label(t) == "ClientStatus"]
    # self:: / static:: inside the model itself.
    assert not [u for u in uses if u[0].startswith("app/Models/")]


# ── validation ──────────────────────────────────────────────────────────────

def test_form_request_rules_link_tables_enums_and_rules(graph):
    rows = {
        (r, _label(t), tuple(e.get("fields", [])), c)
        for e, s, t in _edges(graph, "validates_table", "validates_with")
        if s["source_file"].endswith("StoreBranchRequest.php")
        for r, c in [(e["relation"], e["confidence"])]
    }
    assert rows == {
        ("validates_table", "clients", ("client_id",), "EXTRACTED"),       # Rule::exists('clients', 'id')
        ("validates_table", "branches", ("name",), "EXTRACTED"),          # 'a|unique:branches,name,'.$x
        ("validates_table", "users", ("manager_id",), "EXTRACTED"),       # 'nullable|exists:users,id'
        ("validates_table", "categories", ("category_id",), "INFERRED"),  # Rule::exists(Category::class)
        ("validates_table", "people", ("emails.*",), "INFERRED"),         # 'unique:App\Models\Person,email'
        ("validates_table", "tags", ("tag_ids.*",), "EXTRACTED"),         # Rule::unique('tags')->ignore()
        ("validates_with", "BranchType", ("type",), "EXTRACTED"),         # Rule::enum
        ("validates_with", "ClientStatus", ("status",), "EXTRACTED"),     # new Enum(...)
        ("validates_with", "ValidCode", ("code",), "EXTRACTED"),          # new ValidCode(...)
    }
    assert _edge(graph, "validates_table", ".rules()", "branches")["rules"] == ["unique"]
    assert _edge(graph, "validates_with", ".rules()", "BranchType")["rule_kind"] == "enum"
    assert _edge(graph, "validates_with", ".rules()", "ValidCode")["rule_kind"] == "custom_rule"


def test_rules_built_in_variables_and_livewire_property(graph):
    rows = {
        (_label(s), _label(t), e["confidence"])
        for e, s, t in _edges(graph, "validates_table")
        if s["source_file"].endswith(("UpdateBranchRequest.php", "BranchForm.php"))
    }
    assert rows == {
        (".rules()", "categories", "EXTRACTED"),       # Rule::exists inside $categoryRules
        (".rules()", "tags", "EXTRACTED"),             # $rules['tag'] = '...|exists:tags,id'
        ("BranchForm", "clients", "EXTRACTED"),         # Livewire `protected $rules` belongs to the class
    }                                                   # 'exists:'.config(...) names no table


def test_inline_validate_and_validator_make(graph):
    rows = {
        (_label(s), e["relation"], _label(t))
        for e, s, t in _edges(graph, "validates_table", "validates_with")
        if s["source_file"].endswith("BranchController.php")
    }
    assert rows == {
        (".store()", "validates_table", "clients"),      # $request->validate([...])
        (".store()", "validates_table", "users"),        # Validator::make($data, [...])
        (".store()", "validates_with", "ValidCode"),
    }


def test_unknown_or_dynamic_tables_are_skipped(graph):
    tables = {_label(t) for _e, _s, t in _edges(graph, "validates_table")}
    assert "ghosts" not in tables                        # no migration creates it
    assert not [t for t in tables if t.startswith("exists")]


# ── Filament ────────────────────────────────────────────────────────────────

def test_resource_model_pages_and_relation_managers(graph):
    f = _pairs(graph, "filament_resource_for", "filament_page", "filament_relation_manager")
    assert f == {
        ("filament_resource_for", "BranchResource", "Branch", "EXTRACTED"),
        ("filament_page", "BranchResource", "ListBranches", "EXTRACTED"),
        ("filament_page", "BranchResource", "CreateBranch", "EXTRACTED"),
        ("filament_page", "BranchResource", "EditBranch", "EXTRACTED"),
        ("filament_relation_manager", "BranchResource", "EmployeesRelationManager", "EXTRACTED"),
        ("filament_relation_manager", "BranchResource", "TagsRelationManager", "EXTRACTED"),         # RelationGroup
        ("filament_relation_manager", "BranchResource", "AuditRelationManager", "EXTRACTED"),        # FQCN
        ("filament_resource_for", "ClientResource", "Client", "EXTRACTED"),
        ("filament_page", "ClientResource", "ListBranches", "EXTRACTED"),
        ("filament_page", "ClientResource", "EditClient", "EXTRACTED"),
        ("filament_relation_manager", "ClientResource", "BranchesRelationManager", "EXTRACTED"),
    }


def test_filament_pages_resolve_through_the_import_alias_not_the_short_name(graph):
    # Two classes are called ListBranches; each resource's page is the one in its own namespace.
    lists = {
        _label(s): t["source_file"]
        for _e, s, t in _edges(graph, "filament_page") if _label(t) == "ListBranches"
    }
    assert lists == {
        "BranchResource": "app/Filament/Resources/BranchResource/Pages/ListBranches.php",
        "ClientResource": "app/Filament/Resources/Clients/Pages/ListBranches.php",
    }
    e = _edge(graph, "filament_page", "BranchResource", "EditBranch")
    assert e["route"] == "/{record}/edit" and e["keys"] == ["edit"]
    pages = {_label(t) for _e, s, t in _edges(graph, "filament_page") if _label(s) == "BranchResource"}
    assert "NoSuchPage" not in pages


# ── gate / determinism ──────────────────────────────────────────────────────

DATA_RELATIONS = {
    "eloquent_has_one", "eloquent_has_many", "eloquent_belongs_to", "eloquent_belongs_to_many",
    "eloquent_has_one_through", "eloquent_has_many_through", "eloquent_morph_one", "eloquent_morph_many",
    "eloquent_morph_to_many", "eloquent_morphed_by_many", "eloquent_through", "eloquent_pivot", "casts_to",
    "uses_model", "maps_to_table", "foreign_key_to", "alters_table", "validates_table", "validates_with",
    "filament_resource_for", "filament_page", "filament_relation_manager",
}


def test_not_a_laravel_project_is_untouched(tmp_path):
    """Same sources, no artisan / composer.json: the generic PHP extractor output only."""
    root = tmp_path / "plain"
    for p in APP.rglob("*.php"):
        dest = root / p.relative_to(APP)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(p.read_bytes())
    g = _extract(root)
    assert not [e for e in g["edges"] if e["relation"] in DATA_RELATIONS]
    assert not [n for n in g["nodes"] if str(n["id"]).startswith("table:")]


def test_extraction_is_deterministic():
    a, b = _extract(APP), _extract(APP)
    key = lambda e: (e["source"], e["target"], e["relation"], e["confidence"], e.get("source_location"))  # noqa: E731
    assert sorted(map(key, a["edges"])) == sorted(map(key, b["edges"]))
    assert [n["id"] for n in a["nodes"]] == [n["id"] for n in b["nodes"]]


def test_every_data_edge_has_a_source_location_and_real_endpoints(graph):
    ids = {n["id"] for n in graph["nodes"]}
    for e, _s, _t in _edges(graph, *DATA_RELATIONS):
        assert e["source"] in ids and e["target"] in ids
        assert e["confidence"] in ("EXTRACTED", "INFERRED")
        assert e["source_location"].startswith("L")
        assert e["source_file"]
