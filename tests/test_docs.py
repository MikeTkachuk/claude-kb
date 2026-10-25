#!/usr/bin/env python3
"""Check that every `kb ...` invocation in the docs is real.

Documentation drift is the failure mode this project can least afford: the docs
are read by an agent that will then act on them, and a plausible-looking command
that does not parse is worse than no example, because it is trusted.

Two sources, since a wrong example is no better for being in `--help`:

  the docs     every `kb ...` line in a fenced block of README.md / DESIGN.md /
               CHANGELOG.md
  the parser   every example in a subcommand's epilog, i.e. what `kb X --help`
               prints

Two passes over them, both against the live argument parser rather than a copy:

  static   command exists, every flag is known to *that* command, and every
           flag that needs a value has one. Placeholders and pipes are fine
           here, so this covers the examples that cannot be run as written.

  live     the read-only invocations that contain no placeholder are executed
           against this repo's own store and must exit 0.

Usage: python tests/test_docs.py [-v] [doc.md ...]
"""

import argparse
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from kbgraph.cli import build_parser  # noqa: E402

# CHANGELOG.md is checked with the rest: an entry describing a command is read
# by someone deciding whether to upgrade, and a removed command left quoted in
# the entry that introduced it is exactly the drift this checker exists for.
DOCS = ("README.md", "DESIGN.md", "CHANGELOG.md")

# A token that stands for something the reader supplies. An example containing
# one is checked statically but never executed.
PLACEHOLDER = re.compile(r"[<>…]|\.\.\.$|^(FILE|TEXT|PATH|ID|TYPE|STATUS|NAME|"
                         r"RULE|KIND|REGEX|NNN|N|T|F|D|L|K|R)$")

# Commands that cannot change anything, so they are safe to run here. `section`,
# `code` and `kw` are dual-purpose: they only stay read-only while their first
# positional is not a write verb, which is checked separately.
READ_ONLY = {
    "ctx", "item", "meta", "index", "section", "subsection", "find",
    "guardrails", "tree", "impact", "path", "code", "hubs", "orphans", "stats",
    "kw", "kw-suggest", "dup", "relate", "validate", "lint", "render",
    "glossary", "title", "run", "debt", "thresholds",
}
# `baseline` is deliberately NOT in READ_ONLY: its only verb writes a file, so
# a documented `kb baseline write` would be executed against this repo during
# the suite and drop .kb-baseline.json into the working tree.
# Mirrors write.VERBS. Kept as its own literal because this checker must run
# against the parser without importing the write layer -- but it must not
# drift: a verb missing here is a documented mutation that the live pass would
# execute against this repo's own store.
WRITE_VERBS = {"add", "rm", "clear", "new", "rename", "order", "label",
               "summary", "kind", "set", "write", "reset", "fmt", "mv"}

VERBOSE = "-v" in sys.argv
PASS, FAIL = [0], []


def ok(cond, what, detail=""):
    if cond:
        PASS[0] += 1
        if VERBOSE:
            print("  ok   %s" % what)
    else:
        FAIL.append(what)
        print("  FAIL %s%s" % (what, ("\n       " + detail) if detail else ""))


# --------------------------------------------------------------- extraction


def invocations(text):
    """Every `kb ...` command line inside a fenced code block.

    Only fenced blocks: prose and table cells carry syntax summaries such as
    `kb kw add|rm <id>`, which are documentation of a shape rather than a
    command anyone would type.
    """
    out, fenced, buf = [], False, ""
    for raw in text.splitlines():
        if raw.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            continue
        line = raw.strip()
        if buf:                                  # continuation of a `\` line
            buf = buf[:-1].rstrip() + " " + line
        elif line.startswith("$ kb ") or line == "$ kb" or \
                line.startswith("kb ") or line == "kb":
            buf = line[2:].lstrip() if line.startswith("$ ") else line
        else:
            continue
        if buf.endswith("\\"):
            continue
        out.append(buf)
        buf = ""
    return out


def segments(cmd):
    """Split a pipeline and keep the parts that are kb invocations, dropping a
    leading `xargs` so `... | xargs kb verify` is checked too."""
    out = []
    for part in cmd.split("|"):
        toks = part.strip().split()
        while toks and toks[0] in ("xargs", "sh", "sudo"):
            toks = toks[1:]
        if toks and toks[0] == "kb":
            out.append(" ".join(toks))
    return out


def clean(cmd):
    """Reduce a documented line to just the command.

    Two things get dropped: a trailing shell comment, and the aligned
    description column that help examples carry (`kb section auth --full
    <spaces> full keys, not truncated`). Both are prose, and feeding prose to
    shlex is how a checker invents failures the docs do not have.
    """
    q = None
    for i, ch in enumerate(cmd):
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "#" and i and cmd[i - 1].isspace():
            cmd = cmd[:i]
            break
    return re.split(r"\s{3,}", cmd.strip())[0].rstrip()


# ------------------------------------------------------------ static checks


def flag_table(parser):
    """{command: {flag: takes_a_value}}, plus the top-level flags under None."""
    sub = [a for a in parser._actions
           if isinstance(a, argparse._SubParsersAction)][0]
    table = {None: _flags(parser)}
    for name, sp in sub.choices.items():
        table[name] = _flags(sp)
    return table, sub.choices


def _flags(p):
    """{flag: does it REQUIRE a value}.

    `nargs="?"` means the value is optional, so a bare occurrence is legal --
    `kb lint --baseline` and `kb lint --baseline other.json` are both correct.
    Classing it with the value-taking flags reported the documented bare form
    as missing an argument, which is the checker being wrong about a shape it
    had not seen rather than the documentation being stale.
    """
    out = {}
    for act in p._actions:
        for opt in act.option_strings:
            out[opt] = act.nargs not in (0, "?") and not isinstance(
                act, (argparse._StoreTrueAction, argparse._StoreFalseAction,
                      argparse._HelpAction, argparse._VersionAction,
                      argparse._CountAction))
    return out


def check_static(cmd, table, choices, where):
    try:
        toks = shlex.split(cmd, posix=True)
    except ValueError as e:
        ok(False, "%s: %s" % (where, cmd), "unparseable quoting: %s" % e)
        return None
    toks = toks[1:]                                    # drop "kb"

    globals_ = table[None]
    i, name = 0, None
    while i < len(toks):
        t = toks[i]
        if t.startswith("-"):
            flag = t.split("=", 1)[0]
            if flag not in globals_:
                ok(False, "%s: %s" % (where, cmd),
                   "unknown top-level flag %s" % flag)
                return None
            if globals_[flag] and "=" not in t:
                i += 1
        else:
            name = t
            break
        i += 1

    if name is None:                     # `kb --help`, `kb --version`
        return None
    if name not in choices:
        ok(False, "%s: %s" % (where, cmd), "no such command: %s" % name)
        return None

    known = dict(table[name])
    known.update(globals_)
    rest = toks[i + 1:]
    j = 0
    while j < len(rest):
        t = rest[j]
        if t == "--":
            break
        if t.startswith("-") and t != "-":
            flag = t.split("=", 1)[0]
            if flag not in known:
                ok(False, "%s: %s" % (where, cmd),
                   "%s does not take %s" % (name, flag))
                return None
            if known[flag] and "=" not in t:
                if j + 1 >= len(rest):
                    ok(False, "%s: %s" % (where, cmd),
                       "%s needs a value" % flag)
                    return None
                j += 1
        j += 1
    ok(True, "%s: %s" % (where, cmd))
    return name, toks[i + 1:]


# -------------------------------------------------------------- live checks


def runnable(name, args):
    if name not in READ_ONLY:
        return False
    if args and args[0] in WRITE_VERBS:          # `section new`, `code add`
        return False
    if "--fix" in args:                          # lint --fix writes
        return False
    for a in args:
        if PLACEHOLDER.search(a):
            return False
    return True


def check_live(name, args):
    p = subprocess.run([sys.executable, "-m", "kbgraph.cli", name] + args,
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src")))
    detail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
    ok(p.returncode == 0, "runs: kb %s %s" % (name, " ".join(args)),
       "exit %d: %s" % (p.returncode, detail[-1] if detail else ""))


def help_invocations(choices):
    """Every `kb ...` example in a subcommand's epilog.

    These are the examples a reader meets first, and they are hand-written prose
    inside the parser rather than anything the parser itself checks. They name
    ids from whatever repo they were written for, so they are checked statically
    and never executed.

    Epilogs only. A `description` carries the *shape* of a command --
    `kb section new|rm|rename|order|label` -- which is documentation of a family
    rather than anything anyone would type.
    """
    out = []
    for name, sp in sorted(choices.items()):
        buf = ""
        for raw in (sp.epilog or "").splitlines():
            line = raw.strip()
            if buf:
                buf = buf[:-1].rstrip() + " " + line
            elif line.startswith("kb ") or line == "kb":
                buf = line
            else:
                continue
            if buf.endswith("\\"):
                continue
            out.append(("kb %s --help" % name, buf))
            buf = ""
    return out


def test_documented_commands_are_real():
    """Every `kb ...` in the docs and in every --help epilog parses, and the
    runnable ones exit 0.

    Deliberately one test over hundreds of checks, rather than one test each:
    the useful answer to "did the docs drift" is the whole list of what drifted,
    and a suite that stopped at the first stale example would have you fix and
    re-run it once per example.
    """
    failed, total, live = _run_checks(list(DOCS))
    assert not failed, ("%d of %d documented invocations are wrong (%d executed):\n%s"
                        % (len(failed), total, live,
                           "\n".join("  - %s" % f for f in failed)))


def _run_checks(docs):
    del FAIL[:]
    PASS[0] = 0
    parser = build_parser()
    table, choices = flag_table(parser)

    total = live = 0
    for where, cmd in help_invocations(choices):
        for seg in segments(clean(cmd)):
            total += 1
            check_static(seg, table, choices, where)
    for doc in docs:
        path = os.path.join(ROOT, doc)
        if not os.path.exists(path):
            ok(False, "doc exists: %s" % doc, "not found")
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for cmd in invocations(text):
            for seg in segments(clean(cmd)):
                total += 1
                got = check_static(seg, table, choices, doc)
                if got and runnable(*got):
                    live += 1
                    check_live(*got)

    print("\n%d invocations in %s · %d executed · %d checks passed, %d failed"
          % (total, ", ".join(docs), live, PASS[0], len(FAIL)))
    return list(FAIL), total, live


if __name__ == "__main__":
    # Still usable directly, which is how you point it at one doc:
    #     python tests/test_docs.py README.md
    _failed, _total, _live = _run_checks(
        [a for a in sys.argv[1:] if a.endswith(".md")] or list(DOCS))
    for _name in _failed:
        print("  - %s" % _name)
    sys.exit(1 if _failed else 0)
