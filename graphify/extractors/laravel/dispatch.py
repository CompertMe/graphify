"""Jobs, events, listeners, notifications, mail and observers (see ``runtime``).

Edges (source is the calling method / closure, or the registering class):

* ``dispatches``: caller -> job class, for ``X::dispatch*()``, ``dispatch(new X)``,
  ``dispatch_sync``, ``Bus::dispatch|chain|batch``, ``->chain([...])``, ``Queue::push``.
  The ``dispatch`` of ``X::dispatch()`` comes from a vendor trait the corpus cannot resolve,
  so the class is judged from source: it (or an in-corpus ancestor / trait) lists
  ``ShouldQueue`` / ``Queueable`` / the Bus ``Dispatchable``. A class that merely sits
  under ``Jobs/`` and calls a ``dispatch`` it does not define is ``INFERRED``.
* ``dispatches_event``: caller -> event class, for ``event(new E)``, ``broadcast``,
  ``Event::dispatch(new E)`` and ``E::dispatch()`` on a class using the Events
  ``Dispatchable`` trait.
* ``listens_to``: listener (class, or closure) -> event, from ``Event::listen``,
  ``$listen`` and ``events()`` arrays of a service provider (``JobPipeline::make([...])``
  makes each job a listener), ``subscribe(Dispatcher $events)`` subscribers and
  ``#[ListensTo]`` (``EXTRACTED``); and from the ``handle(Event $e)`` type hint of a class
  under ``app/Listeners`` that no registration covers (``INFERRED``, Laravel's event
  auto-discovery). A vendor event is the external stub node of the imported class.
* ``notifies``: ``->notify(new N)`` / ``Notification::send($to, new N)``.
  ``sends_mail``: ``Mail::...->send|queue|later(new M)``.
* ``observes``: observer -> model, from ``Model::observe(O::class)`` and
  ``#[ObservedBy([O::class])]``.

Not modelled: string event names, ``dispatch($variable)``, jobs built by a factory,
``$dispatchesEvents`` model events, listeners found by ``withEvents(discover: [...])``
paths other than ``app/Listeners``, ``Event::listen`` with a wildcard.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from graphify.extractors.laravel.phpread import (
    arg_node,
    array_values,
    attributes,
    call_args,
    call_name,
    chain_root,
    class_arg_fqn,
    class_decls,
    class_ref_in_scope,
    enclosing_callable,
    enclosing_type,
    facade_of,
    iter_calls_in,
    methods_of,
    name_node_text,
    new_class_fqn,
    param_types,
    positional_args,
    property_value_node,
    string_class_fqn,
    type_refs,
)
from graphify.extractors.laravel.phpsrc import (
    CLOSURE_TYPES,
    PhpFile,
    array_items,
    class_const_raw,
    line_of,
    load_php_file,
    str_value,
)
from graphify.extractors.laravel.runtime import Runtime

_WANT = re.compile(
    rb"dispatch|event\(|broadcast\(|Event::|->chain\(|withChain|Bus::|JobPipeline|->notify|Notification::|"
    rb"Mail::|observe\(|ObservedBy|ListensTo|\$listen\b|\$subscribe\b|function\s+(?:events|listens|subscribe)\b|Queue::"
)
_BUS_DISPATCHABLE = "illuminate\\foundation\\bus\\dispatchable"
_EVENT_DISPATCHABLE = "illuminate\\foundation\\events\\dispatchable"
_JOB_MARKERS = frozenset({
    "illuminate\\contracts\\queue\\shouldqueue", "illuminate\\contracts\\queue\\shouldbeunique",
    "illuminate\\bus\\queueable", "illuminate\\foundation\\queue\\queueable", _BUS_DISPATCHABLE,
})
_STATIC_DISPATCH = frozenset({
    "dispatch", "dispatchsync", "dispatchnow", "dispatchafterresponse", "dispatchif", "dispatchunless", "withchain",
})
_OBJ_DISPATCH = frozenset({"dispatch", "dispatchsync", "dispatchnow", "dispatchafterresponse"})
_HELPER_DISPATCH = frozenset({"dispatch", "dispatch_sync", "dispatch_now"})
_EVENT_FACADE_DISPATCH = frozenset({"dispatch", "until", "dispatchnow"})
_QUEUE_PUSH = frozenset({"push", "pushon", "later", "lateron", "bulk"})
_MAIL_SEND = frozenset({"send", "queue", "later", "sendnow"})
_KNOWN_FACADES = frozenset({"bus", "event", "queue", "notification", "mail"})
_DISPATCHER = frozenset({"illuminate\\events\\dispatcher", "illuminate\\contracts\\events\\dispatcher"})
_OBSERVED_BY = "illuminate\\database\\eloquent\\attributes\\observedby"
_LISTENS_TO = {"illuminate\\events\\attributes\\listensto", "illuminate\\contracts\\events\\listensto"}
_DISCOVERY_DIR = "app/Listeners/"


@dataclass
class Spec:
    """One listener as written: a class (and method), a closure, or the jobs of a pipeline."""

    fqn: str | None = None
    method: str | None = None
    closure: object | None = None
    jobs: list[str] = field(default_factory=list)
    node: object | None = None


def _jobs_dir(rel: str) -> bool:
    return "/Jobs/" in "/" + rel


class Dispatch:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.registered: set[tuple[str, str]] = set()
        self.discovered: list[tuple[PhpFile, str, str, str, int]] = []
        self.subscribe_regs: set[str] = set()   # FQNs named by `$subscribe` / `Event::subscribe`
        self.subscribed: set[str | None] = set()  # subscriber classes already read

    @staticmethod
    def wants(rel: str, raw: bytes) -> bool:
        return bool(_WANT.search(raw)) or rel.startswith(_DISCOVERY_DIR)

    # ── per file ───────────────────────────────────────────────────────────
    def scan(self, f: PhpFile) -> None:
        for decl in class_decls(f):
            if decl.node.type == "class_declaration":
                self._class(f, decl)
        for call in iter_calls_in(f.root):
            self._call(f, call)

    def finish(self) -> None:
        rt = self.rt
        for fqn in sorted(self.subscribe_regs):
            self._registered_subscriber(fqn)
        for f, listener, event, method, line in self.discovered:
            if (listener, event) in self.registered:
                continue
            rt.edge(listener, event, "listens_to", "INFERRED", f, line, "listener_discovery",
                    method=method, via="auto-discovery")

    def _registered_subscriber(self, fqn: str) -> None:
        """A class a provider names as a subscriber: read its ``subscribe()`` even when its
        file was not scanned for this run or its parameter is untyped."""
        rt = self.rt
        nid = rt.klass(fqn)
        node = rt.idx.nodes.get(nid) if nid else None
        if fqn in self.subscribed or node is None:
            return
        rel = rt.idx.rel(node["source_file"])
        f = load_php_file(rt.root / rel, rel)
        if f is None:
            return
        for decl in class_decls(f):
            if decl.fqn == fqn:
                sub = methods_of(f, decl.node).get("subscribe")
                if sub is not None:
                    self._subscriber(f, decl, sub, registered=True)

    # ── class level: attributes, provider arrays, subscribers, discovery ───
    def _class(self, f: PhpFile, decl) -> None:
        rt = self.rt
        cls = decl.node
        nid = rt.klass(decl.fqn) if decl.fqn else None
        for fqn, args in attributes(f, cls):
            low = fqn.lower()
            node = args[0].node if args else None
            if low == _OBSERVED_BY and nid:
                for obs in self._class_list(f, node):
                    o = rt.klass(obs)
                    rt.edge(o, nid, "observes", "EXTRACTED", f, line_of(cls), "observed_by_attribute")
            elif low in _LISTENS_TO and nid:
                for ev in self._class_list(f, node):
                    rt.edge(nid, rt.target(ev), "listens_to", "EXTRACTED", f, line_of(cls), "listens_to_attribute")
                    self._remember(nid, rt.target(ev))
        refs = type_refs(f, cls)
        if any(r.rsplit("\\", 1)[-1].endswith("ServiceProvider") for r in refs) or decl.name.endswith("ServiceProvider"):
            listen = property_value_node(f, cls, "listen")
            if listen is not None:
                self._event_map(f, listen, "$listen")
            methods = methods_of(f, cls)
            for name in ("events", "listens"):
                m = methods.get(name)
                if m is not None:
                    ret = self._returned_array(m)
                    if ret is not None:
                        self._event_map(f, ret, f"{name}()")
        subscribe = property_value_node(f, cls, "subscribe")
        if subscribe is not None:
            self.subscribe_regs.update(self._class_list(f, subscribe))
        sub = methods_of(f, cls).get("subscribe")
        if sub is not None and nid:
            self._subscriber(f, decl, sub)
        if f.rel.startswith(_DISCOVERY_DIR) and nid:
            self._discover(f, decl, nid)

    @staticmethod
    def _returned_array(method):
        body = method.child_by_field_name("body")
        for stmt in (body.children if body is not None else []):
            if stmt.type == "return_statement":
                expr = next((c for c in stmt.children if c.is_named), None)
                if expr is not None and expr.type == "array_creation_expression":
                    return expr
        return None

    def _class_list(self, f: PhpFile, node) -> list[str]:
        values = array_values(node)
        if values is None:
            values = [node] if node is not None else []
        out = []
        for v in values:
            fqn = class_arg_fqn(f, v) or string_class_fqn(str_value(f, v))
            if fqn:
                out.append(fqn)
        return out

    def _remember(self, listener: str | None, event: str | None) -> None:
        if listener and event:
            self.registered.add((listener, event))

    # ── listener maps ──────────────────────────────────────────────────────
    def _event_map(self, f: PhpFile, array_node, context: str) -> None:
        """``[Event::class => [Listener::class, [L::class, 'm'], JobPipeline::make([...])...]]``."""
        items = array_items(array_node)
        for key, value in items or []:
            raw = class_const_raw(f, key) if key is not None else None
            event = f.class_fqn(raw) if raw else None
            if not event:
                continue
            values = array_values(value)
            nodes = values if values is not None and not self._is_pair(f, value) else [value]
            for n in nodes:
                self._register(f, [event], self._spec(f, n), line_of(n), context)

    def _is_pair(self, f: PhpFile, node) -> bool:
        """``[L::class, 'method']`` is one listener, not a list of two."""
        values = array_values(node)
        if values is None or len(values) != 2:
            return False
        return class_const_raw(f, values[0]) is not None and str_value(f, values[1]) is not None

    def _spec(self, f: PhpFile, node) -> Spec | None:
        if node is None:
            return None
        if node.type in CLOSURE_TYPES:
            return Spec(closure=node, node=node)
        raw = class_const_raw(f, node)
        if raw:
            return Spec(fqn=f.class_fqn(raw), node=node)
        lit = str_value(f, node)
        if lit is not None:
            cls, _, method = lit.partition("@")
            return Spec(fqn=string_class_fqn(cls), method=method or None, node=node) if string_class_fqn(cls) else None
        values = array_values(node)
        if values is not None and len(values) == 2:
            first, second = values
            method = str_value(f, second)
            if method is None:
                return None
            raw = class_const_raw(f, first)
            if raw:
                return Spec(fqn=f.class_fqn(raw), method=method, node=node)
            if first.type == "class_constant_access_expression" and f.text(first).lower().startswith(("self::", "static::")):
                return Spec(fqn=enclosing_type(f, first), method=method, node=node)
            return None
        if node.type == "member_call_expression":
            root = chain_root(node)
            if root is not None and root.type == "scoped_call_expression" and call_name(f, root) == "make":
                scope = name_node_text(f, root.child_by_field_name("scope"))
                if scope and (f.class_fqn(scope) or "").lower().endswith("\\jobpipeline"):
                    jobs = self._class_list(f, arg_node(root, 0))
                    return Spec(jobs=jobs, node=node) if jobs else None
        return None

    def _register(self, f: PhpFile, events: list[str], spec: Spec | None, line: int, context: str) -> None:
        rt = self.rt
        if spec is None:
            return
        for event_fqn in events:
            event = rt.target(event_fqn)
            if event is None:
                continue
            if spec.closure is not None:
                src = rt.idx.closure_at(f.rel, line_of(spec.closure))
                rt.edge(src, event, "listens_to", "EXTRACTED", f, line, context, **{"listener": "closure"})
                continue
            sources = [(rt.klass(j), {"via": "JobPipeline"}) for j in spec.jobs] if spec.jobs else [
                (rt.klass(spec.fqn), {"method": spec.method} if spec.method else {})
            ]
            for src, extra in sources:
                if rt.edge(src, event, "listens_to", "EXTRACTED", f, line, context, **extra) is not None:
                    self._remember(src, event)

    def _events_of_listen(self, f: PhpFile, call) -> list[str]:
        first = arg_node(call, 0)
        events: list[str] = []
        if first is not None and first.type in CLOSURE_TYPES:
            params = param_types(f, first)
            types = next(iter(params.values()), [])
            return list(types)
        values = array_values(first)
        for v in values if values is not None else [first]:
            raw = class_const_raw(f, v) if v is not None else None
            if raw:
                fqn = f.class_fqn(raw)
                if fqn:
                    events.append(fqn)
        return events

    def _subscriber(self, f: PhpFile, decl, sub, *, registered: bool = False) -> None:
        """``subscribe(Dispatcher $events)``: ``$events->listen(E::class, 'method')`` calls and
        a returned ``[E::class => 'method']`` array. A class named in ``$subscribe`` /
        ``Event::subscribe`` (``registered``) need not type its parameter."""
        params = param_types(f, sub)
        if not params:
            return
        var, types = next(iter(params.items()))
        if not registered and not any(t.lower() in _DISPATCHER for t in types):
            return
        if decl.fqn in self.subscribed:
            return
        self.subscribed.add(decl.fqn)
        me = decl.fqn
        for call in iter_calls_in(sub):
            if call.type != "member_call_expression" or call_name(f, call) != "listen":
                continue
            obj = call.child_by_field_name("object")
            if obj is None or f.text(obj).lstrip("$") != var:
                continue
            events = self._events_of_listen(f, call)
            spec = self._subscriber_spec(f, arg_node(call, 1), me)
            if spec is None:
                continue
            for ev in events:
                self._register(f, [ev], spec, line_of(call), "subscriber")
        ret = self._returned_array(sub)
        for key, value in (array_items(ret) or []) if ret is not None else []:
            raw = class_const_raw(f, key) if key is not None else None
            if not raw:
                continue
            event = f.class_fqn(raw)
            for v in (array_values(value) or [value]) if value is not None else []:
                method = str_value(f, v)
                if method and event and me:
                    self._register(f, [event], Spec(fqn=me, method=method, node=v), line_of(v), "subscriber")

    def _subscriber_spec(self, f: PhpFile, node, me: str | None) -> Spec | None:
        if node is None:
            return None
        lit = str_value(f, node)
        if lit is not None and "@" not in lit and string_class_fqn(lit) and "\\" not in lit and me:
            return Spec(fqn=me, method=lit, node=node)  # 'onLogin': a method of the subscriber
        return self._spec(f, node)

    def _discover(self, f: PhpFile, decl, nid: str) -> None:
        """Laravel's event auto-discovery: ``handle*`` / ``__invoke`` methods of a listener
        class whose first parameter is typed with the event class."""
        rt = self.rt
        for name, m in methods_of(f, decl.node).items():
            mname = f.text(m.child_by_field_name("name"))
            if not (mname.startswith("handle") or mname == "__invoke"):
                continue
            params = param_types(f, m)
            types = next(iter(params.values()), [])
            for fqn in types:
                event = rt.target(fqn)
                if event is not None and event != nid:
                    self.discovered.append((f, nid, event, mname, line_of(m)))
            del name

    # ── call sites ─────────────────────────────────────────────────────────
    def _call(self, f: PhpFile, call) -> None:
        name = call_name(f, call)
        if name is None:
            return
        if call.type == "function_call_expression":
            self._function(f, call, name.lower())
        elif call.type == "scoped_call_expression":
            self._scoped(f, call, name)
        else:
            self._member(f, call, name)

    def _function(self, f: PhpFile, call, name: str) -> None:
        if name in _HELPER_DISPATCH:
            self._job(f, call, arg_node(call, 0), "dispatch_helper", new_only=True)
        elif name in ("event", "broadcast"):
            self._event(f, call, arg_node(call, 0), "event_helper")

    def _scoped(self, f: PhpFile, call, name: str) -> None:
        scope = call.child_by_field_name("scope")
        facade = facade_of(f, scope)
        low = name.lower()
        if facade in _KNOWN_FACADES:
            if facade == "bus":
                if low in _OBJ_DISPATCH:
                    self._job(f, call, arg_node(call, 0), "bus_dispatch", new_only=True)
                elif low in ("chain", "batch"):
                    self._elements(f, call, arg_node(call, 0), f"bus_{low}")
            elif facade == "event":
                if low in _EVENT_FACADE_DISPATCH:
                    self._event(f, call, arg_node(call, 0), "event_facade")
                elif low == "subscribe":
                    self.subscribe_regs.update(self._class_list(f, arg_node(call, 0)))
                elif low == "listen":
                    spec = self._spec(f, arg_node(call, 1))
                    events = self._events_of_listen(f, call)
                    only = arg_node(call, 0)
                    if spec is None and arg_node(call, 1) is None and only is not None and only.type in CLOSURE_TYPES:
                        spec = Spec(closure=only, node=call)  # Event::listen(function (E $e) {...})
                    self._register(f, events, spec, line_of(call), "Event::listen")
            elif facade == "queue" and low in _QUEUE_PUSH:
                for a in positional_args(call):
                    self._job(f, call, a, "queue_push", new_only=True)
            elif facade == "notification" and low in ("send", "sendnow"):
                self._notify(f, call, arg_node(call, 1), "notification_facade")
            elif facade == "mail" and low in _MAIL_SEND:
                self._mail(f, call)
            return
        fqn = class_ref_in_scope(f, call, scope)
        if not fqn:
            return
        if low in _STATIC_DISPATCH:
            nid = self.rt.klass(fqn)
            if nid is None:
                return
            kind, conf = self._classify_static(nid)
            src = self.rt.owner(f, call)
            line = line_of(call)
            if kind == "job":
                self.rt.edge(src, nid, "dispatches", conf, f, line, "dispatch_static", **self._via(conf, f))
                if low == "withchain":
                    self._elements(f, call, arg_node(call, 0), "with_chain")
            elif kind == "event":
                self.rt.edge(src, nid, "dispatches_event", conf, f, line, "dispatch_static")
        elif low == "observe":
            self._observe(f, call, fqn)

    def _member(self, f: PhpFile, call, name: str) -> None:
        low = name.lower()
        if low in ("chain", "withchain"):
            self._elements(f, call, arg_node(call, 0), "chain")
        elif low in _OBJ_DISPATCH:
            self._job(f, call, arg_node(call, 0), "dispatch_method", new_only=True)
        elif low in ("notify", "notifynow"):
            self._notify(f, call, arg_node(call, 0), "notify")
        elif low in _MAIL_SEND:
            root = chain_root(call)
            if root is not None and root.type == "scoped_call_expression" \
                    and facade_of(f, root.child_by_field_name("scope")) == "mail":
                self._mail(f, call)
        elif low == "listen":
            obj = call.child_by_field_name("object")
            scope = enclosing_callable(call)
            if obj is not None and obj.type == "variable_name" and scope is not None:
                types = param_types(f, scope).get(f.text(obj).lstrip("$"), [])
                if any(t.lower() in _DISPATCHER for t in types) and scope.type == "method_declaration" \
                        and f.text(scope.child_by_field_name("name")) != "subscribe":
                    self._register(f, self._events_of_listen(f, call), self._spec(f, arg_node(call, 1)),
                                   line_of(call), "dispatcher_listen")

    # ── emitters ───────────────────────────────────────────────────────────
    @staticmethod
    def _via(conf: str, f: PhpFile) -> dict:
        return {"via": "jobs-directory"} if conf == "INFERRED" else {}

    def _classify_static(self, nid: str) -> tuple[str | None, str]:
        rt = self.rt
        facts = rt.facts(nid)
        if facts is None:
            return None, "EXTRACTED"
        if _EVENT_DISPATCHABLE in facts.vendor:
            return "event", "EXTRACTED"
        if rt.declares(nid, "dispatch"):
            return None, "EXTRACTED"  # a dispatch the corpus defines: the call resolver owns it
        if facts.vendor & _JOB_MARKERS:
            return "job", "EXTRACTED"
        node = rt.idx.nodes.get(nid, {})
        if facts.vendor and _jobs_dir(rt.idx.rel(node.get("source_file", ""))):
            return "job", "INFERRED"
        return None, "EXTRACTED"

    def _job_like(self, nid: str) -> str | None:
        """Confidence if the class can be dispatched as a job object, else None."""
        rt = self.rt
        facts = rt.facts(nid)
        if facts is None:
            return None
        if facts.vendor & _JOB_MARKERS:
            return "EXTRACTED"
        node = rt.idx.nodes.get(nid, {})
        if _jobs_dir(rt.idx.rel(node.get("source_file", ""))) and ("handle" in facts.methods or rt.declares(nid, "handle")):
            return "INFERRED"
        return None

    def _job(self, f: PhpFile, call, node, context: str, *, new_only: bool) -> None:
        if node is None or (new_only and node.type != "object_creation_expression"):
            return
        fqn = class_arg_fqn(f, node)
        nid = self.rt.klass(fqn)
        if nid is None:
            return
        conf = self._job_like(nid)
        if conf is None:
            return
        self.rt.edge(self.rt.owner(f, call), nid, "dispatches", conf, f, line_of(node), context, **self._via(conf, f))

    def _elements(self, f: PhpFile, call, node, context: str) -> None:
        """Jobs listed in a ``chain([...])`` / ``batch([...])`` array (nested arrays allowed)."""
        values = array_values(node)
        if values is None:
            return
        for v in values:
            if array_values(v) is not None:
                self._elements(f, call, v, context)
                continue
            if v is None or v.type not in ("object_creation_expression", "class_constant_access_expression"):
                continue
            nid = self.rt.klass(class_arg_fqn(f, v))
            conf = self._job_like(nid) if nid else None
            if nid and conf:
                self.rt.edge(self.rt.owner(f, call), nid, "dispatches", conf, f, line_of(v), context, **self._via(conf, f))

    def _event(self, f: PhpFile, call, node, context: str) -> None:
        if node is None or node.type != "object_creation_expression":
            return
        nid = self.rt.target(class_arg_fqn(f, node))
        self.rt.edge(self.rt.owner(f, call), nid, "dispatches_event", "EXTRACTED", f, line_of(node), context)

    def _notify(self, f: PhpFile, call, node, context: str) -> None:
        if node is None or node.type != "object_creation_expression":
            return
        nid = self.rt.klass(new_class_fqn(f, node))
        self.rt.edge(self.rt.owner(f, call), nid, "notifies", "EXTRACTED", f, line_of(node), context)

    def _mail(self, f: PhpFile, call) -> None:
        for a in call_args(call):
            if a.node is not None and a.node.type == "object_creation_expression":
                nid = self.rt.klass(new_class_fqn(f, a.node))
                self.rt.edge(self.rt.owner(f, call), nid, "sends_mail", "EXTRACTED", f, line_of(a.node), "mail")

    def _observe(self, f: PhpFile, call, model_fqn: str) -> None:
        rt = self.rt
        model = rt.klass(model_fqn)
        if model is None:
            return
        first = arg_node(call, 0)
        values = array_values(first)
        for v in values if values is not None else [first]:
            if v is None:
                continue
            fqn = class_arg_fqn(f, v) or string_class_fqn(str_value(f, v))
            rt.edge(rt.klass(fqn), model, "observes", "EXTRACTED", f, line_of(call), "model_observe")


__all__ = ["Dispatch", "Spec"]
