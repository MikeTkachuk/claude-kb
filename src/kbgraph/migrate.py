"""One-way migration: schema-1 knowledgebase.md -> schema-2 knowledgebase.json.

This is the only markdown parser in the codebase and it exists to be run once
per repo. Nothing else reads the v1 format; after migration the md is a render.

Three parts are not mechanical and are reported rather than assumed:
  * `type` per item -- heuristic here, reviewed by a human before the per-type
    length caps are enforced (TYPE_OVERRIDES carries the corrections).
  * section `Key knowledge` bullets -- matched back to the item they restate,
    becoming that item's `summary` + `highlight`. Unmatched bullets are printed.
  * `meta` dates -- recovered from git history of the md, not invented.
"""

import os
import re
import subprocess
import sys
from collections import OrderedDict

from . import render as render_mod
from . import store as store_mod
from .model import SCHEMA, Graph

H1 = re.compile(r"^#\s+(.*)$")
H2 = re.compile(r"^##\s+(.*?)\s*$")
H3 = re.compile(r"^###\s+(.*?)\s*$")
FIELD = re.compile(r"^-\s+(Code|Key|Affects|Affected by|Keywords):\s*(.*)$")
ITEM_SECTION = re.compile(r"^\[([^\]]+)\]$")
META_SECTION = re.compile(r"^§(.+)$")
SEP = re.compile(r"\s*(?:·|;)\s*")

MODAL = re.compile(r"\b(MUST|NEVER|ONLY|EXACTLY)\b")

GLOSSARY = {
    "types": {
        "guardrail": {"desc": "rule or contract whose violation breaks behavior",
                      "key_max": 320, "requires_modal": True},
        "contract": {"desc": "shape or protocol co-owned with the backend",
                     "key_max": 400},
        "concept": {"desc": "definition or invariant of the area", "key_max": 400},
        "behavior": {"desc": "what the main path actually does", "key_max": 320},
        "edge-case": {"desc": "what happens at a boundary or exceptional path",
                      "key_max": 240},
        "limitation": {"desc": "known gap, deliberately unfixed", "key_max": 240},
        "design-choice": {"desc": "a road taken, with the road not taken",
                          "key_max": 400},
        "directive": {"desc": "user-owned top-level priority; overrides every "
                              "other item", "key_max": 1200,
                      "requires_code": False},
    },
    "statuses": {
        "active": {"desc": "true of the code today"},
        "todo": {"desc": "agreed but not yet true of the code"},
        "deprecated": {"desc": "kept for history; no longer governs"},
    },
    "edge_kinds": {
        "constrains": {"label": "Constrains", "inverse": "Constrained by"},
        "calls": {"label": "Calls", "inverse": "Called by"},
        "consumes": {"label": "Consumes", "inverse": "Consumed by"},
        "refines": {"label": "Refines", "inverse": "Refined by"},
        "relates": {"label": "Relates to", "inverse": "Referenced by"},
    },
}

# Corrections to the type heuristic, from reading all 104 keys. The heuristic
# is kept because it gets ~2/3 right and makes the corrections auditable; this
# table is what a human reviews before the per-type caps are enforced.
#
# The recurring correction: a page/service/component item that *describes what
# the surface does* is `behavior`, even when its key happens to contain a MUST
# or a rationale clause. `guardrail` is reserved for rules, `contract` for
# shapes co-owned with the backend.
TYPE_OVERRIDES = {
    # [polling] -- explicitly "a known limitation to plan around, not a bug"
    "polling.swr-dedupe-staleness": "limitation",

    # [downloads] -- these describe the download path, they do not rule on it
    "downloads.authed-download": "behavior",
    "downloads.filename-derivation": "behavior",

    # [pages] -- page descriptions, not rules. The four real rules in this
    # section (root-shim, login, toast-on-mutation, mutate-after-mutation,
    # suspense-for-searchparams) keep `guardrail`.
    "pages.applications-list": "behavior",
    "pages.app-detail": "behavior",
    "pages.new-application": "behavior",
    "pages.preferences": "behavior",
    "pages.admin": "behavior",
    "pages.mold-create": "behavior",
    "pages.mold-detail": "behavior",
    "pages.mold-doc-preview": "behavior",
    "pages.prompts": "behavior",
    "pages.version-detail": "behavior",
    "pages.payment-history": "behavior",
    "pages.activity": "behavior",

    # [styling] -- a component description, not a design rationale
    "styling.user-menu": "behavior",

    # [contracts] -- everything named *-shapes is a contract by definition
    "contracts.no-rename-rule": "guardrail",
    "contracts.payment-shapes": "contract",
    "contracts.activity-shapes": "contract",
    "contracts.model-shapes": "contract",
    "contracts.mold-shapes": "contract",
    "contracts.prompt-shapes": "contract",

    # [services] -- API surface listings
    "services.molds-api": "behavior",
    "services.prompts-api": "behavior",

    # [testing]
    "testing.e2e-trigger": "guardrail",        # a process rule, not an edge case
    "testing.e2e-lifecycle-scope": "concept",  # a scope definition
    "testing.e2e-nav-contracts": "behavior",   # what the suite verifies
}

# Directives carry no v1 bullet, so nothing would highlight them and they would
# never reach §index -- unacceptable for the one section that overrides every
# other item. These are compressions of the directive keys, not edits to them.
DIRECTIVE_SUMMARIES = {
    "directive.priority-order":
        "privacy > backend-contract > UX stability > visual tone > feature velocity.",
    "directive.privacy":
        "User content is sensitive: no third-party calls carrying it, no analytics "
        "on form fields, no body logging.",
    "directive.backend-contract":
        "Shapes, status strings and endpoint paths are co-owned with the backend; "
        "never rename unilaterally.",
    "directive.visual-tone":
        "Factory/foundry with slight brutalism; avoid SaaS-landing aesthetics.",
    "directive.scope-discipline":
        "Bug fixes carry no surrounding cleanup; features stay in scope; no "
        "speculative abstractions.",
    "directive.tdd-services":
        "New services, components and lib utilities ship with tests; both "
        "`npm test` and `npm run e2e` must pass before a feature is complete.",
}

# v1 section `Key knowledge` bullets, matched to the item each one restates.
# Explicit rather than scored: IDF matching put the token-location bullet on
# auth.ssr-guard and swapped testing.stack with testing.e2e-selectors, and a
# section header quietly attached to the wrong item is exactly the drift this
# schema exists to remove.
#
# `None` means the bullet is dropped: it restates an item already summarised
# by another bullet, points at a directive, or is a historical migration note.
# Matching is by section + the bullet's opening text.
BULLET_MAP = [
    ("auth", "JWT lives in", "auth.token-location"),
    ("auth", "All authed pages are", "auth.ssr-guard"),
    ("auth", "Backend authorization is the real gate", "auth.admin-gate"),
    ("auth", "Redirects to `/login`", "auth.page-gate"),

    ("polling", "Backend-driven generation is async", "polling.hook-as-only-poller"),
    ("polling", "Terminal states:", "polling.terminal-statuses"),
    ("polling", "Polling is driven by SWR", "polling.interval"),
    ("polling", "Caller gates polling", None),          # = hook-as-only-poller

    ("api-client", "Client code treats the backend", "api-client.same-origin-rewrite"),
    ("api-client", "There is no CORS", None),           # = same-origin-rewrite
    ("api-client", "`NEXT_PUBLIC_API_URL` is compile-time", "api-client.env-var"),

    ("downloads", "All authed file downloads", "downloads.authed-download"),
    ("downloads", "Filename is the", "downloads.filename-derivation"),
    ("downloads", "Authed `/api/*` URLs MUST NOT", "downloads.no-href-trick"),

    ("pages", "All routes are", None),                  # = auth.ssr-guard
    ("pages", "Each page gates via", None),             # = auth.page-gate
    ("pages", "Mutating actions toast", "pages.toast-on-mutation"),
    ("pages", "`useSearchParams()` consumers", "pages.suspense-for-searchparams"),

    ("navigation", "Two **window classes**", "navigation.window-classes"),
    ("navigation", "`@modal/` inside", "navigation.modal-slot"),
    ("navigation", "Popups have TWO tiers", "navigation.popup-tiers"),
    ("navigation", "`<PageFrame", "navigation.page-frame"),
    ("navigation", "View Transitions API", "navigation.view-transitions"),

    ("styling", "UI uses **Tailwind", "styling.tailwind-base"),
    ("styling", "Visual tone is factory/foundry", "styling.no-saas-aesthetic"),
    ("styling", "The only retained globals", "styling.sidebar"),

    ("contracts", "`src/lib/types.ts` and", "contracts.shared-module"),
    ("contracts", "Every shape / status string", "contracts.no-rename-rule"),
    ("contracts", "Version-generation status vocab", "contracts.version-status-vocab"),
    ("contracts", "`cover_letter_needed` is removed", None),   # historical note
    ("contracts", "Each frontend release declares",
     "contracts.backend-version-coupling"),

    ("services", "Service functions in", "services.applications-mutations"),
    ("services", "Read-only `useEffect`-based", "services.admin-api"),
    ("services", "`ConflictError` is the typed error", "services.conflict-error"),

    ("testing", "See `§directive.tdd-services`", None),        # points at a directive
    ("testing", "Two stacks.", "testing.stack"),
    ("testing", "MSW intercepts", "testing.msw-contract"),
    ("testing", "Coverage (Vitest) targets", None),            # = directive.tdd-services
    ("testing", "E2E state is ephemeral", "testing.e2e-backend-container"),

    ("payments", "Hosted Checkout redirect", "payments.hosted-checkout-flow"),
    ("payments", "Prices are integer cents", "payments.fee-display-source"),
    ("payments", "Credit balance is a single SWR", "payments.balance-label"),
]


# ------------------------------------------------------------------ v1 parse


class V1Item(object):
    def __init__(self, iid, section):
        self.id = iid
        self.section = section
        self.code = ""
        self.key = ""
        self.keywords = []
        self.affects = []
        self.affected_by = []


def parse_v1(path):
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    title = "Knowledge Base"
    sections = OrderedDict()      # id -> {"bullets": [...]}
    items = OrderedDict()
    sec = meta = item = field = None

    for raw in lines:
        m = H1.match(raw)
        if m and title == "Knowledge Base":
            title = m.group(1).strip()
            continue

        m = H2.match(raw)
        if m:
            name = m.group(1).strip()
            item = field = None
            mi, mm = ITEM_SECTION.match(name), META_SECTION.match(name)
            if mi:
                sec, meta = mi.group(1), None
                sections.setdefault(sec, {"bullets": []})
            elif mm:
                meta, sec = mm.group(1), None
            else:
                sec = meta = None
            continue

        # §user-directives items are prose under the heading, not field lines
        if meta == "user-directives":
            m = H3.match(raw)
            if m:
                iid = m.group(1).strip()
                sec_id = iid.split(".", 1)[0]
                sections.setdefault(sec_id, {"bullets": []})
                item = V1Item(iid, sec_id)
                items[iid] = item
                continue
            if item is not None and raw.strip() and not raw.startswith("_"):
                item.key = (item.key + " " + raw.strip()).strip()
            continue

        if not sec:
            continue

        m = H3.match(raw)
        if m:
            item = V1Item(m.group(1).strip(), sec)
            items[item.id] = item
            field = None
            continue

        if item is not None:
            m = FIELD.match(raw)
            if m:
                field, val = m.group(1), m.group(2).strip()
                if field == "Code":
                    item.code = val
                elif field == "Key":
                    item.key = val
                elif field == "Keywords":
                    item.keywords = _split(val)
                elif field == "Affects":
                    item.affects = _split(val)
                else:
                    item.affected_by = _split(val)
                continue
            # a Key that wraps onto an indented continuation line
            if field == "Key" and raw.strip() and raw[:1].isspace():
                item.key += " " + raw.strip()
                continue
            if not raw.strip():
                field = None
            continue

        s = raw.strip()
        if s.startswith("- "):
            sections[sec]["bullets"].append(s[2:].strip())

    return title, sections, items


def _split(s):
    return [x.strip() for x in SEP.split(s.strip()) if x.strip()]


# ------------------------------------------------------------------ code refs


def code_refs(raw):
    """`path - symbol - part ; path2` -> typed refs. Line numbers never appear
    in v1 anchors, so there is nothing to strip."""
    out = []
    for anchor in (a.strip() for a in raw.split(";")):
        if not anchor:
            continue
        parts = [p.strip() for p in anchor.split(" - ", 2)]
        path = parts[0]
        if not path:
            continue
        ref = {"kind": "file", "path": path}
        if len(parts) > 1 and parts[1]:
            ref = {"kind": "symbol", "path": path, "symbol": parts[1]}
            if len(parts) > 2 and parts[2]:
                ref["part"] = parts[2]
        out.append(ref)
    return out


# ---------------------------------------------------------------------- type


def guess_type(it):
    """Cheap and wrong sometimes on purpose -- TYPE_OVERRIDES is the fix, and
    the caps stay off until a human has read the assignments."""
    k = it.key
    low = k.lower()
    if it.section == "directive":
        return "directive"
    if re.search(r"\b(instead of|rather than|chosen over|deliberately|"
                 r"we do not|road not taken|trade-?off)\b", low):
        return "design-choice"
    if re.search(r"\b(not implemented|no support|unsupported|does not support|"
                 r"known gap|not yet|missing\b.*\bby design)\b", low):
        return "limitation"
    if MODAL.search(k):
        return "guardrail"
    if it.section == "contracts" or re.search(
            r"\b(shape|payload|enum|field names?|co-owned|response body|"
            r"status strings?)\b", low):
        return "contract"
    if re.search(r"\b(if |when |on (?:404|401|403|error|failure)|fails?\b|"
                 r"empty|missing|absent|edge case)\b", low):
        return "edge-case"
    if re.search(r"\b(renders?|fetches|polls?|calls?|shows?|displays?|"
                 r"redirects?|mounts?|submits?)\b", low):
        return "behavior"
    return "concept"


# ---------------------------------------------------------------------- dates


def git_history(md_path, quiet=False):
    """{id: (created, modified)} recovered by replaying every commit that
    touched the md. One `git show` per commit beats one `git log -S` per item
    by an order of magnitude, and gives exact modification dates for free."""
    repo = os.path.dirname(os.path.abspath(md_path))
    name = os.path.basename(md_path)
    try:
        log = subprocess.run(
            ["git", "log", "--reverse", "--format=%H %ad", "--date=short",
             "--", name],
            cwd=repo, capture_output=True, text=True, encoding="utf-8",
            errors="replace")
    except OSError:
        return {}
    if log.returncode != 0:
        return {}

    dates = {}
    prev = {}
    commits = [l.split(" ", 1) for l in log.stdout.splitlines() if " " in l]
    for n, (sha, date) in enumerate(commits, 1):
        show = subprocess.run(["git", "show", "%s:%s" % (sha, name)],
                              cwd=repo, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if show.returncode != 0:
            continue
        cur = _blocks(show.stdout)
        for iid, text in cur.items():
            if iid not in dates:
                dates[iid] = [date, date]
            elif prev.get(iid) != text:
                dates[iid][1] = date
        prev = cur
        if not quiet and n % 25 == 0:
            sys.stderr.write("  ...%d/%d commits\n" % (n, len(commits)))
    return {k: tuple(v) for k, v in dates.items()}


def _blocks(text):
    """id -> the item's raw block, for change detection across commits."""
    out, cur, buf = {}, None, []
    for line in text.splitlines():
        m = H3.match(line)
        if m:
            if cur:
                out[cur] = "\n".join(buf).strip()
            cur, buf = m.group(1).strip(), []
            continue
        if line.startswith("## "):
            if cur:
                out[cur] = "\n".join(buf).strip()
            cur, buf = None, []
            continue
        if cur:
            buf.append(line)
    if cur:
        out[cur] = "\n".join(buf).strip()
    return out


# ----------------------------------------------------------------- key knowledge


def attach_bullets(items, sections, report):
    """A v1 section bullet is a restatement of one of its items -- that
    duplication is exactly what `summary` + `highlight` replace. BULLET_MAP
    says which item; anything it does not cover is reported, never guessed."""
    for sid, sec in sections.items():
        for bullet in sec["bullets"]:
            target, mapped = None, False
            for msid, prefix, iid in BULLET_MAP:
                if msid == sid and bullet.startswith(prefix):
                    target, mapped = iid, True
                    break
            if not mapped:
                report["unmapped"].append((sid, bullet))
                continue
            if target is None:
                report["dropped"].append((sid, bullet))
                continue
            it = items.get(target)
            if it is None:
                report["unmapped"].append((sid, "BAD MAPPING -> " + target))
                continue
            if it["section"] != sid:
                report["unmapped"].append(
                    (sid, "%s is in [%s], not [%s]" % (target, it["section"], sid)))
                continue
            it["highlight"] = True
            report["matched"].append((target, bullet))
            # A summary longer than the key is not a summary; the item is
            # still highlighted and falls back to rendering its key.
            if len(bullet) < len(it["key"]):
                it["summary"] = bullet
            else:
                report["long"].append((target, bullet))


# --------------------------------------------------------------------- build


def build(md_path, use_git=True, run=None, quiet=False):
    title, sections, v1items = parse_v1(md_path)
    report = {"unmapped": [], "dropped": [], "long": [], "dangling": [],
              "types": {}, "no_dates": [], "resectioned": [], "matched": []}

    dates = git_history(md_path, quiet=quiet) if use_git else {}
    today = _today()

    order = {}
    for n, sid in enumerate(sections):
        # directives lead; everything else keeps its file order
        order[sid] = 0 if sid == "directive" else (n + 1) * 10

    # Union both v1 directions into a single outbound list. v1 edges are meant
    # to be symmetric but ~40 are not; dropping `Affected by` outright would
    # silently delete every one-sided edge, so it is folded in instead.
    outbound = {iid: set(it.affects) for iid, it in v1items.items()}
    for iid, it in v1items.items():
        for src in it.affected_by:
            outbound.setdefault(src, set()).add(iid)

    items = {}
    for iid, it in v1items.items():
        # In v1 an item's section was wherever it physically sat, which drifts
        # from its id prefix (four pages.* items live under ## [payments]).
        # In v2 section is a field, so the id prefix -- which the owning
        # section's `Items:` list agrees with -- wins.
        prefix = iid.split(".", 1)[0]
        if prefix in sections and prefix != it.section:
            report["resectioned"].append((iid, it.section, prefix))
            it.section = prefix
        created, modified = dates.get(iid, (today, today))
        if iid not in dates:
            report["no_dates"].append(iid)
        t = TYPE_OVERRIDES.get(iid) or guess_type(it)
        report["types"][t] = report["types"].get(t, 0) + 1
        edges = sorted(x for x in outbound.get(iid, ()) if x != iid)
        for e in edges:
            if e not in v1items:
                report["dangling"].append((iid, e))
        d = {
            "id": iid,
            "section": it.section,
            "type": t,
            "status": "active",
            "key": it.key.strip(),
            "code": code_refs(it.code),
            "meta": {"created": created, "modified": modified,
                     "verified": modified},
        }
        if iid in DIRECTIVE_SUMMARIES:
            d["summary"] = DIRECTIVE_SUMMARIES[iid]
            d["highlight"] = True
        if it.keywords:
            d["keywords"] = it.keywords
        if edges:
            d["affects"] = [{"to": e, "kind": "relates"} for e in edges]
        items[iid] = d

    attach_bullets(items, sections, report)

    data = {
        "schema": SCHEMA,
        "title": title,
        "run": run,
        "glossary": GLOSSARY,
        "sections": {sid: {"order": order[sid], "label": sid}
                     for sid in sections},
        "items": {},
    }
    for iid in sorted(items):
        d = dict(items[iid])
        d.pop("id")
        data["items"][iid] = d
    return data, report


def _today():
    import datetime
    return datetime.date.today().isoformat()


# ------------------------------------------------------------------- command


def upgrade(a):
    """Raise an existing store to the current schema.

    Deliberately reads the JSON directly rather than through `store.load`:
    load is exactly what refuses an older store, so routing the upgrade
    through it would make the fix unreachable from the error that demands it.
    """
    import json
    path = a.kb
    if not path:
        try:
            path = store_mod.find_store()
        except store_mod.KBNotFound as e:
            sys.stderr.write("kb: %s\n" % e)
            return 2
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    found = data.get("schema")
    want = getattr(a, "to", None)
    if want is not None and want != SCHEMA:
        sys.stderr.write("kb: this build writes schema %d, not %r -- "
                         "run `kb migrate --to %d`\n" % (SCHEMA, want, SCHEMA))
        return 2
    if found == SCHEMA:
        print("already schema %d -- nothing to do" % SCHEMA)
        return 0
    if found not in store_mod.UPGRADABLE:
        sys.stderr.write("kb: cannot upgrade schema %r to %d\n"
                         % (found, SCHEMA))
        return 2
    data["schema"] = SCHEMA
    g = Graph(data, path=path)
    g.validate()
    if a.dry_run:
        sys.stdout.write(store_mod.dumps(g.to_dict()))
        print("-- dry run, nothing written")
        return 0
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(store_mod.dumps(g.to_dict()))
    render_mod.write(g)
    print("upgraded %s: schema %s -> %d (%d items unchanged)"
          % (store_mod.STORE, found, SCHEMA, len(g.items)))
    return 0


def cmd_migrate(a):
    if getattr(a, "to", None):
        return upgrade(a)
    md = a.md or os.path.join(os.getcwd(), store_mod.RENDER)
    if not os.path.exists(md):
        sys.stderr.write("kb: no such file: %s\n" % md)
        return 2
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(md)),
                                store_mod.STORE)
    if os.path.exists(out) and not a.force:
        sys.stderr.write("kb: %s already exists (use --force)\n" % out)
        return 2

    data, report = build(md, use_git=not a.no_git, run=a.run)
    g = Graph(data)
    g.validate()

    if a.dry_run:
        sys.stdout.write(store_mod.dumps(data))
    else:
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(store_mod.dumps(data))

    n_edges = sum(len(i.affects) for i in g.items.values())
    print("\nmigrated %d items in %d sections, %d edges"
          % (len(g.items), len(g.sections), n_edges))
    print("types: " + " · ".join("%s %d" % (k, v)
                                 for k, v in sorted(report["types"].items())))
    print("highlights: %d items carry a section summary"
          % sum(1 for i in g.items.values() if i.highlight))
    if report["no_dates"]:
        print("no git history for %d items (stamped today): %s"
              % (len(report["no_dates"]), ", ".join(report["no_dates"][:5])))
    if a.show_matches:
        print("\nsection bullets attached as summaries:")
        for iid, b in report["matched"]:
            print("  %-34s %s" % (iid, b[:100]))
    if report["resectioned"]:
        print("\n%d items moved to the section their id names:"
              % len(report["resectioned"]))
        for iid, was, now in report["resectioned"]:
            print("  %s: [%s] -> [%s]" % (iid, was, now))
    if report["dangling"]:
        print("\n%d edges point at ids that do not exist:" % len(report["dangling"]))
        for src, dst in report["dangling"]:
            print("  %s -> %s" % (src, dst))
    if report["long"]:
        print("\n%d bullets were longer than the key they summarise "
              "(highlighted, no summary stored):" % len(report["long"]))
        for iid, b in report["long"]:
            print("  %s: %s" % (iid, b[:110]))
    if report["dropped"]:
        print("\n%d bullets dropped as redundant (BULLET_MAP -> None):"
              % len(report["dropped"]))
        for sid, b in report["dropped"]:
            print("  [%s] %s" % (sid, b[:110]))
    if report["unmapped"]:
        print("\n%d section bullets are NOT in BULLET_MAP -- add an entry for "
              "each (an item id, or None to drop it):" % len(report["unmapped"]))
        for sid, b in report["unmapped"]:
            print("  [%s] %s" % (sid, b[:110]))
    if not a.dry_run:
        print("\nwrote %s" % out)
        # Migration is the one operation entitled to replace the v1 md: the
        # render it writes supersedes exactly the file it just consumed.
        print("wrote %s" % render_mod.write(
            store_mod.load(out, validate=False), force=True))
    return 0
