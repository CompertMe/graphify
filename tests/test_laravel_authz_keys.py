"""Laravel abilities, policies, config keys and translation keys.

Fixture: tests/fixtures/laravel_authz_app (gates, policies, a permission enum, config
and lang files, a module registered through mergeConfigFrom / loadTranslationsFrom).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_php")

from graphify.extract import collect_files, extract  # noqa: E402
from graphify.extractors.laravel.authzkeys import last_report  # noqa: E402

APP = Path(__file__).parent / "fixtures" / "laravel_authz_app"
CTRL = "app/Http/Controllers/ContactController.php"


def _extract(root: Path) -> dict:
    return extract(collect_files(root), root=root, parallel=False)


@pytest.fixture(scope="module")
def graph() -> dict:
    return _extract(APP)


def _nodes(g: dict) -> dict:
    return {n["id"]: n for n in g["nodes"]}


def _kind(g: dict, kind: str) -> list[dict]:
    return [n for n in g["nodes"] if isinstance(n.get("metadata"), dict) and n["metadata"].get("kind") == kind]


def _edges(g: dict, relation: str) -> list[dict]:
    return [e for e in g["edges"] if e["relation"] == relation]


def _described(g: dict, relation: str, files: tuple[str, ...] | None = None) -> set[tuple[str, str, str, str]]:
    nodes = _nodes(g)
    return {
        (e["confidence"], nodes[e["source"]]["label"], nodes[e["target"]]["label"], e["source_file"])
        for e in _edges(g, relation)
        if files is None or e["source_file"] in files
    }


# ── abilities ────────────────────────────────────────────────────────────────


def test_ability_nodes_come_from_gates_resources_and_enum_values(graph):
    labels = {n["label"] for n in _kind(graph, "laravel_ability")}
    assert labels == {
        "ability:create-branches", "ability:close-books", "ability:manage-legacy",
        "ability:legacy-check", "ability:invokable-gate", "ability:no-such-handler",
        "ability:reports.view", "ability:reports.create",
        # enum-backed, used by a check, no Gate::define
        "ability:view-contacts", "ability:edit-contacts",
    }


def test_dynamic_define_and_unused_or_ambiguous_enum_values_are_not_abilities(graph):
    labels = {n["label"] for n in _kind(graph, "laravel_ability")}
    # `Gate::define($name, ...)` in a loop, a case nothing checks, and a value two enums share
    assert not labels & {"ability:a", "ability:b", "ability:never-checked", "ability:archive-roles"}


def test_gate_define_handler_edges(graph):
    got = {(s, t) for _c, s, t, _f in _described(graph, "handled_by", ("app/Providers/AuthServiceProvider.php",))}
    assert got == {
        ("ability:create-branches", "{closure#1}()"),
        ("ability:close-books", "{closure#2}()"),
        ("ability:manage-legacy", ".manage()"),
        ("ability:legacy-check", ".check()"),
        ("ability:invokable-gate", ".__invoke()"),
        ("ability:reports.view", ".view()"),
        ("ability:reports.create", ".create()"),
    }
    assert {c for c, *_ in _described(graph, "handled_by", ("app/Providers/AuthServiceProvider.php",))} == {"EXTRACTED"}


def test_enum_value_link_is_inferred_and_only_for_unique_values(graph):
    nodes = _nodes(graph)
    links = {
        (nodes[e["source"]]["label"], nodes[e["target"]]["label"], e["confidence"])
        for e in _edges(graph, "references_constant")
        if nodes[e["source"]]["label"].startswith("ability:")
    }
    assert links == {
        ("ability:create-branches", "CREATE_BRANCHES", "INFERRED"),
        ("ability:view-contacts", "VIEW_CONTACTS", "INFERRED"),
        ("ability:edit-contacts", "EDIT_CONTACTS", "INFERRED"),
    }


def test_checks_resolve_literals_enum_values_and_arrays(graph):
    got = {(s, t) for _c, s, t, f in _described(graph, "checks_ability", (CTRL,)) if t.startswith("ability:")}
    assert got == {
        (".branches()", "ability:create-branches"),    # Gate::allows
        (".branches()", "ability:close-books"),        # $user->cannot
        (".branches()", "ability:manage-legacy"),      # Gate::any([...])
        (".branches()", "ability:legacy-check"),
        (".branches()", "ability:reports.view"),       # Gate::resource ability
        (".edit()", "ability:edit-contacts"),          # Enum::CASE->value
        (".edit()", "ability:view-contacts"),          # Enum::CASE (BackedEnum ability)
    }
    # undefined name, computed name, concatenation, ambiguous enum value: no edge, no stub node
    assert not any("undefined" in n["label"] or "prefix-" in n["label"] for n in graph["nodes"])


def test_ability_check_edges_are_extracted_and_located(graph):
    edges = [
        e for e in _edges(graph, "checks_ability")
        if e["source_file"] == CTRL and _nodes(graph)[e["target"]]["label"] == "ability:create-branches"
    ]
    assert len(edges) == 1 and edges[0]["confidence"] == "EXTRACTED"
    assert edges[0]["source_location"] == "L35"


def test_route_can_middleware_and_route_method_link_to_ability(graph):
    got = {(c, s, t) for c, s, t, f in _described(graph, "checks_ability", ("routes/web.php",))}
    assert ("EXTRACTED", "GET /branches", "ability:create-branches") in got       # group middleware
    assert ("EXTRACTED", "GET /contacts/edit", "ability:edit-contacts") in got    # ->can('...')
    assert not any(s == "GET /unknown" for _c, s, _t in got)                      # undefined ability


def test_blade_can_directives(graph):
    got = {(c, t) for c, _s, t, f in _described(graph, "checks_ability", ("resources/views/contacts/index.blade.php",))}
    assert ("EXTRACTED", "ability:close-books") in got
    assert ("EXTRACTED", "ability:manage-legacy") in got and ("EXTRACTED", "ability:invokable-gate") in got
    # @can in a comment, @can($dynamic) and an undefined @canany member: nothing
    assert ("EXTRACTED", "ability:create-branches") not in got
    assert not any("never-defined" in t for _c, t in got)


def test_project_classes_named_gate_or_config_are_not_the_facades(graph):
    assert not [e for e in graph["edges"] if e["source_file"] == "app/Services/NotFacades.php"
                and e["relation"] in ("checks_ability", "reads_config")]


# ── policies ─────────────────────────────────────────────────────────────────


def test_policy_for_edges_and_their_provenance(graph):
    got = {(c, s, t) for c, s, t, _f in _described(graph, "policy_for")}
    assert got == {
        ("EXTRACTED", "OrderPolicy", "Order"),      # $policies, policy outside App\Policies
        ("EXTRACTED", "ReportPolicy", "Report"),    # Gate::policy
        ("EXTRACTED", "InvoicePolicy", "Invoice"),  # #[UsePolicy]
        ("INFERRED", "ContactPolicy", "Contact"),   # naming convention
    }
    nodes = _nodes(graph)
    via = {nodes[e["target"]]["label"]: e["via"] for e in _edges(graph, "policy_for")}
    assert via == {"Order": "$policies", "Report": "Gate::policy", "Invoice": "UsePolicy", "Contact": "convention"}


def test_explicit_registration_beats_the_convention_policy(graph):
    # App\Policies\OrderPolicy exists too, but Order is registered to the Billing one
    nodes = _nodes(graph)
    for e in _edges(graph, "policy_for"):
        if nodes[e["target"]]["label"] == "Order":
            assert nodes[e["source"]]["source_file"] == "app/Domain/Billing/OrderPolicy.php"
    assert not any(
        nodes[e["source"]]["source_file"] == "app/Policies/OrderPolicy.php" for e in _edges(graph, "policy_for")
    )


def test_authorize_with_a_known_model_links_to_the_policy_method(graph):
    nodes = _nodes(graph)
    got = {
        (nodes[e["source"]]["label"], nodes[e["target"]]["source_file"], nodes[e["target"]]["label"], e["confidence"])
        for e in _edges(graph, "checks_ability")
        if e["source_file"] == CTRL and nodes[e["target"]]["label"].startswith(".")
    }
    assert got == {
        (".index()", "app/Policies/ContactPolicy.php", ".viewAny()", "INFERRED"),     # Contact::class
        (".update()", "app/Policies/ContactPolicy.php", ".update()", "INFERRED"),     # typed parameter
        (".show()", "app/Domain/Billing/OrderPolicy.php", ".view()", "INFERRED"),     # $policies
        (".show()", "app/Domain/Billing/InvoicePolicy.php", ".view()", "INFERRED"),   # #[UsePolicy]
    }


def test_unknown_or_policyless_model_gets_no_edge(graph):
    nodes = _nodes(graph)
    show_targets = {
        nodes[e["target"]]["source_file"]
        for e in _edges(graph, "checks_ability")
        if e["source_file"] == CTRL and nodes[e["source"]]["label"] == ".show()"
    }
    # Branch has no policy, `$unknown` has no type: only the Order and Invoice policies
    assert show_targets == {"app/Domain/Billing/OrderPolicy.php", "app/Domain/Billing/InvoicePolicy.php"}


def test_route_middleware_with_a_qualified_model_links_to_the_policy_method(graph):
    nodes = _nodes(graph)
    got = {
        (nodes[e["source"]]["label"], nodes[e["target"]]["label"], e["confidence"])
        for e in _edges(graph, "checks_ability")
        if e["source_file"] == "routes/web.php" and nodes[e["target"]]["label"].startswith(".")
    }
    assert got == {("GET /contacts", ".viewAny()", "INFERRED")}


# ── config keys ──────────────────────────────────────────────────────────────


def test_config_leaf_keys_with_env_metadata(graph):
    keys = {n["metadata"]["key"]: n for n in _kind(graph, "laravel_config_key")}
    assert set(keys) == {
        "app.name", "app.debug", "app.locale", "app.providers", "app.cipher",
        "services.stripe.key", "services.stripe.secret",
        "services.stripe.options.retry.max", "services.stripe.options.retry.deep",   # depth cap: 4 levels
        "services.mailgun.domain", "services.mailgun.endpoint", "services.plans",    # list = leaf
        "modules.blog.name", "modules.blog.per_page",                                # mergeConfigFrom prefix
    }
    assert keys["app.name"]["metadata"]["env"] == ["APP_NAME"]
    assert keys["app.name"]["metadata"]["env_default"] == "Laravel"
    assert keys["app.debug"]["metadata"]["env"] == ["APP_DEBUG"]            # cast around env()
    assert keys["services.stripe.key"]["metadata"].get("env_default") is None
    assert "env" not in keys["app.locale"]["metadata"]
    assert keys["app.name"]["source_file"] == "config/app.php"


def test_unregistered_module_config_has_no_key_nodes(graph):
    assert not any("orphan" in n["label"] for n in graph["nodes"])


def test_config_keys_are_contained_by_their_file(graph):
    nodes = _nodes(graph)
    contained = {
        nodes[e["target"]]["label"]: nodes[e["source"]]["source_file"]
        for e in _edges(graph, "contains") if nodes[e["target"]]["label"].startswith("config:")
    }
    assert contained["config:app.name"] == "config/app.php"
    assert contained["config:modules.blog.per_page"] == "modules/Blog/config/config.php"


def test_config_usages_resolve_exact_parent_and_branch(graph):
    nodes = _nodes(graph)
    rows = {
        (nodes[e["target"]]["label"], e["confidence"], e.get("resolved_as"), e["key"], e["access"])
        for e in _edges(graph, "reads_config") if e["source_file"] == "app/Services/Reporter.php"
    }
    assert ("config:app.name", "EXTRACTED", None, "app.name", "get") in rows            # config()
    assert ("config:services.mailgun.domain", "EXTRACTED", None, "services.mailgun.domain", "get") in rows  # Config::get
    assert ("config:modules.blog.per_page", "EXTRACTED", None, "modules.blog.per_page", "get") in rows      # config()->get
    assert ("config:app.debug", "EXTRACTED", None, "app.debug", "get") in rows          # Config::boolean
    assert ("config:app.locale", "EXTRACTED", None, "app.locale", "set") in rows        # config([...])
    assert (
        "config:services.stripe.options.retry.deep", "INFERRED", "parent",
        "services.stripe.options.retry.deep.deeper", "get",
    ) in rows
    # a branch naming a whole section links to the file, as INFERRED
    assert ("services.php", "INFERRED", "file", "services.stripe", "get") in rows
    # unknown, dynamic and interpolated keys: nothing
    assert not any(r[3] in ("no.such.key",) or "{" in r[3] for r in rows)


def test_config_edges_come_from_the_enclosing_method_or_the_blade_file(graph):
    nodes = _nodes(graph)
    sources = {
        nodes[e["source"]]["label"] for e in _edges(graph, "reads_config")
    }
    assert sources == {".run()", "index.blade.php"}


# ── translation keys ─────────────────────────────────────────────────────────


def test_one_translation_node_per_key_across_locales(graph):
    keys = {n["metadata"]["key"]: n["metadata"] for n in _kind(graph, "laravel_translation_key")}
    # only keys some usage reads get a node (`messages.errors.forbidden` is defined but never used)
    assert set(keys) == {
        "messages.welcome", "messages.goodbye", "messages.errors.not_found",
        "messages.days", "blog::posts.title", "Save changes", "Hello, :name!",
    }
    assert last_report()["translation_keys_defined"] == 8 and last_report()["translation_keys_used"] == 7
    assert keys["messages.welcome"]["locales"] == ["ar", "en"] and "missing_locales" not in keys["messages.welcome"]
    assert keys["messages.goodbye"]["locales"] == ["en"] and keys["messages.goodbye"]["missing_locales"] == ["ar"]
    assert keys["Save changes"]["source"] == "json" and keys["messages.welcome"]["source"] == "php"
    assert keys["blog::posts.title"]["namespace"] == "blog"
    primary = {n["metadata"]["key"]: n["source_file"] for n in _kind(graph, "laravel_translation_key")}
    assert primary["messages.welcome"] == "lang/en/messages.php"       # en is the primary locale
    assert primary["Save changes"] == "lang/en.json"
    assert primary["blog::posts.title"] == "modules/Blog/resources/lang/en/posts.php"


def test_translation_usages_link_in_php_and_blade(graph):
    nodes = _nodes(graph)
    rows = {
        (nodes[e["source"]]["label"], nodes[e["target"]]["label"])
        for e in _edges(graph, "uses_translation")
    }
    assert rows == {
        (".texts()", "lang:messages.welcome"),
        (".texts()", "lang:messages.errors.not_found"),
        (".texts()", "lang:messages.goodbye"),
        (".texts()", "lang:blog::posts.title"),
        (".texts()", "lang:Save changes"),
        (".texts()", "lang:messages.days"),
        (".texts()", "lang:Hello, :name!"),
        ("index.blade.php", "lang:messages.welcome"),
        ("index.blade.php", "lang:messages.goodbye"),     # @lang
        ("index.blade.php", "lang:Save changes"),         # trans()
    }
    assert {e["confidence"] for e in _edges(graph, "uses_translation")} == {"EXTRACTED"}


def test_missing_translation_keys_are_reported_not_linked(graph):
    nodes = _nodes(graph)
    by_file = {
        n["source_file"]: n["metadata"] for n in graph["nodes"]
        if isinstance(n.get("metadata"), dict) and "missing_translation_keys" in n["metadata"]
    }
    assert by_file["app/Services/Reporter.php"]["missing_translation_keys"] == ["messages.missing_one"]
    assert by_file["resources/views/contacts/index.blade.php"]["missing_translation_keys"] == ["messages.gone"]
    # free text with no JSON entry is a default-locale string, a branch is not missing
    assert "Free text with no json entry" not in str(by_file) and "messages.errors" not in str(by_file["app/Services/Reporter.php"])
    assert not any(n["label"].endswith("missing_one") for n in nodes.values())
    assert last_report()["translation_missing_keys"] == 2


# ── gate, determinism, ids ───────────────────────────────────────────────────


def test_non_laravel_project_is_untouched(tmp_path):
    root = tmp_path / "plain"
    shutil.copytree(APP, root)
    (root / "artisan").unlink()
    (root / "composer.json").write_text('{"name": "x/plain"}\n', encoding="utf-8")
    g = _extract(root)
    assert not _kind(g, "laravel_ability") and not _kind(g, "laravel_config_key")
    assert not _kind(g, "laravel_translation_key")
    assert not [e for e in g["edges"] if e["relation"] in ("checks_ability", "reads_config", "uses_translation", "policy_for")]


def test_output_is_deterministic():
    def snapshot() -> list:
        g = _extract(APP)
        nodes = sorted(n["id"] for n in g["nodes"])
        edges = sorted((e["source"], e["target"], e["relation"], e["confidence"], e["source_location"]) for e in g["edges"])
        return [nodes, edges]

    assert snapshot() == snapshot()


def test_node_ids_are_unique_and_stable(graph):
    ids = [n["id"] for n in graph["nodes"]]
    assert len(ids) == len(set(ids))
    ability = next(n for n in _kind(graph, "laravel_ability") if n["label"] == "ability:create-branches")
    assert ability["id"].startswith("ability_create_branches_")
