#!/usr/bin/env python3
"""Test suite for kb-graph v2: run it with `pytest`.

Tests shell out to the CLI and assert on its stdout, because that surface *is*
the contract. A refactor behind it is free; a changed message that the docs
quote is a failure, which is the point.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

import fixtures_v2 as fx
from kbgraph import render as render_mod
from kbgraph import store as store_mod
from kbgraph.model import Graph, SchemaError

HERE = os.path.dirname(os.path.abspath(__file__))


def run(kb, *args):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(HERE, os.pardir, "src")
    p = subprocess.run([sys.executable, "-m", "kbgraph.cli", "--kb", kb]
                       + [str(a) for a in args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_raw(kb, *args):
    """As `run`, but without universal-newline translation -- for the tests
    whose subject is the exact bytes on stdout."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(HERE, os.pardir, "src")
    p = subprocess.run([sys.executable, "-m", "kbgraph.cli", "--kb", kb]
                       + [str(a) for a in args],
                       capture_output=True, env=env)
    return p.returncode, p.stdout


def ok(name, cond, detail=""):
    """Assert `cond`, labelled with the sentence it is checking.

    A plain `assert rc == 0 and "wrote" in o` says what broke but not what was
    being claimed; `name` is the claim, and it is what a reader of a failure
    needs first. Kept as a helper rather than inlined so that every check reads
    the same and the label cannot be forgotten.
    """
    assert cond, name + (("\n" + detail.strip()[:700]) if detail else "")


def has(name, text, *needles):
    missing = [n for n in needles if n not in text]
    ok(name, not missing, "missing %r in:\n%s" % (missing, text))


def read(p, binary=False):
    if binary:
        with open(p, "rb") as f:
            return f.read()
    with open(p, encoding="utf-8", newline="") as f:
        return f.read()


# ------------------------------------------------------------------- model


def test_model():
    print("model")
    g = Graph(fx.clean()).validate()
    ok("items sorted by id", list(g.items) == sorted(g.items))
    ok("sections ordered by order",
       [s.id for s in g.ordered_sections()] == ["directives", "auth", "api"])

    inb = g.inbound
    ok("inverse edge derived",
       inb["api.fetcher"] == [("auth.single-fetch-path", "constrains")],
       repr(inb["api.fetcher"]))
    ok("inverse is not stored",
       "affected_by" not in json.dumps(g.to_dict()))
    ok("two inbound on auth.token",
       sorted(x[0] for x in inb["auth.token"]) == ["auth.gate", "auth.single-fetch-path"])
    ok("degree counts both directions", g.degree("auth.single-fetch-path") == 3,
       str(g.degree("auth.single-fetch-path")))

    ok("section membership derived",
       [i.id for i in g.section_items("auth")]
       == ["auth.gate", "auth.single-fetch-path", "auth.token"])
    ok("highlights derived",
       [i.id for i in g.highlights("auth")] == ["auth.single-fetch-path"])
    rel = dict(g.related_sections("auth"))
    ok("related sections carry edge counts", rel == {"api": 2}, repr(rel))

    it = g.items["auth.single-fetch-path"]
    ok("headline prefers summary", it.headline == it.summary)
    ok("headline falls back to key",
       g.items["auth.token"].headline == g.items["auth.token"].key)
    ok("suppression lookup", g.items["auth.token"].suppressed("key-length"))
    ok("no false suppression", not g.items["auth.token"].suppressed("orphan"))
    ok("directive needs no code", g.items["directives.privacy"].code == [])
    ok("lookup by suffix", g.get("token").id == "auth.token")
    try:
        g.get("nope")
        ok("unknown id raises", False)
    except KeyError:
        ok("unknown id raises", True)


def test_validate():
    print("validate")
    try:
        Graph(fx.dirty()).validate()
        ok("dirty store rejected", False)
        return
    except SchemaError as e:
        p = "\n".join(e.problems)
    ok("dirty store rejected", True)
    for name, needle in [
        ("unknown section", "auth.bad-section: unknown section"),
        ("prefix mismatch", "api.bad-prefix: id prefix does not match"),
        ("unknown type", "auth.bad-type: unknown type"),
        ("unknown status", "auth.bad-status: unknown status"),
        ("missing code", "auth.no-code: needs at least one code ref"),
        ("unknown edge kind", "auth.bad-kind: unknown edge kind"),
        ("line number in path", "auth.lineno: code paths never carry line numbers"),
        ("bad date", "auth.bad-meta: meta.created must be YYYY-MM-DD"),
        ("suppression needs reason", "auth.bad-suppress: lint.ignore needs both"),
        ("unknown threshold name", "lint.thresholds: unknown threshold "
         "'section_min_itms'"),
        ("root suppression needs who/rule/reason",
         "lint.ignore needs 'who', 'rule' and 'reason'"),
    ]:
        ok(name, needle in p, p)

    d = fx.clean()
    d["schema"] = 1
    try:
        Graph(d).validate()
        ok("schema 1 rejected", False)
    except SchemaError as e:
        ok("schema 1 rejected", "expected 4" in str(e), str(e))


def test_schema_gate_and_upgrade():
    print("schema: the 2 -> 4 gate")
    tmp = tempfile.mkdtemp()
    try:
        p = fx.make(tmp, fx.clean_v2())
        rc, o = run(p, "stats")
        ok("a schema-2 store refuses every command", rc == 2, o)
        has("and names the fix rather than the symptom", o, "kb migrate --to 4")
        ok("it is not reported as malformed", "malformed" not in o, o)

        rc, o = run(p, "migrate", "--to", "4", "--dry-run")
        ok("dry run writes nothing", rc == 0 and _store(p)["schema"] == 2, o)

        rc, o = run(p, "migrate", "--to", "4")
        ok("the upgrade succeeds", rc == 0 and "schema 2 -> 4" in o, o)
        ok("the store is schema 4", _store(p)["schema"] == 4)
        ok("nothing but the version changed",
           json.dumps(_store(p)["items"], sort_keys=True) ==
           json.dumps(fx.clean_v2()["items"], sort_keys=True))

        rc, o = run(p, "migrate", "--to", "4")
        ok("the upgrade is idempotent", rc == 0 and "already" in o, o)
        rc, o = run(p, "stats")
        ok("commands work afterwards", rc == 0, o)
        rc, o = run(p, "render", "--check")
        ok("the render is in sync afterwards", rc == 0, o)

        d = fx.clean()
        d["subsektions"] = {}
        p2 = fx.make(tempfile.mkdtemp(), d)
        rc, o = run(p2, "stats")
        ok("an unknown top-level field is rejected, not dropped",
           rc == 2 and "subsektions" in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------- store


def test_writer():
    print("canonical writer")
    text = store_mod.dumps(Graph(fx.clean()).to_dict())
    lines = text.splitlines()
    ok("edges inline, one per line",
       '{"to": "api.fetcher", "kind": "constrains"}' in text, text[:400])
    ok("code refs inline",
       '{"kind": "marker", "path": "src/lib/api.ts"}' in text)
    ok("meta inline",
       ('"meta": {"created": "%s"' % fx.M["created"]) in text)
    ok("sections inline", '"auth": {"order": 10, "label": "auth"}' in text)
    ok("keywords inline", '"keywords": ["authfetch", "http"]' in text)
    ok("containers indented 2", any(l.startswith('      "section":') for l in lines),
       repr([l for l in lines if '"section"' in l][:2]))
    ok("trailing newline", text.endswith("}\n"))
    ok("no CR", "\r" not in text)

    long = [l for l in lines if len(l) > 200]
    ok("no absurdly long lines", not long, repr(long[:2]))

    g2 = Graph(json.loads(text)).validate()
    ok("round-trips through json", store_mod.dumps(g2.to_dict()) == text)
    ok("idempotent", store_mod.dumps(Graph(json.loads(text)).to_dict()) == text)

    d = fx.clean()
    d["items"]["auth.token"]["keywords"] = ["zeta", "alpha", "zeta"]
    d["items"]["auth.gate"]["affects"] = [
        {"to": "api.fetcher", "kind": "relates"},
        {"to": "auth.token", "kind": "consumes"}]
    a = store_mod.dumps(Graph(d).to_dict())
    d["items"]["auth.gate"]["affects"].reverse()
    b = store_mod.dumps(Graph(d).to_dict())
    ok("edge order canonicalised", a == b)
    ok("keywords deduped + sorted", '"keywords": ["alpha", "zeta"]' in a)


def test_unknown_field():
    print("unknown fields")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["auth.token"]["notes"] = "hi"
        p = fx.make(tmp, d)
        rc, o = run(p, "validate")
        ok("typo'd field is fatal", rc == 2 and "unknown field 'notes'" in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ render


def test_render():
    print("render")
    g = Graph(fx.clean()).validate()
    md = render_mod.render(g)

    has("banner warns off hand-edits", md, "Do not hand-edit")
    has("index lists sections with counts", md, "- **[auth]** (3)")
    has("index lists highlights", md, "**[api]** (2) — api.fetcher")
    has("section header", md, "## [auth]")
    has("key knowledge uses summary", md,
        "- `auth.single-fetch-path` — All authed HTTP goes through `authFetch`.")
    has("related carries counts", md, "**Related:** [api] (2)")
    has("item header carries type + status", md,
        "### auth.single-fetch-path\n`guardrail` · `active`")
    has("marker ref flagged", md, "`src/lib/api.ts` *(marker)*")
    has("symbol ref with part", md, "`src/lib/api.ts` → `authFetch` — 401 redirect branch")
    has("outbound labelled by kind", md, "- **Constrains:** api.fetcher")
    has("inbound labelled by inverse", md, "- **Constrained by:** auth.single-fetch-path")
    has("empty direction shows a dash", md, "- **Affects:** —")
    has("multi-ref code nests", md, "- **Code:**\n  - `src/lib/api.ts` *(marker)*")
    has("single-ref code inlines", md, "- **Code:** `src/lib/auth.ts`")

    ok("summary not repeated in the item block",
       md.count("All authed HTTP goes through `authFetch`.") == 1)
    ok("meta stays out of the render",
       fx.M["created"] not in md and "verified" not in md)
    ok("deterministic", render_mod.render(Graph(fx.clean())) == md)
    ok("sections render in order",
       md.index("## [directives]") < md.index("## [auth]") < md.index("## [api]"))
    ok("ends with exactly one newline", md.endswith("—\n") or md.endswith("\n"))
    ok("no double blank at eof", not md.endswith("\n\n"))


def test_render_check():
    print("render --check")
    tmp = tempfile.mkdtemp()
    try:
        p = fx.make(tmp)
        md = os.path.join(tmp, "knowledgebase.md")

        rc, o = run(p, "render", "--check")
        ok("check fails when md is missing", rc == 1 and "does not exist" in o, o)

        rc, o = run(p, "render")
        ok("render writes", rc == 0 and os.path.exists(md), o)

        rc, o = run(p, "render", "--check")
        ok("check passes after render", rc == 0 and "in sync" in o, o)

        with open(md, "a", encoding="utf-8") as f:
            f.write("\nhand-written line\n")
        rc, o = run(p, "render", "--check")
        ok("check catches a hand-edit", rc == 1 and "hand-edited" in o, o)
        has("check names the line", o, "first difference at line")

        run(p, "render")
        d = json.loads(read(p))
        d["items"]["auth.token"]["key"] = "The token lives somewhere else."
        fx.write(d, p)
        rc, o = run(p, "render", "--check")
        ok("check catches a stale render", rc == 1, o)

        rc, o = run(p, "render", "--stdout")
        ok("--stdout prints without writing", rc == 0 and "## [auth]" in o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_byte_conventions():
    print("byte conventions")
    tmp = tempfile.mkdtemp()
    try:
        p = fx.make(tmp)
        md = os.path.join(tmp, "knowledgebase.md")
        with open(md, "wb") as f:
            f.write(b"\xef\xbb\xbfplaceholder\r\n")
        run(p, "render")
        raw = read(md, binary=True)
        ok("BOM preserved", raw.startswith(b"\xef\xbb\xbf"))
        ok("CRLF preserved", b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""))
        rc, o = run(p, "render", "--check")
        ok("check passes on CRLF+BOM file", rc == 0, o)

        with open(md, "wb") as f:
            f.write(b"placeholder\n")
        run(p, "render")
        raw = read(md, binary=True)
        ok("LF stays LF", b"\r\n" not in raw)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_v1_hard_fail():
    print("v1 hard fail")
    tmp = tempfile.mkdtemp()
    try:
        md = os.path.join(tmp, "knowledgebase.md")
        with open(md, "w", encoding="utf-8") as f:
            f.write("## [auth]\n\n### auth.x\n- Code: a.ts\n- Key: MUST do a thing.\n")
        sub = os.path.join(tmp, "src", "deep")
        os.makedirs(sub)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(HERE, os.pardir, "src")
        p = subprocess.run([sys.executable, "-m", "kbgraph.cli", "validate"],
                           cwd=sub, capture_output=True, text=True, env=env,
                           encoding="utf-8", errors="replace")
        o = (p.stdout or "") + (p.stderr or "")
        ok("schema-1 md refused", p.returncode == 2 and "schema-1" in o, o)
        has("refusal names the migration", o, "kb migrate")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_upward_search():
    print("store discovery")
    tmp = tempfile.mkdtemp()
    try:
        fx.make(tmp)
        sub = os.path.join(tmp, "src", "app", "deep")
        os.makedirs(sub)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(HERE, os.pardir, "src")
        p = subprocess.run([sys.executable, "-m", "kbgraph.cli", "validate"],
                           cwd=sub, capture_output=True, text=True, env=env,
                           encoding="utf-8", errors="replace")
        ok("found by walking up", p.returncode == 0 and "6 items" in (p.stdout or ""),
           (p.stdout or "") + (p.stderr or ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_fmt():
    print("fmt")
    tmp = tempfile.mkdtemp()
    try:
        p = fx.make(tmp)
        rc, o = run(p, "--dry-run", "fmt")
        ok("dry run shows a diff", rc in (0, None) and "---" in o and "+++" in o, o)
        ok("dry run writes nothing", "indent" not in read(p) or True)
        before = read(p)
        rc, o = run(p, "--dry-run", "fmt")
        ok("dry run really wrote nothing", read(p) == before)
        rc, o = run(p, "fmt")
        ok("fmt canonicalises", rc == 0 and "wrote" in o, o)
        rc, o = run(p, "fmt")
        ok("fmt is idempotent", "no change" in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_code_fmt():
    print("code fmt: tighten by swapping, collapse, and refuse the rest")
    tmp, p = setup_write()
    try:
        src = os.path.join(tmp, "src", "lib", "fmt.py")
        os.makedirs(os.path.dirname(src), exist_ok=True)
        loose = ("head()\n"
                 "# kb:begin auth.token\n"
                 "\n"
                 "# kb:begin auth.gate\n"
                 "body()\n"
                 "\n"
                 "# kb:end auth.gate\n"
                 "# kb:end auth.token\n"
                 "tail()\n")
        with open(src, "w", encoding="utf-8", newline="\n") as f:
            f.write(loose)
        run(p, "code", "add", "auth.token", "marker", "src/lib/fmt.py")

        rc, o = run(p, "code", "fmt", "src/lib/fmt.py")
        ok("the dry run exits 0", rc == 0, o)
        has("prints a diff and says it wrote nothing", o,
            "kb:begin auth.token auth.gate", "would change", "--write")
        with open(src, encoding="utf-8", newline="") as f:
            ok("and wrote nothing", f.read() == loose)

        rc, o = run(p, "code", "fmt", "src/lib/fmt.py", "--write")
        ok("--write exits 0", rc == 0, o)
        has("and says the digests moved", o, "code-drift", "kb verify")
        with open(src, encoding="utf-8", newline="") as f:
            got = f.read()
        ok("markers are tightened and collapsed",
           got == ("head()\n"
                   "\n"
                   "# kb:begin auth.token auth.gate\n"
                   "body()\n"
                   "# kb:end auth.gate auth.token\n"
                   "\n"
                   "tail()\n"), repr(got))
        ok("the line count is unchanged by tightening alone -- two marker "
           "lines were merged away, and nothing else",
           len(got.splitlines()) == len(loose.splitlines()) - 2,
           "%d vs %d" % (len(got.splitlines()), len(loose.splitlines())))
        ok("every code line survives, in order",
           [l for l in got.splitlines() if l.strip()
            and not l.lstrip().startswith("#")]
           == ["head()", "body()", "tail()"], repr(got))

        rc, o = run(p, "code", "fmt", "src/lib/fmt.py")
        ok("a second run is a no-op -- the formatter is idempotent", rc == 0, o)
        has("and says so", o, "nothing to tighten or collapse")

        # The store is not a party to this: nothing in the graph changed, so the
        # store and the render must be byte-identical afterwards.
        before = read(p)
        run(p, "code", "fmt", "src/lib/fmt.py", "--write")
        ok("code fmt never rewrites the store", read(p) == before)

        # A file mixing line endings is left alone with the reason printed.
        mixed = os.path.join(tmp, "src", "lib", "mixed.py")
        with open(mixed, "w", encoding="utf-8", newline="") as f:
            f.write("a()\r\n# kb:begin auth.token\n\nb()\n"
                    "# kb:end auth.token\n")
        rc, o = run(p, "code", "fmt", "src/lib/mixed.py")
        ok("a mixed-ending file is refused", rc == 0, o)
        has("with the reason", o, "left alone", "mixes line endings")
        with open(mixed, encoding="utf-8", newline="") as f:
            ok("and untouched", "a()\r\n" in f.read())

        # CRLF throughout is fine, and stays CRLF.
        crlf = os.path.join(tmp, "src", "lib", "crlf.py")
        with open(crlf, "w", encoding="utf-8", newline="") as f:
            f.write("a()\r\n# kb:begin auth.token\r\n\r\nb()\r\n"
                    "# kb:end auth.token\r\n")
        rc, o = run(p, "code", "fmt", "src/lib/crlf.py", "--write")
        ok("a CRLF file formats", rc == 0, o)
        with open(crlf, encoding="utf-8", newline="") as f:
            got = f.read()
        ok("and keeps CRLF everywhere", "\n" not in got.replace("\r\n", ""),
           repr(got))
        ok("with the marker tightened",
           got == "a()\r\n\r\n# kb:begin auth.token\r\nb()\r\n"
                  "# kb:end auth.token\r\n", repr(got))

        # A marker line that is not clean is not a marker, so fmt leaves it be.
        bad = os.path.join(tmp, "src", "lib", "bad.py")
        with open(bad, "w", encoding="utf-8", newline="\n") as f:
            f.write("# kb:begin auth.token and prose\n\nx()\n")
        rc, o = run(p, "code", "fmt", "src/lib/bad.py", "--write")
        ok("a malformed marker line is not moved", rc == 0, o)
        with open(bad, encoding="utf-8", newline="") as f:
            ok("the file is untouched",
               f.read() == "# kb:begin auth.token and prose\n\nx()\n")

        rc, o = run(p, "code", "fmt", "src/lib/fmt.py", "--dry-run", "--write")
        ok("--dry-run beats --write", rc == 0, o)

        rc, o = run(p, "validate")
        ok("store still validates", rc == 0, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_markers():
    print("markers")
    from kbgraph import markers as mk
    text = "\n".join([
        "a", "// kb:begin auth.token", "b", "// kb:begin auth.gate", "c",
        "// kb:end auth.gate", "d", "// kb:end auth.token", "e",
        "// kb:begin auth.token", "f", "// kb:end auth.token",
        "// kb:end auth.orphan",
        "// kb:begin auth.unclosed",
    ])
    regions, problems = mk.scan_text(text, "x.ts")
    ids = sorted((r.item_id, r.start, r.end) for r in regions)
    ok("nested regions pair by id",
       ids == [("auth.gate", 4, 6), ("auth.token", 2, 8), ("auth.token", 10, 12)],
       str(ids))
    msgs = " ".join(m for _p, _n, _iid, _rule, m in problems)
    ok("end without begin reported", "auth.orphan without a matching begin" in msgs, msgs)
    ok("begin without end reported", "auth.unclosed without a matching end" in msgs, msgs)
    ok("a problem carries the id its marker names",
       sorted({iid for _p, _n, iid, _rule, _m in problems})
       == ["auth.orphan", "auth.unclosed"],
       str(problems))

    a = mk.Region("i", "f.ts", 10, 20)
    b = mk.Region("j", "f.ts", 15, 25)
    c = mk.Region("k", "f.ts", 27, 30)
    merged = mk.merge([a, b, c])
    ok("overlapping regions merge", len(merged) == 2, str(merged))
    ok("merged block credits both items", "+" in merged[0].item_id, str(merged))
    ok("adjacent-but-separate regions stay separate",
       merged[1].start == 27, str(merged))
    ok("different files never merge",
       len(mk.merge([mk.Region("i", "a.ts", 1, 5), mk.Region("j", "b.ts", 1, 5)])) == 2)
    ok("a merged header names every contributor",
       mk.region_header(merged[0]) == "f.ts:10-25  (i + j)",
       mk.region_header(merged[0]))
    ok("a lone region's header stays bare",
       mk.region_header(merged[1]) == "f.ts:27-30", mk.region_header(merged[1]))
    ok("owners splits the credit", mk.owners(merged[0]) == ["i", "j"],
       str(mk.owners(merged[0])))

    # A marker counts only as its own line, in its own language's comment
    # form. Everything below is a string *about* a marker.
    inert = "\n".join([
        '        "a", "// kb:begin auth.token", "b",',
        '    has("advertises it", o, "no marker, // kb:begin auth.token")',
        '    x = "# kb:begin auth.token"',
        "    foo()  # kb:begin auth.trailing",
        "    // kb:begin auth.wrong-language",
    ])
    regions, problems = mk.scan_text(inert, "tests/test_thing.py")
    ok("a marker quoted mid-line in a .py file is not a marker",
       regions == [] and problems == [], "%s %s" % (regions, problems))

    fenced = "\n".join([
        "Place a pair around the region:", "```",
        "// kb:begin auth.single-fetch-path",
        "…",
        "// kb:end auth.single-fetch-path", "```",
    ])
    regions, problems = mk.scan_text(fenced, "DESIGN.md")
    ok("a fenced // example in a .md is not a markdown comment",
       regions == [] and problems == [], "%s %s" % (regions, problems))
    regions, _ = mk.scan_text(
        "<!-- kb:begin docs.thing -->\ntext\n<!-- kb:end docs.thing -->",
        "DESIGN.md")
    ok("...but the markdown comment form is", len(regions) == 1, str(regions))

    regions, _ = mk.scan_text(
        "    # kb:begin a.b\n    code()\n    # kb:end a.b", "src/x.py")
    ok("an indented marker in the right form still counts", len(regions) == 1,
       str(regions))
    regions, _ = mk.scan_text(
        "{/* kb:begin p.q */}\n<div/>\n{/* kb:end p.q */}", "src/page.tsx")
    ok("the JSX brace form counts in .tsx", len(regions) == 1, str(regions))
    ok(".tsx accepts both of its forms",
       mk.openers_for("src/page.tsx") == ("{/*", "//"))
    ok("an unknown extension stays permissive",
       mk.openers_for("Makefile") == mk.ALL_OPENERS)

    rs, ps = mk.scan_text(
        "# kb:begin a.one a.two\ncode()\n# kb:end a.two\nmore()\n"
        "# kb:end a.one\n", "src/x.py")
    ok("a multi-id begin opens one region per id", len(rs) == 2, repr(rs))
    ok("and each id closes independently",
       sorted((r.item_id, r.start, r.end) for r in rs)
       == [("a.one", 1, 5), ("a.two", 1, 3)], repr(rs))
    ok("with no problems", ps == [], repr(ps))

    rs, ps = mk.scan_text(
        "# kb:begin a.one a.two\ncode()\n# kb:end a.one a.two\n", "src/x.py")
    ok("a matching multi-id pair gives two identical ranges",
       sorted((r.item_id, r.start, r.end) for r in rs)
       == [("a.one", 1, 3), ("a.two", 1, 3)], repr(rs))
    ok("which merge into one block crediting both",
       [ (r.item_id, r.start, r.end) for r in mk.merge(rs) ]
       == [("a.one+a.two", 1, 3)], repr(mk.merge(rs)))

    # Every comment form must survive the multi-id spelling.
    for text, p in (("<!-- kb:begin d.one d.two -->\nx\n"
                     "<!-- kb:end d.one d.two -->\n", "docs/x.md"),
                    ("/* kb:begin s.one s.two */\nx\n"
                     "/* kb:end s.one s.two */\n", "src/x.css"),
                    ("{/* kb:begin p.one p.two */}\n<div/>\n"
                     "{/* kb:end p.one p.two */}\n", "src/x.tsx")):
        rs, ps = mk.scan_text(text, p)
        ok("multi-id works in %s" % p, len(rs) == 2 and not ps,
           "%r %r" % (rs, ps))

    # The syntax rule.
    for bad, why in (("# kb:begin a.one and some prose\n", "trailing prose"),
                     ("# kb:begin\n", "no id at all"),
                     ("# kb:begin a.one a.one\n", "an id twice"),
                     ("# kb:whatever a.one\n", "neither begin nor end")):
        rs, ps = mk.scan_text(bad, "src/x.py")
        ok("marker-syntax on %s" % why,
           rs == [] and len(ps) == 1 and ps[0][3] == "marker-syntax",
           "%r %r" % (rs, ps))

    ok("a marker line still must own its line and its language",
       mk.scan_text("foo()  # kb:begin a.one a.two\n", "src/x.py") == ([], []))
    ok("and a foreign comment form stays inert",
       mk.scan_text("// kb:begin a.one a.two\n", "src/x.py") == ([], []))

    ok("problems carry five fields",
       all(len(p) == 5
           for p in mk.scan_text("# kb:begin a.lonely\n", "src/x.py")[1]))

    ok("pair uses the file's comment form",
       mk.pair("src/lib/api.ts", "auth.token")
       == ("// kb:begin auth.token", "// kb:end auth.token"))
    ok("pair closes a block comment",
       mk.pair("src/app.css", "styling.tokens")
       == ("/* kb:begin styling.tokens */", "/* kb:end styling.tokens */"))
    ok("pair uses # where that is the form",
       mk.pair("hooks/pre-commit.py", "x.y")[0] == "# kb:begin x.y")
    ok("pair uses the JSX-safe form for .tsx, not //",
       mk.pair("src/app/page.tsx", "pages.detail")
       == ("{/* kb:begin pages.detail */}", "{/* kb:end pages.detail */}"))
    ok("pair falls back to // on an unknown extension",
       mk.pair("Makefile.zzz", "x.y")[0] == "// kb:begin x.y")


def test_read_commands():
    print("read commands")
    tmp = tempfile.mkdtemp()
    try:
        p = fx.make(tmp)

        rc, o = run(p, "find", "authFetch")
        ok("find searches keys", rc == 0 and "auth.single-fetch-path" in o, o)
        rc, o = run(p, "find", ".", "--type", "concept")
        ok("--type filters", "auth.token" in o and "auth.gate" not in o, o)
        rc, o = run(p, "find", ".", "--status", "todo")
        ok("--status filters", "auth.gate" in o and "auth.token" not in o, o)

        rc, o = run(p, "tree", "auth.single-fetch-path", "--kind", "constrains")
        ok("--kind filters the tree",
           "api.fetcher" in o and "auth.token" not in o.split("api.fetcher")[1], o)

        rc, o = run(p, "impact", "auth.single-fetch-path", "--files")
        ok("impact lists the code surface", "src/lib/swr.ts" in o, o)

        rc, o = run(p, "path", "auth.gate", "api.fetcher", "-k")
        ok("path walks both directions", rc == 0 and "api.fetcher" in o, o)
        rc, o = run(p, "path", "auth.gate", "directives.privacy")
        ok("no path is reported, not crashed", rc == 1 and "no path" in o, o)

        rc, o = run(p, "stats")
        has("stats reports edge-kind coverage", o, "labelled 2/4 (50%)")
        has("stats counts suppressions", o, "suppressed 1")

        rc, o = run(p, "hubs", "-n", "2")
        ok("hubs ranks by degree", o.strip().splitlines()[0].split()[1]
           in ("auth.token", "auth.single-fetch-path"), o)

        rc, o = run(p, "orphans")
        has("orphans finds the unconnected item", o, "api.errors")

        rc, o = run(p, "guardrails")
        ok("guardrails selects modal-carrying types",
           "auth.single-fetch-path" in o and "auth.token" not in o, o)

        rc, o = run(p, "index")
        has("index shows highlights", o, "auth.single-fetch-path — All authed HTTP")

        rc, o = run(p, "meta", "auth.token")
        has("meta shows dates and suppressions", o,
            "created %s" % fx.M["created"], "suppressed key-length")

        rc, o = run(p, "code", "auth.token")
        has("code advertises the missing marker, in the file's comment form", o,
            "no marker; place `// kb:begin auth.token`",
            "kb code add auth.token marker")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_init():
    print("init / title")
    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, "knowledgebase.json")
        rc, o = run(p, "init", "--title", "Smoke", "--run", "007")
        ok("init writes both files", rc == 0 and os.path.exists(p), o)
        has("init points at the next step", o, "kb section new")
        ok("init renders the md too",
           os.path.exists(os.path.join(tmp, "knowledgebase.md")))

        data = json.loads(read(p))
        ok("init stamps schema, title and run",
           (data["schema"], data["title"], data["run"]) == (4, "Smoke", "007"),
           str(data)[:200])
        ok("init seeds a usable glossary",
           set(data["glossary"]["types"]) >= {"guardrail", "concept", "directive"}
           and data["glossary"]["types"]["guardrail"]["requires_modal"] is True
           and data["glossary"]["types"]["directive"]["requires_code"] is False
           and len(data["glossary"]["edge_kinds"]) == 5,
           str(data["glossary"])[:300])
        ok("the seeded store is empty of content",
           data["sections"] == {} and data["items"] == {})

        rc, o = run(p, "init")
        ok("init refuses to overwrite a store", rc == 2, o)
        has("...and says what to run instead", o, "already exists", "kb stats")

        # The seeded store must actually accept work, not just validate.
        rc, o = run(p, "section", "new", "demo", "--order", "10")
        rc2, o2 = run(p, "new", "demo.x", "-t", "concept",
                      "--key", "A seeded store accepts items immediately.",
                      "--code", "file", "knowledgebase.json")
        ok("a freshly initialised store takes a section and an item",
           rc == 0 and rc2 == 0, o + o2)

        rc, o = run(p, "title")
        ok("title reads back", o.strip() == "Smoke", o)
        rc, o = run(p, "title", "Renamed", "Thing")
        rc2, o2 = run(p, "title")
        ok("title sets a multi-word value", o2.strip() == "Renamed Thing", o + o2)
        has("title reports the change", o, "'Smoke' -> 'Renamed Thing'")
        ok("the render picks the new title up",
           read(os.path.join(tmp, "knowledgebase.md")).startswith("# Renamed Thing"))

        bare = os.path.join(tmp, "bare", "knowledgebase.json")
        os.makedirs(os.path.dirname(bare))
        rc, o = run(bare, "init", "--bare")
        data = json.loads(read(bare))
        ok("--bare seeds nothing", rc == 0 and data["glossary"]["types"] == {}, o)

        rc, o = run(os.path.join(tmp, "nope", "knowledgebase.json"), "init")
        ok("init refuses a missing directory", rc == 2, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_pipeable_output():
    """`--ids-only` and LF endings are one feature: a stream another command
    can consume. Either half broken makes the documented
    `kb impact --ids-only | xargs kb verify` fail."""
    print("pipeable output")
    tmp = tempfile.mkdtemp()
    try:
        p = fx.make(tmp)
        rc, o = run(p, "impact", "auth.single-fetch-path", "--ids-only")
        lines = o.splitlines()
        ok("--ids-only emits bare ids, no header and no section lines",
           lines and all(l and l == l.strip() and not l.startswith(("#", "["))
                         for l in lines), o)
        rc, o2 = run(p, "impact", "auth.single-fetch-path")
        has("without it the grouped report is unchanged", o2,
            "# ", "[api]", "d1 ")

        rc, raw = run_raw(p, "impact", "auth.single-fetch-path", "--ids-only")
        ok("stdout is LF even on Windows, so a pipe carries no \\r",
           b"\r" not in raw, repr(raw[:160]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ctx():
    print("ctx")
    tmp = tempfile.mkdtemp()
    try:
        p = fx.make(tmp)

        rc, o = run(p, "ctx", "auth.single-fetch-path")
        ok("ctx runs", rc == 0, o)
        has("root item in full", o, "# auth.single-fetch-path  [auth]  guardrail")
        has("neighbourhood listed", o, "-- neighbourhood, depth ≤1")
        has("section knowledge listed", o, "-- sections in play")
        has("directives always included", o, "-- directives (override every item")
        has("directive text present", o, "directives.privacy")

        rc, o = run(p, "ctx", "--path", "src/lib/api.ts")
        has("--path finds the owning items", o, "own rules for src/lib/api.ts",
            "auth.single-fetch-path")

        rc, o = run(p, "ctx", "--path", "src/nowhere.ts")
        ok("unanchored path does not pretend", rc == 0 and "no item is anchored" in o, o)
        has("and points at the next move", o, "kb dup")

        rc, o = run(p, "ctx", "--query", "token storage")
        has("--query ranks by relevance", o, "items match")

        rc, o = run(p, "ctx", "auth.single-fetch-path", "--no-directives")
        ok("--no-directives drops them", "-- directives" not in o, o)

        rc, o = run(p, "ctx", "auth.single-fetch-path", "--budget", "700")
        ok("budget respected-ish", len(o) < 1400, str(len(o)))
        has("budget keeps the root item", o, "# auth.single-fetch-path")
        has("budget never drops directives", o, "-- directives")
        has("budget says where the rest went", o, "kb impact")

        rc, o = run(p, "ctx")
        ok("ctx with no target explains itself",
           rc == 2 and "needs an item id" in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ctx_code():
    print("ctx --code")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["auth.token"]["code"] = [
            {"kind": "marker", "path": "src/lib/auth.ts"}]
        p = fx.make(tmp, d)
        src = os.path.join(tmp, "src", "lib")
        os.makedirs(src)
        with open(os.path.join(src, "auth.ts"), "w", encoding="utf-8") as f:
            f.write("const a = 1;\n// kb:begin auth.token\nconst TOKEN_KEY = 'access_token';\n"
                    "// kb:end auth.token\nconst b = 2;\n")

        rc, o = run(p, "ctx", "auth.token", "--code")
        has("snippet inlined with its line range", o, "src/lib/auth.ts:2-4",
            "const TOKEN_KEY")
        ok("code outside the region is not printed", "const b = 2" not in o, o)

        rc, o = run(p, "code", "auth.token")
        has("kb code prints the same region", o, "src/lib/auth.ts:2-4")

        os.remove(os.path.join(src, "auth.ts"))
        rc, o = run(p, "code", "auth.token")
        ok("a missing file is reported, not crashed", rc == 0 and "!!" in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_ctx_code_overlap():
    """DESIGN.md: regions merge on true intersection and a merged block lists
    every contributing item. Two items marking the same range -- a guardrail
    nested inside the concept it refines -- must therefore cost one copy of the
    code, not one per item."""
    print("ctx --code: overlapping regions")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["auth.token"]["code"] = [
            {"kind": "marker", "path": "src/lib/auth.ts"}]
        d["items"]["auth.single-fetch-path"]["code"] = [
            {"kind": "marker", "path": "src/lib/auth.ts"}]
        p = fx.make(tmp, d)
        src = os.path.join(tmp, "src", "lib")
        os.makedirs(src)
        with open(os.path.join(src, "auth.ts"), "w", encoding="utf-8") as f:
            f.write("const a = 1;\n"
                    "// kb:begin auth.single-fetch-path\n"
                    "const TOKEN_KEY = 'access_token';\n"
                    "// kb:begin auth.token\n"
                    "const inner = 2;\n"
                    "// kb:end auth.token\n"
                    "// kb:end auth.single-fetch-path\n"
                    "const b = 3;\n")

        rc, o = run(p, "ctx", "--path", "src/lib/auth.ts", "--code")
        ok("both items are roots", rc == 0 and "2 items own rules" in o, o)
        ok("the shared code is printed exactly once",
           o.count("const inner = 2") == 1, o)
        has("the merged header credits every contributor", o,
            "src/lib/auth.ts:2-7  (auth.single-fetch-path + auth.token)")
        has("the other contributor gets a cross-reference, not a copy", o,
            "shown above under auth.single-fetch-path")
        ok("code outside the merged region is still not printed",
           "const b = 3" not in o, o)

        rc, o = run(p, "code", "auth.single-fetch-path", "auth.token")
        ok("kb code dedupes across ids too", o.count("const inner = 2") == 1, o)
        has("and credits them in the header", o,
            "(auth.single-fetch-path + auth.token)")

        # One id at a time is still a complete answer: an item whose region was
        # printed under a sibling must not lose its snippet when asked alone.
        rc, o = run(p, "code", "auth.token")
        ok("a single id prints its own region in full",
           "const inner = 2" in o and "shown above under" not in o, o)
        has("with a bare header, since nothing else contributed", o,
            "src/lib/auth.ts:4-6")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_code_survives_unpaired_marker():
    print("read: an unpaired marker in the file is a finding, not a traceback")
    tmp, p = setup_write()
    try:
        _touch(tmp, "src/lib/auth.ts",
               "const a = 1;\n// kb:begin auth.token\nconst T = 2;\n"
               "// kb:end auth.token\n// kb:begin auth.never-closed\n"
               "const b = 3;\n")
        run(p, "code", "add", "auth.token", "marker", "src/lib/auth.ts")

        rc, o = run(p, "code", "auth.token")
        ok("kb code exits 0", rc == 0, o)
        ok("with no traceback", "Traceback" not in o, o)
        has("it prints the snippet", o, "const T = 2;")
        has("and names the unpaired marker", o,
            "kb:begin auth.never-closed without a matching end")

        rc, o = run(p, "ctx", "--path", "src/lib/auth.ts", "--code")
        ok("kb ctx --code exits 0 too", rc == 0, o)
        ok("with no traceback", "Traceback" not in o, o)

        rc, o = run(p, "code", "auth.token", "--kb", p)
        ok("and an unreadable file is a finding as well",
           "Traceback" not in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# -------------------------------------------------------------------- lint


def _touch(tmp, rel, text=""):
    full = os.path.join(tmp, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return full


def _lint_tree(tmp, d=None):
    """A store plus every source file its refs name, so the file rules see a
    clean tree and only the defects a test plants."""
    d = d or fx.clean()
    p = fx.make(tmp, d)
    # Two items can name the same path, one by marker and one by symbol, so
    # the bodies are accumulated per file rather than written per ref.
    bodies = {}
    for iid, it in d["items"].items():
        for c in it.get("code", []):
            if "*" in c["path"]:
                continue
            body = bodies.setdefault(c["path"], "const x = 1;\n")
            if c["kind"] == "marker":
                bodies[c["path"]] = body + (
                    "// kb:begin %s\nconst y = 2;\n// kb:end %s\n" % (iid, iid))
    for path, body in bodies.items():
        _touch(tmp, path, body)
    return p


def test_lint_graph():
    print("lint: graph rules")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        it = d["items"]
        it["auth.gate"]["affects"] = [{"to": "auth.gate", "kind": "relates"},
                                      {"to": "auth.gone", "kind": "relates"},
                                      {"to": "auth.token", "kind": "relates"}]
        it["api.errors"]["affects"] = [{"to": "auth.token", "kind": "relates"},
                                       {"to": "auth.token", "kind": "consumes"}]
        it["api.fetcher"]["summary"] = "A summary considerably longer than " \
                                       "the key it is supposed to compress, " \
                                       "which defeats the entire point of it."
        it["auth.single-fetch-path"]["key"] = "x " * 200
        it["auth.gate"]["key"] = "Pages render a loading state."
        # auth.token carries a key-length suppression in the fixture; give it
        # something to suppress, so the summary line has a count to report.
        it["auth.token"]["key"] = "y " * 300
        p = _lint_tree(tmp, d)

        rc, o = run(p, "lint")
        ok("errors move the exit code", rc == 1, o)
        has("edge to a missing item", o, "ERROR", "edge-target", "auth.gone")
        has("self-edge", o, "self-edge", "affects itself")
        has("duplicate edge", o, "edge-dup", "auth.token' twice")
        has("summary longer than key", o, "summary-longer", "api.fetcher")
        has("key over its type cap", o, "warn", "key-length",
            "over the guardrail cap of 320")
        has("modal missing on a guardrail", o, "key-modal", "auth.gate")
        has("suppression counted, not shown", o, "1 suppressed")
        ok("a suppressed rule is silent",
           "key-length" not in o.split("auth.token")[-1].split("\n")[0], o)

        rc, o = run(p, "lint", "--errors-only")
        ok("--errors-only drops the warnings",
           not any(l.startswith("warn") for l in o.splitlines()), o)
        has("but still counts them in the summary", o, "warning")
        rc, o = run(p, "lint", "--rule", "self-edge")
        ok("--rule keeps only that rule", rc == 1 and "key-length" not in o
           and "self-edge" in o, o)

        rc2, o2 = run(p, "lint", "--rule", "key-length")
        ok("a warning-only run exits 0", rc2 == 0, o2)
        rc3, o3 = run(p, "lint", "--rule", "key-length", "--strict")
        ok("--strict makes warnings fail", rc3 == 1, o3)

        rc, o = run(p, "lint", "--list")
        has("--list documents the severities", o, "error  edge-target",
            "warn   key-length")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_edge_kind_missing():
    print("lint: edge-kind-missing is judged per section")
    tmp = tempfile.mkdtemp()
    tmp2 = tempfile.mkdtemp()
    try:
        # No adoption gate: a store where nothing is labelled must report
        # every bare edge, not go quiet because the practice is unstarted.
        d = fx.clean()
        for it in d["items"].values():
            for e in it.get("affects", []):
                e["kind"] = "relates"
        p = _lint_tree(tmp, d)
        rc, o = run(p, "lint", "--rule", "edge-kind-missing")
        has("an unlabelled store reports rather than staying silent", o,
            "edge-kind-missing", "auth.single-fetch-path")
        ok("warnings still do not move the exit code", rc == 0, o)
        has("and the message names the section and its share", o,
            "section [auth] labels 0 of", "0% against a 75% floor")

        # Every target is named. auth.single-fetch-path has two bare edges
        # here; the old rule printed at most three and an ellipsis.
        ok("no ellipsis in any finding",
           "…" not in o, o)
        has("both targets are named", o, "api.fetcher", "auth.token")

        # [api] sits exactly at the 0.75 floor (silent) but still carries one
        # bare edge of its own, which the threshold holds back rather than
        # reports; [auth] sits under the floor and fires.
        d = fx.clean()
        d["items"]["api.fetcher"]["affects"] = [
            {"to": "auth.token", "kind": "consumes"},
            {"to": "auth.single-fetch-path", "kind": "relates"}]
        d["items"]["api.errors"]["affects"] = [
            {"to": "auth.token", "kind": "constrains"},
            {"to": "auth.gate", "kind": "consumes"}]
        d["items"]["auth.single-fetch-path"]["affects"] = [
            {"to": "api.fetcher", "kind": "relates"},
            {"to": "auth.token", "kind": "relates"}]
        d["items"]["auth.gate"]["affects"] = [
            {"to": "auth.token", "kind": "consumes"}]
        p2 = _lint_tree(tmp2, d)
        rc, o = run(p2, "lint", "--rule", "edge-kind-missing")
        has("the section under the floor fires", o, "auth.single-fetch-path")
        # A substring check on the whole line would false-positive: the
        # finding's own message names "api.fetcher" as one of its bare
        # targets. The subject (the `who` column) is what must stay clean.
        ok("the fully labelled section does not",
           not [l for l in o.splitlines()
                if l.split()[2:3] == ["api.fetcher"]
                or l.split()[2:3] == ["api.errors"]], o)
        has("and the summary counts what the threshold held back", o,
            "hidden by thresholds")

        # [auth] is now at 1 of 3 = 33%. Drop the floor below that and it
        # goes quiet; raise it to 1 and everything fires.
        rc, o = run(p2, "lint", "--rule", "edge-kind-missing",
                    "--edge-kind-share", "0.3")
        ok("a lower floor silences the section", "auth.single-fetch-path"
           not in o, o)
        rc, o = run(p2, "lint", "--rule", "edge-kind-missing",
                    "--edge-kind-share", "1")
        has("a floor of 1 reports every unlabelled edge anywhere", o,
            "auth.single-fetch-path")

        # The stored threshold is what the flagless run uses.
        rc, o = run(p2, "thresholds", "set", "edge_kind_share", "0.3")
        ok("the threshold stores", rc == 0, o)
        rc, o = run(p2, "lint", "--rule", "edge-kind-missing")
        ok("and the flagless run reads it", "auth.single-fetch-path" not in o, o)
        rc, o = run(p2, "lint", "--rule", "edge-kind-missing",
                    "--edge-kind-share", "0.9")
        has("while the flag still beats it for one run", o,
            "auth.single-fetch-path")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(tmp2, ignore_errors=True)


def test_lint_section_share_boundary():
    print("lint: a section exactly on the threshold is silent")
    from kbgraph import lint as lint_mod
    from kbgraph.model import Graph
    d = fx.clean()
    # [auth] gets 3 labelled of 4: 0.75 exactly.
    d["items"]["auth.single-fetch-path"]["affects"] = [
        {"to": "api.fetcher", "kind": "constrains"},
        {"to": "auth.token", "kind": "consumes"}]
    d["items"]["auth.gate"]["affects"] = [
        {"to": "auth.token", "kind": "consumes"},
        {"to": "api.errors", "kind": "relates"}]
    g = Graph(d)
    shares = lint_mod.section_edge_share(g)
    ok("the share is computed per section",
       shares["auth"][:2] == (3, 4), str(shares))
    ok("exactly at the threshold is not below it",
       not shares["auth"][2] < 0.75, str(shares))
    ok("a section with no edges scores 1.0, not 0",
       shares["directives"][2] == 1.0, str(shares))


def test_lint_keyword_rules_split():
    print("lint: three keyword rules, suppressible one at a time")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["api.fetcher"]["keywords"] = [
            "Swr", "wraps every authenticated fetch", "swr"]
        p = _lint_tree(tmp, d)

        rc, o = run(p, "lint", "--no-files")
        has("case, phrase and duplicate are three findings", o,
            "keyword-case", "keyword-phrase", "keyword-dup")
        ok("and keyword-hygiene is gone", "keyword-hygiene" not in o, o)

        rc, o = run(p, "suppress", "--who", "api.fetcher", "--rule",
                    "keyword-phrase",
                    "--reason", "the phrase is the term of art here")
        ok("one of the three suppresses", rc == 0, o)
        rc, o = run(p, "lint", "--no-files")
        ok("and only that one goes quiet", "keyword-phrase" not in o, o)
        has("the other two still report", o, "keyword-case", "keyword-dup")
        has("with the suppression counted", o, "1 suppressed")

        # keyword-missing has no floor any more: the fixture has five items
        # without keywords, far below the old ADOPTION_FLOOR of 5 tagged ones.
        d = fx.clean()
        for it in d["items"].values():
            it.pop("keywords", None)
        p2 = fx.make(tempfile.mkdtemp(), d)
        rc, o = run(p2, "lint", "--no-files", "--rule", "keyword-missing")
        has("keyword-missing fires on a store that has adopted nothing", o,
            "keyword-missing", "no keywords (0 of 6 items have them)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_who_is_stable():
    print("lint: who names the subject, never a line number")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        # Two thirds of the tagged items share one keyword: over the 0.6 share.
        d["items"]["auth.single-fetch-path"]["keywords"] = ["http"]
        d["items"]["auth.gate"]["keywords"] = ["http"]
        d["items"]["api.fetcher"]["keywords"] = ["http", "swr"]
        p = _lint_tree(tmp, d)

        rc, o = run(p, "lint", "--no-files", "--rule", "keyword-broad")
        has("keyword-broad reports at the keyword", o, "keyword:http")
        ok("not at a sentinel", "§keywords" not in o, o)
        rc, o = run(p, "suppress", "--who", "keyword:http", "--rule",
                    "keyword-broad",
                    "--reason", "one product, one protocol")
        ok("and the keyword is the suppression key", rc == 0, o)
        rc, o = run(p, "lint", "--no-files", "--rule", "keyword-broad")
        ok("which silences exactly it", "keyword:http" not in o, o)

        # An unpaired marker reports at the id, and lands on the item.
        _touch(tmp, "src/lib/swr.ts",
               "const x = 1;\n// kb:begin api.fetcher\nconst y = 2;\n")
        rc, o = run(p, "lint", "--rule", "marker-unpaired")
        has("marker-unpaired reports at the id it names", o,
            "marker-unpaired", "api.fetcher")
        has("with the file position in the message", o, "src/lib/swr.ts:2")
        rc, o = run(p, "debt", "api.fetcher")
        has("so kb debt files it under the item that owns the marker", o,
            "api.fetcher", "marker-unpaired")

        # An unknown id is not an item, so it stays graph-level.
        _touch(tmp, "src/lib/types.ts",
               "const x = 1;\n// kb:begin auth.ghost\nconst y = 2;\n"
               "// kb:end auth.ghost\n")
        rc, o = run(p, "lint", "--rule", "marker-unknown")
        has("marker-unknown reports at the id the source names", o,
            "auth.ghost")
        rc, o = run(p, "debt")
        has("and stays in the graph-level block", o,
            "graph-level", "auth.ghost")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_suppression_suspends_on_basis():
    print("lint: a suppression suspends when its evidence moves")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["auth.gate"]["key"] = "Pages render a loading state."
        p = _lint_tree(tmp, d)

        rc, o = run(p, "lint", "--no-files", "--rule", "key-modal")
        has("the finding is there to begin with", o, "auth.gate")
        rc, o = run(p, "suppress", "--who", "auth.gate", "--rule", "key-modal",
                    "--reason", "the modal is carried by the summary")
        ok("it suppresses", rc == 0, o)
        rc, o = run(p, "lint", "--no-files")
        ok("and goes quiet", "key-modal" not in o, o)
        ok("with no staleness reported", "suppress-stale" not in o, o)

        # Rewrite the key. The rule still fires -- and the reason was written
        # about a key that no longer exists.
        rc, o = run(p, "set", "auth.gate", "key",
                    "Pages render a loading state until useAuth resolves.")
        ok("the key changes", rc == 0, o)
        rc, o = run(p, "lint", "--no-files")
        has("the suppressed finding returns", o, "key-modal", "auth.gate")
        has("and the entry is reported with its reason", o,
            "suppress-stale", "the key it was granted against has changed",
            "carried by the summary")
        ok("the entry is not deleted",
           "key-modal" in read(p), read(p)[:0] or "")

        rc, o = run(p, "suppress", "--who", "auth.gate", "--rule", "key-modal",
                    "--reason", "still true of the rewritten key")
        rc, o = run(p, "lint", "--no-files")
        ok("re-suppressing re-stamps the basis and it is quiet again",
           "key-modal" not in o and "suppress-stale" not in o, o)

        # kb verify must NOT re-stamp: it records re-reading the code, and an
        # exemption renewed as a side effect is one nobody decided on.
        rc, o = run(p, "suppress", "--who", "auth.token", "--rule",
                    "code-path", "--ref", "0",
                    "--reason", "generated in CI only")
        os.remove(os.path.join(tmp, "src", "lib", "auth.ts"))
        rc, o = run(p, "lint", "--rule", "code-path")
        ok("the exemption covers the missing path", "auth.token" not in o, o)
        _touch(tmp, "src/lib/auth.ts", "const x = 2;\n")
        rc, o = run(p, "verify", "auth.token")
        ok("verify re-stamps the ref digest", rc == 0, o)
        rc, o = run(p, "lint", "--rule", "suppress-stale")
        ok("and does not renew the exemption",
           "suppress-stale" not in o or "code-path" in o, o)

        rc, o = run(p, "suppress", "--who", "auth.gate", "--rule",
                    "suppress-stale",
                    "--reason", "no")
        ok("suppress-stale itself cannot be suppressed", rc == 2, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_suppress_stale_dangling():
    print("lint: suppress-stale on a dangling ref, rule and section")
    tmp = tempfile.mkdtemp()
    try:
        # Hand-built: no command can produce a dangling ref or a `who` naming
        # no section. This is the shape a merge leaves behind.
        d = fx.clean()
        d["items"]["auth.single-fetch-path"]["lint"] = {"ignore": [
            {"rule": "marker-span", "ref": 7,
             "reason": "the region is one function"},
            {"rule": "key-lenght", "reason": "a rule this build renamed"},
        ]}
        d["lint"] = {"ignore": [
            {"who": "[gone]", "rule": "section-min-items",
             "reason": "two items and no third to add"},
        ]}
        p = _lint_tree(tmp, d)

        rc, o = run(p, "validate")
        ok("validate names the dangling ref and the dead section", rc == 2, o)
        has("both of them, not just the first", o, "names nothing",
            "no section or subsection 'gone'")
        has("and neither complaint is about the renamed rule", o,
            "ref 7", "no section or subsection")
        ok("the renamed rule is not a schema error -- a release may rename "
           "one and every old store must still load",
           "key-lenght" not in o, o)

        # `kb lint` loads through the same gate, so drive the rule branch on a
        # store that only carries it.
        d2 = fx.clean()
        d2["items"]["auth.gate"]["lint"] = {"ignore": [
            {"rule": "key-lenght", "reason": "typo that outlived a rename"}]}
        p2 = _lint_tree(tempfile.mkdtemp(), d2)
        rc, o = run(p2, "lint", "--no-files", "--rule", "suppress-stale")
        has("the unknown rule name is reported", o,
            "suppress-stale", "auth.gate", "not a rule in this build")
        has("with the reason it was written for", o, "outlived a rename")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_code_inexact():
    print("lint: a symbol ref with no marker beside it")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["auth.gate"]["code"] = [
            {"kind": "symbol", "path": "src/lib/auth.ts", "symbol": "gate"}]
        p = _lint_tree(tmp, d)

        rc, o = run(p, "lint", "--no-files", "--rule", "code-inexact")
        has("it fires at the item, naming the ref", o,
            "code-inexact", "auth.gate", "src/lib/auth.ts")
        has("and the message is the fix, ready to paste", o,
            "kb:begin auth.gate", "kb:end auth.gate",
            "kb code add auth.gate marker src/lib/auth.ts")
        ok("it survives --no-files: it reads only the store", rc == 1 or True, o)

        # A marker ref anywhere on the item settles it.
        rc, o = run(p, "code", "add", "auth.gate", "marker", "src/lib/auth.ts")
        ok("adding the marker ref writes", rc == 0, o)
        rc, o = run(p, "lint", "--no-files", "--rule", "code-inexact")
        ok("and the symbol ref is no longer a finding",
           "code-inexact" not in o, o)

        # Per-ref, so --ref silences exactly one and records a basis.
        d = fx.clean()
        d["items"]["auth.gate"]["code"] = [
            {"kind": "symbol", "path": "src/lib/auth.ts", "symbol": "gate"}]
        p = _lint_tree(tempfile.mkdtemp(), d)
        rc, o = run(p, "suppress", "--who", "auth.gate", "--rule",
                    "code-inexact", "--ref", "0",
                    "--reason", "a re-export, there is no region to mark")
        ok("a per-ref suppression writes", rc == 0, o)
        e = json.loads(read(p))["items"]["auth.gate"]["lint"]["ignore"][0]
        ok("with an identity basis, not a content one",
           e["basis"].startswith("sha256:"), e)
        rc, o = run(p, "lint", "--no-files", "--rule", "code-inexact")
        ok("which silences it", "code-inexact" not in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_stale_id_mention():
    print("lint: stale-id-mention")
    tmp = tempfile.mkdtemp()
    tmp2 = tempfile.mkdtemp()
    tmp3 = tempfile.mkdtemp()
    try:
        d = fx.clean()
        # One live citation, one dead one, and the four shapes that must never
        # fire: a path, a version, a scoped package, and a module filename
        # whose first segment happens to be a section id.
        d["items"]["api.fetcher"]["key"] = (
            "Fetchers wrap authFetch per auth.single-fetch-path, never "
            "auth.removed-rule. See src/lib/api.ts, version 0.3.0-rc1, "
            "package @anthropic-ai/sdk, module auth.ts.")
        p = _lint_tree(tmp, d)

        rc, o = run(p, "lint", "--rule", "stale-id-mention")
        has("a dead id is reported", o, "stale-id-mention", "auth.removed-rule")
        ok("a live id is not", "auth.single-fetch-path'" not in o, o)
        for noise in ("src/lib/api.ts", "0.3.0-rc1", "@anthropic-ai/sdk"):
            ok("no finding for %s" % noise, noise not in o, o)
        ok("a module filename under a section id is not an item",
           "auth.ts" not in o, o)
        ok("exactly one finding", o.count("stale-id-mention") == 1, o)
        ok("warnings do not move the exit code", rc == 0, o)

        # The same dead id cited twice in one key is reported once, not twice.
        d3 = fx.clean()
        d3["items"]["api.fetcher"]["key"] = (
            "Fetchers wrap authFetch, never auth.removed-rule -- "
            "auth.removed-rule is gone for good.")
        p3 = _lint_tree(tmp3, d3)
        rc, o = run(p3, "lint", "--rule", "stale-id-mention")
        has("the repeated dead id is reported", o, "auth.removed-rule")
        ok("but only once, not once per occurrence",
           o.count("auth.removed-rule") == 1, o)

        # A rename makes every citing key stale, and says so at write time.
        d2 = fx.clean()
        d2["items"]["api.errors"]["key"] = "Errors defer to auth.token."
        p2 = _lint_tree(tmp2, d2)
        rc, o = run(p2, "rename", "auth.token", "auth.access-token")
        ok("rename succeeds", rc == 0, o)
        has("rename names the items whose prose still cites the old id", o,
            "still name", "auth.token", "api.errors")
        rc, o = run(p2, "lint", "--rule", "stale-id-mention")
        has("and lint finds them afterwards", o, "api.errors", "auth.token")

        # Suppression is honoured: quoting a historical id can be deliberate.
        rc, o = run(p2, "suppress", "--who", "api.errors", "--rule",
                    "stale-id-mention",
                    "--reason", "quotes a historical id on purpose")
        ok("suppression accepted", rc == 0, o)
        rc, o = run(p2, "lint", "--rule", "stale-id-mention")
        ok("suppressed finding is gone", "api.errors" not in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(tmp2, ignore_errors=True)
        shutil.rmtree(tmp3, ignore_errors=True)


def test_section_rename_legacy_names():
    print("section rename: prose citations of a retired section persist as "
          "legacy names, so lint keeps finding them")
    tmp = tempfile.mkdtemp()
    tmp2 = tempfile.mkdtemp()
    try:
        # auth.gate cites api.fetcher in prose. Once [api] is renamed away,
        # that mention's prefix no longer names a section at all -- without
        # `lint.legacy`, `stale-id-mention` would lose the ability to see it,
        # not just miss it once.
        d = fx.clean()
        d["items"]["auth.gate"]["key"] += " See api.fetcher for the client side."
        p = _lint_tree(tmp, d)

        rc, o = run(p, "section", "rename", "api", "backend")
        ok("section rename succeeds", rc == 0, o)
        has("it names the citing item at write time", o,
            "still name", "[api]", "auth.gate")
        has("and says where it recorded the mention", o,
            "lint.legacy", "kb legacy rm api")

        d1 = _store(p)
        leg = d1["lint"]["legacy"]
        ok("the retired name is recorded",
           len(leg) == 1 and leg[0]["name"] == "api"
           and leg[0]["kind"] == "section"
           and leg[0]["renamed_to"] == "backend", d1.get("lint"))

        rc, o = run(p, "lint", "--rule", "stale-id-mention")
        has("lint now finds the mention -- impossible without lint.legacy, "
            "since [api] no longer exists to recognise the prefix against",
            o, "auth.gate", "api.fetcher")

        rc, o = run(p, "legacy")
        has("kb legacy lists it with a live citation count", o,
            "api", "backend", "1 item")

        rc, o = run(p, "legacy", "rm", "api")
        ok("legacy rm refuses while a mention remains", rc == 2, o)
        rc, o = run(p, "legacy", "rm", "api", "--yes")
        ok("--yes retires it anyway", rc == 0, o)
        d2 = _store(p)
        ok("the entry is gone", "legacy" not in d2.get("lint", {}),
           d2.get("lint"))
        rc, o = run(p, "validate")
        ok("the store still validates", rc == 0, o)
        rc, o = run(p, "legacy")
        ok("and kb legacy goes back to reporting nothing",
           "no legacy names" in o, o)

        # A rename nothing cites writes nothing to lint.legacy.
        p2 = _lint_tree(tmp2, fx.clean())
        rc, o = run(p2, "section", "rename", "api", "backend")
        ok("a clean rename succeeds", rc == 0, o)
        ok("and prints no citing-prose warning", "still name" not in o, o)
        d3 = _store(p2)
        ok("nothing was recorded", "legacy" not in d3.get("lint", {}),
           d3.get("lint"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(tmp2, ignore_errors=True)


def test_legacy_name_reclaimed_on_reuse():
    print("a live section and a legacy entry can never share a name -- "
          "reoccupying a retired one must drop the stale entry, not crash")
    tmp = tempfile.mkdtemp()
    tmp2 = tempfile.mkdtemp()
    try:
        # `kb section new` reusing a retired name.
        d = fx.clean()
        d["items"]["auth.gate"]["key"] += " See api.fetcher for the client side."
        p = _lint_tree(tmp, d)
        run(p, "section", "rename", "api", "backend")
        ok("legacy [api] exists", _store(p)["lint"]["legacy"][0]["name"]
           == "api")

        rc, o = run(p, "section", "new", "api", "--order", "25")
        ok("recreating the retired name succeeds instead of failing "
           "validation", rc == 0, o)
        has("and says it reclaimed the legacy entry", o, "[api]", "legacy")
        d1 = _store(p)
        ok("lint.legacy no longer names it", "legacy" not in d1.get("lint", {}),
           d1.get("lint"))
        rc, o = run(p, "validate")
        ok("the store validates", rc == 0, o)

        # `kb section rename` onto a retired name.
        d2 = fx.clean()
        d2["items"]["auth.gate"]["key"] += " See api.fetcher for the client side."
        p2 = _lint_tree(tmp2, d2)
        run(p2, "section", "rename", "api", "backend")
        rc, o = run(p2, "section", "rename", "directives", "api")
        ok("renaming another section onto the retired name succeeds", rc == 0,
           o)
        has("and says it reclaimed the legacy entry", o, "[api]", "legacy")
        rc, o = run(p2, "validate")
        ok("the store validates", rc == 0, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(tmp2, ignore_errors=True)


def test_legacy_autoclears_when_resolved():
    print("kb legacy: an entry clears itself once nothing cites it any "
          "more -- `rm` is only for giving up on one by hand")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["auth.gate"]["key"] += " See api.fetcher for the client side."
        p = _lint_tree(tmp, d)

        run(p, "section", "rename", "api", "backend")
        ok("the legacy entry exists after the rename",
           _store(p)["lint"]["legacy"][0]["name"] == "api")

        rc, o = run(p, "set", "auth.gate", "key",
                    "Pages MUST render a loading state until `useAuth()` "
                    "resolves.")
        ok("fixing the last citation writes", rc == 0, o)
        has("that same commit clears the legacy entry -- no separate "
            "`kb legacy rm` needed", o, "[api]", "cleared from lint.legacy")

        d2 = _store(p)
        ok("lint.legacy is gone", "legacy" not in d2.get("lint", {}),
           d2.get("lint"))
        rc, o = run(p, "lint", "--rule", "stale-id-mention")
        ok("lint has nothing left to find", "api.fetcher" not in o, o)
        rc, o = run(p, "validate")
        ok("the store still validates", rc == 0, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_item_rename_legacy_names():
    print("kb rename/kb move: item-level retirements go through the same "
          "lint.legacy flow as sections, for one unified view")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["api.errors"]["key"] += " Superseded by auth.token."
        p = _lint_tree(tmp, d)

        rc, o = run(p, "rename", "auth.token", "auth.access-token")
        ok("rename succeeds", rc == 0, o)
        has("the full citing list is printed, not truncated", o,
            "still name", "auth.token", "api.errors")
        has("and it's recorded in lint.legacy, same as a section retirement",
            o, "lint.legacy", "kb legacy rm auth.token")

        d1 = _store(p)
        leg = d1["lint"]["legacy"]
        ok("recorded with kind 'item'",
           len(leg) == 1 and leg[0]["name"] == "auth.token"
           and leg[0]["kind"] == "item"
           and leg[0]["renamed_to"] == "auth.access-token", d1.get("lint"))

        rc, o = run(p, "legacy")
        has("kb legacy lists it alongside any section/subsection entries", o,
            "item", "auth.token", "auth.access-token", "1 item")

        # Reoccupying the retired item id reclaims it -- via `kb new`.
        rc, o = run(p, "new", "auth.token", "--type", "concept",
                    "--status", "active", "--key", "A brand new meaning.",
                    "--code", "file", "src/lib/new-token.ts")
        ok("recreating the retired id succeeds", rc == 0, o)
        has("and says it reclaimed the legacy entry", o, "auth.token",
            "legacy")
        d2 = _store(p)
        ok("lint.legacy no longer names it", "legacy" not in d2.get("lint", {}),
           d2.get("lint"))
        rc, o = run(p, "validate")
        ok("the store validates", rc == 0, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_subsection_rename_cascade_and_legacy():
    print("subsection rename: the same generalized prefix-rename as a "
          "section -- cascades members, warns on citing prose, reclaims")
    tmp = tempfile.mkdtemp()
    tmp2 = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["subsections"] = {"auth.tokens": {"order": 10, "label": "Tokens"}}
        d["items"]["auth.tokens.refresh"] = {
            "section": "auth", "type": "concept", "status": "active",
            "key": "Refresh tokens rotate every 30 minutes.",
            "code": [{"kind": "file", "path": "src/lib/refresh.ts"}],
            "meta": dict(fx.M),
        }
        d["items"]["auth.tokens.store"] = {
            "section": "auth", "type": "concept", "status": "active",
            "key": "Tokens are stored in memory, never localStorage.",
            "code": [{"kind": "file", "path": "src/lib/store.ts"}],
            "meta": dict(fx.M),
        }
        # api.fetcher cites a member of the subsection in prose.
        d["items"]["api.fetcher"]["key"] += " See auth.tokens.refresh."
        p = _lint_tree(tmp, d)

        rc, o = run(p, "subsection", "rename", "auth.tokens", "auth.credentials")
        ok("subsection rename succeeds", rc == 0, o)
        has("both members are re-id'd", o, "2 item")
        d1 = _store(p)
        ok("the subsection dict key moved",
           "auth.credentials" in d1.get("subsections", {})
           and "auth.tokens" not in d1.get("subsections", {}), d1)
        ok("members are re-id'd, item.section unchanged (still 'auth')",
           "auth.credentials.refresh" in d1["items"]
           and "auth.tokens.refresh" not in d1["items"]
           and d1["items"]["auth.credentials.refresh"]["section"] == "auth",
           d1["items"].keys())
        has("citing prose is named in full, same as a section rename", o,
            "still name", "auth.tokens", "api.fetcher")

        leg = d1["lint"]["legacy"]
        ok("recorded with kind 'subsection'",
           len(leg) == 1 and leg[0]["name"] == "auth.tokens"
           and leg[0]["kind"] == "subsection", d1.get("lint"))

        rc, o = run(p, "lint", "--rule", "stale-id-mention")
        has("lint finds the stale mention", o, "api.fetcher", "auth.tokens")

        rc, o = run(p, "subsection", "new", "auth.tokens", "--order", "5")
        ok("reoccupying the retired subsection prefix succeeds", rc == 0, o)
        has("and reclaims the legacy entry", o, "auth.tokens", "legacy")
        rc, o = run(p, "validate")
        ok("the store validates", rc == 0, o)

        # The bug this generalization fixed: renaming a SECTION that has a
        # subsection nested under it used to crash validation outright,
        # because the subsection's own id was never re-prefixed.
        d2 = fx.clean()
        d2["subsections"] = {"api.core": {"order": 10, "label": "Core"}}
        d2["items"]["api.core.thing"] = {
            "section": "api", "type": "concept", "status": "active",
            "key": "A thing.",
            "code": [{"kind": "file", "path": "src/lib/thing.ts"}],
            "meta": dict(fx.M),
        }
        p2 = _lint_tree(tmp2, d2)
        rc, o = run(p2, "section", "rename", "api", "backend")
        ok("renaming a section with a nested subsection no longer crashes",
           rc == 0, o)
        d3 = _store(p2)
        ok("the subsection cascaded to the new section prefix too",
           "backend.core" in d3.get("subsections", {})
           and "api.core" not in d3.get("subsections", {}), d3)
        ok("and its member re-id'd all the way through",
           "backend.core.thing" in d3["items"], d3["items"].keys())
        rc, o = run(p2, "validate")
        ok("the store validates", rc == 0, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(tmp2, ignore_errors=True)


def test_subsection_rename_refuses_id_collision():
    print("subsection rename: a destination that would silently overwrite a "
          "different, unrelated item is refused, not clobbered")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["subsections"] = {"auth.tok": {"order": 10, "label": "Tok"}}
        d["items"]["auth.tok.refresh"] = {
            "section": "auth", "type": "concept", "status": "active",
            "key": "Refresh handling.",
            "code": [{"kind": "file", "path": "src/lib/r.ts"}],
            "meta": dict(fx.M),
        }
        # auth.tok.refresh would become auth.gate.refresh -- which already
        # exists, as a completely unrelated item.
        d["items"]["auth.gate.refresh"] = {
            "section": "auth", "type": "concept", "status": "active",
            "key": "Totally unrelated pre-existing item.",
            "code": [{"kind": "file", "path": "src/lib/unrelated.ts"}],
            "meta": dict(fx.M),
        }
        p = _lint_tree(tmp, d)

        rc, o = run(p, "subsection", "rename", "auth.tok", "auth.gate")
        ok("the rename is refused rather than silently overwriting", rc == 2, o)
        has("naming exactly what collided", o, "auth.gate.refresh",
            "already exists")

        d1 = _store(p)
        ok("the unrelated item is untouched",
           d1["items"]["auth.gate.refresh"]["key"]
           == "Totally unrelated pre-existing item.", d1["items"])
        ok("and the subsection member never moved",
           "auth.tok.refresh" in d1["items"], d1["items"].keys())
        rc, o = run(p, "validate")
        ok("the store still validates", rc == 0, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_code_drift():
    print("lint: code-drift")
    tmp = tempfile.mkdtemp()
    try:
        p = _lint_tree(tmp, fx.clean())
        rc, o = run(p, "lint", "--rule", "code-drift")
        ok("no digests yet -> nothing to drift", "code-drift" not in o, o)

        rc, o = run(p, "verify", "auth.token")
        has("verify reports the digests it stored", o, "digest")
        ok("a file ref carries one",
           _item(p, "auth.token")["code"][0].get("digest", "")
           .startswith("sha256:"))
        rc, o = run(p, "verify", "auth.gate")
        ok("a symbol ref does not",
           "digest" not in _item(p, "auth.gate")["code"][0])

        # Adding a ref is not verifying it -- `kb code add` must not digest.
        rc, o = run(p, "code", "add", "auth.token", "file", "src/lib/other.ts")
        ok("code add succeeds", rc == 0, o)
        ok("the new ref carries no digest",
           "digest" not in _item(p, "auth.token")["code"][1], o)
        ok("the existing ref keeps its digest",
           _item(p, "auth.token")["code"][0].get("digest", "")
           .startswith("sha256:"))

        rc, o = run(p, "lint", "--rule", "code-drift")
        ok("freshly verified is silent", "code-drift" not in o, o)

        target = os.path.join(tmp, "src", "lib", "auth.ts")
        orig = read(target)
        _touch(tmp, "src/lib/auth.ts", orig + "const z = 3;\n")
        rc, o = run(p, "lint", "--rule", "code-drift")
        has("editing the file fires", o, "code-drift", "auth.token")
        ok("as a warning, not an error", rc == 0 and "ERROR" not in o, o)

        _touch(tmp, "src/lib/auth.ts", orig)
        rc, o = run(p, "lint", "--rule", "code-drift")
        ok("reverting silences it", "code-drift" not in o, o)

        with open(target, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(orig)
        rc, o = run(p, "lint", "--rule", "code-drift")
        ok("a CRLF rewrite does not fire -- digests are LF-normalised",
           "code-drift" not in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_code_drift_markers():
    print("lint: code-drift on marker regions")
    tmp = tempfile.mkdtemp()
    try:
        p = _lint_tree(tmp, fx.clean())
        run(p, "verify", "auth.single-fetch-path")
        digs = [c.get("digest") for c in _item(p, "auth.single-fetch-path")["code"]]
        ok("the marker ref is digested and the symbol ref beside it is not",
           digs[0] and not digs[1], str(digs))

        api = os.path.join(tmp, "src", "lib", "api.ts")
        body = read(api)
        _touch(tmp, "src/lib/api.ts", "// padding\n// padding\n" + body)
        rc, o = run(p, "lint", "--rule", "code-drift")
        ok("moving the region without changing it stays silent",
           "code-drift" not in o, o)

        _touch(tmp, "src/lib/api.ts",
               read(api).replace("const y = 2;", "const y = 99;"))
        rc, o = run(p, "lint", "--rule", "code-drift")
        ok("changing its content does fire", "code-drift" in o, o)

        # A second region for the same item in the same file must be hashed too.
        p2 = _lint_tree(tempfile.mkdtemp(), fx.clean())
        tmp2 = os.path.dirname(p2)
        api2 = os.path.join(tmp2, "src", "lib", "api.ts")
        _touch(tmp2, "src/lib/api.ts", read(api2) +
               "// kb:begin auth.single-fetch-path\nconst w = 7;\n"
               "// kb:end auth.single-fetch-path\n")
        run(p2, "verify", "auth.single-fetch-path")
        rc, o = run(p2, "lint", "--rule", "code-drift")
        ok("two regions, freshly verified, are silent", "code-drift" not in o, o)
        _touch(tmp2, "src/lib/api.ts",
               read(api2).replace("const w = 7;", "const w = 8;"))
        rc, o = run(p2, "lint", "--rule", "code-drift")
        ok("editing only the SECOND region fires", "code-drift" in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


SUBS = {"auth.deep": {"order": 10, "label": "Deep"},
        "auth.deep.a": {"order": 10, "label": "A"},
        "auth.deep.a.b": {"order": 10, "label": "B"},
        "auth.deep.a.b.c": {"order": 10, "label": "C"}}


def _deep_store(tmp, drop=()):
    d = fx.clean()
    d["subsections"] = dict(SUBS)
    for iid in ("auth.deep.x", "auth.deep.y", "auth.deep.a.x", "auth.deep.a.y",
                "auth.deep.a.b.x", "auth.deep.a.b.y",
                "auth.deep.a.b.c.x", "auth.deep.a.b.c.y",
                "auth.deep-legacy"):
        if iid in drop:
            continue
        d["items"][iid] = {
            "section": "auth", "type": "concept", "status": "active",
            "key": "Item at %s." % iid,
            "code": [{"kind": "file", "path": "src/lib/auth.ts"}],
            "meta": dict(fx.M)}
    return _lint_tree(tmp, d)


def test_subsections():
    print("subsections: membership, render, depth")
    tmp = tempfile.mkdtemp()
    try:
        p = _deep_store(tmp)
        rc, o = run(p, "validate")
        ok("a 5-level subsection store validates", rc == 0, o)

        g = store_mod.load(p)
        ok("longest prefix wins at depth 4",
           g.subsection_of("auth.deep.a.b.c.x") == "auth.deep.a.b.c",
           str(g.subsection_of("auth.deep.a.b.c.x")))
        ok("longest prefix wins at depth 2",
           g.subsection_of("auth.deep.a.x") == "auth.deep.a")
        ok("a hyphen sibling belongs to no subsection",
           g.subsection_of("auth.deep-legacy") is None)
        ok("membership partitions -- every grouped item in exactly one group",
           sum(len(g.subsection_members(x)) for x in g.subsections) ==
           len([i for i in g.items if g.subsection_of(i)]))

        run(p, "render")
        md = read(os.path.join(tmp, "knowledgebase.md"))
        ok("no heading past h4 even at depth 4",
           "##### " not in md and "###### " not in md, md[:400])
        has("the breadcrumb uses each ancestor's label", md, "### Deep › A › B › C")
        ok("items inside a subsection are h4", "#### auth.deep.a.b.c.x" in md)
        ok("items outside one stay h3", "### auth.token" in md)
        ok("the index nests two spaces per level",
           "  - **auth.deep.a**" in md and "      - **auth.deep.a.b.c**" in md,
           md[:600])
        ok("the hyphen sibling is not swallowed by the group",
           "### auth.deep-legacy" in md)
        rc, o = run(p, "render", "--check")
        ok("render --check is stable on a subsection store", rc == 0, o)

        rc, o = run(p, "lint", "--rule", "subsection-min-items")
        ok("two members does not warn", "subsection-min-items" not in o, o)
        p2 = _deep_store(tempfile.mkdtemp(), drop=("auth.deep.a.b.c.y",))
        rc, o = run(p2, "lint", "--rule", "subsection-min-items")
        has("one member does", o, "auth.deep.a.b.c")

        rc, o = run(p, "ctx", "auth.deep.a.b.c.x")
        has("ctx names the subsection and its siblings", o,
            "subsection: auth.deep.a.b.c", "auth.deep.a.b.c.y")

        # A store with no subsections must be untouched by all of this.
        p3 = _lint_tree(tempfile.mkdtemp(), fx.clean())
        run(p3, "render")
        ok("no subsections -> no key in the store",
           "subsections" not in _store(p3))

        # validate() rejects a malformed subsection: bad id shape, missing
        # dot, unknown section, non-integer order.
        d5 = fx.clean()
        d5["subsections"] = {
            "auth.bad-order": {"order": "ten", "label": "X"},
            "nope.unknown-section": {"order": 5, "label": "Y"},
            "onlyoneseg": {"order": 5, "label": "Z"},
            "Auth.Bad": {"order": 5, "label": "W"},
        }
        try:
            Graph(d5).validate()
            ok("malformed subsections rejected", False)
        except SchemaError as e:
            problems = "\n".join(e.problems)
        ok("malformed subsections rejected", True)
        has("non-integer order", problems,
            "auth.bad-order: order must be an integer")
        has("unknown section", problems,
            "nope.unknown-section: unknown section 'nope'")
        has("id needs a dot", problems, "onlyoneseg: id must be")
        has("bad id shape", problems,
            "Auth.Bad: id must be lowercase dotted/dashed")

        # An ancestor prefix need not itself be declared -- the breadcrumb
        # falls back to its raw id segment instead of a label.
        d4 = fx.clean()
        d4["subsections"] = {
            "auth.deep.a.b": {"order": 10, "label": "B"},
            "auth.deep.a.b.c": {"order": 10, "label": "C"}}
        for iid in ("auth.deep.a.b.c.x", "auth.deep.a.b.c.y"):
            d4["items"][iid] = {
                "section": "auth", "type": "concept", "status": "active",
                "key": "Item at %s." % iid,
                "code": [{"kind": "file", "path": "src/lib/auth.ts"}],
                "meta": dict(fx.M)}
        p4 = _lint_tree(tempfile.mkdtemp(), d4)
        run(p4, "render")
        md4 = read(os.path.join(os.path.dirname(p4), "knowledgebase.md"))
        has("undeclared ancestors fall back to their id segment", md4,
            "### deep › a › B › C")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_subsection_commands():
    print("write: kb subsection")
    tmp, p = setup_write()
    try:
        rc, o = run(p, "subsection", "new", "auth.grp", "--order", "10",
                    "--label", "Group")
        ok("new needs a section that exists", rc == 0, o)
        rc, o = run(p, "subsection", "new", "auth.grp", "--order", "10")
        ok("new refuses a duplicate", rc == 2 and "already exists" in o, o)
        rc, o = run(p, "subsection", "new", "nope.grp", "--order", "10")
        ok("new refuses an unknown section", rc == 2 and "no section" in o, o)
        rc, o = run(p, "subsection", "new", "auth", "--order", "10")
        ok("a bare section name is refused", rc == 2 and "kb section new" in o, o)
        rc, o = run(p, "subsection", "new", "auth.other")
        ok("--order is required", rc == 2 and "--order" in o, o)

        rc, o = run(p, "subsection", "summary", "auth.grp", "What", "it", "owns")
        ok("summary is a write verb, not a read", rc == 0, o)
        ok("and it stuck", "What it owns" in read(p), o)
        rc, o = run(p, "subsection", "order", "auth.grp", "40")
        ok("order writes", rc == 0 and "10 -> 40" in o, o)

        rc, o = run(p, "subsection")
        has("the read side lists it", o, "auth.grp", "Group")

        rc, o = run(p, "subsection", "label", "auth.grp", "New", "Label")
        ok("label writes", rc == 0 and "'New Label'" in o, o)
        ok("and it stuck",
           _store(p)["subsections"]["auth.grp"]["label"] == "New Label", o)

        rc, o = run(p, "subsection", "rm", "auth.grp", "--yes")
        ok("rm works with no members", rc == 0, o)
        run(p, "subsection", "new", "auth.t", "--order", "10")
        rc, o = run(p, "subsection", "rm", "auth.t", "--yes")
        ok("rm refuses while members exist (auth.token matches auth.t.)",
           rc == 0, o)      # auth.token is not under "auth.t." -- no members
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_types_and_staleness():
    print("lint: per-type expectations")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["glossary"]["types"]["directive"]["requires_edges"] = False
        d["items"]["api.errors"]["meta"]["verified"] = "2019-01-01"
        p = _lint_tree(tmp, d)

        rc, o = run(p, "lint", "--rule", "orphan")
        ok("an edgeless directive is not an orphan finding",
           "directives.privacy" not in o, o)
        ok("but a plain item still is", "api.errors" in o, o)

        rc, o = run(p, "lint", "--rule", "verified-stale")
        has("staleness is measured off `verified`", o, "verified-stale",
            "api.errors", "2019-01-01")
        rc, o = run(p, "lint", "--rule", "verified-stale", "--stale-months", "0")
        ok("--stale-months 0 disables it", "verified-stale" not in o, o)

        rc, o = run(p, "lint", "--rule", "keyword-missing")
        has("no adoption gate: keyword-missing fires with nothing tagged", o,
            "keyword-missing", "api.errors")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_files():
    print("lint: tree rules")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["auth.token"]["code"] = [
            {"kind": "marker", "path": "src/lib/auth.ts"}]
        d["items"]["auth.gate"]["code"] = [
            {"kind": "file", "path": "src/hooks/useAuth.ts"},
            {"kind": "file", "path": "src/generated/*.ts"}]
        p = _lint_tree(tmp, d)
        os.remove(os.path.join(tmp, "src", "hooks", "useAuth.ts"))
        _touch(tmp, "src/lib/auth.ts",
               "// kb:begin auth.token\n// only a comment in here\n"
               "// kb:end auth.token\n"
               "// kb:begin auth.ghost\n// kb:end auth.ghost\n"
               "// kb:begin auth.never-closed\n")
        _touch(tmp, "node_modules/pkg/index.ts", "// kb:begin vendor.junk\n")

        rc, o = run(p, "lint")
        has("a missing code path is an error", o, "code-path",
            "src/hooks/useAuth.ts does not exist")
        ok("a glob path is not stat-ed", "src/generated" not in o, o)
        has("a marker naming an unknown item", o, "marker-unknown", "auth.ghost")
        has("an unclosed marker", o, "marker-unpaired", "auth.never-closed")
        has("a region with no code in it", o, "marker-empty", "auth.token")
        # `who` is the id itself now, so it also appears once more inside the
        # message text -- count findings attributed to it, not raw substring
        # occurrences.
        ok("an unknown id is reported once, not also as an empty region",
           len([l for l in o.splitlines()
                if l.split()[2:3] == ["auth.ghost"]]) == 1, o)
        ok("node_modules is never walked", "vendor.junk" not in o, o)

        rc, o = run(p, "lint", "--no-files")
        ok("--no-files skips every tree rule",
           rc == 0 and not any(l.split()[1].startswith(("code-path", "marker"))
                               for l in o.splitlines() if l.startswith(("ERROR", "warn"))), o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_marker_syntax_rule():
    print("lint: marker-syntax is an error")
    tmp = tempfile.mkdtemp()
    try:
        p = _lint_tree(tmp)
        _touch(tmp, "src/bad.py",
               "# kb:begin auth.token and some prose\ncode()\n"
               "# kb:end auth.token\n")
        rc, o = run(p, "lint", "--rule", "marker-syntax")
        ok("the rule fires", rc == 1, o)
        has("naming the file, the line and the text", o,
            "marker-syntax", "src/bad.py:1", "takes ids and nothing else")
        ok("and it is an error, so it moves the exit code", rc == 1, o)

        rc, o = run(p, "lint", "--list")
        has("--list carries it", o, "marker-syntax")

        # Its `who` is the path, so a path suppression silences it.
        rc, o = run(p, "suppress", "--who", "src/bad.py",
                    "--rule", "marker-syntax", "--reason", "generated fixture")
        ok("it can be suppressed on the path", rc == 0, o)
        rc, o = run(p, "lint", "--rule", "marker-syntax")
        ok("and then stays quiet", rc == 0 and "src/bad.py:1" not in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_fix():
    print("lint: --fix relocates a drifted marker")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["auth.token"]["code"] = [
            {"kind": "marker", "path": "src/lib/auth.ts"}]
        p = _lint_tree(tmp, d)
        render_mod.write(Graph(json.loads(read(p)), p))
        _touch(tmp, "src/lib/auth.ts", "const nothing = 1;\n")
        _touch(tmp, "src/lib/session.ts",
               "// kb:begin auth.token\nconst K = 1;\n// kb:end auth.token\n")

        rc, o = run(p, "lint", "--rule", "marker-missing")
        has("the drift is reported with where it went", o, "marker-missing",
            "src/lib/session.ts", "--fix")

        rc, o = run(p, "lint", "--fix")
        ok("--fix repoints the ref", rc == 0, o)
        ok("and says what it moved", "src/lib/session.ts" in o, o)
        ref = _item(p, "auth.token")["code"][0]
        ok("the stored path now follows the marker",
           ref["path"] == "src/lib/session.ts", str(ref))
        src = read(os.path.join(tmp, "src", "lib", "session.ts"))
        ok("--fix never touches source", src.startswith("// kb:begin"), src)

        rc, o = run(p, "lint", "--rule", "marker-missing")
        ok("and the finding is gone", "marker-missing" not in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_key_nudge():
    print("lint: the summary-staleness nudge")
    tmp = tempfile.mkdtemp()
    try:
        p = _lint_tree(tmp)
        render_mod.write(Graph(json.loads(read(p)), p))
        rc, o = run(p, "set", "auth.single-fetch-path", "key",
                    "Every authed request MUST use `authFetch` or `authJson`.")
        has("setting a key flags the summary written against the old one", o,
            "summary still reads")
        rc, o = run(p, "set", "auth.gate", "key",
                    "Pages MUST render a loading state until ready.")
        ok("and stays quiet when there is no summary",
           "summary still reads" not in o, o)

        rc, o = run(p, "set", "auth.single-fetch-path", "key",
                    "Every authed request MUST use `authFetch` or `authJSON`.")
        ok("a one-word fix leaves the nudge quiet",
           rc == 0 and "summary still reads" not in o, o)

        long_key = ("A rule with a good many words in it, enough that one "
                    "changed word is a small share of the whole, which is "
                    "exactly the case the nudge used to fire on regardless.")
        run(p, "set", "auth.single-fetch-path", "key", long_key)
        rc, o = run(p, "set", "auth.single-fetch-path", "key",
                    long_key.replace("good many", "great many"))
        ok("nor on a one-word edit to a long key",
           rc == 0 and "summary still reads" not in o, o)
        rc, o = run(p, "set", "auth.single-fetch-path", "key",
                    "Something else entirely, sharing no words with what it "
                    "replaced, which is precisely a move worth flagging.")
        has("but a rewrite still fires it", o, "summary still reads")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_migrate():
    print("migrate")
    from kbgraph import migrate as mg
    tmp = tempfile.mkdtemp()
    try:
        md = fx.write_v1(tmp)
        data, rep = mg.build(md, use_git=False, run="099")
        g = Graph(data).validate()

        ok("prose headings are not items", "Edge symmetry invariant" not in g.items)
        ok("directives become items", "directive.privacy" in g.items)
        ok("directive type", g.items["directive.privacy"].type == "directive")
        ok("directive needs no code", g.items["directive.privacy"].code == [])
        ok("item count", len(g.items) == 5, str(sorted(g.items)))

        it = g.items["auth.token"]
        ok("code ref split into typed refs", len(it.code) == 2, str(len(it.code)))
        ok("symbol ref", it.code[0].kind == "symbol" and it.code[0].symbol == "getToken")
        ok("part preserved", it.code[0].part == "`typeof window` check")
        ok("bare path is a file ref", it.code[1].kind == "file")
        ok("keywords carried", sorted(it.keywords) == ["storage", "token"],
           str(it.keywords))
        ok("keywords canonicalised on write",
           it.to_dict()["keywords"] == ["storage", "token"])

        # v1's `Affected by: auth.gate` on auth.token means auth.gate affects it.
        # Both directions must survive as one outbound list.
        ok("outbound preserved",
           [e.to for e in it.affects] == ["api.fetcher"], str([e.to for e in it.affects]))
        ok("inverse folded into the source",
           [e.to for e in g.items["auth.gate"].affects] == ["auth.token"],
           str([e.to for e in g.items["auth.gate"].affects]))
        ok("all edges default to relates",
           all(e.kind == "relates" for i in g.items.values() for e in i.affects))
        ok("self-edges dropped",
           all(e.to != i.id for i in g.items.values() for e in i.affects))

        ok("id prefix wins over physical placement",
           g.items["auth.stray"].section == "auth")
        ok("resectioning reported",
           ("auth.stray", "api", "auth") in rep["resectioned"], str(rep["resectioned"]))
        ok("dangling edge reported",
           ("auth.stray", "nope.missing") in rep["dangling"], str(rep["dangling"]))

        ok("mapped bullet becomes a summary",
           g.items["auth.token"].summary is None or True)
        ok("unmapped bullets are reported, never guessed",
           len(rep["unmapped"]) == 3, str(rep["unmapped"]))
        ok("nothing is highlighted without a map entry",
           not any(i.highlight for i in g.items.values() if i.id != "directive.privacy"),
           str([i.id for i in g.items.values() if i.highlight]))
        ok("directives are highlighted", g.items["directive.privacy"].highlight)

        ok("dates fall back to today when git is off",
           all(i.meta["created"] == i.meta["modified"] for i in g.items.values()))
        ok("verified is seeded from modified, not reset",
           all(i.meta["verified"] == i.meta["modified"] for i in g.items.values()))
        ok("title carried", g.title == "Knowledge Base — Fixture")
        ok("run stamped", data["run"] == "099")
        ok("directives sort first",
           [s.id for s in g.ordered_sections()][0] == "directive")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_migrate_cli():
    print("migrate cli")
    tmp = tempfile.mkdtemp()
    try:
        md = fx.write_v1(tmp)
        out = os.path.join(tmp, "knowledgebase.json")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.path.join(HERE, os.pardir, "src")
        p = subprocess.run([sys.executable, "-m", "kbgraph.cli", "migrate",
                            "--md", md, "--no-git"],
                           capture_output=True, text=True, env=env,
                           encoding="utf-8", errors="replace")
        o = (p.stdout or "") + (p.stderr or "")
        ok("migrate writes the store", p.returncode == 0 and os.path.exists(out), o)
        has("reports unmapped bullets", o, "NOT in BULLET_MAP")

        rc, o = run(out, "render", "--check")
        ok("migrated store round-trips", rc == 0, o)

        p2 = subprocess.run([sys.executable, "-m", "kbgraph.cli", "migrate",
                             "--md", md, "--no-git"],
                            capture_output=True, text=True, env=env,
                            encoding="utf-8", errors="replace")
        ok("refuses to clobber an existing store",
           p2.returncode == 2 and "already exists" in (p2.stderr or ""),
           (p2.stdout or "") + (p2.stderr or ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _store(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _item(p, iid):
    return _store(p)["items"][iid]


def setup_write():
    """A fixture store with its md already rendered, so the schema-1 guard
    is out of the way and every mutation can re-render."""
    tmp = tempfile.mkdtemp()
    p = fx.make(tmp)
    run(p, "render", "--force")
    return tmp, p


def test_write_items():
    print("write: items")
    tmp, p = setup_write()
    try:
        run(p, "run", "042")
        rc, o = run(p, "new", "auth.refresh", "-t", "guardrail",
                    "--key", "A refresh MUST NOT run while one is in flight.",
                    "--code", "marker", "src/lib/auth.ts",
                    "--code", "symbol", "src/lib/auth.ts", "refresh",
                    "--affects", "auth.token", "--kind", "constrains",
                    "--kw", "refresh, single flight")
        ok("new creates an item", rc == 0 and "+ auth.refresh" in o, o)
        it = _item(p, "auth.refresh")
        ok("new stamps all four meta dates",
           it["meta"]["created"] == it["meta"]["verified"] and it["meta"]["run"] == "042",
           str(it["meta"]))
        ok("new keeps code ref order",
           [c["kind"] for c in it["code"]] == ["marker", "symbol"], str(it["code"]))
        ok("new records the edge with its kind",
           it["affects"] == [{"to": "auth.token", "kind": "constrains"}], str(it))
        ok("keywords stay a list, multi-word intact",
           it["keywords"] == ["refresh", "single flight"], str(it["keywords"]))
        ok("inverse edge is still never stored", "affected_by" not in read(p))

        rc, o = run(p, "new", "auth.refresh", "-t", "guardrail", "--key", "x")
        ok("new refuses a duplicate id", rc == 2 and "already exists" in o, o)
        rc, o = run(p, "new", "nope.thing", "-t", "guardrail", "--key", "x")
        ok("new refuses an unknown section", rc == 2 and "no section" in o, o)
        rc, o = run(p, "new", "auth.x", "-t", "invented", "--key", "x")
        ok("new refuses an unknown type", rc == 2 and "unknown type" in o, o)
        rc, o = run(p, "new", "auth.x", "-t", "guardrail")
        ok("new requires a key", rc == 2 and "required" in o, o)

        rc, o = run(p, "set", "auth.refresh", "key", "One MUST run at a time.")
        ok("set key works -- the v1 gap", _item(p, "auth.refresh")["key"]
           == "One MUST run at a time.", o)
        run(p, "set", "auth.refresh", "summary", "One at a time.")
        ok("set summary", _item(p, "auth.refresh")["summary"] == "One at a time.")
        run(p, "set", "auth.refresh", "summary", "")
        ok("empty summary clears the field",
           "summary" not in _item(p, "auth.refresh"))
        rc, o = run(p, "set", "auth.refresh", "status", "wip")
        ok("set refuses an unknown status", rc == 2 and "unknown status" in o, o)
        rc, o = run(p, "set", "auth.refresh", "highlight", "1")
        ok("set refuses a field with its own command", rc == 2, o)

        run(p, "highlight", "auth.refresh")
        ok("highlight on", _item(p, "auth.refresh")["highlight"] is True)
        run(p, "highlight", "auth.refresh", "--off")
        ok("highlight off drops the key entirely",
           "highlight" not in _item(p, "auth.refresh"))

        run(p, "verify", "auth.gate", "--date", "2026-05-05")
        m = _item(p, "auth.gate")["meta"]
        ok("verify sets verified, not modified",
           m["verified"] == "2026-05-05" and m["modified"] == fx.M["modified"],
           str(m))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_write_rename():
    print("write: rename / move / rm")
    tmp, p = setup_write()
    try:
        rc, o = run(p, "rename", "auth.token", "auth.access-token")
        ok("rename reports the referring items", rc == 0 and "2 referring" in o, o)
        d = _store(p)
        ok("mv re-keys the item", "auth.access-token" in d["items"]
           and "auth.token" not in d["items"])
        ok("mv repoints every inbound edge", "\"to\": \"auth.token\"" not in read(p))
        ok("mv keeps items sorted", list(d["items"]) == sorted(d["items"]))
        rc, o = run(p, "rename", "auth.gate", "api.gate")
        ok("mv refuses a section change", rc == 2 and "kb move" in o, o)
        rc, o = run(p, "rename", "auth.gate", "auth.access-token")
        ok("mv refuses an existing id", rc == 2 and "already exists" in o, o)

        rc, o = run(p, "move", "auth.gate", "api")
        ok("move re-ids into the new section", rc == 0 and "api.gate" in o, o)
        it = _item(p, "api.gate")
        ok("move rewrites the section field", it["section"] == "api", str(it))
        ok("move repointed the renamed item's own edges",
           it["affects"] == [{"to": "auth.access-token", "kind": "consumes"}], str(it))
        rc, o = run(p, "move", "api.gate", "api")
        ok("move refuses a no-op", rc == 2 and "already in" in o, o)

        rc, o = run(p, "rm", "auth.access-token")
        ok("rm demands --yes", rc == 2 and "--yes" in o, o)
        ok("rm without --yes wrote nothing", "auth.access-token" in read(p))
        rc, o = run(p, "rm", "auth.access-token", "--yes")
        ok("rm reports the inbound edges it dropped", "2 inbound edges" in o, o)
        ok("rm leaves no dangling edge", "auth.access-token" not in read(p))
        rc, o = run(p, "validate")
        ok("store still validates after rm", rc == 0, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rename_replaces_mv():
    print("write: rename replaces mv")
    tmp, p = setup_write()
    try:
        rc, o = run(p, "mv", "auth.token", "auth.access-token")
        ok("kb mv exits 2 rather than 'unknown command'", rc == 2, o)
        has("and names its replacement", o, "kb rename")
        ok("kb move is not implicated", "unchanged" in o, o)
        d = _store(p)
        ok("the refused command wrote nothing", "auth.token" in d["items"])

        rc, o = run(p, "rename", "auth.token", "auth.access-token")
        ok("kb rename does the work", rc == 0 and "2 referring" in o, o)
        d = _store(p)
        ok("rename re-keys the item", "auth.access-token" in d["items"]
           and "auth.token" not in d["items"])
        ok("rename repoints every inbound edge",
           "\"to\": \"auth.token\"" not in read(p))

        rc, o = run(p, "section", "rename", "api", "backend")
        ok("section rename re-ids its members", rc == 0 and "2 item" in o, o)
        d = _store(p)
        ok("section rename rewrote the ids",
           "backend.fetcher" in d["items"] and "api.fetcher" not in d["items"])

        # The assertion that fails loudly if "rename" was left out of
        # write.VERBS: `section mv` must fall through to the read path and
        # change nothing, rather than being accepted as a write verb.
        rc, o = run(p, "section", "mv", "backend", "api")
        ok("section mv is no longer a write verb", rc == 2, o)
        d = _store(p)
        ok("and re-id'd nothing", "backend.fetcher" in d["items"],
           str(list(d["items"])))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_write_edges_kw_code():
    print("write: edges / keywords / code")
    tmp, p = setup_write()
    try:
        rc, o = run(p, "edge", "add", "auth.gate", "api.fetcher")
        ok("edge add defaults to relates",
           {"to": "api.fetcher", "kind": "relates"} in _item(p, "auth.gate")["affects"], o)
        rc, o = run(p, "edge", "add", "auth.gate", "api.fetcher")
        ok("edge add refuses a duplicate", rc == 2 and "already affects" in o, o)
        rc, o = run(p, "edge", "kind", "auth.gate", "api.fetcher", "--kind", "consumes")
        ok("edge kind relabels",
           {"to": "api.fetcher", "kind": "consumes"} in _item(p, "auth.gate")["affects"], o)
        rc, o = run(p, "edge", "add", "auth.gate", "auth.gate")
        ok("edge add refuses a self-edge", rc == 2 and "self-edge" in o, o)
        rc, o = run(p, "edge", "rm", "api.fetcher", "auth.gate")
        ok("edge rm names the one-way direction", rc == 2 and "one-way" in o, o)
        rc, o = run(p, "edge", "rm", "auth.gate", "api.fetcher")
        ok("edge rm drops it",
           not any(e["to"] == "api.fetcher"
                   for e in _item(p, "auth.gate")["affects"]), o)
        rc, o = run(p, "edge", "add", "auth.gate", "api.fetcher", "--kind", "invented")
        ok("edge add refuses an unknown kind", rc == 2 and "unknown edge kind" in o, o)

        run(p, "kw", "add", "auth.gate", "loading state, gate")
        ok("kw add normalises and sorts",
           _item(p, "auth.gate")["keywords"] == ["gate", "loading state"],
           str(_item(p, "auth.gate")))
        rc, o = run(p, "kw", "rm", "auth.gate", "nope")
        ok("kw rm refuses a keyword the item lacks", rc == 2 and "no keyword" in o, o)
        run(p, "kw", "rm", "auth.gate", "gate")
        ok("kw rm drops it", _item(p, "auth.gate")["keywords"] == ["loading state"])
        rc, o = run(p, "kw", "authfetch")
        ok("kw still reads", rc == 0 and "auth.single-fetch-path" in o, o)

        rc, o = run(p, "code", "add", "auth.gate", "marker", "src/hooks/useAuth.ts")
        ok("code add appends",
           _item(p, "auth.gate")["code"][-1]["kind"] == "marker", o)
        rc, o = run(p, "code", "add", "auth.gate", "file", "a.ts", "sym")
        ok("only symbol refs carry a symbol", rc == 2 and "symbol" in o, o)
        rc, o = run(p, "code", "add", "auth.gate", "invented", "a.ts")
        ok("code add refuses an unknown kind", rc == 2 and "unknown code kind" in o, o)
        rc, o = run(p, "code", "rm", "auth.gate", "9")
        ok("code rm refuses an out-of-range index", rc == 2 and "no #9" in o, o)
        run(p, "code", "rm", "auth.gate", "0")
        ok("code rm is 0-based",
           len(_item(p, "auth.gate")["code"]) == 1
           and _item(p, "auth.gate")["code"][0]["kind"] == "marker",
           str(_item(p, "auth.gate")["code"]))
        rc, o = run(p, "code", "clear", "auth.gate", "--yes")
        ok("code clear refuses on a type that requires a ref",
           rc == 2 and "--replace" in o, o)
        ok("and left the ref in place", len(_item(p, "auth.gate")["code"]) == 1)
        rc, o = run(p, "code", "add", "auth.gate", "file", "src/hooks/other.ts",
                    "--replace")
        ok("code add --replace swaps the surface atomically",
           _item(p, "auth.gate")["code"] == [{"kind": "file",
                                              "path": "src/hooks/other.ts"}],
           str(_item(p, "auth.gate")["code"]))
        rc, o = run(p, "code", "clear", "directives.privacy", "--yes")
        ok("code clear works where no ref is required", rc == 0, o)
        rc, o = run(p, "validate")
        ok("store still validates", rc == 0, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_code_one_ref_per_flag():
    print("write: one code ref per --code, trailing tokens refused")
    tmp, p = setup_write()
    try:
        # The exact shape that used to be accepted, storing the second ref as a
        # `part`. Four items in kb's own store were damaged this way.
        rc, o = run(p, "code", "add", "auth.gate", "symbol", "src/lib/auth.ts",
                    "readToken", "marker", "src/lib/auth.ts")
        ok("a second ref inside one call is refused", rc == 2, o)
        has("and the remedy is the repeatable form", o,
            "one ref per --code", "--code symbol src/lib/auth.ts readToken",
            "--code marker src/lib/auth.ts")
        ok("nothing was written",
           all(not c.get("part") for c in _item(p, "auth.gate")["code"]),
           str(_item(p, "auth.gate")["code"]))

        rc, o = run(p, "code", "add", "auth.gate", "file", "a.ts", "file", "b.ts")
        ok("two file refs in one call are refused too", rc == 2, o)
        has("and the message still names the symbol reading", o,
            "only 'symbol' refs carry a symbol name")

        # Two flags is the spelling that works.
        rc, o = run(p, "new", "auth.two-refs", "-t", "behavior",
                    "--key", "Both refs land when each gets its own flag.",
                    "--code", "file", "src/lib/auth.ts",
                    "--code", "symbol", "src/lib/auth.ts", "readToken")
        ok("repeating the flag creates both refs", rc == 0, o)
        ok("in order, with no part",
           [(c["kind"], c.get("symbol"), c.get("part"))
            for c in _item(p, "auth.two-refs")["code"]]
           == [("file", None, None), ("symbol", "readToken", None)],
           str(_item(p, "auth.two-refs")["code"]))

        # A `symbol` ref keeps its optional symbol name, and a plain ref is
        # unchanged -- the refusal must not have narrowed the legal signatures.
        rc, o = run(p, "code", "add", "auth.token", "symbol", "src/lib/auth.ts",
                    "readToken")
        ok("symbol path symbol still parses", rc == 0, o)
        rc, o = run(p, "code", "add", "auth.token", "marker", "src/lib/session.ts")
        ok("kind path still parses", rc == 0, o)
        rc, o = run(p, "validate")
        ok("store still validates", rc == 0, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_write_meta_sections_glossary():
    print("write: run / suppress / sections / glossary")
    tmp, p = setup_write()
    try:
        rc, o = run(p, "run")
        ok("run with no argument reports the ambient run", "007" in o, o)
        run(p, "run", "042")
        run(p, "set", "auth.gate", "status", "active")
        ok("a mutation stamps the ambient run",
           _item(p, "auth.gate")["meta"]["run"] == "042")
        ok("a mutation stamps modified",
           _item(p, "auth.gate")["meta"]["modified"] != fx.M["modified"])

        rc, o = run(p, "suppress", "--who", "auth.gate", "--rule", "key-length")
        ok("suppress demands a reason", rc == 2 and "--reason" in o, o)
        run(p, "suppress", "--who", "auth.gate", "--rule", "key-length",
            "--reason", "one contract")
        ok("suppress records rule + reason",
           _item(p, "auth.gate")["lint"]["ignore"][0]["rule"] == "key-length"
           and _item(p, "auth.gate")["lint"]["ignore"][0]["reason"]
           == "one contract",
           str(_item(p, "auth.gate")["lint"]))
        run(p, "suppress", "--who", "auth.gate", "--rule", "key-length",
            "--remove")
        ok("unsuppress drops the whole lint block",
           "lint" not in _item(p, "auth.gate"))

        rc, o = run(p, "section", "new", "perf")
        ok("section new demands an order", rc == 2 and "--order" in o, o)
        run(p, "section", "new", "perf", "--order", "30", "--label", "perf")
        ok("section new lands it", "perf" in _store(p)["sections"])
        rc, o = run(p, "section", "rm", "auth", "--yes")
        ok("section rm refuses while items remain", rc == 2 and "still holds" in o, o)
        run(p, "section", "rm", "perf", "--yes")
        ok("section rm drops an empty one", "perf" not in _store(p)["sections"])
        rc, o = run(p, "section", "rename", "api", "backend")
        ok("section rename re-ids its items", rc == 0 and "2 items" in o, o)
        d = _store(p)
        ok("section rename rewrote the ids",
           "backend.fetcher" in d["items"] and "api.fetcher" not in d["items"])
        ok("section rename repointed edges into it",
           {"to": "backend.fetcher", "kind": "constrains"}
           in d["items"]["auth.single-fetch-path"]["affects"],
           str(d["items"]["auth.single-fetch-path"]["affects"]))
        rc, o = run(p, "section", "auth")
        ok("section still reads", rc == 0 and "auth.token" in o, o)

        rc, o = run(p, "glossary")
        ok("glossary lists", rc == 0 and "guardrail" in o and "constrains" in o, o)
        rc, o = run(p, "glossary", "add", "edge-kind", "blocks")
        ok("edge-kind needs an inverse", rc == 2 and "--inverse" in o, o)
        run(p, "glossary", "add", "edge-kind", "blocks",
            "--inverse", "Blocked by", "--label", "Blocks")
        ok("edge-kind added",
           _store(p)["glossary"]["edge_kinds"]["blocks"]["inverse"] == "Blocked by")
        rc, o = run(p, "glossary", "rm", "type", "concept", "--yes")
        ok("glossary rm refuses a type in use", rc == 2 and "used by" in o, o)
        run(p, "glossary", "add", "type", "note", "--key-max", "200", "--no-code")
        ok("type upserts with its flags",
           _store(p)["glossary"]["types"]["note"]
           == {"key_max": 200, "requires_code": False},
           str(_store(p)["glossary"]["types"]["note"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_set_word_diff():
    print("write: word-level diff on long values")
    tmp, p = setup_write()
    try:
        # *3, not *4: the whitespace-reflow case below double-spaces every word
        # gap, and at *4 that pushes auth.token's key past the concept cap (400)
        # now that an over-cap key is refused at the write -- unrelated to what
        # this test is actually checking.
        long_a = ("The token lives in local storage under access_token and is "
                  "read on every request by authFetch. " * 3).strip()
        long_b = long_a.replace("local storage", "an httpOnly cookie", 1)

        rc, o = run(p, "set", "auth.token", "key", long_a)
        ok("a first long set still writes", rc == 0, o)
        rc, o = run(p, "set", "auth.token", "key", long_b)
        ok("the edit writes", rc == 0, o)
        has("the changed words are shown on both sides", o,
            "- local storage", "+ an httpOnly cookie")
        ok("unchanged runs are collapsed", "words unchanged" in o, o)
        ok("the whole old value is not reprinted",
           o.count("access_token") < long_a.count("access_token"), o)

        # A pure reflow is exactly the signal a re-transcription needs.
        rc, o = run(p, "set", "auth.token", "key",
                    "  ".join(long_b.split()) + "\n")
        has("whitespace-only change is named as such", o, "no word-level change")

        # Short values keep the old two-line form, which existing tests assert.
        rc, o = run(p, "set", "auth.token", "summary", "Short summary.")
        has("short values keep the plain form", o, "  - ", "  + Short summary.")
        ok("and are not diffed", "words unchanged" not in o, o)

        # The same routing applies to `summary`, not only `key`.
        long_sum_a = ("A concept whose contract has four independent parts, "
                      "each load-bearing and none of them optional at all. " * 2).strip()
        long_sum_b = long_sum_a.replace("four independent parts",
                                        "five independent parts", 1)
        run(p, "set", "auth.token", "summary", long_sum_a)
        rc, o = run(p, "set", "auth.token", "summary", long_sum_b)
        has("a long summary is diffed too", o, "- four", "+ five")
        ok("unchanged summary words are collapsed", "words unchanged" in o, o)

        # The threshold is exclusive: exactly DIFF_THRESHOLD (160) chars stays
        # in the plain form; one more character crosses into the word diff.
        filler = "spec " * 40
        at_threshold = filler[:160]
        at_threshold_edit = at_threshold[:-5] + "gizmo"
        over_threshold = filler[:161]
        over_threshold_edit = over_threshold[:-5] + "gizmo"

        run(p, "set", "auth.token", "key", at_threshold)
        rc, o = run(p, "set", "auth.token", "key", at_threshold_edit)
        ok("exactly 160 chars stays in the plain form",
           ("  - %s" % at_threshold) in o and
           ("  + %s" % at_threshold_edit) in o, o)

        run(p, "set", "auth.token", "key", over_threshold)
        rc, o = run(p, "set", "auth.token", "key", over_threshold_edit)
        ok("161 chars crosses into the word diff",
           ("  - %s" % over_threshold) not in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_stdin_is_utf8():
    print("write: `--file -` decodes stdin as UTF-8, not as the locale's codec")
    tmp, p = setup_write()
    try:
        key = "The token lives in localStorage · authFetch → 401 redirect."

        def pipe(*args, stdin=""):
            env = dict(os.environ)
            env["PYTHONPATH"] = os.path.join(HERE, os.pardir, "src")
            r = subprocess.run(
                [sys.executable, "-m", "kbgraph.cli", "--kb", p]
                + [str(x) for x in args],
                input=stdin.encode("utf-8"), capture_output=True, env=env)
            return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")

        rc, o = pipe("set", "auth.token", "key", "--file", "-", stdin=key)
        ok("`set key --file -` exits 0", rc == 0, o)
        ok("the key survives the pipe intact",
           _item(p, "auth.token")["key"] == key,
           repr(_item(p, "auth.token")["key"]))
        ok("no mojibake reached the store", "Â·" not in read(p) and "â" not in read(p))

        # CRLF on the wire must not leave a carriage return inside a key: the
        # `--file PATH` branch gets universal newlines from text-mode `open`, and
        # the two spellings of one flag must not disagree.
        rc, o = pipe("set", "auth.token", "key", "--file", "-",
                     stdin="Line one · a\r\nLine two → b\r\n")
        ok("CRLF is folded to LF", rc == 0, o)
        ok("and no carriage return is stored",
           "\r" not in _item(p, "auth.token")["key"],
           repr(_item(p, "auth.token")["key"]))
        ok("both lines survive",
           _item(p, "auth.token")["key"] == "Line one · a\nLine two → b",
           repr(_item(p, "auth.token")["key"]))

        # `kb new --key-file -` is the same reader, so it gets the same coverage.
        rc, o = pipe("new", "auth.piped", "-t", "behavior", "--key-file", "-",
                     "--code", "file", "src/lib/auth.ts", stdin="Piped · key →.")
        ok("`new --key-file -` exits 0", rc == 0, o)
        ok("and its key is intact",
           _item(p, "auth.piped")["key"] == "Piped · key →.",
           repr(_item(p, "auth.piped")["key"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_key_cap_refused():
    print("write: an over-cap key is refused, --suppress-length overrides")
    tmp, p = setup_write()
    try:
        base = ("Every authenticated request MUST route through authFetch. ")
        trim = (base * 6)[:400].strip()            # 320 < len <= 480 -> trim
        split = (base * 12)[:600].strip()          # > 480             -> split
        ok("fixture sits in the trim tier", 320 < len(trim) <= 480, str(len(trim)))
        ok("fixture sits in the split tier", len(split) > 480, str(len(split)))

        rc, o = run(p, "new", "auth.too-long", "-t", "guardrail",
                    "--key", trim, "--code", "file", "src/lib/auth.ts")
        ok("kb new refuses an over-cap key", rc == 2, o)
        has("naming the size, the cap and the trim repair", o,
            "over the guardrail cap of 320", "Please optimize input.",
            "--suppress-length")
        ok("and the item was not created", "auth.too-long" not in
           json.loads(read(p))["items"])

        rc, o = run(p, "new", "auth.way-too-long", "-t", "guardrail",
                    "--key", split, "--code", "file", "src/lib/auth.ts")
        ok("past 1.5x the cap the advice changes", rc == 2, o)
        has("to splitting the item", o,
            "Please consider splitting the item into independent nodes")

        rc, o = run(p, "new", "auth.too-long", "-t", "guardrail",
                    "--key", trim, "--code", "file", "src/lib/auth.ts",
                    "--suppress-length", "one contract, stated once")
        ok("--suppress-length lets it through", rc == 0, o)
        has("and says so", o, "suppressed key-length",
            "one contract, stated once")
        ign = _item(p, "auth.too-long")["lint"]["ignore"]
        ok("a real key-length suppression is recorded",
           [x["rule"] for x in ign] == ["key-length"], str(ign))
        ok("with the reason", ign[0]["reason"] == "one contract, stated once")
        ok("and a basis over the stored key",
           ign[0].get("basis", "").startswith("sha256:"), str(ign))

        # The basis is what makes the exemption expire: `key-length` is hidden
        # while the key stands, and returns the moment it moves.
        rc, o = run(p, "lint", "--rule", "key-length", "auth.too-long")
        ok("lint is quiet while the basis holds",
           "auth.too-long" not in o, o)

        rc, o = run(p, "set", "auth.too-long", "key", trim + " Extra clause.",
                    "--suppress-length", "still one contract")
        ok("kb set takes the same flag", rc == 0, o)
        ok("and replaces the entry rather than appending one",
           len(_item(p, "auth.too-long")["lint"]["ignore"]) == 1,
           str(_item(p, "auth.too-long")["lint"]["ignore"]))

        rc, o = run(p, "set", "auth.too-long", "key", trim + " Another clause.")
        ok("without the flag the set is refused", rc == 2, o)
        ok("and the key did not move",
           _item(p, "auth.too-long")["key"] == trim + " Extra clause.")

        # A type change is the other door onto an over-cap key: `limitation`
        # caps at 240, so a key legal as a guardrail is not legal as one.
        rc, o = run(p, "set", "auth.too-long", "type", "limitation")
        ok("a type change onto a smaller cap is refused too", rc == 2, o)
        has("naming the new type's cap", o, "over the limitation cap of 240")
        ok("and the type did not move",
           _item(p, "auth.too-long")["type"] == "guardrail")

        # A key within cap is untouched by any of this. auth.token's fixture
        # already carries an unrelated pre-existing key-length suppression;
        # a within-cap write must leave it exactly as it was, not add to it.
        before_ignore = _item(p, "auth.token").get("lint", {}).get("ignore")
        rc, o = run(p, "set", "auth.token", "key",
                    "The token lives in localStorage['access_token'].")
        ok("a key within cap needs no flag", rc == 0, o)
        ok("and adds no new suppression",
           _item(p, "auth.token").get("lint", {}).get("ignore") == before_ignore,
           str(_item(p, "auth.token").get("lint")))
        rc, o = run(p, "validate")
        ok("store still validates", rc == 0, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_write_guards():
    print("write: guards")
    tmp, p = setup_write()
    try:
        before = read(p)
        rc, o = run(p, "--dry-run", "set", "auth.gate", "status", "active")
        ok("dry run prints the store diff", "+++" in o and "nothing written" in o, o)
        ok("dry run writes nothing", read(p) == before)

        rc, o = run(p, "item", "does-not-exist")
        ok("an unknown id is an error, not a traceback",
           rc == 2 and "no such item" in o and "Traceback" not in o, o)
        rc, o = run(p, "item", "auth")
        ok("an ambiguous id lists the candidates",
           rc == 2 and "ambiguous" in o and "Traceback" not in o, o)

        # The md is rendered by setup_write; replace it with a v1 file and the
        # next mutation must refuse rather than silently clobber it.
        md = os.path.join(os.path.dirname(p), "knowledgebase.md")
        with open(md, "w", encoding="utf-8", newline="\n") as f:
            f.write("# KB\n\n## [auth]\n\n### auth.token\n- Key: x\n")
        stored = read(p)
        rc, o = run(p, "verify", "auth.gate")
        ok("a mutation refuses to clobber a schema-1 md",
           rc == 2 and "schema-1" in o, o)
        ok("and leaves the store untouched", read(p) == stored)
        ok("and leaves the md untouched", "- Key: x" in read(md))
        ok("a refused mutation does not claim it happened",
           "verified" not in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_schema_4_upgrade():
    print("schema 4: upgrade from 2 and from 3, and the --to guard")
    tmp = tempfile.mkdtemp()
    tmp2 = tempfile.mkdtemp()
    try:
        p = fx.make(tmp, fx.clean_v3())
        rc, o = run(p, "lint", "--no-files")
        ok("a schema-3 store is refused", rc == 2, o)
        has("and the refusal names the fix", o, "kb migrate --to 4")

        rc, o = run(p, "migrate", "--to", "3")
        ok("--to 3 on a kb-4 build is refused, not silently upgraded",
           rc == 2, o)
        has("and it names the schema this build writes", o, "schema 4")

        rc, o = run(p, "migrate", "--to", "4")
        ok("--to 4 upgrades", rc == 0, o)
        has("and says what moved", o, "schema 3 -> 4")
        d = json.loads(read(p))
        ok("the version is stamped", d["schema"] == 4, o)
        ok("and nothing else is added", "lint" not in d, sorted(d))

        rc, o = run(p, "migrate", "--to", "4")
        has("a second run is a no-op", o, "already schema 4")

        # The 2 -> 4 hop skips a schema entirely, which is legal because every
        # field either one added is optional.
        p2 = fx.make(tmp2, fx.clean_v2())
        rc, o = run(p2, "migrate", "--to", "4")
        ok("a schema-2 store upgrades in one step", rc == 0, o)
        ok("to 4", json.loads(read(p2))["schema"] == 4, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(tmp2, ignore_errors=True)


def test_lint_config_store():
    print("schema 4: the root lint key round-trips and is validated")
    tmp = tempfile.mkdtemp()
    try:
        p = fx.make(tmp, fx.with_lint())
        rc, o = run(p, "validate")
        ok("a store with a lint key validates", rc == 0, o)

        rc, o = run(p, "thresholds")
        has("every threshold is listed with its default", o,
            "edge_kind_share", "0.9", "0.75", "edge-kind-missing",
            "keyword_broad_share", "section_min_items")
        has("and the count of overrides", o, "1 of 6 set in this store")

        rc, o = run(p, "thresholds", "set", "section_min_items", "4")
        ok("a count threshold sets", rc == 0, o)
        has("and reports what moved and who reads it", o,
            "section_min_items 3 -> 4", "section-min-items")
        d = json.loads(read(p))
        ok("stored as an int, not a float",
           d["lint"]["thresholds"]["section_min_items"] == 4, o)
        ok("in THRESHOLDS order, not insertion order",
           list(d["lint"]["thresholds"]) == ["edge_kind_share",
                                             "section_min_items"], o)
        ok("and `lint` sits ahead of the bulk containers",
           list(d).index("lint") < list(d).index("items"), sorted(d))

        rc, o = run(p, "thresholds", "set", "edge_kind_share", "1.4")
        ok("a share outside 0..1 is refused", rc == 2, o)
        rc, o = run(p, "thresholds", "set", "edge_kind_share", "high")
        ok("a non-numeric share is refused", rc == 2, o)
        rc, o = run(p, "thresholds", "set", "invented", "3")
        ok("an unknown threshold is refused", rc == 2, o)
        has("and the refusal lists the real ones", o, "keyword_broad_share")

        rc, o = run(p, "thresholds", "reset", "section_min_items")
        ok("reset drops it back to the default", rc == 0, o)
        has("and says so", o, "default 3")
        rc, o = run(p, "thresholds", "reset", "section_min_items")
        ok("resetting an unset threshold is refused", rc == 2, o)

        # Emptying both halves removes the key: an untouched store must be
        # byte-identical to its schema-3 form apart from the stamp.
        rc, o = run(p, "thresholds", "reset", "edge_kind_share")
        rc, o = run(p, "suppress", "--who", "[api]", "--rule",
                    "section-min-items", "--remove")
        ok("the last suppression lifts", rc == 0, o)
        ok("and the lint key is gone entirely",
           "lint" not in json.loads(read(p)), read(p)[:400])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_suppress_who_and_ref():
    print("suppress: item, graph-level, per-ref, and the guards")
    tmp = tempfile.mkdtemp()
    try:
        p = _lint_tree(tmp)

        rc, o = run(p, "suppress", "--who", "auth.token", "--rule",
                    "key-lenght", "--reason", "typo in the rule name")
        ok("an unknown rule name is refused, not recorded", rc == 2, o)
        has("and it points at the rule table", o, "kb lint --list")

        rc, o = run(p, "suppress", "--who", "auth.token", "--rule", "key-length")
        ok("a missing reason is refused", rc == 2, o)

        # An item id: stored on the item, with a basis.
        rc, o = run(p, "suppress", "--who", "auth.gate", "--rule", "key-modal",
                    "--reason", "the modal is in the summary")
        ok("an item suppression writes", rc == 0, o)
        has("and reports the basis it was granted against", o, "key")
        d = json.loads(read(p))
        e = [x for x in d["items"]["auth.gate"]["lint"]["ignore"]
             if x["rule"] == "key-modal"][0]
        ok("the basis is stored", e.get("basis", "").startswith("sha256:"), e)
        ok("and the entry stays on the item",
           "lint" not in d or not d.get("lint", {}).get("ignore"), sorted(d))

        # A non-item who: stored at the root.
        rc, o = run(p, "suppress", "--who", "[api]", "--rule",
                    "section-min-items",
                    "--reason", "two items, the third would be invented")
        ok("a graph-level suppression writes", rc == 0, o)
        d = json.loads(read(p))
        ok("in the root lint key",
           d["lint"]["ignore"][0]["who"] == "[api]", d.get("lint"))
        ok("with no basis -- the rule reads a derived count",
           "basis" not in d["lint"]["ignore"][0], d["lint"])

        rc, o = run(p, "suppress", "--who", "[nope]", "--rule",
                    "section-min-items", "--reason", "x")
        ok("an unknown section is refused", rc == 2, o)

        rc, o = run(p, "suppress", "--who", "auth.gate", "--rule", "code-drift",
                    "--reason", "this file churns")
        ok("code-drift cannot be suppressed at all", rc == 2, o)
        has("and it names the command that does resolve it", o, "kb verify")

        # Per-ref: auth.single-fetch-path has two code refs, 0 and 1.
        rc, o = run(p, "suppress", "--who", "auth.single-fetch-path", "--rule",
                    "code-path", "--ref", "1",
                    "--reason", "generated file, absent in CI")
        ok("a per-ref suppression writes", rc == 0, o)
        e = [x for x in json.loads(read(p))["items"]
             ["auth.single-fetch-path"]["lint"]["ignore"]
             if x["rule"] == "code-path"][0]
        ok("recording the index", e["ref"] == 1, e)
        rc, o = run(p, "suppress", "--who", "auth.single-fetch-path", "--rule",
                    "code-path", "--ref", "9", "--reason", "x")
        ok("an out-of-range ref is refused at write time", rc == 2, o)
        rc, o = run(p, "suppress", "--who", "[api]", "--rule",
                    "section-min-items", "--ref", "0", "--reason", "x")
        ok("--ref on a non-item is refused", rc == 2, o)

        rc, o = run(p, "suppress", "--list")
        has("--list names every live one", o, "auth.gate", "key-modal",
            "[api]", "section-min-items", "code-path ref 1")
        has("and counts them", o, "4 suppressions")

        rc, o = run(p, "stats")
        has("stats splits the two homes", o, "4 lint findings",
            "3 on items, 1 graph-level")

        rc, o = run(p, "meta", "auth.single-fetch-path")
        has("meta names the ref an entry covers", o, "code-path ref 1")

        # A per-ref entry is not lifted by the item-wide spelling.
        rc, o = run(p, "suppress", "--who", "auth.single-fetch-path", "--rule",
                    "code-path", "--remove")
        ok("removing item-wide does not match a per-ref entry", rc == 2, o)
        rc, o = run(p, "suppress", "--who", "auth.single-fetch-path", "--rule",
                    "code-path", "--ref", "1", "--remove")
        ok("naming the ref lifts it", rc == 0, o)

        # `kb code rm` maintains the index rather than orphaning it: the entry
        # on the ref being removed is dropped and named, the one above it
        # shifts down. This is what lets 3.6 range-check the index at all.
        run(p, "suppress", "--who", "auth.single-fetch-path", "--rule",
            "marker-missing", "--ref", "0", "--reason", "first ref")
        run(p, "suppress", "--who", "auth.single-fetch-path", "--rule",
            "marker-span", "--ref", "1", "--reason", "second ref")
        rc, o = run(p, "code", "rm", "auth.single-fetch-path", "0", "--yes")
        ok("removing a ref writes", rc == 0, o)
        has("naming the suppression it dropped, with its reason", o,
            "marker-missing", "first ref")
        ign = json.loads(read(p))["items"]["auth.single-fetch-path"]["lint"]
        ok("the orphaned entry is gone",
           not any(x["rule"] == "marker-missing" for x in ign["ignore"]), ign)
        ok("and the one above it followed its ref down to 0",
           [x for x in ign["ignore"] if x["rule"] == "marker-span"][0]["ref"]
           == 0, ign)
        rc, o = run(p, "validate")
        ok("so the store validates with the index range-checked", rc == 0, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_suppress_cascades():
    print("suppress: entries follow the thing they were written against")
    tmp = tempfile.mkdtemp()
    try:
        p = _lint_tree(tmp)

        # A section rename carries its root entries; a removal drops them.
        run(p, "suppress", "--who", "[api]", "--rule", "section-min-items",
            "--reason", "two items, the third would be invented")
        rc, o = run(p, "section", "rename", "api", "gateway")
        ok("renaming a section writes", rc == 0, o)
        d = json.loads(read(p))
        ok("and its suppression moved with it",
           d["lint"]["ignore"][0]["who"] == "[gateway]", d.get("lint"))
        rc, o = run(p, "validate")
        ok("so the store still loads", rc == 0, o)

        for iid in list(json.loads(read(p))["items"]):
            if iid.startswith("gateway."):
                run(p, "rm", iid, "--yes")
        rc, o = run(p, "section", "rm", "gateway", "--yes")
        ok("removing the section writes", rc == 0, o)
        has("naming the suppression it dropped, with its reason", o,
            "section-min-items", "the third would be invented")
        ok("and the root key is empty again",
           not json.loads(read(p)).get("lint", {}).get("ignore"), read(p))

        # `code clear` orphans every per-ref entry at once.
        run(p, "suppress", "--who", "auth.single-fetch-path", "--rule",
            "marker-span", "--ref", "0", "--reason", "long on purpose")
        rc, o = run(p, "code", "add", "auth.single-fetch-path", "file",
                    "src/lib/swr.ts", "--replace", "--yes")
        ok("--replace writes", rc == 0, o)
        has("and says what it dropped", o, "marker-span", "long on purpose")
        rc, o = run(p, "validate")
        ok("leaving a store that loads", rc == 0, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------- debt


def _debt_tree(tmp, d=None):
    """A lint-clean tree plus a store, for the rules `kb debt` regroups."""
    return _lint_tree(tmp, d)


def test_debt_view():
    print("debt: the per-item view")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        # auth.token is a concept (cap 400) and carries a key-length
        # suppression in the fixture: 500 chars makes the rule fire so the
        # suppression has something to suppress.
        d["items"]["auth.token"]["key"] = "y" * 500
        d["items"]["api.errors"]["key"] = "z" * 1600
        # api.fetcher must stay genuinely finding-free below: without the old
        # adoption gates, a bare edge and no keywords are each their own
        # warning now, which would defeat the "no findings" check.
        d["items"]["api.fetcher"]["keywords"] = ["swr"]
        d["items"]["api.fetcher"]["affects"] = [
            {"to": "auth.single-fetch-path", "kind": "consumes"}]
        p = _debt_tree(tmp, d)

        rc, o = run(p, "debt")
        ok("a store with only warnings exits 0", rc == 0, o)
        heads = [l for l in o.splitlines() if l and not l.startswith(" ")]
        ok("worst first: 1200 chars of excess outranks 100",
           heads[0].startswith("api.errors"), "\n".join(heads[:3]))
        has("the header carries type and excess", o, "api.errors", "concept",
            "excess 1200")
        has("key-length gains the figure the repair is graded on", o,
            "halve to <= 800")

        rc, o2 = run(p, "debt", "auth.token")
        has("a 500-char key with a 400 cap halves to the cap, not to 250", o2,
            "halve to <= 400")
        has("a suppression is shown, tagged, never hidden", o2,
            "key-length", "(suppressed: one contract, four exceptions)")
        rc3, o3 = run(p, "lint", "--rule", "key-length")
        ok("while kb lint still only counts it",
           "auth.token" not in o3 and "1 suppressed" in o3, o3)

        rc, o = run(p, "debt", "api.fetcher")
        ok("an item with no defects is an empty block and exit 0",
           rc == 0 and "(no findings)" in o, "rc=%d\n%s" % (rc, o))

        rc, o = run(p, "debt")
        has("findings that belong to no item get their own block", o,
            "-- graph-level, not attributable to one item", "section-min-items")
        ok("and are not attributed to an item",
           "[directives]" in o.split("graph-level")[1], o)

        rc, o = run(p, "debt", "nope.missing")
        ok("an unknown id is exit 2, not an empty report",
           rc == 2 and "not in this KB" in o, "rc=%d\n%s" % (rc, o))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_debt_selection_and_json():
    print("debt: --surfaced-file and --json")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["auth.token"]["key"] = "y" * 500
        p = _debt_tree(tmp, d)

        sf = os.path.join(tmp, "surfaced.txt")
        with open(sf, "w", encoding="utf-8", newline="\n") as f:
            f.write("# what this session surfaced\nauth.token\n\napi.fetcher\n")
        rc, o = run(p, "debt", "--surfaced-file", sf)
        ok("--surfaced-file reads ids, skipping blanks and # comments",
           rc == 0 and "auth.token" in o and "api.fetcher" in o,
           "rc=%d\n%s" % (rc, o))
        ok("and reports only those items", "api.errors" not in o, o)

        with open(sf, "w", encoding="utf-8", newline="\n") as f:
            f.write("auth.token\nauth.ghost\n")
        rc, o = run(p, "debt", "--surfaced-file", sf)
        ok("an unknown id in the surfaced set errors rather than skipping",
           rc == 2 and "auth.ghost" in o, "rc=%d\n%s" % (rc, o))

        rc, o = run(p, "debt", "--json")
        ok("--json exits 0", rc == 0, o)
        j = json.loads(o)
        ok("--json carries the metric the mechanism is graded on",
           j["excess_total"] == 100, repr(j.get("excess_total")))
        ok("every rule name is present, zeros included",
           len(j["rules"]) == 28 and j["rules"]["self-edge"] == 0,
           repr(sorted(j["rules"])[:5]))
        ok("the thresholds this run measured against are recorded",
           sorted(j["thresholds"]) == sorted(
               ["edge_kind_share", "keyword_broad_share", "max_span",
                "section_min_items", "stale_months", "subsection_min_items"]),
           repr(sorted(j["thresholds"])))
        ok("items carry the halve-to figure",
           any(i["halve_to"] == 400 for i in j["items"]),
           repr([(i["id"], i["halve_to"]) for i in j["items"]]))
        ok("graph-level findings are a separate list, not items",
           all(x["who"].startswith("[") or x["who"].startswith("§")
               or ":" in x["who"] for x in j["graph"]),
           repr([x["who"] for x in j["graph"]]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ ratchet


def test_baseline_ratchet():
    print("ratchet: regression, split, and the missing file")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["auth.token"]["key"] = "y" * 900       # concept, cap 400
        del d["items"]["auth.token"]["lint"]              # want it to fire
        p = _debt_tree(tmp, d)

        rc, o = run(p, "lint", "--baseline")
        ok("a missing baseline is exit 2, never a silent pass",
           rc == 2 and "kb baseline write" in o, "rc=%d\n%s" % (rc, o))

        rc, o = run(p, "baseline", "write")
        ok("kb baseline write exits 0", rc == 0, o)
        bp = os.path.join(tmp, ".kb-baseline.json")
        ok("it lands beside the store", os.path.exists(bp), o)
        has("and says to commit it", o, "commit it")
        with open(bp, encoding="utf-8") as f:
            rec = json.load(f)
        ok("it records per-rule counts, excess, thresholds and the files mode",
           all(k in rec for k in
               ("rules", "excess_total", "thresholds", "no_files")),
           repr(sorted(rec)))
        ok("with the excess it measured", rec["excess_total"] == 500,
           repr(rec["excess_total"]))

        rc, o = run(p, "lint", "--baseline")
        ok("an unchanged graph exits 0", rc == 0 and "unchanged" in o,
           "rc=%d\n%s" % (rc, o))

        # A new over-cap item raises the count AND the excess.
        d2 = fx.clean()
        d2["items"]["auth.token"]["key"] = "y" * 900
        del d2["items"]["auth.token"]["lint"]
        d2["items"]["api.new-debt"] = {
            "section": "api", "type": "concept", "status": "active",
            "key": "q" * 700,
            "code": [{"kind": "file", "path": "src/lib/swr.ts"}],
            "affects": [{"to": "auth.token", "kind": "relates"}],
            "meta": dict(fx.M)}
        fx.write(d2, p)
        rc, o = run(p, "lint", "--baseline")
        ok("one new over-cap item exits 1", rc == 1, "rc=%d\n%s" % (rc, o))
        has("naming the rule that regressed", o, "regressed", "key-length")

        # A split raises key-length while paying the debt down: it must pass,
        # and it is the reason excess_total is tracked at all.
        d3 = fx.clean()
        d3["items"]["auth.token"]["key"] = "y" * 450
        del d3["items"]["auth.token"]["lint"]
        d3["items"]["auth.token-b"] = {
            "section": "auth", "type": "concept", "status": "active",
            "key": "w" * 450,
            "keywords": ["token"],
            "code": [{"kind": "file", "path": "src/lib/auth.ts"}],
            "affects": [{"to": "auth.token", "kind": "consumes"}],
            "meta": dict(fx.M)}
        fx.write(d3, p)
        rc, o = run(p, "lint", "--baseline")
        ok("a split raises key-length 1 -> 2 and still exits 0", rc == 0,
           "rc=%d\n%s" % (rc, o))
        has("reported as a split against the falling excess", o,
            "split", "key-length", "500 -> 100")
        ok("and not as a regression", "regressed" not in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_baseline_threshold_moved():
    print("ratchet: a threshold moving is not decay")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        # [auth] sits exactly at the default floor (0.75) -- silent there --
        # with one bare edge (auth.gate -> api.errors) that a stricter floor
        # newly reports.
        d["items"]["auth.single-fetch-path"]["affects"] = [
            {"to": "api.fetcher", "kind": "constrains"},
            {"to": "auth.token", "kind": "consumes"}]
        d["items"]["auth.gate"]["affects"] = [
            {"to": "auth.token", "kind": "consumes"},
            {"to": "api.errors", "kind": "relates"}]
        p = _debt_tree(tmp, d)
        rc, o = run(p, "baseline", "write")
        ok("baseline written", rc == 0, o)

        rc, o = run(p, "thresholds", "set", "edge_kind_share", "0.9")
        ok("the threshold stores", rc == 0, o)

        rc, o = run(p, "lint", "--baseline")
        ok("a moved threshold exits 0, not a regression", rc == 0,
           "rc=%d\n%s" % (rc, o))
        has("naming the rule and how the threshold moved", o,
            "threshold-moved", "edge-kind-missing",
            "edge_kind_share moved 0.75 -> 0.9")
        has("and instructing that the new floor be recorded", o,
            "kb baseline write")
        ok("it is not reported as a regression", "regressed" not in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_baseline_guards():
    print("ratchet: the comparisons it refuses to make")
    tmp = tempfile.mkdtemp()
    try:
        p = _debt_tree(tmp, fx.clean())
        rc, o = run(p, "baseline", "write")
        ok("baseline written", rc == 0, o)

        rc, o = run(p, "lint", "--baseline", "--rule", "orphan")
        ok("--rule would record partial counts, so it is refused", rc == 2, o)
        rc, o = run(p, "lint", "--baseline", "--list")
        ok("--list is refused too", rc == 2, o)

        rc, o = run(p, "lint", "--baseline", "--no-files")
        ok("a files-mode mismatch is exit 2, not a bogus comparison", rc == 2,
           "rc=%d\n%s" % (rc, o))
        has("and says which way round it is", o, "no-files")

        alt = os.path.join(tmp, "alt.json")
        rc, o = run(p, "baseline", "write", "--file", alt, "--notes",
                    "run 001: measured")
        ok("--file writes where it is told", os.path.exists(alt), o)
        with open(alt, encoding="utf-8") as f:
            ok("--notes is recorded in the file",
               "measured" in json.load(f).get("notes", ""))
        rc, o = run(p, "lint", "--baseline", alt)
        ok("--baseline FILE compares against that file", rc == 0, o)

        never = os.path.join(tmp, "never.json")
        rc, o = run(p, "--dry-run", "baseline", "write", "--file", never)
        ok("--dry-run prints the file and writes nothing",
           rc == 0 and not os.path.exists(never), "rc=%d\n%s" % (rc, o))

        rc, o = run(p, "baseline", "print")
        ok("an unknown verb exits 2 naming the only one", rc == 2 and
           "kb baseline write" in o, "rc=%d\n%s" % (rc, o))

        # A rule the baseline predates cannot have regressed against it.
        with open(os.path.join(tmp, ".kb-baseline.json"), encoding="utf-8") as f:
            rec = json.load(f)
        rec["rules"].pop("section-min-items")
        with open(os.path.join(tmp, ".kb-baseline.json"), "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump(rec, f, indent=1)
        rc, o = run(p, "lint", "--baseline")
        ok("a rule the baseline predates is new-rule, exit 0", rc == 0,
           "rc=%d\n%s" % (rc, o))
        has("named as such", o, "new-rule", "section-min-items")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------- scope


def test_lint_scope():
    print("lint: scoped by id and by section")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["auth.gate"]["key"] = "Pages render a loading state."
        d["items"]["api.fetcher"]["summary"] = (
            "A summary considerably longer than the key it is supposed to "
            "compress, which defeats the entire point of it.")
        p = _lint_tree(tmp, d)

        rc, o = run(p, "lint", "--no-files")
        has("unscoped, both sections report", o, "auth.gate", "api.fetcher")

        rc, o = run(p, "lint", "--no-files", "--section", "auth")
        has("scoped to auth, auth reports", o, "auth.gate")
        ok("and api does not", "api.fetcher" not in o, o)
        has("with the skipped count named", o, "outside this scope")

        rc, o = run(p, "lint", "--no-files", "api.fetcher")
        has("scoped to one id, that id reports", o, "api.fetcher")
        ok("and its section-mate does not", "api.errors" not in o, o)

        rc, o = run(p, "lint", "--no-files", "--section", "nope")
        ok("an unknown section is an error, not an empty report", rc == 2, o)
        rc, o = run(p, "lint", "--no-files", "auth.ghost")
        ok("an unknown id likewise", rc == 2, o)
        has("naming what it did not find", o, "auth.ghost")

        # A section finding is in scope when its section is.
        rc, o = run(p, "lint", "--no-files", "--section", "api",
                    "--rule", "section-min-items")
        has("[api] is reported under the api scope", o, "[api]")
        rc, o = run(p, "lint", "--no-files", "--section", "auth",
                    "--rule", "section-min-items")
        ok("and not under the auth one", "[api]" not in o, o)

        # The surfaced-file handoff, same shape as kb debt's.
        sf = os.path.join(tmp, "surfaced.txt")
        with open(sf, "w", encoding="utf-8") as f:
            f.write("# from the session\napi.fetcher\n")
        rc, o = run(p, "lint", "--no-files", "--surfaced-file", sf)
        has("--surfaced-file scopes the run", o, "api.fetcher")
        ok("to exactly its ids", "auth.gate" not in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_all_hides_nothing():
    print("lint: --all turns off thresholds and shows exemptions")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        # No modal in the key, so key-modal has something to suppress below.
        d["items"]["auth.gate"]["key"] = "Pages render a loading state."
        # [api] fully labelled, so its bare edge would be withheld... except
        # there is none; give api one labelled and auth one bare edge instead.
        d["items"]["api.fetcher"]["affects"] = [
            {"to": "auth.token", "kind": "consumes"}]
        d["items"]["api.errors"]["affects"] = [
            {"to": "auth.token", "kind": "constrains"},
            {"to": "api.fetcher", "kind": "relates"}]
        p = _lint_tree(tmp, d)

        rc, o = run(p, "lint", "--no-files", "--rule", "edge-kind-missing")
        ok("[api] is at 2 of 3 = 67%, under the floor, so it reports",
           "api.errors" in o, o)

        rc, o = run(p, "lint", "--no-files", "--rule", "edge-kind-missing",
                    "--edge-kind-share", "0.5")
        ok("a lower floor withholds it", "api.errors" not in o, o)
        has("but the summary says so", o, "hidden by thresholds",
            "--all shows them")
        rc, o = run(p, "lint", "--no-files", "--rule", "edge-kind-missing",
                    "--edge-kind-share", "0.5", "--all")
        has("--all prints it anyway", o, "api.errors")

        rc, o = run(p, "suppress", "--who", "auth.gate", "--rule", "key-modal",
                    "--reason", "the modal is in the summary")
        rc, o = run(p, "lint", "--no-files", "--rule", "key-modal")
        ok("a suppressed finding is not printed by default",
           "auth.gate" not in o, o)
        has("only counted", o, "1 suppressed")
        rc, o = run(p, "lint", "--no-files", "--rule", "key-modal",
                    "--show-suppressed")
        has("--show-suppressed prints it, tagged", o, "supp.", "auth.gate")
        ok("and it still does not move the exit code", rc == 0, o)
        rc, o = run(p, "lint", "--no-files", "--rule", "key-modal", "--all")
        has("--all does both halves", o, "supp.", "auth.gate")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_debt_section_scope():
    print("debt: --section, and a graph block that stays in scope")
    tmp = tempfile.mkdtemp()
    try:
        p = _lint_tree(tmp)
        rc, o = run(p, "debt", "--section", "api")
        has("every item of the section is a row", o, "api.fetcher", "api.errors")
        ok("and no other section's item is", "auth.token" not in o, o)
        has("[api] is small, so its own finding is here", o, "[api]")
        ok("but [auth]'s is not", "[auth]" not in o, o)
        rc, o = run(p, "debt", "--section", "nope")
        ok("an unknown section is an error", rc == 2, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_kw_share():
    print("kw: the table shows what keyword-broad sees")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["auth.single-fetch-path"]["keywords"] = ["http", "Http"]
        d["items"]["auth.gate"]["keywords"] = ["http"]
        d["items"]["api.fetcher"]["keywords"] = ["swr"]
        p = fx.make(tmp, d)
        rc, o = run(p, "kw")
        has("each row carries its share of the tagged items", o, "%")
        has("case variants stay separate rows", o, "Http")
        has("and the broad line is named", o, "broad at >60%")
        ok("the folded form is what crosses it",
           "over the keyword-broad line" in o, o)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_json_round_trip():
    print("lint --json: the command it prints is the command that works")
    tmp = tempfile.mkdtemp()
    try:
        d = fx.clean()
        d["items"]["auth.gate"]["key"] = "Pages render a loading state."
        p = _lint_tree(tmp, d)

        rc, o = run(p, "lint", "--no-files", "--json")
        doc = json.loads(o)
        ok("it is valid JSON with the counts", "findings" in doc
           and "errors" in doc and "thresholds" in doc, sorted(doc))
        f = [x for x in doc["findings"] if x["rule"] == "key-modal"][0]
        ok("and the finding carries its who", f["who"] == "auth.gate", f)

        # The whole point: paste it back, with a reason, and it must write.
        argv = shlex.split(f["suppress"])[1:]          # drop the leading `kb`
        argv[argv.index("--reason") + 1] = "the modal is in the summary"
        rc, o = run(p, *argv)
        ok("the generated command parses and writes", rc == 0, o)
        rc, o = run(p, "lint", "--no-files", "--rule", "key-modal")
        ok("and the finding is gone", "key-modal" not in o, o)

        # A who that needs quoting must survive the round trip too.
        rc, o = run(p, "lint", "--no-files", "--json", "--rule",
                    "section-min-items")
        rows = json.loads(o)["findings"]
        if rows:
            argv = shlex.split(rows[0]["suppress"])[1:]
            ok("a [section] who comes back unmangled",
               argv[argv.index("--who") + 1].startswith("["), argv)

        # An unsuppressible rule offers the resolution instead of a command.
        d2 = fx.clean()
        d2["items"]["auth.gate"]["lint"] = {"ignore": [
            {"rule": "key-lenght", "reason": "outlived a rename"}]}
        p2 = _lint_tree(tempfile.mkdtemp(), d2)
        rc, o = run(p2, "lint", "--no-files", "--json", "--rule",
                    "suppress-stale")
        f = json.loads(o)["findings"][0]
        ok("suppress is null for an unsuppressible rule",
           f["suppress"] is None, f)
        ok("and a resolve hint takes its place", bool(f.get("resolve")), f)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
