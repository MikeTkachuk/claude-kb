# kb-graph

A knowledge base for a codebase, shaped as a graph and driven entirely from the
command line. It records the rules a codebase actually runs on — contracts,
guardrails, design choices, known gaps — anchors each one to the code it
governs, and answers questions about them in one call.

It exists for a specific failure: an agent editing a file it does not know the
rules for. `kb ctx --path <file>` answers "what governs this file" completely,
in one command, instead of grepping a long document and reading slices of it.

Two files, in the root of the repo being described:

| file | role |
|---|---|
| `knowledgebase.json` | **the store** — the source of truth. Written only by `kb`. |
| `knowledgebase.md` | **the render** — generated from the store, for humans to read. |

They are committed together, and `kb render --check` fails if the second is not
exactly a render of the first. Neither is ever hand-edited: every field has a
command, and a hand-edit is a validation and canonical-form bypass that the next
`kb` write would silently revert.

**This repo describes itself.** `knowledgebase.md` here is a KB *of kb-graph*,
built with `kb`, and every example below was executed against it.

## Install

```
pip install kb-graph            # once published
pip install /path/to/kb         # from a checkout, into the repo you are describing
pip install -e .                # from a clone, working on kb-graph itself
```

`kb` is installed **once per environment, not once per repo**. It finds the
store by searching upward from the working directory, so one install serves
every repo you point it at; nothing is vendored into the repo being described
except the two KB files and `.kb-baseline.json`.

Zero dependencies, Python ≥3.9. `[project.scripts]` puts a `kb` executable in
`<python>/Scripts` (Windows) or `<python>/bin`; that directory has to be on
`PATH`, which is a property of the environment and not something the package can
do for you.

Check it:

```
$ kb --version
kb-graph 4.0.0 (schema 4)
```

### Adopting it in another repo

Three cases, and which one you are in is decided by what that repo already has:

| the repo has | do this |
|---|---|
| nothing | `kb init --title "…"`, then [Quick start](#quick-start) |
| a **schema-2** or **schema-3** `knowledgebase.json` | `kb migrate --to 4` — every other command refuses the store until you do |
| a hand-written **v1** `knowledgebase.md` | `kb migrate --show-matches` to preview, then `kb migrate` |

Then, in that repo:

```sh
cp <kb-graph>/hooks/pre-commit .git/hooks/ && chmod +x .git/hooks/pre-commit
kb lint                            # the standing backlog
kb baseline write                  # freeze it as a floor, so it can only shrink
git add knowledgebase.json knowledgebase.md .kb-baseline.json
```

All three files belong in git. The first two are checked against each other by
`kb render --check`, and the third is the ratchet's only reference — an
unstaged baseline means a fresh clone compares against nothing.

The upgrade to schema 4 is deliberately a flag day rather than a silent
re-stamp: a store written by kb 4.x is not readable by kb 3.x, so two clones on
different versions would otherwise fight over the same file. Every field schema
3 and schema 4 added is optional, so the upgrade itself only stamps the version
and re-renders — nothing in the store changes shape until you use a subsection,
run `kb verify`, override a threshold, or suppress a finding that sits outside
an item.

## Quick start

```sh
cd my-repo
kb init --title "My Repo"          # writes both files, seeds the vocabulary
kb section new auth --order 10     # sections order the render
kb new auth.single-fetch-path -t guardrail \
    --key "Every authenticated request MUST go through authFetch." \
    --summary "All authed HTTP goes through authFetch." \
    --highlight --code file src/lib/api.ts --kw authfetch,http
kb lint                            # what is still missing
```

`kb init` seeds eight types, three statuses and five edge kinds, because a store
with an empty glossary accepts no items at all. `kb glossary` shows what you got.

## The loop

Five commands carry the whole workflow. The rest are the precise layer beneath
them.

| when | command | question it answers |
|---|---|---|
| **before editing any file** | `kb ctx --path <file>` | what rules govern this file, and what else do they touch |
| entering an area cold | `kb ctx --query "…"` | which rules are about this topic |
| **before adding a rule** | `kb dup "<the rule>"` | does this already exist in other words |
| after changing something | `kb impact <id>` | what else might no longer be true |
| once re-checked | `kb verify <id>…` | record that it is still true |
| before committing | `kb lint --errors-only` | is anything checkably wrong |

`kb ctx --path` first is the point of the whole tool. Skipping it for an
"obvious" fix is how a rule gets violated by someone who did not know it
existed.

### What `kb ctx` returns

Verbatim, on this repo (one item elided for length):

```
$ kb ctx --path src/kbgraph/search.py --depth 1
== 3 items own rules for src/kbgraph/search.py

# search.code-dir-bias  [search]  behavior
  Passing --code multiplies the score of items anchored in the same directory by 1.6, on the observation that the rules governing a file are usually recorded against its neighbours.
  code src/kbgraph/search.py → score
       src/kbgraph/search.py (marker)
  → refines        search.idf-bag

# search.idf-bag  [search]  concept
  Relevance is an IDF-weighted bag of terms per item, drawn from its key at weight 1, its keywords at 3, its code basenames and symbols at 2 and its own name at 1.5. Identifiers are split on camel-case boundaries so authFetch also matches auth and fetch.
  code src/kbgraph/search.py → item_terms
       src/kbgraph/search.py (marker)
  ← consumed by    ctx.mandatory-first-step · read.kw-suggest-writes-nothing
  ← refined by     search.code-dir-bias · search.find-is-literal
  ← referenced by  migrate.bullet-map-is-explicit · search.no-index-on-disk

-- neighbourhood, depth ≤1 (4) -- re-verify each Key after the edit
  d1 ← consumes     ctx.mandatory-first-step               guardrail   kb ctx --path <file> MUST be run before editing any source file, and it is not skippable for a fix that looks…
  d1 ← relates      migrate.bullet-map-is-explicit         design-choice The v1 section-header bullets are attached to the items they restate by an explicit hand-checked map, not by …
  d1 ← consumes     read.kw-suggest-writes-nothing         behavior    kw-suggest prints a ready-to-run kb kw add line per item and writes nothing. A keyword that does not discrimi…
  d1 ← refines      search.find-is-literal                 concept     find is a literal regex match over chosen fields and answers where does this string appear. dup, relate and c…

-- sections in play
  [search] (4)  related [ctx] (1) · [migrate] (1) · [read] (1)

-- directives (override every item above)
  * directive.documented-examples-are-executed — Every documented example is run before it ships.
  * directive.kb-is-the-only-access-path — Read the graph only through `kb`, never the files.
  * directive.never-hand-edit-either-file — Both KB files are written by `kb` alone.
  * directive.trust-the-code — Code wins over the graph; update the graph.
```

Four blocks: the matched items in full, their neighbourhood labelled by edge
kind and direction, the sections in play, and every directive. `--code` inlines
the marker snippets. `--budget N` caps the size, shedding neighbour detail
first and never the roots or the directives.

## Concepts

**Item** — one rule, with an id of `<section>.<slug>`. The prefix *is* the
section membership, so it is not stored twice and cannot drift; moving an item
between sections is therefore always a rename.

**Key** — the rule itself, as a single statement. Length is capped per type,
because a type without enforcement is decoration.

**Summary** — a compressed key, shown in section headers and `kb index`. Only
for items worth surfacing (`--highlight`).

**Type** — what kind of claim the item makes. Each type carries its own cap and
requirements:

```
$ kb glossary
types
  behavior       what the main path actually does  [key ≤ 320]
  concept        definition or invariant of the area  [key ≤ 400]
  contract       shape or protocol co-owned with another system  [key ≤ 400]
  design-choice  a road taken, with the road not taken  [key ≤ 400]
  directive      user-owned top-level priority; overrides every other item  [key ≤ 1200 · no code ref needed · edgeless by design]
  edge-case      what happens at a boundary or exceptional path  [key ≤ 240]
  guardrail      rule or contract whose violation breaks behavior  [key ≤ 320 · MUST/NEVER/ONLY/EXACTLY]
  invariant      a property the whole tool is built to preserve  [key ≤ 400 · MUST/NEVER/ONLY/EXACTLY]
  limitation     known gap, deliberately unfixed  [key ≤ 240]
statuses
  active · deprecated · todo
edge kinds
  calls          → Calls            ← Called by
  constrains     → Constrains       ← Constrained by
  consumes       → Consumes         ← Consumed by
  refines        → Refines          ← Refined by
  relates        → Relates to       ← Referenced by
```

The vocabulary is data, not code: `kb glossary add type <name> --modal
--key-max N` extends it with no code change, and commands like `kb guardrails`
follow the flags rather than a hardcoded list.

**Edges** — directed, kinded, and stored on the source only. `A affects B`
means a change to A may falsify B. The inverse is derived, never stored, which
is what removes edge symmetry as something that can be got wrong.

**Code refs** — what the item governs, in order:

| kind | yields a snippet | use for |
|---|---|---|
| `marker` | **yes** | a `kb:begin`/`kb:end` pair around the exact region |
| `symbol` | no | a named function or constant; a grep target |
| `file` | no | whole-file relevance |

Refs never carry line numbers. Marker line numbers are resolved on every read,
because a stored line number is wrong the next time anyone edits the file.

**Meta** — `created`, `modified`, `verified`, `run`. `modified` moves on every
mutation; `verified` moves **only** via `kb verify`, because editing an item is
not the same as re-checking it against the code. The gap between them is what
the staleness rule measures.

**Run** — an ambient id (`kb run 034`) stamped onto every item a mutation
touches, so you can later ask which change introduced or moved a rule.

## Markers

A marker is a comment pair naming an item, placed by hand:

```python
# kb:begin store.upward-discovery
def find_store(explicit=None, start=None):
    ...
# kb:end store.upward-discovery
```

Then register it: `kb code add store.upward-discovery marker src/kbgraph/store.py`.

`kb` never writes source files, so there is no command that inserts a marker —
`kb code <id>` prints the exact pair to paste, in the right comment form for
that file, and lint verifies it from then on.

**A marker counts only when it opens its own line, in that file's own comment
form.** Both halves matter: they are what make `"// kb:begin x"` inside a Python
test fixture, and a fenced ` ``` // kb:begin x ``` ` example inside a markdown
file, inert. Without that rule, any repo documenting this scheme reports its own
examples as broken markers.

| extension | form |
|---|---|
| `.ts .tsx .js .jsx .mjs` | `// kb:begin <id>` |
| `.tsx` inside JSX children | `{/* kb:begin <id> */}` — **required**; `//` there renders as visible text on the page with no compile error |
| `.css .scss` | `/* kb:begin <id> */` |
| `.py .sh .yml .yaml .toml` | `# kb:begin <id>` |
| `.md .html` | `<!-- kb:begin <id> -->` |

One line may name several ids, which is exactly equivalent to that many stacked
single-id lines — and is what `kb code fmt` produces:

```
# kb:begin lint.rule-table-is-the-source lint.severity-split
RULES = (…)
# kb:end lint.rule-table-is-the-source lint.severity-split
```

A marker line carries ids and nothing else. Trailing text is the `marker-syntax`
error, not a comment: a half-corrupted marker that still delimits a region is
worse than one that reports itself.

### `kb code fmt`

The one command that writes source, and the narrowest one it could be. It moves
a `kb:begin` down past blank lines just inside its region and a `kb:end` up past
them — by **swapping** the lines, so the file's blank-line layout and its line
count are untouched — and then merges marker lines that ended up adjacent into
one multi-id line.

```
kb code fmt                  whole tree, diff only
kb code fmt src/kbgraph      one directory
```

It prints a diff and writes nothing until `--write`. Before writing a file it
re-reads its own output and confirms that the sequence of non-blank, non-marker
lines is unchanged and that every item still covers the same code lines; a file
it cannot prove that for — including any file mixing line endings — is left alone
with the reason printed. Marker digests cover the marker lines themselves, so
after a run `kb lint --rule code-drift` names the items to re-read and
`kb verify <id>…` records it.

The id is repeated on `end`. Pairing is a stack keyed by id, so regions from
different items may overlap and nest freely. Two regions merge only if their
line ranges genuinely intersect — regions a few lines apart are apart for a
reason.

## Command reference

Every command carries `--help` with a description and, where it earns one, a
worked example. `kb --help` lists all of them.

`--kb`, `--root` and `--dry-run` work on either side of the command name.

### Reading

| command | what it gives you |
|---|---|
| `kb ctx <id>\|--path F\|--query T` | the full working context: roots, neighbourhood, sections, directives |
| `kb item <id>…` | one item in full: key, refs, edges both ways, keywords |
| `kb meta <id>…` | dates, run, suppressions |
| `kb index` | every section and its highlighted items |
| `kb section [name]` | a section's members, highlights and related sections |
| `kb subsection [prefix]` | a subsection's label, summary and members |
| `kb find <regex>` | literal search over key / id / keywords / code / summary |
| `kb guardrails [section]` | every item of a modal-carrying type |
| `kb tree <id>…` | indented dependency walk, cycles marked |
| `kb impact <id>…` | the deduped closure: everything to re-verify |
| `kb path <a> <b>` | shortest edge path between two items |
| `kb code <id>…` | the code an item governs, marker snippets resolved live |
| `kb hubs` | highest-degree items — riskiest to change, best read first |
| `kb orphans` | items with no edges |
| `kb stats` | counts, edge-kind coverage, key debt, suppression count |
| `kb debt [id…]` | every defect grouped under its item, worst first |
| `kb kw [term…]` | the keyword vocabulary, or items carrying a term |
| `kb kw-suggest [id…]` | proposed `kb kw add` lines, IDF-ranked (writes nothing) |
| `kb dup "<text>"` | closest existing items — run before adding one |
| `kb relate "<text>"` | candidate edges for a draft item |

`find` matches; `dup`, `relate` and `ctx --query` score. Looking for a
near-duplicate with `find` is the mistake that split them: a rule restated in
different words matches no pattern.

```
$ kb dup "markers are placed by hand because the tool never edits source" -n 4
closest existing items -- fold into one of these before adding:
    51.7  markers.hand-placed-with-a-printed-pair  Because kb never writes source, an unmarked ref advertises …
    37.6  invariants.never-writes-source           kb MUST NEVER write a source file. Markers are placed by …
    32.2  markers.merge-on-intersection-only       Two regions MUST merge only when their line ranges actually …
    31.7  markers.own-line-and-own-language        A marker counts ONLY when it opens its own line and uses …
```

`tree` shows shape and repeats shared descendants; `impact` gives the set, once
each, grouped by section with the depth it was found at:

```
$ kb impact invariants.never-writes-source --depth 2
# 5 items, depth<=2, from: invariants.never-writes-source
[lint]
  d1 lint.fix-is-store-only        guardrail   lint --fix does the one mechanical repair there is …
  d2 lint.tree-scan                behavior    The marker pass walks the whole tree rather than only …
[markers]
  d1 markers.hand-placed-with-a-printed-pair  behavior    Because kb never writes source, an unmarked ref …
  d1 markers.only-kind-with-snippets          limitation  ONLY a marker ref yields a snippet. …
  d2 markers.pair-syntax                      concept     A marker is a kb:begin <id> and kb:end <id> pair …
```

`--ids-only` emits bare ids and nothing else, so the closure pipes:

```sh
kb impact invariants.never-writes-source --ids-only | xargs kb verify
```

### Writing

Every writable field has exactly one command. Mutations apply immediately — git
is the undo — and each re-validates the whole graph before writing a byte, so a
command whose result would be malformed is refused rather than half-applied.

| target | command |
|---|---|
| store | `kb init [--title T] [--run N] [--bare]` |
| title | `kb title [TEXT]` |
| item | `kb new <id> -t TYPE --key "…"` · `kb rm <id> --yes` |
| rename | `kb rename <old> <new>` · `kb move <id> <section>` (re-ids it) — citing prose is named in full and tracked in `kb legacy` |
| `type` `status` `key` `summary` | `kb set <id> <field> <value>` (or `--file -`) |
| `highlight` | `kb highlight <id>… [--off]` |
| `keywords` | `kb kw add\|rm <id> <term>…` |
| `code[]` | `kb code add\|rm\|clear <id> …` — `rm` takes a **0-based** index, as `kb item` prints it |
| `affects[]` | `kb edge add\|rm\|kind <a> <b> [--kind K]` |
| `meta.verified` | `kb verify <id>… [--date D]` |
| `meta.run` | `kb run [NNN]` |
| `meta.modified` | automatic on every mutation |
| `lint.ignore` | `kb suppress --who W --rule R --reason "…"` · `--ref N` · `--remove` · `--list` |
| `lint.thresholds` | `kb thresholds [set\|reset NAME [VALUE]]` |
| `lint.legacy` | `kb legacy [rm NAME [--yes]]` — retired names a rename left cited in prose |
| sections | `kb section new\|rm\|rename\|order\|label …` |
| subsections | `kb subsection new\|rm\|rename\|order\|label\|summary …` |
| glossary | `kb glossary add\|rm type\|status\|edge-kind …` |

`--dry-run` prints the store diff and writes nothing. Destructive commands
(`rm`, `code clear`, `section rm`, `glossary rm`) additionally require `--yes`;
`--dry-run` satisfies that guard, since it cannot destroy anything.

`code`, `kw`, `section` and `subsection` are one name for both sides — a leading
verb (`add`, `rm`, `new`, `clear`, `rename`, `order`, `label`, `summary`)
selects the write path.

### Store maintenance

| command | purpose |
|---|---|
| `kb render [--check\|--stdout\|--force]` | regenerate the md; `--check` fails on any drift |
| `kb lint [ID…] [--all\|--section S\|--json\|--errors-only\|--strict\|--fix\|--list\|--rule R\|--no-files\|--baseline F]` | quality check |
| `kb baseline write [--file F\|--notes T]` | record the ratchet's reference |
| `kb validate` | structural check: is this a graph at all |
| `kb fmt` | rewrite the store in canonical form |
| `kb migrate` | one-way conversion of a schema-1 markdown KB; `--to 4` upgrades an existing store |

### Docs

| command | purpose |
|---|---|
| `kb manual` | print README.md |
| `kb changelog` | print CHANGELOG.md |

Neither touches a store, and both work from an installed package, not just a
checkout. Both read dist-info/METADATA: pyproject's dynamic `readme` key
concatenates README.md and CHANGELOG.md into it for every wheel and sdist, so
neither needs a file of its own to ship. `kb manual` and `kb changelog` split
that one field back apart on CHANGELOG's top heading;
`tests/test_packaging.py` checks both commands' output against the two root
files so they cannot drift apart.

## Lint

Two severities, and **only errors move the exit code**.

An error means the graph asserts something checkably untrue — an edge to an id
that does not exist, a code path not in the tree, a marker whose `end` never
came. Each is a fact rather than a taste, and cheap to fix. A warning means the
graph is true but not yet good, and is a backlog rather than a breakage.

The split is forced by real data: on the KB this tool was built for, 64 of 104
keys were over their type's cap on day one. A gate that failed on those would
have been switched off within a day, and a gate that is off is worth less than a
gate that only blocks on facts.

```
$ kb lint --list
error  edge-target            affects an item that does not exist
error  self-edge              an item that affects itself
error  edge-dup               the same edge target listed twice
error  code-path              a code ref whose path is not in the tree
error  marker-missing         a marker ref with no `kb:begin` in that file
error  marker-unpaired        `kb:begin` without `kb:end`, or the reverse
error  marker-unknown         a marker in the source naming an unknown item
error  marker-jsx             a `//` marker in JSX children -- renders as text
error  marker-syntax          a marker line carrying anything but ids
error  summary-longer         a summary longer than the key it compresses
warn   key-length             key past its type's cap
warn   key-modal              a modal-requiring type whose key has no modal
warn   orphan                 no edges in either direction
warn   section-min-items      fewer items in a section than the threshold   [section_min_items = 3]
warn   subsection-min-items   fewer items in a subsection than the threshold   [subsection_min_items = 2]
warn   keyword-case           a keyword that is not lowercase
warn   keyword-phrase         a keyword of more than three words
warn   keyword-dup            the same keyword listed twice on one item
warn   keyword-missing        an item with no keywords
warn   keyword-broad          a keyword tagging most of the tagged items   [keyword_broad_share = 0.6]
warn   marker-span            a marker region long enough that `end` likely drifted   [max_span = 120]
warn   marker-empty           a marker region with no code in it
warn   verified-stale         not re-checked against the code in N months   [stale_months = 6]
warn   edge-kind-missing      edges still carrying the default 'relates' kind   [edge_kind_share = 0.75]
warn   stale-id-mention       key prose names an id that does not exist
warn   code-drift             code changed since the item was last verified
warn   code-inexact           a symbol ref with no marker ref beside it
warn   suppress-stale         a suppression that no longer resolves

27 rules · only errors move the exit code (--strict promotes warnings) · a threshold in brackets is what that rule measures against, settable with `kb thresholds`
```

- `--strict` promotes warnings to failures, for a deliberate cleanup pass.
- `--rule R` runs one rule; `--no-files` drops every rule that touches the tree,
  which is what makes lint usable against a store kept away from its repo.
- **`--all` shows everything**: every threshold off, every suppressed finding
  printed and tagged. Nothing a default run holds back is invisible — the
  summary always says how many findings a threshold withheld.
- **Scope it** with ids, `--section S` or `--surfaced-file F` when a team owns
  part of the graph. A finding is in scope when its subject is; the ones that
  belong to the whole store are counted out loud rather than dropped.
- `--fix` does the one mechanical repair there is: repointing a marker ref whose
  region now lives in exactly one other file. It writes the store only, and
  refuses to guess when the region turns up in more than one place.
- **`--json` prints the command**, not just the finding: every row carries the
  `kb suppress --who … --rule … [--ref N]` call that silences it, filled in but
  for the reason. This is the intended way to work through a backlog — nothing
  has to infer which parts of a text layout are arguments.
- Suppress a finding with `kb suppress --who W --rule R --reason "…"`, where `W`
  is the finding's own `who` — an item id, a `[section]`, a `keyword:<kw>`.
  `--ref N` narrows it to one code ref, 0-based as `kb item` prints it. The
  reason is mandatory, and `kb suppress --list`, every lint summary and
  `kb stats` report the live count, so escape hatches cannot quietly accumulate.
- A suppression is granted **against the evidence it names**. Rewrite the key it
  was written about and the exemption suspends: the finding comes back and
  `suppress-stale` reports the entry, with its reason, so re-confirming it is a
  decision. Only `kb suppress` re-stamps — `kb verify` deliberately does not.
- A per-ref exemption **follows its ref**. `kb code rm` renumbers the entries
  above the one it removed and drops the one it orphaned, printing its reason;
  `kb section rm` and `rename` do the same for the root entries naming them. An
  exemption expires or moves — it never silently transfers to something else.
- A renamed-away **section stays findable**. Once a section itself is renamed,
  its old id stops being "known", and `stale-id-mention` would otherwise lose
  the ability to ever find prose still citing it — not just at rename time,
  permanently. `kb rename`, `kb move`, `kb section rename` and
  `kb subsection rename` all write to `kb legacy` when a rename leaves any
  prose citing the old name (item, section or subsection alike, never for a
  name nothing cites), and a `section`-kind entry there is what keeps the old
  prefix recognised. An entry clears itself the moment nothing cites it any
  more, or `kb legacy rm NAME` retires it by hand.
- Two rules cannot be suppressed. `suppress-stale` reports exemptions that no
  longer resolve, so silencing it would hide what it exists to surface.
  `code-drift` has no basis it could be granted against — the ref's content is
  what re-fires it — so the answer is `kb verify`, and the refusal says so.

## The ratchet

Warnings are a backlog, so a gate that failed on their absolute count would be
switched off within a day. What can gate is **regression**:

```
kb baseline write                  record today's counts as the floor
kb lint --baseline                 exit 1 only if something got worse
```

The comparison is per rule, plus total over-cap excess — never a single sum of
warnings. Splitting one oversized item into three *raises* `key-length` by two
while paying down the debt, and only tracking both numbers lets that pass.

Three rises are reported and still exit 0, because none of them is decay:

| | when |
|---|---|
| `split` | `key-length` rose but total excess fell |
| `threshold-moved` | a rule is measuring against a different number, because someone ran `kb thresholds` |
| `rule-gone` | a rule the baseline has and this build does not — renamed or split |
| `new-rule` | a rule the recorded baseline predates |

Each of them prints the `kb baseline write` that records the new floor.

`.kb-baseline.json` **belongs in git**. It is the ratchet's only reference, so
an unstaged one means a fresh clone compares against nothing.

## Hygiene in a consumer repo

The point of the scoping flags is that a team can be handed a part of the graph
and trust the report about it.

```
kb lint --section lint --all       everything wrong in [lint], nothing withheld
kb lint --section lint --json      the same, with the command to silence each
kb debt --section lint             grouped by item, worst first
kb lint --section <yours> --strict hold the section to zero
```

Nothing is hidden by default and nothing is hidden by scoping. A default run
already reports every finding it has; `--all` additionally turns off the two
thresholds that can withhold one (`edge_kind_share`, `keyword_broad_share`) and
prints suppressed findings tagged. A scoped run says in its summary how many
findings it skipped as belonging to another part of the store.

Tune the thresholds per repo rather than arguing with them:

```
kb thresholds                      what they are, and which rule reads each
kb thresholds set edge_kind_share 0.9
```

Then record the floor and let the ratchet hold it:

```
kb baseline write --notes "lint section adopted edge kinds"
```

## Exit codes

| code | meaning |
|---|---|
| 0 | success |
| 1 | a check failed: lint errors, render drift, no path between two items |
| 2 | bad usage, or a mutation refused before it wrote anything |

Scripts and hooks may rely on the split. Note that `--strict` moves warnings
into code 1, and that `kb path` returning 1 is a real answer rather than a
malfunction.

## Pre-commit hook

```sh
cp hooks/pre-commit /path/to/your/repo/.git/hooks/ && chmod +x .git/hooks/pre-commit
```

Three checks, cheapest first — `validate`, then `render --check`, then `lint
--errors-only` — about 0.1s on a 100-item KB over a 600-file tree, so it runs on
every commit rather than only the ones that touch the KB. A hand-edit to the md
can arrive in a commit that never mentions it.

A fourth check runs **only if `.kb-baseline.json` exists**: `kb lint
--baseline`, which fails the commit on a regression while staying silent about
the standing backlog. A repo that has never run `kb baseline write` is
unaffected.

The hook exits 0 when `kb` is not on `PATH` and when the repo has no store, so
it is safe to install everywhere.

## Working on kb-graph itself

```
$ pip install -e ".[test]"
$ pytest                           # 59 tests, ~630 assertions
```

The package itself has zero dependencies; `pytest` is the only dev one, and the
suites are ordinary pytest modules — plain `assert`, no custom runner. `ok(name,
cond)` and `has(name, text, *needles)` are assertion helpers, not a framework:
they attach the sentence being checked to the failure message, which a bare
`assert "x" in out` cannot.

| file | covers |
|---|---|
| `tests/test_v2.py` | `src/kbgraph/` — the command, end to end |
| `tests/test_docs.py` | every documented `kb …` invocation, including `--help` epilogs |
| `tests/test_packaging.py` | `kb manual` / `kb changelog` output against the two root files |

Tests shell out to the CLI and assert on its stdout, because that surface *is*
the contract. A refactor behind it is free; a changed message that the docs
quote is a test failure, which is the point.

`test_docs.py` extracts every `kb …` invocation from `README.md`, `DESIGN.md`,
`CHANGELOG.md` **and every `--help` epilog**, then checks each against the live
argument parser:
unknown command, unknown flag, or a flag on the wrong command all fail. The
read-only ones that contain no placeholder are additionally executed against this
repo's own store and must exit 0. It reports every drifted example in one run
rather than stopping at the first, so it can also be aimed at a single doc:

```
$ python tests/test_docs.py README.md
```

Documentation drift is the failure mode this project can least afford, since the
docs are read by an agent that will act on them — and a wrong example is no
better for being in `--help` than in a file.

`DESIGN.md` holds the schema in full and the decision log — the reasoning behind
choices that a cold reader would otherwise relitigate.

## Gotchas

**Ids resolve loosely.** Exact, then unique suffix, then unique substring, so
`kb item upward-discovery` finds `store.upward-discovery`. An ambiguous fragment
lists the candidates rather than guessing.

**Flags on `kb code` must trail the positionals.** `kb code add ID marker path
--replace` parses; `kb code add ID --replace marker path` does not. argparse
cannot resume a variadic positional list after an optional.

**`kb kw` splits on commas and middots, not whitespace.** A keyword may be up to
three words, so `kb kw add x 'access token'` is one term and `kb kw add x access
token` is two.

**A summary must be shorter than its key.** It is a compression of it, and the
reverse is a lint error.

**`kb code clear` is refused for a type that requires a code ref.** Use
`kb code add <id> marker <path> --replace` to swap the code surface atomically.
Both drop every per-ref suppression on the item, naming each with its reason as
they go -- an exemption keyed on a ref index has nothing left to mean once that
ref is gone.

**No item anchored at a file is not silence.** It means no rule is *recorded*
for that file, not that none applies. `kb ctx --path` says so explicitly and
points at `--query`.
