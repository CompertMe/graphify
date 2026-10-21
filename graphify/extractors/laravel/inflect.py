"""English pluralisation and snake-casing as Laravel's ``Str::plural`` / ``Str::snake`` do them.

Eloquent derives a model's table from its class name: ``Str::snake(class_basename)``
pluralised (``BranchManager`` -> ``branch_managers``). Laravel pluralises with Doctrine's
inflector; this is a port of its rule order for the endings that occur in table names,
not the whole rule set. Where English is irregular beyond the lists below the result is
wrong, and the caller marks the table edge ``INFERRED`` for exactly that reason: a model
that cares sets ``protected $table``.

Limits: only the last word is pluralised (as in Laravel), uncountable and irregular words
are the lists below, and a name the rules cannot place falls through to "+s".
"""
from __future__ import annotations

import re

_UNCOUNTABLE = frozenset({
    "audio", "bison", "cattle", "chassis", "compensation", "coreopsis", "data", "deer", "education",
    "emoji", "equipment", "evidence", "feedback", "firmware", "fish", "furniture", "gold", "hardware",
    "information", "jedi", "kin", "knowledge", "love", "metadata", "money", "moose", "news",
    "nutrition", "offspring", "plankton", "pokemon", "police", "rain", "recommended", "related",
    "rice", "series", "sheep", "software", "species", "swine", "traffic", "wheat",
})
_IRREGULAR = {
    "atlas": "atlases", "axe": "axes", "beef": "beefs", "brother": "brothers", "cafe": "cafes",
    "chateau": "chateaux", "child": "children", "cookie": "cookies", "corpus": "corpuses",
    "criterion": "criteria", "curriculum": "curricula", "demo": "demos", "domino": "dominoes",
    "echo": "echoes", "foot": "feet", "fungus": "fungi", "ganglion": "ganglions", "gas": "gases",
    "genie": "genies", "genus": "genera", "goose": "geese", "graffito": "graffiti", "hippopotamus": "hippopotami",
    "hoof": "hoofs", "human": "humans", "iris": "irises", "larva": "larvae", "leaf": "leaves",
    "lens": "lenses", "loaf": "loaves", "man": "men", "medium": "media", "memorandum": "memoranda",
    "money": "monies", "mongoose": "mongooses", "mouse": "mice", "move": "moves", "mythos": "mythoi",
    "niche": "niches", "nucleus": "nuclei", "numen": "numina", "occiput": "occiputs", "octopus": "octopuses",
    "opus": "opuses", "ox": "oxen", "passerby": "passersby", "penis": "penises", "person": "people",
    "plateau": "plateaux", "runner-up": "runners-up", "safe": "safes", "sex": "sexes", "sieve": "sieves",
    "soliloquy": "soliloquies", "son-in-law": "sons-in-law", "syllabus": "syllabi", "testis": "testes",
    "thief": "thieves", "tooth": "teeth", "tornado": "tornadoes", "trilby": "trilbys", "turf": "turfs",
    "valve": "valves", "volcano": "volcanoes", "abuse": "abuses", "avalanche": "avalanches",
    "cache": "caches",
}
# Doctrine's plural rules, in the order it tries them (first match wins).
_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(quiz)$"), r"\1zes"),
    (re.compile(r"(?i)^(oxen)$"), r"\1"),
    (re.compile(r"(?i)^(ox)$"), r"\1en"),
    (re.compile(r"(?i)([ml])ice$"), r"\1ice"),
    (re.compile(r"(?i)([ml])ouse$"), r"\1ice"),
    (re.compile(r"(?i)(matr|vert|ind)(?:ix|ex)$"), r"\1ices"),
    (re.compile(r"(?i)(x|ch|ss|sh)$"), r"\1es"),
    (re.compile(r"(?i)([^aeiouy]|qu)y$"), r"\1ies"),
    (re.compile(r"(?i)(hive)$"), r"\1s"),
    (re.compile(r"(?i)(?:([^f])fe|([lr])f)$"), r"\1\2ves"),
    (re.compile(r"(?i)sis$"), "ses"),
    (re.compile(r"(?i)([ti])a$"), r"\1a"),
    (re.compile(r"(?i)([ti])um$"), r"\1a"),
    (re.compile(r"(?i)(buffal|tomat|volcan)o$"), r"\1oes"),
    (re.compile(r"(?i)(bu)s$"), r"\1ses"),
    (re.compile(r"(?i)(alias|status)$"), r"\1es"),
    (re.compile(r"(?i)(octop|vir)i$"), r"\1i"),
    (re.compile(r"(?i)(octop|vir)us$"), r"\1i"),
    (re.compile(r"(?i)^(ax|test)is$"), r"\1es"),
    (re.compile(r"(?i)s$"), "s"),
    (re.compile(r"$"), "s"),
]


def plural(word: str) -> str:
    """``word`` pluralised; only its last ``_`` / camel-case word changes."""
    if not word:
        return word
    head, sep, last = word.rpartition("_")
    low = last.lower()
    if low in _UNCOUNTABLE:
        return word
    if low in _IRREGULAR:
        return f"{head}{sep}{_IRREGULAR[low]}"
    for pattern, repl in _RULES:
        if pattern.search(last):
            return f"{head}{sep}{pattern.sub(repl, last, count=1)}"
    return word


def snake(name: str) -> str:
    """``BranchManager`` -> ``branch_manager`` (``Str::snake``: ``HTTPLog`` -> ``h_t_t_p_log``)."""
    if not name.islower():
        name = re.sub(r"(.)(?=[A-Z])", r"\1_", re.sub(r"\s+", "", name)).lower()
    return name


def table_for_class(basename: str) -> str:
    """The table Eloquent derives for a model class: ``Str::plural(Str::snake($basename))``."""
    return plural(snake(basename))
