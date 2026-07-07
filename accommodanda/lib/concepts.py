"""Concept (begrepp) normalization: collapse the inflected surface forms of a
legal term onto one canonical concept, so SFS definitions, DV nyckelord, EU
defined terms and the hand-authored wiki pages all land on the same
`begrepp/<Name>` node.

The vocabulary is bounded (defined legal terms), so this is a hand-rolled,
**corpus-aware** Swedish noun de-inflector, not a general lemmatizer:

  * `_bases(form)` proposes the plausible base (indefinite-singular) forms of a
    term by *reversing* each inflectional ending. Ambiguous endings yield several
    candidates -- notably `-arna`, the definite plural of both an `-are` agent
    noun (`näringsidkarna` → `näringsidkare`) and an `-ar` plural (`bilarna` →
    `bil`). A bare `-are` is NEVER stripped: it is the agent *base*, so `domare`
    does not reduce to `dom`.
  * `cluster(forms)` unions each form only with candidate bases that are
    *themselves observed forms* -- so the corpus decides which reading is real.
    The canonical display/URI form of a group is a wiki-authored form if present
    (the wiki uses base form by convention), else the most base-like member.

A hand-edited override file (`data/begrepp_aliases.json`) maps stubborn variants and
true synonyms onto a canonical, and lists forms to KEEP DISTINCT (blocking a
wrong auto-merge). De-inflection only touches a term's last word (the head in
this corpus's compounds and `X av/för Y` phrases); casing and whitespace are
folded so `på Internet` / `på internet` are one concept.
"""

import json
from pathlib import Path

from .util import normalize_fold as _norm

RES = Path(__file__).resolve().parent / "data" / "begrepp_aliases.json"

# generic inflectional endings reversed to a base (definite singular, plural and
# definite plural). NOT -are (an agent base) and NOT derivational (-ning/-het/
# -else), which would merge unrelated words.
_ENDINGS = ("arna", "erna", "orna", "na", "ar", "er", "or", "en", "et", "n", "t")


def _bases(word):
    """Candidate base forms of a lower-cased Swedish noun, by reversing each
    inflectional ending (several when ambiguous); empty when it looks like a base.
    The corpus picks the real one (`cluster` keeps only observed candidates)."""
    out = set()
    forms = {word}
    if word.endswith("s") and len(word) > 4:     # genitive -> also the plain form
        forms.add(word[:-1])
    for w in forms:
        if w.endswith("arna") and len(w) > 6:    # -are agent noun, definite plural
            out.add(w[:-4] + "are")              #   näringsidkarna -> näringsidkare
        if w.endswith("aren") and len(w) > 5:    # -are agent noun, definite singular
            out.add(w[:-1])                      #   näringsidkaren -> näringsidkare
        for end in _ENDINGS:                     # generic plural / definite
            if w.endswith(end) and len(w) - len(end) >= 3:
                out.add(w[:-len(end)])
    out.discard(word)
    return out


def _last_word_bases(form):
    """Candidate base forms of a whole (lower-cased) term: its last word
    de-inflected, the rest kept (the head inflects in this corpus's terms)."""
    parts = form.split(" ")
    return {" ".join(parts[:-1] + [b]) for b in _bases(parts[-1])} if parts else set()




def _ucfirst(name):
    return name[0].upper() + name[1:] if name else name


def _base_score(form):
    """Lower is more base-like: shorter wins, a definite/plural ending is a
    tie-break penalty (so `Borgenär` beats `Borgenären`)."""
    inflected = bool(_bases(form.lower()))
    return (len(form), inflected, form)


def canonical_form(forms):
    """The display/URI form for a group of surface variants: a wiki-authored form
    if the group has one, else the most base-like member."""
    wiki = [f for f in forms if f in _wiki_titles()]
    return _ucfirst(min(wiki or list(forms), key=_base_score))


# --------------------------------------------------------------------------
# overrides (hand-edited) + the wiki base-form registry
# --------------------------------------------------------------------------

_OVERRIDES = None
_WIKI = None


def _load():
    global _OVERRIDES
    if _OVERRIDES is None:
        data = json.loads(RES.read_text())
        _OVERRIDES = {"alias": {_norm(k): v for k, v in data.get("alias", {}).items()},
                      "distinct": [{_norm(x) for x in p}
                                   for p in data.get("keep_distinct", [])]}
    return _OVERRIDES


def _wiki_titles():
    return _WIKI or set()


def register_wiki(titles):
    """Tell the normalizer which display forms are wiki-authored (base form by
    convention), so they win canonical selection and never silently move."""
    global _WIKI
    _WIKI = set(titles)


# --------------------------------------------------------------------------
# clustering -- the corpus-wide canonicalisation
# --------------------------------------------------------------------------

def cluster(forms):
    """Group surface `forms` into concepts: `{canonical: sorted([variants])}`. A
    form unions with a candidate base only when that base is itself observed (or
    a hand-edited alias target); keep-distinct pairs are split back apart."""
    over = _load()
    by_norm = {}
    for f in forms:
        by_norm.setdefault(_norm(f), set()).add(f)
    parent = {k: k for k in by_norm}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        if a in parent and b in parent and find(a) != find(b):
            parent[find(a)] = find(b)

    for k in list(by_norm):
        target = over["alias"].get(k)
        if target:
            union(k, _norm(target))              # explicit alias wins
        for cand in _last_word_bases(k):
            if cand in by_norm:
                union(k, cand)

    comps = {}
    for k in by_norm:
        comps.setdefault(find(k), set()).update(by_norm[k])

    out = {}
    for members in comps.values():
        for sub in _split_distinct(members):
            # an explicit alias target is the canonical (a human decision); else
            # the wiki form, else the most base-like member
            targets = sorted(over["alias"][_norm(m)] for m in sub
                             if _norm(m) in over["alias"])
            out[_ucfirst(targets[0]) if targets else canonical_form(sub)] = sorted(sub)
    return out


def _split_distinct(members):
    """Split a group so no keep-distinct pair shares it (a wrong auto-merge the
    override forbids). Members off every distinct list stay with the first part."""
    norms = {m: _norm(m) for m in members}
    pairs = [d for d in _load()["distinct"]
             if len(d & set(norms.values())) > 1]
    if not pairs:
        return [members]
    parts = [{m for m in members if norms[m] in d} for d in pairs]
    rest = {m for m in members if not any(norms[m] in d for d in pairs)}
    if parts:
        parts[0] |= rest
    return [p for p in parts if p]
