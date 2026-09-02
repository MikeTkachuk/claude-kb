"""Locating, reading and writing the JSON store.

Diff quality is a requirement, not a nicety: `json.dump(indent=2)` would put
every edge on four lines and turn a one-word change into a 12-line diff. The
writer here indents containers and emits leaf objects inline.
"""

import json
import os

from .model import SCHEMA, Graph, ITEM_FIELDS, ROOT_FIELDS, SchemaError

STORE = "knowledgebase.json"
RENDER = "knowledgebase.md"

MIGRATE_HINT = (
    "found a schema-1 %s but no %s.\n"
    "kb 4.x reads the JSON store only. Migrate with:  kb migrate\n"
    "(or keep using the v1 grep+slice protocol in CLAUDE.md for this repo)"
)


class KBNotFound(Exception):
    pass


# Schemas this build can upgrade from. An older store is a migration prompt,
# not a malformed one -- `validate` would call it "expected 4, found 3", which
# names the symptom and not the fix. Both hops only stamp the version: every
# field either schema added is optional and absent until used, so 2 -> 4 in
# one step is the same write as 3 -> 4.
#
# That is a property of these two hops, not of `migrate`. `upgrade()` sets
# data["schema"] and writes; it has no per-hop transform and no ordering. The
# first migration that must REWRITE a field -- renaming a threshold, say, which
# is the only sanctioned way to rename one -- has to sequence the hops instead
# of stamping, and this tuple stops being a set of equivalent starting points.
UPGRADABLE = (2, 3)


class SchemaTooOld(Exception):
    def __init__(self, found):
        self.found = found
        Exception.__init__(
            self,
            "this store is schema %s; kb reads schema %d.\n"
            "Upgrade it once with:  kb migrate --to %d\n"
            "The upgrade only stamps the version -- both schema-3 fields are "
            "optional and stay absent until used. git is the undo."
            % (found, SCHEMA, SCHEMA))


# kb:begin store.upward-discovery
def find_store(explicit=None, start=None):
    """Walk up from CWD looking for the store. Repo-agnostic on purpose --
    the tool is installed once and used from any repo."""
    if explicit:
        if not os.path.exists(explicit):
            raise KBNotFound("no such file: %s" % explicit)
        return os.path.abspath(explicit)
    d = os.path.abspath(start or os.getcwd())
    while True:
        cand = os.path.join(d, STORE)
        if os.path.exists(cand):
            return cand
        legacy = os.path.join(d, RENDER)
        if os.path.exists(legacy) and _is_schema_1(legacy):
            raise KBNotFound(MIGRATE_HINT % (RENDER, STORE))
        parent = os.path.dirname(d)
        if parent == d:
            raise KBNotFound("no %s found (use --kb)" % STORE)
        d = parent
# kb:end store.upward-discovery


def _is_schema_1(md_path):
    """A v1 knowledgebase.md has `- Key:` field lines; a v2 render does not."""
    try:
        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(20000)
    except OSError:
        return False
    return "\n- Key: " in head


def load(explicit=None, validate=True):
    path = find_store(explicit)
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError as e:
            raise SchemaError(["%s: invalid JSON -- %s" % (path, e)])
    if data.get("schema") in UPGRADABLE and data.get("schema") != SCHEMA:
        raise SchemaTooOld(data.get("schema"))
    g = Graph(data, path=path)
    _reject_unknown_fields(data)
    if validate:
        g.validate()
    return g


def _reject_unknown_fields(data):
    p = []
    # Root fields were never checked before schema 3, so an unknown top-level
    # key was silently dropped by the writer rather than reported -- exactly
    # the round-trip data loss the item-level check exists to prevent.
    for f in data:
        if f not in ROOT_FIELDS:
            p.append("unknown top-level field '%s'" % f)
    for iid, d in sorted(data.get("items", {}).items()):
        for f in d:
            if f not in ITEM_FIELDS:
                p.append("%s: unknown field '%s'" % (iid, f))
    if p:
        raise SchemaError(p)


def save(graph, path=None):
    path = path or graph.path
    text = dumps(graph.to_dict())
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


# ----------------------------------------------------------- canonical writer


def _scalar(v):
    return v is None or isinstance(v, (str, int, float, bool))


# kb:begin store.canonical-writer
def _is_leaf(v):
    """A leaf object is one whose every value is a scalar or a list of
    scalars -- edges, code refs, meta, glossary entries, sections. These are
    read as a unit, so they are written as a unit."""
    if not isinstance(v, dict):
        return False
    for x in v.values():
        if _scalar(x):
            continue
        if isinstance(x, list) and all(_scalar(y) for y in x):
            continue
        return False
    return True


def _enc(v):
    return json.dumps(v, ensure_ascii=False)


def _inline(v):
    if isinstance(v, dict):
        return "{" + ", ".join("%s: %s" % (_enc(k), _inline(x))
                               for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_inline(x) for x in v) + "]"
    return _enc(v)
# kb:end store.canonical-writer


def _emit(v, ind, out):
    pad = "  " * ind
    if _scalar(v):
        out.append(_enc(v))
        return
    if isinstance(v, list):
        if not v:
            out.append("[]")
            return
        if all(_scalar(x) for x in v):
            out.append(_inline(v))
            return
        out.append("[\n")
        for i, x in enumerate(v):
            out.append(pad + "  ")
            if _is_leaf(x):
                out.append(_inline(x))
            else:
                _emit(x, ind + 1, out)
            out.append(",\n" if i < len(v) - 1 else "\n")
        out.append(pad + "]")
        return
    if not v:
        out.append("{}")
        return
    if _is_leaf(v):
        out.append(_inline(v))
        return
    out.append("{\n")
    keys = list(v)
    for i, k in enumerate(keys):
        out.append(pad + "  " + _enc(k) + ": ")
        _emit(v[k], ind + 1, out)
        out.append(",\n" if i < len(keys) - 1 else "\n")
    out.append(pad + "}")


def dumps(data):
    out = []
    _emit(data, 0, out)
    out.append("\n")
    return "".join(out)
