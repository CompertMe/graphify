"""Artisan commands, the scheduler and ``Artisan::call`` (see ``runtime``).

Command names come from the class itself: ``$signature`` (the part before the first
argument / option), ``$name`` or ``#[AsCommand(name: ...)]``; ``Artisan::command('sig', fn)``
declares a closure command, modelled as a ``laravel_command`` node whose ``handled_by``
edge points at the closure. The name index maps a command name to the one node that
declares it; a name two commands declare, or no in-corpus command declares (a vendor
command), resolves to nothing.

Edges:

* ``schedules``: a schedule-entry node (``kind: laravel_schedule``, one per
  ``Schedule::command|job|call`` statement, carrying the frequency chain and the cron
  expression it implies) -> the command class / closure command / job class / closure.
* ``invokes_command``: the calling method or closure -> the command, for
  ``Artisan::call|queue('sig')`` and ``$this->call|callSilent('sig' | X::class)``.

Both are ``EXTRACTED``: the statement names the command by a literal. Not modelled: a
command name built at run time, ``Schedule::exec``, ``Artisan::call(X::class)``, commands
registered by a package, ``$aliases``.
"""
from __future__ import annotations

import re
from pathlib import Path

from graphify.extractors.base import _file_stem, _make_id
from graphify.extractors.laravel.phpread import (
    arg_node,
    attributes,
    call_args,
    call_name,
    chain_after,
    class_arg_fqn,
    class_decls,
    enclosing_callable,
    facade_of,
    iter_calls_in,
    new_class_fqn,
    param_types,
    property_value_node,
    string_class_fqn,
)
from graphify.extractors.laravel.phpsrc import CLOSURE_TYPES, PhpFile, class_ref, line_of, str_value
from graphify.extractors.laravel.runtime import Runtime

COMMAND_KIND = "laravel_command"
SCHEDULE_KIND = "laravel_schedule"
_SCHEDULE_CLASS = "illuminate\\console\\scheduling\\schedule"
_ASCOMMAND = "illuminate\\console\\attributes\\ascommand"
_ASCOMMAND_SYMFONY = "symfony\\component\\console\\attribute\\ascommand"
_SCHEDULE_METHODS = frozenset({"command", "job", "call"})
_ARTISAN_CALLS = frozenset({"call", "queue"})
_THIS_CALLS = frozenset({"call", "callsilent", "callsilently"})
_WANT_COMMAND = re.compile(rb"\$signature|AsCommand|Artisan::command")
_WANT_SITES = re.compile(rb"(?i)schedule|Artisan::|->call\(|->callSilent")

# frequency method -> (minute, hour, day of month, month, day of week); None keeps the field
_FIXED = {
    "everyminute": ("*", None, None, None, None),
    "everytwominutes": ("*/2", None, None, None, None),
    "everythreeminutes": ("*/3", None, None, None, None),
    "everyfourminutes": ("*/4", None, None, None, None),
    "everyfiveminutes": ("*/5", None, None, None, None),
    "everytenminutes": ("*/10", None, None, None, None),
    "everyfifteenminutes": ("*/15", None, None, None, None),
    "everythirtyminutes": ("0,30", None, None, None, None),
    "hourly": ("0", None, None, None, None),
    "everytwohours": ("0", "*/2", None, None, None),
    "everythreehours": ("0", "*/3", None, None, None),
    "everyfourhours": ("0", "*/4", None, None, None),
    "everysixhours": ("0", "*/6", None, None, None),
    "daily": ("0", "0", None, None, None),
    "weekly": ("0", "0", None, None, "0"),
    "monthly": ("0", "0", "1", None, None),
    "quarterly": ("0", "0", "1", "1-12/3", None),
    "yearly": ("0", "0", "1", "1", None),
    "weekdays": (None, None, None, None, "1-5"),
    "weekends": (None, None, None, None, "0,6"),
    "sundays": (None, None, None, None, "0"),
    "mondays": (None, None, None, None, "1"),
    "tuesdays": (None, None, None, None, "2"),
    "wednesdays": (None, None, None, None, "3"),
    "thursdays": (None, None, None, None, "4"),
    "fridays": (None, None, None, None, "5"),
    "saturdays": (None, None, None, None, "6"),
}
_TIMING_ARGS = frozenset({"cron", "hourlyat", "dailyat", "at", "twicedaily", "twicedailyat", "weeklyon", "monthlyon"})
_CONSTRAINTS = frozenset({"when", "skip", "between", "unlessbetween", "environments"})
_OPTIONS = frozenset({
    "withoutoverlapping", "ononeserver", "runinbackground", "eveninmaintenancemode",
    "appendoutputto", "sendoutputto", "emailoutputto", "emailoutputonfailure", "name", "description",
    "onsuccess", "onfailure", "after", "before", "then", "pingbefore", "thenping", "thenpingif",
    "pingbeforeif", "pingonsuccess", "pingonfailure", "user", "graceperiod", "storeoutput",
    "emailoutputon", "onqueue", "onconnection",
})


def first_token(value: str | None) -> str | None:
    if value is None:
        return None
    parts = value.split()
    return parts[0] if parts else None


class Console:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.names: dict[str, list[str]] = {}

    @staticmethod
    def wants(raw: bytes) -> bool:
        return bool(_WANT_COMMAND.search(raw)) or (b"$name" in raw and b"Command" in raw)

    @staticmethod
    def wants_sites(raw: bytes) -> bool:
        return bool(_WANT_SITES.search(raw))

    # ── pass A: command declarations ───────────────────────────────────────
    def collect(self, f: PhpFile) -> None:
        rt = self.rt
        for decl in class_decls(f):
            if decl.node.type != "class_declaration" or decl.fqn is None:
                continue
            found = self._declared_name(f, decl.node)
            if found is None:
                continue
            raw, name, source = found
            nid = rt.klass(decl.fqn)
            if nid is None or not self._is_command(f, nid, decl.node):
                continue
            self.names.setdefault(name, []).append(nid)
            rt.annotate(nid, command_name=name, command_signature=raw, command_source=source)
        if b"Artisan::command" in f.src:
            self._closure_commands(f)

    def _declared_name(self, f: PhpFile, cls) -> tuple[str, str, str] | None:
        for prop in ("signature", "name"):
            raw = str_value(f, property_value_node(f, cls, prop))
            name = first_token(raw)
            if raw is not None and name:
                return raw, name, prop
        for fqn, args in attributes(f, cls):
            if fqn.lower() in (_ASCOMMAND, _ASCOMMAND_SYMFONY):
                node = next((a.node for a in args if a.name == "name"), None)
                if node is None:
                    node = next((a.node for a in args if a.name is None), None)
                raw = str_value(f, node)
                name = first_token(raw)
                if raw is not None and name:
                    return raw, name, "AsCommand"
        return None

    def _is_command(self, f: PhpFile, nid: str, cls) -> bool:
        if any(fqn.lower() in (_ASCOMMAND, _ASCOMMAND_SYMFONY) for fqn, _a in attributes(f, cls)):
            return True
        facts = self.rt.facts(nid)  # `vendor` already includes the vendor ancestors of in-corpus parents
        return facts is not None and any(v.rsplit("\\", 1)[-1].endswith("command") for v in facts.vendor)

    def _closure_commands(self, f: PhpFile) -> None:
        rt = self.rt
        for call in iter_calls_in(f.root):
            if call.type != "scoped_call_expression" or call_name(f, call) != "command":
                continue
            if facade_of(f, call.child_by_field_name("scope")) != "artisan":
                continue
            raw = str_value(f, arg_node(call, 0))
            name = first_token(raw)
            closure = arg_node(call, 1)
            if not name or closure is None or closure.type not in CLOSURE_TYPES:
                continue
            line = line_of(call)
            description = None
            for link in chain_after(call):
                if call_name(f, link) in ("purpose", "describe"):
                    description = str_value(f, arg_node(link, 0))
            if f.rel in rt.fresh:
                nid = rt.new_id(_file_stem(Path(f.rel)), "artisan_command", name)
                meta = {"kind": COMMAND_KIND, "command_name": name, "command_signature": raw,
                        "command_source": "Artisan::command"}
                if description:
                    meta["description"] = description
                rt.add_node(f, nid, name, line, meta)
                target = rt.idx.closure_at(f.rel, line_of(closure))
                rt.edge(nid, target, "handled_by", "EXTRACTED", f, line, "closure_command")
            else:
                nid = _make_id(_file_stem(Path(f.rel)), "artisan_command", name)
                if nid not in rt.ids:
                    continue
            self.names.setdefault(name, []).append(nid)

    # ── pass B: schedule and Artisan::call ─────────────────────────────────
    def finish(self, files: dict[str, PhpFile]) -> None:
        for rel in sorted(files):
            f = files[rel]
            for call in iter_calls_in(f.root):
                self._site(f, call)

    def _site(self, f: PhpFile, call) -> None:
        name = call_name(f, call)
        if name is None:
            return
        if call.type == "scoped_call_expression":
            facade = facade_of(f, call.child_by_field_name("scope"))
            if facade == "schedule" and name in _SCHEDULE_METHODS:
                self._schedule(f, call, name)
            elif facade == "artisan" and name in _ARTISAN_CALLS:
                self._artisan_call(f, call, queued=name == "queue")
        elif call.type == "member_call_expression":
            obj = call.child_by_field_name("object")
            if obj is None:
                return
            if name in _SCHEDULE_METHODS and self._is_schedule_receiver(f, call, obj):
                self._schedule(f, call, name)
            elif name.lower() in _THIS_CALLS and f.text(obj) == "$this":
                self._artisan_call(f, call, queued=False, allow_class=True)

    def _is_schedule_receiver(self, f: PhpFile, call, obj) -> bool:
        text = f.text(obj).replace(" ", "")
        if obj.type == "variable_name":
            var = text.lstrip("$")
            scope = enclosing_callable(call)
            types = param_types(f, scope).get(var, []) if scope is not None else []
            if any(t.lower() == _SCHEDULE_CLASS for t in types):
                return True
            return var == "schedule"
        if text == "$this->schedule":
            return True
        if obj.type == "function_call_expression" and call_name(f, obj) == "app":
            arg = arg_node(obj, 0)
            return arg is not None and (class_ref(f, arg) or "").lower() == _SCHEDULE_CLASS
        return False

    def _resolve_name(self, name: str) -> tuple[str | None, str | None]:
        """``(node id, unresolved reason)`` for a command name."""
        hits = self.names.get(name, [])
        uniq = list(dict.fromkeys(hits))
        if len(uniq) == 1:
            return uniq[0], None
        return None, "ambiguous" if uniq else "vendor_or_unknown"

    def _schedule(self, f: PhpFile, call, method: str) -> None:
        rt = self.rt
        line = line_of(call)
        first = arg_node(call, 0)
        target: str | None = None
        target_label: str
        target_kind: str
        reason: str | None = None
        written: str | None = None
        if method == "command":
            target_kind = "command"
            lit = str_value(f, first)
            fqn = class_ref(f, first) if first is not None else None
            if lit is not None and first_token(lit):
                written = first_token(lit)
                target_label = written or lit
                target, reason = self._resolve_name(written or "")
            elif fqn:
                written = fqn
                target_label = fqn.rsplit("\\", 1)[-1]
                target = rt.klass(fqn)
                reason = None if target else "vendor_or_unknown"
            else:
                return
        elif method == "job":
            target_kind = "job"
            fqn = class_arg_fqn(f, first) or string_class_fqn(str_value(f, first))
            if not fqn:
                return
            written = fqn
            target_label = fqn.rsplit("\\", 1)[-1]
            target = rt.klass(fqn)
            reason = None if target else "vendor_or_unknown"
        else:  # call
            target_kind = "callable"
            target_label = "closure"
            if first is not None and first.type in CLOSURE_TYPES:
                target = rt.idx.closure_at(f.rel, line_of(first))
            elif first is not None and first.type == "object_creation_expression":
                cls =rt.klass(new_class_fqn(f, first))
                target = (rt.idx.lookup_method(cls, "__invoke") or cls) if cls else None
                target_label = (new_class_fqn(f, first) or "invokable").rsplit("\\", 1)[-1]
            else:
                return
            reason = None if target else "unresolved"
        chain = chain_after(call)
        freq, options, cron, cron_source, extras = self._chain_meta(f, chain)
        label_freq = f"cron {cron}" if cron_source == "literal" else (freq[0] if freq else "")
        label =f"schedule {target_label}" + (f" {label_freq}" if label_freq else "")
        meta: dict = {"kind": SCHEDULE_KIND, "target_kind": target_kind, "target": written or target_label}
        if freq:
            meta["frequency"] = freq
        if options:
            meta["options"] = options
        if cron:
            meta["cron"] = cron
            meta["cron_source"] = cron_source
        meta.update(extras)
        if reason:
            meta["unresolved"] = reason
        nid = rt.new_id(_file_stem(Path(f.rel)), "schedule", f"l{line}", written or target_label)
        rt.add_node(f, nid, label, line, meta)
        if target:
            rt.edge(nid, target, "schedules", "EXTRACTED", f, line, "schedule_" + target_kind)

    def _chain_meta(self, f: PhpFile, chain: list) -> tuple[list[str], list[str], str | None, str | None, dict]:
        freq: list[str] = []
        options: list[str] = []
        extras: dict = {}
        fields = ["*", "*", "*", "*", "*"]
        literal_cron: str | None = None
        derivable = True
        timing_seen = False
        for link in chain:
            name = call_name(f, link) or ""
            low = name.lower()
            args = call_args(link)
            shown = ", ".join(f.text(a.node) for a in args if a.node is not None)
            written = f"{name}({shown})" if shown else name
            if low == "cron":
                lit = str_value(f, arg_node(link, 0))
                freq.append(written)
                timing_seen = True
                if lit is not None and len(lit.split()) == 5:
                    literal_cron = lit
                else:
                    derivable = False
            elif low in _FIXED:
                freq.append(written)
                timing_seen = True
                for i, v in enumerate(_FIXED[low]):
                    if v is not None:
                        fields[i] = v
            elif low in _TIMING_ARGS:
                freq.append(written)
                timing_seen = True
                if not self._apply_timing(f, low, link, fields):
                    derivable = False
            elif low == "timezone":
                tz = str_value(f, arg_node(link, 0))
                if tz:
                    extras["timezone"] = tz
                options.append(name)
            elif low in _CONSTRAINTS:
                options.append(name)
                extras["conditional"] = True
            elif low in _OPTIONS:
                options.append(name)
            else:
                options.append(name)
                derivable = False
        if literal_cron is not None and len(freq) == 1:
            return freq, options, literal_cron, "literal", extras
        if timing_seen and derivable and literal_cron is None:
            return freq, options, " ".join(fields), "derived", extras
        return freq, options, None, None, extras

    @staticmethod
    def _apply_timing(f: PhpFile, low: str, link, fields: list[str]) -> bool:
        def num(node) -> str | None:
            if node is not None and node.type == "integer":
                return f.text(node)
            return None

        def hhmm(node) -> tuple[str, str] | None:
            lit = str_value(f, node)
            m = re.fullmatch(r"(\d{1,2}):(\d{2})", lit or "")
            return (str(int(m.group(2))), str(int(m.group(1)))) if m else None

        if low == "hourlyat":
            minute = num(arg_node(link, 0))
            if minute is None:
                return False
            fields[0] = minute
            return True
        if low in ("dailyat", "at"):
            t = hhmm(arg_node(link, 0))
            if t is None:
                return False
            fields[0], fields[1] = t
            return True
        if low == "twicedaily":
            a, b = num(arg_node(link, 0)), num(arg_node(link, 1))
            if a is None or b is None:
                return False
            fields[0], fields[1] = "0", f"{a},{b}"
            return True
        if low == "twicedailyat":
            a, b, m = num(arg_node(link, 0)), num(arg_node(link, 1)), num(arg_node(link, 2))
            if a is None or b is None or m is None:
                return False
            fields[0], fields[1] = m, f"{a},{b}"
            return True
        if low == "weeklyon":
            dow, t = num(arg_node(link, 0)), hhmm(arg_node(link, 1))
            if dow is None:
                return False
            fields[2:5] = [fields[2], fields[3], dow]
            fields[0], fields[1] = t if t else ("0", "0")
            return True
        if low == "monthlyon":
            dom, t = num(arg_node(link, 0)), hhmm(arg_node(link, 1))
            if dom is None:
                return False
            fields[2] = dom
            fields[0], fields[1] = t if t else ("0", "0")
            return True
        return False

    def _artisan_call(self, f: PhpFile, call, *, queued: bool, allow_class: bool = False) -> None:
        rt = self.rt
        first = arg_node(call, 0)
        target: str | None = None
        written: str | None = None
        lit = str_value(f, first)
        if lit is not None and first_token(lit):
            written = first_token(lit)
            target, _why = self._resolve_name(written or "")
        elif allow_class and first is not None:
            fqn = class_ref(f, first)
            if fqn:
                written = fqn
                cls = rt.klass(fqn)
                if cls and any(cls in v for v in self.names.values()):
                    target = cls
        if target is None:
            return
        src = rt.owner(f, call)
        extra: dict = {"command": written}
        if queued:
            extra["queued"] = True
        rt.edge(src, target, "invokes_command", "EXTRACTED", f, line_of(call), "artisan_call", **extra)


__all__ = ["COMMAND_KIND", "SCHEDULE_KIND", "Console", "first_token"]
