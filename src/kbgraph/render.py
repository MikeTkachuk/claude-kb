"""Markdown render -- one way, JSON to md, for the human.

The model reads the graph through `kb`, never through this file, so the
render is free to optimise for reading rather than for grep-ability. It is
committed alongside the store and `kb render --check` fails on any drift,
which catches both a stale render and a hand-edit.
"""

import os

from .store import RENDER

BANNER = ("<!-- Generated from knowledgebase.json by `kb render`. Do not "
          "hand-edit: `kb render --check` fails on drift, and hand-edits are "
          "translated into `kb` commands, never absorbed. -->")

DOT = " · "


def _bullets(ids):
    return DOT.join(ids) if ids else "—"


def render_item(g, it, level=3):
    out = ["%s %s" % ("#" * level, it.id),
           "`%s` · `%s`" % (it.type, it.status), "", it.key, ""]

    if len(it.code) == 1:
        out.append("- **Code:** %s" % _code(it.code[0]))
    elif it.code:
        out.append("- **Code:**")
        for c in it.code:
            out.append("  - %s" % _code(c))

    # Outbound edges grouped by kind, in glossary order so the render is
    # stable no matter what order they were authored in.
    by_kind = {}
    for e in it.affects:
        by_kind.setdefault(e.kind, []).append(e.to)
    if by_kind:
        for kind in g.glossary.edge_kinds:
            if kind in by_kind:
                out.append("- **%s:** %s" % (g.glossary.label(kind),
                                             _bullets(sorted(by_kind[kind]))))
    else:
        out.append("- **Affects:** —")

    inb = {}
    for src, kind in g.inbound.get(it.id, []):
        inb.setdefault(kind, []).append(src)
    if inb:
        for kind in g.glossary.edge_kinds:
            if kind in inb:
                out.append("- **%s:** %s" % (g.glossary.inverse(kind),
                                             _bullets(sorted(inb[kind]))))
    else:
        out.append("- **Affected by:** —")

    return out


def _code(c):
    s = "`%s`" % c.path
    if c.symbol:
        s += " → `%s`" % c.symbol
    if c.part:
        s += " — %s" % c.part
    if c.kind == "marker":
        s += " *(marker)*"
    return s


def render_section(g, sec):
    out = ["## [%s]" % sec.label, ""]
    hi = g.highlights(sec.id)
    if hi:
        out.append("**Key knowledge:**")
        for it in hi:
            out.append("- `%s` — %s" % (it.id, it.headline))
        out.append("")
    rel = g.related_sections(sec.id)
    if rel:
        out.append("**Related:** %s"
                   % DOT.join("[%s] (%d)" % (g.sections[s].label
                                             if s in g.sections else s, n)
                              for s, n in rel))
        out.append("")
    groups, loose = {}, []
    for it in g.section_items(sec.id):
        pid = g.subsection_of(it.id)
        if pid is None:
            loose.append(it)
        else:
            groups.setdefault(pid, []).append(it)

    # Ungrouped items first, at the heading level they have always had, so a
    # store that declares no subsection renders byte-identically to before.
    for it in loose:
        out.extend(render_item(g, it, 3))
        out.append("")

    for sub_ in g.section_subsections(sec.id):
        if sub_.id not in groups:
            continue
        out.append("### %s" % _breadcrumb(g, sub_))
        out.append("")
        blurb = _sub_summary(g, sub_)
        if blurb:
            out.append(blurb)
            out.append("")
        for it in groups[sub_.id]:
            out.extend(render_item(g, it, 4))
            out.append("")
    return out


def _breadcrumb(g, sub_):
    """`Mold detail › Doc › Header` -- depth as text, so the heading level
    never grows. Markdown stops at h6, which caps heading-carried nesting at
    three levels; a breadcrumb has no ceiling.

    Each ancestor contributes its own declared label, not its raw id segment:
    a trail that reads `mold-detail › doc › Header` mixes ids and prose in one
    line and makes the labels look decorative. An undeclared ancestor -- legal,
    since a subsection may be declared without its parent being one -- falls
    back to its id segment.
    """
    parts = sub_.id.split(".")
    trail = []
    for n in range(2, len(parts)):
        anc = g.subsections.get(".".join(parts[:n]))
        trail.append(anc.label if anc is not None else parts[n - 1])
    trail.append(sub_.label)
    return " › ".join(trail)


def _sub_summary(g, sub_):
    """Explicit summary, else the same-id item's headline, else nothing.

    The middle case is the migration state: `pages.mold-detail` is both a
    monster item and the prefix of its own children, and stays that way while
    it is halved. When it is finally deleted the subsection's own summary takes
    over, with no re-iding at any step.
    """
    if sub_.summary:
        return sub_.summary
    parent = g.items.get(sub_.id)
    return parent.headline if parent is not None else None


def render_index(g):
    out = ["## §index", ""]
    for sec in g.ordered_sections():
        items = g.section_items(sec.id)
        hi = [it.id for it in items if it.highlight]
        line = "- **[%s]** (%d)" % (sec.label, len(items))
        if hi:
            line += " — " + DOT.join(hi)
        out.append(line)
        # Nested list bullets, two spaces per level. Inside a list item
        # indentation is relative to the item's content column, so this nests
        # to any depth without ever becoming an indented code block.
        for sub_ in g.section_subsections(sec.id):
            out.append("%s- **%s** (%d) — %s"
                       % ("  " * sub_.depth, sub_.id,
                          len(g.subsection_members(sub_.id)), sub_.label))
    out.append("")
    return out


def render(g):
    out = ["# %s" % g.title, "", BANNER, ""]
    out.extend(render_index(g))
    for sec in g.ordered_sections():
        out.append("---")
        out.append("")
        out.extend(render_section(g, sec))
    while out and not out[-1]:
        out.pop()
    return "\n".join(out) + "\n"


# ------------------------------------------------------------ byte conventions


# kb:begin render.byte-conventions
def conventions(path):
    """Read the existing render's newline / BOM / final-newline choices so a
    rewrite never shows up as a whole-file diff."""
    if not os.path.exists(path):
        return {"bom": False, "newline": "\n", "final_nl": True}
    with open(path, "rb") as f:
        raw = f.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
    crlf = text.count("\r\n")
    nl = "\r\n" if crlf and crlf >= text.count("\n") - crlf else "\n"
    return {"bom": bom, "newline": nl, "final_nl": text.endswith(("\n", "\r"))}


def to_bytes(text, conv):
    if conv["newline"] != "\n":
        text = text.replace("\n", conv["newline"])
    if not conv["final_nl"]:
        text = text.rstrip("\r\n")
    data = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if conv["bom"] else data
# kb:end render.byte-conventions


def render_path(g):
    return os.path.join(os.path.dirname(g.path), RENDER)


class LegacyRender(Exception):
    """The md on disk is a schema-1 knowledgebase, not a render of this store.

    Overwriting it would destroy hand-authored content that has never been
    migrated, so every write path refuses until it is done deliberately.
    """


def is_schema_1(path):
    if not os.path.exists(path):
        return False
    with open(path, "rb") as f:
        head = f.read(20000).decode("utf-8", "replace")
    return "\n- Key: " in head


def write(g, force=False):
    path = render_path(g)
    if not force and is_schema_1(path):
        raise LegacyRender(
            "%s is still a schema-1 knowledgebase, not a render.\n"
            "Overwriting it would discard the v1 file. Re-run with --force "
            "once you are ready to replace it." % RENDER)
    data = to_bytes(render(g), conventions(path))
    with open(path, "wb") as f:
        f.write(data)
    return path


# kb:begin render.check-is-the-gate
def check(g):
    """Return (ok, message). True iff render(json) == the md on disk."""
    path = render_path(g)
    if not os.path.exists(path):
        return False, "%s does not exist -- run `kb render`" % RENDER
    with open(path, "rb") as f:
        have = f.read()
    want = to_bytes(render(g), conventions(path))
    if have == want:
        return True, "%s is in sync with the store" % RENDER
    return False, _drift(have, want)


def _drift(have, want):
    hl = have.decode("utf-8", "replace").splitlines()
    wl = want.decode("utf-8", "replace").splitlines()
    for i in range(max(len(hl), len(wl))):
        h = hl[i] if i < len(hl) else "<eof>"
        w = wl[i] if i < len(wl) else "<eof>"
        if h != w:
            return ("%s is stale or hand-edited (first difference at line %d)\n"
                    "  on disk: %s\n"
                    "  render : %s\n"
                    "Hand-edits are never absorbed: translate them into `kb` "
                    "commands, then `kb render`." % (RENDER, i + 1, h[:160], w[:160]))
    return "%s differs from the render in whitespace or encoding only" % RENDER
# kb:end render.check-is-the-gate
