"""In-memory graph model for the schema-4 knowledge base.

The JSON store holds graph data and nothing else. Everything that can be
derived from it -- inverse edges, section membership, section adjacency,
the index -- is computed here and never stored, so it cannot drift.
"""

import hashlib
import os
import re

SCHEMA = 4

ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Canonical field order. The writer emits in this order; anything not listed
# is a schema error, so a typo cannot survive a round-trip.
ITEM_FIELDS = ("section", "type", "status", "key", "summary", "highlight",
               "keywords", "code", "affects", "meta", "lint")
# `code` is not listed here: whether it may be empty is a per-type glossary
# question (`requires_code`), so it is checked alongside the type.
ITEM_REQUIRED = ("section", "type", "status", "key", "meta")
META_FIELDS = ("created", "modified", "verified", "run")
CODE_KINDS = ("marker", "symbol", "file")
# `lint` sits before the bulk containers: it is a handful of lines a human
# edits through `kb thresholds` / `kb suppress` and reads in a diff, and after
# `items` it would be eighty items down the file.
ROOT_FIELDS = ("schema", "title", "run", "lint", "glossary", "sections",
               "subsections", "items")

# Every threshold a rule reads, with the value it uses when the store overrides
# nothing. These names are the store's keys AND the `kb thresholds` arguments,
# so a threshold cannot exist under one name in the config and another in the
# rule -- the same property `lint.RULES` gives rule names.
THRESHOLDS = {
    "edge_kind_share": 0.75,
    "keyword_broad_share": 0.6,
    "section_min_items": 3,
    "subsection_min_items": 2,
    "stale_months": 6,
    "max_span": 120,
}
# The two that are shares of a population rather than counts, so validation
# can hold them to 0..1 and `kb thresholds` can parse them as floats.
SHARE_THRESHOLDS = ("edge_kind_share", "keyword_broad_share")
# Which rule reads which, for `kb thresholds` to print. A threshold nobody
# reads is dead config, and this table is what makes that visible.
THRESHOLD_RULES = {
    "edge_kind_share": "edge-kind-missing",
    "keyword_broad_share": "keyword-broad",
    "section_min_items": "section-min-items",
    "subsection_min_items": "subsection-min-items",
    "stale_months": "verified-stale",
    "max_span": "marker-span",
}

# What each rule reads, i.e. what a suppression of it was granted against. A
# rule absent from this map has no honest basis -- its evidence is source text
# (the three marker-scanning rules), a count across the whole store
# (`keyword-broad`, the two min-items rules), or a date whose finding clears
# itself (`verified-stale`) -- and its suppressions are simply not
# basis-checked.
#
# `code-drift` is deliberately absent and is refused outright by `kb suppress`
# (PLAN-2 3.3, UNSUPPRESSIBLE). Its basis could only be `ref-content`, which is
# the very thing whose change re-fires it: the exemption would suspend on the
# next edit to that file, every time. Drift's honest resolution is `kb verify`.
BASIS_FIELDS = {
    "edge-target": "edges", "self-edge": "edges", "edge-dup": "edges",
    "orphan": "edges", "edge-kind-missing": "edges",
    "key-length": "key", "key-modal": "key", "stale-id-mention": "key",
    "summary-longer": "key+summary",
    "keyword-case": "keywords", "keyword-phrase": "keywords",
    "keyword-dup": "keywords", "keyword-missing": "keywords",
    "code-path": "ref-identity", "marker-missing": "ref-identity",
    "marker-span": "ref-content", "marker-empty": "ref-content",
    "code-inexact": "ref-identity",
}

DIGEST = "sha256:"
DIGEST_LEN = 16


def _digest(text):
    return DIGEST + hashlib.sha256(
        text.encode("utf-8")).hexdigest()[:DIGEST_LEN]


def basis(g, it, rule, ref=None):
    """A digest of exactly what `rule` reads on `it`, or None when it has no
    honest basis.

    This is the evidence a suppression is granted against. When it stops
    matching, the thing the reason was written about has moved, and the
    exemption is suspended rather than carried silently onto a different key.
    Deliberately per-field rather than a digest of the whole item: otherwise
    adding a keyword would invalidate a `key-length` exemption, and an
    invalidation nobody believes is one everybody re-stamps without reading.
    """
    field = BASIS_FIELDS.get(rule)
    if field is None:
        return None
    if field == "key":
        return _digest(it.key or "")
    if field == "key+summary":
        return _digest((it.key or "") + "\n" + (it.summary or ""))
    if field == "keywords":
        return _digest("\n".join(sorted(k.lower() for k in it.keywords)))
    if field == "edges":
        return _digest("\n".join(sorted("%s %s" % (e.to, e.kind)
                                        for e in it.affects)))
    if ref is None or not 0 <= ref < len(it.code):
        # A per-ref rule suppressed item-wide, or an index that no longer
        # names a ref. A digest standing for every ref at once would move when
        # an unrelated ref did, so no basis is the honest answer; the dangling
        # index is `suppress-stale`'s business, not this function's.
        return None
    c = it.code[ref]
    ident = _digest("%s %s %s" % (c.kind, c.path, c.symbol or ""))
    if field == "ref-identity":
        return ident
    # Local import: `markers` reads the tree, and `model` is the one module
    # with no I/O in it. Only the content bases need it.
    from . import markers
    # `digest_ref` is None for every `symbol` ref by design (hashing a symbol
    # body needs real TypeScript parsing, which DESIGN.md declined), so those
    # fall back to identity: the basis then catches a repointed ref but not an
    # edited body, which is exactly as much as the tool can see.
    return markers.digest_ref(g.root, c, it.id) or ident


class SchemaError(Exception):
    """The store is malformed. Distinct from lint: lint reports on a graph
    that loaded, this means it did not."""

    def __init__(self, problems):
        self.problems = list(problems)
        Exception.__init__(self, "\n".join(self.problems))


class CodeRef:
    # `digest` must be a named parameter, not just a slot: Item.__init__ builds
    # refs with CodeRef(**c) straight from the store, so a field the signature
    # does not accept is a TypeError at load rather than a schema error.
    __slots__ = ("kind", "path", "symbol", "part", "digest")

    def __init__(self, kind, path, symbol=None, part=None, digest=None):
        self.kind = kind
        self.path = path
        self.symbol = symbol
        self.part = part
        self.digest = digest

    def to_dict(self):
        d = {"kind": self.kind, "path": self.path}
        if self.symbol:
            d["symbol"] = self.symbol
        if self.part:
            d["part"] = self.part
        if self.digest:
            d["digest"] = self.digest
        return d

    def __str__(self):
        s = self.path
        if self.symbol:
            s += " → " + self.symbol
        if self.part:
            s += " [%s]" % self.part
        if self.kind == "marker":
            s += " (marker)"
        return s


class Edge:
    __slots__ = ("to", "kind")

    def __init__(self, to, kind="relates"):
        self.to = to
        self.kind = kind

    def to_dict(self):
        return {"to": self.to, "kind": self.kind}


class Item:
    __slots__ = ("id", "section", "type", "status", "key", "summary",
                 "highlight", "keywords", "code", "affects", "meta", "lint")

    def __init__(self, iid, d):
        self.id = iid
        self.section = d.get("section")
        self.type = d.get("type")
        self.status = d.get("status")
        self.key = d.get("key", "")
        self.summary = d.get("summary")
        self.highlight = bool(d.get("highlight", False))
        self.keywords = list(d.get("keywords", []))
        self.code = [CodeRef(**c) for c in d.get("code", [])]
        self.affects = [Edge(e.get("to"), e.get("kind", "relates"))
                        for e in d.get("affects", [])]
        self.meta = dict(d.get("meta", {}))
        self.lint = dict(d.get("lint", {}))

    @property
    def name(self):
        """Id without the section prefix."""
        return self.id.split(".", 1)[1] if "." in self.id else self.id

    @property
    def headline(self):
        """What a section header shows for this item."""
        return self.summary or self.key

    @property
    def code_paths(self):
        return [c.path for c in self.code]

    def suppression(self, rule, ref=None):
        """The entry silencing `rule` on this item, or None.

        An entry that names a `ref` silences only findings from that code ref;
        one that names none silences the rule for the whole item. The index is
        the addressing `lint.py` already uses for its relocation map, so the
        store gains no second way to point at a code ref.
        """
        for x in self.lint.get("ignore", []):
            if x.get("rule") != rule:
                continue
            want = x.get("ref")
            if want is None or want == ref:
                return x
        return None

    def suppressed(self, rule, ref=None):
        return self.suppression(rule, ref) is not None

    def to_dict(self):
        d = {"section": self.section, "type": self.type, "status": self.status,
             "key": self.key}
        if self.summary:
            d["summary"] = self.summary
        if self.highlight:
            d["highlight"] = True
        if self.keywords:
            d["keywords"] = sorted(set(self.keywords))
        d["code"] = [c.to_dict() for c in self.code]
        if self.affects:
            # Canonical order is by target: authoring order carries no meaning
            # and would otherwise churn the diff. Code ref order does carry
            # meaning, so it is left alone.
            d["affects"] = [e.to_dict()
                            for e in sorted(self.affects, key=lambda e: e.to)]
        d["meta"] = {k: self.meta[k] for k in META_FIELDS if k in self.meta}
        if self.lint.get("ignore"):
            d["lint"] = {"ignore": [dict(x) for x in self.lint["ignore"]]}
        return d


class Section:
    __slots__ = ("id", "order", "label")

    def __init__(self, sid, d):
        self.id = sid
        self.order = d.get("order", 0)
        self.label = d.get("label", sid)

    def to_dict(self):
        return {"order": self.order, "label": self.label}


class Subsection:
    """A named group of items inside a section, keyed by their shared id prefix.

    Membership is derived from the prefix and never stored, exactly as section
    membership is. `section` and `depth` are likewise derived: the id already
    carries both, and storing either would be storing drift.
    """
    __slots__ = ("id", "order", "label", "summary")

    def __init__(self, sid, d):
        self.id = sid
        self.order = d.get("order", 0)
        self.label = d.get("label", sid.rsplit(".", 1)[-1])
        self.summary = d.get("summary")

    @property
    def section(self):
        return self.id.split(".", 1)[0]

    @property
    def depth(self):
        return self.id.count(".")

    def to_dict(self):
        d = {"order": self.order, "label": self.label}
        if self.summary:
            d["summary"] = self.summary
        return d


class Glossary:
    def __init__(self, d):
        self.types = dict(d.get("types", {}))
        self.statuses = dict(d.get("statuses", {}))
        self.edge_kinds = dict(d.get("edge_kinds", {}))

    def label(self, kind):
        return self.edge_kinds.get(kind, {}).get("label", kind.capitalize())

    def inverse(self, kind):
        return self.edge_kinds.get(kind, {}).get("inverse", kind + " by")

    def key_max(self, type_):
        return self.types.get(type_, {}).get("key_max")

    def requires_modal(self, type_):
        return bool(self.types.get(type_, {}).get("requires_modal"))

    def requires_code(self, type_):
        return self.types.get(type_, {}).get("requires_code", True)

    def requires_edges(self, type_):
        """Whether an item of this type with no edges is worth a lint warning.
        A directive is edgeless by design -- it governs the whole graph rather
        than a neighbourhood of it -- so orphanhood is its normal state, not a
        finding to look at every run."""
        return self.types.get(type_, {}).get("requires_edges", True)

    def to_dict(self):
        return {"types": self.types, "statuses": self.statuses,
                "edge_kinds": self.edge_kinds}


class LintConfig:
    """The store's `lint` key: per-repo thresholds, suppressions for findings
    that belong to no item, and retired names still watched for in prose.

    Item suppressions stay on the item -- there `who` IS an item id and the
    entry has an owner to live in. Everything else (`[section]`, `[prefix]`,
    `keyword:<kw>`, a path) has no owner, so it lives here keyed by the exact
    `who` string the finding prints. One spelling to learn, and `kb debt`'s
    "is `who` an item id" attribution rule keeps working untouched.

    `legacy` holds one entry per renamed-away item, section or subsection
    that some other item's key still cites in prose -- an id, or a
    section/subsection prefix, is never written here unless something
    actually names it, and an entry with `kind` "section" is also the one
    thing that lets `stale-id-mention` recognise a prose token whose section
    no longer exists at all (see `lint._graph_rules`).
    """
    __slots__ = ("thresholds", "ignore", "legacy")

    def __init__(self, d):
        self.thresholds = dict(d.get("thresholds") or {})
        self.ignore = [dict(x) for x in (d.get("ignore") or [])]
        self.legacy = [dict(x) for x in (d.get("legacy") or [])]

    def threshold(self, name):
        """The store's value, or the default. Not `dict.get(name, default)`:
        a stored `0` is a real setting and must not read as absent."""
        v = self.thresholds.get(name)
        return THRESHOLDS[name] if v is None else v

    def suppression(self, who, rule):
        """The entry silencing `rule` on `who`, or None."""
        for x in self.ignore:
            if x.get("who") == who and x.get("rule") == rule:
                return x
        return None

    def to_dict(self):
        d = {}
        if self.thresholds:
            # THRESHOLDS order, not insertion order: the store is a diff
            # surface and a set of settings must not reorder itself.
            d["thresholds"] = {k: self.thresholds[k]
                               for k in THRESHOLDS if k in self.thresholds}
        if self.ignore:
            d["ignore"] = [dict(x) for x in
                           sorted(self.ignore,
                                  key=lambda x: (x.get("who") or "",
                                                 x.get("rule") or ""))]
        if self.legacy:
            d["legacy"] = [dict(x) for x in
                           sorted(self.legacy, key=lambda x: x.get("name") or "")]
        return d

    def __bool__(self):
        return bool(self.thresholds or self.ignore or self.legacy)


class Graph:
    """The whole store. Items are held sorted by id; since ids are
    <section>.<name> that groups them by section for free."""

    def __init__(self, data, path=None):
        self.path = path
        self.schema = data.get("schema")
        self.title = data.get("title", "Knowledge Base")
        self.run = data.get("run")
        self.lint = LintConfig(data.get("lint", {}))
        self.glossary = Glossary(data.get("glossary", {}))
        self.sections = {k: Section(k, v)
                         for k, v in sorted(data.get("sections", {}).items())}
        self.subsections = {k: Subsection(k, v) for k, v in
                            sorted(data.get("subsections", {}).items())}
        self.items = {k: Item(k, v)
                      for k, v in sorted(data.get("items", {}).items())}
        self._inbound = None
        self._root = None

    # ------------------------------------------------------------ derived

    # kb:begin model.derived-inverse
    @property
    def inbound(self):
        """id -> [(source_id, kind)]. The inverse of every `affects` edge.

        Only one direction is stored, which is what deletes v1's edge-symmetry
        invariant and its two-sided writes.
        """
        if self._inbound is None:
            inb = {k: [] for k in self.items}
            for it in self.items.values():
                for e in it.affects:
                    if e.to in inb:
                        inb[e.to].append((it.id, e.kind))
            for v in inb.values():
                v.sort()
            self._inbound = inb
        return self._inbound
    # kb:end model.derived-inverse

    def invalidate(self):
        self._inbound = None

    def ordered_sections(self):
        return sorted(self.sections.values(), key=lambda s: (s.order, s.id))

    def section_items(self, sid):
        return [it for it in self.items.values() if it.section == sid]

    # kb:begin model.subsection-longest-prefix-wins
    def subsection_of(self, iid):
        """The deepest declared subsection this id sits under, or None.

        Longest prefix wins. With `pages.a` and `pages.a.b` both declared, a
        bare `startswith` puts `pages.a.b.c` in BOTH -- membership stops being
        a function and the render duplicates the item. The whole nesting story
        rests on this being a single deepest answer.
        """
        best = None
        for pid in self.subsections:
            if iid.startswith(pid + ".") and (best is None or len(pid) > len(best)):
                best = pid
        return best
    # kb:end model.subsection-longest-prefix-wins

    def subsection_members(self, pid):
        return [it for it in self.items.values()
                if self.subsection_of(it.id) == pid]

    def section_subsections(self, sid):
        return sorted((s for s in self.subsections.values()
                       if s.section == sid),
                      key=lambda s: (s.order, s.id))

    def highlights(self, sid):
        return [it for it in self.section_items(sid) if it.highlight]

    def related_sections(self, sid):
        """Sections reachable by any edge crossing out of or into this one,
        with edge counts, most-connected first."""
        counts = {}
        for it in self.section_items(sid):
            for e in it.affects:
                other = self.items.get(e.to)
                if other is not None and other.section != sid:
                    counts[other.section] = counts.get(other.section, 0) + 1
        for it in self.items.values():
            if it.section == sid:
                continue
            for e in it.affects:
                tgt = self.items.get(e.to)
                if tgt is not None and tgt.section == sid:
                    counts[it.section] = counts.get(it.section, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def neighbors(self, iid, direction="down", kinds=None):
        """[(other_id, kind, arrow)] -- `down` follows affects, `up` follows
        the derived inverse, `both` unions them without duplicating a pair."""
        it = self.items[iid]
        out, seen = [], set()
        if direction in ("down", "both"):
            for e in it.affects:
                if kinds and e.kind not in kinds:
                    continue
                if e.to not in seen:
                    seen.add(e.to)
                    out.append((e.to, e.kind, "→"))
        if direction in ("up", "both"):
            for src, kind in self.inbound.get(iid, []):
                if kinds and kind not in kinds:
                    continue
                if src not in seen:
                    seen.add(src)
                    out.append((src, kind, "←"))
        return out

    def walk(self, roots, direction="down", depth=1, kinds=None):
        """Breadth-first closure. Returns (items in visit order, {id: depth})."""
        from collections import deque
        seen, order, q = {}, [], deque()
        for r in roots:
            if r.id not in seen:
                seen[r.id] = 0
                order.append(r)
                q.append((r.id, 0))
        while q:
            iid, d = q.popleft()
            if d >= depth:
                continue
            for nid, _kind, _arrow in self.neighbors(iid, direction, kinds):
                nxt = self.items.get(nid)
                if nxt is None or nid in seen:
                    continue
                seen[nid] = d + 1
                order.append(nxt)
                q.append((nid, d + 1))
        return order, seen

    def match_paths(self, query):
        """[(item, path, how)] -- exact matches win, then suffix, then loose."""
        q = query.replace("\\", "/").strip().lstrip("./")
        base = os.path.basename(q)
        exact, suffix, loose = [], [], []
        for it in self.items.values():
            for c in it.code:
                pn = (c.path or "").replace("\\", "/")
                if pn == q:
                    exact.append((it, c, "exact"))
                elif pn.endswith("/" + q) or q.endswith("/" + pn):
                    suffix.append((it, c, "suffix"))
                elif os.path.basename(pn) == base or q in pn:
                    loose.append((it, c, "loose"))
        return exact or suffix or loose

    @property
    def root(self):
        """Where `Code:` paths resolve from -- the directory holding the
        store, unless overridden (`--root`, for a store kept out of tree)."""
        if self._root:
            return self._root
        return os.path.dirname(self.path) if self.path else os.getcwd()

    @root.setter
    def root(self, value):
        self._root = value

    def degree(self, iid):
        it = self.items[iid]
        return len(it.affects) + len(self.inbound.get(iid, []))

    # --------------------------------------------------------- key debt

    # kb:begin debt.excess-is-the-metric
    def key_excess(self, it):
        """Characters this item's key is over its type's cap, or 0. A type
        with no cap declared has no debt by definition, not zero debt."""
        cap = self.glossary.key_max(it.type)
        return max(0, len(it.key) - cap) if cap else 0

    def halve_to(self, it):
        """The length a repair of this key is graded against, or None when the
        type declares no cap.

        Half, but never below the cap: a 500-char key with a 320 cap halves to
        320, not 250, because 250 asks for a key shorter than the type's own
        budget and no one would write it.
        """
        cap = self.glossary.key_max(it.type)
        return max(cap, len(it.key) // 2) if cap else None

    def excess_total(self, items=None):
        """Total over-cap excess. This, not the warning count, is what the
        hygiene mechanism is graded on: splitting one 17,950-char item into
        eight raises `key-length` by seven while cutting the debt by a third,
        and a metric that called that a regression would forbid the repair.
        """
        return sum(self.key_excess(i)
                   for i in (self.items.values() if items is None else items))
    # kb:end debt.excess-is-the-metric

    # ------------------------------------------------------------- lookup

    # kb:begin model.prefix-resolution
    def get(self, iid):
        """Exact id, then unique suffix, then unique substring."""
        if iid in self.items:
            return self.items[iid]
        hits = [i for i in self.items if i.endswith("." + iid)] or \
               [i for i in self.items if iid in i]
        if len(hits) == 1:
            return self.items[hits[0]]
        if not hits:
            raise KeyError("no such item: %s" % iid)
        raise KeyError("ambiguous '%s' -> %s"
                       % (iid, ", ".join(sorted(hits)[:8])))
    # kb:end model.prefix-resolution

    def section_of(self, name):
        name = name.strip("[]")
        if name in self.sections:
            return self.sections[name]
        cand = [s for s in self.sections if name in s]
        if len(cand) == 1:
            return self.sections[cand[0]]
        raise KeyError("no such section: %s (have: %s)"
                       % (name, ", ".join(sorted(self.sections))))

    # --------------------------------------------------------- validation

    def validate(self):
        """Structural checks only -- everything that must hold for the graph
        to be a graph at all. Judgment calls (length caps, staleness, orphans)
        are lint's job, not this."""
        p = []
        if self.schema != SCHEMA:
            p.append("schema: expected %d, found %r" % (SCHEMA, self.schema))
        for sid, s in self.sections.items():
            if not ID_RE.match(sid):
                p.append("section %s: id must be lowercase dotted/dashed" % sid)
            if not isinstance(s.order, int):
                p.append("section %s: order must be an integer" % sid)
        for pid, sd in self.subsections.items():
            if not ID_RE.match(pid):
                p.append("subsection %s: id must be lowercase dotted/dashed" % pid)
            elif "." not in pid:
                p.append("subsection %s: id must be '<section>.<prefix>'" % pid)
            elif sd.section not in self.sections:
                p.append("subsection %s: unknown section '%s'" % (pid, sd.section))
            if not isinstance(sd.order, int):
                p.append("subsection %s: order must be an integer" % pid)
        for kind, spec in self.glossary.edge_kinds.items():
            if "inverse" not in spec:
                p.append("glossary.edge_kinds.%s: missing 'inverse'" % kind)
        p.extend(self._validate_lint())
        for iid, it in self.items.items():
            p.extend(self._validate_item(iid, it))
        if p:
            raise SchemaError(p)
        return self

    def _validate_lint(self):
        """The root `lint` key. Structural only, per
        `model.validate-is-structural`.

        What may be enforced here is decided by one property: a write that
        `commit()` accepts must be a store that `load()` accepts. `commit`
        validates before it serialises (`write.py:171-186`) and `load`
        validates what it read (`store.py:78-91`), over the same pure
        `validate()`, so the two agree by construction. An invariant every
        mutation command maintains may therefore be enforced; an invariant that
        depends on the tree, on a later release's tables, or on something no
        command owns may not, because the store would become unloadable and
        `kb` could not be the tool that repairs it.

        `who` naming a section is enforceable because `kb section rm` and
        `kb section rename` now cascade (3.10b). The rule name is not: a
        release that renames a rule would strand every store carrying the old
        spelling, so a stale rule name is `suppress-stale`'s finding.
        """
        p = []
        for name in sorted(self.lint.thresholds):
            v = self.lint.thresholds[name]
            if name not in THRESHOLDS:
                p.append("lint.thresholds: unknown threshold '%s' (known: %s)"
                         % (name, ", ".join(sorted(THRESHOLDS))))
            elif isinstance(v, bool) or not isinstance(v, (int, float)):
                p.append("lint.thresholds.%s must be a number, found %r"
                         % (name, v))
            elif name in SHARE_THRESHOLDS and not 0.0 <= v <= 1.0:
                p.append("lint.thresholds.%s is a share and must be between 0 "
                         "and 1, found %r" % (name, v))
            elif name not in SHARE_THRESHOLDS and v < 0:
                p.append("lint.thresholds.%s must not be negative, found %r"
                         % (name, v))
        for x in self.lint.ignore:
            if not x.get("who") or not x.get("rule") or not x.get("reason"):
                p.append("lint.ignore needs 'who', 'rule' and 'reason': %r"
                         % (x,))
                continue
            who = x["who"]
            if who in self.items:
                p.append("lint.ignore: '%s' is an item, so its suppressions "
                         "live on the item, not here" % who)
            elif who.startswith("[") and who.endswith("]") \
                    and who[1:-1] not in self.sections \
                    and who[1:-1] not in self.subsections:
                # Enforceable because every command that can retire a section
                # cascades (3.10b). A `keyword:` or a path `who` is NOT checked:
                # no command owns a keyword's lifetime, and a path is a fact
                # about the tree, which `validate` never reads.
                p.append("lint.ignore: no section or subsection '%s' -- "
                         "`kb section rm` drops its entries, so this one was "
                         "hand-written or merged in" % who[1:-1])
        seen_legacy = set()
        for x in self.lint.legacy:
            if not x.get("name") or not x.get("kind") or not x.get("renamed_to") \
                    or not x.get("since"):
                p.append("lint.legacy needs 'name', 'kind', 'renamed_to' and "
                         "'since': %r" % (x,))
                continue
            name = x["name"]
            if x["kind"] not in ("item", "section", "subsection"):
                p.append("lint.legacy.%s: kind must be item, section or "
                         "subsection, found %r" % (name, x["kind"]))
            if name in seen_legacy:
                p.append("lint.legacy: '%s' is listed twice" % name)
            seen_legacy.add(name)
            if name in self.items or name in self.sections \
                    or name in self.subsections:
                p.append("lint.legacy: '%s' is live (an item, section or "
                         "subsection), not a renamed-away name -- only a "
                         "rename command writes this list, and reoccupying "
                         "a retired name always retires the entry" % name)
            if not DATE_RE.match(str(x["since"])):
                p.append("lint.legacy.%s: since must be YYYY-MM-DD, found %r"
                         % (name, x["since"]))
        return p

    def _validate_item(self, iid, it):
        p = []
        if not ID_RE.match(iid):
            p.append("%s: id must be lowercase dotted/dashed" % iid)
        for f in ITEM_REQUIRED:
            if getattr(it, f, None) in (None, "", [], {}):
                p.append("%s: missing required field '%s'" % (iid, f))
        if it.section and it.section not in self.sections:
            p.append("%s: unknown section '%s'" % (iid, it.section))
        elif it.section and not iid.startswith(it.section + "."):
            p.append("%s: id prefix does not match section '%s'"
                     % (iid, it.section))
        if it.type and it.type not in self.glossary.types:
            p.append("%s: unknown type '%s'" % (iid, it.type))
        if it.status and it.status not in self.glossary.statuses:
            p.append("%s: unknown status '%s'" % (iid, it.status))
        if not it.code and self.glossary.requires_code(it.type):
            p.append("%s: needs at least one code ref" % iid)
        for c in it.code:
            if c.kind not in CODE_KINDS:
                p.append("%s: bad code kind '%s'" % (iid, c.kind))
            if not c.path:
                p.append("%s: code ref without a path" % iid)
            if c.kind != "symbol" and c.symbol:
                p.append("%s: only 'symbol' refs carry a symbol (%s)"
                         % (iid, c.path))
            # kb:begin model.no-line-numbers
            if ":" in (c.path or "") or re.search(r"[#:]\d+", c.path or ""):
                p.append("%s: code paths never carry line numbers (%s)"
                         % (iid, c.path))
            # kb:end model.no-line-numbers
        for e in it.affects:
            if e.kind not in self.glossary.edge_kinds:
                p.append("%s: unknown edge kind '%s'" % (iid, e.kind))
        for f in META_FIELDS:
            v = it.meta.get(f)
            if f == "run":
                continue
            if v is None:
                p.append("%s: meta.%s is required" % (iid, f))
            elif not DATE_RE.match(str(v)):
                p.append("%s: meta.%s must be YYYY-MM-DD, found %r"
                         % (iid, f, v))
        for x in it.lint.get("ignore", []):
            if not x.get("rule") or not x.get("reason"):
                p.append("%s: lint.ignore needs both 'rule' and 'reason'" % iid)
            ref = x.get("ref")
            if ref is not None:
                if isinstance(ref, bool) or not isinstance(ref, int) \
                        or ref < 0:
                    p.append("%s: lint.ignore ref must be a non-negative "
                             "code-ref index, found %r" % (iid, ref))
                elif ref >= len(it.code):
                    # Range-checked because no command can produce this state:
                    # `kb code rm` renumbers the entries and drops the one it
                    # orphans in the same mutation, `code clear` and
                    # `--replace` drop them all (3.10a). What reaches here is a
                    # hand-edit or a merge, and naming it at load is more use
                    # than carrying it as a live-looking exemption.
                    p.append("%s: lint.ignore ref %d names nothing -- the item "
                             "has %d code ref%s"
                             % (iid, ref, len(it.code),
                                "" if len(it.code) == 1 else "s"))
            if x.get("basis") is not None and not isinstance(x["basis"], str):
                p.append("%s: lint.ignore basis must be a string, found %r"
                         % (iid, x["basis"]))
        return p

    # ------------------------------------------------------------- output

    def to_dict(self):
        d = {
            "schema": SCHEMA,
            "title": self.title,
            "run": self.run,
            "glossary": self.glossary.to_dict(),
            "sections": {k: self.sections[k].to_dict()
                         for k in sorted(self.sections)},
            "items": {k: self.items[k].to_dict() for k in sorted(self.items)},
        }
        if self.subsections:
            # Omitted when empty, so a store that declares no subsection is
            # byte-identical to its schema-2 form apart from the version stamp.
            d["subsections"] = {k: self.subsections[k].to_dict()
                                for k in sorted(self.subsections)}
        if self.lint:
            # Same rule: a store that overrides no threshold and suppresses
            # nothing outside an item carries no `lint` key at all, so the
            # schema-4 upgrade is a version stamp and nothing else.
            d["lint"] = self.lint.to_dict()
        return {k: d[k] for k in ROOT_FIELDS if k in d}
