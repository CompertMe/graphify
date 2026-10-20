"""`Status::Active` and `Status::class` in one file: one edge each, right targets.

Two PHP paths touch `Scope::MEMBER`: the `Foo::class` emitter (PHP name rules via
`_resolve_php_type_references`) and the enum-case resolver. Merged, a `Foo::BAR`
access must yield exactly ONE `references_constant` edge (the case node, else the
class) with the scope resolved by the PHP name rules, and `Foo::class` exactly one
edge to the class. A same-named enum in another namespace is a decoy.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from graphify.extract import extract

_REAL = (
    "<?php\nnamespace App\\Enums;\nenum Status: string {\n"
    "    case Active = 'a';\n    case Draft = 'd';\n}\n"
)
_DECOY = (
    "<?php\nnamespace Vendor\\Other;\nenum Status: string {\n"
    "    case Active = 'a';\n    case Draft = 'd';\n}\n"
)


def _run(tmp_path: Path, files: dict[str, str]) -> dict:
    for name, body in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return extract([tmp_path / n for n in files], cache_root=Path(tempfile.mkdtemp()),
                   root=tmp_path, parallel=False)


def _node(r: dict, label: str, file_name: str) -> dict:
    found = [n for n in r["nodes"]
             if n.get("label") == label and (n.get("source_file") or "").endswith(file_name)]
    assert len(found) == 1, (label, file_name, found)
    return found[0]


def _targets(r: dict, source: str) -> list[str]:
    return sorted(e["target"] for e in r["edges"]
                  if e["source"] == source and e["relation"] == "references_constant")


def test_case_and_class_each_one_edge_decoy_untouched(tmp_path):
    r = _run(tmp_path, {
        "App/Enums/Status.php": _REAL,
        "Vendor/Other/Status.php": _DECOY,
        "App/Use.php": (
            "<?php\nnamespace App;\nuse App\\Enums\\Status;\n"
            "class Use1 {\n"
            "    public function a() { return Status::Active; }\n"
            "    public function b() { return Status::class; }\n"
            "}\n"),
    })
    a = _node(r, ".a()", "Use.php")["id"]
    b = _node(r, ".b()", "Use.php")["id"]
    real_case = _node(r, "Active", "App/Enums/Status.php")["id"]
    real_enum = _node(r, "Status", "App/Enums/Status.php")["id"]
    assert _targets(r, a) == [real_case]
    assert _targets(r, b) == [real_enum]
    decoys = {n["id"] for n in r["nodes"] if "Vendor/Other" in (n.get("source_file") or "")}
    assert not [e for e in r["edges"]
                if e["relation"] == "references_constant" and e["target"] in decoys]


def test_both_accesses_in_one_method_yield_one_edge_each(tmp_path):
    r = _run(tmp_path, {
        "App/Enums/Status.php": _REAL,
        "Vendor/Other/Status.php": _DECOY,
        "App/Use.php": (
            "<?php\nnamespace App;\nuse App\\Enums\\Status;\n"
            "class Use1 {\n"
            "    public function both() { return [Status::Active, Status::class]; }\n"
            "}\n"),
    })
    m = _node(r, ".both()", "Use.php")["id"]
    real_case = _node(r, "Active", "App/Enums/Status.php")["id"]
    real_enum = _node(r, "Status", "App/Enums/Status.php")["id"]
    assert _targets(r, m) == sorted([real_case, real_enum])


def test_scope_resolves_by_namespace_not_unique_name(tmp_path):
    # Same-namespace use (no `use`), and a leading-backslash FQCN; the decoy shares
    # the simple name so only PHP name rules pick the right enum.
    r = _run(tmp_path, {
        "App/Enums/Status.php": _REAL,
        "Vendor/Other/Status.php": _DECOY,
        "App/Enums/Same.php": (
            "<?php\nnamespace App\\Enums;\nclass Same {\n"
            "    public function a() { return Status::Draft; }\n}\n"),
        "App/Fq.php": (
            "<?php\nnamespace App;\nclass Fq {\n"
            "    public function a() { return \\App\\Enums\\Status::Draft; }\n}\n"),
        "App/Unknown.php": (
            "<?php\nnamespace App;\nclass Unknown {\n"
            "    public function a() { return Status::Draft; }\n}\n"),
    })
    draft = _node(r, "Draft", "App/Enums/Status.php")["id"]
    assert _targets(r, _node(r, ".a()", "Enums/Same.php")["id"]) == [draft]
    assert _targets(r, _node(r, ".a()", "App/Fq.php")["id"]) == [draft]
    # `Status` in namespace App names App\Status, which is not in the corpus: no edge
    assert _targets(r, _node(r, ".a()", "App/Unknown.php")["id"]) == []


def test_non_case_constant_links_to_the_resolved_class_once(tmp_path):
    r = _run(tmp_path, {
        "App/Enums/Status.php": _REAL,
        "Vendor/Other/Status.php": _DECOY,
        "App/Use.php": (
            "<?php\nnamespace App;\nuse App\\Enums\\Status;\n"
            "class Use1 {\n"
            "    public function a() { return Status::active; }\n"  # wrong case: not a case
            "}\n"),
    })
    a = _node(r, ".a()", "Use.php")["id"]
    assert _targets(r, a) == [_node(r, "Status", "App/Enums/Status.php")["id"]]
