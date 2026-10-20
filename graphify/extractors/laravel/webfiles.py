"""The corpus files and symbols the web-side passes attach edges to.

``SymbolIndex`` only knows PHP. The Inertia and Ziggy passes also need a Vue/JS
page's file node, the node of the JS function around a ``route()`` call, and the
file node of a Blade view. Everything is read from the nodes the extractors
already produced, so the corpus boundary (``.graphifyignore``, detect) stays the
one the rest of the pipeline uses: a file that was not extracted is not there.
"""
from __future__ import annotations

import posixpath

from graphify.extractors.laravel.symbols import SymbolIndex, _loc_line

JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue")


class FileIndex:
    def __init__(self, idx: SymbolIndex, all_nodes: list[dict]):
        self.idx = idx
        self.files: dict[str, dict] = {}
        # (rel, declaration line) -> nodes declared there
        self._by_line: dict[tuple[str, int], list[dict]] = {}
        for n in all_nodes:
            sf = n.get("source_file")
            if not n.get("id") or not sf:
                continue
            rel = idx.rel(sf)
            label = str(n.get("label", ""))
            line = _loc_line(n.get("source_location"))
            if line is None and n.get("source_location") in (None, ""):
                line = 1
            if line is None:
                continue
            if line == 1 and _is_file_label(rel, label):
                self.files.setdefault(rel, n)
                continue
            self._by_line.setdefault((rel, line), []).append(n)

    def file_node(self, rel: str) -> dict | None:
        return self.files.get(rel)

    def js_files(self) -> list[str]:
        return sorted(r for r in self.files if r.lower().endswith(JS_SUFFIXES))

    def symbol_at(self, rel: str, lines: list[int], names: list[str]) -> dict | None:
        """The single node in ``rel`` declared on one of ``lines`` whose label is one of
        ``names`` (``foo`` or ``foo()``). None when there is no match or several."""
        want = set()
        for name in names:
            want.update({name, f"{name}()"})
        found: dict[str, dict] = {}
        for line in lines:
            for n in self._by_line.get((rel, line), []):
                if str(n.get("label", "")) in want:
                    found[n["id"]] = n
        return next(iter(found.values())) if len(found) == 1 else None


def _is_file_label(rel: str, label: str) -> bool:
    if not label or "." not in posixpath.basename(label):
        return False
    return rel == label or rel.endswith("/" + label)
