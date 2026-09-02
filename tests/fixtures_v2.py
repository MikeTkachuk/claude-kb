"""Synthetic schema-4 stores. Zero deps, no repo required."""

import datetime
import json
import os

def _iso(days_ago):
    return (datetime.date.today() - datetime.timedelta(days=days_ago)).isoformat()

GLOSSARY = {
    "types": {
        "guardrail": {"desc": "rule whose violation breaks behavior",
                      "key_max": 320, "requires_modal": True},
        "concept": {"desc": "definition or invariant of the area", "key_max": 400},
        "behavior": {"desc": "what the main path actually does", "key_max": 320},
        "limitation": {"desc": "known gap, deliberately unfixed", "key_max": 240},
        "directive": {"desc": "user-owned top-level priority", "key_max": 1000,
                      "requires_code": False},
    },
    "statuses": {"active": {}, "todo": {}, "deprecated": {}},
    "edge_kinds": {
        "constrains": {"label": "Constrains", "inverse": "Constrained by"},
        "consumes": {"label": "Consumes", "inverse": "Consumed by"},
        "relates": {"label": "Relates to", "inverse": "Referenced by"},
    },
}

M = {"created": _iso(60), "modified": _iso(14),
     "verified": _iso(14), "run": "007"}


def clean():
    return {
        "schema": 4,
        "title": "Knowledge Base — Fixture",
        "run": "007",
        "glossary": GLOSSARY,
        "sections": {
            "directives": {"order": 0, "label": "directives"},
            "auth": {"order": 10, "label": "auth"},
            "api": {"order": 20, "label": "api"},
        },
        "items": {
            "directives.privacy": {
                "section": "directives", "type": "directive", "status": "active",
                "key": "User content NEVER leaves the same origin.",
                "code": [], "meta": dict(M),
            },
            "auth.single-fetch-path": {
                "section": "auth", "type": "guardrail", "status": "active",
                "key": "Every authenticated request MUST go through `authFetch`.",
                "summary": "All authed HTTP goes through `authFetch`.",
                "highlight": True,
                "keywords": ["authfetch", "http"],
                "code": [{"kind": "marker", "path": "src/lib/api.ts"},
                         {"kind": "symbol", "path": "src/lib/api.ts",
                          "symbol": "authFetch", "part": "401 redirect branch"}],
                "affects": [{"to": "api.fetcher", "kind": "constrains"},
                            {"to": "auth.token", "kind": "relates"}],
                "meta": dict(M),
            },
            "auth.token": {
                "section": "auth", "type": "concept", "status": "active",
                "key": "The token lives in `localStorage[\"access_token\"]`.",
                "code": [{"kind": "file", "path": "src/lib/auth.ts"}],
                "meta": dict(M),
                "lint": {"ignore": [{"rule": "key-length",
                                     "reason": "one contract, four exceptions"}]},
            },
            "auth.gate": {
                "section": "auth", "type": "guardrail", "status": "todo",
                "key": "Pages MUST render a loading state until `useAuth()` resolves.",
                "code": [{"kind": "symbol", "path": "src/hooks/useAuth.ts",
                          "symbol": "useAuth"}],
                "affects": [{"to": "auth.token", "kind": "consumes"}],
                "meta": dict(M),
            },
            "api.fetcher": {
                "section": "api", "type": "concept", "status": "active",
                "key": "SWR fetchers wrap `authFetch` and never call `fetch` directly.",
                "highlight": True,
                "code": [{"kind": "file", "path": "src/lib/swr.ts"}],
                "affects": [{"to": "auth.single-fetch-path", "kind": "relates"}],
                "meta": dict(M),
            },
            "api.errors": {
                "section": "api", "type": "concept", "status": "deprecated",
                "key": "Error envelopes carry `detail` as a string.",
                "code": [{"kind": "file", "path": "src/lib/types.ts"}],
                "meta": dict(M),
            },
        },
    }


def clean_v2():
    """A schema-2 store, kept for exactly one purpose: exercising
    `kb migrate --to 4` from the oldest schema this build accepts. Everything
    else in the suite is schema 4, because a fixture the tool refuses to load
    cannot test anything else about it."""
    d = clean()
    d["schema"] = 2
    d.pop("subsections", None)
    d.pop("lint", None)
    return d


def clean_v3():
    """A schema-3 store: the hop every existing user actually takes."""
    d = clean()
    d["schema"] = 3
    d.pop("lint", None)
    return d


def with_lint(d=None):
    """A store carrying both halves of the root `lint` key."""
    d = d or clean()
    d["lint"] = {
        "thresholds": {"edge_kind_share": 0.9},
        "ignore": [{"who": "[api]", "rule": "section-min-items",
                    "reason": "two items, and the third would be invented"}],
    }
    return d


def dirty():
    """Every structural failure validate() must catch, one per item."""
    d = clean()
    it = d["items"]
    it["auth.bad-section"] = {
        "section": "nope", "type": "concept", "status": "active",
        "key": "x", "code": [{"kind": "file", "path": "a.ts"}], "meta": dict(M)}
    it["api.bad-prefix"] = {
        "section": "auth", "type": "concept", "status": "active",
        "key": "x", "code": [{"kind": "file", "path": "a.ts"}], "meta": dict(M)}
    it["auth.bad-type"] = {
        "section": "auth", "type": "invented", "status": "active",
        "key": "x", "code": [{"kind": "file", "path": "a.ts"}], "meta": dict(M)}
    it["auth.bad-status"] = {
        "section": "auth", "type": "concept", "status": "wip",
        "key": "x", "code": [{"kind": "file", "path": "a.ts"}], "meta": dict(M)}
    it["auth.no-code"] = {
        "section": "auth", "type": "concept", "status": "active",
        "key": "x", "code": [], "meta": dict(M)}
    it["auth.bad-kind"] = {
        "section": "auth", "type": "concept", "status": "active", "key": "x",
        "code": [{"kind": "file", "path": "a.ts"}],
        "affects": [{"to": "auth.token", "kind": "invented"}], "meta": dict(M)}
    it["auth.lineno"] = {
        "section": "auth", "type": "concept", "status": "active", "key": "x",
        "code": [{"kind": "file", "path": "src/lib/api.ts:42"}], "meta": dict(M)}
    it["auth.bad-meta"] = {
        "section": "auth", "type": "concept", "status": "active", "key": "x",
        "code": [{"kind": "file", "path": "a.ts"}],
        "meta": {"created": "March", "modified": "2026-03-04",
                 "verified": "2026-03-04"}}
    it["auth.bad-suppress"] = {
        "section": "auth", "type": "concept", "status": "active", "key": "x",
        "code": [{"kind": "file", "path": "a.ts"}], "meta": dict(M),
        "lint": {"ignore": [{"rule": "key-length"}]}}
    d["lint"] = {
        "thresholds": {"section_min_itms": 4},
        "ignore": [{"who": "[auth]", "rule": "section-min-items"}],
    }
    return d


V1_MD = """# Knowledge Base — Fixture

## §user-directives

_User-owned._

### directive.privacy
User content is sensitive. NO third-party calls.

## §maintenance

### Edge symmetry invariant
Prose that is not an item.

## [auth]

**Key knowledge:**
- The token lives in local storage and nowhere else.
- A bullet that no map entry covers.

**Items:** auth.token · auth.gate

**Related:** [api]

### auth.token
- Code: src/lib/auth.ts - getToken - `typeof window` check ; src/lib/store.ts
- Key: Token is stored at `localStorage["access_token"]`.
- Keywords: token · storage
- Affects: api.fetcher
- Affected by: auth.gate

### auth.gate
- Code: src/hooks/useAuth.ts - useAuth
- Key: Pages MUST render a loading state until ready.
- Affects: auth.token

## [api]

**Key knowledge:**
- Fetchers wrap authFetch.

**Items:** api.fetcher

### api.fetcher
- Code: src/lib/swr.ts
- Key: SWR fetchers wrap `authFetch`.
- Affected by: auth.token

### auth.stray
- Code: src/lib/stray.ts
- Key: An item that physically sits under the wrong section heading.
- Affects: nope.missing
"""


def write_v1(tmp):
    import os as _os
    p = _os.path.join(tmp, "knowledgebase.md")
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(V1_MD)
    return p


def write(data, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path


def make(tmp, data=None):
    """Write a store into tmp and return its path."""
    os.makedirs(tmp, exist_ok=True)
    return write(clean() if data is None else data,
                 os.path.join(tmp, "knowledgebase.json"))
