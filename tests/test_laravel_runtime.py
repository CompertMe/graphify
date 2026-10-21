"""Laravel runtime wiring: commands, scheduler, jobs, events, listeners, observers, bindings.

Fixture: tests/fixtures/laravel_runtime_app (a mini Laravel 11 app that uses every form the
pass reads, plus the forms it must leave alone). tests/fixtures/laravel_plain_php is a PHP
project that is not a Laravel project and must get none of these edges.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_php")

from graphify.extract import collect_files, extract  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
APP = FIXTURES / "laravel_runtime_app"
PLAIN = FIXTURES / "laravel_plain_php"

RUNTIME_RELATIONS = {
    "schedules", "invokes_command", "dispatches", "dispatches_event", "listens_to", "notifies",
    "sends_mail", "observes", "binds", "facade_for",
}


def _extract(root: Path) -> dict:
    return extract(collect_files(root), root=root, parallel=False)


@pytest.fixture(scope="module")
def graph() -> dict:
    logs: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            logs.append(record)

    handler = _Keep(logging.WARNING)
    logging.getLogger("graphify.extractors.laravel").addHandler(handler)
    try:
        g = _extract(APP)
    finally:
        logging.getLogger("graphify.extractors.laravel").removeHandler(handler)
    g["_warnings"] = [r.getMessage() for r in logs]
    return g


def _nodes(g: dict) -> dict:
    return {n["id"]: n for n in g["nodes"]}


def _edges(g: dict, relation: str) -> list[dict]:
    return [e for e in g["edges"] if e["relation"] == relation]


def _label(n: dict) -> str:
    return n["label"].removeprefix(".")


def _described(g: dict, relation: str) -> set[tuple[str, str, str, str]]:
    """(file the edge sits in, source label, target label, confidence)."""
    nodes = _nodes(g)
    return {
        (e["source_file"], _label(nodes[e["source"]]), _label(nodes[e["target"]]), e["confidence"])
        for e in _edges(g, relation)
    }


def _by_kind(g: dict, kind: str) -> list[dict]:
    return [n for n in g["nodes"] if (n.get("metadata") or {}).get("kind") == kind]


def test_the_pass_ran_without_warnings(graph):
    assert graph["_warnings"] == []


# ── commands and scheduler ─────────────────────────────────────────────────

def test_command_names_are_stored_on_the_class_node(graph):
    got = {
        n["label"]: (n["metadata"]["command_name"], n["metadata"]["command_source"])
        for n in graph["nodes"]
        if (n.get("metadata") or {}).get("command_name") and n.get("file_type") == "code"
        and (n["metadata"].get("kind") != "laravel_command")
    }
    assert got == {
        "SendReports": ("reports:send", "signature"),
        "PruneStale": ("stale:prune", "name"),
        "AttributeCommand": ("app:attr", "AsCommand"),
        "DupOne": ("dup:cmd", "signature"),
        "DupTwo": ("dup:cmd", "signature"),
        "BackupRun": ("backup:run", "signature"),
    }
    send = next(n for n in graph["nodes"] if n["label"] == "SendReports")
    assert send["metadata"]["command_signature"] == "reports:send {user?} {--force : Skip the guard}"


def test_closure_command_is_a_node_handled_by_its_closure(graph):
    (cmd,) = [n for n in _by_kind(graph, "laravel_command")]
    assert cmd["label"] == "inspire"
    assert cmd["metadata"]["description"] == "Display an inspiring quote"
    handled = [e for e in _edges(graph, "handled_by") if e["source"] == cmd["id"]]
    nodes = _nodes(graph)
    assert [nodes[e["target"]]["label"] for e in handled] == ["{closure#1}()"]


def test_schedule_entries_carry_the_cron_expression(graph):
    got = {
        (n["source_file"], n["metadata"]["target"], n["metadata"].get("cron"), n["metadata"].get("cron_source"))
        for n in _by_kind(graph, "laravel_schedule")
    }
    assert got == {
        ("app/Console/Kernel.php", "reports:send", "0 0 * * *", "derived"),
        ("app/Console/Kernel.php", "App\\Console\\Commands\\PruneStale", "*/5 * * * *", "literal"),
        ("app/Console/Kernel.php", "backup:run", "0 13 * * *", "derived"),
        ("app/Console/Kernel.php", "inspire", "0 * * * *", "derived"),
        ("app/Console/Kernel.php", "app:attr", "*/5 * * * *", "derived"),
        ("app/Console/Kernel.php", "dup:cmd", "0 0 * * 0", "derived"),
        ("app/Console/Kernel.php", "vendor:publish", "0 0 1 * *", "derived"),
        ("app/Console/Kernel.php", "App\\Jobs\\ProcessReport", "* * * * *", "derived"),
        ("app/Console/Kernel.php", "App\\Jobs\\ProcessReport", "17 * * * *", "derived"),
        ("bootstrap/app.php", "app:attr", "* * * * 1-5", "derived"),
        ("routes/console.php", "inspire", "0 * * * *", "derived"),
        ("routes/console.php", "App\\Console\\Commands\\BackupRun", "0 1,13 * * *", "derived"),
        ("routes/console.php", "reports:send", "*/10 * * * *", "derived"),
    }


def test_schedules_edges_go_from_the_entry_to_the_command_or_job(graph):
    nodes = _nodes(graph)
    got = {
        (e["source_file"], nodes[e["source"]]["metadata"]["target"], nodes[e["target"]]["label"], e["confidence"])
        for e in _edges(graph, "schedules")
    }
    assert got == {
        ("app/Console/Kernel.php", "reports:send", "SendReports", "EXTRACTED"),
        ("app/Console/Kernel.php", "App\\Console\\Commands\\PruneStale", "PruneStale", "EXTRACTED"),
        ("app/Console/Kernel.php", "backup:run", "BackupRun", "EXTRACTED"),
        ("app/Console/Kernel.php", "inspire", "inspire", "EXTRACTED"),
        ("app/Console/Kernel.php", "app:attr", "AttributeCommand", "EXTRACTED"),
        ("app/Console/Kernel.php", "App\\Jobs\\ProcessReport", "ProcessReport", "EXTRACTED"),
        ("bootstrap/app.php", "app:attr", "AttributeCommand", "EXTRACTED"),
        ("routes/console.php", "inspire", "inspire", "EXTRACTED"),
        ("routes/console.php", "App\\Console\\Commands\\BackupRun", "BackupRun", "EXTRACTED"),
        ("routes/console.php", "reports:send", "SendReports", "EXTRACTED"),
    }


def test_ambiguous_and_vendor_commands_get_no_edge(graph):
    nodes = _nodes(graph)
    sources_without_edge = {
        n["metadata"]["target"]: n["metadata"]["unresolved"]
        for n in _by_kind(graph, "laravel_schedule") if "unresolved" in n["metadata"]
    }
    assert sources_without_edge == {"dup:cmd": "ambiguous", "vendor:publish": "vendor_or_unknown"}
    linked = {e["source"] for e in _edges(graph, "schedules")}
    assert not {n["id"] for n in nodes.values() if n.get("metadata", {}).get("unresolved")} & linked
    # a command name built at run time ('not:declared' . config('x')) is not even an entry
    assert not any("not:declared" in str(n.get("metadata")) for n in graph["nodes"])


def test_artisan_calls_become_invokes_command_edges(graph):
    assert _described(graph, "invokes_command") == {
        ("app/Http/Controllers/ReportController.php", "artisan()", "BackupRun", "EXTRACTED"),
        ("app/Http/Controllers/ReportController.php", "artisan()", "SendReports", "EXTRACTED"),
        ("app/Console/Commands/SendReports.php", "handle()", "PruneStale", "EXTRACTED"),
        ("app/Console/Commands/PruneStale.php", "handle()", "SendReports", "EXTRACTED"),
        ("app/Console/Commands/AttributeCommand.php", "handle()", "BackupRun", "EXTRACTED"),
    }
    queued = [e for e in _edges(graph, "invokes_command") if e.get("queued")]
    assert [e["command"] for e in queued] == ["reports:send"]


# ── jobs and events ────────────────────────────────────────────────────────

def test_job_dispatch_forms_resolve_without_the_vendor_trait(graph):
    ctrl = "app/Http/Controllers/ReportController.php"
    assert _described(graph, "dispatches") == {
        (ctrl, "store()", "ProcessReport", "EXTRACTED"),   # X::dispatch / dispatchIf
        (ctrl, "store()", "ChildJob", "EXTRACTED"),        # dispatchSync through the BaseJob ancestor
        (ctrl, "store()", "SyncJob", "EXTRACTED"),         # dispatch(new X)
        (ctrl, "store()", "LastJob", "EXTRACTED"),         # Bus::dispatch(new X)
        (ctrl, "store()", "VendorTraitJob", "INFERRED"),   # under Jobs/, dispatch from an unseen trait
        (ctrl, "pipeline()", "ProcessReport", "EXTRACTED"),  # Bus::chain / Bus::batch / ->chain
        (ctrl, "pipeline()", "ChildJob", "EXTRACTED"),
        (ctrl, "pipeline()", "LastJob", "EXTRACTED"),
    }
    inferred = [e for e in _edges(graph, "dispatches") if e["confidence"] == "INFERRED"]
    assert [e["via"] for e in inferred] == ["jobs-directory"]


def test_non_jobs_and_dynamic_dispatch_get_no_edge(graph):
    nodes = _nodes(graph)
    targets = {nodes[e["target"]]["label"] for e in _edges(graph, "dispatches")}
    # Helper::dispatch is a plain static method; PlainJob has no trait that could supply dispatch();
    # Vendor\Package\RemoteJob is not in the corpus; `$name::dispatch()` is dynamic; Helper in a chain.
    assert not targets & {"Helper", "PlainJob", "RemoteJob"}


def test_event_dispatch_forms(graph):
    ctrl = "app/Http/Controllers/ReportController.php"
    assert _described(graph, "dispatches_event") == {
        (ctrl, "events()", "UserRegistered", "EXTRACTED"),   # event(new E)
        (ctrl, "events()", "OrderShipped", "EXTRACTED"),     # E::dispatch() through the Events\Dispatchable trait
        (ctrl, "events()", "InvoicePaid", "EXTRACTED"),      # Event::dispatch(new E)
    }


def test_notifications_and_mail(graph):
    ctrl = "app/Http/Controllers/ReportController.php"
    assert _described(graph, "notifies") == {(ctrl, "events()", "InvoiceNotification", "EXTRACTED")}
    assert _described(graph, "sends_mail") == {(ctrl, "events()", "WelcomeMail", "EXTRACTED")}


def test_listener_maps(graph):
    got = _described(graph, "listens_to")
    explicit = {
        ("app/Providers/EventServiceProvider.php", "SendWelcomeEmail", "UserRegistered", "EXTRACTED"),
        ("app/Providers/EventServiceProvider.php", "NotifyAdmins", "UserRegistered", "EXTRACTED"),
        ("app/Providers/EventServiceProvider.php", "LogLogin", "UserRegistered", "EXTRACTED"),
        ("app/Providers/AppServiceProvider.php", "ShipListener", "OrderShipped", "EXTRACTED"),
        ("app/Providers/AppServiceProvider.php", "{closure#4}()", "ClosureEvent", "EXTRACTED"),
        ("app/Providers/AppServiceProvider.php", "{closure#5}()", "InvoicePaid", "EXTRACTED"),
        ("app/Providers/TenancyServiceProvider.php", "ProcessReport", "TenantCreated", "EXTRACTED"),
        ("app/Providers/TenancyServiceProvider.php", "ChildJob", "TenantCreated", "EXTRACTED"),
        ("app/Providers/TenancyServiceProvider.php", "ShipListener", "TenantCreated", "EXTRACTED"),
        ("app/Subscribers/AuditSubscriber.php", "AuditSubscriber", "InvoicePaid", "EXTRACTED"),
        ("app/Subscribers/AuditSubscriber.php", "AuditSubscriber", "TenantCreated", "EXTRACTED"),
        # named in $subscribe, untyped `subscribe($events)`, returns [Event::class => 'method']
        ("app/Subscribers/UntypedSubscriber.php", "UntypedSubscriber", "ClosureEvent", "EXTRACTED"),
        ("app/Listeners/AttributeListener.php", "AttributeListener", "InvoicePaid", "EXTRACTED"),
    }
    discovered = {
        ("app/Listeners/DiscoveredListener.php", "DiscoveredListener", "Discovered", "INFERRED"),
        ("app/Listeners/DiscoveredListener.php", "DiscoveredListener", "OrderShipped", "INFERRED"),
        ("app/Listeners/LogLogin.php", "LogLogin", "Illuminate\\Auth\\Events\\Login", "INFERRED"),
    }
    assert got == explicit | discovered


def test_discovery_does_not_duplicate_an_explicit_registration(graph):
    nodes = _nodes(graph)
    pairs = [(nodes[e["source"]]["label"], nodes[e["target"]]["label"]) for e in _edges(graph, "listens_to")]
    # SendWelcomeEmail::handle(UserRegistered) is also discoverable: only the registration stays
    assert pairs.count(("SendWelcomeEmail", "UserRegistered")) == 1
    discovered = [e for e in _edges(graph, "listens_to") if e["confidence"] == "INFERRED"]
    assert {e["via"] for e in discovered} == {"auto-discovery"}
    # helper(Discovered $e) is not a handle*/__invoke method
    assert [e["method"] for e in discovered if nodes[e["target"]]["label"] == "Discovered"] == ["handle"]


def test_observers(graph):
    assert _described(graph, "observes") == {
        ("app/Models/Post.php", "PostObserver", "Post", "EXTRACTED"),         # #[ObservedBy]
        ("app/Models/User.php", "AuditObserver", "User", "EXTRACTED"),        # static::observe
        ("app/Providers/AppServiceProvider.php", "CommentObserver", "Comment", "EXTRACTED"),  # Model::observe
    }


# ── container ──────────────────────────────────────────────────────────────

def test_bindings_link_abstract_to_concrete(graph):
    got = _described(graph, "binds")
    provider = "app/Providers/AppServiceProvider.php"
    assert got == {
        (provider, "Payment", "Gateway", "EXTRACTED"),
        (provider, "Clock", "SystemClock", "EXTRACTED"),                  # fn () => new SystemClock()
        (provider, "Ambiguous", "FlakyThing", "EXTRACTED"),               # closure returning $app->make(X::class)
        (provider, "Ambiguous", "SystemClock", "EXTRACTED"),
        (provider, "Tracker", "TrackerImpl", "EXTRACTED"),                # $bindings array
        (provider, "Mailer", "SmtpMailer", "EXTRACTED"),                  # $singletons array
        (provider, "Laravel\\Fortify\\Contracts\\LoginResponse", "LoginResponse", "EXTRACTED"),  # vendor stub abstract
        (provider, "register()", "ReportService", "EXTRACTED"),           # string alias: the registering method
    }


def test_binding_edges_say_when_the_abstract_has_alternatives(graph):
    nodes = _nodes(graph)
    by_pair = {(nodes[e["source"]]["label"], nodes[e["target"]]["label"]): e for e in _edges(graph, "binds")}
    assert by_pair[("Ambiguous", "FlakyThing")]["alternatives"] == 2
    assert by_pair[("Ambiguous", "SystemClock")]["alternatives"] == 2
    # Tracker is also bound by a closure that returns one of two classes: readable + unreadable
    assert by_pair[("Tracker", "TrackerImpl")]["alternatives"] == 2
    assert "alternatives" not in by_pair[("Payment", "Gateway")]
    assert by_pair[(".register()", "ReportService")]["abstract"] == "reports"


def test_self_bindings_and_dynamic_abstracts_give_no_edge(graph):
    nodes = _nodes(graph)
    targets = {nodes[e["target"]]["label"] for e in _edges(graph, "binds")}
    assert "SelfBound" not in targets
    gateway = [e for e in _edges(graph, "binds") if nodes[e["target"]]["label"] == "Gateway"]
    assert len(gateway) == 1  # `bind($this->abstract(), Gateway::class)` has an unreadable abstract


def test_facades_point_at_their_accessor(graph):
    nodes = _nodes(graph)
    got = {
        (nodes[e["source"]]["label"], nodes[e["target"]]["label"], e["confidence"], e.get("via"))
        for e in _edges(graph, "facade_for")
    }
    assert got == {
        ("Pay", "Payment", "EXTRACTED", None),
        ("Pay", "Gateway", "INFERRED", "binding"),
        ("Reports", "ReportService", "EXTRACTED", "alias binding"),
    }
    # `'mystery.' . config('x')` is not a literal: no edge
    assert not [e for e in _edges(graph, "facade_for") if nodes[e["source"]]["label"] == "Mystery"]


# ── gate and determinism ───────────────────────────────────────────────────

def test_every_runtime_edge_survives_the_one_edge_per_pair_graph(graph):
    """`Gateway implements Payment` and `Payment binds Gateway` share a node pair, as do a
    `Foo::class` mention and the dispatch of Foo: the built graph keeps the runtime relation."""
    from graphify.build import build_from_json

    built = build_from_json(graph, root=APP)
    kept = {
        (d.get("_src", u), d.get("_tgt", v), d["relation"]): d
        for u, v, d in built.edges(data=True) if d.get("relation") in RUNTIME_RELATIONS
    }
    wanted = {(e["source"], e["target"], e["relation"]) for e in graph["edges"] if e["relation"] in RUNTIME_RELATIONS}
    assert wanted - set(kept) == set()
    nodes = _nodes(graph)
    payment = next(d for (s, t, r), d in kept.items() if (nodes[s]["label"], nodes[t]["label"]) == ("Payment", "Gateway"))
    assert payment["relation"] == "binds" and payment["also_relations"] == ["implements"]


def test_a_plain_php_project_gets_none_of_it():
    g = _extract(PLAIN)
    assert not {e["relation"] for e in g["edges"]} & RUNTIME_RELATIONS
    assert not [n for n in g["nodes"] if (n.get("metadata") or {}).get("kind") in ("laravel_schedule", "laravel_command")]


def test_extraction_is_deterministic(graph):
    again = _extract(APP)

    def keys(g: dict) -> list:
        return sorted(
            (e["source"], e["target"], e["relation"], e["confidence"], e["source_location"])
            for e in g["edges"] if e["relation"] in RUNTIME_RELATIONS
        )

    assert keys(graph) == keys(again)
    assert keys(graph)
