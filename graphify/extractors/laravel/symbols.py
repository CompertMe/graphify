"""Read-only index over the PHP nodes/edges the extractor already produced.

Lets the Laravel passes turn a resolved FQN into a class node, a method name into
the method node declared by the class or an in-corpus ancestor/trait, and a
closure's line into its closure node. Nothing here guesses: an FQN that matches
zero or several declarations, or a method that only a vendor ancestor could
declare, resolves to None and the caller emits no edge.
"""
from __future__ import annotations

import posixpath
import re
from pathlib import Path

from graphify.extractors.laravel.phpsrc import get_parser
from graphify.extractors.resolution import _php_file_namespace

_PHP_SUFFIXES = (".php",)
# The PHP extractor names a closure `{closure#N}()` or, as a routing-call
# argument, `VERB /path()`.
_CLOSURE_LABEL = re.compile(r"^(?:\{closure#\d+\}|[A-Z]+ \S*)\(\)$")


# Each Laravel pass builds its own SymbolIndex over the same nodes: what a node's path or a file's
# namespace turns into is computed once per run, not once per pass.
_REL_MEMO: dict[tuple[str, str], str] = {}
_NS_MEMO: dict[tuple[str, int, int], str | None] = {}


def clear_memos() -> None:
    _REL_MEMO.clear()
    _NS_MEMO.clear()


def _relativize(root: Path, source_file: str) -> str:
    p = Path(source_file)
    out = p.as_posix()
    if p.is_absolute():
        try:
            out = p.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            out = p.as_posix()
    return out


def _file_namespace(parser, path: Path) -> str | None:
    """``_php_file_namespace`` memoized on the file's identity (path hash, size, mtime)."""
    try:
        st = path.stat()
        key = (str(path), st.st_size, st.st_mtime_ns)
    except OSError:
        return _php_file_namespace(parser, path)
    if key not in _NS_MEMO:
        _NS_MEMO[key] = _php_file_namespace(parser, path)
    return _NS_MEMO[key]


def _loc_line(loc) -> int | None:
    if isinstance(loc, str) and loc[:1] == "L" and loc[1:].isdigit():
        return int(loc[1:])
    return None


class SymbolIndex:
    def __init__(self, root: Path, all_nodes: list[dict], all_edges: list[dict]):
        self.root = root
        self._rel_cache: dict[str, str] = {}
        self._ns_cache: dict[str, str | None] = {}
        self.nodes: dict[str, dict] = {}
        self.file_nodes: dict[str, dict] = {}
        self.php_files: set[str] = set()
        self._classes: dict[str, list[dict]] = {}
        self._closures: dict[tuple[str, int], list[str]] = {}
        self._methods: dict[str, dict[str, str]] = {}
        self._supers: dict[str, list[str]] = {}

        for n in all_nodes:
            nid = n.get("id")
            sf = n.get("source_file")
            if not nid or not sf:
                continue
            self.nodes.setdefault(nid, n)
            rel = self.rel(sf)
            if not rel.lower().endswith(_PHP_SUFFIXES) or rel.lower().endswith(".blade.php"):
                continue
            self.php_files.add(rel)
            label = str(n.get("label", ""))
            if label == posixpath.basename(rel) and n.get("source_location") in ("L1", None, ""):
                self.file_nodes.setdefault(rel, n)
            elif n.get("_callable_class"):
                self._classes.setdefault(label.lower(), []).append(n)
            elif _CLOSURE_LABEL.match(label):
                line = _loc_line(n.get("source_location"))
                if line is not None:
                    self._closures.setdefault((rel, line), []).append(nid)

        super_pairs: dict[str, list[tuple[int, str]]] = {}
        for e in all_edges:
            rel_type = e.get("relation")
            src, tgt = e.get("source"), e.get("target")
            if rel_type == "method":
                if not src or not tgt:
                    continue
                target = self.nodes.get(tgt)
                if target is None:
                    continue
                name = str(target.get("label", "")).lstrip(".").removesuffix("()").lower()
                self._methods.setdefault(src, {}).setdefault(name, tgt)
            elif rel_type in ("mixes_in", "inherits"):
                if src and tgt:
                    super_pairs.setdefault(src, []).append((0 if rel_type == "mixes_in" else 1, tgt))
        for src, pairs in super_pairs.items():
            # Traits first, then the parent class: PHP lookup order (stable sort
            # keeps the declaration order inside each group).
            self._supers[src] = [t for _k, t in sorted(pairs, key=lambda p: p[0])]

    def rel(self, source_file: str) -> str:
        cached = self._rel_cache.get(source_file)
        if cached is not None:
            return cached
        key = (str(self.root), source_file)
        out = _REL_MEMO.get(key)
        if out is None:
            out = _relativize(self.root, source_file)
            _REL_MEMO[key] = out
        self._rel_cache[source_file] = out
        return out

    def file_node(self, rel: str) -> dict | None:
        return self.file_nodes.get(rel)

    def _namespace(self, rel: str) -> str | None:
        if rel not in self._ns_cache:
            parser = get_parser()
            self._ns_cache[rel] = (
                _file_namespace(parser, self.root / rel) if parser is not None else None
            )
        return self._ns_cache[rel]

    def resolve_class(self, fqn: str | None) -> str | None:
        """Node id of the one in-corpus class/trait/enum declaring ``fqn``."""
        if not fqn:
            return None
        fqn = fqn.lstrip("\\")
        found: dict[str, dict] = {}
        for node in self._classes.get(fqn.rsplit("\\", 1)[-1].lower(), []):
            rel = self.rel(node["source_file"])
            ns = self._namespace(rel)
            if ns is None:
                continue  # several namespaces in one file: never guess
            label = str(node.get("label", ""))
            declared = f"{ns}\\{label}" if ns else label
            if declared.lower() == fqn.lower():
                found.setdefault(node["id"], node)
        return next(iter(found)) if len(found) == 1 else None

    def lookup_method(self, class_nid: str, name: str) -> str | None:
        """Method node declared by the class, one of its traits, or an in-corpus ancestor."""
        want = name.lower()
        seen: set[str] = set()
        stack = [class_nid]
        while stack:
            cur = stack.pop(0)
            if cur in seen:
                continue
            seen.add(cur)
            hit = self._methods.get(cur, {}).get(want)
            if hit:
                return hit
            stack = [s for s in self._supers.get(cur, []) if s in self.nodes] + stack
        return None

    def closure_at(self, rel: str, line: int) -> str | None:
        ids = self._closures.get((rel, line), [])
        return ids[0] if len(ids) == 1 else None
