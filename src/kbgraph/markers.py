"""Source markers: `kb:begin <id>` / `kb:end <id>` pairs in comments.

Markers are the only code ref that yields a snippet. They are placed by hand
-- `kb` never writes source files -- and line numbers are resolved on every
read, never stored.
"""

import hashlib
import os
import re

# The ids are captured as one run and split by `marker_ids`, which is also what
# rejects anything else on the line. A marker delimits a region of code; a line
# that half looks like one is corruption, not prose, and reporting it is cheaper
# than silently delimiting the wrong range.
MARK = re.compile(r"kb:(begin|end)[ \t]+(.*)$")

# An id as `model.id-is-section-membership` spells it: `<section>.<slug>`. The dot
# is load-bearing rather than decorative -- without it `a.one and prose` parses as
# three ids, and a corrupted marker line becomes two marker-unknown findings
# instead of the one marker-syntax finding it actually is. Whether the id exists
# is `marker-unknown`'s question, not this one's.
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.[A-Za-z0-9][A-Za-z0-9._-]*$")

# One comment form per extension. Detection depends on these: see `openers_for`.
COMMENT_FORMS = {
    ".ts": "//", ".tsx": "//", ".js": "//", ".jsx": "//", ".mjs": "//",
    ".css": "/* */", ".scss": "/* */",
    ".py": "#", ".sh": "#", ".yml": "#", ".yaml": "#", ".toml": "#",
    ".md": "<!-- -->", ".html": "<!-- -->",
}

# Only used for a file whose extension is not in COMMENT_FORMS, where guessing
# wrong would silently drop a real marker.
ALL_OPENERS = ("//", "#", "/*", "<!--", "{/*")

# A comment closer a marker line may end with, longest first so `*/}` is not
# mistaken for `*/` with a stray brace.
CLOSERS = ("*/}", "*/", "-->")

# Directories no marker scan or rewrite descends into.  Lives here rather than in
# `lint` because `code fmt` walks the tree too, and two copies of this set would
# drift the first time one of them learned about a new build directory.
SKIP_DIRS = {"node_modules", ".next", ".git", ".venv", "venv", "__pycache__",
             "dist", "build", "out", "coverage", ".idea", ".vscode",
             ".pytest_cache", "test-results", "playwright-report", ".turbo"}


# kb:begin markers.own-line-and-own-language
def openers_for(path):
    """The comment openers a marker may use in this file.

    A marker written in some *other* language's comment syntax is text about a
    marker, not a marker. That single rule is what makes `"// kb:begin x"`
    inside a Python test fixture, and a fenced ```// kb:begin x``` example in a
    markdown file, both inert -- without it, any repo that documents this
    scheme reports its own examples as broken markers.
    """
    ext = os.path.splitext(path or "")[1]
    if ext == ".tsx":
        # JSX children need the brace form; the rest of the file uses `//`.
        return ("{/*", "//")
    form = COMMENT_FORMS.get(ext)
    if not form:
        return ALL_OPENERS
    return (form.split(" ", 1)[0],)


def opens_marker(line, openers):
    """True when `line` is a comment that starts with a marker.

    A marker MUST own its line: only whitespace may precede the comment
    opener, and `kb:` must be the first thing inside the comment. A trailing
    `foo()  # kb:begin x` does not delimit a region legibly, and accepting it
    is what lets a quoted string anywhere in a line masquerade as one.
    """
    s = line.lstrip()
    for o in openers:
        if s.startswith(o) and s[len(o):].lstrip().startswith("kb:"):
            return True
    return False


def marker_ids(rest):
    """The ids on a marker line, or None when the line is not a clean marker.

    `rest` is everything after `kb:begin` / `kb:end`. A trailing comment closer
    is dropped; what is left MUST be ids separated by whitespace, each named once.
    Returning None for anything else is what makes a malformed marker a reported
    error rather than a region quietly delimited at the wrong place.
    """
    s = rest.strip()
    for c in CLOSERS:
        if s.endswith(c):
            s = s[:-len(c)].strip()
            break
    toks = s.split()
    if not toks or not all(ID.match(t) for t in toks):
        return None
    if len(set(toks)) != len(toks):
        return None
    return toks


def marker_at(line, openers):
    """`(kind, ids, prefix, suffix)` for a clean marker line, else None.

    `prefix` is everything before `kb:` -- the indentation and the comment
    opener -- and `suffix` is the comment closer with its space. Keeping both
    verbatim is what lets a rewritten line stay in the file's own style without
    this module having to know what that style was.
    """
    if not opens_marker(line, openers):
        return None
    m = MARK.search(line)
    if not m:
        return None
    ids = marker_ids(m.group(2))
    if ids is None:
        return None
    suffix = ""
    tail = m.group(2).rstrip()
    for c in CLOSERS:
        if tail.endswith(c):
            suffix = " " + c
            break
    return m.group(1), ids, line[:m.start()], suffix


def render_marker(kind, ids, prefix, suffix):
    """The one line that carries `ids`, in the style `marker_at` read."""
    return "%skb:%s %s%s" % (prefix, kind, " ".join(ids), suffix)
# kb:end markers.own-line-and-own-language


class Region(object):
    __slots__ = ("item_id", "path", "start", "end")

    def __init__(self, item_id, path, start, end):
        self.item_id = item_id
        self.path = path
        self.start = start
        self.end = end

    @property
    def span(self):
        return self.end - self.start + 1

    def intersects(self, other):
        return self.start <= other.end and other.start <= self.end

    def __repr__(self):
        return "<%s %s:%d-%d>" % (self.item_id, self.path, self.start, self.end)


# kb:begin markers.hand-placed-with-a-printed-pair
def pair(path, item_id):
    """The exact `begin`/`end` pair to paste into `path`, in that file's
    comment form.

    `kb` never writes source (invariant 7), so an item's marker gap can only
    be closed by hand -- the least the tool can do is spell the pair out in
    the right syntax for the file it is missing from.
    """
    if path.endswith(".tsx"):
        # `//` inside JSX children renders as visible text, and whether a
        # given line is in JSX is not knowable from the path, so .tsx always
        # gets the form that is safe in both places.
        open_, close = "{/*", "*/}"
    else:
        form = COMMENT_FORMS.get(os.path.splitext(path)[1], "//")
        open_, close = form.split(" ", 1) if " " in form else (form, "")
    def one(word):
        s = "%s kb:%s %s" % (open_, word, item_id)
        return s + (" " + close if close else "")
    return one("begin"), one("end")
# kb:end markers.hand-placed-with-a-printed-pair


# kb:begin markers.stack-pairing
def scan_text(text, path=""):
    """Return ([Region], [problem]). Pairing is a stack keyed by id, so
    regions belonging to different items may overlap and nest freely.

    A marker line may name several ids -- `kb:begin a.one a.two` -- and that is
    exactly equivalent to that many single-id lines stacked, because each id gets
    its own push. `kb:end a.two` alone still closes only `a.two`, and the two
    regions come out with identical ranges, which `merge` already folds into one
    block crediting both. It is also what `kb code fmt` collapses towards.

    A problem is (path, line, item_id, rule, message). The id is a field rather
    than only a phrase inside the message because `marker-unpaired` reports at
    the id it names: a file position moves the moment somebody edits above it,
    and a suppression keyed on one silently stops matching. The rule is a field
    because a malformed marker line and an unclosed one are different findings,
    and this is the only place that can tell them apart.
    """
    regions, problems = [], []
    open_ = {}
    openers = openers_for(path)
    for n, line in enumerate(text.splitlines(), 1):
        if not opens_marker(line, openers):
            continue
        m = MARK.search(line)
        if not m:
            problems.append((path, n, None, "marker-syntax",
                             "%s:%d expected `kb:begin` or `kb:end`: %r"
                             % (path, n, line.strip())))
            continue
        kind, ids = m.group(1), marker_ids(m.group(2))
        if ids is None:
            problems.append((path, n, None, "marker-syntax",
                             "%s:%d `kb:%s` takes ids and nothing else: %r"
                             % (path, n, kind, line.strip())))
            continue
        for iid in ids:
            if kind == "begin":
                open_.setdefault(iid, []).append(n)
                continue
            stack = open_.get(iid)
            if not stack:
                problems.append((path, n, iid, "marker-unpaired",
                                 "kb:end %s without a matching begin" % iid))
                continue
            regions.append(Region(iid, path, stack.pop(), n))
    for iid in sorted(open_):
        for n in open_[iid]:
            problems.append((path, n, iid, "marker-unpaired",
                             "kb:begin %s without a matching end" % iid))
    regions.sort(key=lambda r: (r.start, r.end))
    return regions, problems
# kb:end markers.stack-pairing


DIGEST_PREFIX = "sha256:"
DIGEST_LEN = 16


def _norm(text):
    """Hash LF-normalised text. Without this every file digest recorded on
    Windows would drift the moment the same tree was read on Linux, or after
    any editor rewrote the line endings -- a false positive on every ref."""
    return "\n".join(text.splitlines()).encode("utf-8")


def _read(root, path):
    full = os.path.join(root, path.replace("/", os.sep)) if root else path
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def digest_ref(root, ref, item_id):
    """A content digest for one code ref, or None when it has no honest one.

    `symbol` refs deliberately return None: hashing a symbol body needs real
    TypeScript parsing, which DESIGN.md declined. A ref with no digest simply
    never drifts -- `verified-stale` already covers "nobody has re-read this".
    """
    if ref.kind == "file":
        text = _read(root, ref.path)
        if text is None:
            return None
        return DIGEST_PREFIX + hashlib.sha256(_norm(text)).hexdigest()[:DIGEST_LEN]
    if ref.kind == "marker":
        text = _read(root, ref.path)
        if text is None:
            return None
        regions, _problems = scan_text(text, ref.path)
        # An item may own several regions in one file (scan_text pairs by a
        # stack, so repeats and nesting are legal). Hash all of them, in file
        # order, so the digest is deterministic regardless of scan order.
        mine = sorted((r for r in regions if r.item_id == item_id),
                      key=lambda r: (r.start, r.end))
        if not mine:
            return None
        lines = text.splitlines()
        body = []
        for r in mine:
            body.extend(lines[r.start - 1:r.end])
        return DIGEST_PREFIX + hashlib.sha256(
            _norm("\n".join(body))).hexdigest()[:DIGEST_LEN]
    return None


def scan_file(path, root=""):
    full = os.path.join(root, path) if root else path
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        # Five fields, like every other problem this module emits: the shape
        # being inconsistent here is what made `kb code` crash on a file it
        # could not read or could not pair.
        return [], [(path, 0, None, "marker-unpaired", "unreadable: %s" % e)]
    return scan_text(text, path)


def owners(region):
    """Every item id that contributed to a (possibly merged) region."""
    return region.item_id.split("+")


def region_header(region):
    """`path:start-end`, naming every contributing item once more than one
    item's marker landed in the same range."""
    s = "%s:%d-%d" % (region.path, region.start, region.end)
    own = owners(region)
    if len(own) > 1:
        s += "  (%s)" % " + ".join(own)
    return s


# kb:begin markers.one-region-printed-once
def merged_plan(root, items):
    """Marker regions for `items`, merged *across* item boundaries so a range
    two of them both mark is read, and printed, exactly once.

    Asking per item -- "where is this one item marked" -- is the wrong question
    when several items are rendered together, which is every `kb ctx --path`
    and every multi-id `kb code`. A guardrail marked inside the concept it
    refines then prints the same lines once per item, and merging cannot fix it
    downstream because each item's regions only ever meet their own siblings.

    A marker ref stores the path it was last seen at; if the marker is not
    there any more that is a lint finding, not a crash.

    Returns (plan, problems), both keyed by item id. Each plan entry is a list
    of (region, shown_under) in file order: `shown_under` is None for the item
    that prints the region, and the id of that item for every other
    contributor, which gets a cross-reference instead of a copy.
    """
    order = []
    for it in items:
        if it.id not in order:
            order.append(it.id)
    cache, problems, regions = {}, {}, []
    for it in items:
        problems.setdefault(it.id, [])
        for ref in it.code:
            if ref.kind != "marker":
                continue
            if ref.path not in cache:
                cache[ref.path] = scan_file(ref.path, root)
                # A file-level problem -- an unpaired marker naming some *other*
                # item -- belongs to whoever made us open the file, once. Reduced
                # to (path, line, message) here: `kb code` and `kb ctx --code`
                # print the position and the text and have no use for the id or
                # the rule, and mixing two tuple shapes in one list is what made
                # both of them crash on any file with an unpaired marker.
                problems[it.id].extend(
                    (p, n, msg) for p, n, _iid, _rule, msg in cache[ref.path][1])
            mine = [r for r in cache[ref.path][0] if r.item_id == it.id]
            if not mine:
                problems[it.id].append(
                    (ref.path, 0, "no `kb:begin %s` found in this file" % it.id))
            regions.extend(mine)
    plan = {iid: [] for iid in order}
    for r in merge(regions):
        contributors = [i for i in order if i in owners(r)] or [owners(r)[0]]
        plan[contributors[0]].append((r, None))
        for other in contributors[1:]:
            plan[other].append((r, contributors[0]))
    return plan, problems
# kb:end markers.one-region-printed-once


# kb:begin markers.merge-on-intersection-only
def merge(regions):
    """Merge only on true range intersection. Two regions a few lines apart
    are apart for a reason; merging them would invent a contiguity that is
    not in the source and hide the gap."""
    out = []
    for r in sorted(regions, key=lambda r: (r.path, r.start, r.end)):
        if out and out[-1].path == r.path and out[-1].intersects(r):
            prev = out[-1]
            prev.end = max(prev.end, r.end)
            if r.item_id not in prev.item_id.split("+"):
                prev.item_id += "+" + r.item_id
            continue
        out.append(Region(r.item_id, r.path, r.start, r.end))
    return out
# kb:end markers.merge-on-intersection-only


def snippet(root, region, max_lines=None):
    full = os.path.join(root, region.path) if root else region.path
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    body = lines[region.start - 1:region.end]
    if max_lines and len(body) > max_lines:
        head = body[:max_lines - 1]
        return head + ["    … %d more lines" % (len(body) - len(head))]
    return body


# kb:begin markers.jsx-comment-form
def jsx_risk(path, text):
    """A `//` marker inside JSX children renders as visible text on the page
    with no compile error -- the one silent failure mode in the scheme."""
    if not path.endswith(".tsx"):
        return []
    out = []
    depth = 0
    for n, line in enumerate(text.splitlines(), 1):
        m = MARK.search(line)
        if m and line.lstrip().startswith("//") and depth > 0:
            out.append((path, n, "`//` marker inside JSX children -- use {/* … */}"))
        depth += line.count("return (") - line.count(");")
        if depth < 0:
            depth = 0
    return out
# kb:end markers.jsx-comment-form


def newline_of(text):
    """The file's line ending, or None when it mixes them.

    A formatter that silently normalised CRLF to LF would rewrite every line in
    the file, which is the one thing this command promises not to do. A mixed
    file is left alone and said so, rather than picked a winner for.
    """
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    if len([n for n in (crlf, lf, cr) if n]) > 1:
        return None
    if crlf:
        return "\r\n"
    if cr:
        return "\r"
    return "\n"


def walk_files(root, paths=None):
    """([repo-relative file], [named-but-skipped]) for the marker passes.

    Only extensions with a declared comment form: a file whose comment syntax
    would have to be guessed is not one this module will rewrite. A path named
    explicitly and skipped for that reason is returned rather than dropped, so
    the caller can say why nothing happened to it.
    """
    found, skipped, seen = [], [], set()
    for t in (list(paths) if paths else [""]):
        rel = t.replace("\\", "/").rstrip("/")
        full = os.path.join(root, rel.replace("/", os.sep)) if rel else root
        if os.path.isfile(full):
            if os.path.splitext(full)[1] in COMMENT_FORMS:
                if rel not in seen:
                    seen.add(rel)
                    found.append(rel)
            else:
                skipped.append(rel)
            continue
        for dirpath, dirnames, filenames in os.walk(full):
            dirnames[:] = sorted(d for d in dirnames
                                 if d not in SKIP_DIRS and not d.startswith("."))
            for fn in sorted(filenames):
                if os.path.splitext(fn)[1] not in COMMENT_FORMS:
                    continue
                r = os.path.relpath(os.path.join(dirpath, fn),
                                    root).replace(os.sep, "/")
                if r not in seen:
                    seen.add(r)
                    found.append(r)
    return found, skipped


def code_lines(text, path=""):
    """Every line that is neither blank nor a marker, in order. What a reformat
    MUST leave untouched."""
    openers = openers_for(path)
    return [ln for ln in text.splitlines()
            if ln.strip() and marker_at(ln, openers) is None]


def region_shape(text, path=""):
    """Per item id, the code each of its regions covers, plus the problem count.

    The second half of the safety check, and the half `code_lines` cannot see: a
    collapse that dropped an id leaves every line in the file identical and every
    region wrong. Blank and marker lines are excluded from each body because
    moving those is the entire point of the command.
    """
    openers = openers_for(path)
    lines = text.splitlines()
    regions, problems = scan_text(text, path)
    shape = {}
    for r in regions:
        shape.setdefault(r.item_id, []).append(
            [ln for ln in lines[r.start - 1:r.end]
             if ln.strip() and marker_at(ln, openers) is None])
    return sorted(shape.items()), len(problems)


def fmt_check(old, new, path=""):
    """None when `new` is a legal reformat of `old`, else why it is not.

    Two structural equalities and deliberately no digests: what this command
    promises is that the only lines it changes are marker lines and blank ones,
    so that is exactly what is checked, in the terms the promise was made in.
    """
    if code_lines(old, path) != code_lines(new, path):
        return "would change a line that is neither a marker nor blank"
    if region_shape(old, path) != region_shape(new, path):
        return "would change which code a region covers"
    return None


def _tighten(lines, openers):
    """Swap each region boundary past the blank lines just inside it, in place.

    Repeated to a fixed point: one pass leaves a `begin` unmoved when the blank
    line it wanted was still occupied by another `begin` below it, which is
    exactly the stack this command exists to tidy. Bounded by the line count
    because every swap moves one marker one line and no swap creates a blank.
    """
    for _round in range(len(lines) + 1):
        changed = False
        # A marker line is never blank, so a `begin` walking down stops at its
        # own `end` and can never cross it. Same, upwards, for an `end`.
        for i in range(len(lines) - 1):
            mk = marker_at(lines[i], openers)
            if mk and mk[0] == "begin" and not lines[i + 1].strip():
                lines[i], lines[i + 1] = lines[i + 1], lines[i]
                changed = True
        for i in range(len(lines) - 1, 0, -1):
            mk = marker_at(lines[i], openers)
            if mk and mk[0] == "end" and not lines[i - 1].strip():
                lines[i], lines[i - 1] = lines[i - 1], lines[i]
                changed = True
        if not changed:
            return


def _collapse(lines, openers):
    """Merge vertically adjacent same-kind marker lines into one multi-id line."""
    out, i, n = [], 0, len(lines)
    while i < n:
        mk = marker_at(lines[i], openers)
        if not mk:
            out.append(lines[i])
            i += 1
            continue
        kind, ids, prefix, suffix = mk[0], list(mk[1]), mk[2], mk[3]
        j = i + 1
        while j < n:
            nxt = marker_at(lines[j], openers)
            # Same kind, same indentation and the same comment form: a marker in
            # a different style is in a different context, and merging the two
            # would put one of them in syntax that does not hold there.
            if not nxt or nxt[0] != kind or nxt[2] != prefix or nxt[3] != suffix:
                break
            for iid in nxt[1]:
                if iid not in ids:
                    ids.append(iid)
            j += 1
        out.append(render_marker(kind, ids, prefix, suffix))
        i = j
    return out


# kb:begin markers.fmt-touches-markers-and-blanks-only
def fmt_text(text, path=""):
    """(new_text, None), or (None, reason) when the file must be left alone.

    Tighten, then collapse: collapse only merges markers that are already
    adjacent, so tightening first is what creates most of the work for it.
    """
    nl = newline_of(text)
    if nl is None:
        return None, "mixes line endings"
    openers = openers_for(path)
    # `split(nl)` rather than `splitlines()`: splitlines also breaks on form
    # feed, vertical tab and U+2028, so rejoining its output would rewrite lines
    # this command promised not to touch.
    lines = text.split(nl)
    # A trailing newline leaves a final empty element. Held back so that no
    # marker can be swapped into it, which would move the newline off the end of
    # the file.
    trailing = bool(lines) and lines[-1] == ""
    if trailing:
        lines.pop()
    _tighten(lines, openers)
    lines = _collapse(lines, openers)
    if trailing:
        lines.append("")
    return nl.join(lines), None
# kb:end markers.fmt-touches-markers-and-blanks-only
