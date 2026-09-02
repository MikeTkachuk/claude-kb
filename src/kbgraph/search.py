"""Relevance scoring over the graph.

Ported unchanged in spirit from v1: an IDF-weighted bag of terms per item,
built from its key, keywords, code basenames and id. It backs `find`,
`dup`, `relate` and `ctx --query`, none of which want an index on disk.
"""

import math
import os
import re
from collections import defaultdict

STOP = set(
    """the a an and or of to in for on with is are be by not no nor all every must
    never only exactly always this that it its as at from when then than which what
    who whose if else via use uses used using into onto out per each both any some
    same other there here we you they them their our your but can will would should
    has have had was were been being does did done was new one two also just still
    such not none non yes may might shall about after before over under between""".split()
)


def tokens(s):
    out = []
    for p in re.findall(r"[A-Za-z][A-Za-z0-9_]*", s or ""):
        parts = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", p).lower().split()
        for t in [p.lower()] + parts:
            if len(t) >= 3 and t not in STOP:
                out.append(t)
    return out


# kb:begin search.idf-bag
def item_terms(it):
    bag = defaultdict(float)
    for t in set(tokens(it.key)):
        bag[t] += 1.0
    for k in it.keywords:
        for t in set(tokens(k)):
            bag[t] += 3.0
    for p in it.code_paths:
        for t in set(tokens(os.path.basename(p))):
            bag[t] += 2.0
    for c in it.code:
        for t in set(tokens(c.symbol or "")):
            bag[t] += 2.0
    for t in set(tokens(it.name)):
        bag[t] += 1.5
    return bag
# kb:end search.idf-bag


def build_idf(items):
    df = defaultdict(int)
    n = max(1, len(items))
    for it in items:
        for t in set(item_terms(it)):
            df[t] += 1
    return {t: math.log(1 + n / (1.0 + c)) for t, c in df.items()}


def score(items, text, code=None, exclude=(), idf=None):
    """[(score, item)] best first. `code` biases toward the same directory."""
    items = list(items)
    idf = idf if idf is not None else build_idf(items)
    q = defaultdict(float)
    for t in tokens(text):
        q[t] += 1.0
    if code:
        for t in tokens(os.path.basename(code)):
            q[t] += 2.0
    if not q:
        return []
    # kb:begin search.code-dir-bias
    qdir = os.path.dirname(code.replace("\\", "/")) if code else None
    scored = []
    for it in items:
        if it.id in exclude:
            continue
        bag = item_terms(it)
        s = sum(w * bag[t] * idf.get(t, 1.0) for t, w in q.items() if t in bag)
        if qdir:
            for p in it.code_paths:
                if os.path.dirname(p.replace("\\", "/")) == qdir:
                    s *= 1.6
                    break
        # kb:end search.code-dir-bias
        if s > 0:
            scored.append((s, it))
    scored.sort(key=lambda x: (-x[0], x[1].id))
    return scored
