"""Laravel configuration keys and translation keys.

``config/*.php`` and ``lang/**`` are plain ``return [...]`` array files: the PHP
extractor yields only a file node for each. This module reads the arrays and
gives every literal key a node, and reads the call sites (``config('a.b')``,
``__('file.key')``) so ``authzkeys`` can link a usage to the key it names.

Config keys. A file ``config/a/b.php`` contributes the prefix ``a.b``; a file a
service provider registers with ``mergeConfigFrom(path, 'key')`` contributes
``key`` (the path must be a literal this package's path reader understands). Nested
arrays flatten to dotted leaf keys down to ``MAX_DEPTH`` levels (the array at the
cap is a leaf); a list (no string keys) is a leaf, never flattened. ``env('X', d)``
calls inside the value are recorded as metadata (``env`` names, ``env_default``).

Translation keys. ``{lang,resources/lang}/<locale>/<group>.php`` is the key
``<group>.<dotted leaf>``; a provider's ``loadTranslationsFrom(path, 'ns')`` adds
``ns::<group>.<leaf>``; ``<locale>.json`` keys are the strings themselves. One key is
one node however many locales define it (``locales`` / ``missing_locales`` in
metadata). Laravel looks a JSON key up before a group key, so the resolver does too.
"""
from __future__ import annotations

import hashlib
import posixpath
import re
from dataclasses import dataclass, field

from graphify.extractors.base import _make_id
from graphify.extractors.laravel.authz import call_name, is_facade, iter_all_calls
from graphify.extractors.laravel.phpsites import Owners
from graphify.extractors.laravel.phpsrc import (
    PhpFile,
    args_of,
    array_items,
    line_of,
    named,
    path_value,
    positional,
    str_value,
)

MAX_DEPTH = 4
LANG_MAX_DEPTH = 8
CONFIG_KEY = "laravel_config_key"
TRANSLATION_KEY = "laravel_translation_key"

_KEY_OK = re.compile(r"^[\w\-]+(?:\.[\w\-:]+)*$")
_GROUP_KEY = re.compile(r"^(?:[\w\-]+::)?[\w\-/]+(?:\.[\w\-]+)+$")
_CONFIG_GET = frozenset({"get", "has", "string", "integer", "float", "boolean", "array", "collection"})
_Q = r"""(?:'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)")"""
# A helper call, not a method (`->trans(`), static (`::config(`) or part of a longer name.
_NOT_MEMBER = r"(?<![\w$\\])(?<!->)(?<!::)"
_BLADE_CONFIG = re.compile(_NOT_MEMBER + r"config\(\s*" + _Q)
_BLADE_TRANS = re.compile(
    r"@lang\(\s*" + _Q + "|" + _NOT_MEMBER + r"(?:__|trans|trans_choice)\(\s*" + _Q
    + r"|\bLang::(?:get|choice|has)\(\s*" + _Q
)


def stable_id(kind: str, text: str, salt: str = "") -> str:
    """Deterministic, collision-free node id: readable slug + hash of the exact text."""
    digest = hashlib.sha1(f"{salt}\0{text}".encode("utf-8")).hexdigest()[:8]
    return f"{_make_id(kind, text)[:72].rstrip('_')}_{digest}"


# ── array files ──────────────────────────────────────────────────────────────


@dataclass
class KeyDef:
    key: str
    line: int
    env: list[str] = field(default_factory=list)
    env_default: str | None = None


def returned_array(f: PhpFile):
    """The array literal of the file's top-level ``return [...]``."""
    for stmt in f.root.children:
        if stmt.type == "return_statement":
            inner = [c for c in stmt.children if c.is_named]
            if len(inner) == 1 and inner[0].type == "array_creation_expression":
                return inner[0]
    return None


def _env_calls(f: PhpFile, node) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == "function_call_expression" and call_name(f, n) == "env":
            pos = positional(args_of(n))
            name = str_value(f, pos[0].node) if pos else None
            if name:
                default = None
                if len(pos) > 1:
                    d = pos[1].node
                    default = str_value(f, d)
                    if default is None and d is not None and d.type in ("integer", "float", "boolean", "null"):
                        default = f.text(d)
                out.append((name, default))
        stack.extend(reversed(n.children))
    return out


def flatten(f: PhpFile, array, prefix: str, max_depth: int, with_env: bool) -> tuple[list[KeyDef], set[str]]:
    """Dotted leaf keys of ``array`` plus the dotted branch prefixes seen on the way."""
    leaves: list[KeyDef] = []
    branches: set[str] = set()

    def walk(node, pre: str, depth: int) -> None:
        items = array_items(node)
        if items is None:
            return
        for k, v in items:
            if k is None:
                continue
            ks = str_value(f, k)
            if ks is None or ks == "" or "\n" in ks:
                continue  # int index, constant or computed key: not a literal name
            key = f"{pre}.{ks}" if pre else ks
            sub = array_items(v)  # None unless v is an array literal
            if sub and depth < max_depth and any(
                kk is not None and str_value(f, kk) is not None for kk, _vv in sub
            ):
                branches.add(key)
                walk(v, key, depth + 1)
                continue
            kd = KeyDef(key, line_of(k))
            if with_env and v is not None:
                envs = _env_calls(f, v)
                kd.env = [n for n, _d in envs]
                kd.env_default = envs[0][1] if envs else None
            leaves.append(kd)

    walk(array, prefix, 1)
    return leaves, branches


# ── registered locations ─────────────────────────────────────────────────────


@dataclass
class Registration:
    kind: str      # "config" | "lang"
    path: str      # root-relative file (config) or directory (lang)
    key: str       # config prefix / translation namespace
    rel: str
    line: int


def scan_registrations(f: PhpFile) -> list[Registration]:
    out: list[Registration] = []
    for call in iter_all_calls(f.root):
        name = call_name(f, call)
        if name not in ("mergeConfigFrom", "loadTranslationsFrom") or call.type != "member_call_expression":
            continue
        obj = call.child_by_field_name("object")
        if obj is None or obj.type != "variable_name" or f.text(obj) != "$this":
            continue
        pos = positional(args_of(call))
        if len(pos) < 2:
            continue
        path, key = path_value(f, pos[0].node), str_value(f, pos[1].node)
        if not path or not key or not _KEY_OK.match(key.replace("::", ".")):
            continue
        out.append(Registration(
            "config" if name == "mergeConfigFrom" else "lang", path, key, f.rel, line_of(call),
        ))
    return out


# ── usages ───────────────────────────────────────────────────────────────────


@dataclass
class KeyUse:
    rel: str
    line: int
    owner: str | None
    key: str
    access: str = "get"


def _first_arg(f: PhpFile, call):
    args = args_of(call)
    arg = named(args, "key")
    if arg is not None:
        return arg.node
    pos = positional(args)
    return pos[0].node if pos else None


def _is_config_receiver(f: PhpFile, obj) -> bool:
    """``config()`` or ``app('config')`` (the receiver of ``->get('a.b')``)."""
    if obj is None or obj.type != "function_call_expression":
        return False
    fn = call_name(f, obj)
    pos = positional(args_of(obj))
    if fn == "config":
        return not pos
    return fn == "app" and len(pos) == 1 and str_value(f, pos[0].node) == "config"


def scan_config_php(f: PhpFile, owners: Owners) -> list[KeyUse]:
    out: list[KeyUse] = []
    for call in iter_all_calls(f.root):
        name = call_name(f, call)
        if name is None:
            continue
        access = None
        if call.type == "function_call_expression":
            if name == "config":
                access = "any"
        elif call.type == "scoped_call_expression":
            if is_facade(f, call.child_by_field_name("scope"), "Config"):
                access = "set" if name == "set" else ("get" if name in _CONFIG_GET else None)
        elif _is_config_receiver(f, call.child_by_field_name("object")):
            access = "set" if name == "set" else ("get" if name in _CONFIG_GET else None)
        if access is None:
            continue
        first = _first_arg(f, call)
        if first is None:
            continue
        key = str_value(f, first)
        keys: list[tuple[str, str]] = []
        if key is not None:
            keys.append((key, "get" if access == "any" else access))
        else:
            items = array_items(first)
            if items is not None and access in ("any", "set"):
                for k, _v in items:
                    ks = str_value(f, k) if k is not None else None
                    if ks is not None:
                        keys.append((ks, "set"))
        for k, acc in keys:
            if _KEY_OK.match(k):
                out.append(KeyUse(f.rel, line_of(call), owners.of(f, call), k, acc))
    return out


def scan_trans_php(f: PhpFile, owners: Owners) -> list[KeyUse]:
    out: list[KeyUse] = []
    for call in iter_all_calls(f.root):
        name = call_name(f, call)
        if name is None:
            continue
        if call.type == "function_call_expression":
            if name not in ("__", "trans", "trans_choice"):
                continue
        elif call.type == "scoped_call_expression":
            if name not in ("get", "choice", "has", "trans", "transChoice") or not is_facade(
                f, call.child_by_field_name("scope"), "Lang"
            ):
                continue
        else:
            continue
        key = str_value(f, _first_arg(f, call))
        if key:
            out.append(KeyUse(f.rel, line_of(call), owners.of(f, call), key))
    return out


def _unq(single: str | None, double: str | None) -> str:
    if single is not None:
        return single.replace("\\'", "'").replace("\\\\", "\\")
    return (double or "").replace('\\"', '"').replace("\\\\", "\\")


def scan_blade_config(rel: str, cleaned: str) -> list[KeyUse]:
    if "config(" not in cleaned:
        return []
    out = []
    for m in _BLADE_CONFIG.finditer(cleaned):
        key = _unq(m.group(1), m.group(2))
        if _KEY_OK.match(key):
            out.append(KeyUse(rel, cleaned.count("\n", 0, m.start()) + 1, None, key))
    return out


def scan_blade_trans(rel: str, cleaned: str) -> list[KeyUse]:
    if "__(" not in cleaned and "trans" not in cleaned and "@lang" not in cleaned and "Lang::" not in cleaned:
        return []
    out = []
    for m in _BLADE_TRANS.finditer(cleaned):
        key = ""
        for i in (1, 3, 5):  # one (single, double) quote pair per alternative
            if m.group(i) is not None or m.group(i + 1) is not None:
                key = _unq(m.group(i), m.group(i + 1))
                break
        if key:
            out.append(KeyUse(rel, cleaned.count("\n", 0, m.start()) + 1, None, key))
    return out


# ── indexes ──────────────────────────────────────────────────────────────────


@dataclass
class ConfigKey:
    key: str
    rel: str
    line: int
    env: list[str]
    env_default: str | None


class ConfigIndex:
    def __init__(self) -> None:
        self.leaves: dict[str, list[ConfigKey]] = {}
        self.branches: dict[str, set[str]] = {}   # branch prefix -> files that define leaves under it
        self.file_prefix: dict[str, str] = {}

    def add_file(self, f: PhpFile, prefix: str) -> None:
        array = returned_array(f)
        if array is None:
            return
        self.file_prefix[f.rel] = prefix
        leaves, branches = flatten(f, array, prefix, MAX_DEPTH, with_env=True)
        for kd in leaves:
            self.leaves.setdefault(kd.key, []).append(ConfigKey(kd.key, f.rel, kd.line, kd.env, kd.env_default))
        for b in branches | {prefix}:
            self.branches.setdefault(b, set()).add(f.rel)

    def resolve(self, key: str) -> tuple[str, list[ConfigKey]] | tuple[str, str] | None:
        """``("exact", [ConfigKey])`` / ``("parent", [ConfigKey])`` (the nearest ancestor that is
        a leaf, e.g. ``config('app.providers.0')`` -> ``app.providers``) / ``("file", rel)`` (the key
        names a branch spread over exactly one file) / None."""
        hit = self.leaves.get(key)
        if hit:
            return "exact", hit
        parts = key.split(".")
        for i in range(len(parts) - 1, 0, -1):
            parent = ".".join(parts[:i])
            if parent in self.leaves:
                return "parent", self.leaves[parent]
        files = self.branches.get(key)
        if files and len(files) == 1:
            return "file", next(iter(files))
        return None


@dataclass
class TransKey:
    key: str
    kind: str                     # "php" | "json"
    rel: str                      # primary locale file
    line: int
    locales: list[str]
    missing_locales: list[str]
    group: str | None
    namespace: str | None


def split_lang_file(directory: str, rel: str) -> tuple[str, str] | None:
    """``(locale, group)`` of ``rel`` under lang directory ``directory``, for a PHP array file."""
    prefix = directory.rstrip("/") + "/" if directory else ""
    if not rel.startswith(prefix) or not rel.lower().endswith(".php"):
        return None
    inner = rel[len(prefix):][: -len(".php")]
    parts = inner.split("/")
    if len(parts) < 2 or parts[0] == "vendor":
        return None
    return parts[0], "/".join(parts[1:])


class TransIndex:
    def __init__(self) -> None:
        self.php: dict[str, TransKey] = {}
        self.json: dict[str, TransKey] = {}
        self.branches: set[str] = set()

    def all_keys(self) -> list[TransKey]:
        return [*self.json.values(), *self.php.values()]

    def add_php(self, namespace: str | None, files: dict[tuple[str, str], PhpFile]) -> None:
        """``files``: ``{(locale, group): PhpFile}`` of one lang location."""
        ns = f"{namespace}::" if namespace else ""
        by_key: dict[str, dict[str, tuple[str, int]]] = {}
        all_locales: set[str] = set()
        for (locale, group), f in sorted(files.items()):
            array = returned_array(f)
            if array is None:
                continue
            all_locales.add(locale)
            leaves, branches = flatten(f, array, f"{ns}{group}", LANG_MAX_DEPTH, with_env=False)
            self.branches.add(f"{ns}{group}")
            self.branches.update(branches)
            for kd in leaves:
                by_key.setdefault(kd.key, {}).setdefault(locale, (f.rel, kd.line))
        for key, per_locale in by_key.items():
            if key in self.php:
                continue
            locales = sorted(per_locale)
            primary = "en" if "en" in per_locale else locales[0]
            rel, line = per_locale[primary]
            group = key[len(ns):].split(".", 1)[0]
            self.php[key] = TransKey(
                key, "php", rel, line, locales, sorted(all_locales - set(locales)), group, namespace,
            )

    def add_json(self, per_locale: dict[str, tuple[str, dict[str, int]]]) -> None:
        """``{locale: (rel, {key: line})}``."""
        all_locales = set(per_locale)
        by_key: dict[str, dict[str, tuple[str, int]]] = {}
        for locale, (rel, keys) in sorted(per_locale.items()):
            for k, line in keys.items():
                by_key.setdefault(k, {})[locale] = (rel, line)
        for key, locs in by_key.items():
            locales = sorted(locs)
            primary = "en" if "en" in locs else locales[0]
            rel, line = locs[primary]
            self.json.setdefault(
                key, TransKey(key, "json", rel, line, locales, sorted(all_locales - set(locales)), None, None)
            )

    def resolve(self, key: str) -> TransKey | None:
        """JSON keys first (Laravel's lookup order), then ``group.key``."""
        return self.json.get(key) or self.php.get(key)

    def is_missing(self, key: str) -> bool:
        """A ``group.key``-shaped name no lang file defines (free text is just a default-locale string)."""
        return self.resolve(key) is None and key not in self.branches and bool(_GROUP_KEY.match(key))


_JSON_KEY_LINE = re.compile(r'^\s*("(?:[^"\\]|\\.)*")\s*:')


def json_keys(text: str) -> dict[str, int] | None:
    """String-valued top-level keys of a flat JSON lang file with the line each starts on."""
    import json

    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    lines_of: dict[str, int] = {}
    for i, ln in enumerate(text.splitlines(), 1):
        m = _JSON_KEY_LINE.match(ln)
        if m:
            try:
                lines_of.setdefault(json.loads(m.group(1)), i)
            except ValueError:
                continue
    return {k: lines_of.get(k, 1) for k, v in data.items() if isinstance(v, str)}
