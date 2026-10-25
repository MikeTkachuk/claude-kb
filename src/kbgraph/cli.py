"""kb -- query, lint and author the knowledgebase graph.

The JSON store is the source of truth; the markdown is a render of it. Every
question the graph can answer has a command, and every writable field has
exactly one command, so nothing ever needs to be hand-edited.

Mutations apply immediately (git is the undo). `--dry-run` shows the store
diff without writing; destructive commands additionally require `--yes`.
"""

import argparse
import os
import sys
from importlib.metadata import PackageNotFoundError, metadata as pkg_metadata

from . import ctx
from . import debt as debt_mod
from . import lint as lint_mod
from . import read
from . import render as render_mod
from . import store as store_mod
from . import write
from . import __version__
from .model import SCHEMA, Graph, SchemaError
from .store import KBNotFound, SchemaTooOld
from .write import commit, die, out


# ------------------------------------------------------------------ commands

# What `kb init` seeds. A store with an empty glossary accepts no item at all
# (`--type` must name a known type), so "empty" would mean "unusable until you
# have run eight glossary commands". These are the vocabulary the schema was
# designed around; edit any of it afterwards with `kb glossary`.
SEED_TYPES = (
    ("guardrail", "rule or contract whose violation breaks behavior", 320,
     {"requires_modal": True}),
    ("contract", "shape or protocol co-owned with another system", 400, {}),
    ("concept", "definition or invariant of the area", 400, {}),
    ("behavior", "what the main path actually does", 320, {}),
    ("edge-case", "what happens at a boundary or exceptional path", 240, {}),
    ("limitation", "known gap, deliberately unfixed", 240, {}),
    ("design-choice", "a road taken, with the road not taken", 400, {}),
    ("directive", "user-owned top-level priority; overrides every other item",
     1200, {"requires_code": False, "requires_edges": False}),
)
SEED_EDGE_KINDS = (
    ("constrains", "Constrains", "Constrained by"),
    ("calls", "Calls", "Called by"),
    ("consumes", "Consumes", "Consumed by"),
    ("refines", "Refines", "Refined by"),
    ("relates", "Relates to", "Referenced by"),
)


def seed_glossary():
    types = {}
    for name, desc, cap, extra in SEED_TYPES:
        spec = {"desc": desc, "key_max": cap}
        spec.update(extra)
        types[name] = spec
    return {
        "types": types,
        "statuses": {"active": {}, "todo": {}, "deprecated": {}},
        "edge_kinds": {k: {"label": lab, "inverse": inv}
                       for k, lab, inv in SEED_EDGE_KINDS},
    }


def cmd_init(a):
    """Create a store. Runs before the load step, since there is nothing to
    load -- the same exemption `migrate` has."""
    path = os.path.abspath(a.kb or os.path.join(os.getcwd(), store_mod.STORE))
    if os.path.exists(path):
        die("%s already exists -- `kb stats` to see what is in it" % path)
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        die("no such directory: %s" % parent)
    g = Graph({"schema": SCHEMA, "title": a.title, "run": a.run,
               "glossary": {} if a.bare else seed_glossary(),
               "sections": {}, "items": {}}, path=path)
    g.validate()
    store_mod.save(g)
    render_mod.write(g)
    out("wrote %s + %s" % (store_mod.STORE, store_mod.RENDER))
    out("")
    out("next:")
    out("  kb glossary                              the seeded vocabulary"
        if not a.bare else
        "  kb glossary add type <name> --desc ...   nothing is defined yet")
    out("  kb section new <name> --order 10         sections order the render")
    out("  kb new <name>.<slug> -t concept --key \"...\" --code file <path>")
    out("  kb lint                                  what is still missing")


def _docs_text():
    """README.md and CHANGELOG.md, concatenated in that order.

    Sourced from dist-info/METADATA, not a file read: pyproject's dynamic
    `readme` key bakes both files into the Description field for every wheel
    and sdist, so nothing extra needs to ship for either doc to work
    installed. The fallback below only fires in a checkout with no build
    metadata yet (e.g. before the first `pip install -e .`)."""
    try:
        return pkg_metadata("kb-graph").get_payload()
    except PackageNotFoundError:
        pass
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    try:
        with open(os.path.join(root, "README.md"), encoding="utf-8") as f:
            manual = f.read()
        with open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8") as f:
            changelog = f.read()
    except OSError:
        die("no docs available -- neither installed metadata nor a checkout "
            "next to src/")
    return manual.rstrip("\n") + "\n\n" + changelog


def cmd_manual(a):
    """Print README.md. Runs before the load step -- a manual is not about any
    one store, the same exemption `init` and `migrate` have.

    The two docs share one field (see `_docs_text`); CHANGELOG.md's own
    top heading is the only place "# Changelog" starts a line, so it is
    the split point back to just the README half."""
    text = _docs_text()
    i = text.find("\n# Changelog")
    sys.stdout.write(text[:i] if i != -1 else text)


def cmd_changelog(a):
    """Print CHANGELOG.md. Runs before the load step, same as `manual`."""
    text = _docs_text()
    i = text.find("\n# Changelog")
    if i == -1:
        die("no CHANGELOG section found in the docs metadata")
    sys.stdout.write(text[i + 1:])


def cmd_render(g, a):
    if a.check:
        ok, msg = render_mod.check(g)
        out(msg)
        if not ok:
            raise SystemExit(1)
        return
    if a.stdout:
        sys.stdout.write(render_mod.render(g))
        return
    try:
        path = render_mod.write(g, force=a.force)
    except render_mod.LegacyRender as e:
        die(str(e))
    out("wrote %s" % path)


def cmd_validate(g, a):
    out("ok: schema %d, %d sections, %d items, %d edges"
        % (g.schema, len(g.sections), len(g.items),
           sum(len(i.affects) for i in g.items.values())))


def cmd_fmt(g, a):
    commit(g, a, "canonical")


def _migrate(a):
    # Deferred: migrate pulls in git plumbing and the v1 parser, neither of
    # which any other command needs.
    from .migrate import cmd_migrate
    return cmd_migrate(a)


# ---------------------------------------------------------------- dispatch

class Fmt(argparse.RawDescriptionHelpFormatter):
    """Keep the hand-written descriptions and examples verbatim, and widen the
    help column: at argparse's default of 24 the longest command names
    (`guardrails`, `kw-suggest`) and flags (`--include-roots`) wrap onto a
    second line, which turns the command list into a 60-line scroll."""

    def __init__(self, prog, indent_increment=2, max_help_position=31,
                 width=None):
        argparse.RawDescriptionHelpFormatter.__init__(
            self, prog, indent_increment, max_help_position, width)

    def add_argument(self, action):
        argparse.RawDescriptionHelpFormatter.add_argument(self, action)
        # argparse sizes the help column from each subcommand name measured at
        # its *group's* indent, then prints the names two columns deeper -- so
        # the longest ones always overflow by exactly the difference. Widening
        # max_help_position alone cannot fix it; the column is a min() of that
        # and this measurement.
        if action.choices and hasattr(action, "_get_subactions"):
            longest = max([len(str(c)) for c in action.choices] or [0])
            self._action_max_length = max(
                self._action_max_length,
                longest + self._current_indent + self._indent_increment)

EPILOG = """\
the loop:
  kb ctx --path <file>        before editing anything -- every rule governing it
  kb dup "<the rule>"         before adding an item -- does it already exist?
  kb impact <id>              after a change -- everything to re-verify
  kb verify <id>...           once re-checked against the code and still true
  kb lint --errors-only       what the pre-commit hook blocks on

ids resolve by exact match, then unique suffix, then unique substring, so
`kb item single-fetch-path` finds `auth.single-fetch-path`. An ambiguous
fragment lists the candidates instead of guessing.

--kb / --root / --dry-run are accepted before or after COMMAND.

exit codes:
  0  ok
  1  a check failed: lint errors, render drift, no path between two items
  2  bad usage, or a mutation refused before it wrote anything
"""


# kb:begin cli.global-flags-work-on-both-sides
def global_flags():
    """The flags every command honours.

    They are attached to the top-level parser *and*, via `parents`, to every
    subparser, so `kb --dry-run set x key "..."` and `kb set x key "..."
    --dry-run` both work -- `--dry-run` is the flag most likely to be typed
    after the command, and argparse rejects that unless the subparser owns it
    too. `default=SUPPRESS` is what makes sharing them safe: without it the
    subparser's own default would overwrite a value the top-level parser had
    already set, silently turning `kb --dry-run ...` into a real write.
    """
    g = argparse.ArgumentParser(add_help=False)
    grp = g.add_argument_group("global flags (before or after COMMAND)")
    grp.add_argument("--kb", default=argparse.SUPPRESS,
                     help="path to knowledgebase.json (default: search upward "
                          "from CWD)")
    grp.add_argument("--root", default=argparse.SUPPRESS,
                     help="resolve code paths from here (default: the store's "
                          "own directory)")
    grp.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                     help="print the store diff instead of writing; also "
                          "satisfies --yes")
    return g
# kb:end cli.global-flags-work-on-both-sides


def build_parser():
    parent = global_flags()
    p = argparse.ArgumentParser(
        prog="kb", description=__doc__, epilog=EPILOG,
        formatter_class=Fmt, parents=[parent])
    p.add_argument("--version", action="version",
                   version="kb-graph %s (schema %d)" % (__version__, SCHEMA))
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND",
                           title="commands")

    def add(name, fn, help_, description=None, epilog=None):
        s = sub.add_parser(name, help=help_,
                           description=(description or help_).rstrip(),
                           epilog=epilog, formatter_class=Fmt,
                           parents=[parent])
        s.set_defaults(fn=fn)
        return s

    # ---- context (the entry point; everything below is the precise layer)
    s = add("ctx", ctx.cmd_ctx, "BEFORE EDITING: rules, neighbourhood, code",
            """\
Assemble a complete working context in one call, and the mandatory first step
before touching any file.

Four blocks, in this order: the matched root items in full; their neighbourhood
out to --depth, each line labelled with the edge kind and the direction it was
reached by; the sections in play with their highlighted items; and every
directive. Directives are always included because they override every other
item -- a context that omits them is one that can argue you into violating one.

Pick the roots one of three ways -- by id, by --path or by --query. If more
than one is given, --path wins, then --query, then the ids.""",
            """\
examples:
  kb ctx --path src/lib/api.ts             every rule anchored at a file
  kb ctx --path src/lib/api.ts --code      ... with the marker snippets inline
  kb ctx auth.single-fetch-path --depth 2  a rule and its neighbourhood
  kb ctx --query "credit balance" -n 3     relevance-ranked entry, cold
  kb ctx auth.token --budget 4000          degrade to fit a context window

--path matching is exact, then path-suffix, then loose (basename or substring);
the best tier that hits at all wins. If nothing is anchored at the file, that
means no rule is *recorded* for it -- not that none applies -- and the command
says so and points at --query.

--budget degrades in steps: neighbour key text first, then the length of the
neighbour list, then the neighbourhood block entirely. The head, the root
items and the directives are never dropped.""")
    s.add_argument("ids", nargs="*", metavar="ID",
                   help="root items to build the context around")
    s.add_argument("--path", metavar="FILE",
                   help="roots = every item with a code ref to this file")
    s.add_argument("--query", metavar="TEXT",
                   help="roots = the best-scoring items for this text")
    s.add_argument("--depth", type=int, default=1,
                   help="how far to follow edges from the roots (default: 1)")
    s.add_argument("--direction", "-d", choices=["down", "up", "both"],
                   default="both",
                   help="down = what the roots affect, up = what affects them "
                        "(default: both)")
    s.add_argument("--kind", action="append", metavar="KIND",
                   help="only follow these edge kinds (repeatable)")
    s.add_argument("--code", action="store_true",
                   help="inline the marker snippets for the root items")
    s.add_argument("--max-lines", type=int, default=40, metavar="N",
                   help="cap each snippet at N lines (default: 40)")
    s.add_argument("--budget", type=int, default=0, metavar="CHARS",
                   help="cap total output, dropping detail by priority "
                        "(default: no cap)")
    s.add_argument("--no-directives", action="store_true",
                   help="omit the directive block (rarely what you want)")
    s.add_argument("-n", type=int, default=5, metavar="N",
                   help="how many --query hits to use as roots (default: 5)")

    # ---- query
    s = add("item", read.cmd_item, "print items in full",
            """\
Print each item's key, code refs, edges in both directions grouped by kind,
and keywords. Dates and suppressions are not here -- they are `kb meta`.""",
            "examples:\n"
            "  kb item auth.single-fetch-path\n"
            "  kb item single-fetch-path auth.token    several at once")
    s.add_argument("ids", nargs="+", metavar="ID", help="items to print")

    s = add("meta", read.cmd_meta, "dates, run and suppressions for an item",
            """\
Print created / modified / verified / run, plus any lint suppressions with
their reasons.

`modified` moves on every mutation; `verified` moves only when you run
`kb verify`, because editing an item is not the same as re-checking it
against the code. The gap between them is what `verified-stale` measures.""")
    s.add_argument("ids", nargs="+", metavar="ID", help="items to report on")

    s = add("section", write.dispatch(read.cmd_section, write.cmd_section_write,
                                      "names",
                                      verbs=("new", "rm", "rename", "order",
                                             "label")),
            "section knowledge + members, or new|rm|rename|order|label",
            """\
With a section name (or none, for all of them): print the label, the item
count, the highlighted items, the related sections with edge counts, and every
member.

With a verb first, write instead:
  kb section new <id> --order N [--label L]   create one
  kb section rm <id> --yes                    delete an empty one
  kb section rename <old> <new>               rename, re-id'ing every member
  kb section order <id> <N>                   move it in the render
  kb section label <id> <text...>             change its display name

--order is required on `new`: it is the only thing that fixes where the section
lands in the render. `rename` re-ids every member (and every subsection under
it), because an id prefix *is* its section membership; `rm` refuses while the
section still holds items.

`rename` also warns if any item's key still names the old section in prose,
and records it in `kb legacy` -- the same thing `kb rename`, `kb move` and
`kb subsection rename` do for their own retired names. Reusing a retired
section id (`new` or as a rename's destination) reclaims it automatically.""",
            "examples:\n"
            "  kb section                       every section, with members\n"
            "  kb section auth --full           full keys, not truncated\n"
            "  kb section new payments --order 95 --label payments\n"
            "  kb section order payments 45")
    s.add_argument("names", nargs="*", metavar="NAME|VERB",
                   help="sections to print, or a write verb and its arguments")
    s.add_argument("--full", "-f", action="store_true",
                   help="print keys in full instead of truncating them")
    s.add_argument("--order", type=int, metavar="N",
                   help="render position (section new|order)")
    s.add_argument("--label", metavar="TEXT",
                   help="display name (section new)")
    s.add_argument("--yes", action="store_true",
                   help="confirm a destructive write (section rm)")

    s = add("subsection", write.dispatch(read.cmd_subsection,
                                         write.cmd_subsection_write, "names",
                                         verbs=("new", "rm", "rename", "order",
                                                "label", "summary")),
            "grouped items inside a section, or new|rm|rename|order|label|summary",
            """\
A subsection groups items sharing an id prefix under one heading with a
paragraph of orientation. Membership is derived from the prefix -- the deepest
declared one wins -- so nothing is stored twice and nothing can drift.

With a prefix (or none, for all of them): print the label, summary and members.

With a verb first, write instead:
  kb subsection new <prefix> --order N [--label L] [--summary S]
  kb subsection rm <prefix> --yes             refuses while members exist
  kb subsection rename <old> <new>            rename, re-id'ing every member
  kb subsection order <prefix> <N>
  kb subsection label <prefix> <text...>
  kb subsection summary <prefix> <text...>

Nesting has no depth limit: the render shows depth as a breadcrumb rather than
by heading level, which markdown caps at six.

`rename` stays within the same section -- a subsection and a section are both
just an id prefix that member ids extend, so it works exactly like `kb
section rename`: every member is re-id'd, any citing prose is named and
tracked in `kb legacy`, and a retired prefix is reclaimed if reused.""",
            "examples:\n"
            "  kb subsection\n"
            "  kb subsection new pages.mold-detail --order 10 --label \"Mold detail\"\n"
            "  kb subsection summary pages.mold-detail What the detail page owns.\n"
            "  kb subsection rename pages.mold-detail pages.mold-info\n"
            "  kb subsection order pages.mold-detail 20")
    s.add_argument("names", nargs="*", metavar="PREFIX|VERB",
                   help="subsections to print, or a write verb and its arguments")
    s.add_argument("--full", "-f", action="store_true",
                   help="print keys in full instead of truncating them")
    s.add_argument("--order", type=int, metavar="N",
                   help="render position (subsection new|order)")
    s.add_argument("--label", metavar="TEXT", help="display name")
    s.add_argument("--summary", metavar="TEXT",
                   help="one-paragraph orientation for the group")
    s.add_argument("--yes", action="store_true",
                   help="confirm a destructive write (subsection rm)")

    add("index", read.cmd_index, "sections + their highlighted items",
        """\
The whole map, one line per section plus its highlighted items -- the cheapest
way to see what the KB covers before drilling in with `kb ctx` or
`kb section`.""")

    s = add("find", read.cmd_find, "regex search over key / code / id / keywords",
            """\
Python regex, case-insensitive unless --case. Matches against one field or,
by default, all of them joined: key, id, keywords, code refs, summary.

This is a literal search. For "what is relevant to this topic" use
`kb ctx --query` (relevance-ranked) or `kb dup` (near-duplicate detection)
instead -- both score rather than match.""",
            "examples:\n"
            "  kb find authFetch                       anywhere\n"
            "  kb find 'localStorage' --field key\n"
            "  kb find . --type guardrail --status todo  every unfinished guardrail\n"
            "  kb find 'api\\\\.ts$' --field code --full")
    s.add_argument("pattern", help="Python regular expression")
    s.add_argument("--field", choices=["key", "code", "id", "keywords",
                                       "summary", "all"], default="all",
                   help="restrict the search to one field (default: all)")
    s.add_argument("--type", metavar="TYPE", help="only items of this type")
    s.add_argument("--status", metavar="STATUS",
                   help="only items of this status")
    s.add_argument("--case", action="store_true", help="case-sensitive match")
    s.add_argument("--full", action="store_true",
                   help="print keys in full instead of truncating them")

    s = add("guardrails", read.cmd_guardrails, "items of a modal-carrying type",
            """\
Every item whose *type* is declared `requires_modal` in the glossary -- the
rules whose keys must say MUST / NEVER / ONLY / EXACTLY. Which types those are
is a glossary question, not a hardcoded list, so this follows
`kb glossary add type <t> --modal`.""")
    s.add_argument("section", nargs="?", metavar="SECTION",
                   help="only within this section (default: the whole KB)")
    s.add_argument("--full", action="store_true",
                   help="print keys in full instead of truncating them")

    s = add("tree", read.cmd_tree, "dependency tree",
            """\
Indented recursive walk from each id. Repeat visits on one branch are marked
`(cycle)` and stop there, so a cyclic graph still terminates.

Use `kb impact` instead when you want the flat set of everything reachable --
a tree repeats shared descendants once per path to them.""",
            "examples:\n"
            "  kb tree auth.single-fetch-path --depth 3\n"
            "  kb tree auth.token -d up -k          what depends on it, with keys\n"
            "  kb tree api.fetcher --kind constrains --kind calls")
    s.add_argument("ids", nargs="+", metavar="ID", help="roots to walk from")
    s.add_argument("--depth", type=int, default=2,
                   help="levels to descend (default: 2)")
    s.add_argument("--direction", "-d", choices=["down", "up", "both"],
                   default="down",
                   help="down = affects, up = affected by, both = one tree each "
                        "(default: down)")
    s.add_argument("--kind", action="append", metavar="KIND",
                   help="only follow these edge kinds (repeatable)")
    s.add_argument("--keys", "-k", action="store_true",
                   help="show each item's key alongside its id")

    s = add("impact", read.cmd_impact, "flat closure: everything to re-verify",
            """\
The transitive closure from the roots, deduped, grouped by section, each line
marked with the depth it was found at. This is the change-blast-radius
question: after editing what the roots govern, every item listed here has a
key that may no longer be true.

Depth defaults to 99, i.e. the full closure. The roots themselves are excluded
unless --include-roots.""",
            "examples:\n"
            "  kb impact auth.single-fetch-path\n"
            "  kb impact auth.token --files        also list the code surface\n"
            "  kb impact api.fetcher --ids-only | xargs kb verify\n"
            "  kb impact auth.token --depth 1 -d both")
    s.add_argument("ids", nargs="+", metavar="ID", help="roots to close over")
    s.add_argument("--depth", type=int, default=99,
                   help="cap the closure depth (default: 99, i.e. all of it)")
    s.add_argument("--direction", "-d", choices=["down", "up", "both"],
                   default="down",
                   help="down = affects, up = affected by (default: down)")
    s.add_argument("--kind", action="append", metavar="KIND",
                   help="only follow these edge kinds (repeatable)")
    s.add_argument("--files", action="store_true",
                   help="also list every code path the closure touches")
    s.add_argument("--ids-only", action="store_true",
                   help="bare ids, one per line, nothing else -- for piping")
    s.add_argument("--include-roots", action="store_true",
                   help="count the roots as part of the closure")

    s = add("path", read.cmd_path, "shortest edge path between two items",
            """\
Breadth-first shortest path, following edges in either direction (an edge is
stored one-way but a connection is a connection). Prints the chain with each
hop's kind and the direction it was traversed.

Exits 1 when the two items are in disconnected parts of the graph.""",
            "examples:\n"
            "  kb path auth.token pages.mold-detail\n"
            "  kb path auth.token api.fetcher --keys")
    s.add_argument("src", metavar="FROM", help="start item")
    s.add_argument("dst", metavar="TO", help="end item")
    s.add_argument("--kind", action="append", metavar="KIND",
                   help="only traverse these edge kinds (repeatable)")
    s.add_argument("--keys", "-k", action="store_true",
                   help="show each item's key under its id")

    s = add("code", write.dispatch(read.cmd_code, write.cmd_code_write, "ids",
                                   verbs=("add", "rm", "clear", "fmt", "mv")),
            "the code an item governs, or add|rm|clear a ref, mv a path, or "
            "fmt the markers",
            """\
With an id: print the item's code refs, and the source of every `marker` ref
resolved to live line numbers. Refs that are not markers yield no snippet, and
the command prints the exact `kb:begin`/`kb:end` pair to place by hand instead
-- in that file's comment form. `kb` never writes source (invariant 7), so
there is no command that places a marker for you.

With a verb first, write instead:
  kb code add <id> marker|symbol|file <path> [symbol]
  kb code rm <id> <n>          n is 0-based, as printed by `kb item`
  kb code clear <id> --yes     only for types that need no code ref
  kb code fmt [path...]        tighten and collapse markers; --write applies
  kb code mv <old> <new>       repoint every ref under a moved file or directory

`code mv` runs after `git mv`, never instead of it: it writes only the store and
refuses until <new> exists in the tree. One find-and-replace for a file or a
directory alike: a ref path equal to <old> becomes <new>, and one starting with
<old>/ has that prefix replaced (src/library is not under src/lib). Refs of
every kind move, and the suppressions keyed by the path follow them. Prose
citing <old> is named, not rewritten.

`code fmt` is the one command that writes source, and the narrowest one it could
be: a `kb:begin` swaps places with a blank line just inside its region, a
`kb:end` does the same upwards, and marker lines that end up adjacent merge into
one `kb:begin a.one a.two`. Nothing that is not a marker or a blank line is
touched, which it re-verifies against its own output before writing anything --
a file it cannot prove that for is left alone with the reason printed. It prints
a diff and writes nothing unless `--write` is given.

Only `symbol` refs may carry a symbol name, and one call adds exactly one ref:
trailing tokens are refused rather than kept as a label, which is what used to
turn `symbol <path> <sym> marker <path>` into a single ref carrying the second
as text. Code paths never carry line numbers -- markers resolve them on every
read.""",
            """\
examples:
  kb code auth.single-fetch-path
  kb code auth.token --max-lines 80
  kb code add auth.token marker src/lib/auth.ts
  kb code add auth.token symbol src/lib/auth.ts readToken
  kb code add auth.token marker src/lib/session.ts --replace
  kb code rm auth.token 2
  kb code fmt                  whole tree, diff only
  kb code fmt src/kbgraph      one directory
  kb code mv src/lib/auth.ts src/lib/session.ts
  kb code mv src/lib src/core  a whole directory

--replace and --yes must trail the positionals: argparse cannot resume a
variadic positional list after a flag, so `kb code add ID --replace marker
path` does not parse.

A type declared `requires_code` cannot end up with zero refs, so `code clear`
is refused for one; `code add ... --replace` is the atomic way to swap the
code surface of such an item.""")
    s.add_argument("ids", nargs="+", metavar="ID|add|rm|clear|fmt|mv",
                   help="items to print, or a write verb and its arguments")
    s.add_argument("--max-lines", type=int, default=40, metavar="N",
                   help="cap each snippet at N lines (default: 40)")
    # argparse cannot resume a nargs='+' positional after a flag, so the
    # flags on this command have to trail: `kb code add ID file p.ts --replace`.
    s.add_argument("--replace", action="store_true",
                   help="code add: drop the existing refs first (put it last)")
    s.add_argument("--yes", action="store_true",
                   help="confirm a destructive write (code clear)")
    s.add_argument("--write", action="store_true",
                   help="code fmt: apply the diff (default: print it only)")

    s = add("hubs", read.cmd_hubs, "highest-degree items (riskiest to change)",
            """\
Items ranked by total degree, inbound plus outbound. A high-degree item is one
whose key many other keys lean on, so it is both the most expensive to get
wrong and the most valuable to read first in an unfamiliar area.""")
    s.add_argument("-n", type=int, default=15, metavar="N",
                   help="how many to show (default: 15)")

    add("orphans", read.cmd_orphans, "items with no edges",
        """\
Items with no edges in either direction. Usually a gap -- something was added
without being connected to what it constrains -- but not always: a type
declared `requires_edges: false` in the glossary is edgeless by design, and
`kb lint` knows the difference where this command does not.""")

    add("stats", read.cmd_stats, "counts, edge-kind coverage, suppressions",
        """\
Store path, schema, ambient run, item and edge counts, edge-kind label
coverage, the type / status / section distributions, code-ref kinds, and the
number of live lint suppressions.

Edge-kind coverage and the suppression count are the two numbers meant to be
watched over time: the first should trend up, the second should not quietly
grow.""")

    s = add("kw", write.dispatch(read.cmd_kw, write.cmd_kw_write, "terms",
                                 verbs=("add", "rm")),
            "list keywords, find items by keyword, or add|rm on an item",
            """\
No arguments: every keyword with the number of items carrying it, most-used
first. With terms: the items tagged with any of them, or all of them under
--all.

With a verb first, write instead:
  kb kw add <id> <term>...     union with what the item already has
  kb kw rm  <id> <term>...     refuses if the item lacks one of them

Keywords are lowercased on the way in and may be up to three words. Only
commas and middots split one argument, so a multi-word term just needs
quoting; separate arguments are separate terms either way.""",
            "examples:\n"
            "  kb kw                              the whole vocabulary\n"
            "  kb kw authfetch http               either keyword\n"
            "  kb kw authfetch http --all         both\n"
            "  kb kw add auth.token authfetch http        two terms\n"
            "  kb kw add auth.token 'access token'        one term, two words\n"
            "  kb kw add auth.token 'access token,jwt'    two terms")
    s.add_argument("terms", nargs="*", metavar="TERM|add ID TERM...",
                   help="keywords to search for, or a write verb and its "
                        "arguments")
    s.add_argument("--all", action="store_true",
                   help="require every term, not any of them")

    s = add("kw-suggest", read.cmd_kw_suggest, "propose keywords (IDF)",
            """\
Print a ready-to-run `kb kw add` line per item, with the terms that best
discriminate it from the rest of the KB (IDF-weighted over its key, id, code
basenames and symbols).

This writes nothing. Review the lines, then run the ones you agree with --
which is the point: a keyword that does not discriminate is worse than no
keyword, and only a human can tell which of these do.""",
            "examples:\n"
            "  kb kw-suggest                        every untagged item\n"
            "  kb kw-suggest auth.token -n 4\n"
            "  kb kw-suggest --overwrite | sh       accept them all, tagged or not")
    s.add_argument("ids", nargs="*", metavar="ID",
                   help="items to suggest for (default: every untagged item)")
    s.add_argument("-n", type=int, default=6, metavar="N",
                   help="terms per item (default: 6)")
    s.add_argument("--overwrite", action="store_true",
                   help="include items that already have keywords")

    s = add("dup", read.cmd_dup, "BEFORE ADDING: does this rule already exist?",
            """\
Score the draft text against every existing key and print the closest matches.
Run it before `kb new`: two items stating the same rule in different words is
the failure mode a knowledge base dies of, and it is invisible to `kb find`
because the words differ.

--code biases the ranking toward items anchored in the same directory.""",
            "examples:\n"
            "  kb dup \"every authed request goes through authFetch\"\n"
            "  kb dup \"the token is read from localStorage\" --code src/lib/auth.ts")
    s.add_argument("text", help="the rule you are about to write, in prose")
    s.add_argument("--code", metavar="PATH",
                   help="the file it would be anchored at, to bias the ranking")
    s.add_argument("-n", type=int, default=8, metavar="N",
                   help="how many candidates to show (default: 8)")

    s = add("relate", read.cmd_relate, "candidate edges for a draft item",
            """\
The same scoring as `kb dup`, read the other way: once you have decided the
item is new, these are the items it most likely has edges to. Nothing is
written -- pick from the list and run `kb edge add`.""",
            "examples:\n"
            "  kb relate \"SWR fetchers wrap authFetch\" --code src/lib/swr.ts\n"
            "  kb relate \"...\" --exclude auth.token,api.fetcher")
    s.add_argument("text", help="the draft item's key, in prose")
    s.add_argument("--code", metavar="PATH",
                   help="the file it is anchored at, to bias the ranking")
    s.add_argument("--exclude", metavar="ID,ID",
                   help="comma-separated ids to leave out (edges you have)")
    s.add_argument("-n", type=int, default=10, metavar="N",
                   help="how many candidates to show (default: 10)")

    # ---- authoring
    s = add("new", write.cmd_new, "create an item",
            """\
Create an item. The id is `<section>.<slug>`: the section must already exist
and the prefix must match it, because the prefix *is* the membership.

--type and a key are required. `created`, `modified` and `verified` are
stamped today, and the ambient run (`kb run`) is stamped alongside them.

Run `kb dup` first.

A key over its type's cap is refused; `--suppress-length "<reason>"` creates it
anyway and records the suppression. One ref per `--code`, repeated as often as
needed.""",
            """\
examples:
  kb new auth.token --type concept --key "The token lives in localStorage." \\
      --code file src/lib/auth.ts

  kb new auth.single-fetch-path -t guardrail --highlight \\
      --key "Every authenticated request MUST go through authFetch." \\
      --summary "All authed HTTP goes through authFetch." \\
      --code marker src/lib/api.ts \\
      --code symbol src/lib/api.ts authFetch \\
      --affects polling.swr-fetcher,downloads.authed-download \\
      --kind constrains --kw authfetch,http

  kb new x.y -t behavior --key-file - --code file src/x.ts   # key from stdin

--affects and --affected-by take comma- or space-separated ids and are
repeatable; --kind labels every edge the command creates, in both directions.
Ids resolve by suffix, so `--affects swr-fetcher` is enough.

`--code` takes exactly one ref and refuses trailing tokens: two refs are two
flags. It is variadic, so it must not sit immediately before the id --
`kb new --code file src/x.ts my.item` reads the id as part of the ref.""")
    s.add_argument("id", metavar="section.slug", help="the new item's id")
    s.add_argument("--type", "-t", required=True, metavar="TYPE",
                   help="a type from the glossary (`kb glossary` lists them)")
    s.add_argument("--status", default="active", metavar="STATUS",
                   help="a status from the glossary (default: active)")
    s.add_argument("--key", metavar="TEXT", help="the rule itself")
    s.add_argument("--key-file", metavar="PATH",
                   help="read the key from a file ('-' for stdin)")
    s.add_argument("--suppress-length", metavar="REASON",
                   help="create it anyway despite an over-cap key, recording "
                        "this as the key-length suppression's reason")
    s.add_argument("--summary", metavar="TEXT",
                   help="compressed key, for the section header")
    s.add_argument("--highlight", action="store_true",
                   help="surface it in its section header and the index")
    s.add_argument("--code", action="append", nargs="+",
                   metavar="KIND PATH [SYMBOL]",
                   help="one ref per flag, repeated for more: --code marker "
                        "src/lib/api.ts --code file src/lib/auth.ts")
    s.add_argument("--affects", action="append", metavar="ID,ID",
                   help="outbound edges (repeatable)")
    s.add_argument("--affected-by", action="append", metavar="ID,ID",
                   help="inbound edges, written on the other item (repeatable)")
    s.add_argument("--kind", default="relates", metavar="KIND",
                   help="edge kind for those edges (default: relates)")
    s.add_argument("--keywords", "--kw", action="append", dest="keywords",
                   metavar="TERM,TERM", help="keywords (repeatable)")

    s = add("rm", write.cmd_rm, "delete an item and every edge into it",
            """\
Delete the item and drop every edge that pointed at it, naming each one as it
goes. Destructive, so it requires --yes; --dry-run previews the store diff
without it.

Consider `kb set <id> status deprecated` instead when the rule is gone but the
id is still referenced by plans, commits or other items.""")
    s.add_argument("id", metavar="ID", help="item to delete")
    s.add_argument("--yes", action="store_true", help="confirm the deletion")

    s = add("rename", write.cmd_rename, "rename an item within its section",
            """\
Re-id the item and repoint every inbound edge. The new id must keep the same
section prefix -- changing that is `kb move`, which is a different operation
with the same mechanics.

Edges are repointed; prose is not. Any other item whose key text names the old
id is listed in full, and `kb lint --rule stale-id-mention` finds them again
on the next run regardless. If any do, the old id is also recorded in
`kb legacy` -- mostly for a uniform "every retired name" view, since an item's
own section stays valid either way and lint needs no help re-finding it; the
entry clears itself once nothing cites the old id any more.""",
            "examples:\n  kb rename auth.token auth.access-token")
    s.add_argument("src", metavar="OLD_ID", help="current id")
    s.add_argument("dst", metavar="NEW_ID",
                   help="new id, same section prefix")

    s = add("mv", write.cmd_mv_gone, "removed -- renamed to `kb rename`",
            """\
Removed. `kb mv` is now `kb rename`, and `kb section mv` is `kb section
rename`; `kb move <id> <section>` is unchanged. The name is kept only to say
so, and exits 2.""")
    s.add_argument("args", nargs="*", metavar="...", help=argparse.SUPPRESS)

    s = add("move", write.cmd_move, "move an item to another section (re-ids it)",
            """\
Move the item to another section. This always re-ids it, because the id prefix
*is* the section membership -- there is no way to keep the old id, and no flag
that pretends there is. Every inbound edge is repointed, and citing prose is
handled exactly like `kb rename` -- named in full, and recorded in
`kb legacy` if any remains.""",
            "examples:\n  kb move auth.token contracts")
    s.add_argument("id", metavar="ID", help="item to move")
    s.add_argument("section", metavar="SECTION", help="destination section")

    s = add("set", write.cmd_set, "set type / status / key / summary",
            """\
Set one scalar field. Only these four are settable here; edges, keywords, code
refs, highlight and suppressions each have their own command, so there is
exactly one way to write anything.

`kb set <id> summary ""` clears the summary. Setting `key` on an item that has
a summary prints the summary back as a reminder that it may no longer
compress the new key -- the store cannot detect that later, because `meta` is
per item rather than per field. Only when the key actually moved: an edit under
20% of it is a reword, and the nudge stays quiet.

A key over its type's cap (`kb glossary`) is refused, with the repair that fits
its size -- trim it, or split it into independent items. `--suppress-length
"<reason>"` writes it anyway and records a `key-length` suppression, granted
against that key: it suspends if the key changes again.""",
            """\
examples:
  kb set auth.token status deprecated
  kb set auth.token key "The token lives in localStorage['access_token']."
  kb set auth.token key --file draft.txt
  kb set auth.token key --file -            from stdin
  kb set auth.token summary ""              clear it
  kb set auth.token key --file draft.txt --suppress-length <reason>""")
    s.add_argument("id", metavar="ID", help="item to edit")
    s.add_argument("field", choices=write.SETTABLE, help="which field to set")
    s.add_argument("value", nargs="?", metavar="VALUE",
                   help="the new value ('' clears a summary)")
    s.add_argument("--file", metavar="PATH",
                   help="read the value from a file ('-' for stdin)")
    s.add_argument("--suppress-length", metavar="REASON",
                   help="set it anyway despite an over-cap key, recording this "
                        "as the key-length suppression's reason")

    s = add("highlight", write.cmd_highlight,
            "surface an item in its section header",
            """\
Mark items as the section's key knowledge: their summary (or key, if there is
no summary) is what the section header and `kb index` show. --off unsets it.""")
    s.add_argument("ids", nargs="+", metavar="ID", help="items to flag")
    s.add_argument("--off", action="store_true", help="unhighlight instead")

    s = add("edge", write.cmd_edge, "add|rm|kind an edge (outbound, one-way)",
            """\
Edges are stored on the source only; the inverse is derived, so there is one
write and it cannot go half-done. Direction is meaningful: `A affects B` says
a change to A may falsify B.

  kb edge add  <src> <dst> [--kind K]   default kind: relates
  kb edge rm   <src> <dst>
  kb edge kind <src> <dst> --kind K     relabel an edge that exists

`add` refuses when the edge is already there and tells you to relabel; `rm`
checks the reverse direction and says so if that is what you meant.""",
            "examples:\n"
            "  kb edge add auth.single-fetch-path polling.swr-fetcher "
            "--kind constrains\n"
            "  kb edge kind auth.token api.fetcher --kind consumes\n"
            "  kb edge rm auth.token api.fetcher")
    s.add_argument("verb", choices=["add", "rm", "kind"], help="what to do")
    s.add_argument("src", metavar="FROM", help="the affecting item")
    s.add_argument("dst", metavar="TO", help="the affected item")
    s.add_argument("--kind", metavar="KIND",
                   help="edge kind from the glossary (required for `kind`)")

    s = add("verify", write.cmd_verify, "mark items re-checked against the code",
            """\
Stamp `meta.verified` with today (or --date) and nothing else. Deliberately
distinct from `modified`: re-reading an item, looking at the code and finding
the key still true is the act that resets the staleness clock, and editing an
item is not that.

This is the command that makes `verified-stale` mean something, and the one to
run at the end of a change over everything `kb impact` listed.""",
            "examples:\n"
            "  kb verify auth.token auth.single-fetch-path\n"
            "  kb impact auth.token --ids-only | xargs kb verify\n"
            "  kb verify auth.token --date 2026-07-01")
    s.add_argument("ids", nargs="+", metavar="ID",
                   help="items you have just re-checked")
    s.add_argument("--date", metavar="YYYY-MM-DD",
                   help="the date to stamp (default: today)")

    s = add("title", write.cmd_title, "get/set the render's title heading",
            """\
With no argument, print the store's title. With one, set it -- it is the
`# <title>` heading at the top of knowledgebase.md.""",
            "examples:\n  kb title\n  kb title Resume Foundry Frontend")
    s.add_argument("text", nargs="*", metavar="TEXT",
                   help="the new title (omit to read it)")

    s = add("run", write.cmd_run, "get/set the ambient run id stamped on writes",
            """\
With no argument, print the current run id. With one, set it: from then on
every item any mutation touches gets `meta.run` stamped with it, which is what
lets you ask later which change introduced or last moved a rule.

Set it once at the start of a run.""",
            "examples:\n  kb run          what is it now\n  kb run 034")
    s.add_argument("id", nargs="?", metavar="NNN",
                   help="the run id to stamp (omit to read it)")

    s = add("suppress", write.cmd_suppress, "silence one lint finding",
            """\
Record that a lint rule does not apply, with a reason. The reason is
mandatory: a suppression without one is indistinguishable from giving up, and
`kb stats`, `kb suppress --list` and every lint summary report the live count
so they cannot quietly accumulate.

--who is the finding's own `who` field, copied verbatim. An item id resolves
through the graph and the entry is stored on that item; a `[section]`, a
`[prefix]`, a `keyword:<kw>` or a path has no owner and is stored in the
store's root `lint` key.

--ref N names one of the item's code refs, 0-based as `kb item` prints them,
for the rules that fire once per ref (code-path, marker-missing, marker-span,
marker-empty, code-inexact). Without it the entry covers every ref on that item.
The index follows the ref: `kb code rm` renumbers these entries and drops the
one it orphans, naming its reason as it goes.

Rule names come from `kb lint --list`; an unknown one is refused rather than
recorded. `kb lint --json` prints the exact command for every finding it
reports, which is the intended way to call this.""",
            "examples:\n"
            "  kb suppress --who auth.unauth-exceptions --rule key-length \\\n"
            "      --reason \"one contract with 4 enumerated exceptions; "
            "splitting loses the framing\"\n"
            "  kb suppress --who auth.token --rule code-path --ref 1 \\\n"
            "      --reason \"generated file, absent in CI\"\n"
            "  kb suppress --who '[debt]' --rule section-min-items \\\n"
            "      --reason \"the ratchet is three items and will not grow\"\n"
            "  kb suppress --who auth.unauth-exceptions --rule key-length "
            "--remove\n"
            "  kb suppress --list")
    s.add_argument("--who", metavar="WHO",
                   help="an item id, or the finding's `who` field")
    s.add_argument("--rule", metavar="RULE",
                   help="a rule name from `kb lint --list`")
    s.add_argument("--reason", metavar="TEXT",
                   help="why the rule does not apply (mandatory)")
    s.add_argument("--ref", type=int, metavar="N",
                   help="silence only findings from this code ref (0-based)")
    s.add_argument("--remove", "--off", action="store_true", dest="remove",
                   help="lift the suppression instead")
    s.add_argument("--list", action="store_true",
                   help="print every live suppression and exit")

    s = add("thresholds", write.cmd_thresholds,
            "show or set the per-repo lint thresholds",
            """\
With no argument, print every threshold, the value this store uses, the default
and the rule that reads it. With `set` or `reset`, change one.

A threshold is per repo because the number that makes a rule useful is not the
same in a store of 40 items and one of 400. The defaults are what this build
ships; a value set here is stored in the KB and travels with it, and the
matching `kb lint` flag still overrides both for one run.""",
            """\
examples:
  kb thresholds
  kb thresholds set edge_kind_share 0.9
  kb thresholds set section_min_items 4
  kb thresholds reset edge_kind_share""")
    s.add_argument("args", nargs="*", metavar="[set|reset NAME [VALUE]]",
                   help="omit to print them all")

    s = add("legacy", write.cmd_legacy,
            "renamed-away names (item, section or subsection) still cited in prose",
            """\
`kb rename`, `kb move`, `kb section rename` and `kb subsection rename` all
re-id every affected id and repoint every edge -- but prose elsewhere can
still cite the old name (`see model.prefix-resolution`). Repointing an edge
is a mechanical fix; rewriting someone else's key is an edit, so no rename
can make that fix itself.

When one leaves any prose still citing the old name, it is recorded here
automatically (never for a name nothing cites) with the kind it was --
item, section or subsection. For a retired *section*, this is also load-
bearing: once the section id is gone, `stale-id-mention` has no section left
to recognise that prefix against and would stop finding the mentions
entirely, not just at rename time. An item or subsection has no such gap --
its own section stays valid -- so its entry here exists purely so `kb legacy`
is the one place to see every retired name, of any kind, in one list.

An entry leaves this list one of two ways: fix (or suppress away) every
citing mention and the next write clears it on its own -- nothing to
remember to run -- or decide it doesn't matter and retire it by hand with
`rm`, which refuses while mentions remain unless you pass --yes. Reusing a
retired name (as a new item, section, subsection, or a rename's destination)
also retires its entry, automatically, the moment it happens.

With no argument, print every legacy name, its kind, what it became, when,
and how many mentions still cite it.""",
            "examples:\n  kb legacy\n  kb legacy rm auth.token\n"
            "  kb legacy rm model --yes")
    s.add_argument("args", nargs="*", metavar="[rm NAME]",
                   help="omit to list every legacy name")
    s.add_argument("--yes", action="store_true",
                   help="retire it even if mentions remain")

    s = add("glossary", write.cmd_glossary,
            "show, or add|rm type|status|edge-kind",
            """\
With no arguments, print the vocabulary: types with their caps and
requirements, statuses, and edge kinds with both ends of the arrow named.

  kb glossary add type <name> --desc "..." [--key-max N] [--modal]
      [--no-code] [--no-edges]
  kb glossary add status <name> [--desc "..."]
  kb glossary add edge-kind <name> --inverse "..." [--label "..."]
  kb glossary rm type|status|edge-kind <name> --yes

`add` on an existing name updates it in place, leaving unmentioned settings
alone. `rm` refuses while any item still uses the name.

The type flags are what make types more than decoration:
  --key-max N   lint warns past N characters
  --modal       the key must contain MUST / NEVER / ONLY / EXACTLY
  --no-code     items of this type need no code ref
  --no-edges    edgeless is normal, do not warn as an orphan
Each has an inverse (--no-modal, --needs-code, --needs-edges) that clears it.

--inverse is required on an edge kind: the render names both ends of the
arrow.""",
            "examples:\n"
            "  kb glossary\n"
            "  kb glossary add type directive --desc \"user-owned priority\" \\\n"
            "      --key-max 1200 --no-code --no-edges\n"
            "  kb glossary add edge-kind constrains --inverse \"constrained by\" "
            "--label Constrains\n"
            "  kb glossary rm status draft --yes")
    s.add_argument("args", nargs="*", metavar="[add|rm type|status|edge-kind NAME]",
                   help="omit to print the glossary")
    s.add_argument("--desc", metavar="TEXT", help="one-line description")
    s.add_argument("--key-max", type=int, metavar="N",
                   help="type: warn past N characters")
    s.add_argument("--modal", action="store_true",
                   help="type: require a modal")
    s.add_argument("--no-modal", action="store_true",
                   help="type: stop requiring a modal")
    s.add_argument("--no-code", action="store_true",
                   help="type: items of it need no code ref")
    s.add_argument("--needs-code", action="store_true",
                   help="type: require a code ref again")
    s.add_argument("--no-edges", action="store_true",
                   help="type: edgeless is normal, do not warn as an orphan")
    s.add_argument("--needs-edges", action="store_true",
                   help="type: warn on orphanhood again")
    s.add_argument("--label", metavar="TEXT",
                   help="edge-kind: outbound label")
    s.add_argument("--inverse", metavar="TEXT",
                   help="edge-kind: inbound label (required on add)")
    s.add_argument("--yes", action="store_true",
                   help="confirm a destructive write (glossary rm)")

    # ---- store
    s = add("init", cmd_init, "create a store in this directory",
            """\
Write a new knowledgebase.json (and its render) in the current directory, or
at --kb. Refuses to touch an existing store: there is no --force, because the
failure it would cover for is "I destroyed a knowledge base by mistyping a
path".

The glossary is seeded with the vocabulary the schema was designed around --
eight types with their length caps and requirements, three statuses, five edge
kinds -- because a store with an empty glossary accepts no items at all.
`kb glossary` shows what you got; `kb glossary add|rm` changes it. `--bare`
skips the seed if you would rather define your own from nothing.""",
            "examples:\n"
            "  kb init\n"
            "  kb init --title \"Resume Foundry Frontend\" --run 001\n"
            "  kb init --kb docs/knowledgebase.json --bare")
    s.set_defaults(needs_store=False)
    s.add_argument("--title", default="Knowledge Base", metavar="TEXT",
                   help="the render's heading (default: Knowledge Base)")
    s.add_argument("--run", metavar="NNN",
                   help="ambient run id to start stamping with")
    s.add_argument("--bare", action="store_true",
                   help="empty glossary instead of the seeded vocabulary")

    s = add("render", cmd_render, "render knowledgebase.md from the store",
            """\
Write knowledgebase.md next to the store. Every mutation already re-renders,
so this is for the cases where that did not happen: a hand-edited md, or a
store changed by something other than `kb`.

--check is the gate: it compares the md on disk byte-for-byte against
render(store) and exits 1 on any difference, naming the first line that
differs. That is what pre-commit runs, and it catches a stale render and a
hand-edit with one test. Hand-edits are never absorbed -- translate them into
`kb` commands.

The render preserves the existing file's BOM, line endings and final newline,
so a rewrite never shows up as a whole-file diff.""",
            "examples:\n"
            "  kb render\n"
            "  kb render --check\n"
            "  kb render --stdout | less")
    s.add_argument("--check", action="store_true",
                   help="fail if the md on disk differs from the render")
    s.add_argument("--stdout", action="store_true",
                   help="print instead of writing")
    s.add_argument("--force", action="store_true",
                   help="replace a schema-1 knowledgebase.md")

    s = add("lint", _lint, "quality check (exit 1 on errors only)",
            """\
Every rule in one pass. Two severities, and only errors move the exit code:

  ERROR  the graph asserts something checkably untrue -- an edge to an id that
         does not exist, a code path that is not in the tree, a marker whose
         `end` never came. Facts, not tastes, and cheap to fix. This is the
         set a pre-commit hook may block on.
  warn   the graph is true but not yet good -- keys over their type's cap,
         missing modals, orphans, staleness. A backlog, not a breakage.

`kb lint --list` prints the rule table with severities; those names are what
`kb suppress` and `--rule` take. `--strict` promotes warnings to failures for
a deliberate cleanup pass.

The file-touching rules walk the tree from the store's directory (or --root),
skipping vendored and generated directories. --no-files drops all of them,
which is what makes lint usable against a store kept away from its repo.

Scope it with ids, `--section` or `--surfaced-file` when a team owns part of
the graph: a finding is in scope when its subject is. Findings about the store
as a whole -- an over-broad keyword, a marker in a file no item claims -- belong
to no section, so a scoped run counts them out loud rather than dropping them.

`--all` turns off every threshold and prints suppressed findings tagged. It is
the answer to "show me everything that is wrong with my part", which no
combination of the other flags gives you.""",
            """\
examples:
  kb lint                          the whole picture
  kb lint --all                    every finding, thresholds and exemptions
  kb lint --section lint           one section, and only it
  kb lint lint.tree-scan store.upward-discovery
  kb lint --errors-only            what pre-commit blocks on
  kb lint --json                   each finding with the command that silences it
  kb lint --list                   the rule table
  kb lint --rule key-length --rule orphan
  kb lint --strict                 hold a cleanup pass to zero warnings
  kb lint --fix                    repoint marker refs whose region moved
  kb lint --stale-months 0         switch off the staleness rule for this run
  kb lint --edge-kind-share 1      every unlabelled edge, in every section
  kb lint --baseline               fail only on regression, not on backlog
  kb lint --baseline <file>        against a baseline kept somewhere else

--fix does the one mechanical repair there is: a marker ref whose region now
lives in exactly one other file gets repointed. It writes the store only --
`kb` never edits source -- and refuses to guess when the region turns up in
more than one place.""")
    s.add_argument("ids", nargs="*", metavar="ID",
                   help="report only on these items (default: the whole store)")
    s.add_argument("--section", action="append", metavar="SECTION",
                   help="report only on this section (repeatable)")
    s.add_argument("--surfaced-file", metavar="FILE",
                   help="read ids from FILE, one per line (# comments ok)")
    s.add_argument("--all", action="store_true",
                   help="ignore every threshold and show suppressed findings "
                        "tagged -- the whole backlog, nothing held back")
    s.add_argument("--show-suppressed", action="store_true",
                   help="print suppressed findings, tagged, without touching "
                        "the thresholds")
    s.add_argument("--errors-only", action="store_true",
                   help="print errors, skip the warning backlog")
    s.add_argument("--json", action="store_true",
                   help="machine-readable, with the exact kb suppress command "
                        "for every finding")
    s.add_argument("--strict", action="store_true",
                   help="warnings fail too, for a cleanup pass")
    s.add_argument("--rule", action="append", metavar="RULE",
                   help="only this rule (repeatable)")
    s.add_argument("--no-files", action="store_true",
                   help="skip every check that touches the tree")
    s.add_argument("--fix", action="store_true",
                   help="apply the mechanical repairs (drifted marker refs)")
    s.add_argument("--stale-months", type=int, default=None, metavar="N",
                   help="verified-stale horizon for this run (default: the "
                        "store's threshold, 6; 0 disables)")
    s.add_argument("--max-span", type=int, default=None, metavar="N",
                   help="marker-span threshold in lines for this run "
                        "(default: the store's threshold, 120)")
    s.add_argument("--edge-kind-share", type=float, default=None, metavar="F",
                   help="edge-kind-missing fires while a section labels less "
                        "than this share of its edges (default: the store's "
                        "threshold, 0.75)")
    s.add_argument("--list", action="store_true", help="print the rule table")
    s.add_argument("--baseline", nargs="?", const=True, default=None,
                   metavar="FILE",
                   help="compare against a recorded baseline and exit 1 on "
                        "any regression (default: .kb-baseline.json)")
    s.add_argument("--yes", action="store_true",
                   help="confirm the writes made by --fix")

    s = add("debt", debt_mod.cmd_debt, "defects grouped by item, worst first",
            """\
Every lint finding regrouped under the item it belongs to, ordered by over-cap
excess so the item costing the most is first.

This is the repair view. `kb lint` is one finding per line, which is what a
gate wants; a session repairing an item wants that item's defects together,
plus the number the repair is graded on -- `halve to <= N`, which is half the
key but never below the type's own cap.

Findings that are not about one item -- a section with too few items, a
keyword that tags most of the store, a marker in a file no item claims -- are
listed in their own trailing block rather than being attributed to an item
that does not own them. A suppressed finding is shown tagged, never hidden: a
suppression is a decision, not a disappearance.""",
            """\
examples:
  kb debt                           the whole store, worst first
  kb debt --json                    machine-readable; what the ratchet reads
  kb debt --rule key-length         one rule across every item
  kb debt --section lint            one section's items, worst first
  kb debt --surfaced-file <file>    the session's surfaced set, one id per line

An unknown id is an error, not an empty block: the surfaced set comes out of a
previous `kb` command, so an id the store does not have means a stale file.""")
    s.add_argument("ids", nargs="*", metavar="ID",
                   help="items to report on (default: the whole store)")
    s.add_argument("--json", action="store_true",
                   help="machine-readable report, including excess_total")
    s.add_argument("--rule", action="append", metavar="RULE",
                   help="only this rule (repeatable)")
    s.add_argument("--section", action="append", metavar="SECTION",
                   help="report on every item in this section (repeatable)")
    s.add_argument("--surfaced-file", metavar="FILE",
                   help="read ids from FILE, one per line (# comments ok)")
    s.add_argument("--no-files", action="store_true",
                   help="skip every check that touches the tree")
    s.add_argument("--stale-months", type=int, default=None, metavar="N",
                   help="verified-stale horizon for this run (default: the "
                        "store's threshold, 6; 0 disables)")
    s.add_argument("--max-span", type=int, default=None, metavar="N",
                   help="marker-span threshold in lines for this run "
                        "(default: the store's threshold, 120)")

    s = add("baseline", debt_mod.cmd_baseline, "record the ratchet's reference",
            """\
Record today's per-rule finding counts, the total over-cap excess and the state
of every adoption gate, for `kb lint --baseline` to compare against.

Per rule and not a single total, because a split raises `key-length` by six
while cutting the debt: only tracking both lets that pass. The gate state is
recorded because an adoption-gated rule turns on for the whole store in the run
that crosses the floor, and that is adoption rather than decay.

The file belongs in git. It is the ratchet's only reference, so an unstaged one
means a fresh clone compares against nothing.""",
            """\
examples:
  kb baseline write
  kb baseline write --notes "run 001: split lint.rule-table; key-length 3 -> 9"
  kb baseline write --file build/kb-baseline.json""")
    s.add_argument("verb", metavar="write", help="the only verb")
    s.add_argument("--file", metavar="FILE",
                   help="where to write it (default: .kb-baseline.json beside "
                        "the store)")
    s.add_argument("--notes", metavar="TEXT",
                   help="why the numbers moved -- recorded in the file")
    s.add_argument("--no-files", action="store_true",
                   help="skip every check that touches the tree")
    s.add_argument("--stale-months", type=int, default=None, metavar="N",
                   help="verified-stale horizon for this run (default: the "
                        "store's threshold, 6; 0 disables)")
    s.add_argument("--max-span", type=int, default=None, metavar="N",
                   help="marker-span threshold in lines for this run "
                        "(default: the store's threshold, 120)")

    add("validate", cmd_validate, "structural check of the store",
        """\
Assert that the store is a graph at all: schema version, ids, required fields,
section membership matching the id prefix, known types / statuses / edge
kinds, well-formed code refs and dates. Prints a one-line summary and exits 0,
or names every problem and exits 2.

This runs on every load, so it rarely fails in isolation -- it is here for
pre-commit and for checking a store you edited by other means. Judgment calls
(length, staleness, orphans) are `kb lint`, not this.""")

    add("fmt", cmd_fmt, "rewrite the store in canonical form",
        """\
Rewrite the store the way `kb` writes it: items and sections sorted by id,
fields in canonical order, edges sorted by target, leaf objects inline.
Prints "no change" when it already is, which is the normal answer -- every
mutation writes canonically, so this is for a store that was touched by
something else.""")

    s = sub.add_parser(
        "migrate", help="one-way: schema-1 md -> json, or --to 4 to upgrade",
        description="""\
Two one-way conversions, neither reversible except through git.

Without --to: convert a schema-1 knowledgebase.md into a knowledgebase.json at
the current schema, once. kb 4.x carries no dual-model code, so a v1 repo
either migrates or keeps using its own v1 protocol. Item dates are recovered
from git history rather than stamped with today, so the staleness lint means
something on day one; --no-git skips that and stamps instead.

v1's asymmetric edges are unioned, not dropped: outbound becomes `Affects`
plus the inverse of every `Affected by`. Section membership comes from the id
prefix, which silently relocates items that sat under the wrong heading.

With --to 4: upgrade an existing schema-2 or schema-3 store in place. Every
other command refuses an older store and names this one, because a version
check that guesses is how two stores drift apart. Every field schema 3 and
schema 4 added is optional, so the upgrade only stamps the version and
re-renders.""",
        epilog="""\
examples:
  kb migrate --show-matches          see what attaches where, write nothing
  kb migrate --run 033
  kb migrate --to 4                  upgrade an older store in place
  kb migrate --md old/kb.md --out new/knowledgebase.json --force""",
        formatter_class=Fmt, parents=[parent])
    s.set_defaults(fn=_migrate, needs_store=False)
    s.add_argument("--md", metavar="PATH",
                   help="the v1 knowledgebase.md (default: search upward)")
    s.add_argument("--out", metavar="PATH",
                   help="where to write knowledgebase.json (default: beside it)")
    s.add_argument("--no-git", action="store_true",
                   help="skip date recovery from git history")
    s.add_argument("--run", metavar="NNN",
                   help="ambient run id to stamp on the store")
    s.add_argument("--force", action="store_true",
                   help="overwrite an existing store")
    s.add_argument("--to", type=int, metavar="N",
                   help="upgrade an existing store to schema N (only 4)")
    s.add_argument("--show-matches", action="store_true",
                   help="print every section bullet and the item it was "
                        "attached to")

    # ---- docs (neither reads nor writes a store)
    s = add("manual", cmd_manual, "print README.md",
            """\
The full manual, straight from dist-info/METADATA: the dynamic `readme` key
in pyproject.toml bakes README.md and CHANGELOG.md together into every wheel
and sdist as the package's long description, so neither needs a file of its
own to ship. A checkout with no build metadata yet falls back to reading
README.md and CHANGELOG.md next to src/.""")
    s.set_defaults(needs_store=False)

    s = add("changelog", cmd_changelog, "print CHANGELOG.md",
            """\
Version history, split back out of the same dist-info/METADATA payload
`manual` reads -- see `kb manual --help`.""")
    s.set_defaults(needs_store=False)

    return p


# kb:begin cli.utf8-lf-console
def _lint(g, a):
    """`kb lint` with one flag that is a different command underneath.

    The routing is here rather than in `lint.cmd_lint` because the ratchet
    lives in `debt`, `debt` imports `lint`, and the reverse import would be a
    cycle. `cli` already imports both, so it is the layer that can choose.
    """
    if a.baseline:
        return debt_mod.cmd_lint_baseline(g, a)
    return lint_mod.cmd_lint(g, a)


def _utf8_console():
    """Force UTF-8 LF output on every platform.

    Encoding: output carries · → — everywhere. On a cp1252 console that is an
    UnicodeEncodeError, not a mojibake, so it must be forced.

    Newline: on Windows, text-mode translation turns every `\\n` into `\\r\\n`,
    which survives a pipe -- `kb impact --ids-only | xargs kb verify` then
    passes ids with a trailing carriage return and every one of them is "no
    such item". The store and the render are already written `newline="\\n"`;
    this makes the console agree with them.
    """
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", newline="\n")
        except (AttributeError, ValueError):
            pass
# kb:end cli.utf8-lf-console


GLOBAL_DEFAULTS = (("kb", None), ("root", None), ("dry_run", False))


def main(argv=None):
    _utf8_console()
    a = build_parser().parse_args(argv)
    # The global flags carry default=SUPPRESS so that a subparser's copy cannot
    # overwrite a value the top-level parser already set -- which also means
    # they are absent from the namespace when unused. Fill them in here, once,
    # so every command downstream can just read them.
    for name, default in GLOBAL_DEFAULTS:
        if not hasattr(a, name):
            setattr(a, name, default)
    # `init` and `migrate` are the two commands that run without a store,
    # because producing one is what they are for.
    if not getattr(a, "needs_store", True):
        return a.fn(a)
    try:
        g = store_mod.load(a.kb)
    except KBNotFound as e:
        die(str(e))
    except SchemaTooOld as e:
        die(str(e))
    except SchemaError as e:
        die("store is malformed:\n  " + "\n  ".join(e.problems))
    if a.root:
        g.root = a.root
    try:
        return a.fn(g, a)
    except KeyError as e:
        # `get`/`section_of` raise this for an unknown or ambiguous name --
        # a typo, not a crash.
        die(e.args[0] if e.args and isinstance(e.args[0], str) else str(e))


if __name__ == "__main__":
    # console_scripts wraps main() in sys.exit(); `python -m` does not, and
    # without this a non-zero return silently becomes exit 0.
    sys.exit(main() or 0)
