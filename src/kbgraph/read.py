"""Read commands. These operate on the model, never on a file, so none of
them cares that the store is JSON and the render is markdown."""

import os
import re
import sys
from collections import defaultdict

from . import markers
from .search import build_idf, item_terms, score, tokens

DOT = " · "


def out(s=""):
    sys.stdout.write(s + "\n")


def trunc(s, n):
    """n <= 0 means no limit -- callers use 0 for "print it in full"."""
    s = " ".join((s or "").split())
    if n <= 0 or len(s) <= n:
        return s
    return s[:n - 1] + "…"


def tag(it):
    return "%s/%s" % (it.type, it.status) if it.status != "active" else it.type


def code_line(ref):
    s = ref.path
    if ref.symbol:
        s += " → " + ref.symbol
    if ref.part:
        s += " [%s]" % ref.part
    if ref.kind == "marker":
        s += " (marker)"
    elif ref.kind == "file":
        s += " (file)"
    return s


def edge_lines(g, it, indent="  "):
    """Outbound then inbound, grouped by kind, in glossary order."""
    lines = []
    by = defaultdict(list)
    for e in it.affects:
        by[e.kind].append(e.to)
    for kind in g.glossary.edge_kinds:
        if kind in by:
            lines.append("%s→ %-14s %s"
                         % (indent, kind, DOT.join(sorted(by[kind]))))
    inb = defaultdict(list)
    for src, kind in g.inbound.get(it.id, []):
        inb[kind].append(src)
    for kind in g.glossary.edge_kinds:
        if kind in inb:
            lines.append("%s← %-14s %s"
                         % (indent, g.glossary.inverse(kind).lower(),
                            DOT.join(sorted(inb[kind]))))
    return lines


def show_item(g, it, width=0, code=True):
    out("### %s  [%s]  %s" % (it.id, it.section, tag(it)))
    out("  " + (it.key if not width else trunc(it.key, width)))
    if code and it.code:
        for n, ref in enumerate(it.code):
            out("  %s %d %s" % ("code" if n == 0 else "    ", n,
                                code_line(ref)))
    for line in edge_lines(g, it):
        out(line)
    if it.keywords:
        out("  kw   " + DOT.join(it.keywords))


def line_item(g, it, width=140, mark=""):
    return "  %s%-40s %-13s %s" % (mark, it.id, tag(it), trunc(it.key, width))


# ------------------------------------------------------------------ commands


def cmd_item(g, a):
    for iid in a.ids:
        show_item(g, g.get(iid))
        out()


def cmd_meta(g, a):
    for iid in a.ids:
        it = g.get(iid)
        m = it.meta
        out("%s  created %s · modified %s · verified %s · run %s"
            % (it.id, m.get("created", "?"), m.get("modified", "?"),
               m.get("verified", "?"), m.get("run") or "-"))
        for x in it.lint.get("ignore", []):
            label = x.get("rule") if x.get("ref") is None \
                else "%s ref %d" % (x.get("rule"), x["ref"])
            out("  suppressed %s -- %s" % (label, x.get("reason")))


def cmd_section(g, a):
    names = a.names or [s.id for s in g.ordered_sections()]
    for nm in names:
        sec = g.section_of(nm)
        items = g.section_items(sec.id)
        out("## [%s]  (%d items)" % (sec.label, len(items)))
        for it in g.highlights(sec.id):
            out("  * %s — %s" % (it.id, trunc(it.headline, 150)))
        rel = g.related_sections(sec.id)
        if rel:
            out("  related: " + DOT.join("[%s] (%d)" % (s, n) for s, n in rel))
        for it in items:
            out(line_item(g, it, 10000 if a.full else 120))
        out()


def cmd_subsection(g, a):
    want = list(a.names or [])
    pids = sorted(g.subsections)
    if want:
        pids = [p for p in pids if any(w.strip("[]") in p for w in want)]
        if not pids:
            out("no subsection matches %s" % ", ".join(want))
            return
    if not pids:
        out("no subsections -- `kb subsection new <prefix> --order N`")
        return
    for pid in pids:
        sub = g.subsections[pid]
        members = g.subsection_members(pid)
        out("[%s]  order %d  (%d item%s)"
            % (pid, sub.order, len(members), "" if len(members) == 1 else "s"))
        if sub.label:
            out("  label    %s" % sub.label)
        blurb = sub.summary or (g.items[pid].headline if pid in g.items else None)
        if blurb:
            out("  summary  %s" % trunc(blurb, 0 if a.full else 160))
        for it in members:
            out("    %-40s %-12s %s"
                % (it.id, tag(it), trunc(it.key, 0 if a.full else 90)))
        out("")


def cmd_index(g, a):
    for sec in g.ordered_sections():
        items = g.section_items(sec.id)
        hi = g.highlights(sec.id)
        out("[%s] (%d)" % (sec.label, len(items)))
        for it in hi:
            out("    %s — %s" % (it.id, trunc(it.headline, 140)))


def cmd_find(g, a):
    try:
        rx = re.compile(a.pattern, 0 if a.case else re.I)
    except re.error as e:
        out("bad regex: %s" % e)
        return 2
    hits = []
    for it in g.items.values():
        if a.type and it.type != a.type:
            continue
        if a.status and it.status != a.status:
            continue
        fields = {
            "key": it.key,
            "id": it.id,
            "keywords": " ".join(it.keywords),
            "code": " ".join(code_line(c) for c in it.code),
            "summary": it.summary or "",
        }
        blob = fields[a.field] if a.field != "all" else " ".join(fields.values())
        if rx.search(blob):
            hits.append(it)
    for it in hits:
        out(line_item(g, it, 10000 if a.full else 140))
    out("-- %d items" % len(hits))


def cmd_guardrails(g, a):
    sec = g.section_of(a.section).id if a.section else None
    n = 0
    for it in g.items.values():
        if sec and it.section != sec:
            continue
        if not g.glossary.requires_modal(it.type):
            continue
        n += 1
        out(line_item(g, it, 10000 if a.full else 160))
    out("-- %d items of a modal-carrying type" % n)


def cmd_tree(g, a):
    kinds = set(a.kind) if a.kind else None
    dirs = ["down", "up"] if a.direction == "both" else [a.direction]
    for d in dirs:
        if len(dirs) > 1:
            out("== %s" % ("AFFECTS (downstream)" if d == "down"
                           else "AFFECTED BY (upstream)"))
        for iid in a.ids:
            _tree(g, g.get(iid), d, a.depth, a.keys, kinds, set(), 0)
        if len(dirs) > 1:
            out()


def _tree(g, it, direction, depth, keys, kinds, stack, lvl, kind=None):
    mark = "  (cycle)" if it.id in stack else ""
    label = "  " * lvl + (("%s " % kind) if kind else "") + it.id + mark
    out("%-64s %s" % (label, trunc(it.key, 110)) if keys else label)
    if mark or lvl >= depth:
        return
    stack = stack | {it.id}
    for nid, k, _arrow in g.neighbors(it.id, direction, kinds):
        nxt = g.items.get(nid)
        if nxt is None:
            out("  " * (lvl + 1) + nid + "  (DANGLING)")
        else:
            _tree(g, nxt, direction, depth, keys, kinds, stack, lvl + 1, k)


def cmd_impact(g, a):
    kinds = set(a.kind) if a.kind else None
    roots = [g.get(i) for i in a.ids]
    rootids = {r.id for r in roots}
    order, depths = g.walk(roots, a.direction, a.depth, kinds)
    bysec = defaultdict(list)
    for it in order:
        if it.id in rootids and not a.include_roots:
            continue
        bysec[it.section].append(it)
    total = sum(len(v) for v in bysec.values())
    if a.ids_only:
        # Bare ids and nothing else -- the flag exists to be piped into
        # `xargs kb verify`, and a header or a `[section]` line in that stream
        # becomes an argument.
        for s in sorted(bysec):
            for it in bysec[s]:
                out(it.id)
        return
    out("# %d items, depth<=%d, from: %s"
        % (total, a.depth, ", ".join(sorted(rootids))))
    for s in sorted(bysec):
        out("[%s]" % s)
        for it in bysec[s]:
            out(line_item(g, it, 150, "d%d " % depths[it.id]))
    if a.files:
        paths = []
        for it in order:
            for p in it.code_paths:
                if p not in paths:
                    paths.append(p)
        out("\n# code surface (%d paths)" % len(paths))
        for p in sorted(paths):
            out("  " + p)


def cmd_path(g, a):
    from collections import deque
    src, dst = g.get(a.src), g.get(a.dst)
    prev = {src.id: None}
    q = deque([src.id])
    while q:
        cur = q.popleft()
        if cur == dst.id:
            break
        for nid, kind, arrow in g.neighbors(cur, "both",
                                            set(a.kind) if a.kind else None):
            if nid in g.items and nid not in prev:
                prev[nid] = (cur, kind, arrow)
                q.append(nid)
    if dst.id not in prev:
        out("no path between %s and %s" % (src.id, dst.id))
        return 1
    chain, cur = [], dst.id
    while cur:
        p = prev[cur]
        chain.append((cur, p[1] if p else None, p[2] if p else None))
        cur = p[0] if p else None
    chain.reverse()
    for i, (iid, kind, arrow) in enumerate(chain):
        head = "  " * i + ("" if i == 0 else "%s%s%s " % (arrow, kind, arrow))
        out(head + iid)
        if a.keys:
            out("  " * i + "    " + trunc(g.items[iid].key, 140))


def cmd_hubs(g, a):
    ranked = sorted(g.items.values(), key=lambda i: (-g.degree(i.id), i.id))
    for it in ranked[:a.n]:
        out("  %3d  %-40s %-13s %s"
            % (g.degree(it.id), it.id, tag(it), trunc(it.key, 110)))


def cmd_orphans(g, a):
    n = 0
    for it in g.items.values():
        if not it.affects and not g.inbound.get(it.id):
            n += 1
            out(line_item(g, it, 140))
    out("-- %d items with no edges" % n)


def cmd_stats(g, a):
    items = list(g.items.values())
    edges = sum(len(i.affects) for i in items)
    out("store     %s" % g.path)
    out("schema    %d, run %s" % (g.schema, g.run or "-"))
    out("items     %d in %d sections" % (len(items), len(g.sections)))
    out("edges     %d (%.1f per item)" % (edges, edges / max(1, len(items))))
    kinds = defaultdict(int)
    for i in items:
        for e in i.affects:
            kinds[e.kind] += 1
    labelled = edges - kinds.get("relates", 0)
    out("          labelled %d/%d (%.0f%%) -- %s"
        % (labelled, edges, 100.0 * labelled / max(1, edges),
           DOT.join("%s %d" % (k, v) for k, v in sorted(kinds.items()))))
    by = defaultdict(int)
    for i in items:
        by[i.type] += 1
    out("types     " + DOT.join("%s %d" % (k, v) for k, v in sorted(by.items())))
    st = defaultdict(int)
    for i in items:
        st[i.status] += 1
    out("statuses  " + DOT.join("%s %d" % (k, v) for k, v in sorted(st.items())))
    out("markers   %d refs, %d symbol, %d file"
        % (sum(1 for i in items for c in i.code if c.kind == "marker"),
           sum(1 for i in items for c in i.code if c.kind == "symbol"),
           sum(1 for i in items for c in i.code if c.kind == "file")))
    over = [i for i in items if g.key_excess(i)]
    out("key debt  %d chars over cap, across %d item%s%s"
        % (g.excess_total(), len(over), "" if len(over) == 1 else "s",
           (" -- worst %s at %d" % (max(over, key=g.key_excess).id,
                                    g.key_excess(max(over, key=g.key_excess))))
           if over else ""))
    supp = sum(len(i.lint.get("ignore", [])) for i in items)
    graph_supp = len(g.lint.ignore)
    out("suppressed %d lint findings%s"
        % (supp + graph_supp,
           "  (%d on items, %d graph-level)" % (supp, graph_supp)
           if graph_supp else ""))
    if g.lint.thresholds:
        out("thresholds %s"
            % DOT.join("%s %s" % (k, g.lint.thresholds[k])
                       for k in sorted(g.lint.thresholds)))
    out("sections  " + DOT.join("%s %d" % (s.label, len(g.section_items(s.id)))
                                for s in g.ordered_sections()))


def cmd_kw(g, a):
    if not a.terms:
        counts, folded = defaultdict(int), defaultdict(int)
        tagged = [i for i in g.items.values() if i.keywords]
        for it in tagged:
            for k in it.keywords:
                counts[k] += 1
                folded[k.lower()] += 1
        share = g.lint.threshold("keyword_broad_share")
        line = share * len(tagged)
        for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            f = folded[k.lower()]
            # Rows stay case-sensitive so a stray `Auth` is still visible as
            # its own row, but the share is the folded one, because that is
            # what keyword-broad counts.
            out("  %3d  %3.0f%%  %s%s"
                % (n, 100.0 * f / max(1, len(tagged)), k,
                   "   <- over the keyword-broad line" if f > line else ""))
        out("-- %d distinct keywords across %d tagged items · broad at >%d%% "
            "(%.1f items)"
            % (len(counts), len(tagged), round(100.0 * share), line))
        return
    terms = [t.lower() for t in a.terms]
    for it in g.items.values():
        have = [k.lower() for k in it.keywords]
        hit = all(t in have for t in terms) if a.all else any(t in have for t in terms)
        if hit:
            out(line_item(g, it, 140))


def cmd_dup(g, a):
    hits = score(g.items.values(), a.text, a.code)
    if not hits:
        out("nothing similar -- this looks new")
        return
    out("closest existing items -- fold into one of these before adding:")
    for s, it in hits[:a.n]:
        out("  %6.1f  %-40s %s" % (s, it.id, trunc(it.key, 120)))


def cmd_relate(g, a):
    ex = set(a.exclude.split(",")) if a.exclude else set()
    hits = score(g.items.values(), a.text, a.code, exclude=ex)
    out("candidate edges (highest relevance first):")
    for s, it in hits[:a.n]:
        out("  %6.1f  %-40s [%s] %s" % (s, it.id, it.section, trunc(it.key, 100)))


def cmd_kw_suggest(g, a):
    idf = build_idf(list(g.items.values()))
    targets = [g.get(i) for i in a.ids] if a.ids else [
        i for i in g.items.values() if a.overwrite or not i.keywords]
    for it in targets:
        bag = item_terms(it)
        ranked = sorted(bag.items(), key=lambda kv: -kv[1] * idf.get(kv[0], 1.0))
        picks = [t for t, _ in ranked[:a.n]]
        out("kb kw add %s %s" % (it.id, " ".join(picks)))


def cmd_code(g, a):
    """Print the code an item governs -- marker snippets where they exist,
    and an honest gap where they do not.

    Several ids share one merged plan, so a range two of them both mark prints
    under the first and is cross-referenced under the rest.
    """
    root = g.root
    items = [g.get(iid) for iid in a.ids]
    plan, problems = markers.merged_plan(root, items)
    for it in items:
        out("### %s  [%s]  %s" % (it.id, it.section, tag(it)))
        if not it.code:
            out("  (no code refs)")
            continue
        mine = plan.get(it.id, [])
        # The nudge advertises a gap, so it is only honest when there is one.
        # An item that already has a marker region somewhere is not missing
        # anything just because it also carries a symbol or file ref.
        if not mine:
            for n, ref in enumerate(it.code):
                begin, end = markers.pair(ref.path, it.id)
                out("  %d %s -- no marker; place `%s` / `%s` by hand"
                    % (n, code_line(ref), begin, end))
            out("  (kb never writes source; once placed: kb code add %s marker "
                "<path>)" % it.id)
        else:
            for n, ref in enumerate(it.code):
                if ref.kind != "marker":
                    out("  %d %s" % (n, code_line(ref)))
        for r, shown_under in mine:
            if shown_under:
                out("  %s -- shown above under %s"
                    % (markers.region_header(r), shown_under))
                continue
            out("  " + markers.region_header(r))
            for line in markers.snippet(root, r, a.max_lines):
                out("    " + line)
        for path, n, msg in problems.get(it.id, []):
            out("  !! %s:%d %s" % (path, n, msg))
        out()
