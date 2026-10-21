"""Project options for Laravel view resolution, read from ``<root>/.graphifyrc``.

Some view locations are decided at runtime and cannot be read from source, the
typical case being a theme system that calls
``View::replaceNamespace("theme-{$area}", $theme->viewsPath())`` for whichever
theme is active. The project states that mapping itself::

    # namespace -> directories (root-relative, comma separated, first match wins)
    laravel.view_namespace.theme-app=resources/themes/app/default/resources/views
    laravel.view_namespace.theme-guest=resources/themes/guest/default/resources/views
    # anonymous component directories: <x-name> (no prefix) and <x-prefix::name>
    laravel.component_path=resources/themes/app/default/resources/views/components
    laravel.component_path.layout=resources/themes/app/default/resources/views/components/layout

An edge that resolves through one of these carries ``via: "graphifyrc"`` and is
``INFERRED``: the mapping is the project's assumption, not a fact of the source.
Nothing is read here when the file is absent, and a line that is not a
``laravel.*`` key is ignored (the rest of the file belongs to ``graphify.rcfile``).
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from pathlib import Path

_NS = "laravel.view_namespace."
_COMP = "laravel.component_path"


@dataclass
class LaravelViewOptions:
    view_namespaces: dict[str, list[str]] = field(default_factory=dict)
    component_paths: list[tuple[str, str | None]] = field(default_factory=list)  # (dir, prefix)


def _dirs(value: str) -> list[str]:
    out: list[str] = []
    for part in value.split(","):
        part = part.strip().replace("\\", "/")
        if not part or part.startswith("/"):
            continue  # a root-relative directory is required
        norm = posixpath.normpath(part)
        if norm == ".." or norm.startswith("../"):
            continue
        if norm != "." and norm not in out:
            out.append(norm)
    return out


def load_view_options(root: Path) -> LaravelViewOptions:
    opts = LaravelViewOptions()
    try:
        text = (Path(root) / ".graphifyrc").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return opts
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = (s.strip() for s in line.split("=", 1))
        if key.startswith(_NS):
            ns = key[len(_NS):].strip()
            dirs = _dirs(val)
            if ns and dirs:
                opts.view_namespaces[ns] = dirs
        elif key == _COMP or key.startswith(_COMP + "."):
            prefix = key[len(_COMP) + 1:].strip() or None
            for d in _dirs(val):
                opts.component_paths.append((d, prefix))
    return opts
