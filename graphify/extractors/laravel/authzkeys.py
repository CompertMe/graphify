"""Authorization, configuration-key and translation-key modelling (``laravel_authz_keys``).

Runs after the route pass and the web pass (it reads the route nodes they emit),
for a Laravel project only. Reading is in ``authz`` (gates, policies, ability
checks) and ``keys`` (``config/*.php``, lang files, their call sites); this module
resolves the records against the corpus and emits:

Nodes (``metadata.kind``)
    ``laravel_ability``          one per ability name defined by ``Gate::define`` /
                                 ``Gate::resource`` or backed by a permission enum case;
    ``laravel_config_key``       one per literal leaf key of a config array file;
    ``laravel_translation_key``  one per ``group.key`` / JSON key, shared by all locales.

Edges
    ``contains``          file -> ability / key node (EXTRACTED);
    ``handled_by``        ability -> handler method or closure of the definition (EXTRACTED);
    ``references_constant`` ability -> enum case whose string value equals the ability
                          (INFERRED; only when exactly one case in the corpus has that value);
    ``policy_for``        policy class -> model: ``Gate::policy`` / ``$policies`` /
                          ``#[UsePolicy]`` (EXTRACTED), Laravel's naming convention (INFERRED);
    ``checks_ability``    route / method / closure / Blade file -> ability (EXTRACTED: the
                          name is written in the source), or -> the policy method when the
                          model class is statically known (INFERRED, ``via`` names how the
                          policy was found);
    ``reads_config``      usage -> config key (EXTRACTED exact; INFERRED when the key is
                          below a leaf, ``resolved_as: parent``, or names a branch of one file,
                          ``resolved_as: file``);
    ``uses_translation``  usage -> translation key (EXTRACTED).

Fail closed: a dynamic name, an ability nothing defines, a model with no resolvable
policy, a config/translation key no file defines get no edge. Edges and nodes are emitted
for files extracted in this run (like the route pass); the indexes are built from every file.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from graphify.extractors.laravel import authz, keys
from graphify.extractors.laravel.emit import ROUTE_KIND
from graphify.extractors.laravel.phpsites import Owners
from graphify.extractors.laravel.phpsrc import PhpFile, load_php_file
from graphify.extractors.laravel.symbols import SymbolIndex
from graphify.extractors.laravel.webfiles import FileIndex
from graphify.resolver_registry import LanguageResolver

ABILITY_KIND = "laravel_ability"
LANG_DIRS = ("lang", "resources/lang")
_SKIP = ("vendor/", "tests/", "node_modules/", "storage/", "bootstrap/cache/")
_DEF_MARKERS = (b"Gate::", b"$policies", b"UsePolicy", b"mergeConfigFrom", b"loadTranslationsFrom")
_USE_MARKERS = (
    b"Gate", b"can(", b"cannot(", b"cant(", b"canAny", b"cannotAny", b"authorize", b"middleware(",
    b"config(", b"Config::", b"__(", b"trans(", b"trans_choice(", b"Lang::",
)
_LOG = logging.getLogger(__name__)
_REPORT: dict[str, int] = {}
_CAN_MIDDLEWARE = re.compile(r"^can:([^,]+)(?:,(.*))?$")


def last_report() -> dict[str, int]:
    """Counters of the last run (nodes/edges added, unresolved and missing keys)."""
    return dict(_REPORT)


class _Out:
    def __init__(self, existing_ids: set[str]):
        self.ids = set(existing_ids)
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self._seen: set[tuple[str, str, str]] = set()
        self.meta_updates: dict[str, dict] = {}

    def node(self, node: dict) -> bool:
        if node["id"] in self.ids:
            return False
        self.ids.add(node["id"])
        self.nodes.append(node)
        return True

    def edge(self, src: str, tgt: str, relation: str, conf: str, sf: str, line: int, context: str, **extra) -> None:
        if src == tgt or (src, tgt, relation) in self._seen:
            return
        self._seen.add((src, tgt, relation))
        e = {
            "source": src, "target": tgt, "relation": relation, "confidence": conf,
            "source_file": sf, "source_location": f"L{max(line, 1)}", "weight": 1.0, "context": context,
        }
        e.update(extra)
        self.edges.append(e)


class _Project:
    """The shared state of one run."""

    def __init__(self, root: Path, per_file: list, all_nodes: list[dict], all_edges: list[dict]):
        self.root = root
        self.idx = SymbolIndex(root, all_nodes, all_edges)
        self.files = FileIndex(self.idx, all_nodes)
        self.nodes = all_nodes
        self.fresh = {
            self.idx.rel(n["source_file"]) for r in per_file if r for n in r.get("nodes", []) if n.get("source_file")
        }
        self.owners = Owners(self.idx, self.files)
        self.out = _Out({n["id"] for n in all_nodes if n.get("id")})
        self._cache: dict[str, PhpFile | None] = {}
        self._sf_prefix: tuple[str, str] | None = None

    def load(self, rel: str) -> PhpFile | None:
        if rel not in self._cache:
            self._cache[rel] = load_php_file(self.root / rel, rel)
        return self._cache[rel]

    def drop(self, rel: str) -> None:
        self._cache.pop(rel, None)

    def raw(self, rel: str) -> bytes:
        try:
            return (self.root / rel).read_bytes()
        except OSError:
            return b""

    def source_file_for(self, rel: str) -> str:
        """``source_file`` string in the style the extractor used for the other files."""
        fnode = self.files.file_node(rel)
        if fnode is not None:
            return fnode["source_file"]
        for r, n in self.files.files.items():
            sf = n["source_file"]
            if sf.endswith(r):
                return sf[: len(sf) - len(r)] + rel
            break
        return rel


# ── entry ────────────────────────────────────────────────────────────────────


def run(root: Path, per_file: list, all_nodes: list[dict], all_edges: list[dict]) -> None:
    _REPORT.clear()
    proj = _Project(root, per_file, all_nodes, all_edges)
    if not proj.files.files:
        return
    facts, cfg_regs, lang_regs, enums = _scan_php(proj)
    blade = _scan_blade(proj)
    _model_abilities(proj, facts, enums, blade)
    _model_config(proj, cfg_regs, facts, blade)
    _model_translations(proj, lang_regs, facts, blade)
    _apply_meta(proj, all_nodes)
    all_nodes.extend(proj.out.nodes)
    all_edges.extend(proj.out.edges)
    _REPORT["nodes_added"] = len(proj.out.nodes)
    _REPORT["edges_added"] = len(proj.out.edges)
    _LOG.info("laravel authz/keys: %s", _REPORT)


class _Facts:
    def __init__(self) -> None:
        self.defs: list[authz.GateDef] = []
        self.resources: list[tuple[str, str, list | None, str, int]] = []
        self.policies: list[authz.PolicyReg] = []
        self.uses: list[authz.AbilityUse] = []
        self.config_uses: list[keys.KeyUse] = []
        self.trans_uses: list[keys.KeyUse] = []


def _scan_php(proj: _Project):
    idx = proj.idx
    rels = [r for r in sorted(idx.php_files) if not r.startswith(_SKIP)]
    enums = authz.EnumIndex()
    raws: dict[str, bytes] = {}
    for rel in rels:
        raw = proj.raw(rel)
        raws[rel] = raw
        if b"enum " in raw:
            f = proj.load(rel)
            if f is not None:
                enums.add_file(f)
            proj.drop(rel)
    facts = _Facts()
    cfg_regs: list[keys.Registration] = []
    lang_regs: list[keys.Registration] = []
    for rel in rels:
        raw = raws[rel]
        is_fresh = rel in proj.fresh
        wants_defs = any(m in raw for m in _DEF_MARKERS)
        wants_uses = is_fresh and any(m in raw for m in _USE_MARKERS)
        if not (wants_defs or wants_uses):
            continue
        f = proj.load(rel)
        if f is None:
            continue
        got = authz.scan_php(f, proj.owners, enums, want_uses=is_fresh)
        facts.defs.extend(got.defs)
        facts.resources.extend(got.resources)
        facts.policies.extend(got.policies)
        facts.uses.extend(got.uses)
        if b"mergeConfigFrom" in raw or b"loadTranslationsFrom" in raw:
            for reg in keys.scan_registrations(f):
                (cfg_regs if reg.kind == "config" else lang_regs).append(reg)
        if is_fresh:
            if b"config(" in raw or b"Config::" in raw:
                facts.config_uses.extend(keys.scan_config_php(f, proj.owners))
            if any(m in raw for m in (b"__(", b"trans(", b"trans_choice(", b"Lang::")):
                facts.trans_uses.extend(keys.scan_trans_php(f, proj.owners))
        proj.drop(rel)
    return facts, cfg_regs, lang_regs, enums


class _Blade:
    def __init__(self) -> None:
        self.abilities: list[authz.AbilityUse] = []
        self.config: list[keys.KeyUse] = []
        self.trans: list[keys.KeyUse] = []


def _scan_blade(proj: _Project) -> _Blade:
    out = _Blade()
    for rel in sorted(r for r in proj.files.files if r.endswith(".blade.php") and r in proj.fresh):
        if rel.startswith(_SKIP):
            continue
        try:
            text = (proj.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        cleaned = authz.blank_blade_comments(text)
        out.abilities.extend(authz.scan_blade(rel, cleaned))
        out.config.extend(keys.scan_blade_config(rel, cleaned))
        out.trans.extend(keys.scan_blade_trans(rel, cleaned))
    return out


# ── abilities and policies ───────────────────────────────────────────────────


def _model_abilities(proj: _Project, facts: _Facts, enums: authz.EnumIndex, blade: _Blade) -> None:
    idx, files, out = proj.idx, proj.files, proj.out

    defs = list(facts.defs)
    for res, policy_fqn, mapping, rel, line in facts.resources:
        pol = idx.resolve_class(policy_fqn)
        if pol is None:
            continue
        pairs = mapping if mapping is not None else list(authz.RESOURCE_ABILITIES.items())
        for ability, method in pairs:
            if idx.lookup_method(pol, method):
                defs.append(authz.GateDef(f"{res}.{ability}", rel, line, policy_fqn, method, via="Gate::resource"))
    by_name: dict[str, list[authz.GateDef]] = {}
    for d in sorted(defs, key=lambda d: (d.rel, d.line, d.name)):
        by_name.setdefault(d.name, []).append(d)

    routes = [
        n for n in proj.nodes
        if isinstance(n.get("metadata"), dict) and n["metadata"].get("kind") == ROUTE_KIND
    ]
    used: set[str] = set()
    for u in [*facts.uses, *blade.abilities]:
        used.update(u.names)
    for n in routes:
        used.update(n["metadata"].get("abilities") or [])

    value_index = enums.values()
    enum_for: dict[str, authz.EnumCase] = {}
    for name in sorted(used):
        cases = value_index.get(name)
        if cases and len(cases) == 1 and not cases[0].rel.startswith(_SKIP):
            enum_for[name] = cases[0]

    ability_nid: dict[str, str] = {}
    for name in sorted(set(by_name) | set(enum_for)):
        ds = by_name.get(name, [])
        case = enum_for.get(name)
        if ds:
            rel, line = ds[0].rel, ds[0].line
        elif case is not None:
            rel, line = case.rel, case.line
        else:
            continue
        nid = keys.stable_id("ability", name)
        ability_nid[name] = nid
        fnode = files.file_node(rel)
        if fnode is None or rel not in proj.fresh:
            continue
        meta: dict = {"kind": ABILITY_KIND, "ability": name, "defined_by": sorted({d.via for d in ds})}
        if case is not None:
            meta["enum_case"] = f"{case.fqn}::{case.name}"
        if len(ds) > 1:
            meta["definitions"] = len(ds)
        if out.node({
            "id": nid, "label": f"ability:{name}", "file_type": "code",
            "source_file": fnode["source_file"], "source_location": f"L{line}", "metadata": meta,
        }):
            out.edge(fnode["id"], nid, "contains", "EXTRACTED", fnode["source_file"], line, "ability_definition")

    for name, ds in by_name.items():
        nid = ability_nid[name]
        for d in ds:
            fnode = files.file_node(d.rel)
            if fnode is None or d.rel not in proj.fresh:
                continue
            target = _handler_node(proj, d)
            if target:
                out.edge(nid, target, "handled_by", "EXTRACTED", fnode["source_file"], d.line, "gate_handler", via=d.via)
    for name, case in enum_for.items():
        cnode = files.symbol_at(case.rel, [case.line], [case.name])
        fnode = files.file_node(case.rel)
        if cnode is None or fnode is None or case.rel not in proj.fresh:
            continue
        out.edge(
            ability_nid[name], cnode["id"], "references_constant", "INFERRED",
            fnode["source_file"], case.line, "ability_enum_value", rule="enum case value equals ability name",
        )
    _REPORT["abilities"] = len(ability_nid)

    policies = _Policies(proj, facts.policies)
    policies.emit_policy_for()

    # usages -> ability / policy method
    for u in [*facts.uses, *blade.abilities]:
        fnode = files.file_node(u.rel)
        if fnode is None:
            continue
        src = u.owner or fnode["id"]
        sf = fnode["source_file"]
        for name in u.names:
            hit = policies.method_for(u.model, name) if u.model else None
            if hit is not None:
                method, pol_via, model_nid = hit
                out.edge(
                    src, method, "checks_ability", "INFERRED", sf, u.line, "policy_method",
                    ability=name, via=pol_via, model=model_nid and proj.idx.nodes[model_nid].get("label"),
                )
            elif name in ability_nid:
                out.edge(src, ability_nid[name], "checks_ability", "EXTRACTED", sf, u.line, u.form, ability=name)
    for n in routes:
        meta = n["metadata"]
        rel = proj.idx.rel(n.get("source_file", ""))
        if rel not in proj.fresh:
            continue
        line = _line(n)
        sf = n["source_file"]
        for entry in meta.get("middleware") or []:
            m = _CAN_MIDDLEWARE.match(entry) if isinstance(entry, str) else None
            if not m:
                continue
            name, model = m.group(1).strip(), (m.group(2) or "").split(",")[0].strip()
            hit = None
            if model and "\\" in model:
                hit = policies.method_for(model.lstrip("\\"), name)
            if hit is not None:
                out.edge(
                    n["id"], hit[0], "checks_ability", "INFERRED", sf, line, "policy_method",
                    ability=name, via=hit[1], model=hit[2] and proj.idx.nodes[hit[2]].get("label"),
                )
            elif name in ability_nid:
                out.edge(n["id"], ability_nid[name], "checks_ability", "EXTRACTED", sf, line, "route_middleware", ability=name)
    _REPORT["ability_edges"] = sum(1 for e in out.edges if e["relation"] == "checks_ability")


def _handler_node(proj: _Project, d: authz.GateDef) -> str | None:
    idx = proj.idx
    if d.closure_line is not None:
        return idx.closure_at(d.rel, d.closure_line)
    if d.handler_class and d.handler_method:
        cls = idx.resolve_class(d.handler_class)
        return idx.lookup_method(cls, d.handler_method) if cls else None
    return None


def _line(node: dict) -> int:
    loc = str(node.get("source_location") or "")
    return int(loc[1:]) if loc[:1] == "L" and loc[1:].isdigit() else 1


class _Policies:
    """Model -> policy lookup: explicit registrations first, then Laravel's naming convention."""

    def __init__(self, proj: _Project, regs: list[authz.PolicyReg]):
        self.proj = proj
        self.regs = sorted(regs, key=lambda r: (r.rel, r.line, r.model, r.policy))
        self.explicit: dict[str, tuple[str, str, authz.PolicyReg]] = {}   # model nid -> (policy nid, via, reg)
        conflicted: set[str] = set()
        for r in self.regs:
            model, policy = proj.idx.resolve_class(r.model), proj.idx.resolve_class(r.policy)
            if model is None or policy is None:
                continue
            prior = self.explicit.get(model)
            if prior is not None and prior[0] != policy:
                conflicted.add(model)
            self.explicit.setdefault(model, (policy, r.via, r))
        for m in conflicted:
            del self.explicit[m]   # two different policies registered: never pick one
        self._conv: dict[str, tuple[str, str] | None] = {}

    def policy_of(self, model_fqn: str) -> tuple[str, str, str] | None:
        """``(policy nid, via, model nid)``."""
        idx = self.proj.idx
        model = idx.resolve_class(model_fqn)
        if model is None:
            return None
        hit = self.explicit.get(model)
        if hit is not None:
            return hit[0], hit[1], model
        for cand in authz.guess_policy_names(model_fqn):
            nid = idx.resolve_class(cand)
            if nid is not None:
                return nid, "convention", model
        return None

    def method_for(self, model_fqn: str | None, ability: str) -> tuple[str, str, str] | None:
        if not model_fqn:
            return None
        got = self.policy_of(model_fqn)
        if got is None:
            return None
        method = self.proj.idx.lookup_method(got[0], ability)
        return (method, got[1], got[2]) if method else None

    def emit_policy_for(self) -> None:
        proj = self.proj
        idx, files, out = proj.idx, proj.files, proj.out
        seen: set[tuple[str, str]] = set()
        for model, (policy, via, reg) in sorted(self.explicit.items()):
            fnode = files.file_node(reg.rel)
            if fnode is None or reg.rel not in proj.fresh:
                continue
            seen.add((policy, model))
            out.edge(policy, model, "policy_for", "EXTRACTED", fnode["source_file"], reg.line, "policy_registration", via=via)
        # convention: every policy class XPolicy whose model X the framework would map back to it
        for label, nodes in sorted(idx._classes.items()):
            if not label.endswith("policy") or len(label) <= len("policy"):
                continue
            for pnode in nodes:
                base = str(pnode.get("label", ""))[: -len("Policy")]
                prel = idx.rel(pnode["source_file"])
                fnode = files.file_node(prel)
                if fnode is None or prel not in proj.fresh:
                    continue
                for mnode in idx._classes.get(base.lower(), []):
                    if mnode["id"] == pnode["id"] or mnode["id"] in self.explicit:
                        continue
                    ns = idx._namespace(idx.rel(mnode["source_file"]))
                    if ns is None:
                        continue
                    fqn = f"{ns}\\{mnode['label']}" if ns else str(mnode["label"])
                    found = next((c for c in authz.guess_policy_names(fqn) if idx.resolve_class(c)), None)
                    if found is None or idx.resolve_class(found) != pnode["id"]:
                        continue
                    if (pnode["id"], mnode["id"]) in seen:
                        continue
                    seen.add((pnode["id"], mnode["id"]))
                    out.edge(
                        pnode["id"], mnode["id"], "policy_for", "INFERRED", fnode["source_file"],
                        _line(pnode), "policy_convention", via="convention",
                    )
        _REPORT["policy_for"] = sum(1 for e in out.edges if e["relation"] == "policy_for")


# ── config ───────────────────────────────────────────────────────────────────


def _model_config(proj: _Project, regs: list[keys.Registration], facts: _Facts, blade: _Blade) -> None:
    idx, files, out = proj.idx, proj.files, proj.out
    index = keys.ConfigIndex()
    prefixes: dict[str, str] = {}
    for rel in sorted(idx.php_files):
        if rel.startswith("config/") and rel.endswith(".php"):
            prefixes[rel] = rel[len("config/"):-len(".php")].replace("/", ".")
    for reg in sorted(regs, key=lambda r: (r.rel, r.line)):
        if reg.kind == "config" and reg.path in idx.php_files and reg.path not in prefixes:
            prefixes[reg.path] = reg.key
    for rel, prefix in sorted(prefixes.items()):
        f = proj.load(rel)
        if f is not None:
            index.add_file(f, prefix)
        proj.drop(rel)
    node_of: dict[tuple[str, str], str] = {}
    for key, defs in sorted(index.leaves.items()):
        for d in defs:
            nid = keys.stable_id("config", key, salt=d.rel)
            node_of[(key, d.rel)] = nid
            fnode = files.file_node(d.rel)
            if fnode is None or d.rel not in proj.fresh:
                continue
            meta: dict = {"kind": keys.CONFIG_KEY, "key": key}
            if d.env:
                meta["env"] = d.env
                if d.env_default is not None:
                    meta["env_default"] = d.env_default
            if out.node({
                "id": nid, "label": f"config:{key}", "file_type": "code",
                "source_file": fnode["source_file"], "source_location": f"L{d.line}", "metadata": meta,
            }):
                out.edge(fnode["id"], nid, "contains", "EXTRACTED", fnode["source_file"], d.line, "config_key")
    _REPORT["config_keys"] = sum(len(v) for v in index.leaves.values())

    unresolved = 0
    for u in [*facts.config_uses, *blade.config]:
        fnode = files.file_node(u.rel)
        if fnode is None:
            continue
        src = u.owner or fnode["id"]
        sf = fnode["source_file"]
        got = index.resolve(u.key)
        if got is None:
            unresolved += 1
            continue
        mode, target = got
        if mode == "file":
            tnode = files.file_node(str(target))
            if tnode is not None and tnode["id"] != src and u.rel != target:
                out.edge(src, tnode["id"], "reads_config", "INFERRED", sf, u.line, "config_key",
                         key=u.key, access=u.access, resolved_as="file")
            continue
        assert isinstance(target, list)
        for d in target:
            if d.rel == u.rel:
                continue
            extra: dict[str, object] = {"key": u.key, "access": u.access}
            conf = "EXTRACTED"
            if mode == "parent":
                conf, extra["resolved_as"], extra["matched_key"] = "INFERRED", "parent", d.key
            if len(target) > 1:
                conf, extra["ambiguous"] = "INFERRED", len(target)
            out.edge(src, node_of[(d.key, d.rel)], "reads_config", conf, sf, u.line, "config_key", **extra)
    _REPORT["config_unresolved_uses"] = unresolved


# ── translations ─────────────────────────────────────────────────────────────


def _model_translations(proj: _Project, regs: list[keys.Registration], facts: _Facts, blade: _Blade) -> None:
    idx, files, out = proj.idx, proj.files, proj.out
    index = keys.TransIndex()

    locations: list[tuple[str, str | None]] = [(d, None) for d in LANG_DIRS]
    seen_dirs: set[str] = set(LANG_DIRS)
    for reg in sorted(regs, key=lambda r: (r.rel, r.line)):
        if reg.kind == "lang" and reg.path not in seen_dirs:
            seen_dirs.add(reg.path)
            locations.append((reg.path, reg.key))
    for directory, namespace in locations:
        grouped: dict[tuple[str, str], PhpFile] = {}
        for rel in sorted(r for r in idx.php_files if r.startswith(directory + "/")):
            parsed = keys.split_lang_file(directory, rel)
            if parsed is None:
                continue
            f = proj.load(rel)
            if f is not None:
                grouped[parsed] = f
        if grouped:
            index.add_php(namespace, grouped)
            for f in grouped.values():
                proj.drop(f.rel)
        if namespace is None:
            per_locale: dict[str, tuple[str, dict[str, int]]] = {}
            try:
                names = sorted(os.listdir(proj.root / directory))
            except OSError:
                names = []
            for name in names:
                path = proj.root / directory / name
                if not name.endswith(".json") or not path.is_file():
                    continue
                try:
                    parsed_json = keys.json_keys(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    continue
                if parsed_json:
                    per_locale[name[: -len(".json")]] = (f"{directory}/{name}", parsed_json)
            if per_locale:
                index.add_json(per_locale)

    # Resolve the usages first: a key gets a node only when some usage reads it. A project's lang
    # files hold tens of thousands of strings (+40% nodes on a typical app); the used ones are
    # the part of the graph that connects to code. `translation_keys` / `_unused` count the rest.
    resolved: list[tuple[keys.KeyUse, keys.TransKey]] = []
    missing: dict[str, set[str]] = {}
    unresolved = 0
    for u in [*facts.trans_uses, *blade.trans]:
        if files.file_node(u.rel) is None:
            continue
        tk = index.resolve(u.key)
        if tk is None:
            unresolved += 1
            if index.is_missing(u.key):
                missing.setdefault(u.rel, set()).add(u.key)
            continue
        resolved.append((u, tk))
    used = {(tk.kind, tk.key): tk for _u, tk in resolved}

    node_of: dict[tuple[str, str], str] = {}
    for (kind, key), tk in sorted(used.items()):
        nid = keys.stable_id("lang", key, salt=kind + (tk.namespace or ""))
        node_of[(kind, key)] = nid
        fnode = files.file_node(tk.rel)
        sf = fnode["source_file"] if fnode is not None else proj.source_file_for(tk.rel)
        label = tk.key if len(tk.key) <= 80 else tk.key[:77] + "..."
        meta: dict = {"kind": keys.TRANSLATION_KEY, "key": tk.key, "source": tk.kind, "locales": tk.locales}
        if tk.missing_locales:
            meta["missing_locales"] = tk.missing_locales
        if tk.namespace:
            meta["namespace"] = tk.namespace
        if out.node({
            "id": nid, "label": f"lang:{label}", "file_type": "code",
            "source_file": sf, "source_location": f"L{tk.line}", "metadata": meta,
        }) and fnode is not None:
            out.edge(fnode["id"], nid, "contains", "EXTRACTED", sf, tk.line, "translation_key")
    _REPORT["translation_keys_defined"] = len(index.json) + len(index.php)
    _REPORT["translation_keys_used"] = len(used)

    for u, tk in resolved:
        fnode = files.file_node(u.rel)
        if fnode is None:
            continue
        out.edge(
            u.owner or fnode["id"], node_of[(tk.kind, tk.key)], "uses_translation", "EXTRACTED",
            fnode["source_file"], u.line, "translation_key", key=u.key,
        )
    for rel, ks in missing.items():
        fnode = files.file_node(rel)
        if fnode is not None:
            ordered = sorted(ks)
            out.meta_updates[fnode["id"]] = {
                "missing_translation_key_count": len(ordered),
                "missing_translation_keys": ordered[:50],
            }
    _REPORT["translation_unresolved_uses"] = unresolved
    _REPORT["translation_missing_keys"] = sum(len(v) for v in missing.values())


def _apply_meta(proj: _Project, all_nodes: list[dict]) -> None:
    updates = proj.out.meta_updates
    if not updates:
        return
    for i, n in enumerate(all_nodes):
        extra = updates.get(n.get("id", ""))
        if extra is not None:
            meta = n.get("metadata")
            merged = dict(meta) if isinstance(meta, dict) else {}
            merged.update(extra)
            all_nodes[i] = {**n, "metadata": merged}


def resolve_laravel_authz_keys(per_file: list, all_nodes: list[dict], all_edges: list[dict]) -> None:
    from graphify.extractors import laravel

    root = laravel._scan_root
    if root is None or not laravel.is_laravel_project(root):
        return
    run(root, per_file, all_nodes, all_edges)


# Runs after the route and web resolvers: it reads the route nodes they emit.
AUTHZ_KEYS_RESOLVER = LanguageResolver(
    "laravel_authz_keys", frozenset({".php"}), resolve_laravel_authz_keys
)
