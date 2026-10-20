"""Laravel project detection.

Route modelling only runs for a Laravel project, so a PHP corpus that merely
contains a ``Route::get()`` call (Slim, Lumen-like micro frameworks, a docs
snippet) is left byte-for-byte as the PHP extractor produced it. The scan root
is the project root: ``artisan`` beside the root, or ``composer.json`` requiring
``laravel/framework``.
"""
from __future__ import annotations

import json
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
