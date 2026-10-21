"""Laravel authorization facts read from PHP and Blade source.

Everything here is a *scan*: it turns one parsed file into small records (gate
definitions, policy registrations, ability checks) and never touches the graph.
``authzkeys`` resolves the records against the corpus and emits nodes/edges.

Rules shared by every reader below:

* only literal strings (and ``Enum::CASE->value`` / ``Enum::CASE`` of an in-corpus
  string enum, whose value is a literal in the enum file) name an ability;
  anything computed is skipped, never guessed;
* a facade (``Gate``, ``Config``, ``Lang``) counts only when the name written in
  the file resolves, with the one PHP name-resolution rule set
  (``PhpFile.class_fqn``), to the Laravel facade or the global alias, so a
  project class that happens to be called ``Gate`` or ``Config`` is left alone;
* a model class is "statically known" only from ``X::class``, ``new X`` or a
  typed parameter of the enclosing function.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from graphify.extractors.laravel.phpsites import Owners
from graphify.extractors.laravel.phpsrc import (
    CLOSURE_TYPES,
    PhpFile,
    args_of,
    array_items,
    class_const_raw,
    class_ref,
    line_of,
    named,
    positional,
    str_value,
)

FACADE_NS = "Illuminate\\Support\\Facades\\"
_ALL_CALLS = frozenset({
    "function_call_expression", "scoped_call_expression",
    "member_call_expression", "nullsafe_member_call_expression",
})
_FUNCTIONS = CLOSURE_TYPES | {"method_declaration", "function_definition"}
_BUILTIN_TYPES = frozenset({
    "array", "string", "int", "float", "bool", "mixed", "callable", "iterable", "object",
    "self", "static", "parent", "null", "false", "true", "void", "never", "closure",
})
_GATE_CHECKS = frozenset({"allows", "denies", "check", "authorize", "inspect"})
_GATE_MANY = frozenset({"any", "none"})
_USER_CHECKS = frozenset({"can", "cannot", "cant", "canany", "cannotany"})
_ROUTE_ROOTS = frozenset({"route", "router", "$router", "$route"})
# Laravel's Gate::resource default ability -> policy method map.
RESOURCE_ABILITIES = {
    "viewAny": "viewAny", "view": "view", "create": "create", "update": "update",
    "delete": "delete", "restore": "restore", "forceDelete": "forceDelete",
}


def iter_all_calls(root) -> Iterator:
    """Every call expression under ``root`` (nullsafe ``?->`` included), in source order."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in _ALL_CALLS:
            yield node
        stack.extend(reversed(node.children))


def call_name(f: PhpFile, call) -> str | None:
    """The called method / function name as written (not lower-cased)."""
    if call.type == "function_call_expression":
        fn = call.child_by_field_name("function")
        if fn is None or fn.type not in ("name", "qualified_name"):
            return None
        return f.text(fn).rsplit("\\", 1)[-1].strip()
    name = call.child_by_field_name("name")
    return f.text(name) if name is not None else None


def is_facade(f: PhpFile, scope, name: str) -> bool:
    """``scope`` (the ``Gate`` of ``Gate::allows``) is Laravel's ``name`` facade."""
    if scope is None or scope.type not in ("name", "qualified_name"):
        return False
    fqn = f.class_fqn(f.text(scope).strip())
    return fqn is not None and fqn.lstrip("\\") in (name, FACADE_NS + name)


def chain_root(call):
    """The expression a ``a()->b()->c()`` chain starts from."""
    cur = call
    while cur is not None:
        if cur.type in ("member_call_expression", "nullsafe_member_call_expression"):
            cur = cur.child_by_field_name("object")
        elif cur.type == "member_access_expression":
            cur = cur.child_by_field_name("object")
        else:
            break
    return cur


def _is_route_chain(f: PhpFile, call) -> bool:
    root = chain_root(call)
    if root is None:
        return False
    if root.type == "scoped_call_expression":
        scope = root.child_by_field_name("scope")
        return scope is not None and f.text(scope).rsplit("\\", 1)[-1].strip().lower() in _ROUTE_ROOTS
    if root.type == "variable_name":
        return f.text(root).lower() in _ROUTE_ROOTS
    return False


# ── enums ───────────────────────────────────────────────────────────────────


@dataclass
class EnumCase:
    fqn: str
    name: str
    value: str | None
    rel: str
    line: int


@dataclass
class EnumIndex:
    """String-backed in-corpus enums: ``fqn.lower() -> {case name -> EnumCase}``."""

    by_fqn: dict[str, dict[str, EnumCase]] = field(default_factory=dict)

    def add_file(self, f: PhpFile) -> None:
        if f.ns is None:
            return
        stack = [f.root]
        while stack:
            node = stack.pop()
            if node.type == "enum_declaration":
                self._add_enum(f, node)
                continue
            stack.extend(reversed(node.children))

    def _add_enum(self, f: PhpFile, node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = f.text(name_node)
        fqn = f"{f.ns}\\{name}" if f.ns else name
        cases: dict[str, EnumCase] = {}
        for case in _walk_type(node, "enum_case"):
            cname = case.child_by_field_name("name")
            if cname is None:
                continue
            value = str_value(f, case.child_by_field_name("value"))
            cases[f.text(cname)] = EnumCase(fqn, f.text(cname), value, f.rel, line_of(case))
        self.by_fqn[fqn.lower()] = cases

    def case(self, fqn: str, name: str) -> EnumCase | None:
        return self.by_fqn.get(fqn.lower(), {}).get(name)

    def values(self) -> dict[str, list[EnumCase]]:
        """``{string value: [cases]}`` over every string-backed case."""
        out: dict[str, list[EnumCase]] = {}
        for fqn in sorted(self.by_fqn):
            for c in self.by_fqn[fqn].values():
                if c.value is not None:
                    out.setdefault(c.value, []).append(c)
        return out


def _walk_type(node, kind: str) -> Iterator:
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == kind:
            yield n
            continue
        stack.extend(reversed(n.children))


def ability_values(f: PhpFile, node, enums: EnumIndex) -> list[str] | None:
    """The ability string(s) a node spells: a literal, ``Enum::CASE->value`` or ``Enum::CASE``
    of an in-corpus string enum, or an array of those. None when any part is dynamic."""
    if node is None:
        return None
    s = str_value(f, node)
    if s is not None:
        return [s]
    case_node = node
    if node.type in ("member_access_expression", "nullsafe_member_access_expression"):
        prop = node.child_by_field_name("name")
        if prop is None or f.text(prop) != "value":
            return None
        case_node = node.child_by_field_name("object")
    if case_node is not None and case_node.type == "class_constant_access_expression":
        named_children = [c for c in case_node.children if c.is_named]
        if len(named_children) == 2:
            raw = f.text(named_children[0]).strip()
            fqn = f.class_fqn(raw) if raw and not raw.startswith("$") else None
            case = enums.case(fqn, f.text(named_children[1])) if fqn else None
            if case is not None and case.value is not None:
                return [case.value]
        return None
    items = array_items(node)
    if items is None:
        return None
    out: list[str] = []
    for _k, v in items:
        got = ability_values(f, v, enums)
        if got is None or len(got) != 1:
            return None
        out.append(got[0])
    return out


# ── static model class ───────────────────────────────────────────────────────


def model_fqn(f: PhpFile, node) -> str | None:
    """FQN of the model class an authorization argument statically names."""
    if node is None:
        return None
    if class_const_raw(f, node):
        return class_ref(f, node)
    if node.type == "object_creation_expression":
        cls = next((c for c in node.children if c.type in ("name", "qualified_name")), None)
        return f.class_fqn(f.text(cls).strip()) if cls is not None else None
    if node.type == "variable_name":
        return _typed_variable(f, node)
    return None


def _param_type(f: PhpFile, param) -> str | None:
    for c in param.children:
        if c.type == "optional_type":
            c = next((x for x in c.children if x.is_named), None)
        if c is not None and c.type == "named_type":
            raw = f.text(c).strip()
            if raw and raw.lower() not in _BUILTIN_TYPES:
                return f.class_fqn(raw)
            return None
        if c is not None and c.type in ("union_type", "intersection_type", "primitive_type"):
            return None
    return None


def _typed_variable(f: PhpFile, var) -> str | None:
    want = f.text(var)
    cur = var.parent
    while cur is not None:
        if cur.type in _FUNCTIONS:
            params = cur.child_by_field_name("parameters")
            for p in (params.children if params is not None else []):
                if p.type not in ("simple_parameter", "property_promotion_parameter"):
                    continue
                pv = next((c for c in p.children if c.type == "variable_name"), None)
                if pv is not None and f.text(pv) == want:
                    return _param_type(f, p)
        cur = cur.parent
    return None


# ── records ──────────────────────────────────────────────────────────────────


@dataclass
class GateDef:
    name: str
    rel: str
    line: int
    handler_class: str | None = None     # FQN of [C::class,'m'] / 'C@m' / C::class
    handler_method: str | None = None
    closure_line: int | None = None
    via: str = "Gate::define"


@dataclass
class PolicyReg:
    model: str
    policy: str
    rel: str
    line: int
    via: str


@dataclass
class AbilityUse:
    rel: str
    line: int
    owner: str | None
    names: list[str]
    model: str | None
    form: str
    extra_args: bool = False


@dataclass
class AuthzFacts:
    defs: list[GateDef] = field(default_factory=list)
    resources: list[tuple[str, str, list | None, str, int]] = field(default_factory=list)
    policies: list[PolicyReg] = field(default_factory=list)
    uses: list[AbilityUse] = field(default_factory=list)


def scan_php(f: PhpFile, owners: Owners, enums: EnumIndex, want_uses: bool) -> AuthzFacts:
    facts = AuthzFacts()
    _scan_policy_properties(f, facts)
    for call in iter_all_calls(f.root):
        name = call_name(f, call)
        if name is None:
            continue
        if call.type == "scoped_call_expression":
            scope = call.child_by_field_name("scope")
            if scope is not None and is_facade(f, scope, "Gate"):
                _scan_gate_static(f, call, name, owners, enums, facts, want_uses)
            continue
        if not want_uses or call.type == "function_call_expression":
            continue
        low = name.lower()
        obj = call.child_by_field_name("object")
        if low == "authorize":
            if obj is not None and obj.type == "variable_name" and f.text(obj) == "$this":
                _add_use(f, call, owners, enums, facts, "authorize", many=False)
            elif (
                obj is not None and obj.type == "scoped_call_expression"
                and call_name(f, obj) == "forUser"
                and is_facade(f, obj.child_by_field_name("scope"), "Gate")
            ):
                _add_use(f, call, owners, enums, facts, "Gate", many=False)
        elif low in _GATE_CHECKS or low in _GATE_MANY:
            if (
                obj is not None and obj.type == "scoped_call_expression"
                and call_name(f, obj) == "forUser"
                and is_facade(f, obj.child_by_field_name("scope"), "Gate")
            ):
                _add_use(f, call, owners, enums, facts, "Gate", many=low in _GATE_MANY)
        elif low in _USER_CHECKS and not _is_route_chain(f, call):
            _add_use(f, call, owners, enums, facts, "user", many=low.endswith("any"))
        elif low == "middleware" and not _is_route_chain(f, call):
            _scan_middleware_call(f, call, owners, facts)
    return facts


def _scan_gate_static(f, call, name, owners, enums, facts: AuthzFacts, want_uses: bool) -> None:
    low = name.lower()
    args = args_of(call)
    pos = positional(args)
    if low == "define" and len(pos) >= 2:
        names = ability_values(f, pos[0].node, enums)
        if names is not None and len(names) == 1:
            facts.defs.append(_gate_def(f, call, names[0], pos[1].node))
    elif low == "policy" and len(pos) >= 2:
        model, policy = class_ref(f, pos[0].node), class_ref(f, pos[1].node)
        if model and policy:
            facts.policies.append(PolicyReg(model, policy, f.rel, line_of(call), "Gate::policy"))
    elif low == "resource" and len(pos) >= 2:
        res, policy = str_value(f, pos[0].node), class_ref(f, pos[1].node)
        extra = array_items(pos[2].node) if len(pos) > 2 else None
        if res and policy:
            mapping: list | None = None
            if len(pos) > 2:
                mapping = []
                for k, v in extra or []:
                    ks, vs = str_value(f, k) if k is not None else None, str_value(f, v)
                    if ks is None or vs is None:
                        mapping = None
                        break
                    mapping.append((ks, vs))
                if extra is None:
                    mapping = None
                    policy = None
            if policy:
                facts.resources.append((res, policy, mapping, f.rel, line_of(call)))
    elif want_uses and (low in _GATE_CHECKS or low in _GATE_MANY):
        _add_use(f, call, owners, enums, facts, "Gate", many=low in _GATE_MANY)


def _gate_def(f: PhpFile, call, name: str, handler) -> GateDef:
    gd = GateDef(name, f.rel, line_of(call))
    if handler is None:
        return gd
    if handler.type in CLOSURE_TYPES:
        gd.closure_line = line_of(handler)
        return gd
    cls = class_ref(f, handler)
    if cls:
        gd.handler_class, gd.handler_method = cls, "__invoke"
        return gd
    items = array_items(handler)
    if items is not None and len(items) == 2:
        c, m = class_ref(f, items[0][1]), str_value(f, items[1][1])
        if c and m:
            gd.handler_class, gd.handler_method = c, m
        return gd
    s = str_value(f, handler)
    if s and "@" in s:
        c, m = s.split("@", 1)
        if c.strip("\\") and m:
            gd.handler_class, gd.handler_method = c.strip("\\"), m
    return gd


def _add_use(f: PhpFile, call, owners: Owners, enums: EnumIndex, facts: AuthzFacts, form: str, many: bool) -> None:
    args = args_of(call)
    pos = positional(args)
    arg = named(args, "ability")
    first = arg.node if arg is not None else (pos[0].node if pos else None)
    names = ability_values(f, first, enums)
    if not names:
        return
    if not many and len(names) != 1:
        return
    rest = pos[1:] if arg is None else pos
    model = model_fqn(f, rest[0].node) if rest else None
    facts.uses.append(AbilityUse(
        f.rel, line_of(call), owners.of(f, call), names, model, form, extra_args=bool(rest),
    ))


def _scan_middleware_call(f: PhpFile, call, owners: Owners, facts: AuthzFacts) -> None:
    """``$this->middleware('can:x')`` in a controller constructor (route chains are
    modelled by the route pass and skipped by the caller)."""
    names: list[str] = []
    models: list[str | None] = []
    for a in positional(args_of(call)):
        vals = [str_value(f, a.node)]
        items = array_items(a.node)
        if items is not None:
            vals = [str_value(f, v) for _k, v in items]
        for v in vals:
            if v and v.startswith("can:"):
                head = v[4:].split(",")
                if head[0].strip():
                    names.append(head[0].strip())
                    models.append(head[1].strip() if len(head) > 1 else None)
    for name, model in zip(names, models):
        fq = model.lstrip("\\") if model and "\\" in model else None
        facts.uses.append(AbilityUse(
            f.rel, line_of(call), owners.of(f, call), [name], fq, "middleware", extra_args=model is not None,
        ))


def _scan_policy_properties(f: PhpFile, facts: AuthzFacts) -> None:
    stack = [f.root]
    while stack:
        node = stack.pop()
        if node.type == "property_element":
            var = next((c for c in node.children if c.type == "variable_name"), None)
            if var is not None and f.text(var) == "$policies":
                value = next((c for c in node.children if c.type == "array_creation_expression"), None)
                for k, v in array_items(value) or []:
                    model = class_ref(f, k) if k is not None else None
                    policy = class_ref(f, v)
                    if model and policy:
                        facts.policies.append(PolicyReg(model, policy, f.rel, line_of(node), "$policies"))
            continue
        if node.type == "class_declaration":
            _scan_use_policy(f, node, facts)
        stack.extend(reversed(node.children))


def _scan_use_policy(f: PhpFile, cls, facts: AuthzFacts) -> None:
    name_node = cls.child_by_field_name("name")
    if name_node is None or f.ns is None:
        return
    model = f"{f.ns}\\{f.text(name_node)}" if f.ns else f.text(name_node)
    for attr in _walk_type(cls, "attribute"):
        group = attr.parent.parent if attr.parent is not None else None  # attribute_group > attribute_list
        if group is None or group.parent is None or group.parent.id != cls.id:
            continue
        an = next((c for c in attr.children if c.type in ("name", "qualified_name")), None)
        if an is None:
            continue
        fqn = f.class_fqn(f.text(an).strip())
        if fqn is None or fqn.rsplit("\\", 1)[-1] != "UsePolicy":
            continue
        arg_list = next((c for c in attr.children if c.type == "arguments"), None)
        first = next((c for c in (arg_list.children if arg_list is not None else []) if c.type == "argument"), None)
        value = next((c for c in first.children if c.is_named), None) if first is not None else None
        policy = class_ref(f, value) if value is not None else None
        if policy:
            facts.policies.append(PolicyReg(model, policy, f.rel, line_of(attr), "UsePolicy"))


# ── policy discovery ─────────────────────────────────────────────────────────


def guess_policy_names(model: str) -> list[str]:
    """Laravel's ``Gate::guessPolicyName``: candidates for ``App\\Models\\Contact`` in the
    order the framework tries them (the deepest ``...\\Policies\\ContactPolicy`` first)."""
    model = model.lstrip("\\")
    parts = model.split("\\")
    base, segments = parts[-1], parts[:-1]
    out = []
    for i in range(len(segments), 0, -1):
        out.append("\\".join(segments[:i]) + "\\Policies\\" + base + "Policy")
    if not segments:
        out.append("Policies\\" + base + "Policy")
    return out


# ── Blade ────────────────────────────────────────────────────────────────────

_BLADE_COMMENT = re.compile(r"\{\{--.*?--\}\}", re.S)
_Q = r"""(?:'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)")"""
_BLADE_CAN = re.compile(r"@(?:else)?(?:can|cannot)\s*\(\s*" + _Q + r"(?:\s*,\s*([^)\n]*))?", re.I)
_BLADE_CANANY = re.compile(r"@(?:else)?can(?:not)?any\s*\(\s*\[([^\]]*)\]", re.I)
_BLADE_EXPR = re.compile(
    r"(?:->|::)\s*(?:can|cannot|cant|allows|denies|check|authorize)\s*\(\s*" + _Q
    + r"(?:\s*,\s*([^)\n]*))?",
)
_STR = re.compile(_Q)
_FQ_CLASS = re.compile(r"^\\?([A-Za-z_]\w*(?:\\[A-Za-z_]\w*)+)::class$")


def _unquote(single: str | None, double: str | None) -> str:
    if single is not None:
        return single.replace("\\'", "'").replace("\\\\", "\\")
    return (double or "").replace('\\"', '"').replace("\\\\", "\\")


def blank_blade_comments(text: str) -> str:
    return _BLADE_COMMENT.sub(lambda m: re.sub(r"[^\r\n]", " ", m.group(0)), text)


def scan_blade(rel: str, text: str) -> list[AbilityUse]:
    """``@can('x')``, ``@cannot``, ``@canany(['a','b'])`` and ``->can('x')`` / ``Gate::allows('x')``
    written inside Blade. A model argument counts only when written as a qualified
    ``Fq\\Name::class`` (Blade has no ``use`` context to resolve a short name with)."""
    if "can" not in text and "Gate" not in text and "allows" not in text:
        return []
    cleaned = blank_blade_comments(text)
    out: list[AbilityUse] = []
    for rx, form in ((_BLADE_CAN, "@can"), (_BLADE_EXPR, "blade_expr")):
        for m in rx.finditer(cleaned):
            rest = (m.group(3) or "").strip()
            fq = _FQ_CLASS.match(rest.split(",")[0].strip()) if rest else None
            line = cleaned.count("\n", 0, m.start()) + 1
            out.append(AbilityUse(
                rel, line, None, [_unquote(m.group(1), m.group(2))], fq.group(1) if fq else None, form,
                extra_args=bool(rest),
            ))
    for m in _BLADE_CANANY.finditer(cleaned):
        names = [_unquote(a, b) for a, b in _STR.findall(m.group(1))]
        if names:
            out.append(AbilityUse(rel, cleaned.count("\n", 0, m.start()) + 1, None, names, None, "@canany"))
    return out
