"""Lint: the judgment calls, on a graph that already loaded.

`model.validate()` is the structural gate -- if it fails the store is not a
graph and nothing here can run. Lint is the layer above: rules that can be
false about a perfectly well-formed store.

Severity, which the schema left open, is settled here:

  **error** -- the graph asserts something checkably untrue. An edge to an id
  that does not exist, a code path that is not in the tree, a marker whose
  `end` never came. Each is a fact, not a taste, and each is cheap to fix.
  Only errors move the exit code, so this is the set a pre-commit hook is
  allowed to block on.

  **warn** -- the graph is true but not yet good. Keys over their type's cap,
  missing modals, orphans, staleness. These are a backlog, not a breakage.

No rule silences itself. The adoption gate that used to keep three rules quiet
until the store had taken the practice up is gone: it answered the wall-of-
warnings problem that `--baseline`, per-cause suppression and scope selection
now answer better, and it did so by telling a repo that nothing was wrong. A
repo working through its backlog needs the backlog.

What remains is thresholds, which are a different thing: a number a rule
measures against, stored per repo (`kb thresholds`) and overridable per run.
`edge-kind-missing` is the one that reads as a gate, and it is judged per
section, because a section is the scope a team owns.

Every finding names a subject in `who`, and `who` is the suppression key: an
item id, a `[section]`, a `[prefix]`, a `keyword:<kw>`, or a path. It carries
nothing volatile -- no line numbers, no sentinels standing for "the whole
store" -- because an exemption keyed on something that moves is one that stops
matching without saying so. File positions live in the message.

A suppression is scoped to the evidence it was granted against
(`model.basis`). When that evidence moves the exemption is suspended: the
finding returns at full severity and `suppress-stale` names the entry, with its
reason, so re-confirming is a decision rather than a default.

Rules deleted by the format change (duplicate ids, `Items` drift, edge
asymmetry, `Related` reciprocity, `§index` coverage) are impossible by
construction and are not reimplemented here.

One rule the schema named is deliberately absent: "`key` modified in a later
run than `summary`". `meta` is per item, not per field, so the store simply
cannot answer it -- there is no honest implementation. It is replaced by a
write-time nudge in `kb set <id> key`, which is where the staleness is
actually created.
"""

import datetime
import os
import re

from . import markers
from . import model as model_mod
from . import write as write_mod
from .read import DOT, out

ERROR, WARN = "error", "warn"

# name, severity, what it catches. `kb lint --list` prints this, and the name
# is what `kb suppress --who W --rule R` takes.
# kb:begin lint.rule-table-is-the-source lint.severity-split
RULES = (
    ("edge-target",     ERROR, "affects an item that does not exist"),
    ("self-edge",       ERROR, "an item that affects itself"),
    ("edge-dup",        ERROR, "the same edge target listed twice"),
    ("code-path",       ERROR, "a code ref whose path is not in the tree"),
    ("marker-missing",  ERROR, "a marker ref with no `kb:begin` in that file"),
    ("marker-unpaired", ERROR, "`kb:begin` without `kb:end`, or the reverse"),
    ("marker-unknown",  ERROR, "a marker in the source naming an unknown item"),
    ("marker-jsx",      ERROR, "a `//` marker in JSX children -- renders as text"),
    ("marker-syntax",   ERROR, "a marker line carrying anything but ids"),
    ("summary-longer",  ERROR, "a summary longer than the key it compresses"),
    ("key-length",      WARN,  "key past its type's cap"),
    ("key-modal",       WARN,  "a modal-requiring type whose key has no modal"),
    ("orphan",          WARN,  "no edges in either direction"),
    ("section-min-items", WARN, "fewer items in a section than the threshold"),
    ("subsection-min-items", WARN, "fewer items in a subsection than the threshold"),
    ("keyword-case",    WARN,  "a keyword that is not lowercase"),
    ("keyword-phrase",  WARN,  "a keyword of more than three words"),
    ("keyword-dup",     WARN,  "the same keyword listed twice on one item"),
    ("keyword-missing", WARN,  "an item with no keywords"),
    ("keyword-broad",   WARN,  "a keyword tagging most of the tagged items"),
    ("marker-span",     WARN,  "a marker region long enough that `end` likely drifted"),
    ("marker-empty",    WARN,  "a marker region with no code in it"),
    ("verified-stale",  WARN,  "not re-checked against the code in N months"),
    ("edge-kind-missing", WARN, "edges still carrying the default 'relates' kind"),
    ("stale-id-mention", WARN, "key prose names an id that does not exist"),
    ("code-drift",      WARN,  "code changed since the item was last verified"),
    ("code-inexact",    WARN,  "a symbol ref with no marker ref beside it"),
    ("suppress-stale",  WARN,  "a suppression that no longer resolves"),
)
SEVERITY = {name: sev for name, sev, _ in RULES}
# kb:end lint.severity-split lint.rule-table-is-the-source

# rule -> the threshold it measures against, inverted from the model's table
# so neither side can name a threshold the other does not have.
_RULE_THRESHOLD = {rule: name
                   for name, rule in model_mod.THRESHOLD_RULES.items()}

# kb:begin lint.two-rules-cannot-be-suppressed
# `suppress-stale` reports exemptions that no longer resolve, so silencing it
# hides exactly what it exists to surface -- and the entry doing the hiding
# would be invisible to the rule that would have found it.
#
# `code-drift` is refused for a different reason: it has no basis it could be
# granted against. The only honest one would be the ref's content, which is the
# very thing whose change re-fires the rule, so the exemption would suspend on
# the next edit to that file and every edit after it. Drift asks "has anyone
# re-read this since it changed", and the answer to that is `kb verify`, not an
# exemption. `kb suppress` refuses both names; `run()` never consults an entry
# for either.
UNSUPPRESSIBLE = ("suppress-stale", "code-drift")
# kb:end lint.two-rules-cannot-be-suppressed

MODAL = re.compile(r"\b(MUST|NEVER|ONLY|EXACTLY)\b")

# One owner, in `markers`: `kb code fmt` walks this same tree, and two copies
# would drift the first time one learned about a new build directory.
SKIP_DIRS = markers.SKIP_DIRS

# A line that is blank or all comment does not count as content inside a
# marker region -- a region of nothing but comments is a marker pair that has
# drifted off the code it was placed around.
COMMENT_START = ("//", "#", "/*", "*", "*/", "<!--", "-->", "{/*")

# kb:begin lint.thresholds-not-gates
# The flags that override a stored threshold for one run. Their argparse
# defaults are None rather than the threshold's own default: otherwise "not
# given" and "given the default value" are the same input, and a flag nobody
# typed would silently beat the store's setting.
FLAG_THRESHOLDS = (("stale_months", "stale_months"),
                   ("max_span", "max_span"),
                   ("edge_kind_share", "edge_kind_share"))


def thresholds(g, overrides=None):
    """The numbers this run uses: a command-line flag beats the store's
    setting, which beats the build's default.

    Resolved once and carried on the `Result`, so the rules, the summary and
    the baseline all read the same numbers by construction -- the property the
    deleted `adoption()` had, kept.
    """
    o = overrides or {}
    return {name: (g.lint.threshold(name) if o.get(name) is None else o[name])
            for name in model_mod.THRESHOLDS}


def overrides_from(a):
    """The thresholds one invocation overrides, read off parsed arguments."""
    o = {}
    for attr, name in FLAG_THRESHOLDS:
        v = getattr(a, attr, None)
        if v is not None:
            o[name] = v
    return o
# kb:end lint.thresholds-not-gates

# kb:begin lint.scope-reports-what-it-skipped
class Scope(object):
    """Which findings a run reports on.

    A consumer repo owns part of the graph and is committing to fix everything
    in that part, so the report has to be complete for that part -- and honest
    about the rest. Membership is by the finding's subject: an item in the
    scope, or a `[section]` / `[prefix]` inside it. A finding about the store
    as a whole (`keyword:<kw>`) or about a file no item claims (`marker-jsx`)
    belongs to nobody's section; it is skipped, counted, and named in the
    summary rather than quietly dropped, because a scoped report that does not
    say what it skipped is one nobody can act on.
    """
    __slots__ = ("items", "sections")

    def __init__(self, items=None, sections=None):
        self.items = set(items or ())
        self.sections = set(sections or ())

    def __bool__(self):
        return bool(self.items or self.sections)

    def covers(self, who):
        if not self:
            return True
        if who in self.items:
            return True
        if who.startswith("[") and who.endswith("]"):
            name = who[1:-1]
            return (name in self.sections
                    or name.split(".", 1)[0] in self.sections)
        return False


def build_scope(g, ids=(), sections=()):
    """Resolve a command line into a Scope. Raises KeyError naming everything
    unknown at once.

    An unknown id is an error rather than an empty report: the id came out of a
    previous `kb` command, so one the store does not have means the caller is
    working from something stale, and "nothing is wrong with it" is the worst
    possible answer to give them.
    """
    items, secs, unknown = set(), set(), []
    for sid in sections or ():
        try:
            sec = g.section_of(sid)
        except KeyError:
            unknown.append("[%s]" % sid)
            continue
        secs.add(sec.id)
        items.update(i.id for i in g.section_items(sec.id))
    for iid in ids or ():
        try:
            items.add(g.get(iid).id)
        except KeyError:
            unknown.append(iid)
    if unknown:
        raise KeyError(", ".join(sorted(set(unknown))))
    return Scope(items, secs)


def surfaced_ids(path):
    """Ids from a file, one per line, `#` comments ignored.

    The surfaced set is produced by a previous `kb` command and consumed by
    this one, so it is a handoff between two runs rather than something anyone
    types by hand.
    """
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        write_mod.die("cannot read --surfaced-file %s: %s" % (path, e))
    return [l.strip() for l in text.splitlines()
            if l.strip() and not l.strip().startswith("#")]
# kb:end lint.scope-reports-what-it-skipped

# kb:begin lint.id-mentions-need-a-section-prefix
# A token in key prose shaped like an item id -- model.ID_RE without the
# anchors, so anything `kb new` would accept is a candidate.
ID_TOKEN = re.compile(r"\b[a-z0-9]+(?:[.-][a-z0-9]+)+\b")

# Last segments that mean "this is a filename, not an id". Eleven of this
# repo's fourteen section ids are also module names (lint, render, store,
# write, model, ...), so without this the rule reports `lint.py` and
# `render.py` as dead ids in any key that cites a source file.
FILE_SUFFIXES = ("py", "ts", "tsx", "js", "jsx", "mjs", "json", "md", "css",
                 "scss", "html", "yml", "yaml", "toml", "sh", "txt", "lock")
# kb:end lint.id-mentions-need-a-section-prefix


class Finding(object):
    # `ref` is the code-ref index for the five per-ref rules and None for the
    # rest. It is not printed in the text layout -- the message already names
    # the path -- but it is half of the suppression key, so anything machine-
    # readable has to carry it.
    __slots__ = ("rule", "severity", "who", "msg", "suppressed", "ref")

    def __init__(self, rule, who, msg, suppressed=False, ref=None):
        self.rule = rule
        self.severity = SEVERITY[rule]
        self.who = who
        self.msg = msg
        # Set only under run(keep_suppressed=True). A suppression is a
        # decision, so `kb debt` shows it tagged rather than hiding it -- but
        # it must never move an exit code, which is why `errors` and
        # `warnings` below filter these out.
        self.suppressed = suppressed
        self.ref = ref

    def __str__(self):
        label = "ERROR " if self.severity == ERROR else "warn  "
        if self.suppressed:
            # Shown only under --show-suppressed / --all, and never counted in
            # `errors` or `warnings`: a decision on the record, not a defect.
            label = "supp. "
        return "%s %-17s %-34s %s" % (label, self.rule, self.who, self.msg)


class Result(object):
    def __init__(self):
        self.findings = []
        self.suppressed = 0
        self.hidden = 0           # findings a threshold kept out of the report
        self.out_of_scope = 0     # findings about something not in this scope
        self.scanned = 0          # files opened by the marker pass
        self.relocations = {}     # (item id, ref index) -> path found instead
        self.thresholds = {}      # what this run measured against

    @property
    def errors(self):
        return [f for f in self.findings
                if f.severity == ERROR and not f.suppressed]

    @property
    def warnings(self):
        return [f for f in self.findings
                if f.severity == WARN and not f.suppressed]

    def sorted(self):
        rank = {ERROR: 0, WARN: 1}
        return sorted(self.findings,
                      key=lambda f: (rank[f.severity], f.rule, f.who, f.msg))


# --------------------------------------------------------------- the rules


def run(g, check_files=True, rules=None, keep_suppressed=False,
        overrides=None, ignore_thresholds=False, scope=None):
    """Every rule, in one pass.

    `rules` filters by name. `check_files=False` drops everything that has to
    touch the tree, which is what makes lint usable against a store kept away
    from its repo. `overrides` is the per-invocation threshold map;
    `ignore_thresholds` runs the threshold-gated rules wide open, which is what
    a repo working through its whole backlog needs.

    `keep_suppressed` records suppressed findings instead of dropping them,
    tagged, for `kb debt` -- which reports a suppression as the decision it is
    rather than letting it disappear. They are still counted in `suppressed`
    and still excluded from `errors` / `warnings`, so no caller's exit code
    moves because of it.

    `scope` restricts the report to one part of the graph; anything outside it
    is counted in `out_of_scope` rather than dropped silently.
    """
    res = Result()
    res.thresholds = thresholds(g, overrides)
    want = set(rules) if rules else None

    def add(rule, who, msg, item=None, ref=None):
        """One finding. `item` is the owner when `who` is an item id, and the
        two are not redundant: `who` is what the reader and the suppression
        key see, `item` is where an item-owned exemption is stored."""
        if want and rule not in want:
            return
        if scope is not None and not scope.covers(who):
            # Counted before the suppression check, so a scoped run's
            # "suppressed" number is about this scope and not the store.
            res.out_of_scope += 1
            return
        if rule not in UNSUPPRESSIBLE:
            entry = (item.suppression(rule, ref) if item is not None
                     else g.lint.suppression(who, rule))
            if entry is not None and not _basis_moved(g, item, entry, rule, ref):
                res.suppressed += 1
                if not keep_suppressed:
                    return
                res.findings.append(Finding(rule, who, msg, suppressed=True,
                                            ref=ref))
                return
        res.findings.append(Finding(rule, who, msg, ref=ref))

    _graph_rules(g, add, res, ignore_thresholds)
    _suppression_rules(g, add, check_files)
    if check_files:
        _file_rules(g, add, res)
    return res


# kb:begin lint.basis-mismatch-suspends
def _basis_moved(g, it, entry, rule, ref):
    """True when the evidence this exemption was granted against has changed.

    `kb suppress` stamps a digest of exactly what the rule reads. When it stops
    matching, the reason was written about something that is no longer there,
    so the exemption is suspended: the finding returns at full severity and
    `suppress-stale` names the entry. Nothing is deleted -- the reason is the
    expensive part of a suppression, and re-confirming it is one command.

    A `None` basis now (a rule with no honest basis, or a ref index that no
    longer names a ref) is not a mismatch: there is nothing to compare, and
    the dangling index is `_suppression_rules`' business.
    """
    want = entry.get("basis")
    if not want or it is None:
        return False
    now = model_mod.basis(g, it, rule, ref)
    return bool(now) and now != want
# kb:end lint.basis-mismatch-suspends


# kb:begin lint.edge-kinds-are-judged-per-section
def section_edge_share(g):
    """(labelled, total, share) per section id.

    Per section rather than per store because a section is the scope a
    consumer team owns: the number a repo is judged on has to be one that repo
    can move. A section with no edges at all scores 1.0 -- there is nothing
    there to label, and a rule that fires on an empty set teaches its reader to
    skip the whole class.
    """
    tally = {}
    for it in g.items.values():
        lab, tot = tally.get(it.section, (0, 0))
        for e in it.affects:
            tot += 1
            if e.kind != "relates":
                lab += 1
        tally[it.section] = (lab, tot)
    return {sid: (lab, tot, (float(lab) / tot) if tot else 1.0)
            for sid, (lab, tot) in tally.items()}
# kb:end lint.edge-kinds-are-judged-per-section


def _graph_rules(g, add, res, ignore_thresholds=False):
    items = list(g.items.values())
    tagged = [i for i in items if i.keywords]
    th = res.thresholds
    shares = section_edge_share(g)
    # kb:begin lint.legacy-widens-the-known-section-gate
    # A renamed-away section id is not in `g.sections` any more, so without
    # `lint.legacy` a mention of it would fail the "known section" gate in
    # `_stale_mentions` and go unreported forever, not just at rename time.
    # Item and subsection retirements have no such gap -- their prefix's own
    # section stays live either way -- so only "section" entries widen this.
    sections = set(g.sections) | {x["name"] for x in g.lint.legacy
                                  if x["kind"] == "section"}
    # kb:end lint.legacy-widens-the-known-section-gate
    freq = {}
    for it in tagged:
        for k in it.keywords:
            freq[k.lower()] = freq.get(k.lower(), 0) + 1

    for it in items:
        seen = set()
        for e in it.affects:
            if e.to == it.id:
                add("self-edge", it.id, "affects itself", it)
            elif e.to not in g.items:
                add("edge-target", it.id,
                    "affects '%s', which is not in the KB" % e.to, it)
            if e.to in seen:
                add("edge-dup", it.id, "affects '%s' twice" % e.to, it)
            seen.add(e.to)

        cap = g.glossary.key_max(it.type)
        if cap and len(it.key) > cap:
            add("key-length", it.id,
                "key is %d chars, %d over the %s cap of %d"
                % (len(it.key), len(it.key) - cap, it.type, cap), it)
        if g.glossary.requires_modal(it.type) and not MODAL.search(it.key):
            add("key-modal", it.id,
                "type '%s' requires one of MUST / NEVER / ONLY / EXACTLY"
                % it.type, it)
        if it.summary and len(it.summary) > len(it.key):
            add("summary-longer", it.id,
                "summary is %d chars against a %d-char key -- it is meant to "
                "compress it" % (len(it.summary), len(it.key)), it)

        if g.degree(it.id) == 0 and g.glossary.requires_edges(it.type):
            add("orphan", it.id, "no edges in either direction", it)

        # Three rules where there was one: a suppression names a defect, and
        # "this keyword is capitalised" and "this keyword is a sentence" are
        # not the same decision to have made about the same item.
        lowered = [k.lower() for k in it.keywords]
        for k in it.keywords:
            if k != k.lower():
                add("keyword-case", it.id,
                    "keyword '%s' is not lowercase" % k, it)
            if len(k.split()) > 3:
                add("keyword-phrase", it.id,
                    "keyword '%s' is a phrase, not a term" % k, it)
        for k in sorted({k for k in lowered if lowered.count(k) > 1}):
            add("keyword-dup", it.id, "keyword '%s' is listed twice" % k, it)
        if not it.keywords:
            add("keyword-missing", it.id,
                "no keywords (%d of %d items have them)"
                % (len(tagged), len(items)), it)

        bare = sorted(e.to for e in it.affects if e.kind == "relates")
        if bare:
            lab, tot, share = shares.get(it.section, (0, 0, 1.0))
            if ignore_thresholds or share < th["edge_kind_share"]:
                # Every target, never an ellipsis: this list is the work, and a
                # report that truncates it cannot be worked through.
                add("edge-kind-missing", it.id,
                    "%d of %d outbound edge%s still 'relates' -- %s "
                    "(section [%s] labels %d of %d, %d%% against a %d%% floor)"
                    % (len(bare), len(it.affects),
                       "" if len(it.affects) == 1 else "s", DOT.join(bare),
                       it.section, lab, tot, round(100.0 * share),
                       round(100.0 * th["edge_kind_share"])), it)
            else:
                res.hidden += 1

        for tok in _stale_mentions(g, it, sections):
            add("stale-id-mention", it.id,
                "key names '%s', which is not an item in this KB" % tok, it)

        stale = _stale(it, th["stale_months"])
        if stale is not None:
            add("verified-stale", it.id,
                "last verified %s (%d days)" % (it.meta.get("verified"), stale),
                it)

        # kb:begin lint.marker-refs-are-the-exact-form
        # A symbol ref names a file and a symbol; nothing can check that the
        # symbol is still in the file, and `digest_ref` returns None for it by
        # design, so it never drifts and never carries a basis. It is the one
        # ref kind that records an intention rather than a fact. A marker ref
        # is the exact form -- the region is delimited in the source, so it can
        # be found, hashed and followed when it moves -- and this rule exists
        # to make the upgrade path visible on the items that already went to
        # the trouble of pointing somewhere.
        if not any(c.kind == "marker" for c in it.code):
            for n, c in enumerate(it.code):
                if c.kind != "symbol":
                    continue
                begin, end = markers.pair(c.path, it.id)
                add("code-inexact", it.id,
                    "%s -> %s is a symbol ref and cannot be verified; mark the "
                    "region instead: place `%s` / `%s` in %s, then "
                    "`kb code add %s marker %s`"
                    % (c.path, c.symbol, begin, end, c.path, it.id, c.path),
                    it, ref=n)
        # kb:end lint.marker-refs-are-the-exact-form

    # The subject is the keyword, not the keyword table: one `who` for every
    # over-broad keyword would be one suppression silencing all of them.
    for k, n in sorted(freq.items()):
        if tagged and n > th["keyword_broad_share"] * len(tagged):
            add("keyword-broad", "keyword:%s" % k,
                "tags %d of %d tagged items (%d%%) -- too broad to "
                "discriminate"
                % (n, len(tagged), round(100.0 * n / len(tagged))))

    for pid in sorted(g.subsections):
        n = len(g.subsection_members(pid))
        if n < th["subsection_min_items"]:
            # Lower than section-min-items by default: a subsection is a split
            # in progress and a legitimate halving can leave exactly two.
            add("subsection-min-items", "[%s]" % pid,
                "%d item%s -- a subsection groups at %d"
                % (n, "" if n == 1 else "s", th["subsection_min_items"]))

    for sid in sorted(g.sections):
        n = len(g.section_items(sid))
        if n < th["section_min_items"]:
            add("section-min-items", "[%s]" % sid,
                "%d item%s -- a section is worth its own heading at %d"
                % (n, "" if n == 1 else "s", th["section_min_items"]))


# kb:begin lint.who-is-the-suppression-key
def _suppression_rules(g, add, check_files):
    """Every suppression that no longer resolves.

    Four ways an exemption stops being one: its rule is not a rule in this
    build (a release renamed or split it), its `--ref` index no longer names a
    code ref, the evidence it was granted against has moved, or its `who`
    matches nothing in the store. All four leave an entry that protects
    nothing while still reading as a decision in force, which is worse than no
    entry at all -- and each one is still holding a reason worth re-reading.

    Two of the four are backstops rather than live paths. `validate` now
    range-checks `ref` and requires a `[section]` `who` to resolve (PLAN-1 3.6),
    which it can only do because `kb code rm` renumbers and `kb section rm`
    cascades (PLAN-1 3.10a/3.10b). On a store this build wrote, neither branch
    fires. They stay for the stores it did not write: a 4.0.0-era file, a merge
    resolved by hand, an editor. Reporting is the right response there --
    refusing to load would leave `kb` unable to repair its own store.
    """
    for it in g.items.values():
        for x in it.lint.get("ignore", []):
            rule, ref = x.get("rule"), x.get("ref")
            why = None
            if rule not in SEVERITY:
                why = "'%s' is not a rule in this build" % rule
            elif ref is not None and not 0 <= ref < len(it.code):
                why = ("--ref %d names nothing: the item has %d code ref%s"
                       % (ref, len(it.code),
                          "" if len(it.code) == 1 else "s"))
            elif x.get("basis"):
                field = model_mod.BASIS_FIELDS.get(rule, "")
                if field.startswith("ref-") and not check_files:
                    # A content basis cannot be recomputed with the tree out of
                    # reach, and "changed" is not an honest answer to "I did
                    # not look".
                    continue
                now = model_mod.basis(g, it, rule, ref)
                if now and now != x["basis"]:
                    why = ("the %s it was granted against has changed"
                           % field.replace("ref-identity", "code ref's path")
                                  .replace("ref-content", "code ref's content")
                                  .replace("key+summary", "key and summary"))
            if why:
                add("suppress-stale", it.id,
                    "%s suppression no longer applies -- %s; the reason was: %s"
                    % (rule, why, x.get("reason") or "?"))

    for x in g.lint.ignore:
        rule, who = x.get("rule"), x.get("who") or ""
        why = None
        if rule not in SEVERITY:
            why = "'%s' is not a rule in this build" % rule
        elif who.startswith("[") and who.endswith("]") \
                and who[1:-1] not in g.sections \
                and who[1:-1] not in g.subsections:
            why = "there is no section or subsection '%s'" % who[1:-1]
        if why:
            add("suppress-stale", who,
                "%s suppression no longer applies -- %s; the reason was: %s"
                % (rule, why, x.get("reason") or "?"))
# kb:end lint.who-is-the-suppression-key

# A root entry whose `who` is a `keyword:` or a path is deliberately not
# checked: a keyword that stopped being over-broad and a `.tsx` that stopped
# having a bad comment are both the exemption doing its job on a finding that
# has not returned yet, not a dangling entry.


def _stale(it, months):
    """Days past the staleness horizon, or None. `verified` is deliberately
    distinct from `modified`: editing an item is not re-checking it."""
    v = it.meta.get("verified")
    if not v or not months:
        return None
    try:
        age = (datetime.date.today() - datetime.date.fromisoformat(str(v))).days
    except ValueError:
        return None
    return age if age > months * 30 else None


def _stale_mentions(g, it, sections):
    """Ids cited in this item's key prose that no longer exist.

    Items cite each other inside their keys (`see model.prefix-resolution`).
    `kb rename` and `kb move` repoint edges but cannot touch prose, so every
    rename rots those mentions -- and with no rule for it the safest advice
    becomes "never rename", which is paralysis rather than advice.

    Recall is traded for precision deliberately. A token counts only when its
    first segment is a known section id AND its last is not a file extension;
    together those keep file paths, versions (`0.3.0-rc1`) and packages
    (`@anthropic-ai/sdk`) out of the findings. A noisy rule here is worse than
    no rule, because it trains the reader to skip the whole class.
    """
    seen, found = set(), []
    for tok in ID_TOKEN.findall(it.key or ""):
        if "." not in tok or tok in seen:
            continue
        seen.add(tok)
        if tok.rsplit(".", 1)[1] in FILE_SUFFIXES:
            continue
        if tok.split(".", 1)[0] not in sections:
            continue
        if tok in g.items:
            continue
        found.append(tok)
    return found


# ---------------------------------------------------------------- the tree


def _norm(path):
    return (path or "").replace("\\", "/").rstrip("/")


def _file_rules(g, add, res):
    root = g.root
    max_span = res.thresholds["max_span"]

    for it in g.items.values():
        for idx, ref in enumerate(it.code):
            p = _norm(ref.path)
            if not p or "*" in p:
                # A glob is a deliberate "this family of files"; there is
                # nothing to stat.
                continue
            if not os.path.exists(os.path.join(root, p.replace("/", os.sep))):
                add("code-path", it.id,
                    "%s does not exist -- if it moved: kb code mv %s <new-path>"
                    % (ref.path, p), it, idx)

    for it in g.items.values():
        for idx, ref in enumerate(it.code):
            if not ref.digest:
                # Never verified against content. `verified-stale` owns that
                # condition; two rules for one gap would double-count it.
                continue
            now = markers.digest_ref(root, ref, it.id)
            if now and now != ref.digest:
                add("code-drift", it.id,
                    "%s changed since it was verified on %s"
                    % (ref.path, it.meta.get("verified")), it, idx)

    regions, problems, texts = _scan_tree(root)
    res.scanned = len(texts)

    for path, line, iid, rule, msg in problems:
        # `who` is the id the marker names, not the file position: a line
        # number moves the moment somebody edits above it, and an exemption
        # keyed on one stops matching without saying so. The position stays in
        # the message, where it is still clickable. An unreadable file, and a
        # marker line too malformed to name an id, have none -- both report at
        # their path, which is a `who` the suppression key already allows.
        who = iid or path
        add(rule, who,
            ("%s:%d %s" % (path, line, msg)) if line and iid
            else (msg if iid is None else "%s %s" % (path, msg)),
            g.items.get(who))

    by_id = {}
    for r in regions:
        by_id.setdefault(r.item_id, []).append(r)

    for iid in sorted(by_id):
        if iid not in g.items:
            r = by_id[iid][0]
            add("marker-unknown", iid,
                "`kb:begin %s` at %s:%d names an item the KB does not have"
                % (iid, r.path, r.start))

    for it in g.items.values():
        mine = by_id.get(it.id, [])
        # The marker ref that claims each file, so a region's finding can be
        # silenced by `--ref N` without covering the item's other regions.
        marker_idx = {}
        for idx, c in enumerate(it.code):
            if c.kind == "marker":
                marker_idx.setdefault(_norm(c.path), idx)

        for idx, ref in enumerate(it.code):
            if ref.kind != "marker":
                continue
            here = [r for r in mine if _norm(r.path) == _norm(ref.path)]
            if here:
                continue
            elsewhere = sorted({r.path for r in mine})
            if len(elsewhere) == 1:
                # Unambiguous: the region moved to exactly one other file, so
                # --fix can follow it. More than one and which is meant is a
                # judgement call, so it stays a plain error.
                res.relocations[(it.id, idx)] = elsewhere[0]
                add("marker-missing", it.id,
                    "no `kb:begin %s` in %s -- found in %s (kb lint --fix "
                    "will repoint it)" % (it.id, ref.path, elsewhere[0]),
                    it, idx)
            else:
                add("marker-missing", it.id,
                    "no `kb:begin %s` in %s%s" % (it.id, ref.path,
                    (" -- found in %s" % ", ".join(elsewhere)) if elsewhere else ""),
                    it, idx)

        for r in mine:
            ridx = marker_idx.get(_norm(r.path))
            if r.span > max_span:
                add("marker-span", it.id,
                    "%s:%d-%d spans %d lines -- `kb:end` has probably drifted"
                    % (r.path, r.start, r.end, r.span), it, ridx)
            body = texts.get(r.path, [])[r.start:r.end - 1]
            if not any(_is_code(x) for x in body):
                add("marker-empty", it.id,
                    "%s:%d-%d contains no code" % (r.path, r.start, r.end),
                    it, ridx)

    for path, lines in texts.items():
        if not path.endswith(".tsx"):
            continue
        for p, line, msg in markers.jsx_risk(path, "\n".join(lines)):
            # No id exists for a stray `//` in JSX children, so the file is the
            # subject and one exemption covers the file.
            add("marker-jsx", p, "%s:%d %s" % (p, line, msg))


def _is_code(line):
    s = line.strip()
    return bool(s) and not s.startswith(COMMENT_START)


# kb:begin lint.tree-scan
def _scan_tree(root):
    """Every marker in the tree, plus the text of the files that hold one.

    Scanning the tree rather than only the paths items claim is what lets
    `marker-unknown` and the relocation hint exist at all: both are questions
    about markers the store does not know where to look for.
    """
    regions, problems, texts = [], [], {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            if os.path.splitext(fn)[1] not in markers.COMMENT_FORMS:
                continue
            full = os.path.join(dirpath, fn)
            rel = _norm(os.path.relpath(full, root))
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError as e:
                # No id names an unreadable file, so it reports at its path,
                # exactly like every other problem `_file_rules` cannot
                # attribute to one item.
                problems.append((rel, 0, None, "marker-unpaired",
                                 "unreadable: %s" % e))
                continue
            if "kb:" not in text:
                continue
            rs, ps = markers.scan_text(text, rel)
            regions.extend(rs)
            problems.extend(ps)
            texts[rel] = text.splitlines()
    return regions, problems, texts
# kb:end lint.tree-scan


# -------------------------------------------------------------- the command


def cmd_lint(g, a):
    if a.list:
        for name, sev, desc in RULES:
            th = _RULE_THRESHOLD.get(name)
            note = "" if th is None else "   [%s = %s]" % (
                th, g.lint.threshold(th))
            out("%-6s %-22s %s%s" % (sev, name, desc, note))
        out("\n%d rules · only errors move the exit code (--strict promotes "
            "warnings) · a threshold in brackets is what that rule measures "
            "against, settable with `kb thresholds`" % len(RULES))
        return
    ids = list(a.ids or [])
    if a.surfaced_file:
        ids.extend(surfaced_ids(a.surfaced_file))
    try:
        scope = build_scope(g, ids, a.section)
    except KeyError as e:
        write_mod.die("not in this KB: %s" % e.args[0])
    res = run(g, check_files=not a.no_files, rules=a.rule,
              overrides=overrides_from(a), scope=scope,
              ignore_thresholds=a.all,
              keep_suppressed=bool(a.all or a.show_suppressed))
    if a.json:
        return _json(g, res, a.errors_only)
    if a.fix:
        return _fix(g, a, res)

    for f in res.sorted():
        if a.errors_only and f.severity != ERROR:
            continue
        out(str(f))
    if res.findings:
        out("")
    out(_summary(g, res))
    if res.errors or (a.strict and res.warnings):
        raise SystemExit(1)


def _summary(g, res):
    edges = sum(len(i.affects) for i in g.items.values())
    labelled = sum(1 for i in g.items.values()
                   for e in i.affects if e.kind != "relates")
    marker_refs = sum(1 for i in g.items.values()
                      for c in i.code if c.kind == "marker")
    bits = ["%d items" % len(g.items),
            "%d error%s" % (len(res.errors), "" if len(res.errors) == 1 else "s"),
            "%d warning%s" % (len(res.warnings),
                              "" if len(res.warnings) == 1 else "s")]
    if res.suppressed:
        bits.append("%d suppressed" % res.suppressed)
    if res.hidden:
        # Never a silent omission: the number is here even when it is the only
        # thing standing between this report and the whole backlog, and
        # `--all` is what prints them.
        bits.append("%d hidden by thresholds (--all shows them)" % res.hidden)
    if res.out_of_scope:
        bits.append("%d outside this scope" % res.out_of_scope)
    bits.append("edge kinds %d/%d (%.0f%%)"
                % (labelled, edges, 100.0 * labelled / max(1, edges)))
    bits.append("%d marker refs" % marker_refs)
    return DOT.join(bits)


def _json(g, res, errors_only):
    import json
    rows = []
    for f in res.sorted():
        if errors_only and f.severity != ERROR:
            continue
        row = {"rule": f.rule, "severity": f.severity, "who": f.who,
               "msg": f.msg, "suppressed": f.suppressed}
        if f.ref is not None:
            row["ref"] = f.ref
        # The exact call, quoted for a POSIX shell, with the one field the
        # tool cannot supply left empty. Generated from the finding rather
        # than written by hand: a drift between what lint prints and what
        # suppress accepts is the bug this whole field exists to prevent.
        if f.rule in UNSUPPRESSIBLE:
            row["suppress"] = None
            row["resolve"] = ("kb verify %s" % f.who if f.rule == "code-drift"
                              else "lift the entry this reports, or re-record "
                                   "it with kb suppress")
        else:
            row["suppress"] = (
                "kb suppress --who %s --rule %s%s --reason \"\""
                % (_shq(f.who), f.rule,
                   "" if f.ref is None else " --ref %d" % f.ref))
        rows.append(row)
    out(json.dumps({"findings": rows,
                    "errors": len(res.errors),
                    "warnings": len(res.warnings),
                    "suppressed": res.suppressed,
                    "hidden": res.hidden,
                    "out_of_scope": res.out_of_scope,
                    "thresholds": res.thresholds}, indent=2))


def _shq(s):
    """Quote a `who` for a shell. `[section]`, `keyword:x` and a bare id all
    reach here, and the first two are glob and word characters."""
    return s if re.match(r"^[A-Za-z0-9._/-]+$", s or "") else "'%s'" % s


# kb:begin lint.fix-is-store-only
def _fix(g, a, res):
    """Mechanical repairs only, and only to the store -- `kb` never writes
    source files (invariant 7), so a drifted marker is repointed in the KB,
    never moved in the code."""
    if not res.relocations:
        out("nothing to fix (%d error%s, %d warning%s -- none mechanical)"
            % (len(res.errors), "" if len(res.errors) == 1 else "s",
               len(res.warnings), "" if len(res.warnings) == 1 else "s"))
        return
    touched = []
    for (iid, idx), path in sorted(res.relocations.items()):
        it = g.items[iid]
        write_mod.say("%s: marker ref %s → %s" % (iid, it.code[idx].path, path))
        it.code[idx].path = path
        touched.append(it)
    write_mod.touch(g, *touched)
    write_mod.commit(g, a, "lint --fix")
# kb:end lint.fix-is-store-only
