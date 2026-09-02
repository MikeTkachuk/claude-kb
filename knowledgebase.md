# kb-graph

<!-- Generated from knowledgebase.json by `kb render`. Do not hand-edit: `kb render --check` fails on drift, and hand-edits are translated into `kb` commands, never absorbed. -->

## §index

- **[directive]** (4) — directive.documented-examples-are-executed · directive.kb-is-the-only-access-path · directive.never-hand-edit-either-file
- **[invariants]** (8) — invariants.md-is-one-way · invariants.never-writes-source · invariants.sole-interface · invariants.total-command-surface
- **[store]** (7) — store.canonical-writer · store.upward-discovery
- **[model]** (7) — model.derived-inverse · model.prefix-resolution
- **[render]** (4) — render.check-is-the-gate
- **[read]** (5) — read.impact-ids-only-is-bare
- **[ctx]** (5) — ctx.directives-always-included · ctx.mandatory-first-step · ctx.unanchored-path-is-not-silence
- **[write]** (17) — write.touch-and-commit · write.validate-before-write · write.verified-is-not-modified
- **[lint]** (19) — lint.severity-split · lint.suppression-needs-reason
- **[debt]** (5)
- **[markers]** (9) — markers.jsx-comment-form · markers.own-line-and-own-language · markers.pair-syntax
- **[search]** (4) — search.idf-bag
- **[cli]** (7) — cli.global-flags-work-on-both-sides · cli.utf8-lf-console
- **[migrate]** (4) — migrate.dates-from-git
- **[testing]** (5) — testing.pytest-runner

---

## [directive]

**Key knowledge:**
- `directive.documented-examples-are-executed` — Every documented example is run before it ships.
- `directive.kb-is-the-only-access-path` — Read the graph only through `kb`, never the files.
- `directive.never-hand-edit-either-file` — Both KB files are written by `kb` alone.

**Related:** [invariants] (3) · [testing] (2) · [cli] (1) · [write] (1)

### directive.documented-examples-are-executed
`directive` · `active`

Documentation is held to the same standard as code: every command shown in help text, README.md, DESIGN.md or CHANGELOG.md MUST have been executed against a real store, and its output pasted rather than imagined. A plausible-looking example that does not parse is worse than no example, because it is trusted. tests/test_docs.py enforces it, over every doc named in its DOCS tuple.

- **Relates to:** cli.help-is-the-documentation · testing.drives-the-cli
- **Referenced by:** testing.docs-checker-flag-shapes

### directive.kb-is-the-only-access-path
`directive` · `active`

The model reaches the graph ONLY through the kb CLI. Never read knowledgebase.json; never grep knowledgebase.md. Every question the graph can answer has a command, so a grep is always the worse answer to a question a command answers exactly. If kb is not on PATH, say so and stop rather than falling back to the md.

- **Relates to:** invariants.sole-interface
- **Affected by:** —

### directive.never-hand-edit-either-file
`directive` · `active`

knowledgebase.json and knowledgebase.md are written by kb and by nothing else. A hand-edit to the md is translated back into kb commands by a model reading the diff, never absorbed; a hand-edit to the json is a canonical-form and validation bypass. The two are committed together, because render --check fails on any drift between them.

- **Relates to:** invariants.both-files-committed · invariants.no-md-parser
- **Affected by:** —

### directive.trust-the-code
`directive` · `active`

When the graph and the code disagree, the code is right and the graph is stale. Fix the item, do not argue from it. After any edit, update every item whose key the change made false.

- **Relates to:** write.verified-is-not-modified
- **Affected by:** —

---

## [invariants]

**Key knowledge:**
- `invariants.md-is-one-way` — Markdown is a render for humans, never an input.
- `invariants.never-writes-source` — kb is read-only against the source tree.
- `invariants.sole-interface` — Every question is a command, not a file read.
- `invariants.total-command-surface` — One command per writable field, in both directions.

**Related:** [render] (4) · [cli] (3) · [directive] (3) · [markers] (3) · [model] (3) · [write] (3) · [ctx] (1) · [lint] (1) · [read] (1) · [store] (1)

### invariants.both-files-committed
`invariant` · `active`

The store and its render MUST be committed together and MUST NEVER be staged apart. Pre-commit runs render --check, which fails when the md on disk is not exactly render(json), catching a stale render and a hand-edit with one test.

- **Code:** `hooks/pre-commit`
- **Constrains:** render.check-is-the-gate · write.touch-and-commit
- **Referenced by:** directive.never-hand-edit-either-file

### invariants.derive-everything-derivable
`invariant` · `active`

Anything derivable MUST be derived rather than stored: inverse edges, section membership, section adjacency, the index, and marker line numbers. Stored redundancy is stored drift.

- **Code:** `src/kbgraph/model.py`
- **Constrains:** model.derived-inverse · model.no-line-numbers · render.derived-sections
- **Refined by:** model.subsection-longest-prefix-wins

### invariants.indexes-are-zero-based
`invariant` · `active`

Every index kb prints or accepts MUST be 0-based. Code refs are the only entity addressed by position today -- edges go by target, keywords by term -- and kb code rm, kb suppress --ref, kb item and lint.relocations now agree. An index that is never printed cannot be checked by the reader, so the number is shown wherever a ref is.

- **Code:** `src/kbgraph/read.py` → `show_item`
- **Constrains:** invariants.total-command-surface
- **Affected by:** —

### invariants.md-is-one-way
`invariant` · `active`

knowledgebase.md is rendered one-way from the store and is ONLY ever read by humans. Because the model does not read it, the render optimises for comprehension rather than grep-ability.

- **Code:** `src/kbgraph/render.py`
- **Constrains:** render.check-is-the-gate · render.derived-sections
- **Refined by:** invariants.no-md-parser

### invariants.never-writes-source
`invariant` · `active`

kb writes source in EXACTLY one command, kb code fmt --write, only to move a marker across a blank or merge adjacent markers: it MUST NEVER change a non-marker, non-blank line, which fmt_check re-verifies before writing. Markers are still placed by hand; lint --fix writes only the store.

- **Code:**
  - `src/kbgraph/lint.py`
  - `src/kbgraph/markers.py` → `fmt_check`
- **Constrains:** lint.fix-is-store-only · markers.hand-placed-with-a-printed-pair · markers.only-kind-with-snippets
- **Refined by:** markers.fmt-touches-markers-and-blanks-only

### invariants.no-md-parser
`invariant` · `active`

No markdown parser exists in either direction, so a hand-edit to the md can NEVER be absorbed automatically. It is translated into kb commands by a model reading git diff knowledgebase.md.

- **Code:** `src/kbgraph/render.py`
- **Refines:** invariants.md-is-one-way
- **Referenced by:** directive.never-hand-edit-either-file

### invariants.sole-interface
`invariant` · `active`

The CLI MUST be the only interface to the graph. This is what pays for the JSON store: the engine assembles exactly the right context in one call instead of the model guessing slice offsets in a markdown file.

- **Code:** `src/kbgraph/cli.py`
- **Constrains:** cli.help-is-the-documentation · ctx.mandatory-first-step · read.impact-ids-only-is-bare
- **Referenced by:** directive.kb-is-the-only-access-path

### invariants.total-command-surface
`invariant` · `active`

The command surface MUST be total: every writable field has EXACTLY one command, and every question answerable from the graph has a command that answers it. A field reachable only by hand-editing JSON is a bug, and so is a question that can only be answered by reading a file.

- **Code:** `src/kbgraph/write.py`
- **Constrains:** cli.no-store-commands · write.dual-dispatch
- **Constrained by:** invariants.indexes-are-zero-based · store.thresholds-are-per-repo
- **Refined by:** cli.help-is-the-documentation · write.one-code-ref-per-flag

---

## [store]

**Key knowledge:**
- `store.canonical-writer` — Leaf objects inline, containers indented: an edge is a one-line diff.
- `store.upward-discovery` — The store is found by walking up from CWD.

**Related:** [lint] (2) · [model] (2) · [cli] (1) · [invariants] (1) · [migrate] (1) · [render] (1) · [write] (1)

### store.canonical-writer
`design-choice` · `active`

The store is written by a hand-rolled emitter rather than json.dump(indent=2), which would put every edge on four lines and turn a one-word change into a twelve-line diff. Objects whose every value is a scalar or a list of scalars are emitted inline; only containers are indented. Diff quality is a requirement, not a nicety.

- **Code:**
  - `src/kbgraph/store.py` → `dumps`
  - `src/kbgraph/store.py` *(marker)*
- **Relates to:** render.byte-conventions
- **Affected by:** —

### store.lint-config-key
`concept` · `active`

The root `lint` key holds two things a graph cannot: per-repo lint thresholds, and the suppressions for findings whose `who` is not an item -- a section, a subsection, a keyword, a path. Item suppressions stay on the item. The key is omitted entirely when both halves are empty, so schema 4 costs an untouched store nothing but its version stamp.

- **Code:** `src/kbgraph/model.py` → `LintConfig`
- **Constrains:** lint.suppression-needs-reason
- **Refines:** store.unknown-field-rejected
- **Refined by:** store.thresholds-are-per-repo

### store.schema-1-hard-fail
`edge-case` · `active`

A directory holding a v1 knowledgebase.md and no store raises with a migrate hint rather than being skipped on the way up the tree. kb 4.x carries no dual-format code: a v1 repo either migrates or keeps its own protocol.

- **Code:** `src/kbgraph/store.py` → `MIGRATE_HINT`
- **Refines:** migrate.one-way-only
- **Affected by:** —

### store.thresholds-are-per-repo
`design-choice` · `active`

Every lint threshold lives in model.THRESHOLDS with a default and is overridable per store through `kb thresholds`, because the number that makes a rule useful differs between a 40-item store and a 400-item one. The name in THRESHOLDS is the store key, the command argument and the flag name at once, so a threshold cannot exist under two spellings.

- **Code:** `src/kbgraph/model.py` → `THRESHOLDS`
- **Constrains:** invariants.total-command-surface
- **Refines:** store.lint-config-key
- **Consumed by:** lint.thresholds-not-gates

### store.unknown-field-rejected
`guardrail` · `active`

A field that is not in ITEM_FIELDS or ROOT_FIELDS MUST be rejected at load as a schema error, at both the item and the top level, so a typo cannot survive a round-trip by being quietly carried through the writer.

- **Code:** `src/kbgraph/store.py` → `_reject_unknown_fields`
- **Refines:** model.validate-is-structural
- **Constrained by:** store.write-implies-load
- **Refined by:** store.lint-config-key

### store.upward-discovery
`behavior` · `active`

find_store walks up from the working directory looking for knowledgebase.json, so the tool is installed once and works from anywhere inside any repo. --kb overrides it with an explicit path, and $KB_FILE is not consulted in v2.

- **Code:**
  - `src/kbgraph/store.py` → `find_store`
  - `src/kbgraph/store.py` *(marker)*
- **Relates to:** cli.no-store-commands
- **Affected by:** —

### store.write-implies-load
`invariant` · `active`

A write kb accepts MUST be a load kb accepts. commit() validates before it serialises and load() validates what it read, both through the same pure validate(), so the only reachable failure is a refused edit and never an unreadable file. This is what bounds validate's scope: it may enforce exactly the invariants every mutation command maintains, because kb cannot repair a store it cannot open.

- **Code:** `src/kbgraph/write.py` *(marker)*
- **Constrains:** store.unknown-field-rejected
- **Refines:** model.validate-is-structural
- **Refined by:** write.mutations-maintain-their-references

---

## [model]

**Key knowledge:**
- `model.derived-inverse` — Only `affects` is stored; every inverse is computed.
- `model.prefix-resolution` — Ids resolve by exact match, then unique suffix, then unique substring.

**Related:** [write] (4) · [invariants] (3) · [ctx] (2) · [store] (2) · [markers] (1) · [migrate] (1) · [render] (1)

### model.derived-inverse
`concept` · `active`

Edges are stored on the source item only, as {to, kind}. The inbound map is computed from them on demand and cached per load. This is what deletes v1 edge symmetry as an invariant, its lint rule, and its two-sided writes: there is one write and it cannot go half-done.

- **Code:**
  - `src/kbgraph/model.py` → `Graph.inbound`
  - `src/kbgraph/model.py` *(marker)*
- **Constrains:** render.derived-sections
- **Constrained by:** invariants.derive-everything-derivable
- **Consumed by:** migrate.edges-unioned

### model.id-is-section-membership
`guardrail` · `active`

An item id MUST be <section>.<slug>, lowercase dotted or dashed, and its prefix MUST match its section field. Membership is therefore not stored twice and cannot drift, and moving an item between sections is ALWAYS a rename.

- **Code:** `src/kbgraph/model.py` → `Graph._validate_item`
- **Constrains:** write.rename-repoints-every-edge · write.subsection-rename-mirrors-section-rename
- **Refined by:** model.subsection-longest-prefix-wins

### model.no-line-numbers
`guardrail` · `active`

A code ref MUST NEVER carry a line number, and validation rejects a path containing one. Line numbers are resolved from markers on every read, because a stored line number is stale the next time anyone edits the file.

- **Code:**
  - `src/kbgraph/model.py` → `Graph._validate_item`
  - `src/kbgraph/model.py` *(marker)*
- **Constrains:** markers.pair-syntax
- **Constrained by:** invariants.derive-everything-derivable

### model.path-match-tiers
`behavior` · `active`

Matching an item to a file path tries exact, then path-suffix in either direction, then loose (same basename, or the query appearing anywhere in the ref). The best tier that hits at all wins outright, so a precise hit is never diluted by loose ones.

- **Code:** `src/kbgraph/model.py` → `Graph.match_paths`
- **Affects:** —
- **Consumed by:** ctx.root-selection-precedence

### model.prefix-resolution
`behavior` · `active`

Every command that takes an id resolves it in three tiers: exact, then unique suffix after a dot, then unique substring. An ambiguous fragment raises with the candidates listed rather than picking one, and an unknown one raises rather than crashing. This is why ids can be typed the way they are read.

- **Code:**
  - `src/kbgraph/model.py` → `Graph.get`
  - `src/kbgraph/model.py` *(marker)*
- **Relates to:** ctx.root-selection-precedence
- **Affected by:** —

### model.subsection-longest-prefix-wins
`guardrail` · `active`

An item MUST belong to the deepest declared subsection whose prefix it sits under, and to no other. A bare startswith puts pages.a.b.c in pages.a, pages.a.b and pages.a.b.c at once, so membership stops being a function and the render duplicates the item.

- **Code:** `src/kbgraph/model.py` *(marker)*
- **Refines:** invariants.derive-everything-derivable · model.id-is-section-membership
- **Affected by:** —

### model.validate-is-structural
`concept` · `active`

validate covers only what must hold for the graph to be a graph: schema version, id shape, required fields, known types and statuses and edge kinds, well-formed refs and dates. Every judgment call -- length, staleness, orphanhood -- belongs to lint, which runs on a store that already loaded.

- **Code:** `src/kbgraph/model.py` → `Graph.validate`
- **Constrains:** write.validate-before-write
- **Consumed by:** write.validate-before-write
- **Refined by:** store.unknown-field-rejected · store.write-implies-load

---

## [render]

**Key knowledge:**
- `render.check-is-the-gate` — `render --check` compares bytes and names the first differing line.

**Related:** [invariants] (4) · [migrate] (1) · [model] (1) · [store] (1) · [write] (1)

### render.byte-conventions
`behavior` · `active`

The renderer reads the existing md BOM, line-ending and final-newline choices and reproduces them, so a rewrite never shows up as a whole-file diff on a repo that uses CRLF or a BOM.

- **Code:**
  - `src/kbgraph/render.py` → `conventions`
  - `src/kbgraph/render.py` *(marker)*
- **Affects:** —
- **Referenced by:** store.canonical-writer

### render.check-is-the-gate
`behavior` · `active`

render --check compares the md on disk byte-for-byte against render(store), exits 1 on any difference, and prints the first line that differs on both sides. One test catches both a stale render and a hand-edit, which is why it is what pre-commit runs.

- **Code:**
  - `src/kbgraph/render.py` → `check`
  - `src/kbgraph/render.py` *(marker)*
- **Affects:** —
- **Constrained by:** invariants.both-files-committed · invariants.md-is-one-way
- **Called by:** write.touch-and-commit

### render.derived-sections
`concept` · `active`

Section key knowledge, member lists, related-section lists with edge counts, and the index are all computed at render time from the items. In v1 these were hand-maintained prose and were the single largest source of drift in the file.

- **Code:** `src/kbgraph/render.py` → `render_section`
- **Affects:** —
- **Constrained by:** invariants.derive-everything-derivable · invariants.md-is-one-way · model.derived-inverse

### render.legacy-guard
`edge-case` · `active`

Any write that would overwrite a schema-1 knowledgebase.md refuses, since it would discard content that was never migrated. render --force is the deliberate override; migrate is exempt, as replacing the file it just consumed is its job.

- **Code:** `src/kbgraph/render.py` → `LegacyRender`
- **Refines:** migrate.one-way-only
- **Affected by:** —

---

## [read]

**Key knowledge:**
- `read.impact-ids-only-is-bare` — `impact --ids-only` prints ids and nothing else, so it pipes.

**Related:** [cli] (2) · [invariants] (1) · [lint] (1) · [search] (1)

### read.guardrails-follows-the-glossary
`behavior` · `active`

The guardrails command lists items whose type is declared requires_modal in the glossary, not items of a type literally named guardrail. Adding a modal-carrying type therefore extends the command with no code change.

- **Code:** `src/kbgraph/read.py` → `cmd_guardrails`
- **Relates to:** lint.rule-table-is-the-source
- **Affected by:** —

### read.impact-ids-only-is-bare
`guardrail` · `active`

impact --ids-only MUST emit bare ids, one per line, with no header and no section lines. The flag exists so the closure can be piped into xargs kb verify, and any decoration in that stream becomes an argument to the next command.

- **Code:** `src/kbgraph/read.py` → `cmd_impact`
- **Affects:** —
- **Constrained by:** cli.utf8-lf-console · invariants.sole-interface
- **Referenced by:** read.tree-vs-impact

### read.kw-suggest-writes-nothing
`behavior` · `active`

kw-suggest prints a ready-to-run kb kw add line per item and writes nothing. A keyword that does not discriminate is worse than no keyword, and only a human can tell which of the IDF-ranked candidates do, so acceptance is a separate deliberate step.

- **Code:** `src/kbgraph/read.py` → `cmd_kw_suggest`
- **Consumes:** search.idf-bag
- **Affected by:** —

### read.path-exits-1-when-disconnected
`edge-case` · `active`

path runs breadth-first over edges in both directions, since a stored direction is about causality rather than reachability. Two disconnected items are reported as such and exit 1, which makes the command usable as a test.

- **Code:** `src/kbgraph/read.py` → `cmd_path`
- **Affects:** —
- **Constrained by:** cli.exit-codes

### read.tree-vs-impact
`concept` · `active`

tree is an indented recursive walk that revisits a shared descendant once per path to it and marks a repeat on one branch as a cycle. impact is the deduped closure grouped by section with a depth mark. Use tree to understand shape, impact to answer what must be re-verified.

- **Code:** `src/kbgraph/read.py` → `cmd_tree`
- **Relates to:** read.impact-ids-only-is-bare
- **Affected by:** —

---

## [ctx]

**Key knowledge:**
- `ctx.directives-always-included` — Every context carries the directives; they override everything in it.
- `ctx.mandatory-first-step` — ctx --path before touching any file. No exceptions but an explicit one.
- `ctx.unanchored-path-is-not-silence` — No item at a path means no rule is recorded, not that none applies.

**Related:** [model] (2) · [invariants] (1) · [search] (1)

### ctx.budget-degradation
`behavior` · `active`

Under --budget the output degrades in fixed steps: neighbour key text is truncated, then the neighbour list is shortened, then the neighbourhood block is replaced by a pointer to kb impact. The head, the root items and the directives are never dropped, so a budgeted context is smaller but never wrong.

- **Code:**
  - `src/kbgraph/ctx.py` → `STEPS`
  - `src/kbgraph/ctx.py` *(marker)*
- **Affects:** —
- **Constrained by:** ctx.directives-always-included

### ctx.directives-always-included
`guardrail` · `active`

Directives are appended to every context and MUST NEVER be dropped by a budget, because they override every other item -- a context that omits them is not a smaller context, it is one that can argue you into violating one. --no-directives is the explicit opt-out and is almost never what you want.

- **Code:** `src/kbgraph/ctx.py` → `build`
- **Constrains:** ctx.budget-degradation
- **Affected by:** —

### ctx.mandatory-first-step
`guardrail` · `active`

kb ctx --path <file> MUST be run before editing any source file, and it is not skippable for a fix that looks obvious. The ONLY exception is the user saying so for that request. It replaces grep-the-KB-and-slice-read-it with one question, and it is the command the whole design exists for.

- **Code:** `src/kbgraph/ctx.py` → `cmd_ctx`
- **Consumes:** search.idf-bag
- **Constrained by:** invariants.sole-interface
- **Refined by:** ctx.unanchored-path-is-not-silence

### ctx.root-selection-precedence
`behavior` · `active`

Roots come from --path, else --query, else the positional ids, in that order when more than one is given. --path takes the best matching tier only; --query takes the top -n scored items.

- **Code:** `src/kbgraph/ctx.py` → `_roots`
- **Consumes:** model.path-match-tiers
- **Referenced by:** model.prefix-resolution

### ctx.unanchored-path-is-not-silence
`edge-case` · `active`

When no item has a code ref to the requested file, ctx says so explicitly and points at --query and dup, because the difference between no rule recorded and no rule applying is exactly where a confident wrong edit comes from.

- **Code:** `src/kbgraph/ctx.py` → `_roots`
- **Refines:** ctx.mandatory-first-step
- **Affected by:** —

---

## [write]

**Key knowledge:**
- `write.touch-and-commit` — Every mutation routes through touch() then commit().
- `write.validate-before-write` — A mutation whose result would be malformed is refused, not half-applied.
- `write.verified-is-not-modified` — verify records re-checking; editing an item is not re-checking it.

**Related:** [lint] (7) · [model] (4) · [invariants] (3) · [cli] (2) · [directive] (1) · [migrate] (1) · [render] (1) · [store] (1)

### write.buffered-output
`design-choice` · `active`

Command summaries are buffered with say() and flushed by commit only once the write is past its guards, because a refused command that has already printed verified or renamed is worse than one that prints nothing.

- **Code:**
  - `src/kbgraph/write.py` → `say`
  - `src/kbgraph/write.py` *(marker)*
- **Refines:** write.validate-before-write
- **Affected by:** —

### write.cap-refused-at-the-write
`guardrail` · `active`

A key past its type's cap MUST be refused by `kb new` and `kb set <id> key`/`type`, not merely warned about later by `key-length`. Over the cap: trim; over 1.5x: split -- usually two rules under one id. `--suppress-length` is the only override, recording a suppression with a basis over the key.

- **Code:**
  - `src/kbgraph/write.py` → `cap_guard`
  - `src/kbgraph/write.py` *(marker)*
- **Consumes:** write.suppression-records-its-basis
- **Refines:** lint.suppression-needs-reason
- **Affected by:** —

### write.destructive-needs-yes
`guardrail` · `active`

Deleting an item, clearing an items code refs, removing a section or a glossary entry MUST carry --yes, or --dry-run which satisfies the same guard by writing nothing. Non-destructive mutations apply immediately.

- **Code:**
  - `src/kbgraph/write.py` → `need_yes`
  - `src/kbgraph/write.py` *(marker)*
- **Relates to:** cli.global-flags-work-on-both-sides
- **Affected by:** —

### write.dual-dispatch
`design-choice` · `active`

code, kw, section and subsection are one command name each for both sides: if the first positional is one of the verbs THAT command accepts, the write path runs, else the read path does. The verb set is per command, not the union: an unimplemented verb used to fall into the last branch, removal for kw.

- **Code:**
  - `src/kbgraph/write.py` → `dispatch`
  - `src/kbgraph/write.py` *(marker)*
- **Affects:** —
- **Constrained by:** invariants.total-command-surface

### write.long-value-word-diff
`design-choice` · `active`

Setting key or summary prints a word-level diff with unchanged runs collapsed once either side passes DIFF_THRESHOLD (160 chars); shorter values keep the two-line form every test asserts on. SequenceMatcher runs with autojunk=False, without which a one-word edit in a long key collapses into one opaque replace.

- **Code:** `src/kbgraph/write.py` *(marker)*
- **Relates to:** write.summary-staleness-nudge
- **Affected by:** —

### write.mutations-maintain-their-references
`invariant` · `active`

A command that retires something another key points at MUST repoint or drop those references in the same mutation. kb code rm renumbers lint.ignore[].ref and drops the entry it orphans; kb section rm and rename cascade the root suppressions that name them. A reference the store validates is only enforceable because no command can leave it dangling, and every dropped entry is printed with its reason.

- **Code:**
  - `src/kbgraph/write.py` → `_reindex_ref_suppressions`
  - `src/kbgraph/write.py` → `_cascade_prefix`
- **Refines:** store.write-implies-load
- **Affected by:** —

### write.one-code-ref-per-flag
`guardrail` · `active`

`--code` and `kb code add` MUST take exactly one ref and refuse every trailing token. Tokens used to be folded into `part`, so `symbol <path> <sym> marker <path>` silently became one ref carrying the other as a label -- four items in this store lost a marker ref that way. A second ref is a second flag.

- **Code:**
  - `src/kbgraph/write.py` → `parse_code`
  - `src/kbgraph/write.py` *(marker)*
- **Refines:** invariants.total-command-surface
- **Affected by:** —

### write.rename-persists-legacy-names
`behavior` · `active`

Any rename that leaves prose citing the old name records {name, kind, renamed_to, since} in lint.legacy, only when something actually cites it. The entry clears itself once nothing does, or kb legacy rm retires it by hand; reoccupying the name reclaims it.

- **Code:**
  - `src/kbgraph/write.py` → `_note_legacy`
  - `src/kbgraph/write.py` → `_autoclear_legacy`
  - `src/kbgraph/write.py` → `_reclaim_legacy`
- **Constrains:** lint.legacy-widens-the-known-section-gate
- **Refines:** write.rename-repoints-every-edge
- **Affected by:** —

### write.rename-refuses-id-collisions
`guardrail` · `active`

A rename's cascade MUST refuse before mutating anything if a derived id would collide with an existing, unrelated item or subsection -- a destination is never guaranteed empty the way a brand-new section is.

- **Code:** `src/kbgraph/write.py` → `_rename_prefix`
- **Constrains:** write.subsection-rename-mirrors-section-rename
- **Affected by:** —

### write.rename-repoints-every-edge
`behavior` · `active`

rename, move and section rename all re-key the item and repoint every inbound edge, deduping the case where an edge already pointed at the new id. Because membership is the id prefix, move and section rename are renames of every affected member. Prose citations are not repointed; stale-id-mention reports them.

- **Code:** `src/kbgraph/write.py` → `rename`
- **Constrains:** lint.id-mentions-need-a-section-prefix
- **Constrained by:** model.id-is-section-membership
- **Refined by:** write.rename-persists-legacy-names · write.subsection-rename-mirrors-section-rename

### write.stdin-is-decoded-utf8
`guardrail` · `active`

`--file -` and `--key-file -` MUST decode stdin as UTF-8, NEVER through sys.stdin's own codec -- that is the locale's, cp1252 on Windows. The corruption is silent: a cp1252 read then cp1252 write is byte-identity, so it round-trips to the screen and only reaches the file.

- **Code:**
  - `src/kbgraph/write.py` → `read_text`
  - `src/kbgraph/write.py` *(marker)*
- **Refines:** cli.utf8-lf-console
- **Affected by:** —

### write.subsection-rename-mirrors-section-rename
`behavior` · `active`

kb section rename and kb subsection rename share one cascade: every item id, and every subsection id, nested under the old prefix is re-derived and re-keyed, since a section and a subsection are both just a name that member ids extend. Only a section rename also re-sections each item.

- **Code:** `src/kbgraph/write.py` → `_rename_prefix`
- **Refines:** write.rename-repoints-every-edge
- **Constrained by:** model.id-is-section-membership · write.rename-refuses-id-collisions

### write.summary-staleness-nudge
`edge-case` · `active`

Setting a key on an item with a summary prints it back, but only once the edit moved at least NUDGE_SHARE (0.2) of the key's words -- a one-word fix does not. meta is per item, not per field, so no lint rule can catch a stale summary.

- **Code:**
  - `src/kbgraph/write.py` → `cmd_set`
  - `src/kbgraph/write.py` *(marker)*
- **Relates to:** lint.no-summary-stale-rule
- **Referenced by:** write.long-value-word-diff

### write.suppression-records-its-basis
`behavior` · `active`

kb suppress stamps a `basis` on the entry: a digest of exactly the fields the rule reads -- the key, the summary, the keywords, the edge list, or one code ref's identity or content. Per field rather than per item, so adding a keyword does not invalidate a key-length exemption. Rules whose evidence is source text or a store-wide count get no basis and are not checked.

- **Code:**
  - `src/kbgraph/model.py` → `basis`
  - `src/kbgraph/write.py` → `cmd_suppress`
- **Refines:** lint.suppression-needs-reason
- **Consumed by:** lint.basis-mismatch-suspends · write.cap-refused-at-the-write

### write.touch-and-commit
`concept` · `active`

touch stamps meta.modified and the ambient meta.run on every item a command changed, including the far end of an edge. commit re-validates, writes the store and re-renders the markdown in one call, so the two files cannot drift apart by one command width.

- **Code:**
  - `src/kbgraph/write.py` → `commit`
  - `src/kbgraph/write.py` *(marker)*
- **Calls:** render.check-is-the-gate
- **Constrained by:** invariants.both-files-committed

### write.validate-before-write
`guardrail` · `active`

Every mutation MUST re-validate the whole graph in memory BEFORE a byte is written, and refuse with the problems listed if the result would be malformed. There is therefore no state in which the store is half-updated, and git is the only undo anyone needs.

- **Code:**
  - `src/kbgraph/write.py` → `commit`
  - `src/kbgraph/write.py` *(marker)*
- **Consumes:** model.validate-is-structural
- **Constrained by:** model.validate-is-structural
- **Refined by:** write.buffered-output

### write.verified-is-not-modified
`guardrail` · `active`

meta.verified MUST only move when someone re-read the item against the code and found it still true, which is what kb verify records -- along with a content digest for every file and marker ref, so code-drift can tell later that the code moved underneath it. Editing an item moves meta.modified and NEVER verified.

- **Code:** `src/kbgraph/write.py` → `cmd_verify`
- **Affects:** —
- **Referenced by:** directive.trust-the-code · lint.basis-mismatch-suspends · migrate.dates-from-git

---

## [lint]

**Key knowledge:**
- `lint.severity-split` — Only errors move the exit code; warnings are a backlog.
- `lint.suppression-needs-reason` — A suppression without a reason is refused.

**Related:** [write] (7) · [debt] (4) · [markers] (4) · [store] (2) · [cli] (1) · [invariants] (1) · [read] (1)

### lint.basis-mismatch-suspends
`guardrail` · `active`

A suppression carrying a basis is SUSPENDED once that basis stops matching: the finding returns at full severity and suppress-stale names the entry with its reason. The entry is never deleted -- the reason is the expensive part -- and only kb suppress re-stamps it. kb verify deliberately does not, so re-verifying an item whose code moved invalidates its code-based exemptions rather than renewing them in passing.

- **Code:**
  - `src/kbgraph/lint.py` → `_basis_moved`
  - `src/kbgraph/lint.py` *(marker)*
- **Consumes:** write.suppression-records-its-basis
- **Relates to:** write.verified-is-not-modified
- **Refined by:** lint.two-rules-cannot-be-suppressed

### lint.edge-kinds-are-judged-per-section
`behavior` · `active`

edge-kind-missing fires for an item with a bare edge only while its SECTION labels less than edge_kind_share (0.75) of its edges -- not the store, because a section is the scope a consumer team owns and the number it is judged on has to be one it can move. A section with no edges scores 1.0. Findings held back by the threshold are counted and reported, never silently dropped.

- **Code:**
  - `src/kbgraph/lint.py` → `section_edge_share`
  - `src/kbgraph/lint.py` *(marker)*
- **Refines:** lint.thresholds-not-gates
- **Affected by:** —

### lint.fix-is-store-only
`guardrail` · `active`

lint --fix does the one mechanical repair there is -- repointing a marker ref whose region now lives in exactly one other file -- and MUST write only the store, never source. It refuses to guess when the region turns up in more than one place.

- **Code:**
  - `src/kbgraph/lint.py` → `_fix`
  - `src/kbgraph/lint.py` *(marker)*
- **Consumes:** lint.tree-scan
- **Constrained by:** invariants.never-writes-source

### lint.id-mentions-need-a-section-prefix
`design-choice` · `active`

stale-id-mention flags a prose token only when its first segment is a known section id and its last is not a file extension. Recall is traded for precision because 11 of this repo's 14 section ids are also module names: without the extension guard the rule reports lint.py and render.py as dead ids.

- **Code:** `src/kbgraph/lint.py` *(marker)*
- **Affects:** —
- **Constrained by:** write.rename-repoints-every-edge
- **Referenced by:** lint.legacy-widens-the-known-section-gate

### lint.json-carries-the-remedy
`behavior` · `active`

kb lint --json emits every finding with the exact kb suppress command that silences it, generated from the finding's own fields rather than written by hand, so what lint prints and what suppress accepts cannot drift. The reader this serves holds one line of output and no other context; a four-column text layout that never says which columns are the arguments serves it badly. The two unsuppressible rules carry a resolve hint instead.

- **Code:** `src/kbgraph/lint.py` → `_json`
- **Consumes:** lint.two-rules-cannot-be-suppressed · lint.who-is-the-suppression-key
- **Affected by:** —

### lint.legacy-widens-the-known-section-gate
`design-choice` · `active`

stale-id-mention's known-section gate also recognises a retired name from lint.legacy, but only kind 'section': that closes the one real detection gap (the old prefix is gone entirely), while an item or subsection keeps resolving fine on its own, so widening for those would add legacy entries with no lint benefit.

- **Code:** `src/kbgraph/lint.py` *(marker)*
- **Relates to:** lint.id-mentions-need-a-section-prefix
- **Constrained by:** write.rename-persists-legacy-names

### lint.marker-line-carries-ids-only
`guardrail` · `active`

marker-syntax is an ERROR: a kb: comment line MUST be kb:begin or kb:end followed by ids and a closer, no id twice, nothing else. Trailing prose used to be ignored, letting a corrupted marker delimit a region at the wrong place.

- **Code:** `src/kbgraph/markers.py` → `marker_ids`
- **Refines:** lint.severity-split · markers.pair-syntax
- **Affected by:** —

### lint.marker-refs-are-the-exact-form
`guardrail` · `active`

A symbol ref records an intention, not a fact: nothing checks the symbol is still in the file, digest_ref returns None for it, so it never drifts and never carries a content basis. code-inexact fires per symbol ref on any item with no marker ref beside it, and its message carries the kb:begin/kb:end pair and the follow-up command ready to paste. An item that has marked a region anywhere is silent.

- **Code:** `src/kbgraph/lint.py` *(marker)*
- **Refines:** markers.only-kind-with-snippets
- **Relates to:** lint.who-is-the-suppression-key
- **Affected by:** —

### lint.no-files-mode
`behavior` · `active`

lint --no-files drops every rule that touches the tree, which is what makes the command usable against a store kept away from the repo it describes, or in an environment where the tree is not checked out.

- **Code:** `src/kbgraph/lint.py` → `run`
- **Refines:** lint.tree-scan
- **Affected by:** —

### lint.no-summary-stale-rule
`limitation` · `active`

There is deliberately no rule for a summary left behind by a later key edit. meta is per item rather than per field, so the store cannot answer the question and no honest implementation exists. A write-time nudge in kb set replaces it.

- **Code:** `src/kbgraph/lint.py` → `cmd_lint`
- **Affects:** —
- **Referenced by:** write.summary-staleness-nudge

### lint.nothing-is-hidden-silently
`guardrail` · `active`

Every finding a run does not print MUST be counted in its summary: N hidden by thresholds, N suppressed, N outside this scope. kb lint --all then turns off every threshold and prints suppressed findings tagged. A consumer repo committing to fix everything in its scope needs a report it can trust to be complete, and a number it cannot see is one it cannot plan around.

- **Code:** `src/kbgraph/lint.py` → `_summary`
- **Constrains:** lint.scope-reports-what-it-skipped · lint.thresholds-not-gates
- **Affected by:** —

### lint.rule-table-is-the-source
`concept` · `active`

The RULES tuple is the single source for rule names, severities and descriptions. It backs lint --list, the --rule filter and the names kb suppress accepts -- and refuses, since an unknown name is now an error rather than a recorded entry that silences nothing. A rule cannot exist under one name in the code and another in the docs.

- **Code:**
  - `src/kbgraph/lint.py` → `RULES`
  - `src/kbgraph/lint.py` *(marker)*
- **Affects:** —
- **Consumed by:** debt.attribution-is-the-who-field
- **Refined by:** lint.severity-split
- **Referenced by:** read.guardrails-follows-the-glossary

### lint.scope-reports-what-it-skipped
`behavior` · `active`

kb lint takes ids, --section and --surfaced-file, the set kb debt already accepted. A finding is in scope when its subject is: an item in the scope, or a [section] / [prefix] inside it. Findings about the whole store -- keyword:<kw>, a marker in a file no item claims -- belong to no section and are counted out loud in the summary rather than dropped, because a scoped report that does not say what it skipped cannot be trusted for what it kept.

- **Code:**
  - `src/kbgraph/lint.py` → `Scope`
  - `src/kbgraph/lint.py` *(marker)*
- **Consumes:** debt.attribution-is-the-who-field · lint.who-is-the-suppression-key
- **Constrained by:** lint.nothing-is-hidden-silently

### lint.severity-split
`guardrail` · `active`

An error means the graph asserts something checkably untrue and is the ONLY severity that moves the exit code on its own. A warning means the graph is true but not yet good: 64 of 104 migrated keys were over cap, and a gate failing on those dies in a day. Under --baseline a warning exits 1, but only by getting worse.

- **Code:**
  - `src/kbgraph/lint.py` → `RULES`
  - `src/kbgraph/lint.py` *(marker)*
- **Refines:** lint.rule-table-is-the-source
- **Constrained by:** cli.exit-codes
- **Refined by:** debt.ratchet-compares-per-rule · lint.marker-line-carries-ids-only · lint.suppression-needs-reason · lint.thresholds-not-gates

### lint.suppression-needs-reason
`guardrail` · `active`

Suppressing a lint finding MUST carry --reason, because a suppression without one is indistinguishable from giving up. The entry lives on the item when the finding's `who` is an item id and in the store's root `lint` key when it is not, and `kb suppress --list`, `kb stats` and every lint summary report the live count so escape hatches cannot quietly accumulate.

- **Code:** `src/kbgraph/write.py` → `cmd_suppress`
- **Refines:** lint.severity-split
- **Constrained by:** store.lint-config-key
- **Refined by:** lint.who-is-the-suppression-key · write.cap-refused-at-the-write · write.suppression-records-its-basis

### lint.thresholds-not-gates
`design-choice` · `active`

No rule silences itself for want of adoption. What remains is thresholds: a number the rule measures against, defaulted in model.THRESHOLDS, stored per repo and overridable per run, with the flag beating the store beating the default. Resolved once per run onto the Result, so the rules, the summary and the baseline read the same numbers by construction.

- **Code:**
  - `src/kbgraph/lint.py` → `thresholds`
  - `src/kbgraph/lint.py` *(marker)*
- **Consumes:** store.thresholds-are-per-repo
- **Refines:** lint.severity-split
- **Constrained by:** lint.nothing-is-hidden-silently
- **Refined by:** lint.edge-kinds-are-judged-per-section

### lint.tree-scan
`behavior` · `active`

The marker pass walks the whole tree rather than only the paths items name, which is what lets marker-unknown and the relocation hint exist at all: both are questions about markers the store does not know where to look for. It skips vendored and generated directories and early-outs on files with no kb: in them.

- **Code:**
  - `src/kbgraph/lint.py` → `_scan_tree`
  - `src/kbgraph/lint.py` *(marker)*
- **Consumes:** markers.stack-pairing
- **Constrained by:** markers.own-line-and-own-language
- **Consumed by:** lint.fix-is-store-only
- **Refined by:** lint.no-files-mode

### lint.two-rules-cannot-be-suppressed
`edge-case` · `active`

Two rules kb suppress refuses by name. suppress-stale reports exemptions that no longer resolve, so an entry silencing it would hide the thing it exists to surface and would itself be invisible to the rule that would have found it. code-drift has no basis it could be granted against -- the only honest one is the ref's content, which is what re-fires it -- so its exemption would lapse on the next edit; the refusal names kb verify instead.

- **Code:** `src/kbgraph/lint.py` *(marker)*
- **Refines:** lint.basis-mismatch-suspends
- **Consumed by:** lint.json-carries-the-remedy

### lint.who-is-the-suppression-key
`guardrail` · `active`

Every finding's `who` MUST be a stable identifier of its subject: an item id, a [section], a [prefix], a keyword:<kw>, or a path. It MUST NOT carry a line number or a sentinel standing for the whole store, because `who` is the suppression key and an exemption keyed on something that moves stops matching without saying so. File positions belong in the message.

- **Code:**
  - `src/kbgraph/lint.py` → `_file_rules`
  - `src/kbgraph/lint.py` *(marker)*
- **Constrains:** debt.attribution-is-the-who-field
- **Refines:** lint.suppression-needs-reason
- **Consumed by:** lint.json-carries-the-remedy · lint.scope-reports-what-it-skipped
- **Referenced by:** lint.marker-refs-are-the-exact-form

---

## [debt]

**Related:** [lint] (4)

### debt.attribution-is-the-who-field
`concept` · `active`

A finding belongs to an item when its `who` IS an item id, and that is the whole test. Several rules point at something that is not an item -- section-min-items at [section], subsection-min-items at [prefix], keyword-broad at keyword:<kw>, marker-jsx at a path. A per-item view cannot attribute those and inventing an owner for them is where a report starts lying, so they go in their own trailing block. marker-unpaired reports at the id its marker names, so it lands under that item when the item exists.

- **Code:** `src/kbgraph/debt.py` *(marker)*
- **Consumes:** lint.rule-table-is-the-source
- **Constrained by:** lint.who-is-the-suppression-key
- **Consumed by:** lint.scope-reports-what-it-skipped

### debt.baseline-belongs-in-git
`guardrail` · `active`

The .kb-baseline.json written by kb baseline write MUST be committed. It is the ratchet's only reference, so an unstaged one means a fresh clone compares against nothing and every regression passes silently.

- **Code:** `src/kbgraph/debt.py` → `cmd_baseline`
- **Constrains:** debt.ratchet-compares-per-rule
- **Affected by:** —

### debt.excess-is-the-metric
`concept` · `active`

Total over-cap excess, not the warning count, is what the hygiene mechanism is graded on: splitting one 17,950-char item into eight raises key-length by seven while cutting the debt by a third. halve_to is max(cap, len//2), so a 500-char key with a 320 cap halves to 320 rather than to 250.

- **Code:** `src/kbgraph/model.py` *(marker)*
- **Refines:** debt.ratchet-compares-per-rule
- **Affected by:** —

### debt.ratchet-compares-per-rule
`guardrail` · `active`

kb lint --baseline MUST compare per rule and against total excess, NEVER a single sum of warnings: a split raises key-length while paying the debt down, and only tracking both numbers lets that pass.

- **Code:** `src/kbgraph/debt.py` *(marker)*
- **Refines:** lint.severity-split
- **Constrained by:** debt.baseline-belongs-in-git
- **Refined by:** debt.excess-is-the-metric · debt.three-rises-that-are-not-decay

### debt.three-rises-that-are-not-decay
`behavior` · `active`

Three rises exit 0 and print the kb baseline write that records the new floor: split (key-length up while excess fell), threshold-moved (a rule measuring against a different number because someone ran kb thresholds) and new-rule (a rule the recorded baseline predates). A fourth line, rule-gone, reports a rule the baseline has and this build does not -- renamed or split -- so a disappearance is never read as an improvement.

- **Code:** `src/kbgraph/debt.py` → `compare`
- **Refines:** debt.ratchet-compares-per-rule
- **Affected by:** —

---

## [markers]

**Key knowledge:**
- `markers.jsx-comment-form` — In JSX children use {/* … */}: a // marker renders as page text.
- `markers.own-line-and-own-language` — A marker counts only on its own line, in that file comment form.
- `markers.pair-syntax` — kb:begin <id> / kb:end <id> in a comment, id repeated on both.

**Related:** [lint] (4) · [invariants] (3) · [model] (1)

### markers.fmt-touches-markers-and-blanks-only
`guardrail` · `active`

kb code fmt tightens by SWAPPING a marker with the blank beside it, not deleting it, so line count and layout survive; only collapse removes a line. It MUST re-read its output, confirm non-marker lines and region coverage are unchanged, and MUST leave a file alone -- with the reason -- when it cannot.

- **Code:**
  - `src/kbgraph/markers.py` → `fmt_text`
  - `src/kbgraph/markers.py` *(marker)*
- **Refines:** invariants.never-writes-source · markers.pair-syntax
- **Affected by:** —

### markers.hand-placed-with-a-printed-pair
`behavior` · `active`

An unmarked ref advertises itself in kb code and kb ctx --code by printing the exact begin/end lines to paste, in that file's comment form. No command places a marker and none deliberately will: kb code fmt only tidies pairs a human already placed.

- **Code:**
  - `src/kbgraph/markers.py` → `pair`
  - `src/kbgraph/markers.py` *(marker)*
- **Refines:** markers.pair-syntax
- **Constrained by:** invariants.never-writes-source

### markers.jsx-comment-form
`guardrail` · `active`

A // marker inside JSX children renders as visible text on the page with no compile error -- the one silent failure mode in the scheme. In .tsx the brace-comment form MUST be used inside JSX, lint has a dedicated error for it, and the pair helper always suggests the safe form for a .tsx file.

- **Code:**
  - `src/kbgraph/markers.py` → `jsx_risk`
  - `src/kbgraph/markers.py` *(marker)*
- **Refines:** markers.pair-syntax
- **Referenced by:** markers.own-line-and-own-language

### markers.merge-on-intersection-only
`guardrail` · `active`

Two regions MUST merge only when their line ranges actually intersect. Regions a few lines apart are apart for a reason, and merging them would invent a contiguity that is not in the source and hide the gap; a merged block credits every contributing item in its header.

- **Code:**
  - `src/kbgraph/markers.py` → `merge`
  - `src/kbgraph/markers.py` *(marker)*
- **Refines:** markers.stack-pairing
- **Refined by:** markers.one-region-printed-once

### markers.one-region-printed-once
`guardrail` · `active`

A range two items both mark MUST be read and printed EXACTLY once, with every contributor named in its header. Per-item merging cannot do that, so kb ctx --code and multi-id kb code share one merged plan; later contributors get a cross-reference.

- **Code:** `src/kbgraph/markers.py` *(marker)*
- **Refines:** markers.merge-on-intersection-only
- **Affected by:** —

### markers.only-kind-with-snippets
`limitation` · `active`

ONLY a marker ref yields a snippet. Extracting one from a bare symbol needs real TypeScript parsing: brace matching fails silently on braces inside strings, template literals and regex literals, and a truncated snippet is worse than none.

- **Code:** `src/kbgraph/markers.py` → `snippet`
- **Relates to:** markers.pair-syntax
- **Constrained by:** invariants.never-writes-source
- **Refined by:** lint.marker-refs-are-the-exact-form

### markers.own-line-and-own-language
`guardrail` · `active`

A marker counts ONLY when it opens its own line and uses the comment form of that file extension. Both halves are load-bearing: they are what make a quoted marker in a test fixture and a fenced // example in a markdown file inert, so a repo that documents this scheme does not report its own examples as broken markers.

- **Code:**
  - `src/kbgraph/markers.py` *(marker)*
  - `src/kbgraph/markers.py` → `openers_for`
- **Constrains:** lint.tree-scan
- **Refines:** markers.pair-syntax
- **Relates to:** markers.jsx-comment-form
- **Affected by:** —

### markers.pair-syntax
`concept` · `active`

A marker is a kb:begin and kb:end pair inside comments, each naming one or more ids, every id repeated on the end so pairing never depends on order or nesting. A line MUST carry ids and nothing else: trailing text is marker-syntax, not prose. One comment form per extension: // /* */ # <!-- -->.

- **Code:** `src/kbgraph/markers.py` → `MARK`
- **Affects:** —
- **Constrained by:** model.no-line-numbers
- **Refined by:** lint.marker-line-carries-ids-only · markers.fmt-touches-markers-and-blanks-only · markers.hand-placed-with-a-printed-pair · markers.jsx-comment-form · markers.own-line-and-own-language
- **Referenced by:** markers.only-kind-with-snippets

### markers.stack-pairing
`behavior` · `active`

Pairing is a stack keyed by item id, so regions from different items may overlap and nest freely, identified by order of appearance at read time. Every id on a multi-id line gets its own push, equivalent to that many stacked lines, so kb:end can close one alone. Instance numbers are never assigned or stored.

- **Code:**
  - `src/kbgraph/markers.py` → `scan_text`
  - `src/kbgraph/markers.py` *(marker)*
- **Affects:** —
- **Consumed by:** lint.tree-scan
- **Refined by:** markers.merge-on-intersection-only

---

## [search]

**Key knowledge:**
- `search.idf-bag` — An IDF-weighted term bag per item, computed per call.

**Related:** [ctx] (1) · [migrate] (1) · [read] (1)

### search.code-dir-bias
`behavior` · `active`

Passing --code multiplies the score of items anchored in the same directory by 1.6, on the observation that the rules governing a file are usually recorded against its neighbours.

- **Code:**
  - `src/kbgraph/search.py` → `score`
  - `src/kbgraph/search.py` *(marker)*
- **Refines:** search.idf-bag
- **Affected by:** —

### search.find-is-literal
`concept` · `active`

find is a literal regex match over chosen fields and answers where does this string appear. dup, relate and ctx --query score by relevance and answer what is about this. Looking for a near-duplicate with find is the mistake the two-command split exists to prevent, because a restatement in different words matches nothing.

- **Code:** `src/kbgraph/read.py` → `cmd_find`
- **Refines:** search.idf-bag
- **Affected by:** —

### search.idf-bag
`concept` · `active`

Relevance is an IDF-weighted bag of terms per item, drawn from its key at weight 1, its keywords at 3, its code basenames and symbols at 2 and its own name at 1.5. Identifiers are split on camel-case boundaries so authFetch also matches auth and fetch.

- **Code:**
  - `src/kbgraph/search.py` → `item_terms`
  - `src/kbgraph/search.py` *(marker)*
- **Affects:** —
- **Consumed by:** ctx.mandatory-first-step · read.kw-suggest-writes-nothing
- **Refined by:** search.code-dir-bias · search.find-is-literal
- **Referenced by:** migrate.bullet-map-is-explicit · search.no-index-on-disk

### search.no-index-on-disk
`design-choice` · `active`

Scores are computed on every call and no index is ever written. At this scale the whole corpus is a few hundred short strings, so the cost is unmeasurable next to a stale index nobody remembers to rebuild.

- **Code:** `src/kbgraph/search.py` → `build_idf`
- **Relates to:** search.idf-bag
- **Affected by:** —

---

## [cli]

**Key knowledge:**
- `cli.global-flags-work-on-both-sides` — --kb, --root and --dry-run parse before or after the command.
- `cli.utf8-lf-console` — stdout is forced to UTF-8 and LF on every platform.

**Related:** [invariants] (3) · [testing] (3) · [read] (2) · [write] (2) · [directive] (1) · [lint] (1) · [store] (1)

### cli.argparse-subaction-column
`edge-case` · `active`

argparse measures subcommand names at their group indent but prints them two columns deeper, so the longest always wrap. Raising max_help_position cannot help, because the column is a min of the two; the formatter re-measures the choices.

- **Code:** `src/kbgraph/cli.py` → `Fmt`
- **Refines:** cli.help-is-the-documentation
- **Affected by:** —

### cli.exit-codes
`contract` · `active`

Exit 0 means the command succeeded. Exit 1 means a check failed and the graph or the tree is at fault: lint errors, render drift, no path between two items. Exit 2 means the invocation was wrong or a mutation was refused before writing anything. Scripts and hooks may rely on the split.

- **Code:** `src/kbgraph/cli.py` → `main`
- **Constrains:** lint.severity-split · read.path-exits-1-when-disconnected
- **Referenced by:** testing.drives-the-cli

### cli.global-flags-work-on-both-sides
`guardrail` · `active`

The global flags are attached to the top-level parser AND every subparser, so --dry-run works on either side of the command name. They MUST carry default=SUPPRESS, or a subparser copy overwrites what the top-level parser set and a dry run becomes a real write.

- **Code:**
  - `src/kbgraph/cli.py` → `global_flags`
  - `src/kbgraph/cli.py` *(marker)*
- **Affects:** —
- **Referenced by:** write.destructive-needs-yes

### cli.help-is-the-documentation
`guardrail` · `active`

Every subcommand MUST carry a description, and every argument a help string; the commands worth an example carry a worked one in the epilog. A flag whose only documentation is its name is undocumented, and the CLI is the first place anyone looks.

- **Code:** `src/kbgraph/cli.py` → `build_parser`
- **Refines:** invariants.total-command-surface
- **Constrained by:** invariants.sole-interface
- **Refined by:** cli.argparse-subaction-column
- **Referenced by:** directive.documented-examples-are-executed

### cli.no-store-commands
`behavior` · `active`

init and migrate run before the store is loaded, because producing a store is what they are for. They are marked needs_store=False and dispatched ahead of the load step; every other command receives an already-validated graph.

- **Code:** `src/kbgraph/cli.py` → `cmd_init`
- **Affects:** —
- **Constrained by:** invariants.total-command-surface
- **Referenced by:** store.upward-discovery

### cli.utf8-lf-console
`guardrail` · `active`

stdout and stderr MUST be reconfigured to UTF-8 with LF newlines at startup. Output carries middots and arrows, which on a cp1252 console is an UnicodeEncodeError rather than mojibake; and Windows text-mode translation would put a carriage return in every piped id.

- **Code:**
  - `src/kbgraph/cli.py` → `_utf8_console`
  - `src/kbgraph/cli.py` *(marker)*
- **Constrains:** read.impact-ids-only-is-bare
- **Consumed by:** testing.raw-bytes-variant
- **Refined by:** write.stdin-is-decoded-utf8

### cli.version-is-declared-once
`guardrail` · `active`

The version MUST be written once, in kbgraph.__version__, and read from there by pyproject.toml's dynamic version. Stated in both, they drift silently: pyproject still said 2.2.0 after the 3.0.0 bump, so a built distribution carried a version kb --version contradicted.

- **Code:**
  - `src/kbgraph/__init__.py`
  - `pyproject.toml`
- **Constrains:** testing.suite-layout
- **Affected by:** —

---

## [migrate]

**Key knowledge:**
- `migrate.dates-from-git` — Item dates are replayed from git history, not stamped with today.

**Related:** [model] (1) · [render] (1) · [search] (1) · [store] (1) · [write] (1)

### migrate.bullet-map-is-explicit
`design-choice` · `active`

The v1 section-header bullets are attached to the items they restate by an explicit hand-checked map, not by matching. IDF matching was tried and rejected: it put the token-location bullet on the SSR guard and swapped two testing items, and a section header quietly attached to the wrong item is exactly the drift the schema exists to remove.

- **Code:** `src/kbgraph/migrate.py` → `BULLET_MAP`
- **Relates to:** search.idf-bag
- **Affected by:** —

### migrate.dates-from-git
`behavior` · `active`

created and modified are recovered by replaying the markdown files git history, one show per commit that touched it, which beat one log -S per item by an order of magnitude. verified is seeded from modified rather than reset to the migration date, so the staleness lint means something on day one.

- **Code:** `src/kbgraph/migrate.py` → `cmd_migrate`
- **Relates to:** write.verified-is-not-modified
- **Affected by:** —

### migrate.edges-unioned
`behavior` · `active`

v1 stored both directions and they had drifted apart, so migration takes the union: outbound becomes Affects plus the inverse of every Affected by. Trusting Affects alone would have silently deleted around forty one-sided edges.

- **Code:** `src/kbgraph/migrate.py` → `build`
- **Consumes:** model.derived-inverse
- **Affected by:** —

### migrate.one-way-only
`guardrail` · `active`

Migration MUST be one-way. Nothing turns a v2 store back into v1 markdown and nothing downgrades a schema, but kb migrate --to N raises an existing store forward -- from schema 2 or 3 to 4, in one stamp either way, since every field both schemas added is optional. kb 4.x carries no code that reads two formats at once: an older store is refused at load with the upgrade command named, and --to N naming anything but the build's own schema is refused rather than silently upgraded further.

- **Code:** `src/kbgraph/migrate.py` → `cmd_migrate`
- **Affects:** —
- **Refined by:** render.legacy-guard · store.schema-1-hard-fail

---

## [testing]

**Key knowledge:**
- `testing.pytest-runner` — pytest is the runner; the package itself still has zero dependencies.

**Related:** [cli] (3) · [directive] (2)

### testing.docs-checker-flag-shapes
`edge-case` · `active`

_flags classifies a flag by nargs; nargs='?' means the value is OPTIONAL. Counting it as value-taking reported the documented bare `kb lint --baseline` as missing an argument -- the checker was wrong, not the docs.

- **Code:** `tests/test_docs.py` → `_flags`
- **Relates to:** directive.documented-examples-are-executed
- **Affected by:** —

### testing.drives-the-cli
`design-choice` · `active`

Almost every test shells out to the CLI and asserts on its stdout, because the CLI surface is the contract the model depends on. A refactor behind it is free; a changed message that a doc quotes is a test failure, which is the point.

- **Code:** `tests/test_v2.py` → `run`
- **Relates to:** cli.exit-codes
- **Referenced by:** directive.documented-examples-are-executed · testing.pytest-runner

### testing.pytest-runner
`concept` · `active`

The suites are ordinary pytest modules -- plain assert, no custom runner -- so `pytest` is the whole command. ok() and has() survive only to label a check with the sentence it is testing. The package keeps zero runtime dependencies; pytest is the single dev one, from pip install -e '.[test]'.

- **Code:** `tests/conftest.py`
- **Relates to:** testing.drives-the-cli
- **Referenced by:** testing.suite-layout

### testing.raw-bytes-variant
`behavior` · `active`

The default harness captures output in text mode, which normalises newlines and would hide exactly the carriage-return bug the console configuration exists to prevent. A raw variant captures bytes for the tests whose subject is the bytes.

- **Code:** `tests/test_v2.py` → `run_raw`
- **Consumes:** cli.utf8-lf-console
- **Affected by:** —

### testing.suite-layout
`concept` · `active`

Two collected files: test_v2.py covers src/kbgraph end to end by shelling out to the CLI, and test_docs.py checks every documented invocation against the live parser. Both ship in a distribution, so a released artifact can be verified in place.

- **Code:** `tests/test_v2.py`
- **Relates to:** testing.pytest-runner
- **Constrained by:** cli.version-is-declared-once
