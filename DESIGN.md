# kb-graph design

The schema in full, and the reasoning behind the choices that a cold reader
would otherwise relitigate. `README.md` is the usage reference; this is why the
thing is shaped the way it is.

JSON is the store. `kb` is the only access path for the model — read *and*
write. Markdown is a rendered artifact for the human.

## Invariants

1. **`kb` is the model's sole interface to the graph.** The model never reads
   `knowledgebase.json` and never greps `knowledgebase.md`. Every question is a
   command. This is what pays for the JSON store: the engine can assemble exactly
   the right context in one call, instead of the model guessing slice offsets.
2. **Markdown is for the human.** Rendered one-way from JSON, for reading only.
   Because the model no longer reads it, the render is free to optimize for human
   comprehension rather than for grep-ability.
3. **No md parser exists.** Hand-edits to the md are translated into `kb` commands
   by a model reading `git diff knowledgebase.md`, never absorbed automatically.
4. **The command surface is total**, in both directions. Every writable field has
   exactly one command; every question answerable from the graph has a command that
   answers it. A field editable only by hand-editing JSON is a bug, and so is a
   question that can only be answered by reading a file.
5. **Derive everything derivable.** Stored data has no redundancy, so it cannot
   self-contradict. See *Derived fields*.
6. **Both files are committed.** `kb render --check` in pre-commit fails if
   `render(json) != md` — catches stale renders and hand-edits in one check.
7. **`kb` never writes source files.** The tool is read-only against the tree.
   Markers are placed by hand; lint reports on them but never repairs them in
   place. `kb lint --fix` writes `knowledgebase.json` only.
8. **A write `kb` accepts is a load `kb` accepts.** `commit()` validates before
   it serialises and `load()` validates what it read, both through the same pure
   `validate()`, so a rejected edit leaves the file untouched and the only
   unreadable store is one `kb` did not write.

   Two consequences bind every later change:

   1. `validate` may enforce exactly the invariants every mutation command
      maintains — no more. `kb code rm` renumbers the suppressions indexing into
      the ref list and `kb section rm` cascades the ones naming the section, so
      both are checkable. A rule name and a threshold name are not: a release may
      rename either, and a store carrying the old spelling must still open. Those
      are lint findings (`suppress-stale`), never schema errors.
   2. **Narrowing `validate` is a schema bump with a migration that repairs it**,
      never a minor and never a patch. There is no `--no-validate` outside
      `migrate.upgrade`, so a check tightened in a point release would leave
      existing stores unopenable by the only tool that could fix them.

   External damage — a hand-edit, a merge resolved badly — is out of scope by
   design. `kb` does not defend against a store it did not write; it guarantees
   only that it never produces one.

The tradeoff being accepted: `kb` is a hard dependency for working in a repo that
uses it. If it breaks, the model is blind until it is fixed. The md render is a
fallback in principle, but it is no longer designed to be one.

## Access model

Granular read commands (`item`, `tree`, `impact`, `path`, `find`, `hubs`, `orphans`,
`stats`) are the precise layer. Above them sits one command that assembles a
complete working context in a single call:

```
kb ctx <id>              --depth 2 --code    # a rule, its neighborhood, its code
kb ctx --path <file>     --depth 1 --code    # everything governing a file
kb ctx --query "…"       --depth 1           # relevance-ranked entry
```

`kb ctx --path` is the mandatory first step before touching any file, replacing
the `grep '- Code: .*<filename>'` ritual that the markdown format required.

## Root

```json
{
  "schema": 4,
  "title": "kb-graph",
  "run": "001",
  "glossary": { "types": {}, "statuses": {}, "edge_kinds": {} },
  "sections": {},
  "items": {},
  "lint": {
    "thresholds": { "edge_kind_share": 0.9 },
    "ignore": [
      {"who": "[debt]", "rule": "section-min-items",
       "reason": "three items, and it will not grow"}
    ],
    "legacy": [
      {"name": "api", "kind": "section", "renamed_to": "backend",
       "since": "2026-08-02"}
    ]
  }
}
```

`lint` is optional and root-level: `thresholds` overrides the built-in default
for a rule (`kb thresholds set NAME VALUE`), and `ignore` holds every
suppression whose `who` is not an item id -- a `[section]`, a `[prefix]`, a
`keyword:<kw>`, or a path. Item-owned suppressions stay on the item, in its own
`lint.ignore` (below); the two lists share a shape but not a home, because a
`[section]` has no item to carry it.

`legacy` holds one entry per renamed-away item, section or subsection that
some other item's key still cites, written by `kb rename` / `kb move` /
`kb section rename` / `kb subsection rename` and never by hand. An entry
clears itself once nothing cites `name` any more, or `kb legacy rm NAME`
retires it regardless. See "Retired names" in the decision log.

`run` is the ambient run id stamped onto `meta.run` of every item a mutation
touches. Set with `kb run <NNN>` at the start of a run; `kb title` sets the
heading. Both exist because a root field with no command would violate
invariant 4.

`kb init` writes this file with a seeded glossary. A store with an empty
glossary accepts no items at all — `--type` must name a known type — so "empty"
would have meant "unusable until eight glossary commands have been run".
`--bare` is there for the case where you genuinely want to define a vocabulary
from nothing.

### What the store does not hold

The store holds graph data and nothing else. Every prose section in the v1 file
dispositions to exactly one of three homes — schema, lint, or the consuming
repo's CLAUDE.md — and none survives as prose inside the KB.

| v1 §maintenance rule | disposition |
|---|---|
| Edge symmetry invariant | **deleted** — only one direction is stored |
| Labeled edges (deferred) | **deleted** — edges are objects, labeling is free |
| Code-ref format | **schema** — typed refs; "no line numbers" is structural |
| Writing style (registers, length) | **glossary** — `key_max`, `requires_modal` per type |
| Section lifecycle (≥3 items) | **lint** — `section-min-items` |
| Reassessment protocol | **lint** (`verified` age) + **CLAUDE.md** (the pass itself) |
| Item lifecycle (create/merge/split/delete) | **CLAUDE.md** |
| Code-comment boundary | **CLAUDE.md** |
| `/analyze` + `/implement` interaction | **CLAUDE.md** |

`§user-directives` stays in the graph as items with `type: directive` — they have
ids and other items reference them. `§index` is derived.

## Item

Keyed by id in `items`, sorted alphabetically by full id. Since ids are
`<section>.<name>`, that groups them by section for free; render order is sections
by their `order`, items alphabetically within. Nothing stores position, so there is
no reordering command and a new item always lands in a deterministic spot.

```json
"auth.single-fetch-path": {
  "section": "auth",
  "type": "guardrail",
  "status": "active",
  "key": "Every authenticated HTTP request MUST go through `authFetch`. Raw `fetch` is permitted ONLY at the sites listed in `auth.unauth-exceptions`.",
  "summary": "All authed HTTP goes through `authFetch`.",
  "highlight": true,
  "keywords": ["authfetch", "http"],
  "code": [
    {"kind": "marker", "path": "src/lib/api.ts"},
    {"kind": "symbol", "path": "src/lib/api.ts", "symbol": "authFetch", "part": "401 redirect branch"}
  ],
  "affects": [
    {"to": "polling.swr-fetcher", "kind": "constrains"},
    {"to": "downloads.authed-download", "kind": "constrains"}
  ],
  "meta": {"created": "2026-03-14", "modified": "2026-07-21", "verified": "2026-07-21", "run": "032"},
  "lint": {"ignore": [{"rule": "key-length", "reason": "one contract with 4 enumerated exceptions; splitting loses the 'these and only these' framing", "basis": "sha256:9f3a1c2b7d4e5f60"}]}
}
```

A per-ref suppression carries `ref` too, the 0-based index into `code`:
`{"rule": "code-path", "ref": 1, "reason": "generated, absent in CI", "basis": "sha256:..."}`.

| field | required | notes |
|---|---|---|
| `section` | yes | must exist in `sections`; id prefix must match |
| `type` | yes | key in `glossary.types` |
| `status` | yes | `active` \| `todo` \| `deprecated` |
| `key` | yes | the rule. Single statement. Length capped per type. |
| `summary` | no | compressed `key` for section-header rendering; falls back to `key` |
| `highlight` | no | surface this item's summary in the section header |
| `keywords` | no | lowercase terms, ≤3 words each |
| `code` | per type | ordered list of typed refs; ≥1 unless the type sets `requires_code: false` |
| `affects` | no | outbound edges only; inverse is derived |
| `meta` | yes | `created` / `modified` / `verified` / `run`, all dates `YYYY-MM-DD` |
| `lint.ignore` | no | per-rule suppression, `reason` mandatory, `ref` for a per-ref rule -- suspended when its `basis` moves |
| `lint` (root) | no | store-wide config: `thresholds` (per-rule overrides) and `ignore` (suppressions whose `who` is not an item id) |

An item field outside this list is rejected at load, so a typo cannot survive a
round-trip by being quietly carried through the writer.

### Code refs

```json
{"kind": "marker", "path": "src/lib/api.ts"}
{"kind": "symbol", "path": "src/lib/api.ts", "symbol": "authFetch", "part": "401 redirect branch"}
{"kind": "file",   "path": "src/lib/api.ts"}
```

- `marker` — a `kb:begin`/`kb:end` pair in that file. The **only** kind that yields
  snippets. `path` is stored for fast lookup and verified by lint; `kb lint --fix`
  relocates it if the marker is found elsewhere.
- `symbol` — grep target, no snippet. Robust snippet extraction from a bare symbol
  requires real TS parsing; deliberately out of scope. `kb code <id>` prints the
  ref plus the exact pair to place by hand, so the gap advertises itself where it
  actually matters.
- `file` — whole-file relevance, no snippet.

Never store line numbers. Derive them on every read.

### Edges

Stored on the source only, as `{"to": ..., "kind": ...}`, one object per line.
Inverse lists are computed at render time using the glossary's inverse label.

Default kind is `relates` — migration does not label 400 edges by hand. `kb lint`
reports label coverage so it trends upward.

## Section

```json
"auth": {"order": 40, "label": "auth"}
```

That is the whole thing. `Key knowledge`, `Items`, and `Related` are all derived.

## Glossary

Data-driven, so adding a type or edge kind needs no code change. What `kb init`
seeds:

```json
"types": {
  "guardrail":    {"desc": "rule or contract whose violation breaks behavior", "key_max": 320, "requires_modal": true},
  "contract":     {"desc": "shape or protocol co-owned with another system", "key_max": 400},
  "concept":      {"desc": "definition or invariant of the area", "key_max": 400},
  "behavior":     {"desc": "what the main path actually does", "key_max": 320},
  "edge-case":    {"desc": "what happens at a boundary or exceptional path", "key_max": 240},
  "limitation":   {"desc": "known gap, deliberately unfixed", "key_max": 240},
  "design-choice":{"desc": "a road taken, with the road not taken", "key_max": 400},
  "directive":    {"desc": "user-owned top-level priority; overrides every other item", "key_max": 1200, "requires_code": false, "requires_edges": false}
},
"statuses": {"active": {}, "todo": {}, "deprecated": {}},
"edge_kinds": {
  "constrains": {"label": "Constrains", "inverse": "Constrained by"},
  "calls":      {"label": "Calls",      "inverse": "Called by"},
  "consumes":   {"label": "Consumes",   "inverse": "Consumed by"},
  "refines":    {"label": "Refines",    "inverse": "Refined by"},
  "relates":    {"label": "Relates to", "inverse": "Referenced by"}
}
```

Per-type flags, each of which some command or rule reads:

| flag | default | effect |
|---|---|---|
| `key_max` | none | `key-length` warns past it |
| `requires_modal` | `false` | the key must contain MUST / NEVER / ONLY / EXACTLY, and `kb guardrails` lists the type |
| `requires_code` | `true` | `false` lets an item have no code ref |
| `requires_edges` | `true` | `false` stops `orphan` warning about the type |

`requires_code` and `requires_edges` were both added on contact with the data,
for the same reason: a directive has no code and no edges, because it governs
the whole graph rather than a neighbourhood of it. A permanent 6-of-6 warning is
noise, and noise is how a warning tier dies.

## Markers

```
// kb:begin auth.single-fetch-path
…
// kb:end auth.single-fetch-path
```

One comment form per extension: `//` for `.ts .tsx .js .jsx .mjs`, `/* */` for
`.css .scss`, `#` for `.py .sh .yml .yaml .toml`, `<!-- -->` for `.md .html`.
`.tsx` additionally accepts `{/* … */}`, which is **required** inside JSX
children — a `//` marker there renders as visible text on the page with no
compile error, the one silent failure mode in the scheme.

A marker counts **only when it opens its own line, in that file's own comment
form.** Both halves are load-bearing, and the rule was forced by building a KB
*about kb*: without it, `"// kb:begin auth.token"` inside a Python test fixture
and a fenced `// kb:begin …` example inside a markdown file both register as real
markers, and this repo reported 17 errors that were all its own documentation.
The alternatives considered were an in-source `kb:ignore-file` sentinel and a
persisted list of ignore globs; both are mechanisms for suppressing a
false positive that simply should not be produced. A trailing
`foo()  # kb:begin x` also stops counting, which is no loss: a marker is a
region delimiter and belongs on its own line.

A marker line may name more than one id — `kb:begin a.one a.two` — and each id
gets its own push onto the pairing stack, so the form is exactly equivalent to
that many stacked lines and `kb:end a.two` still closes only that one. It also
means the two regions come out with identical ranges, which `merge` already
folds into one block crediting both, so nothing downstream needed to learn about
the form.

The line carries ids and nothing else. Tolerating trailing prose was the older
behavior and it was wrong in the one direction that matters: a corrupted marker
still delimited a region, at whatever place the corruption left it.

Invariant 7 is therefore narrower than "kb never writes source": `kb code fmt
--write` does, in the one way that cannot change meaning — a marker line swaps
with a blank line, or several merge into one — and re-verifies that against its
own output before writing. It never inserts, deletes or relocates a region;
markers are still placed by hand.

- The id is repeated on `end`. Pairing is a stack keyed by id, so regions from
  different items may overlap and nest.
- Multiple regions per item are identified by order of appearance at read time.
  Never numbered, never stored.
- Retrieval merges two regions **only if their line ranges intersect**. Adjacent or
  nearby regions print as separate blocks with their own `path:start-end` headers,
  so a gap is always visible rather than papered over. Merged blocks list every
  contributing item id in the header.

## Derived fields

Never stored, computed at render:

| output | derivation |
|---|---|
| item `Affected by` | inverse of every `affects` edge, labeled with the kind's inverse |
| section `Items` | items whose `section` matches, in dict order |
| section `Key knowledge` | `summary \|\| key` of that section's `highlight: true` items, each tagged with its id |
| section `Related` | sections reachable by any edge crossing out of / into this one, rendered with edge counts: `[pages] (14) · [api-client] (9)` |
| `§index` | sections + their highlighted items |
| line numbers | resolved from markers on every read |

## Canonical writer

Diff quality is a requirement, not a nicety.

- Item keys sorted by id; fields within an item in a fixed canonical order.
- Leaf objects (edges, code refs, lint ignores) emitted **inline on one line**;
  only containers get indented. `json.dump(indent=2)` would triple the file's
  line count and make every edge a 4-line diff.
- 2-space indent, LF, trailing newline, UTF-8 no BOM. The md renderer preserves
  the existing file's byte conventions.

## Lint severity

Two levels, and only **error** moves the exit code. `README.md` has the rule
table; the reasoning is here.

**error** — the graph asserts something checkably untrue: `edge-target`,
`self-edge`, `edge-dup`, `code-path`, `marker-missing`, `marker-unpaired`,
`marker-unknown`, `marker-jsx`, `summary-longer`. Each is a fact rather than a
taste, and each is cheap to fix. This is the set a pre-commit hook may block on.

**warn** — the graph is true but not yet good: `key-length`, `key-modal`,
`orphan`, `section-min-items`, `subsection-min-items`, `keyword-case`,
`keyword-phrase`, `keyword-dup`, `keyword-missing`, `keyword-broad`,
`marker-span`, `marker-empty`, `verified-stale`, `edge-kind-missing`,
`stale-id-mention`, `code-drift`, `code-inexact`, `suppress-stale`. A backlog,
not a breakage.

The split is forced by the data. On the store this tool was built for, the error
set had **exactly one** member while the warning set had 72, 64 of them
`key-length`. A flat "any finding fails" would have been switched off within a
day, and the two ways out — mass suppression, or turning the hook off — were
both worse than a tier that sits there and trends down.

Two rules the schema originally named needed adjusting on contact:

- **`summary-stale` does not exist.** "key modified in a later run than summary"
  is unanswerable from the store — `meta` is per item, not per field. It is
  replaced by a write-time nudge: `kb set <id> key` prints the existing summary
  and suggests updating it, which is the moment the staleness is created.
- **`orphan` is per-type**, via `requires_edges`. See *Glossary*.

Rules deleted by the format change — duplicate ids, `Items` drift, edge
asymmetry, `Related` reciprocity, `§index` coverage — are impossible by
construction and are not reimplemented.

`hooks/pre-commit` is the gate: `validate` → `render --check` → `lint
--errors-only`, plus `lint --baseline` when `.kb-baseline.json` exists. The
fourth check is conditional because a repo that has never run `kb baseline
write` should keep exactly the three factual checks; the ratchet blocks on a
per-rule rise, never on the backlog's absolute count. It exits 0 when `kb` is
absent or the repo has no store, so it is safe to install unconditionally.

## Decision log

Settled, with the reasoning that is not recoverable from the schema alone. A cold
session should not relitigate these.

**Storage and access**
- JSON store, md render. The win is the *writer*, not the reader — v1's md parser is
  ~70 lines and fine; the `Editor` line-surgery around it is ~250 lines that exist
  only to put text back without disturbing bytes it doesn't own.
- The model reads through `kb` only; md is the human's copy. Chosen late, and it
  inverts the earlier premise that md had to stay grep-and-slice friendly.
- No md parser, in either direction. Hand-edits round-trip through a model
  translating the diff into commands, which is why invariant 4 demands totality.

**Graph**
- Only `affects` is stored; the inverse is derived. This alone deletes the
  "non-negotiable" edge-symmetry invariant, its lint, and the two-sided writes in
  `add_edge`/`drop_edge`.
- Edges get kinds with per-kind inverse labels. v1 deferred this as "opt-in per
  item, never file-wide" — a caution that only made sense because md made
  labeling expensive. Migration defaults every edge to `relates`; labeling is
  opportunistic, coverage is linted.
- Section `Key knowledge`, `Items`, and `Related` are all derived. The v1 Key
  knowledge lines were already verbatim restatements of items, i.e. a guaranteed
  drift source.
- `Related` renders with edge counts (`[pages] (14)`), so the noise threshold can be
  set by looking at real numbers.

**Items**
- `type` is required and drives per-type length caps — types without enforcement are
  decoration. The motivating failure was one `Key` carrying a contract, three
  exceptions, and two historical migration notes.
- `todo` is a **status**, not a type — it describes lifecycle, not kind.
- Length caps are escapable via `lint.ignore` with a **mandatory reason**, and the
  suppression count is reported so escape hatches can't quietly accumulate.
- `meta` keeps `created` / `modified` / `verified` / `run`. Commit id and message
  were dropped: messages get rewritten by rebase, and `git log -S'<id>'` recovers
  the commit exactly and for free. `verified` is distinct from `modified` and is
  what actually drives reassessment.
- Items sort alphabetically by id. No ordering command, no stored position.
- `highlight` is an explicit flag, not auto-derived from `type`.

**Code refs and markers**
- Snippets are **marker-only**. Extracting a snippet from a bare `kind: symbol` ref
  needs real TS parsing — regex + brace matching fails silently on braces in
  strings, template literals, and regex literals, and a silent partial snippet is
  worse than none. tree-sitter has pip-installable TS bindings if this ever matters.
- Unmarked items advertise the gap in `kb code` and `kb ctx --code`, so markers
  accumulate by demand rather than by a mass-marking decree.
- No marker instance ids. A stack keyed by item id pairs them correctly; instance
  numbers buy nothing at parse time and misnumber themselves on deletion.
- Regions merge **only on true range intersection**. Two regions a few lines apart
  are apart for a reason; merging them invents a contiguity that isn't there.
- Markers are placed by hand — `kb` is read-only against source (invariant 7).
  There is deliberately no `kb mark`; the tool prints the pair instead.

**CLI**
- The global flags (`--kb`, `--root`, `--dry-run`) are attached to every
  subparser as well as the root, because `kb set x key "…" --dry-run` is what
  anyone actually types and argparse rejects it otherwise. They carry
  `default=SUPPRESS` so a subparser copy cannot overwrite what the top-level
  parser set — without that, `kb --dry-run set …` silently becomes a real write,
  which is the worst possible failure for that flag.
- stdout is forced to UTF-8 **and LF**. The encoding half is for cp1252 consoles;
  the newline half is because Windows text-mode translation puts a `\r` in every
  piped id, which made `kb impact --ids-only | xargs kb verify` fail on every
  line while looking correct in a terminal.
- `--ids-only` emits ids and nothing else. It exists to be piped, and a header
  in that stream becomes an argument to the next command.
- Tests assert on CLI stdout rather than on internals, since that surface is the
  contract the model depends on. A changed message that the docs quote is
  therefore a test failure, which is the intent.
- `tests/test_docs.py` checks every documented invocation against the live
  parser and executes the read-only ones. Prose examples are not exempt from
  being true. `CHANGELOG.md` is checked with `README.md` and `DESIGN.md`: a
  command quoted in the entry that introduced it is exactly the drift this
  catches.

**Lint visibility and suppression**
- **The adoption gates are deleted.** `keyword-missing`, `keyword-broad` and
  `edge-kind-missing` used to stay silent until a store had taken the practice
  up, so a store that had adopted nothing was told nothing was wrong — the
  opposite of what a repo working through a backlog needs. The wall-of-warnings
  problem they were guarding against is answered instead by `--baseline`,
  per-cause suppression and scope selection, all of which postdate the gate.
- **Nothing is withheld silently.** A default run reports every finding it has;
  the two thresholds that can still hold one back (`edge_kind_share`,
  `keyword_broad_share`) are counted out loud in the summary, and `--all` turns
  them off. A scoped run says how many findings it skipped as another part of
  the store's. A report that is quiet for an unstated reason is worse than a
  long one.
- **`who` is a stable identifier, not a display string**, because `who` is the
  suppression key. A finding that reported at `path:line` would have its
  exemption expire on the next unrelated edit to that file, so file positions
  moved into the message and `who` names the thing the rule is actually about.
- **`keyword-hygiene` split into three rules.** One `kb suppress` should not
  silence three unrelated defects on one item.
- **A suppression is granted against the evidence it names**, recorded as a
  `basis` hash. Rewrite the key it was written about and it suspends rather than
  carrying over, because the exemption was an argument about a specific text.
  `suppress-stale` then names the entry with its reason, so re-confirming it is
  a decision rather than a default.
- **Two rules refuse suppression.** `suppress-stale` would hide exactly what it
  exists to surface. `code-drift`'s only possible basis is the ref's content,
  which is the thing whose change re-fires it — the exemption would lapse on the
  next edit, so `kb verify` is the honest resolution and the refusal names it.
- **`kb suppress` takes `--who`/`--rule`, not positionals.** A `who` can begin
  with `[` or `-`, and the caller this is built for is an agent holding one line
  of `kb lint --json` and no other context. Neither is served by an argument
  order you have to have been told.
- **Every index `kb` prints or accepts is 0-based**, and every index is printed.
  Code refs are the only entity addressed by position — edges go by target,
  keywords by term — so this is the whole of the convention rather than a
  per-command decision.
- **Baselines record thresholds, not gate state.** A rule measuring against a
  different number is not a regression, and a renamed rule must not read as an
  improvement — hence `threshold-moved` and `rule-gone`.

**Retired names (`lint.legacy`)**
- **A section and a subsection share one rename mechanism**, because both are
  just a name that member ids extend. `kb section rename` and `kb subsection
  rename` cascade through the same `_rename_prefix` — every item id and every
  nested subsection id under the old prefix is re-derived — rather than each
  re-deriving "who is under this prefix". An item has no such subtree, so it
  keeps its own simpler `rename()`.
- **`lint.legacy` exists because `stale-id-mention`'s known-section gate trades
  recall for precision.** A renamed-away section stops being a known section,
  so a citing mention would otherwise be unrecognizable forever, not just at
  rename time — the same trade the gate's file-extension guard makes, just
  discovered on the other side of a rename instead of at write time.
- **Only `kind: "section"` entries widen that gate.** An item or subsection
  retirement has no such gap — its own section stays live either way, so
  `stale-id-mention` finds it unaided. Those kinds are tracked purely so
  `kb legacy` is one place to see every retired name; lint needs no help from
  them.
- **An entry is written only when something cites it**, and **cleared the
  moment nothing does**, on whatever commit makes that true. Nothing to
  remember to run, and no permanent bookkeeping for a rename nobody will ever
  mention again.
- **Reoccupying a retired name reclaims its entry**, automatically, in
  whichever command makes the name live again. A live name and a legacy entry
  can never coexist — validation refuses that store, and worse, every current,
  valid reference to the reoccupied name would otherwise read as a stale
  mention of its own former self.
- **Every rename cascade checks every derived id for a collision before
  mutating anything.** A section's destination can never already hold items —
  `new in g.sections` is refused first, and a nonexistent section owns
  nothing — but a subsection's destination carries no such guarantee: an
  arbitrary prefix can already be a live, undeclared id nobody ever made a
  subsection over. Skipping this check let a subsection rename silently
  overwrite an unrelated item with no error and exit 0, caught in review
  rather than in the wild.

**Process**
- No prose in the store; the disposition table above accounts for every v1 rule.
- Fix inconsistencies on sight rather than logging them; `status: todo` is the
  release valve for anything too large to fix inline without wrecking scope.

## Settled questions

Do not relitigate.

1. **md render per item** — `key`, `type`/`status`, code refs, edges by kind.
   `meta` stays out of the page (reachable via `kb meta <id>`); `summary` is
   header-only, never repeated in the item block.
2. **`meta.created` backfill on migration** — recovered from git, not stamped.
   One `git show` per commit that touched the md replays the file's history and
   yields exact `created` *and* `modified` per item; that beat one `git log -S`
   per item by an order of magnitude. `verified` is seeded from `modified`, not
   reset to the migration date, so the staleness lint means something on day one.
3. **`type` assignment on migration** — model proposed all of them, user
   reviewed the diff, caps enforced afterwards. Corrections live in
   `migrate.TYPE_OVERRIDES`.
4. **Staging rule** — `knowledgebase.json` + `knowledgebase.md`, both
   auto-staged, never apart; no other `*.md`.
5. **Schema v1 KBs elsewhere** — hard-fail with a migrate hint. kb 4.x carries
   no dual-model code.
6. **`kb ctx` output format** — compact text, prototyped against real files
   before being fixed. Directives are always included (they override every item,
   so omitting them yields a context that can argue you into violating one).
   `--budget` degrades by priority — neighbour key text, then the neighbour
   list, then the neighbourhood entirely with a pointer to `kb impact` — but
   never drops the head, the roots, or the directives.

## Migration from v1

One-way and one-time; `kb migrate` is the only command exempt from the guard
that refuses to overwrite a schema-1 markdown file, since replacing the file it
just consumed is its job.

- v1's asymmetric edges are **unioned**, not dropped: outbound =
  `Affects` ∪ inverse of every `Affected by`. Dropping `Affected by` outright
  would have silently deleted ~40 one-sided edges.
- Section membership comes from the id prefix, which dissolves items that
  physically sat under the wrong heading.
- v1 `Key knowledge` bullets are mapped to the item each restates by an explicit
  `migrate.BULLET_MAP`. IDF matching was tried first and rejected: it attached
  the token-location bullet to the SSR guard and swapped two testing items. A
  section header quietly attached to the wrong item is exactly the drift this
  schema exists to remove.
