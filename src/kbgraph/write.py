"""Write commands.

Every writable field has exactly one command (invariant 4), so no field is
reachable only by hand-editing the JSON. Mutations apply immediately -- git is
the undo. `--dry-run` prints the store diff instead of writing; destructive
commands additionally require `--yes`.

Everything routes through `touch()` + `commit()`: `touch` stamps
`meta.modified` and the ambient `meta.run` on every item a command changed,
and `commit` re-validates, writes the store and re-renders the markdown, so
the two files cannot drift apart by one command's width.
"""

import datetime
import difflib
import os
import re
import sys
import textwrap

from . import markers
from . import model as model_mod
from . import render as render_mod
from . import store as store_mod
from .model import CODE_KINDS, CodeRef, Edge, Item, SchemaError, Subsection

# Every verb any dual-dispatch command accepts. `dispatch` is given the subset
# its own command implements: the union used to decide for all of them, so
# `kb kw clear x y` was routed to the keyword *remove* branch by falling through
# its `else`, and every verb added here widened that. The union survives only for
# `tests/test_docs.py`, which must treat a documented verb as a mutation
# whichever command carries it.
VERBS = ("add", "rm", "clear", "new", "rename", "order", "label", "summary",
         "kind", "set", "reset", "fmt", "mv")


def die(msg, code=2):
    sys.stderr.write("kb: %s\n" % msg)
    raise SystemExit(code)


def out(*parts):
    sys.stdout.write(" ".join(str(p) for p in parts) + "\n")


# A mutation's summary must not be printed before the write is known to have
# happened -- otherwise a command that ends up refused (malformed result, or a
# schema-1 md in the way) reports "verified"/"renamed" and then errors out.
# Commands buffer with say(); commit() flushes once it is past the guards, and
# die() drops the buffer on the floor.
_pending = []


# kb:begin write.buffered-output
def say(*parts):
    _pending.append(" ".join(str(p) for p in parts))


def flush():
    for line in _pending:
        sys.stdout.write(line + "\n")
    del _pending[:]
# kb:end write.buffered-output


def today():
    return datetime.date.today().isoformat()


# kb:begin write.touch-and-commit
def touch(g, *items):
    """Stamp modified + the ambient run on every item a mutation changed."""
    for it in items:
        if it is None:
            continue
        it.meta["modified"] = today()
        if g.run:
            it.meta["run"] = g.run
# kb:end write.touch-and-commit


# kb:begin write.destructive-needs-yes
def need_yes(a, what):
    if not getattr(a, "yes", False) and not getattr(a, "dry_run", False):
        die("%s is destructive -- re-run with --yes (or --dry-run to preview)"
            % what)
# kb:end write.destructive-needs-yes


def split_list(chunks, sep=r"[,\s·]+"):
    """`--affects a,b --affects c` and `--affects "a b"` all mean the same."""
    out_ = []
    for chunk in chunks or []:
        for x in re.split(sep, chunk):
            if x.strip():
                out_.append(x.strip())
    return out_


def norm_kw(terms):
    return [" ".join(t.lower().split()) for t in terms if t.strip()]


def split_kw(chunks):
    """Keywords may be up to three words, so whitespace is not a separator
    here -- only a comma or a middot is."""
    return norm_kw(split_list(chunks, r"[,·]+"))


def resolve(g, raw):
    """Prefix-resolve every id, so edges can be typed the way they are read."""
    return [g.get(x).id for x in split_list(raw)]


# kb:begin write.stdin-is-decoded-utf8
def read_text(value, path):
    """A Key is often a paragraph; `--file -` keeps it off the command line.

    stdin is decoded here rather than left to `sys.stdin`, whose encoding is the
    locale's: on Windows that is cp1252, so a UTF-8 key piped in arrives as
    mojibake and is written to the store that way. It is silent because a cp1252
    read followed by a cp1252 write is byte-identity -- the string round-trips to
    the screen and only corrupts the file. `--file PATH` never had the bug; it
    names UTF-8 itself, and two spellings of one flag must not disagree.

    Newlines are folded to LF to match what text-mode `open` already gives the
    PATH branch, so neither spelling can leave a carriage return inside a key.
    """
    if path:
        if path == "-":
            # `sys.stdin` is replaced by a StringIO under test and by a pipe in
            # anger; only the latter has a byte buffer to decode.
            buf = getattr(sys.stdin, "buffer", None)
            raw = buf.read() if buf is not None else sys.stdin.read()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            return raw.replace("\r\n", "\n").replace("\r", "\n").strip()
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return value
# kb:end write.stdin-is-decoded-utf8


# Below this, the two-line `- old / + new` form is the clearest thing to print
# and is what the existing tests assert on. Above it, that form is two
# unreadable walls -- which is the entire problem being solved here.
DIFF_THRESHOLD = 160


# Past this multiple of its type's cap a key is usually two rules wearing one
# id, so the advice changes: asking for a shorter sentence would compress the
# seam rather than open it.
SPLIT_FACTOR = 1.5


# kb:begin write.cap-refused-at-the-write
def cap_guard(g, type_, key, reason, where):
    """Refuse an over-cap key unless `--suppress-length` carries a reason.

    `key-length` has always been a lint warning, which reports the debt once it
    is already committed. Refusing at the write is what stops it accruing, and
    the two messages differ because the repairs do: trim, or split.

    Returns the `lint.ignore` entry to record, or None when the key is within
    cap. `basis` is stamped by the caller, once the key is actually on the item:
    it digests the new key, so the exemption suspends if that key later moves.
    """
    cap = g.glossary.key_max(type_)
    if not cap or len(key) <= cap:
        return None
    advice = ("Please consider splitting the item into independent nodes."
              if len(key) > cap * SPLIT_FACTOR else "Please optimize input.")
    if not reason:
        die("key is %d chars, %d over the %s cap of %d\n    %s\n"
            "    override: %s --suppress-length \"<reason>\""
            % (len(key), len(key) - cap, type_, cap, advice, where))
    return {"rule": "key-length", "reason": reason}


def record_ignore(it, entry):
    """Add one `lint.ignore` entry, replacing any earlier one for the same rule.

    Sorted exactly as `_suppress_item` sorts: rule first, then the item-wide
    entry ahead of the per-ref ones, so the store's order matches how the
    resolver reads them and `kb suppress` and this path cannot disagree.
    """
    ref = entry.get("ref")
    keep = [x for x in it.lint.get("ignore", [])
            if not (x.get("rule") == entry["rule"] and x.get("ref") == ref)]
    keep.append(entry)
    keep.sort(key=lambda x: (x["rule"],
                             -1 if x.get("ref") is None else x["ref"]))
    it.lint["ignore"] = keep
# kb:end write.cap-refused-at-the-write


# kb:begin write.long-value-word-diff
def word_diff(old, new, width=76):
    """A word-level diff of two values, unchanged runs collapsed.

    `kb set <id> key --file -` re-emits a whole key by hand, and afterwards the
    only question that matters is "what actually moved?". Printing both values
    in full answers it for a one-line key and not at all for an 18KB one; a
    rewrite that size has been checked by counting bytes, which is not checking.

    `autojunk=False` is load-bearing. SequenceMatcher's default treats any
    element occurring in more than 1 percent of a sequence of 200 or more as
    junk, which on a word list is every common word, and it collapses a
    one-word edit into a single opaque `replace` spanning the whole region.
    """
    a, b = (old or "").split(), (new or "").split()
    if a == b:
        return ["  (no word-level change -- whitespace or line breaks only)"]
    lines = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            n = i2 - i1
            if n <= 8:
                lines.extend(_wrap("    ", a[i1:i2], width))
            else:
                lines.extend(_wrap("    ", a[i1:i1 + 4], width))
                lines.append("    … (%d words unchanged) …" % (n - 8))
                lines.extend(_wrap("    ", a[i2 - 4:i2], width))
            continue
        if tag in ("replace", "delete"):
            lines.extend(_wrap("  - ", a[i1:i2], width))
        if tag in ("replace", "insert"):
            lines.extend(_wrap("  + ", b[j1:j2], width))
    return lines


def _wrap(prefix, words, width):
    if not words:
        return []
    return textwrap.wrap(" ".join(words), width=width,
                         initial_indent=prefix,
                         subsequent_indent=" " * len(prefix)) or [prefix.rstrip()]
# kb:end write.long-value-word-diff


# A key edit smaller than this share of the key is a reword or a typo fix, not a
# move a summary could have fallen behind. The nudge used to fire on every key
# write, one-word fixes included, and a warning that fires when nothing is wrong
# is one that stops being read.
NUDGE_SHARE = 0.2


def changed_share(old, new):
    """The share of the longer word list that a key edit moved: 0.0 for none,
    1.0 for a full rewrite.

    Words rather than characters, and `autojunk=False`, for the same reasons
    `word_diff` above uses both: a character diff calls a re-wrapped paragraph a
    rewrite, and SequenceMatcher's default junk heuristic treats every common
    word in a long key as noise.
    """
    a, b = (old or "").split(), (new or "").split()
    if not a and not b:
        return 0.0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    same = sum(blk.size for blk in sm.get_matching_blocks())
    return 1.0 - float(same) / max(len(a), len(b))


# ---------------------------------------------------------------- plumbing


def _autoclear_legacy(g):
    """Drop a `lint.legacy` entry the moment nothing cites it any more.

    A citation only ever disappears through `kb set <id> key ...` or `kb rm`,
    and both route through `commit()` -- so checking here, on every write, is
    the one place this cannot be missed, whatever kind of name it is. The
    other way an entry leaves the list is a human giving up on it via
    `kb legacy rm`.
    """
    keep = []
    for x in g.lint.legacy:
        if _citing(g, x["name"], subtree=x["kind"] != "item"):
            keep.append(x)
        else:
            say("%s cleared from lint.legacy -- nothing cites it any more"
                % _label(x["kind"], x["name"]))
    if len(keep) != len(g.lint.legacy):
        g.lint.legacy = keep


def commit(g, a, note=""):
    """Re-validate, write the store, re-render -- or show what would change."""
    _autoclear_legacy(g)
    # kb:begin store.write-implies-load write.validate-before-write
    try:
        g.invalidate()
        g.validate()
    except SchemaError as e:
        die("refusing to write, the result would be malformed:\n  "
            + "\n  ".join(e.problems))
    # kb:end write.validate-before-write store.write-implies-load
    new = store_mod.dumps(g.to_dict())
    with open(g.path, "r", encoding="utf-8") as f:
        old = f.read()
    if old == new:
        flush()
        out("no change" + (" (%s)" % note if note else ""))
        return
    if getattr(a, "dry_run", False):
        # Deliberately ahead of the schema-1 guard: a dry run writes nothing,
        # so it cannot desync the pair, and previewing is the only thing that
        # works in a repo whose md has not been converted yet.
        flush()
        diff = difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                    "knowledgebase.json", "knowledgebase.json (new)")
        sys.stdout.write("".join(diff))
        out("-- dry run, nothing written")
        return
    # Check before writing anything: a mutation that updates the store but
    # cannot re-render leaves the two out of sync, which is the one state
    # `render --check` exists to prevent.
    if render_mod.is_schema_1(render_mod.render_path(g)):
        die("knowledgebase.md is still a schema-1 file, not a render of this "
            "store.\nRun `kb render --force` once to replace it, then retry.")
    with open(g.path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    flush()
    path = render_mod.write(g)
    out("wrote %s + %s%s" % (store_mod.STORE, store_mod.RENDER,
                             " (%s)" % note if note else ""))
    return path


def _label(kind, name):
    return "[%s]" % name if kind in ("section", "subsection") else repr(name)


def _citing(g, name, subtree):
    """Every item whose key still cites `name` in prose.

    `subtree=True` (a section or subsection) also matches anything nested
    under it, since the whole prefix -- not just the name itself -- stopped
    resolving; `subtree=False` (an item, which nothing can be nested under)
    matches only the exact id. Same token shape `stale-id-mention` reads (a
    dotted id, not a file path), but keyed to one exact name rather than "any
    id that doesn't exist": right after the rename `name` cannot resolve to
    anything, so there is nothing left to double check against `g.items`.
    """
    from .lint import FILE_SUFFIXES, ID_TOKEN
    found = set()
    for it in g.items.values():
        for tok in ID_TOKEN.findall(it.key or ""):
            if tok != name and not (subtree and tok.startswith(name + ".")):
                continue
            if tok.rsplit(".", 1)[1] in FILE_SUFFIXES:
                continue
            found.add(it.id)
            break
    return sorted(found)


def _note_legacy(g, name, kind, renamed_to):
    """If prose still cites `name`, warn (in full, not truncated) and record
    it in `lint.legacy` so `kb lint` (sections) or `kb legacy` (all kinds)
    keeps it visible until it is fixed or retired by hand."""
    citing = _citing(g, name, subtree=kind != "item")
    if not citing:
        return
    say("  ! %d item%s still name %s in key prose: %s"
        % (len(citing), "" if len(citing) == 1 else "s", _label(kind, name),
           ", ".join(citing)))
    g.lint.legacy = [x for x in g.lint.legacy if x.get("name") != name]
    g.lint.legacy.append({"name": name, "kind": kind, "renamed_to": renamed_to,
                          "since": today()})
    say("  recorded %s in lint.legacy -- `kb lint` keeps flagging these "
        "mentions until every one is fixed (it then clears itself) or "
        "`kb legacy rm %s` retires it by hand" % (_label(kind, name), name))


def _reclaim_legacy(g, name):
    """Drop a `lint.legacy` entry that `name` -- an item, section or
    subsection -- is about to reoccupy.

    A live name and a legacy entry can never coexist -- `_validate_lint`
    refuses that store, and worse, `_citing` would keep calling every current,
    perfectly valid reference to the reoccupied name a stale mention of its
    old self. So whichever command makes `name` live again (`kb new`,
    `kb section new`, `kb subsection new`, or any rename's destination) must
    retire the stale entry itself, not leave the next commit to discover it.
    """
    keep = [x for x in g.lint.legacy if x.get("name") != name]
    if len(keep) != len(g.lint.legacy):
        g.lint.legacy = keep
        say("  %r was a legacy name (retired earlier); reoccupying it drops "
            "that lint.legacy entry" % name)


def repoint(g, old, new):
    """Aim every edge that targets `old` at `new`, or drop it when `new` is
    None. Returns the items that changed, so the caller can stamp them."""
    changed = []
    for it in g.items.values():
        if it.id == old or not any(e.to == old for e in it.affects):
            continue
        if new is None:
            it.affects = [e for e in it.affects if e.to != old]
        else:
            for e in it.affects:
                if e.to == old:
                    e.to = new
            # A rename can collide with an edge that already pointed at the
            # new id; keep the first and drop the duplicate.
            seen, keep = set(), []
            for e in it.affects:
                if e.to not in seen:
                    seen.add(e.to)
                    keep.append(e)
            it.affects = keep
        changed.append(it)
    return changed


def rename(g, it, new_id, section=None):
    """Rename in place: re-key the item, repoint every inbound edge."""
    old = it.id
    del g.items[old]
    it.id = new_id
    if section:
        it.section = section
    g.items[new_id] = it
    g.items = {k: g.items[k] for k in sorted(g.items)}
    changed = repoint(g, old, new_id)
    for e in it.affects:
        if e.to == old:
            e.to = new_id
    return changed


def _rename_prefix(g, old, new, resection=False):
    """Re-id everything nested under a section or subsection id.

    A section and a subsection are both just a name that member ids extend
    -- `kb section rename` and `kb subsection rename` share this rather than
    each re-deriving "who is under `old`". Only a section rename re-sections
    its members (`resection`); a subsection rename changes the middle
    segment of an id, not which top-level section owns it.

    Membership is `startswith(old + ".")` only, never `== old`: an item's id
    could coincidentally equal some subsection's id without being one of its
    members (`subsection_of` never treats exact equality as membership
    either), and unlike a subsection, no item id can equal a bare section id.

    A section's destination can never already hold items -- `new in
    g.sections` is refused before this runs, and a section id is a mandatory,
    validated prefix of every one of its items, so a section that doesn't
    exist yet owns nothing. A subsection carries no such guarantee: `new` can
    already be a live, unrelated id prefix that nobody declared a subsection
    over. Checked and refused up front, before any mutation, or a derived id
    that collides with an existing one would silently overwrite it -- `kb
    item` losing the item was never a "no change" the caller could notice.
    """
    movers = [i for i in g.items.values() if i.id.startswith(old + ".")]
    moving_subs = [p for p in g.subsections
                  if p == old or p.startswith(old + ".")]
    moving = {i.id for i in movers} | set(moving_subs)
    for it in movers:
        new_id = new + it.id[len(old):]
        if new_id in g.items and new_id not in moving:
            die("%s -> %s: %s already exists as a different item"
                % (it.id, new_id, new_id))
    for pid in moving_subs:
        new_pid = new + pid[len(old):]
        if new_pid in g.subsections and new_pid not in moving:
            die("[%s] -> [%s]: [%s] already exists as a different subsection"
                % (pid, new_pid, new_pid))
    touched = []
    for it in movers:
        touched.append(it)
        touched += rename(g, it, new + it.id[len(old):],
                          section=new if resection else None)
    for pid in moving_subs:
        sub = g.subsections.pop(pid)
        new_pid = new + pid[len(old):]
        default_label = pid.rsplit(".", 1)[-1]
        new_label = (new_pid.rsplit(".", 1)[-1] if sub.label == default_label
                    else sub.label)
        g.subsections[new_pid] = Subsection(new_pid, {
            "order": sub.order, "label": new_label, "summary": sub.summary})
    g.subsections = {k: g.subsections[k] for k in sorted(g.subsections)}
    _cascade_prefix(g, old, new)
    return touched


# kb:begin write.one-code-ref-per-flag
def parse_code(tokens):
    """`file src/x.ts` / `symbol src/x.ts authFetch` -> one CodeRef.

    Exactly one ref, and every token past the signature is refused. They used to
    be folded into `part`, which turned the natural spelling of two refs --
    `symbol src/x.ts foo marker src/x.ts` -- into one ref carrying the other as a
    free-text label. Four items in this store were damaged that way before anyone
    noticed, because no lint rule reads `part` and nothing else ever would have
    said so. `part` itself survives only as a schema-1 migration field, which is
    why refusing it here costs nothing that anybody can write.
    """
    if not tokens:
        die("--code needs at least '<kind> <path>'")
    kind, rest = tokens[0], tokens[1:]
    if kind not in CODE_KINDS:
        die("unknown code kind %r -- one of %s" % (kind, ", ".join(CODE_KINDS)))
    if not rest:
        die("code ref %r needs a path" % kind)
    path = rest[0].replace("\\", "/")
    want = 2 if kind == "symbol" else 1
    if len(rest) > want:
        extra = rest[want:]
        die("code ref %r takes '%s' and nothing else, but got %d extra "
            "token%s: %s\n  only 'symbol' refs carry a symbol name, and there "
            "is one ref per --code -- repeat the flag for a second ref:\n"
            "  --code %s %s --code %s"
            % (kind,
               "<kind> <path> <symbol>" if kind == "symbol" else "<kind> <path>",
               len(extra), "" if len(extra) == 1 else "s", " ".join(extra),
               kind, " ".join(rest[:want]), " ".join(extra)))
    return CodeRef(kind, path, rest[1] if len(rest) > 1 else None)
# kb:end write.one-code-ref-per-flag


# ------------------------------------------------------------------- items


def cmd_new(g, a):
    iid = a.id.strip()
    if iid in g.items:
        die("item already exists: %s (see `kb item %s`)" % (iid, iid))
    if "." not in iid:
        die("item id must be '<section>.<slug>'")
    sec = iid.split(".", 1)[0]
    if sec not in g.sections:
        die("no section [%s] -- create it with `kb section new %s --order N`."
            "\nsections: %s" % (sec, sec, ", ".join(sorted(g.sections))))
    if a.type not in g.glossary.types:
        die("unknown type %r -- one of %s"
            % (a.type, ", ".join(sorted(g.glossary.types))))
    if a.status not in g.glossary.statuses:
        die("unknown status %r -- one of %s"
            % (a.status, ", ".join(sorted(g.glossary.statuses))))
    key = read_text(a.key, a.key_file)
    if not key:
        die("--key or --key-file is required")
    ign = cap_guard(g, a.type, key, a.suppress_length, "kb new %s" % iid)

    d = {"section": sec, "type": a.type, "status": a.status, "key": key,
         "meta": {"created": today(), "modified": today(),
                  "verified": today(), "run": g.run}}
    if a.summary:
        d["summary"] = a.summary
    if a.highlight:
        d["highlight"] = True
    if a.keywords:
        d["keywords"] = sorted(set(split_kw(a.keywords)))
    it = Item(iid, d)
    it.code = [parse_code(t) for t in (a.code or [])]
    if ign:
        ign["basis"] = model_mod.basis(g, it, "key-length")
        record_ignore(it, ign)

    g.items[iid] = it
    g.items = {k: g.items[k] for k in sorted(g.items)}
    g.invalidate()

    stamped = [it]
    for tid in resolve(g, a.affects):
        it.affects.append(Edge(tid, a.kind))
    for sid in resolve(g, a.affected_by):
        src = g.items[sid]
        src.affects.append(Edge(iid, a.kind))
        stamped.append(src)
    say("+ %s  [%s]  %s/%s" % (iid, sec, a.type, a.status))
    if ign:
        say("  suppressed key-length -- %s" % ign["reason"])
    _reclaim_legacy(g, iid)
    touch(g, *stamped)
    commit(g, a, "new %s" % iid)


def cmd_rm(g, a):
    it = g.get(a.id)
    need_yes(a, "deleting %s" % it.id)
    inbound = g.inbound.get(it.id, [])
    del g.items[it.id]
    g.invalidate()
    changed = repoint(g, it.id, None)
    say("- %s  (%d inbound edge%s dropped)"
        % (it.id, len(inbound), "" if len(inbound) == 1 else "s"))
    for src, _kind in inbound:
        say("    %s no longer affects it" % src)
    touch(g, *changed)
    commit(g, a, "rm %s" % it.id)


def cmd_rename(g, a):
    it = g.get(a.src)
    new = a.dst.strip()
    if new in g.items:
        die("target id already exists: %s" % new)
    if "." not in new:
        die("item id must be '<section>.<slug>'")
    if new.split(".", 1)[0] != it.section:
        die("that prefix is a section change -- use `kb move %s %s`"
            % (it.id, new.split(".", 1)[0]))
    old = it.id
    changed = rename(g, it, new)
    say("%s -> %s  (%d referring item%s updated)"
        % (old, new, len(changed), "" if len(changed) == 1 else "s"))
    # Edges are repointed above; prose is not, and cannot be -- rewriting
    # someone else's key is an edit, not a repoint. `_note_legacy` warns now
    # and, if anything still cites `old`, keeps `kb lint` finding it later.
    _note_legacy(g, old, "item", new)
    _reclaim_legacy(g, new)
    touch(g, it, *changed)
    commit(g, a, "rename %s" % new)


def cmd_mv_gone(g, a):
    """`kb mv` was renamed, not aliased.

    An alias would be a second way to write one thing, which
    `invariants.total-command-surface` forbids. But a bare "unknown command"
    is a poor migration for a verb that lived in the README and in muscle
    memory, so the name survives exactly long enough to name its replacement.
    """
    die("`kb mv` is now `kb rename`, and `kb section mv` is `kb section "
        "rename`.\n`kb move <id> <section>` is unchanged -- it re-sections, "
        "rather than re-ids within a section.")


def cmd_move(g, a):
    it = g.get(a.id)
    dst = g.section_of(a.section)
    if dst.id == it.section:
        die("%s is already in [%s]" % (it.id, dst.id))
    # The id prefix *is* the section membership, so a move is always a rename.
    new_id = dst.id + "." + it.name
    if new_id in g.items:
        die("target id already exists: %s" % new_id)
    old = it.id
    changed = rename(g, it, new_id, section=dst.id)
    say("%s -> %s  ([%s] -> [%s], %d referring item%s updated)"
        % (old, new_id, old.split(".", 1)[0], dst.id, len(changed),
           "" if len(changed) == 1 else "s"))
    _note_legacy(g, old, "item", new_id)
    _reclaim_legacy(g, new_id)
    touch(g, it, *changed)
    commit(g, a, "move %s" % new_id)


SETTABLE = ("type", "status", "key", "summary")


def cmd_set(g, a):
    it = g.get(a.id)
    if a.field not in SETTABLE:
        die("cannot set %r -- settable: %s (edges, keywords, code refs, "
            "highlight and suppressions have their own commands)"
            % (a.field, ", ".join(SETTABLE)))
    value = read_text(a.value, a.file)
    if value is None:
        die("a value is required (or --file PATH, or --file - for stdin)")
    if a.field == "type" and value not in g.glossary.types:
        die("unknown type %r -- one of %s"
            % (value, ", ".join(sorted(g.glossary.types))))
    if a.field == "status" and value not in g.glossary.statuses:
        die("unknown status %r -- one of %s"
            % (value, ", ".join(sorted(g.glossary.statuses))))
    if a.field == "key" and not value:
        die("a key cannot be empty")
    if a.field == "summary" and not value:
        value = None  # `kb set X summary ""` is how a summary is cleared
    # A type change is the second door onto an over-cap key: the caps differ per
    # type, so moving an edge-case key onto `limitation` can put it over without
    # touching a character of it. Both doors get the same guard.
    ign = None
    if a.field == "key":
        ign = cap_guard(g, it.type, value, a.suppress_length,
                        "kb set %s key" % it.id)
    elif a.field == "type":
        ign = cap_guard(g, value, it.key, a.suppress_length,
                        "kb set %s type %s" % (it.id, value))
    before = getattr(it, a.field)
    setattr(it, a.field, value)
    say("%s %s:" % (it.id, a.field))
    if a.field in ("key", "summary") and before and value and \
            max(len(before), len(value)) > DIFF_THRESHOLD:
        for line in word_diff(before, value):
            say(line)
    else:
        say("  - %s" % (before if before is not None else "(unset)"))
        say("  + %s" % (value if value is not None else "(unset)"))
    # kb:begin write.summary-staleness-nudge
    if a.field == "key" and it.summary and before != value and \
            changed_share(before, value) >= NUDGE_SHARE:
        # `meta` is per item, not per field, so lint can never notice that the
        # summary was written against the old key. This is the moment the
        # staleness is created, so it is the only place to say so -- but only
        # when the key moved far enough that the summary could have fallen
        # behind it.
        say("  ! summary still reads %r -- `kb set %s summary \"…\"` if the "
            "key moved under it" % (it.summary, it.id))
    # kb:end write.summary-staleness-nudge
    if ign:
        ign["basis"] = model_mod.basis(g, it, "key-length")
        record_ignore(it, ign)
        say("  suppressed key-length -- %s" % ign["reason"])
    touch(g, it)
    commit(g, a, "set %s.%s" % (it.id, a.field))


def cmd_highlight(g, a):
    items = [g.get(i) for i in a.ids]
    for it in items:
        it.highlight = not a.off
        say("%s highlight %s" % (it.id, "off" if a.off else "on"))
    touch(g, *items)
    commit(g, a, "highlight")


def cmd_verify(g, a):
    """`verified` is deliberately not `modified` -- re-reading an item and
    finding it still true is what drives reassessment, not editing it."""
    date = a.date or today()
    items = [g.get(i) for i in a.ids]
    for it in items:
        it.meta["verified"] = date
        if g.run:
            it.meta["run"] = g.run
        n = 0
        for ref in it.code:
            d = markers.digest_ref(g.root, ref, it.id)
            if d:
                ref.digest = d
                n += 1
        say("%s verified %s%s"
            % (it.id, date,
               "  (%d digest%s)" % (n, "" if n == 1 else "s") if n else ""))
    commit(g, a, "verify")


def cmd_run(g, a):
    if not a.id:
        out(g.run or "(no run set)")
        return
    g.run = a.id.strip()
    say("ambient run = %s (stamped on every item a mutation touches)" % g.run)
    commit(g, a, "run %s" % g.run)


def cmd_title(g, a):
    """The render's `# <title>` heading. Trivial, and it exists for the same
    reason `kb run` does: a field with no command is a field that can only be
    changed by hand-editing the store, which invariant 4 forbids."""
    if not a.text:
        out(g.title)
        return
    new = " ".join(a.text).strip()
    if not new:
        die("a title cannot be empty")
    say("title %r -> %r" % (g.title, new))
    g.title = new
    commit(g, a, "title")


REASON_REQUIRED = ("--reason is mandatory: a suppression without one is "
                   "indistinguishable from giving up")


def cmd_suppress(g, a):
    """`kb suppress --who WHO --rule RULE --reason "..."`.

    Every argument is named. The three values come off one line of lint output
    and go straight back in, so nothing about the call depends on remembering
    an order -- which matters most to the reader this command has: an agent
    that has just read `kb lint --json` and has no other context. Named flags
    also keep a `who` that starts with `[` or `-` out of argparse's way; the
    positional spelling could not, and `kb code` already carries a scar from
    exactly that (README, "Flags on `kb code` must trail the positionals").

    WHO is whatever the finding's `who` field says. An item id resolves through
    the graph and the entry lives on that item; anything else -- a `[section]`,
    a `[prefix]`, a `keyword:<kw>`, a path -- has no owner, so the entry lives
    in the store's root `lint` key.
    """
    # Local import: `lint` imports this module for `--fix`, so importing it at
    # module scope here is a cycle. Nothing else in `write` needs the rules.
    from . import lint as lint_mod
    if a.list:
        return _suppress_list(g)
    if not a.who or not a.rule:
        die("kb suppress --who WHO --rule RULE --reason \"...\"\n"
            "  (or kb suppress --list; `kb lint --json` prints the exact "
            "command for each finding)")
    if a.rule not in lint_mod.SEVERITY:
        die("unknown rule %r -- `kb lint --list` prints all %d rule names"
            % (a.rule, len(lint_mod.RULES)))
    if a.rule == "code-drift":
        die("code-drift cannot be suppressed: the only basis it could be "
            "granted against is the ref's content, which is what re-fires it, "
            "so the exemption would lapse on the next edit. Re-read the code "
            "and record that instead: kb verify %s" % a.who)
    if a.rule in lint_mod.UNSUPPRESSIBLE:
        die("%s cannot be suppressed: it reports exemptions that no longer "
            "resolve, and an entry silencing it would be invisible to the "
            "rule that would have found it" % a.rule)
    try:
        it = g.get(a.who)
    except KeyError:
        return _suppress_graph(g, a)
    return _suppress_item(g, a, it)


def _suppress_item(g, a, it):
    """A finding whose `who` is an item id."""
    ref = a.ref
    if ref is not None and not 0 <= ref < len(it.code):
        die("%s has %d code ref%s, so --ref %d names nothing"
            % (it.id, len(it.code), "" if len(it.code) == 1 else "s", ref))
    ign = it.lint.get("ignore", [])
    keep = [x for x in ign
            if not (x.get("rule") == a.rule and x.get("ref") == ref)]
    label = a.rule if ref is None else "%s (ref %d)" % (a.rule, ref)
    if a.remove:
        if len(keep) == len(ign):
            die("%s has no suppression for %s" % (it.id, label))
        if keep:
            it.lint["ignore"] = keep
        else:
            it.lint.pop("ignore", None)
        say("%s unsuppressed %s" % (it.id, label))
    else:
        if not a.reason:
            die(REASON_REQUIRED)
        entry = {"rule": a.rule, "reason": a.reason}
        if ref is not None:
            entry["ref"] = ref
        b = model_mod.basis(g, it, a.rule, ref)
        if b:
            entry["basis"] = b
        keep.append(entry)
        # Rule first, then the item-wide entry ahead of the per-ref ones, so
        # the store's order matches how the resolver reads them.
        keep.sort(key=lambda x: (x["rule"],
                                 -1 if x.get("ref") is None else x["ref"]))
        it.lint["ignore"] = keep
        say("%s suppressed %s -- %s" % (it.id, label, a.reason))
        if b:
            say("  granted against this item's %s; the exemption suspends if "
                "that changes" % model_mod.BASIS_FIELDS[a.rule]
                .replace("ref-identity", "code ref path")
                .replace("ref-content", "code ref content")
                .replace("key+summary", "key and summary"))
    touch(g, it)
    commit(g, a, "suppress")


def _suppress_graph(g, a):
    """A finding with no item to own it: `[section]`, `[prefix]`,
    `keyword:<kw>`, or a path. Stored at the root, keyed by the `who` string
    exactly as lint prints it."""
    who = a.who
    if who.startswith("[") and who.endswith("]"):
        name = who[1:-1]
        if name not in g.sections and name not in g.subsections:
            die("no section or subsection '%s' -- `kb index` lists them" % name)
    if a.ref is not None:
        die("--ref names one of an item's code refs, and %s is not an item"
            % who)
    keep = [x for x in g.lint.ignore
            if not (x.get("who") == who and x.get("rule") == a.rule)]
    if a.remove:
        if len(keep) == len(g.lint.ignore):
            die("no suppression for %s on %s" % (a.rule, who))
        g.lint.ignore = keep
        say("unsuppressed %s on %s" % (a.rule, who))
    else:
        if not a.reason:
            die(REASON_REQUIRED)
        keep.append({"who": who, "rule": a.rule, "reason": a.reason})
        g.lint.ignore = keep
        say("suppressed %s on %s -- %s" % (a.rule, who, a.reason))
    # No touch(): there is no item to stamp. The store's own diff is the record.
    commit(g, a, "suppress")


def _suppress_list(g):
    """Every live suppression, named. `kb stats` counts them; counting is what
    keeps them from accumulating unnoticed, and naming is what makes them
    reviewable."""
    n = 0
    for it in g.items.values():
        for x in it.lint.get("ignore", []):
            n += 1
            label = x.get("rule") if x.get("ref") is None \
                else "%s ref %d" % (x.get("rule"), x["ref"])
            out("%-34s %-22s %s" % (it.id, label, x.get("reason") or ""))
    for x in sorted(g.lint.ignore,
                    key=lambda x: (x.get("who") or "", x.get("rule") or "")):
        n += 1
        out("%-34s %-22s %s" % (x.get("who"), x.get("rule"),
                                x.get("reason") or ""))
    out("")
    out("%d suppression%s" % (n, "" if n == 1 else "s"))


def _num(v):
    """6 rather than 6.0, and 0.75 rather than 0.7500000000000001."""
    if isinstance(v, float):
        return ("%.4f" % v).rstrip("0").rstrip(".")
    return str(v)


def cmd_thresholds(g, a):
    """Show or set the per-repo lint thresholds.

    A stored setting with no command could only be changed by hand-editing the
    store, which invariant 4 forbids -- the same reason `kb run` and `kb title`
    exist for one field each.
    """
    if not a.args:
        out("%-22s %-9s %-9s %s" % ("threshold", "value", "default", "read by"))
        for name in sorted(model_mod.THRESHOLDS):
            out("%-22s %-9s %-9s %s"
                % (name, _num(g.lint.threshold(name)),
                   _num(model_mod.THRESHOLDS[name]),
                   model_mod.THRESHOLD_RULES.get(name, "")))
        out("")
        out("%d of %d set in this store · kb thresholds set NAME VALUE"
            % (len(g.lint.thresholds), len(model_mod.THRESHOLDS)))
        return
    verb, rest = a.args[0], a.args[1:]
    if verb not in ("set", "reset"):
        die("kb thresholds [set NAME VALUE | reset NAME]")
    name = rest[0] if rest else None
    if name not in model_mod.THRESHOLDS:
        die("unknown threshold %r -- one of %s"
            % (name, ", ".join(sorted(model_mod.THRESHOLDS))))
    if verb == "reset":
        if len(rest) != 1:
            die("kb thresholds reset NAME")
        if name not in g.lint.thresholds:
            die("%s is not set in this store; it already uses the default, %s"
                % (name, _num(model_mod.THRESHOLDS[name])))
        was = g.lint.thresholds.pop(name)
        say("%s %s -> default %s"
            % (name, _num(was), _num(model_mod.THRESHOLDS[name])))
    else:
        if len(rest) != 2:
            die("kb thresholds set NAME VALUE")
        share = name in model_mod.SHARE_THRESHOLDS
        try:
            val = float(rest[1]) if share else int(rest[1])
        except ValueError:
            die("%s takes %s, not %r"
                % (name, "a share between 0 and 1" if share
                   else "a whole number", rest[1]))
        if share and not 0.0 <= val <= 1.0:
            die("%s is a share: it must be between 0 and 1, not %s"
                % (name, _num(val)))
        if not share and val < 0:
            die("%s must not be negative" % name)
        was = g.lint.threshold(name)
        g.lint.thresholds[name] = val
        say("%s %s -> %s  (read by %s)"
            % (name, _num(was), _num(val),
               model_mod.THRESHOLD_RULES.get(name, "no rule")))
    commit(g, a, "thresholds")


def cmd_legacy(g, a):
    """Show, or retire, a renamed-away item, section or subsection name that
    prose still cites (`lint.legacy`, written by any rename command).

    An entry never needs `rm` to leave the list once it is actually resolved:
    `_autoclear_legacy` drops it the moment the last citing key changes, as
    part of whatever commit made that true. `rm` is the other exit -- a human
    deciding to stop watching for a name regardless.
    """
    if not a.args:
        if not g.lint.legacy:
            out("no legacy names")
            return
        out("%-9s %-18s %-18s %-12s %s"
            % ("kind", "old", "renamed to", "since", "still cited"))
        for x in sorted(g.lint.legacy, key=lambda x: x["name"]):
            n = len(_citing(g, x["name"], subtree=x["kind"] != "item"))
            out("%-9s %-18s %-18s %-12s %s"
                % (x["kind"], x["name"], x.get("renamed_to") or "?",
                   x.get("since") or "?",
                   "%d item%s" % (n, "" if n == 1 else "s") if n else "none"))
        return
    verb, rest = a.args[0], a.args[1:]
    if verb != "rm" or len(rest) != 1:
        die("kb legacy [rm NAME]")
    name = rest[0].strip("[]")
    keep = [x for x in g.lint.legacy if x.get("name") != name]
    if len(keep) == len(g.lint.legacy):
        die("no legacy entry for %r -- `kb legacy` lists them" % name)
    entry = next(x for x in g.lint.legacy if x["name"] == name)
    still = _citing(g, name, subtree=entry["kind"] != "item")
    if still and not (a.yes or a.dry_run):
        die("%d item%s still cite %r in key prose: %s\n"
            "re-run with --yes to retire it anyway, or fix the mentions first"
            % (len(still), "" if len(still) == 1 else "s", name,
               ", ".join(still)))
    if still:
        say("! retiring %s with %d mention%s still unfixed: %s"
            % (_label(entry["kind"], name), len(still),
               "" if len(still) == 1 else "s", ", ".join(still)))
    g.lint.legacy = keep
    say("%s retired -- `kb lint` no longer watches for it"
        % _label(entry["kind"], name))
    commit(g, a, "legacy rm %s" % name)


# ------------------------------------------------------------------- edges


def cmd_edge(g, a):
    verb = a.verb
    if verb not in ("add", "rm", "kind"):
        die("kb edge add|rm|kind <src> <dst> [kind]")
    src, dst = g.get(a.src), g.get(a.dst)
    if src.id == dst.id:
        die("self-edge: %s" % src.id)
    hit = [e for e in src.affects if e.to == dst.id]
    if verb == "add":
        kind = a.kind or "relates"
        if kind not in g.glossary.edge_kinds:
            die("unknown edge kind %r -- one of %s"
                % (kind, ", ".join(sorted(g.glossary.edge_kinds))))
        if hit:
            die("%s already affects %s (%s) -- use `kb edge kind` to relabel"
                % (src.id, dst.id, hit[0].kind))
        src.affects.append(Edge(dst.id, kind))
        say("%s → %s  (%s)" % (src.id, dst.id, kind))
    elif verb == "rm":
        if not hit:
            rev = [e for e in dst.affects if e.to == src.id]
            die("%s does not affect %s%s" % (src.id, dst.id,
                " -- but %s affects %s; edges are one-way, try `kb edge rm %s %s`"
                % (dst.id, src.id, dst.id, src.id) if rev else ""))
        src.affects = [e for e in src.affects if e.to != dst.id]
        say("%s ✗ %s" % (src.id, dst.id))
    else:
        if not a.kind:
            die("kb edge kind <src> <dst> --kind K")
        if a.kind not in g.glossary.edge_kinds:
            die("unknown edge kind %r -- one of %s"
                % (a.kind, ", ".join(sorted(g.glossary.edge_kinds))))
        if not hit:
            die("%s does not affect %s" % (src.id, dst.id))
        say("%s → %s  %s -> %s" % (src.id, dst.id, hit[0].kind, a.kind))
        for e in hit:
            e.kind = a.kind
    touch(g, src)
    commit(g, a, "edge %s" % verb)


# ---------------------------------------------------------------- keywords


def cmd_kw_write(g, a, verb, rest):
    if not rest:
        die("kb kw %s <id> <term> [term...]" % verb)
    it = g.get(rest[0])
    terms = split_kw(rest[1:])
    if not terms:
        die("no keywords given")
    have = set(it.keywords)
    if verb == "add":
        it.keywords = sorted(have | set(terms))
    else:
        missing = [t for t in terms if t not in have]
        if missing:
            die("%s has no keyword %s" % (it.id, ", ".join(missing)))
        it.keywords = sorted(have - set(terms))
    say("%s kw: %s" % (it.id, " · ".join(it.keywords) or "(none)"))
    touch(g, it)
    commit(g, a, "kw %s" % verb)


# --------------------------------------------------------------- code refs


def _reindex_ref_suppressions(it, removed=None):
    """Keep `lint.ignore[].ref` pointing at the ref it was written against.

    `removed` is the index `kb code rm` just popped, or None for the wholesale
    forms (`code clear`, `code add --replace`) where every per-ref entry is
    orphaned at once. Returns the dropped entries so the caller can print them:
    a suppression is a decision with a reason attached, and deleting one
    silently loses the reasoning even when deleting it is right.
    """
    ign = it.lint.get("ignore", [])
    if not any(x.get("ref") is not None for x in ign):
        return []
    kept, dropped = [], []
    for x in ign:
        ref = x.get("ref")
        if ref is None:
            kept.append(x)
        elif removed is None or ref == removed:
            dropped.append(x)
        else:
            if ref > removed:
                x["ref"] = ref - 1
            kept.append(x)
    if kept:
        it.lint["ignore"] = kept
    else:
        it.lint.pop("ignore", None)
    return dropped


def _say_dropped(it, dropped, why):
    for x in dropped:
        say("  dropped %s suppression on %s (%s) -- the reason was: %s"
            % (x.get("rule"), it.id, why, x.get("reason") or "?"))


def _cascade_prefix(g, old, new=None):
    """Root suppressions follow the section or subsection they were granted
    against. `new=None` is a removal, which drops them; a rename repoints
    them, because the exemption was granted against the prefix's contents and
    a rename does not change those.

    Nested prefixes ride along: `[auth.tokens]` is itself a prefix of `auth`,
    so renaming or removing `auth` carries `[auth.tokens]`'s entries with it
    too -- shared by `kb section rename/rm` and `kb subsection rename/rm`.
    """
    keep, dropped = [], []
    for x in g.lint.ignore:
        who = x.get("who") or ""
        if not (who.startswith("[") and who.endswith("]")):
            keep.append(x)
            continue
        name = who[1:-1]
        if name != old and not name.startswith(old + "."):
            keep.append(x)
        elif new is None:
            dropped.append(x)
        else:
            x["who"] = "[%s]" % (new + name[len(old):])
            keep.append(x)
    g.lint.ignore = keep
    for x in dropped:
        say("  dropped %s suppression on %s -- the reason was: %s"
            % (x.get("rule"), x.get("who"), x.get("reason") or "?"))


def cmd_code_fmt(g, a, paths):
    """`kb code fmt [PATH...] [--write]` -- tighten and collapse markers.

    The only place `kb` writes source, and the narrowest one it could be: a
    marker line swaps with a blank line beside it, or several merge into one.
    Nothing that is not a marker or blank is touched, which `markers.fmt_check`
    re-verifies against the produced text before a byte is written -- per file,
    so one refusal does not cost the others.

    Dry by default, and `--write` is the only spelling that touches the tree,
    which is what makes a documented example safe for `tests/test_docs.py` to
    execute against this repo. `--dry-run` overrides `--write`, like everywhere.

    No `commit`: nothing in the graph changed, so the store must not be
    rewritten and the render must not be touched. `flush` prints what `say`
    buffered and stops there.
    """
    root = g.root
    files, skipped = markers.walk_files(root, paths)
    for rel in skipped:
        say("   -- %s: no comment form declared for that extension" % rel)
    touched, refused = [], []
    for rel in files:
        full = os.path.join(root, rel.replace("/", os.sep))
        try:
            with open(full, "r", encoding="utf-8", newline="") as f:
                text = f.read()
        except OSError as e:
            refused.append((rel, "unreadable: %s" % e))
            continue
        if "kb:" not in text:
            continue
        new, why = markers.fmt_text(text, rel)
        if new is None:
            refused.append((rel, why))
            continue
        if new == text:
            continue
        why = markers.fmt_check(text, new, rel)
        if why:
            refused.append((rel, why))
            continue
        touched.append((rel, text, new))
    for rel, why in refused:
        say("!! %s left alone: %s" % (rel, why))
    for rel, old, new in touched:
        for line in difflib.unified_diff(
                old.splitlines(), new.splitlines(), lineterm="",
                fromfile=rel, tofile=rel, n=1):
            say(line)
    writing = a.write and not a.dry_run
    if not touched:
        say("%d file(s) scanned, nothing to tighten or collapse" % len(files))
    elif not writing:
        say("%d of %d file(s) would change -- re-run with --write"
            % (len(touched), len(files)))
    else:
        for rel, _old, new in touched:
            with open(os.path.join(root, rel.replace("/", os.sep)),
                      "w", encoding="utf-8", newline="") as f:
                f.write(new)
        say("%d of %d file(s) written" % (len(touched), len(files)))
        # A marker ref's digest covers the marker lines themselves, so every
        # rewritten region has drifted by definition. Saying so is the honest
        # end of this command: re-stamping `verified` here would be exactly what
        # `write.verified-is-not-modified` forbids, since nobody re-read anything.
        say("marker digests moved -- `kb lint --rule code-drift` names the items "
            "to re-read, `kb verify <id>...` records it")
    flush()


# kb:begin write.code-mv-repoints-paths
def _moved(path, old, new):
    """`path` rewritten for a move of `old` to `new`, or None if it misses.

    Whole segments only: `src/lib` carries `src/lib` and `src/lib/a.ts`, never
    `src/library/a.ts`. A glob under `old` moves like any other path.
    """
    p = (path or "").replace("\\", "/").rstrip("/")
    if p == old:
        return new
    if p.startswith(old + "/"):
        return new + p[len(old):]
    return None


def cmd_code_mv(g, a, rest):
    """`kb code mv OLD NEW` -- follow a file or directory the tree moved.

    Store-only, like `lint --fix`: the move itself is `git mv`, made first, and
    NEW must already be in the tree, so the command cannot leave the graph
    asserting a path that is not there. Every code ref on every item whose path
    is OLD or sits under it is rewritten, of every kind.

    What is keyed by the old path moves with it, in the same mutation: a root
    suppression whose `who` is the path, and the basis of a per-ref
    `ref-identity` exemption -- which digests the path, so leaving it would
    suspend an exemption whose subject only changed address. A basis that
    already disagreed is left alone: it was suspended before the move, and
    re-stamping it would revive it without anyone re-reading the reason.
    Content digests (the verified `digest`, `ref-content` bases) hash the file,
    not its name, and need nothing. Prose is not rewritten; keys citing OLD are
    named so a human can decide.
    """
    if len(rest) != 2:
        die("kb code mv <old-path> <new-path>  -- move it in the tree first "
            "(git mv), then repoint the store")
    old, new = [x.replace("\\", "/").rstrip("/") for x in rest]
    if not old or not new:
        die("kb code mv needs two non-empty paths, relative to %s" % g.root)
    if old == new:
        die("%s is already where it is -- nothing to move" % old)
    hits = []
    for it in sorted(g.items.values(), key=lambda i: i.id):
        for idx, c in enumerate(it.code):
            to = _moved(c.path, old, new)
            if to is not None:
                hits.append((it, idx, to))
    roots = [x for x in g.lint.ignore
             if _moved(x.get("who"), old, new) is not None]
    if not hits and not roots:
        die("no code ref names %s or anything under it -- `kb find %s "
            "--field code` lists the refs that do exist" % (old, old))
    # kb:begin write.code-mv-follows-the-tree
    if not os.path.exists(os.path.join(g.root, new.replace("/", os.sep))):
        die("%s does not exist under %s -- kb never moves source: move it "
            "first (git mv %s %s), then re-run" % (new, g.root, old, new))
    for it, idx, to in hits:
        c = it.code[idx]
        for j, d in enumerate(it.code):
            if j != idx and d.kind == c.kind and d.symbol == c.symbol \
                    and (_moved(d.path, old, new) or d.path) == to:
                die("%s would hold %s twice -- `kb code rm %s %d` drops the "
                    "old ref first" % (it.id, CodeRef(c.kind, to, c.symbol),
                                       it.id, idx))
    for x in roots:
        to = _moved(x["who"], old, new)
        if any(y.get("who") == to and y.get("rule") == x.get("rule")
               for y in g.lint.ignore):
            die("%s is already suppressed on %s -- `kb suppress --who %s "
                "--rule %s --remove` drops the old entry first"
                % (x.get("rule"), to, x["who"], x.get("rule")))
    # kb:end write.code-mv-follows-the-tree
    touched = {}
    for it, idx, to in hits:
        c = it.code[idx]
        follow = [x for x in it.lint.get("ignore", [])
                  if x.get("ref") == idx and x.get("basis")
                  and model_mod.BASIS_FIELDS.get(x.get("rule")) == "ref-identity"
                  and x["basis"] == model_mod.basis(g, it, x["rule"], idx)]
        say("%s code %d: %s → %s" % (it.id, idx, c.path, to))
        c.path = to
        for x in follow:
            x["basis"] = model_mod.basis(g, it, x["rule"], idx)
            say("  %s suppression re-stamped against the new path" % x["rule"])
        touched[it.id] = it
    for x in roots:
        to = _moved(x["who"], old, new)
        say("suppression %s on %s → %s" % (x.get("rule"), x["who"], to))
        x["who"] = to
    for it in sorted(g.items.values(), key=lambda i: i.id):
        if old in (it.key or "") or old in (it.summary or ""):
            say("  note: %s cites %s in its prose, which is not rewritten -- "
                "`kb set %s key` if it should follow" % (it.id, old, it.id))
    say("%d code ref(s) on %d item(s) repointed, %d root suppression(s) "
        "followed" % (len(hits), len(touched), len(roots)))
    touch(g, *touched.values())
    commit(g, a, "code mv")
# kb:end write.code-mv-repoints-paths


def cmd_code_write(g, a, verb, rest):
    # Both take paths, not an id, so they branch before `g.get`.
    if verb == "fmt":
        return cmd_code_fmt(g, a, rest)
    if verb == "mv":
        return cmd_code_mv(g, a, rest)
    if not rest:
        die("kb code add <id> <kind> <path> [symbol] | code rm <id> <n> | "
            "code clear <id> | code fmt [path...] | code mv <old> <new>")
    it = g.get(rest[0])
    rest = rest[1:]
    if verb == "add":
        ref = parse_code(rest)
        if a.replace:
            # Most types require at least one code ref, so a bare `code clear`
            # can never commit for them. Clearing and adding in one mutation is
            # the only way to actually swap an item's code surface.
            _say_dropped(it, _reindex_ref_suppressions(it),
                         "every code ref was replaced")
            say("%s code cleared (%d ref(s))" % (it.id, len(it.code)))
            it.code = []
        elif any(c.kind == ref.kind and c.path == ref.path and
                 c.symbol == ref.symbol for c in it.code):
            die("%s already has that code ref" % it.id)
        it.code.append(ref)
        say("%s code += %s" % (it.id, ref))
    elif verb == "rm":
        if len(rest) != 1 or not rest[0].isdigit():
            die("kb code rm <id> <n>  (0-based, as printed by `kb item`)")
        n = int(rest[0])
        if not 0 <= n < len(it.code):
            die("%s has %d code ref(s), numbered 0..%d; no #%d"
                % (it.id, len(it.code), len(it.code) - 1, n))
        say("%s code -= %s" % (it.id, it.code.pop(n)))
        _say_dropped(it, _reindex_ref_suppressions(it, n),
                     "the ref it named was removed")
    else:
        need_yes(a, "clearing every code ref on %s" % it.id)
        if g.glossary.requires_code(it.type):
            die("%s is a %r, which needs at least one code ref -- to swap the "
                "code surface use `kb code add %s --replace <kind> <path>`"
                % (it.id, it.type, it.id))
        _say_dropped(it, _reindex_ref_suppressions(it),
                     "every code ref was cleared")
        say("%s code cleared (%d ref(s))" % (it.id, len(it.code)))
        it.code = []
    touch(g, it)
    commit(g, a, "code %s" % verb)


# ---------------------------------------------------------------- sections


def cmd_section_write(g, a, verb, rest):
    if verb == "new":
        if not rest:
            die("kb section new <id> --order N [--label L]")
        sid = rest[0].strip("[]")
        if sid in g.sections:
            die("section [%s] already exists" % sid)
        if a.order is None:
            die("--order is required: it is what fixes the section's place "
                "in the render")
        from .model import Section
        g.sections[sid] = Section(sid, {"order": a.order,
                                        "label": a.label or sid})
        g.sections = {k: g.sections[k] for k in sorted(g.sections)}
        say("+ [%s]  order %d" % (sid, a.order))
        _reclaim_legacy(g, sid)
    elif verb == "rm":
        sec = g.section_of(rest[0] if rest else "")
        members = g.section_items(sec.id)
        if members:
            die("[%s] still holds %d item(s) -- move them first "
                "(`kb move <id> <section>`)" % (sec.id, len(members)))
        need_yes(a, "deleting section [%s]" % sec.id)
        del g.sections[sec.id]
        _cascade_prefix(g, sec.id)
        say("- [%s]" % sec.id)
    elif verb == "rename":
        if len(rest) != 2:
            die("kb section rename <old> <new>")
        sec = g.section_of(rest[0])
        new = rest[1].strip("[]")
        if new in g.sections:
            die("section [%s] already exists" % new)
        old = sec.id
        n = len(g.section_items(old))
        from .model import Section
        del g.sections[sec.id]
        g.sections[new] = Section(new, {"order": sec.order,
                                        "label": new if sec.label == sec.id
                                        else sec.label})
        g.sections = {k: g.sections[k] for k in sorted(g.sections)}
        touched = _rename_prefix(g, old, new, resection=True)
        say("[%s] -> [%s]  (%d item%s re-id'd)"
            % (old, new, n, "" if n == 1 else "s"))
        _reclaim_legacy(g, new)
        _note_legacy(g, old, "section", new)
        touch(g, *touched)
    elif verb == "order":
        if len(rest) != 2 or not rest[1].lstrip("-").isdigit():
            die("kb section order <id> <N>")
        sec = g.section_of(rest[0])
        say("[%s] order %d -> %s" % (sec.id, sec.order, rest[1]))
        sec.order = int(rest[1])
    elif verb == "label":
        if len(rest) < 2:
            die("kb section label <id> <text>")
        sec = g.section_of(rest[0])
        say("[%s] label %r -> %r" % (sec.id, sec.label, " ".join(rest[1:])))
        sec.label = " ".join(rest[1:])
    else:
        die("kb section new|rm|rename|order|label ...")
    commit(g, a, "section %s" % verb)


def cmd_subsection_write(g, a, verb, rest):
    from .model import Subsection
    if verb == "new":
        if not rest:
            die("kb subsection new <prefix> --order N [--label L] [--summary S]")
        pid = rest[0].strip("[]")
        if pid in g.subsections:
            die("subsection [%s] already exists" % pid)
        if "." not in pid:
            die("a subsection prefix is '<section>.<slug>' -- [%s] names a "
                "section, and sections are `kb section new`" % pid)
        if pid.split(".", 1)[0] not in g.sections:
            die("no section [%s] -- create it with `kb section new %s --order N`"
                % (pid.split(".", 1)[0], pid.split(".", 1)[0]))
        if a.order is None:
            die("--order is required: it is what fixes the group's place "
                "in the render")
        g.subsections[pid] = Subsection(pid, {
            "order": a.order,
            "label": a.label or pid.rsplit(".", 1)[-1],
            "summary": a.summary})
        g.subsections = {k: g.subsections[k] for k in sorted(g.subsections)}
        n = len(g.subsection_members(pid))
        say("+ [%s]  order %d  (%d existing item%s adopted)"
            % (pid, a.order, n, "" if n == 1 else "s"))
        _reclaim_legacy(g, pid)
    elif verb == "rm":
        if not rest:
            die("kb subsection rm <prefix> --yes")
        pid = rest[0].strip("[]")
        if pid not in g.subsections:
            die("no such subsection: %s" % pid)
        members = g.subsection_members(pid)
        if members:
            die("[%s] still groups %d item(s) -- rename them out of the prefix "
                "first (`kb rename <id> <new-id>`)" % (pid, len(members)))
        need_yes(a, "deleting subsection [%s]" % pid)
        del g.subsections[pid]
        _cascade_prefix(g, pid)
        say("- [%s]" % pid)
    elif verb == "rename":
        if len(rest) != 2:
            die("kb subsection rename <old> <new>")
        old = rest[0].strip("[]")
        if old not in g.subsections:
            die("no such subsection: %s" % old)
        new = rest[1].strip("[]")
        if new in g.subsections:
            die("subsection [%s] already exists" % new)
        if "." not in new:
            die("a subsection prefix is '<section>.<slug>'")
        if new.split(".", 1)[0] != old.split(".", 1)[0]:
            die("that changes the section -- rename within [%s], `kb "
                "subsection rm` and re-`new` it under the other one instead"
                % old.split(".", 1)[0])
        n = len(g.subsection_members(old))
        touched = _rename_prefix(g, old, new)
        say("[%s] -> [%s]  (%d item%s re-id'd)"
            % (old, new, n, "" if n == 1 else "s"))
        _reclaim_legacy(g, new)
        _note_legacy(g, old, "subsection", new)
        touch(g, *touched)
    elif verb in ("order", "label", "summary"):
        if len(rest) < 2:
            die("kb subsection %s <prefix> <value>" % verb)
        pid = rest[0].strip("[]")
        if pid not in g.subsections:
            die("no such subsection: %s" % pid)
        sub = g.subsections[pid]
        if verb == "order":
            if not rest[1].lstrip("-").isdigit():
                die("kb subsection order <prefix> <N>")
            say("[%s] order %d -> %s" % (pid, sub.order, rest[1]))
            sub.order = int(rest[1])
        elif verb == "label":
            say("[%s] label %r -> %r" % (pid, sub.label, " ".join(rest[1:])))
            sub.label = " ".join(rest[1:])
        else:
            new = " ".join(rest[1:])
            say("[%s] summary %r -> %r" % (pid, sub.summary, new))
            sub.summary = new or None
    else:
        die("kb subsection new|rm|rename|order|label|summary ...")
    commit(g, a, "subsection %s" % verb)


# ---------------------------------------------------------------- glossary


def cmd_glossary(g, a):
    verb = (a.args[0] if a.args else "")
    rest = a.args[1:]
    if verb not in ("add", "rm"):
        _show_glossary(g)
        return
    if len(rest) < 2:
        die("kb glossary add|rm type|status|edge-kind <name> [...]")
    what, name = rest[0], rest[1]
    if what not in ("type", "status", "edge-kind"):
        die("kb glossary %s type|status|edge-kind <name>" % verb)
    table = {"type": g.glossary.types, "status": g.glossary.statuses,
             "edge-kind": g.glossary.edge_kinds}[what]
    if verb == "rm":
        if name not in table:
            die("no such %s: %s" % (what, name))
        users = _glossary_users(g, what, name)
        if users:
            die("%s %r is used by %d item(s): %s"
                % (what, name, len(users), ", ".join(users[:8])))
        need_yes(a, "removing %s %r" % (what, name))
        del table[name]
        say("- %s %s" % (what, name))
    else:
        spec = dict(table.get(name, {}))
        if a.desc:
            spec["desc"] = a.desc
        if what == "type":
            if a.key_max is not None:
                spec["key_max"] = a.key_max
            if a.modal:
                spec["requires_modal"] = True
            if a.no_modal:
                spec.pop("requires_modal", None)
            if a.no_code:
                spec["requires_code"] = False
            if a.needs_code:
                spec.pop("requires_code", None)
            if a.no_edges:
                spec["requires_edges"] = False
            if a.needs_edges:
                spec.pop("requires_edges", None)
        if what == "edge-kind":
            if a.label:
                spec["label"] = a.label
            if a.inverse:
                spec["inverse"] = a.inverse
            if "inverse" not in spec:
                die("--inverse is required: the render names both ends of "
                    "the arrow")
        verbed = "updated" if name in table else "+"
        table[name] = spec
        say("%s %s %s  %s" % (verbed, what, name, spec))
    commit(g, a, "glossary %s" % verb)


def _glossary_users(g, what, name):
    hits = []
    for it in g.items.values():
        if what == "type" and it.type == name:
            hits.append(it.id)
        elif what == "status" and it.status == name:
            hits.append(it.id)
        elif what == "edge-kind" and any(e.kind == name for e in it.affects):
            hits.append(it.id)
    return sorted(hits)


def _show_glossary(g):
    out("types")
    for k in sorted(g.glossary.types):
        spec = g.glossary.types[k]
        flags = []
        if spec.get("key_max"):
            flags.append("key ≤ %d" % spec["key_max"])
        if spec.get("requires_modal"):
            flags.append("MUST/NEVER/ONLY/EXACTLY")
        if spec.get("requires_code") is False:
            flags.append("no code ref needed")
        if spec.get("requires_edges") is False:
            flags.append("edgeless by design")
        out("  %-14s %s%s" % (k, spec.get("desc", ""),
                              ("  [%s]" % " · ".join(flags)) if flags else ""))
    out("statuses")
    out("  " + " · ".join(sorted(g.glossary.statuses)))
    out("edge kinds")
    for k in sorted(g.glossary.edge_kinds):
        spec = g.glossary.edge_kinds[k]
        out("  %-14s → %-16s ← %s"
            % (k, spec.get("label", k), spec.get("inverse", "")))


# ------------------------------------------------------- read/write dispatch
#
# `kb code <id>`, `kb kw <term>` and `kb section <name>` are read commands
# that predate the writers; the coverage table names the writers `kb code
# add`, `kb kw add`, `kb section new`. Rather than split them into second
# top-level names, the first positional picks the side.


# kb:begin write.dual-dispatch
def dispatch(read_fn, write_fn, field, verbs=VERBS):
    """Route to the write path when the first positional is one of `verbs`.

    Per command rather than the union: an unimplemented verb used to reach the
    write function anyway and fall out of its `if/elif` chain into whatever the
    last branch was, which for `kb kw` is removal. Narrowing it sends the same
    input to the read path instead, where it is an unknown id and says so.
    """
    def go(g, a):
        argv = list(getattr(a, field) or [])
        if argv and argv[0] in verbs:
            return write_fn(g, a, argv[0], argv[1:])
        return read_fn(g, a)
    return go
# kb:end write.dual-dispatch
