"""`kb ctx` -- assemble a complete working context in one call.

This is the command the whole design is for. It replaces "grep the KB,
slice-read it, fan out by hand" with a single question, and it is the
mandatory first step before touching any file:

    kb ctx --path src/lib/api.ts        # everything governing a file
    kb ctx auth.single-fetch-path       # a rule and its neighbourhood
    kb ctx --query "credit balance"     # relevance-ranked entry

Directives are always included: they override every other item, so a context
that omits them is a context that can talk you into violating one.
"""

from collections import defaultdict, deque

from . import markers
from .read import DOT, code_line, tag, trunc
from .search import score

DIRECTIVE_TYPE = "directive"


def _walk(g, roots, direction, depth, kinds):
    """BFS recording how each item was reached: (depth, kind, arrow, via)."""
    meta = {}
    q = deque()
    for r in roots:
        if r.id not in meta:
            meta[r.id] = (0, None, None, None)
            q.append(r.id)
    order = [r.id for r in roots]
    while q:
        iid = q.popleft()
        d = meta[iid][0]
        if d >= depth:
            continue
        for nid, kind, arrow in g.neighbors(iid, direction, kinds):
            if nid not in g.items or nid in meta:
                continue
            meta[nid] = (d + 1, kind, arrow, iid)
            order.append(nid)
            q.append(nid)
    return order, meta


def _item_block(g, it, width, show_code=True):
    lines = ["# %s  [%s]  %s" % (it.id, it.section, tag(it))]
    pid = g.subsection_of(it.id)
    if pid is not None:
        sibs = [s.id for s in g.subsection_members(pid) if s.id != it.id]
        # Without this a session repairs one child and leaves an adjacent one
        # asserting the opposite -- the failure the grouping exists to prevent.
        lines.append("  subsection: %s (%d sibling%s)%s"
                     % (pid, len(sibs), "" if len(sibs) == 1 else "s",
                        ("  " + DOT.join(sibs[:6])) if sibs else ""))
    lines.append("  " + (it.key if not width else trunc(it.key, width)))
    if show_code and it.code:
        for n, ref in enumerate(it.code):
            lines.append("  %-4s %s" % ("code" if n == 0 else "", code_line(ref)))
    by = defaultdict(list)
    for e in it.affects:
        by[e.kind].append(e.to)
    for kind in g.glossary.edge_kinds:
        if kind in by:
            lines.append("  → %-14s %s" % (kind, DOT.join(sorted(by[kind]))))
    inb = defaultdict(list)
    for src, kind in g.inbound.get(it.id, []):
        inb[kind].append(src)
    for kind in g.glossary.edge_kinds:
        if kind in inb:
            lines.append("  ← %-14s %s"
                         % (g.glossary.inverse(kind).lower(),
                            DOT.join(sorted(inb[kind]))))
    return lines


def _snippets(g, it, max_lines, plan, problems):
    lines = []
    mine = plan.get(it.id, [])
    for r, shown_under in mine:
        if shown_under:
            lines.append("  --- %s -- shown above under %s"
                         % (markers.region_header(r), shown_under))
            continue
        lines.append("  --- " + markers.region_header(r))
        for s in markers.snippet(g.root, r, max_lines):
            lines.append("    " + s)
    if not mine:
        unmarked = [c for c in it.code if c.kind != "marker"]
        if unmarked:
            begin, end = markers.pair(unmarked[0].path, it.id)
            lines.append("  --- no marker on %s -- place `%s` / `%s` by hand"
                         % (unmarked[0].path, begin, end))
    for path, n, msg in problems.get(it.id, []):
        lines.append("  !!! %s:%d %s" % (path, n, msg))
    return lines


def build(g, a, width, max_neighbors):
    """Returns blocks in drop order. The head, the root items and the
    directives are never dropped: a context missing those is not a smaller
    context, it is a wrong one."""
    roots, header = _roots(g, a)
    if roots is None:
        return {"head": header, "roots": [], "neighbourhood": [],
                "sections": [], "directives": []}

    lines = []
    kinds = set(a.kind) if a.kind else None
    order, meta = _walk(g, roots, a.direction, a.depth, kinds)
    rootids = {r.id for r in roots}

    # One plan for every root, so a range two roots both mark is printed once
    # and cross-referenced under the rest.
    code_plan, code_problems = ({}, {})
    if a.code:
        code_plan, code_problems = markers.merged_plan(g.root, roots)
    for it in roots:
        lines.extend(_item_block(g, it, 0))
        if a.code:
            lines.extend(_snippets(g, it, a.max_lines, code_plan,
                                   code_problems))
        lines.append("")
    root_lines = lines

    lines = []
    rest = [i for i in order if i not in rootids]
    if rest:
        lines.append("-- neighbourhood, depth ≤%d (%d) -- re-verify each Key "
                     "after the edit" % (a.depth, len(rest)))
        for iid in rest[:max_neighbors]:
            d, kind, arrow, _via = meta[iid]
            n = g.items[iid]
            lines.append("  d%d %s %-12s %-38s %-11s %s"
                         % (d, arrow, kind, n.id, tag(n), trunc(n.key, width)))
        if len(rest) > max_neighbors:
            lines.append("  … %d more (raise --budget or narrow with --kind)"
                         % (len(rest) - max_neighbors))
        lines.append("")
    neighbourhood = lines

    lines = []
    secs = []
    for it in roots:
        if it.section not in secs:
            secs.append(it.section)
    lines.append("-- sections in play")
    for sid in secs:
        sec = g.sections[sid]
        rel = g.related_sections(sid)
        lines.append("  [%s] (%d)%s"
                     % (sec.label, len(g.section_items(sid)),
                        ("  related " + DOT.join("[%s] (%d)" % (s, n)
                                                 for s, n in rel[:6]))
                        if rel else ""))
        for hi in g.highlights(sid):
            if hi.id in rootids:
                continue
            lines.append("    * %s — %s" % (hi.id, trunc(hi.headline, width)))
    lines.append("")
    sections = lines

    directives = []
    if not a.no_directives:
        ds = [i for i in g.items.values()
              if i.type == DIRECTIVE_TYPE and i.id not in rootids]
        if ds:
            directives.append("-- directives (override every item above)")
            for d in ds:
                directives.append("  * %s — %s" % (d.id, trunc(d.headline, width)))

    return {"head": header, "roots": root_lines, "neighbourhood": neighbourhood,
            "sections": sections, "directives": directives}


def _roots(g, a):
    """(roots, header lines) -- or (None, message) when nothing matched."""
    if a.path:
        hits = g.match_paths(a.path)
        if not hits:
            return None, [
                "no item is anchored at %s" % a.path,
                "",
                "That means no rule is recorded for this file -- not that none",
                "applies. Before editing, try:",
                "  kb ctx --query \"<what the file does>\"",
                "  kb dup \"<the rule you are about to rely on>\"",
            ]
        roots, seen = [], set()
        for it, ref, how in hits:
            if it.id not in seen:
                seen.add(it.id)
                roots.append(it)
        return roots, ["== %d items own rules for %s" % (len(roots), a.path), ""]

    if a.query:
        hits = score(g.items.values(), a.query)
        if not hits:
            return None, ["nothing matches %r" % a.query]
        roots = [it for _s, it in hits[:a.n]]
        return roots, ["== %d items match %r (best first)"
                       % (len(roots), a.query), ""]

    roots = [g.get(i) for i in a.ids]
    return roots, ["== %s" % ", ".join(r.id for r in roots), ""]


# kb:begin ctx.budget-degradation
ORDER = ("head", "roots", "neighbourhood", "sections", "directives")

# Progressively cheaper renders. Neighbour key text goes first, then the
# length of the neighbour list -- both are navigational aids, unlike the
# root items and the directives.
STEPS = ((110, 40), (60, 20), (60, 8), (0, 0))


def cmd_ctx(g, a):
    if not a.ids and not a.path and not a.query:
        return _die("kb ctx needs an item id, --path, or --query")

    blocks = build(g, a, *STEPS[0])
    text = _join(blocks)
    if a.budget:
        for width, maxn in STEPS[1:]:
            if len(text) <= a.budget:
                break
            blocks = build(g, a, width, maxn)
            if not maxn:
                # Last resort: drop the navigational blocks outright rather
                # than cut the tail off and lose the directives with it.
                blocks["neighbourhood"] = [
                    "-- neighbourhood omitted for --budget; "
                    "`kb impact %s` for the full closure"
                    % (a.ids[0] if a.ids else "<id>"), ""]
                blocks["sections"] = []
            text = _join(blocks)
    print(text)
# kb:end ctx.budget-degradation


def _join(blocks):
    lines = []
    for k in ORDER:
        lines.extend(blocks.get(k, []))
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _die(msg):
    import sys
    sys.stderr.write("kb: %s\n" % msg)
    return 2
