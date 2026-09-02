# Changelog

All notable changes to `kb-graph`. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[semver](https://semver.org/), where the **store schema is part of the public
API** — a schema bump is always a major.

Every `kb …` example below is checked against the live argument parser by
`tests/test_docs.py`, the same as `README.md` and `DESIGN.md`. A command that
disappears in a later version has to be edited out of the entry that introduced
it, or the suite fails.

## [4.3.0] — 2026-08-10

A CLI-native way to read the manual and the changelog once `kb` is installed,
instead of needing a checkout on hand.

### Added

- **`kb manual`** and **`kb changelog`** — print README.md and CHANGELOG.md.
  Both sourced from dist-info/METADATA rather than a shipped file: pyproject's
  dynamic `readme` key concatenates the two into the Description field for
  every wheel and sdist, one build step keeping both current with no second
  copy to drift. `kb manual` and `kb changelog` split that one field back
  apart on CHANGELOG's top heading, the only place `# Changelog` starts a
  line. Both fall back to reading the two root files directly in a checkout
  with no build metadata yet. `tests/test_packaging.py` checks both commands'
  output against the root files.

## [4.2.0] — 2026-08-10

Run 003. Three write-path defects — one of which had already damaged this
repo's own graph — plus marker lines that learn to carry more than one id and
the one command that tidies them.

### Added

- **A marker line may name several ids** — `kb:begin a.one a.two`, with the same
  list on the `kb:end`. Each id gets its own push onto the pairing stack, so the
  form is exactly equivalent to that many stacked single-id lines, and
  `kb:end a.two` alone still closes only that one.

- **`kb code fmt [PATH...]`** — tighten and collapse markers. A `kb:begin` moves
  down past blank lines just inside its region and a `kb:end` moves up past
  them, by *swapping* lines rather than deleting the blanks, so the file's
  blank-line layout and its line count survive; marker lines that end up
  adjacent then merge into one multi-id line.

  ```
  kb code fmt                  whole tree, diff only
  kb code fmt src/kbgraph      one directory
  ```

  It prints a diff and writes nothing until `--write`. This is the only command
  that writes source, and invariant 7 is narrowed to say so: before writing a
  file it re-reads its own output and confirms both that the sequence of
  non-blank, non-marker lines is unchanged and that every item still covers the
  same code lines, and leaves a file alone — with the reason — when it cannot.
  It never inserts, deletes or relocates a region.

  Marker digests cover the marker lines themselves, so a run moves them:
  `kb lint --rule code-drift` names the items to re-read afterwards.

- **`marker-syntax`**, an error: a comment line that opens with `kb:` must be
  `kb:begin` or `kb:end` followed by ids and a comment closer, with no id twice
  and nothing else on the line. Trailing prose used to be ignored, which let a
  corrupted marker delimit a region at whatever place the corruption left it.

- **`--suppress-length "<reason>"`** on `kb new` and `kb set` — the one override
  for the cap refusal below. It records a real `key-length` suppression with a
  basis over the stored key, so the exemption suspends if the key changes
  again. The reason is the flag's value rather than a separate `--reason`,
  which makes a suppression without one unspellable.

### Changed

- **`--code` and `kb code add` take exactly one ref and refuse trailing tokens.**
  They used to fold them into the ref's `part`, so `symbol <path> <sym> marker
  <path>` — the natural way to write two refs — silently became one ref carrying
  the other as a label, and no lint rule reads `part`, so nothing ever said so.
  A second ref is a second flag:

  ```
  kb new x.y -t behavior --key "…" --code symbol src/x.ts foo --code marker src/x.ts
  ```

  `part` remains readable and rendered; its only writer is `kb migrate`, which
  recovers it from the schema-1 `path - symbol - part` form.

- **A key past its type's cap is refused** by `kb new` and by `kb set <id> key`
  or `type`, rather than only warned about afterwards by `key-length`. Over the
  cap the advice is to trim; over 1.5× it is to split, since that size is
  usually two rules under one id.

### Fixed

- **`kb code <id>` and `kb ctx --code` no longer crash on an unpaired marker.**
  Both raised `ValueError: too many values to unpack (expected 3)` whenever a
  file holding one of the item's marker refs also contained an unclosed
  `kb:begin` — which took out `kb ctx --path`, the first step of the loop.
  `merged_plan` was accumulating two different tuple shapes into one list.

- **A dual-dispatch command no longer accepts another command's verb.**
  `kb kw clear x y` reached the keyword writer and fell out of its `if/elif`
  chain into the *remove* branch. `dispatch` now takes the verbs its own command
  implements, so an unimplemented verb goes to the read path and reports an
  unknown id.

- **`--file -` and `--key-file -` no longer corrupt non-ASCII input.** stdin was
  read through `sys.stdin`, whose encoding is the locale's — cp1252 on Windows,
  so a UTF-8 key piped in was stored as mojibake. It was silent because a cp1252
  read followed by a cp1252 write is byte-identity: the text round-tripped to the
  terminal and only the file was wrong. stdin is now decoded as UTF-8 explicitly,
  with newlines folded to LF, matching what `--file PATH` always did.

- **Four items had lost a code ref** to the `--code` bug above, and are repaired:
  `lint.thresholds-not-gates`, `lint.basis-mismatch-suspends`,
  `lint.edge-kinds-are-judged-per-section` and `lint.who-is-the-suppression-key`
  each carried a `symbol` ref whose `part` was the `marker` ref somebody meant to
  add. All four printed "no marker; place `kb:begin …` by hand" while the marker
  was already in the file.

- **The summary-staleness nudge only fires when the key actually moved.** It ran
  on every key write against an item with a summary, one-word fixes included. It
  now measures the edit as a share of the key's words and stays quiet under 20%.

## [4.1.0] — 2026-08-02

Run 002. A rename that leaves prose still citing the old name no longer
depends on someone remembering to go check: it is now tracked, self-clearing,
and covers every renamable id — item, section or subsection.

### Added

- **`kb legacy`** — every item, section or subsection name a rename retired
  that some other item's key still cites, with its kind, what it became, and
  how many mentions remain. `kb rename`, `kb move`, `kb section rename` and
  `kb subsection rename` all write an entry here, but only when something
  actually cites the old name.

  An entry clears itself the moment nothing cites it any more, on whatever
  write made that true — nothing to remember to run. `kb legacy rm NAME`
  retires one by hand instead; it refuses while mentions remain unless
  `--yes`. Reoccupying a retired name (a new item, section or subsection, or
  any rename's destination) reclaims the entry automatically.

  ```
  kb legacy
  kb legacy rm auth.token
  kb legacy rm model --yes
  ```

- **`kb subsection rename <old> <new>`.** Previously the only way to rename a
  subsection was renaming every member item by hand, one at a time. A
  subsection is the same kind of thing as a section — a name that member ids
  extend — so it now shares `kb section rename`'s mechanics: every member id,
  and every subsection nested under it, is re-derived and re-keyed; citing
  prose is named in full and tracked in `kb legacy` exactly as a section
  rename's is.

- **`stale-id-mention` finds a renamed-away section again.** Previously, once
  a section itself was renamed, its old id stopped being a "known section"
  and the rule silently lost the ability to ever find prose still citing it —
  not just at rename time, permanently. A `kind: "section"` entry in
  `lint.legacy` now keeps the old prefix recognised until every mention is
  gone.

- **Store schema**: one new optional field, `lint.legacy` — a list of
  `{name, kind, renamed_to, since}`. Omitted when empty, so a store that has
  never had a citing rename renders byte-identically to its 4.0.0 form. No
  migration needed.

### Fixed

- **`kb rename` and `kb section rename` printed only the first 6 items still
  citing an old name**, with no way to see the rest short of `kb lint`. Both
  now print the full list.
- **`kb section rename` crashed validation outright if the section had a
  subsection nested under it** — the subsection's own id was never
  re-prefixed, so it was left pointing at a section that no longer existed.
  Renaming a section or a subsection now correctly cascades to every
  subsection nested under it as well as every member item.
- **A subsection rename could silently overwrite an unrelated, pre-existing
  item** if the derived destination id happened to coincide with one — no
  error, no warning, exit 0. Every renaming cascade now checks every derived
  id against the current store before touching anything, and refuses cleanly
  if any would collide.

## [4.0.0] — 2026-07-31

Run 002. Lint stops holding findings back, and every finding becomes
suppressible with a reason that is scoped to the evidence it was granted
against.

**Upgrading from 3.x is one command**, as 2 → 3 was:

```
kb migrate --to 4
```

Every command refuses a schema-3 store until this runs, and names it in the
refusal. The one new store field is optional, so the upgrade stamps the version
and re-renders: a store that overrides no threshold and suppresses nothing
outside an item renders byte-identically to its 3.x form.

### Breaking

- **Store schema 3 → 4.** One optional root key, `lint`, holding
  `thresholds` and `ignore`. `kb migrate --to 4` upgrades in place, from
  schema 2 or 3. `--to N` naming anything but this build's schema is now
  refused rather than silently upgrading further.
- **The adoption gates are gone.** `keyword-missing`, `keyword-broad` and
  `edge-kind-missing` no longer stay silent until the store has taken the
  practice up. A store that had adopted nothing was told nothing was wrong,
  which is the opposite of what a repo working through its backlog needs. The
  wall-of-warnings problem is answered by `--baseline`, per-cause suppression
  and scope selection, all of which postdate the gate.
- **`keyword-hygiene` is now three rules** — `keyword-case`, `keyword-phrase`,
  `keyword-dup` — because one `kb suppress` should not silence three unrelated
  defects on one item. Existing suppressions of the old name are reported by
  the new `suppress-stale` rule, with their reasons, so they can be re-filed.
- **`edge-kind-missing` is judged per section**, against `edge_kind_share`
  (0.75). A section is the scope a team owns, and the number it is graded on
  has to be one it can move. It also names every bare edge instead of three
  and an ellipsis.
- **`who` is now a stable identifier** for every finding, because `who` is the
  suppression key: `keyword-broad` reports at `keyword:<kw>` rather than the
  sentinel `§keywords`; `marker-unpaired` and `marker-unknown` report at the id
  the marker names rather than at `path:line`; `marker-jsx` reports at its
  path. File positions moved into the message.
- **`markers.scan_text` returns 4-tuples** `(path, line, item_id, message)`.
- **Baselines record `thresholds`, not `gates`.** The `gate-opened` note is
  replaced by `threshold-moved`, and a new `rule-gone` note keeps a renamed
  rule from reading as an improvement.
- **`kb suppress` takes named arguments**: `--who WHO --rule RULE`, where the
  two positionals used to be. A `who` can begin with `[` or `-`, and the caller
  this command is built for is an agent holding one line of `kb lint --json`
  and no other context — neither is served by an argument order you have to
  have been told.
- **Every index `kb` prints or accepts is 0-based**, and every index is now
  printed. `kb code rm <id> <n>` was 1-based and `kb item` showed no numbers at
  all, while `--ref` and lint's internals were 0-based. Scripts calling
  `kb code rm` must decrement. Code refs are the only entity addressed by
  position — edges go by target, keywords by term — so this is the whole of the
  change, and it is now a stated convention rather than a per-command decision.
- **`kb code rm`, `code clear` and `code add --replace` maintain the
  suppressions that index into the ref list**: entries above a removed ref shift
  down, the entry on the removed ref is dropped, and `kb section rm` / `rename`
  drop or repoint the root entries naming them. Every dropped entry is printed
  with its reason. This is what lets the store validate a `ref` index and a
  `[section]` `who` at all.
- **`code-drift` can no longer be suppressed.** Its only possible basis is the
  ref's content, which is the thing whose change re-fires it, so the exemption
  would lapse on the next edit to that file. `kb verify` is the resolution and
  the refusal names it.

### Added

- **`kb suppress` covers every finding.** `--who` is the finding's own `who`,
  so a `[section]`, a `[prefix]`, a `keyword:<kw>` or a path can carry an
  exemption; those live in the store's root `lint` key, item ones stay on the
  item.

  ```
  kb suppress --who '[debt]' --rule section-min-items --reason "three items, and it will not grow"
  kb suppress --who auth.token --rule code-path --ref 1 --reason "generated, absent in CI"
  kb suppress --list
  ```

  An unknown rule name is now refused rather than recorded as an entry that
  silences nothing.

- **`kb lint --json`** prints every finding with the exact `kb suppress` command
  that silences it, filled in but for the reason — plus the counts and the
  thresholds the run used. `kb debt` has had `--json` since 3.0.0; lint's only
  machine-readable route was a four-column text layout that never said which
  columns were the arguments.

- **`code-inexact`** (warn): a `symbol` code ref on an item that has no `marker`
  ref beside it. A symbol ref records an intention — nothing checks the symbol
  is still in the file, and it can never be content-verified, so it never drifts
  — while a marker region is delimited in the source and can be found, hashed
  and followed when it moves. The message carries the `kb:begin` / `kb:end` pair
  and the follow-up `kb code add` ready to paste.

  **Expect one new warning per unmarked symbol ref on upgrade.** On kb's own
  store that is 52 across 101 items. It is a backlog, not a breakage: warnings do
  not move the exit code, and `kb baseline write` records it as the new
  reference in one command.

- **A suppression is scoped to its evidence.** `kb suppress` stamps a digest of
  exactly the fields the rule reads. Rewrite the key and the exemption
  suspends: the finding returns at full severity and `suppress-stale` names the
  entry, with its reason. Nothing is deleted, and only `kb suppress` re-stamps
  — `kb verify` deliberately does not, so re-verifying an item whose code moved
  invalidates its code-based exemptions instead of renewing them in passing.

- **`kb thresholds`** — per-repo lint settings, because the number that makes a
  rule useful in a 40-item store is not the one that works at 400.

  ```
  kb thresholds
  kb thresholds set edge_kind_share 0.9
  ```

  A matching flag still beats the store for one run: `kb lint
  --edge-kind-share 1` reports every unlabelled edge anywhere. `--stale-months`
  and `--max-span` now default to the store's threshold rather than to a
  constant.

- **Scope selection on `kb lint`** — ids, `--section`, `--surfaced-file`, the
  same set `kb debt` accepts, and `kb debt` gains `--section` to match. A
  scoped run reports how many findings it skipped as belonging elsewhere.

- **`kb lint --all`** — every threshold off, every suppressed finding printed
  and tagged. `--show-suppressed` does the second half alone.

- **`suppress-stale`** (warn) — an exemption that no longer resolves: a moved
  basis, a `--ref` index that names nothing, a rule this build does not have, a
  `who` that matches no section. It is the one rule `kb suppress` refuses to
  suppress.

- **`kb kw`** shows each keyword's share of the tagged items and marks the ones
  over the `keyword-broad` line.

### Fixed

- `kb suppress` validated no rule name, so a typo recorded an entry that
  silenced nothing and still counted in every summary.
- `edge-kind-missing` truncated its target list at three, which made the
  finding unusable as a work list.

## [3.0.0] — 2026-07-30

Run 001, delivered in three merged phases: `hygiene-cli` (lint rules, `kb
rename`, word diff), `schema-3` (digests, sub-sections), `debt` (`kb debt`, the
ratchet). Eight features, none dropped.

**Upgrading from 2.x is a deliberate step**, not automatic:

```
kb migrate --to 3
```

Every command refuses a schema-2 store until this runs, and names it in the
refusal. Both new store fields are optional, so the upgrade only stamps the
version and re-renders — a store that declares no subsection and has never been
verified renders byte-identically to its 2.x form. `kb 3.x` cannot read back a
schema-2 store and `kb 2.x` cannot read a schema-3 one; git is the undo.

### Breaking

- **Store schema 2 → 3.** Two optional fields: a `subsections` root key, and a
  `digest` per code ref. `kb migrate --to 3` upgrades in place. The check is
  strict on purpose — a version check that guesses is how two clones on
  different versions drift apart on the same file.
- **`kb mv` is now `kb rename`**, and `kb section mv` is `kb section rename`.
  The old spelling survives only as a stub that exits 2 naming its replacement,
  because a bare "unknown command" is a poor migration for a verb that lived in
  muscle memory.
- **An unknown root-level key in the store is now an error.** It was previously
  dropped in silence on the next write, which loses data that looked accepted.

### Added

- **`kb debt`** — every lint finding regrouped under the item that owns it,
  ordered by over-cap excess, with the `halve to <= N` target the repair is
  graded on. `kb lint` is one finding per line, which is what a gate wants; a
  session repairing an item wants that item's defects together. Findings that
  belong to no single item get their own trailing block rather than being
  attributed to an item that does not own them.

  ```
  kb debt
  kb debt --rule key-length
  ```

- **The ratchet — `kb baseline write` and `kb lint --baseline`.** Warnings are a
  backlog, and a gate that failed on their absolute count gets switched off
  within a day. What can gate is regression.

  ```
  kb lint --baseline
  ```

  Comparison is per rule plus total over-cap excess, never one summed count:
  splitting an oversized item into three *raises* `key-length` by two while
  paying down the debt. Three rises are reported and still exit 0 — `split`,
  `gate-opened` (an adoption-gated rule crossing its floor and turning on for
  the whole store at once) and `new-rule` — each printing the `kb baseline
  write` that records the new floor. `.kb-baseline.json` belongs in git.

- **Sub-sections** — a first-class grouping of items sharing an id prefix, with
  its own label, summary and order.

  ```
  kb subsection
  kb subsection new pages.mold-detail --order 10 --label "Mold detail"
  ```

  Nesting is unbounded. Depth renders as a breadcrumb rather than by heading
  level, because markdown stops at six `#` and `# title` / `## section` /
  `### item` already spends three. Membership is longest-prefix-wins, so a
  nested id belongs to its deepest declared ancestor and not to all of them.
  Grouping in the render is explicit rather than inferred from id sort order —
  `-` sorts before `.`, so a hyphen sibling like `pages.mold-detail-legacy`
  falls between a parent and its children.

- **Content digests and the `code-drift` rule.** `kb verify` now records a
  digest per code ref; `kb lint` reports the ones whose code has changed since.
  A `file` ref hashes the file, a `marker` ref hashes every region *that item*
  owns in it, and a `symbol` ref records nothing — hashing a symbol body needs
  real parsing. Hashes are over LF-normalised text, or a CRLF rewrite of
  unchanged content would fire on every ref.

- **Four lint rules**, 19 → 23:

  | rule | severity | fires on |
  |---|---|---|
  | `edge-kind-missing` | warn | edges still carrying the default `relates` kind |
  | `stale-id-mention` | warn | key prose naming an id that no longer exists |
  | `code-drift` | warn | code changed since the item was last verified |
  | `subsection-min-items` | warn | fewer than 2 items in a subsection |

  `edge-kind-missing` sits behind the same adoption gate as the keyword rules —
  it stays silent until the store has actually adopted edge kinds, rather than
  greeting a new KB with a wall of warnings. `stale-id-mention` requires a
  known section id *and* a last segment that is not a file extension: 11 of
  this repo's 14 section ids double as module filenames, so without the guard
  it reports `lint.py` and `render.py` as dead ids.

  `subsection-min-items` is 2 where `section-min-items` is 3, because a
  subsection is a split in progress and a legitimate halving can leave exactly
  two members.

- **A fourth pre-commit check**, `kb lint --baseline`, gated on
  `.kb-baseline.json` existing. A repo that has never run `kb baseline write`
  keeps exactly the three checks it had.

- **Word-level diff** when `kb set` changes a `key` or `summary` past 160
  characters, instead of printing both versions in full and leaving the reader
  to spot the edit. `autojunk=False` on the matcher is load-bearing: the
  default treats common words as junk past 200 elements and collapses a
  one-word edit into one opaque replacement.

- **`kb ctx` names an item's subsection and up to six siblings**, so a session
  repairing one child sees the others instead of leaving them asserting a
  now-contradicted statement.

- **`kb stats` reports key debt** — total characters over cap, and across how
  many items.

- `kb rename` additionally lists any item whose key prose still cites the old
  id. `stale-id-mention` would catch them on the next lint anyway; saying so at
  write time is cheaper than discovering it later.

### Changed

- `kb migrate` (v1 markdown → JSON) now writes the current schema rather than a
  hardcoded 2, which had made the v1 path fail its own post-migration
  validation once `SCHEMA` moved.
- A schema mismatch is now raised from the loader, before the graph is built,
  so the message names `kb migrate --to 3` instead of "expected 3, found 2".
- `ROOT_FIELDS` is live. It was declared and referenced nowhere; the writer
  hardcoded six keys, so an unknown root key round-tripped to nothing.
- `kb code add` deliberately does **not** stamp a digest. Adding a ref is not
  verifying it.

### Fixed

- `kb section rename` dispatched to the *read* path and silently did nothing,
  because the shared verb table it is driven by did not list `rename`. Same
  class of bug pre-empted for `kb subsection summary`.
- Packaging: `pyproject.toml` still declared `2.2.0` after the bump, so a built
  distribution carried a version the CLI contradicted. It is now read from
  `kbgraph.__version__`, which is the string `kb --version` prints.

### Packaging

- Zero dependencies, Python ≥3.9, unchanged.
- The sdist carries `tests/`, `hooks/`, `DESIGN.md` and the self-describing KB,
  so it is verifiable in place. The wheel is `src/kbgraph` only.
- 44 tests, ~480 assertions.

### Audit

No network imports, no `eval`/`exec`/`__import__`/`pickle`, no
`os.system`/`rmtree`/`remove`/`unlink`, no third-party dependencies. Across the
whole release: **two new imports** — `hashlib` for digests, `textwrap` for the
word diff — and **one new write site**, `.kb-baseline.json`, for six in total,
all of them store, render, migration or baseline output. `kb` still never
writes into your source tree.

---

## Before 3.0.0

`kb-graph` 2.2.0 is the earliest version; its history predates this changelog.
