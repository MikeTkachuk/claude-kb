"""The per-item defect view, and the regression ratchet built on it.

`kb lint` answers "what is wrong with this store?" one finding per line. That
is the right shape for a gate and the wrong shape for repair: the obligation a
session carries is *per item* -- every item its queries surfaced must be
verified or repaired -- and a session repairing one item wants every defect of
that item together, with the number the repair is graded on.

`kb debt` is that view. It runs exactly the rules `kb lint` runs, through the
same `lint.run()`; there is no second rule table and no rule that only one of
the two commands knows about.

The ratchet is the other half. Warnings must not gate on absolute count: on a
migrated store most items warn, and `hooks/pre-commit` says in as many words
that a gate failing on those "would be switched off in a day". What can gate is
*regression*, and regression needs a recorded baseline -- per rule, because a
split legitimately raises `key-length` while cutting the debt, plus the one
number that moves the other way when it does: total over-cap excess.

Two rises are deliberately not regressions, and both are why a naive
`sum(warnings) > baseline` ratchet is unusable here:

  threshold-moved  a rule measuring against a different number than the
               baseline recorded, because someone ran `kb thresholds`. The
               count moved by decision, not by decay.
  new-rule     a rule the recorded baseline predates. It cannot have regressed
               against a baseline that never measured it.
  rule-gone    a rule the baseline has and this build does not: renamed or
               split, with its findings now counted under the new names.

All three are reported, all three exit 0, and all three name the `kb baseline
write` that records the new floor.
"""

import datetime
import json
import os

from . import lint as lint_mod
from . import model as model_mod
from . import store as store_mod
from .read import DOT, out

BASELINE_FILE = ".kb-baseline.json"


def die(msg, code=2):
    import sys
    sys.stderr.write("kb: %s\n" % msg)
    raise SystemExit(code)


# ------------------------------------------------------------- the debt view


def _reason(it, rule):
    """The reason recorded with a suppression, for the `(suppressed: …)` tag.

    Read here rather than exposed on `Item` because this is the only caller:
    every other consumer of `lint.ignore` asks the yes/no question that
    `Item.suppressed()` already answers.
    """
    for x in it.lint.get("ignore", []):
        if x.get("rule") == rule:
            return x.get("reason") or ""
    return ""


# kb:begin debt.attribution-is-the-who-field
def collect(g, res, ids=None):
    """Regroup a lint `Result` into (rows, graph_level).

    A finding belongs to an item when its `who` **is** an item id. That is the
    whole test, and it is deliberately not a list of rule names: `who` is
    already each rule's own statement about what it is talking about, and
    several rules point at something that is not an item -- `section-min-items`
    at `[section]`, `subsection-min-items` at `[prefix]`, `keyword-broad` at
    `§keywords`, the marker file rules at `path:line`. A per-item view cannot
    attribute those, and inventing an owner for them is where a report starts
    lying. They go in their own trailing block instead.
    """
    per, graph = {}, []
    for f in res.sorted():
        if f.who in g.items:
            per.setdefault(f.who, []).append(f)
        else:
            graph.append(f)

    if ids is None:
        # Whole store: only items that have something to say.
        chosen = sorted(set(per) | {i.id for i in g.items.values()
                                    if g.key_excess(i)})
    else:
        chosen = list(ids)

    rows = []
    for iid in chosen:
        it = g.items[iid]
        rows.append({
            "id": it.id,
            "type": it.type,
            "key_chars": len(it.key),
            "cap": g.glossary.key_max(it.type),
            "excess": g.key_excess(it),
            "halve_to": g.halve_to(it),
            "findings": per.get(iid, []),
        })
    # Worst first: the metric the obligation is graded on, then the sheer
    # number of defects, then the id so the order is total and stable.
    rows.sort(key=lambda r: (-r["excess"], -len(r["findings"]), r["id"]))
    return rows, graph
# kb:end debt.attribution-is-the-who-field


def report(g, res, rows, graph, no_files):
    """The machine-readable form. `kb baseline write` consumes exactly
    `rules`, `excess_total`, `thresholds` and `no_files`; the rest is for
    people and for scripts, and adding a key to it is not a breaking change."""
    counts = {name: 0 for name, _, _ in lint_mod.RULES}
    for f in res.findings:
        if not f.suppressed:
            counts[f.rule] = counts.get(f.rule, 0) + 1
    return {
        "store": g.path,
        "schema": g.schema,
        "generated": datetime.date.today().isoformat(),
        "run": g.run,
        "no_files": bool(no_files),
        "excess_total": g.excess_total(),
        "items_over_cap": sum(1 for i in g.items.values() if g.key_excess(i)),
        "rules": counts,
        # What this run measured against, so a count that moved because a
        # threshold moved can be told from one that moved because the graph
        # got worse. Replaces the adoption-gate state, which no longer exists.
        "thresholds": dict(res.thresholds),
        "items": [{
            "id": r["id"], "type": r["type"], "key_chars": r["key_chars"],
            "cap": r["cap"], "excess": r["excess"], "halve_to": r["halve_to"],
            "findings": [{"rule": f.rule, "severity": f.severity,
                          "msg": f.msg, "suppressed": f.suppressed}
                         for f in r["findings"]],
        } for r in rows],
        "graph": [{"rule": f.rule, "severity": f.severity, "who": f.who,
                   "msg": f.msg} for f in graph],
    }


def _finding_line(g, r, f):
    msg = f.msg
    if f.rule == "key-length" and r["halve_to"] is not None:
        # The one number the surfaced-set obligation is graded on, which no
        # lint rule has any reason to compute.
        msg += " -- halve to <= %d" % r["halve_to"]
    if f.suppressed:
        why = _reason(g.items[r["id"]], f.rule)
        msg += "  (suppressed: %s)" % why if why else "  (suppressed)"
    return "  %-18s %s" % (f.rule, msg)


def cmd_debt(g, a):
    ids = _selection(g, a)
    res = lint_mod.run(g, check_files=not a.no_files, rules=a.rule,
                       keep_suppressed=True,
                       overrides=lint_mod.overrides_from(a))
    rows, graph = collect(g, res, ids)
    if ids is not None:
        # A scoped report drags in no other section's graph-level findings.
        # `collect` cannot do this: it is told ids, and a `[section]` finding
        # is not one -- the mapping from an id to the section it belongs to is
        # this command's business, not the attribution rule's.
        secs = {i.split(".", 1)[0] for i in ids}
        graph = [f for f in graph
                 if f.who.startswith("[") and f.who.endswith("]")
                 and f.who[1:-1].split(".", 1)[0] in secs]

    if a.json:
        out(json.dumps(report(g, res, rows, graph, a.no_files),
                       indent=2, sort_keys=True, ensure_ascii=False))
        return

    for r in rows:
        head = "%-38s %-13s" % (r["id"], r["type"])
        if r["excess"]:
            head += "excess %d" % r["excess"]
        out(head.rstrip())
        for f in r["findings"]:
            out(_finding_line(g, r, f))
        if not r["findings"]:
            out("  (no findings)")
        out("")

    if graph:
        out("-- graph-level, not attributable to one item")
        for f in graph:
            out("  %-18s %-24s %s" % (f.rule, f.who, f.msg))
        out("")

    out(_summary(g, res, rows, graph))


def _summary(g, res, rows, graph):
    over = [r for r in rows if r["excess"]]
    shown = sum(len(r["findings"]) for r in rows) + len(graph)
    bits = ["%d item%s" % (len(rows), "" if len(rows) == 1 else "s"),
            "%d finding%s" % (shown, "" if shown == 1 else "s")]
    if res.suppressed:
        bits.append("%d suppressed" % res.suppressed)
    bits.append("excess %d chars over %d item%s"
                % (sum(r["excess"] for r in over), len(over),
                   "" if len(over) == 1 else "s"))
    bits.append("store total %d" % g.excess_total())
    return DOT.join(bits)


def _selection(g, a):
    """The ids to report on, or None for the whole store.

    An unknown id is an error rather than an empty block: the surfaced set is
    produced by a previous `kb` command, so an id in it that the store does not
    have means the file is stale or the wrong one, and silently reporting
    "nothing wrong" about it is the failure this command exists to prevent.
    """
    ids = list(a.ids or [])
    if a.surfaced_file:
        ids.extend(lint_mod.surfaced_ids(a.surfaced_file))
    for sid in (getattr(a, "section", None) or ()):
        try:
            sec = g.section_of(sid)
        except KeyError:
            die("no section '%s' -- `kb index` lists them" % sid)
        ids.extend(i.id for i in g.section_items(sec.id))
    if not ids:
        return None
    resolved, unknown = [], []
    for iid in ids:
        try:
            resolved.append(g.get(iid).id)
        except KeyError:
            unknown.append(iid)
    if unknown:
        die("not in this KB: %s" % ", ".join(sorted(set(unknown))))
    # Dedupe, keep first-seen order out of the picture -- `collect` sorts.
    return sorted(set(resolved))


# ----------------------------------------------------------------- the ratchet


def baseline_path(g, given):
    """Relative paths resolve against the store's directory, not the working
    directory: the store itself is found by walking upward, so a ratchet that
    depended on where you happened to stand would compare against a different
    file from one shell to the next."""
    p = given or BASELINE_FILE
    if os.path.isabs(p):
        return p
    return os.path.join(os.path.dirname(os.path.abspath(g.path)), p)


def snapshot(g, a):
    """The subset of `kb debt --json` a baseline records."""
    res = lint_mod.run(g, check_files=not a.no_files, keep_suppressed=True,
                       overrides=lint_mod.overrides_from(a))
    rows, graph = collect(g, res, None)
    r = report(g, res, rows, graph, a.no_files)
    return {"rules": r["rules"], "excess_total": r["excess_total"],
            "thresholds": r["thresholds"], "no_files": r["no_files"]}


def cmd_baseline(g, a):
    if a.verb != "write":
        die("unknown verb '%s' -- the only one is: kb baseline write" % a.verb)
    path = baseline_path(g, a.file)
    payload = dict(snapshot(g, a))
    payload["recorded"] = datetime.date.today().isoformat()
    payload["run"] = g.run
    if a.notes:
        payload["notes"] = a.notes
    text = store_mod.dumps(payload)
    if a.dry_run:
        out(text.rstrip())
        out("\n(dry run -- %s not written)" % path)
        return
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    out("wrote %s -- %d rules, excess %d"
        % (path, len(payload["rules"]), payload["excess_total"]))
    out("commit it: the ratchet compares against nothing on a fresh clone.")


# rule -> the threshold it measures against. Inverted from the model's table so
# the ratchet cannot name a threshold the rules do not read.
RULE_THRESHOLD = {rule: name
                  for name, rule in model_mod.THRESHOLD_RULES.items()}


# kb:begin debt.ratchet-compares-per-rule
def compare(base, cur):
    """(regressions, notes, improvements).

    Per rule, never a single sum of warnings: splitting one oversized item into
    several raises `key-length` while cutting `excess_total`, and that has to
    pass. It only can if both are tracked separately.
    """
    reg, notes, better = [], [], []
    b_rules = base.get("rules") or {}
    c_rules = cur["rules"]
    b_th = base.get("thresholds") or {}
    c_th = cur["thresholds"]
    be = base.get("excess_total", 0)
    ce = cur["excess_total"]

    for rule in sorted(set(b_rules) | set(c_rules)):
        if rule not in c_rules:
            # A rule this build no longer has. It cannot have improved: it was
            # renamed or split, and its findings are now counted under other
            # names -- as `new-rule` lines in this same report.
            b = b_rules[rule]
            if b:
                notes.append(("rule-gone", rule,
                              "%d finding%s from a rule this build no longer "
                              "has -- renamed or split"
                              % (b, "" if b == 1 else "s")))
            continue
        c = c_rules[rule]
        if rule not in b_rules:
            if c:
                notes.append(("new-rule", rule,
                              "%d finding%s from a rule this baseline predates"
                              % (c, "" if c == 1 else "s")))
            continue
        b = b_rules[rule]
        if c > b:
            name = RULE_THRESHOLD.get(rule)
            if name and name in b_th and b_th[name] != c_th.get(name):
                # The rule is measuring something else than it was. That is a
                # decision someone recorded with `kb thresholds`, not decay.
                notes.append(("threshold-moved", rule,
                              "%d -> %d findings; %s moved %s -> %s"
                              % (b, c, name, b_th[name], c_th.get(name))))
            elif rule == "key-length" and ce < be:
                # The one rule whose count is subordinate to another number,
                # and the reason `excess_total` is tracked at all. Splitting a
                # 17,950-char item into eight adds seven `key-length` findings
                # and removes two thirds of the debt; a ratchet that read the
                # count alone would forbid the exact repair the mechanism
                # exists to produce. A NEW over-cap item raises both, so it is
                # still caught here.
                notes.append(("split", rule,
                              "%d -> %d findings, but excess fell %d -> %d -- "
                              "a split raises the count while paying the debt"
                              % (b, c, be, ce)))
            else:
                reg.append((rule, b, c))
        elif c < b:
            better.append((rule, b, c))

    if ce > be:
        reg.append(("excess_total", be, ce))
    elif ce < be:
        better.append(("excess_total", be, ce))
    return reg, notes, better
# kb:end debt.ratchet-compares-per-rule


def cmd_lint_baseline(g, a):
    """`kb lint --baseline [FILE]`. Routed here from `cli` rather than from
    `lint.cmd_lint`, because this module imports `lint` and the reverse import
    would be a cycle."""
    if a.rule or a.fix or a.list:
        die("--baseline reports the whole rule table: it cannot be combined "
            "with --rule, --fix or --list")
    path = baseline_path(g, None if a.baseline is True else a.baseline)
    if not os.path.exists(path):
        die("no baseline at %s -- record one with:  kb baseline write" % path)
    try:
        with open(path, encoding="utf-8") as f:
            base = json.load(f)
    except (OSError, ValueError) as e:
        die("cannot read %s: %s" % (path, e))

    cur = snapshot(g, a)
    if bool(base.get("no_files")) != bool(cur["no_files"]):
        die("baseline was recorded with --no-files %s and this run used %s; "
            "the file rules would be compared against nothing"
            % (_yn(base.get("no_files")), _yn(cur["no_files"])))

    reg, notes, better = compare(base, cur)
    for rule, b, c in reg:
        out("regressed   %-22s %d -> %d" % (rule, b, c))
    for kind, rule, msg in notes:
        out("%-15s %-22s %s" % (kind, rule, msg))
    for rule, b, c in better:
        out("improved    %-22s %d -> %d" % (rule, b, c))
    if not (reg or notes or better):
        out("unchanged against %s" % os.path.basename(path))
    out("")

    bits = ["baseline %s" % (base.get("recorded") or "?")]
    if base.get("run"):
        bits.append("run %s" % base["run"])
    bits.append("%d regression%s" % (len(reg), "" if len(reg) == 1 else "s"))
    bits.append("excess %d -> %d" % (base.get("excess_total", 0),
                                     cur["excess_total"]))
    out(DOT.join(bits))
    if notes and not reg:
        out("record the new floor:  kb baseline write --notes \"…\"")
    if reg:
        raise SystemExit(1)


def _yn(v):
    return "on" if v else "off"
