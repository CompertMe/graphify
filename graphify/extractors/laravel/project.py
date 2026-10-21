"""Laravel project detection.

Route modelling only runs for a Laravel project, so a PHP corpus that merely
contains a ``Route::get()`` call (Slim, Lumen-like micro frameworks, a docs
snippet) is left byte-for-byte as the PHP extractor produced it. The scan root
is the project root: ``artisan`` beside the root, or ``composer.json`` requiring
``laravel/framework``.
"""
from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

_FRAMEWORK = "laravel/framework"


def is_laravel_project(root: Path) -> bool:
    try:
        if (root / "artisan").is_file():
            return True
        composer = root / "composer.json"
        if not composer.is_file():
            return False
        data = json.loads(composer.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    for section in ("require", "require-dev"):
        deps = data.get(section)
        if isinstance(deps, dict) and _FRAMEWORK in deps:
            return True
    return False


# The files the Laravel passes read: PHP (route, config, migration, class and Blade files) plus the
# JS/Vue/TS sources Ziggy's ``route('x')`` is scanned in (the ``WEB_RESOLVER`` suffix set).
SOURCE_SUFFIXES = frozenset({".php", ".vue", ".js", ".jsx", ".ts", ".tsx", ".mjs"})


def widen_incremental_targets(
    root: Path,
    targets: list[Path],
    code_files: list[Path],
    touched: Sequence[Path | str],
    skip: set[Path] | None = None,
) -> list[Path]:
    """Files an incremental (changed-files) rebuild of ``root`` must re-extract.

    The Laravel passes link files through names that live elsewhere: ``route('x')`` in a Vue file
    to the route named ``x`` in a route file, ``Schedule::command('sig')`` to the command class that
    declares ``sig``, a model to the migration that creates its table, ``config('a.b')`` to the config
    file. Each edge belongs to one file, but is decided by the other: when the route file changes the
    unchanged Vue file's edge is neither dropped (it is stale) nor created (it is new), and a pass only
    emits for the files it was given. Rather than guess which unchanged files depend on a changed one,
    a change to any file the passes read re-extracts every such file of the project (the AST cache makes
    the unchanged ones cheap), so the Laravel layer is recomputed exactly as a full build computes it.

    ``touched`` holds the changed *and* deleted paths. Anything else (a project that is not Laravel, a
    change that touches no PHP/JS/Vue/TS file) returns ``targets`` unchanged.
    """
    if not any(Path(str(t)).suffix.lower() in SOURCE_SUFFIXES for t in touched):
        return targets
    if not is_laravel_project(root):
        return targets
    keep = {Path(os.path.abspath(p)) for p in targets}
    skipped = {Path(os.path.abspath(p)) for p in skip or ()}
    widened: list[Path] = []
    for p in code_files:
        ap = Path(os.path.abspath(p))
        if ap in skipped:
            continue
        if ap in keep or ap.suffix.lower() in SOURCE_SUFFIXES:
            widened.append(ap)
    return widened
