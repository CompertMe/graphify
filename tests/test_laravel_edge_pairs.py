"""One edge per node pair: Laravel wiring edges survive a `Foo::class` mention or a type link.

The built graph keeps a single edge per node pair. The PHP extractor adds a plain
`references_constant` for every `Foo::class` mention and a class that implements an interface
shares its pair with the container binding of that interface, so without the rule in
`build.py` the surviving edge was decided by the alphabetical order of the relation names.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_php")

from graphify.build import build_from_json  # noqa: E402
from graphify.extract import collect_files, extract  # noqa: E402


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def graph(tmp_path: Path):
    _write(tmp_path, "artisan", "<?php\n")
    _write(tmp_path, "composer.json", '{"require": {"laravel/framework": "^11.0"}}')
    _write(tmp_path, "app/Models/Client.php", """<?php
namespace App\\Models;

use Illuminate\\Database\\Eloquent\\Model;

class Client extends Model
{
    public function branches()
    {
        return $this->hasMany(Branch::class);
    }
}
""")
    _write(tmp_path, "app/Models/Branch.php", """<?php
namespace App\\Models;

use Illuminate\\Database\\Eloquent\\Model;

class Branch extends Model
{
}
""")
    _write(tmp_path, "app/Contracts/Mailer.php", "<?php\nnamespace App\\Contracts;\n\ninterface Mailer {}\n")
    _write(tmp_path, "app/Services/SmtpMailer.php", """<?php
namespace App\\Services;

use App\\Contracts\\Mailer;

class SmtpMailer implements Mailer {}
""")
    _write(tmp_path, "app/Jobs/Ship.php", """<?php
namespace App\\Jobs;

use Illuminate\\Bus\\Queueable;
use Illuminate\\Contracts\\Queue\\ShouldQueue;
use Illuminate\\Foundation\\Bus\\Dispatchable;

class Ship implements ShouldQueue
{
    use Dispatchable, Queueable;
}
""")
    _write(tmp_path, "app/Http/Controllers/OrderController.php", """<?php
namespace App\\Http\\Controllers;

use App\\Jobs\\Ship;

class OrderController
{
    public function store()
    {
        Ship::dispatch();
        return Ship::class;
    }
}
""")
    _write(tmp_path, "app/Providers/AppServiceProvider.php", """<?php
namespace App\\Providers;

use App\\Contracts\\Mailer;
use App\\Services\\SmtpMailer;
use Illuminate\\Support\\ServiceProvider;

class AppServiceProvider extends ServiceProvider
{
    public function register(): void
    {
        $this->app->bind(Mailer::class, SmtpMailer::class);
    }
}
""")
    root = tmp_path.resolve()
    ext = extract(collect_files(root), root=root, parallel=False)
    return build_from_json(ext)


def _edge(G, a: str, b: str, relation: str) -> dict:
    assert G.has_edge(a, b), f"no edge between {a} and {b}"
    data = G.get_edge_data(a, b)
    assert data["relation"] == relation, data
    assert (data["_src"], data["_tgt"]) == (a, b)
    return data


def _extras(data: dict) -> set[str]:
    return set(data.get("also_relations", ()))


def test_typed_eloquent_edge_survives_the_class_mention(graph):
    # `hasMany(Branch::class)` is also a `Foo::class` mention on the same pair.
    data = _edge(graph, "app_models_client_client_branches", "app_models_branch_branch", "eloquent_has_many")
    assert data["confidence"] == "EXTRACTED"
    _edge(graph, "app_models_client_client", "app_models_branch_branch", "eloquent_has_many")


def test_binds_survives_the_implements_link_and_keeps_it(graph):
    data = _edge(graph, "app_contracts_mailer_mailer", "app_services_smtpmailer_smtpmailer", "binds")
    assert "implements" in _extras(data)


def test_dispatches_survives_a_class_mention_and_keeps_it(graph):
    data = _edge(graph, "app_http_controllers_ordercontroller_ordercontroller_store", "app_jobs_ship_ship",
                 "dispatches")
    assert "references_constant" in _extras(data)


@pytest.mark.parametrize("wiring", ["eloquent_belongs_to", "casts_to", "uses_model", "validates_table",
                                    "validates_with", "filament_page", "dispatches", "binds"])
@pytest.mark.parametrize("mention_first", [True, False])
@pytest.mark.parametrize("same_direction", [True, False])
def test_wiring_edge_beats_a_class_mention_in_any_order_and_direction(wiring, mention_first, same_direction):
    """`uses_model` and `validates_*` sort after `references_constant`, the rest before it."""
    def node(i: str) -> dict:
        return {"id": i, "label": i, "source_file": f"{i}.php", "source_location": "L1"}

    def edge(s: str, t: str, rel: str) -> dict:
        return {"source": s, "target": t, "relation": rel, "confidence": "EXTRACTED",
                "source_file": f"{s}.php", "source_location": "L3"}

    mention = edge("a", "b", "references_constant") if same_direction else edge("b", "a", "references_constant")
    typed = edge("a", "b", wiring)
    edges = [mention, typed] if mention_first else [typed, mention]
    G = build_from_json({"nodes": [node("a"), node("b")], "edges": edges})
    data = G.get_edge_data("a", "b")
    assert data["relation"] == wiring
    assert (data["_src"], data["_tgt"]) == ("a", "b")
    assert "references_constant" in _extras(data)
