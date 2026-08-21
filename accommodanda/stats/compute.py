"""Compute the 53 corpus measurements into a `Report`.

Two data sources, deliberately in this order of preference:

1. **The catalog** -- anything expressible as SQL over `documents`/`links` and
   the derived tables. Seconds, and it is the same view the site itself serves.
2. **The artifact trees** (`scan`) -- the per-document and per-node numbers the
   catalog does not hold. Minutes, and the reason `compute` is its own verb.

The split is also the roadmap: every measure that has to reach for `scan` today
is one `relate` could serve from SQL tomorrow (see the PRD's R1-R3).

The population is gällande rätt unless a measure says otherwise: `_in_force`
narrows ``laws`` once, and a measure that needs the whole history (churn,
lifespan, how many acts have been repealed) asks for ``laws_all`` by name.

Each measure still stamps its `title`/`lede` into the artifact, which keeps
the dated snapshots self-describing -- but the *page* takes its prose from
stats.html (1:1 with what renders), except the ledes whose sentences embed
measured values: those the template pulls from the artifact
(``computed_lede``), so the words can never outrun the figures beside them.
A per-figure caveat about population lives in stats.html too, as `stat()`'s
own ``note=`` -- prose beside prose. What is *arithmetic* belongs in the lede
instead: measure 4 names each of its five exclusions with its count, because a
lede that names two of five reads as though the other three do not exist.
"""

import collections
import datetime
import re
import statistics
from concurrent.futures import ProcessPoolExecutor

from ..lib import catalog, layout, util
from ..lib.facets import flow_group
from ..lib.markdown import begrepp_uri
from ..lib.page import register_anchor
from ..lib.pinpoint import citation
from . import scan
from .model import Cell, Measure, Point, Report, Row, Tile

BASE = "https://lagen.nu/"
# the SFS download tree: where change-act titles live (`scan.scan_sfs_register`)
SFS_DOWNLOADED = layout.SFS_DOWNLOADED


def _q(con, sql, params=()):
    return con.execute(sql, params).fetchall()


def _rows(records, label=0, value=1, uri=None, detail=None):
    return [Row(label=str(r[label]), value=r[value],
                uri=(r[uri] if uri is not None else None),
                detail=(str(r[detail]) if detail is not None and r[detail] else None))
            for r in records]


def _series(pairs):
    return [Point(x=str(x), y=y) for x, y in pairs]


def _rank_profile(values, k=120, log=False):
    """`values` sorted largest first and sampled at `k` ranks: a bar per rank
    whose height is that thing's *own* size, never a bucket count. Every
    sampled bar is a real member of the population (its x is the rank), so the
    curve's endpoints are the actual record holders.

    `log` spaces the sampled ranks geometrically -- 1, 2, 3, … 10 … 100 …
    5 000, each step a multiplication -- instead of evenly. Every measure that
    asks it of a corpus whose largest member is a thousand times its median,
    which is all of them here: socialförsäkringsbalken is 1 700 times the
    median statute, so an evenly sampled axis spends 119 of its 120 columns on
    values that are all the same height, and draws one tall bar beside a flat
    line. The head collapses to consecutive ranks under the geometric step (no
    corpus has a rank 1.4), so the column count comes out below `k`."""
    ordered = sorted(values, reverse=True)
    if len(ordered) <= k:
        idx = range(len(ordered))
    elif log:
        idx = sorted({round(len(ordered) ** (i / (k - 1))) - 1 for i in range(k)})
    else:
        idx = [round(i * (len(ordered) - 1) / (k - 1)) for i in range(k)]
    return [Point("{:,}".format(i + 1).replace(",", " "), ordered[i])
            for i in idx]


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0.0


def in_force(col="expired"):
    """SQL for "not repealed *yet*" -- a repeal date that has not arrived does
    not make an act repealed.

    Ellag (1997:857) carries an upphävandedatum of 2027-01-01 and is law until
    then; `expired IS NULL` alone reads it, and 16 other live statutes among them
    Konsumentkreditlagen and Lag om mottagande av asylsökande, as already gone.
    The distinction is the same one the search layer draws at query time
    (`search.REPEALED_IN_FORCE`), evaluated against `now` so it stays right
    between builds rather than being frozen at compute time."""
    return "(%s IS NULL OR %s > date('now'))" % (col, col)


def repealed(col="expired"):
    """SQL for "repealed, and the repeal has taken effect" -- the complement of
    `in_force`. An act repealed as of next year has not lived its life yet, so it
    belongs in neither the lifespan measures nor this year's repeal count."""
    return "(%s IS NOT NULL AND %s <= date('now'))" % (col, col)


def _shorten(text, limit=200):
    """A row label cut where reading stops paying: the longest titles are the
    row's *value*, and five 1 300-character labels would drown the list that
    is supposed to show them off."""
    return text if len(text) <= limit else text[:limit] + "…"


def _paragraf_label(t):
    """The citing form for a paragraf extreme: ``9 kap. 62 § Förordning om EU:s
    gemensamma jordbrukspolitik``.

    No dash between pinpoint and title -- that *is* the Swedish citation form,
    and an em dash made it read as two separate things. The pinpoint is
    chapter-qualified (scan.py builds it from the anchor): a bare "62 §" of a
    chaptered statute names nothing."""
    return "%s %s" % (t[2], t[3]["clean_title"])


def _prop_label(identifier, found):
    """A proposition as a row label: its beteckning plus what it was about,
    "Prop. 2018/19:162 En ny beteckning för kommuner på regional nivå".

    The beteckning alone says nothing about what the proposition did, so the
    catalog's title earns its place -- except where that title *is* the
    beteckning again (1 603 prop artifacts carry no title of their own), which
    would print the identifier twice. `found` is None for a proposition the
    register cites but the catalog does not hold."""
    title = found[1] if found else ""
    return ("%s %s" % (identifier, title[:70])
            if title and not title.startswith(identifier) else identifier)


def _definition_place(definitions):
    """Where an act states its definitions, as (anchor, citation) for the *first*
    one in reading order -- ("4", "artikel 4 och 7 andra"), ("K1P5", "1 kap. 5 §").

    An act may hold several definition articles: CRR states 188 definitions in
    8 of them, each headed "Definitioner" (4, 5, 142, 192, 242, 272, 300, 411).
    The row has to land the reader on one rather than on the act's first page,
    so the link goes to the first in reading order and the count goes in the
    text beside it. Each definition carries its own place and the citation for
    it (`scan._definition`'s callers), because the two corpora write one
    differently and an anchor is not always the citation: 82/714/EEG's
    "Artikel 1.01" anchors as "1-001". (None, None) when the act states its
    definitions nowhere citable."""
    places = dict.fromkeys((d["place"], d["place_label"])
                           for d in definitions if d["place"])
    if not places:
        return None, None
    (anchor, where), *rest = places
    return anchor, (where if not rest
                    else "%s och %d andra" % (where, len(rest)))


def _definition_key(body):
    """Two definitions are the same definition when their text is the same --
    compared with case and trailing punctuation set aside, so "personuppgifter"
    is not credited with a second definition for a full stop."""
    return re.sub(r"[\s.,;:]+$", "", " ".join(body.split()).lower())


def _paragraf_uri(t):
    """The paragraf's own url, not its statute's -- the row promises a specific
    paragraf, so the link has to land on it. The anchor is the node id the
    renderer already emits as the element's `id` (scan.py carries it)."""
    return "%s#%s" % (t[3]["uri"], t[1]) if t[1] else t[3]["uri"]


# ==========================================================================
# the scan pass
# ==========================================================================

def run_scans(jobs=None, progress=None):
    """Walk every artifact tree once. Returns the bundle the measure builders
    read. `progress` is called with a stage name so a long run says where it is."""
    def say(stage):
        if progress:
            progress(stage)

    with ProcessPoolExecutor(max_workers=jobs) as pool:
        say("sfs")
        sfs = list(pool.map(scan.scan_sfs, layout.artifacts("sfs"), chunksize=32))
        say("sfs-register")
        registers = list(pool.map(
            scan.scan_sfs_register,
            sorted(SFS_DOWNLOADED.rglob("*.json*")), chunksize=64))
        say("eurlex")
        eurlex = [r for r in pool.map(scan.scan_eurlex, layout.artifacts("eurlex"),
                                      chunksize=64) if r]
        say("forarbete")
        forarbete = [r for r in pool.map(scan.scan_forarbete,
                                         layout.artifacts("forarbete"),
                                         chunksize=64) if r]
        say("dv")
        dv = [r for r in pool.map(scan.scan_dv, layout.artifacts("dv"),
                                  chunksize=64) if r]
    say("remisser")
    return {"laws": [r for r in sfs if r["kind"] == "law"],
            "versions": [r for r in sfs if r["kind"] == "version"],
            "registers": registers, "eurlex": eurlex,
            "forarbete": forarbete, "dv": dv,
            "remisser": scan.scan_remisser()}


# ==========================================================================
# A. the statute book: size and shape (1-9, 53-54)
# ==========================================================================
#
# The size-and-shape measures share one form, the rank profile: every value
# drawn at its rank, largest first (`_rank_profile`), with the 5 largest and
# 5 smallest named as a plain list under the curve -- the columns already did
# the comparing, so the list draws no second set of bars.

def _group_a(con, s):
    laws = s["laws"]                    # gällande rätt (see `_in_force`)

    # 1 -- body length per statute. The shape is the point: a few giants and
    # a very long tail of two-paragraph förordningar.
    sized = sorted((r for r in laws if r["chars"] > 0),
                   key=lambda r: -r["chars"])
    yield Measure(
        1, "A", "Lagars längd", "profile", unit="tecken",
        lede="Kroppstext i tecken, per konsoliderad författning. Formen är "
             "poängen: några få jättar och en mycket lång svans av "
             "tvåparagrafsförordningar.",
        xlabel="plats i längdordning (logaritmisk)", ylabel="tecken",
        points=_rank_profile([r["chars"] for r in sized], log=True),
        rows=([Row(_shorten(r["clean_title"]), r["chars"], r["uri"],
                   group="Längst") for r in sized[:5]]
              + [Row(_shorten(r["clean_title"]), r["chars"], r["uri"],
                     group="Kortast") for r in sized[-5:][::-1]]))

    by_parag = sorted(laws, key=lambda r: -r["paragrafer"])
    yield Measure(
        2, "A", "Antal paragrafer", "profile", unit="paragrafer",
        lede="Antal § per författning; kapitelantalet står som detalj.",
        xlabel="plats i längdordning (logaritmisk)", ylabel="paragrafer",
        points=_rank_profile([r["paragrafer"] for r in by_parag], log=True),
        rows=([Row(_shorten(r["clean_title"]), r["paragrafer"], r["uri"],
                   "%d kap." % r["kapitel"] if r["kapitel"] else None,
                   group="Flest") for r in by_parag[:5]]
              + [Row(_shorten(r["clean_title"]), r["paragrafer"], r["uri"],
                     group="Minst") for r in by_parag[-5:][::-1]]))

    # 3 -- the paragraf lengths, and (4) the same measure for EU articles
    plens = [(c, a, o, r) for r in laws for c, a, o in r["paragraf_lengths"]]
    lengths = [c for c, *_ in plens]
    yield Measure(
        3, "A", "Paragraflängd i svenska författningar", "profile",
        unit="tecken",
        # the count is the real paragraf count, not len(plens): a paragraf whose
        # body is only a renumbering stub or an editorial note carries no length
        # row (scan.py), so measuring the corpus by the rows here would publish
        # a number several thousand short of the paragrafer that exist
        lede="Gällande rätt har %s paragrafer, varav %s går att mäta. "
             "Medianparagrafen är %d tecken; medelvärdet %d."
             % ("{:,}".format(sum(r["paragrafer"] for r in laws)).replace(",", " "),
                "{:,}".format(len(plens)).replace(",", " "),
                statistics.median(lengths), statistics.mean(lengths)),
        xlabel="plats i längdordning (logaritmisk)", ylabel="tecken",
        points=_rank_profile([c for c in lengths if c > 0], log=True),
        rows=([Row(_paragraf_label(t), t[0], _paragraf_uri(t), group="Längst")
               for t in sorted(plens, key=lambda t: -t[0])[:5]]
              + [Row(_paragraf_label(t), t[0], _paragraf_uri(t), group="Kortast")
                 for t in sorted((x for x in plens if x[0] > 0),
                                 key=lambda t: t[0])[:5]]))

    # 4 -- the population is narrower than "every article node" on purpose:
    # single-instrument documents (in an accession act's bundle "Art. 5" names
    # an article of *which* instrument?), the Swedish manifestation (a tecken
    # measure over mixed languages compares nothing), no ändringsakter (their
    # articles quote the amended act -- CRR2's "Article 1" is 680 000 tecken
    # of quoted CRR), and no articles that swallowed furniture (scan.RE_STRAY,
    # the older tiers' runaway-article defect).
    total_articles = sum(len(a["lengths"]) for a in s["eurlex"])
    single = [a for a in s["eurlex"] if not a["multi_instrument"]]
    swedish = [a for a in single if a["lang"] == "swe"]
    eu_own = [(c, n, a) for a in swedish if not a["amending"]
              for c, n, clean in a["lengths"] if clean and c > 0]
    # every article the population drops, counted where it is dropped: a lede
    # that names two of five exclusions reads as though the other three do not
    # exist. The furniture count is the one that is our defect rather than the
    # corpus's shape -- those articles are still live on the site.
    excluded = [
        ("i ändringsakter", sum(len(a["lengths"]) for a in swedish if a["amending"])),
        ("i dokument som buntar flera akter",
         sum(len(a["lengths"]) for a in s["eurlex"] if a["multi_instrument"])),
        ("i andra språkversioner än den svenska",
         sum(len(a["lengths"]) for a in single if a["lang"] != "swe")),
        ("med inläst sidhuvud eller signaturblock",
         sum(1 for a in swedish if not a["amending"]
             for _c, _n, clean in a["lengths"] if not clean)),
        ("utan text", sum(1 for a in swedish if not a["amending"]
                          for c, _n, clean in a["lengths"] if clean and c <= 0)),
    ]
    yield Measure(
        4, "A", "Artikellängd i EU-rätten", "profile", unit="tecken",
        lede="Databasen har %s artiklar i CELEX sektor 1 (fördrag) och 3 "
             "(lagstiftning); %s mäts. Ej med i beräkningarna: %s. "
             "Median %d tecken."
             % ("{:,}".format(total_articles).replace(",", " "),
                "{:,}".format(len(eu_own)).replace(",", " "),
                ", ".join("%s %s" % ("{:,}".format(count).replace(",", " "), what)
                          for what, count in excluded),
                statistics.median(t[0] for t in eu_own)),
        xlabel="plats i längdordning (logaritmisk)", ylabel="tecken",
        points=_rank_profile([t[0] for t in eu_own], log=True),
        rows=([Row("Art. %s — %s" % (t[1], t[2]["title"][:70] or t[2]["celex"]),
                   t[0], BASE + "ext/celex/" + t[2]["celex"], group="Längst")
               for t in sorted(eu_own, key=lambda t: -t[0])[:5]]
              + [Row("Art. %s — %s" % (t[1], t[2]["title"][:70] or t[2]["celex"]),
                     t[0], BASE + "ext/celex/" + t[2]["celex"], group="Kortast")
                 for t in sorted(eu_own, key=lambda t: t[0])[:5]]))

    titled = sorted((r for r in laws if r["clean_title"]),
                    key=lambda r: len(r["clean_title"]))
    yield Measure(
        5, "A", "Rubriklängd i svenska författningar", "profile",
        unit="tecken",
        # the caveat this lede used to carry (the beteckning is not counted)
        # sits in the template's note -- measure 6 compares its EU median
        # against this one, so the number belongs in the prose beside it
        lede="Medianrubriken bland %s gällande författningar är %d tecken."
             % ("{:,}".format(len(titled)).replace(",", " "),
                statistics.median(len(r["clean_title"]) for r in titled)),
        xlabel="plats i längdordning", ylabel="tecken",
        points=_rank_profile([len(r["clean_title"]) for r in titled]),
        rows=([Row(_shorten(r["clean_title"]), len(r["clean_title"]), r["uri"],
                   group="Längst") for r in titled[-5:][::-1]]
              + [Row(r["clean_title"], len(r["clean_title"]), r["uri"],
                     group="Kortast") for r in titled[:5]]))

    # 6 -- measure 5 asked of the EU acts, where the answer is another sport
    # entirely. Population: single-instrument acts whose articles parsed, in
    # the Swedish manifestation. An empty shell (a corrigendum note whose whole
    # body is "TILL LÄSAREN") has a heading, not a rubrik, and a fördragspaket's
    # title names only the first instrument in the bundle. Measure 4's language
    # rule holds here too and for the same reason -- a tecken measure over
    # mixed languages compares nothing, and the lede compares this median
    # against the Swedish statute median from measure 5. 1 653 English
    # manifestations were in the population, and dropping them moves the
    # median from 231 to 234. Ändringsakter stay: their articles quote the act
    # they amend, but their *title* is an ordinary title.
    eu_titled = sorted((a for a in swedish if a["title"] and a["lengths"]),
                       key=lambda a: len(a["title"]))
    eu_tl = [len(a["title"]) for a in eu_titled]
    yield Measure(
        6, "A", "Rubriklängd i EU-rätten", "profile", unit="tecken",
        lede="En EU-akts rubrik är hela den officiella titeln: utfärdare, "
             "beteckning, datum och ärende i en enda mening. Medianen bland "
             "%s akter på svenska är %d tecken — den svenska medianrubriken "
             "är %d tecken."
             % ("{:,}".format(len(eu_titled)).replace(",", " "),
                statistics.median(eu_tl),
                statistics.median(len(r["clean_title"]) for r in titled)),
        xlabel="plats i längdordning", ylabel="tecken",
        points=_rank_profile(eu_tl),
        rows=([Row(_shorten(a["title"]), len(a["title"]),
                   BASE + "ext/celex/" + a["celex"], group="Längst")
               for a in eu_titled[-5:][::-1]]
              + [Row(a["title"], len(a["title"]),
                     BASE + "ext/celex/" + a["celex"], group="Kortast")
                 for a in eu_titled[:5]]))

    named = [r for r in laws if r["alternate"]]
    yield Measure(
        7, "A", "Lagar med eget namn", "scalar", unit="författningar",
        value=len(named),
        display="%d av %d (%.1f %%)" % (len(named), len(laws),
                                        _pct(len(named), len(laws))),
        lede="Författningar som bär en etablerad förkortning (BrB, RB, ABL, IL) — "
             "kända nog att ha ett smeknamn.",
        rows=[Row(r["alternate"], r["chars"], r["uri"], r["clean_title"][:60])
              for r in sorted(named, key=lambda r: -r["chars"])[:10]])

    dep = collections.Counter(r["department"] for r in laws if r["department"])
    yield Measure(
        8, "A", "Författningar per departement", "bars", unit="författningar",
        lede="Vem som äger regelmassan.",
        xlabel="departement", ylabel="författningar",
        points=_series((d.replace("_", " ").capitalize(), n)
                       for d, n in dep.most_common(12)))

    total_chars = sum(r["chars"] for r in laws)
    words = total_chars // 6
    hours = words / 150 / 60
    # three numbers, not one, so this scalar answers as a KPI row rather than a
    # hero line: "1 234 · 5 678 · 42" set at display size ran as one sentence
    # and wrapped mid-figure. Each number keeps its own tile and its own unit
    yield Measure(
        9, "A", "Hela svensk författningssamling i siffror", "scalar",
        unit="tecken",
        value=total_chars,
        display="%s tecken · %s ord · %d dygns högläsning" % (
            "{:,}".format(total_chars).replace(",", " "),
            "{:,}".format(words).replace(",", " "), hours / 24),
        tiles=[Tile("{:,}".format(total_chars).replace(",", " "), "tecken"),
               Tile("{:,}".format(words).replace(",", " "), "ord"),
               Tile("%d" % (hours / 24), "dygns högläsning")],
        lede="All gällande författningstext tillsammans. Med ett tempo på 150 ord i minuten "
             "tar det %d dygn (utan paus) att läsa upp den." % (hours / 24))

    # 53-54 -- the defined vocabulary. Only an explicit definition statement is
    # counted (scan.definition_body_*): the EU definitions article's points, and
    # the Swedish term list and löptext forms. Population is gällande rätt on the
    # Swedish side (`laws` is already narrowed); the EU side has no in-force data
    # in the catalog, so it is every act of sectors 1 and 3 we hold.
    eu_defs = [r for r in s["eurlex"] if r["definitions"]]
    sfs_defs = [r for r in laws if r["definitions"]]
    descriptive = dict(_q(con, "SELECT uri, descriptive FROM documents "
                               "WHERE source IN ('sfs','eurlex') "
                               "  AND descriptive IS NOT NULL"))

    def act_row(r, group):
        # the row promises the act's definitions, so it links to the article or
        # paragraf that states them, not to the act's first page
        anchor, where = _definition_place(r["definitions"])
        return Row(_shorten(descriptive.get(r["uri"]) or r["title"], 70),
                   len(r["definitions"]),
                   "%s#%s" % (r["uri"], anchor) if anchor else r["uri"],
                   where, group=group)

    yield Measure(
        53, "A", "Rättsakterna med flest definitioner", "toplist",
        unit="definitioner",
        lede="%s EU-rättsakter och %s gällande svenska författningar räknar upp "
             "sina egna definitioner. Tillsammans blir det %s definitioner. "
             "Varje definierad term länkas sedan där den används i resten av "
             "akten."
             % ("{:,}".format(len(eu_defs)).replace(",", " "),
                "{:,}".format(len(sfs_defs)).replace(",", " "),
                "{:,}".format(sum(len(r["definitions"])
                                  for r in eu_defs + sfs_defs)).replace(",", " ")),
        rows=([act_row(r, "EU-rättsakter")
               for r in sorted(eu_defs, key=lambda r: -len(r["definitions"]))[:10]]
              + [act_row(r, "Svenska författningar")
                 for r in sorted(sfs_defs, key=lambda r: -len(r["definitions"]))[:6]]))

    # A begrepp's definitions, one entry per *textually distinct* wording. NIS2
    # art. 6.9 and CER-direktivet art. 2.6 both define "risk" and differ, so they
    # are two; a definition that only points elsewhere ("personuppgifter:
    # personuppgifter enligt definitionen i artikel 4.1 i förordning (EU)
    # 2016/679") states none of its own and is left out.
    #
    # Keyed on the *concept*, not on the surface form the act happens to write:
    # a term's identity in this corpus is its begrepp uri after the inflection
    # fold (`catalog.canonicalize_concepts`), which is what the page the row
    # links to counts. Keyed on the surface form instead, 382 concepts split in
    # two (`Personuppgift` into personuppgift + personuppgifter) and the row
    # printed a number the page it points at contradicts.
    canonical = dict(_q(con, "SELECT variant, canonical FROM concept_alias"))
    concept_name = dict(_q(con, "SELECT uri, label FROM documents "
                                "WHERE source = 'begrepp'"))
    wordings = collections.defaultdict(set)
    holders = collections.defaultdict(set)
    terms: dict[str, str] = {}     # concept uri -> the term as the acts write it
    stated = crossrefs = 0
    for r in eu_defs + sfs_defs:
        for d in r["definitions"]:
            stated += 1
            if d["xref"]:
                crossrefs += 1
                continue
            uri = begrepp_uri(d["term"])
            uri = canonical.get(uri, uri)
            wordings[uri].add(_definition_key(d["body"]))
            holders[uri].add(r["uri"])
            terms.setdefault(uri, d["term"])
    ranked = sorted(wordings.items(), key=lambda kv: -len(kv[1]))[:12]
    yield Measure(
        54, "A", "Begreppen som definieras på flest sätt", "toplist",
        unit="olika definitioner",
        lede="Rättskällorna ställer upp %s definitioner. %s av dem hänvisar bara "
             "vidare till en annan text och räknas inte som egna. De övriga "
             "definierar %s olika begrepp, och två av dem räknas som samma "
             "definition när de har samma lydelse."
             % ("{:,}".format(stated).replace(",", " "),
                "{:,}".format(crossrefs).replace(",", " "),
                "{:,}".format(len(wordings)).replace(",", " ")),
        # every concept the catalog holds carries a label; one it does not hold
        # gets neither a name nor a link, and is named by the term as the acts
        # write it. 1 901 of 22 283 defined concepts have no `documents` row --
        # none of them near the top, but the row must not link to a page that
        # is not there
        rows=[Row(concept_name.get(uri) or terms[uri], len(bodies),
                  uri if uri in concept_name else None,
                  "i %d dokument" % len(holders[uri]))
              for uri, bodies in ranked])


# ==========================================================================
# B. change and churn (10-20)
# ==========================================================================

def _group_b(con, s):
    laws = s["laws"]                    # gällande rätt (see `_in_force`)

    # 10 -- the two ends of one distribution, which used to be two measures: a
    # toplist of the most-amended laws (10) and a scalar counting the never-
    # amended (13). They are the head and the tail of the same rank profile, and
    # apart neither showed that the tail is most of the corpus.
    #
    # The count is one less than the amendment list: `register.Register.acts`
    # opens that list with the grundförfattning itself, so a law with a single
    # entry has never been changed -- which is exactly the test measure 13 made
    # (`len(amendments) <= 1`), now applied to both ends alike.
    changes = {r["uri"]: max(len(r["amendments"]) - 1, 0) for r in laws}
    by_changes = sorted(laws, key=lambda r: -changes[r["uri"]])
    never = [r for r in laws if changes[r["uri"]] == 0]
    yield Measure(
        10, "B", "Ändringar per lag", "profile", unit="ändringar",
        lede="Varje gällande författning på sin plats i ändringsordningen. "
             "%s av %s har aldrig ändrats; den mest ändrade har ändrats %s gånger."
             % ("{:,}".format(len(never)).replace(",", " "),
                "{:,}".format(len(laws)).replace(",", " "),
                "{:,}".format(changes[by_changes[0]["uri"]]).replace(",", " ")),
        xlabel="plats i ändringsordning (logaritmisk)", ylabel="ändringar",
        points=_rank_profile(list(changes.values()), log=True),
        rows=([Row(_shorten(r["clean_title"]), changes[r["uri"]], r["uri"],
                   group="Mest ändrade") for r in by_changes[:6]]
              + [Row(_shorten(r["clean_title"]), 0, r["uri"], r["ikraft"],
                     group="Aldrig ändrade, äldst först")
                 for r in sorted((x for x in never if x["ikraft"]),
                                 key=lambda r: r["ikraft"])[:6]]))

    # 11 -- the chain depth, read out of the download tree's change-act titles.
    # The row links into the base statute's own amendment register, at the entry
    # for this change act (`.../1949:105#L2025:191`) -- the chain is written in
    # that entry's rubrik, so the link lands the reader on the sentence measured
    labels = dict(_q(con, "SELECT label, uri FROM documents WHERE source='sfs'"))
    chains = [(base, bet, rubrik, depth)
              for rows in s["registers"] for base, bet, rubrik, depth in rows
              if depth >= 2]
    seen, deepest = set(), []
    for base, bet, rubrik, depth in sorted(chains, key=lambda t: -t[3]):
        if bet in seen:
            continue
        seen.add(bet)
        deepest.append((labels.get("SFS " + base), bet, rubrik, depth))
    yield Measure(
        11, "B", "”Lag om ändring i lagen om ändring i lagen om…”", "toplist",
        unit="led",
        lede="En ändringsförfattning kan ändra en ändringsförfattning som ändrar "
             "en annan. De längsta kedjorna i registret, räknat i led.",
        # the chains themselves, longest first -- the distribution behind them
        # said how many were 2 and 3 links deep, which is the uninteresting part:
        # the point of this measure is that the deep ones exist at all
        rows=[Row(r[:150], d, uri and uri + "#" + register_anchor(b), b)
              for uri, b, r, d in deepest[:10]])

    touch = collections.defaultdict(set)
    for r in laws:
        for a in r["amendments"]:
            if a["id"]:
                touch[a["id"]].add(r["clean_title"])
    yield Measure(
        12, "B", "Ändringsförfattningen som rör flest lagar samtidigt", "toplist",
        unit="lagar",
        lede="En enda författning kan skriva om hela regelmassan på en gång.",
        rows=[Row(k, len(v), None, sorted(v)[0][:60] + " m.fl.")
              for k, v in sorted(touch.items(), key=lambda t: -len(t[1]))[:10]])

    # 13 was "Lagar som aldrig har ändrats" -- now the tail of measure 10, where
    # it stands beside the head it is the counterweight to

    byyear = collections.Counter()
    for r in laws:
        for a in r["amendments"]:
            if a["ikraft"]:
                byyear[a["ikraft"][:4]] += 1
    today = datetime.date.today().year
    yield Measure(
        14, "B", "Ändringar per år", "series", unit="ändringar",
        lede="Takten har legat stadigt kring tusen till tvåtusen ikraftträdda "
             "ändringar om året sedan 1970-talet.",
        xlabel="år", ylabel="ändringar",
        points=_series(sorted((y, n) for y, n in byyear.items()
                              if y.isdigit() and 1900 <= int(y) <= today)))

    gaps = []
    for r in laws:
        dates = sorted(a["ikraft"] for a in r["amendments"] if a["ikraft"])
        if len(dates) >= 2 and r["ikraft"] and dates[-1] > r["ikraft"]:
            later = [d for d in dates if d > r["ikraft"]]
            if later:
                gaps.append(((datetime.date.fromisoformat(later[0])
                              - datetime.date.fromisoformat(r["ikraft"])).days, r,
                             later[0]))
    yield Measure(
        15, "B", "Snabbast ändrade lag", "toplist", unit="dygn",
        lede="Tiden från att lagen trädde i kraft till att den första ändringen "
             "gjorde det.",
        rows=[Row(r["clean_title"][:80], d, r["uri"], "%s → %s" % (r["ikraft"], first))
              for d, r, first in sorted(gaps, key=lambda t: t[0])[:10]])

    # 16 -- how old the text in force actually is
    ages = []
    for r in laws:
        dates = [a["ikraft"] for a in r["amendments"] if a["ikraft"]]
        if dates and r["paragrafer"]:
            ages.append((max(dates), r))
    yield Measure(
        16, "B", "Lagar med äldst kvarvarande text", "toplist", unit="år",
        lede="Gällande författningar vars senaste ändring ligger längst tillbaka "
             "— regelmassans orörda botten.",
        rows=[Row(r["clean_title"][:80], today - int(d[:4]), r["uri"], d)
              for d, r in sorted(ages, key=lambda t: t[0])[:10]])

    # 17 -- the same question asked per paragraf: how old is the text that is
    # actually in force? A law is not "from 1962" because its SFS number is; it
    # is a mosaic of paragrafer of very different ages, and the register says
    # which amendment last touched each one (`ersatter`/`inforsI`).
    weighted = [(age, int(r["ikraft"][:4]), r) for r in laws
                if (age := text_age(r)) is not None]
    # each row's value is the number of years its group ranks on, never the mean
    # year itself: a row that reads "1 989" under a column headed "år" reads as a
    # count, and a bar drawn from zero to the year 1989 is 99.7 % of the bar
    # drawn to 1994 -- six full-width bars saying nothing
    yield Measure(
        17, "B", "Lagtextens medelålder", "toplist", unit="år",
        lede="En lag är en mosaik: varje paragraf fick sin nuvarande lydelse "
             "ett visst år, och snittet över lagens paragrafer är det år texten "
             "i genomsnitt skrevs. %s lagar har ett register som är utförligt "
             "nog att räkna så, och deras medeltext är från %d."
             % ("{:,}".format(len(weighted)).replace(",", " "),
                statistics.mean([m for m, _, _ in weighted]) if weighted else 0),
        # `born` is the year the act took *effect*, not its SFS year -- brottsbalken
        # is 1962:700 but came into force in 1965, so the detail says "i kraft"
        rows=([Row(r["clean_title"][:70], today - round(mean), r["uri"],
                   "medeltexten är från %d; lagen i kraft %d" % (mean, born),
                   group="Äldst kvarvarande text — textens ålder")
               for mean, born, r in sorted(weighted, key=lambda t: t[0])[:6]]
              + [Row(r["clean_title"][:70], round(mean - born), r["uri"],
                     "i kraft %d, medeltexten från %d" % (born, mean),
                     group="Mest omskrivna — år mellan ikraftträdande och medeltext")
                 for mean, born, r in sorted(weighted,
                                             key=lambda t: t[0] - t[1])[-6:][::-1]]))

    changed = collections.Counter()
    introduced = collections.Counter()
    for r in laws:
        for a in r["amendments"]:
            for anchor in a["ersatter"]:
                changed[anchor] += 1
            for anchor in a["inforsI"]:
                introduced[anchor] += 1
    # the register anchors a rewritten paragraf on the *base* act's uri
    # ("https://lagen.nu/1962:700#K3P1"), which is the catalog key too, so the
    # act's citing name comes straight off `documents`
    names = dict(_q(con, "SELECT uri, descriptive FROM documents "
                         "WHERE source='sfs' AND descriptive IS NOT NULL"))
    yield Measure(
        18, "B", "Den mest omskrivna enskilda paragrafen", "toplist",
        unit="omskrivningar",
        lede="Hur många gånger en och samma paragraf har fått ny lydelse.",
        rows=[Row(citation(u, names.get(u.partition("#")[0])), n, u)
              for u, n in changed.most_common(12)])

    verbs = collections.Counter()
    verb_year = collections.defaultdict(collections.Counter)
    for r in laws:
        for a in r["amendments"]:
            omf, year = a["omfattning"].lower(), (a["ikraft"] or "")[:4]
            for key, pattern in (("ändring", "ändr"), ("upphävande", "upph"),
                                 ("ny paragraf", "ny "), ("omtryck", "omtryck"),
                                 ("ombeteckning", "betecknas")):
                if pattern in omf:
                    verbs[key] += 1
                    if year.isdigit():
                        verb_year[key][year] += 1
    yield Measure(
        19, "B", "Vad ändringar faktiskt gör", "bars", unit="ändringar",
        lede="Fördelningen av registrets omfattningstext i kategorier. En "
             "ändringsförfattning kan göra flera saker och räknas då i flera.",
        xlabel="åtgärd", ylabel="förekomster",
        points=_series(verbs.most_common()))

    vc = collections.Counter(v["of"] for v in s["versions"])
    # titles come from the whole history, not from `laws`: the deepest version
    # stacks belong to statutes that have since been repealed, and looking them
    # up in the in-force list only would render them as a bare "1962:700"
    by_uri = {r["uri"]: r for r in s["laws_all"]}
    yield Measure(
        20, "B", "Tidsmaskinens djup", "toplist", unit="versioner",
        lede="Hur många historiska lydelser lagen.nu kan visa. Totalt %d "
             "versioner mot %d gällande lagar." % (len(s["versions"]), len(laws)),
        rows=[Row(by_uri[u]["clean_title"] if u in by_uri else u.replace(BASE, ""),
                  n, u)
              for u, n in vc.most_common(12)])


# ==========================================================================
# C. time, lifespan, mortality (21-28)
# ==========================================================================

def _group_c(con, s):
    today = datetime.date.today().isoformat()
    yield Measure(
        21, "C", "Äldsta lagar som fortfarande gäller", "toplist", unit="år",
        lede="Regelmassans äldsta lager, fortfarande i kraft.",
        rows=[Row(t, int(today[:4]) - int(d[:4]), u, d) for u, t, d in
              _q(con, "SELECT uri, title, date FROM documents WHERE source='sfs' "
                      "AND " + in_force() + " AND date > '1500' "
                      "ORDER BY date LIMIT 12")])

    # 22 -- lifespan of the repealed, both ends of one quantity: the profile
    # is in dygn so the short end keeps day resolution, the long end's detail
    # translates to years
    span = _q(con, "SELECT uri, title, date, expired, "
                   "CAST(julianday(expired)-julianday(date) AS INT) d "
                   "FROM documents WHERE source='sfs' AND " + repealed() + " "
                   "AND date > '1500' AND d >= 0 ORDER BY d DESC")
    yield Measure(
        22, "C", "Livslängd för upphävda lagar", "profile", unit="dygn",
        lede="Tiden från utfärdande till upphävande. Somliga författningar "
             "överlever sekler; andra hinner knappt träda i kraft innan de "
             "avskaffas.",
        xlabel="plats i längdordning", ylabel="dygn",
        points=_rank_profile([d for *_, d in span]),
        rows=([Row(_shorten(t), d, u, "%s → %s (%d år)" % (a, e, d / 365.25),
                   group="Längst") for u, t, a, e, d in span[:5]]
              + [Row(_shorten(t), d, u, "%s → %s" % (a, e), group="Kortast")
                 for u, t, a, e, d in span[-5:][::-1]]))

    yield Measure(
        23, "C", "Upphävanden per år", "series", unit="upphävanden",
        lede="Hur mycket regelmassa som avvecklas varje år.",
        xlabel="år", ylabel="upphävda författningar",
        points=_series(_q(con, "SELECT substr(expired,1,4) y, count(*) "
                               "FROM documents WHERE source='sfs' "
                               "AND " + repealed() + " AND y BETWEEN '1900' AND ? "
                               "GROUP BY 1 ORDER BY 1", (today[:4],))))

    # 24 -- the survival curve: the single most informative series here
    issued = dict(_q(con, "SELECT substr(date,1,4), count(*) FROM documents "
                          "WHERE source='sfs' AND date BETWEEN '1900' AND ? "
                          "GROUP BY 1", (today,)))
    alive = dict(_q(con, "SELECT substr(date,1,4), count(*) FROM documents "
                         "WHERE source='sfs' AND " + in_force() + " "
                         "AND date BETWEEN '1900' AND ? GROUP BY 1", (today,)))
    yield Measure(
        24, "C", "Överlevnadskurva", "series", unit="procent",
        lede="Av alla författningar utfärdade år X — hur stor andel gäller "
             "fortfarande idag? Kurvan är svensk lagstiftnings halveringstid.",
        xlabel="utfärdandeår", ylabel="% kvar i kraft",
        points=_series(sorted((y, _pct(alive.get(y, 0), n))
                              for y, n in issued.items() if n >= 5)))

    yield Measure(
        25, "C", "Vilken dag träder svensk lag i kraft?", "bars", unit="ändringar",
        lede="Ikraftträdandedatumen samlade på månad. Nästan allt träder i "
             "kraft 1 januari eller 1 juli. Månaderna däremellan är nästan "
             "tomma.",
        xlabel="månad", ylabel="ikraftträdda ändringar",
        # the whole history: this describes when Sweden *has* brought changes
        # into force, and an amendment to an act repealed since was no less a
        # real ikraftträdande. Narrowing to gällande rätt would draw the curve
        # only from the acts that happened to survive (rule: `_in_force`)
        points=_series(_ikraft_months(s["laws_all"])))

    # 26 -- the notice period. Only grundförfattningar: the amendment register
    # dates the ikraftträdande but not the utfärdande (11 of 50 948 entries carry
    # one, and the download tree has none either), so the same curve drawn over
    # changes would describe registration practice rather than lawmaking.
    # whole history, for the same reason as 27: how much notice lawmaking has
    # given is a fact about the past, not about what survives today
    notice = sorted(((d, r) for r in s["laws_all"]
                     if (d := notice_days(r)) is not None), key=lambda t: -t[0])
    days = sorted(d for d, _ in notice)
    yield Measure(
        26, "C", "Hur långt varsel får en ny lag?", "profile", unit="dygn",
        lede="Tiden från att författningen utfärdas till att den träder i kraft, "
             "för var och en av %s grundförfattningar. Medianen är %d dygn; en "
             "fjärdedel får %d dygn eller mindre, och %s träder i kraft samma "
             "dag som de utfärdas."
             % ("{:,}".format(len(days)).replace(",", " "),
                statistics.median(days) if days else 0,
                days[len(days) // 4] if days else 0,
                "{:,}".format(sum(1 for d in days if d == 0)).replace(",", " ")),
        xlabel="plats i varselordning (logaritmisk)", ylabel="dygn",
        points=_rank_profile([d for d, _ in notice], log=True),
        rows=([Row(r["clean_title"][:70], d, r["uri"],
                   "utfärdad %s, i kraft %s" % (r["utfardad"], r["ikraft"]),
                   group="Längst varsel") for d, r in notice[:5]]
              + [Row(r["clean_title"][:70], d, r["uri"],
                     "utfärdad och i kraft %s" % r["ikraft"],
                     group="Kortast varsel") for d, r in notice[-5:][::-1]]))

    future = collections.Counter()
    for r in s["laws"]:
        for a in r["amendments"]:
            if a["ikraft"] and a["ikraft"] > today:
                future[a["ikraft"][:4]] += 1
    yield Measure(
        27, "C", "Framtiden som redan är skriven", "bars", unit="ändringar",
        lede="Ändringar som är beslutade men ännu inte i kraft — lagstiftning "
             "som redan finns men inte gäller.",
        xlabel="ikraftträdandeår", ylabel="beslutade ändringar",
        points=_series(sorted(future.items())))

    dec = collections.Counter()
    for (date,) in _q(con, "SELECT date FROM documents WHERE source='sfs' "
                           "AND " + in_force() + " AND date > '1800'"):
        dec[date[:3] + "0"] += 1
    yield Measure(
        28, "C", "Nya författningar per decennium", "bars", unit="författningar",
        lede="Bland dem som fortfarande gäller — vilket visar hur snabbt äldre "
             "lager tunnas ut snarare än hur mycket som skrevs.",
        xlabel="decennium", ylabel="gällande författningar",
        points=_series(sorted(dec.items())))


MIN_PARAGRAFER = 20     # below this a mean over paragrafer says little
MIN_ANCHORED = 0.9      # share of dated amendments that must name what they touched


def text_age(law):
    """The mean year of the paragrafer actually in force in `law`, or None when
    the law cannot be read that way.

    A statute is not "from 1962" because its SFS number is: it is a mosaic of
    paragrafer of very different ages, and the register says which amendment last
    touched each one (`ersatter`/`inforsI`). A paragraf no amendment names is
    original text, and counts as the law's own year.

    None means the question is unanswerable here, not that the law is young: a
    register that dates its amendments but does not say *what* they touched would
    make every paragraf fall back to the law's own year and the law would read as
    wholly original. `MIN_ANCHORED` is what keeps that artefact out."""
    if not law["ikraft"] or len(law["paragraf_lengths"]) < MIN_PARAGRAFER:
        return None
    dated = [a for a in law["amendments"] if a["ikraft"]]
    anchored = [a for a in dated if a["inforsI"] or a["ersatter"]]
    if not dated or len(anchored) / len(dated) < MIN_ANCHORED:
        return None
    latest = {}
    for a in anchored:
        for anchor in a["inforsI"] + a["ersatter"]:
            frag = anchor.rsplit("#", 1)[-1]
            latest[frag] = max(latest.get(frag, ""), a["ikraft"])
    return statistics.mean(
        int((latest.get(anchor) or law["ikraft"])[:4])
        for _, anchor, _ in law["paragraf_lengths"])


def notice_days(law):
    """Days between a statute being signed and taking effect, or None if either
    date is missing. Only base statutes carry both (see `scan.scan_sfs`)."""
    if not (law["ikraft"] and law["utfardad"] and law["ikraft"] >= law["utfardad"]):
        return None
    return (datetime.date.fromisoformat(law["ikraft"])
            - datetime.date.fromisoformat(law["utfardad"])).days


def bill_lag(propdate, laws):
    """(days, prop, amendment, law) for every amendment whose register entry names
    a proposition `propdate` has a date for. One amendment can appear once per
    proposition it cites, which is what the measure counts."""
    return [((datetime.date.fromisoformat(a["ikraft"])
              - datetime.date.fromisoformat(propdate[f])).days, f, a, r)
            for r in laws for a in r["amendments"] if a["ikraft"]
            for f in a["forarbeten"]
            if f in propdate and a["ikraft"] >= propdate[f]]


MONTHS = tuple(util.MONTHS)


def _ikraft_months(laws):
    counts = collections.Counter()
    for r in laws:
        for a in r["amendments"]:
            if a["ikraft"]:
                counts[int(a["ikraft"][5:7])] += 1
    return [(MONTHS[i - 1], counts.get(i, 0)) for i in range(1, 13)]


# ==========================================================================
# D. the citation graph (29-36)
# ==========================================================================

def _flows(con):
    """Every reference counted by (citing group, cited group), largest first.

    The catalog answers per (source, kind) -- 1 900 rows -- and the grouping
    is `facets.flow_group`, shared with the /hanvisningar/ graph API, so one
    map says what a node is. References to a document the corpus does not hold
    have no cited group and are not counted; 29's lede says how many that is."""
    flows = collections.Counter()
    for src, kind, to_src, to_kind, n in _q(
            con, "SELECT d1.source, d1.kind, d2.source, d2.kind, count(*) "
                 "FROM links l JOIN documents d1 ON d1.uri = l.from_uri "
                 "JOIN documents d2 ON d2.uri = l.to_root GROUP BY 1,2,3,4"):
        flows[(flow_group(src, kind), flow_group(to_src, to_kind))] += n
    return [Cell(a, b, n)
            for (a, b), n in sorted(flows.items(), key=lambda t: -t[1])]


def _group_d(con, s):
    docs = _q(con, "SELECT count(*) FROM documents")[0][0]
    links = _q(con, "SELECT count(*) FROM links")[0][0]
    paged = _q(con, "SELECT count(*) FROM links WHERE from_page IS NOT NULL")[0][0]
    flows = _flows(con)
    # both numbers measured, neither inferred from the other: a reference the
    # flow leaves out because its target is missing is counted by its own query.
    # "links - landed" would say the same thing only while every citing document
    # is itself in the catalog, which nothing here states or checks.
    unresolved = _q(con, "SELECT count(*) FROM links l LEFT JOIN documents d "
                         "ON d.uri = l.to_root WHERE d.uri IS NULL")[0][0]
    yield Measure(
        29, "D", "Databasen i siffror", "sankey", unit="hänvisningar",
        value=links,
        display="%s hänvisningar mellan %s dokument"
                % ("{:,}".format(links).replace(",", " "),
                   "{:,}".format(docs).replace(",", " ")),
        # no characterisation of what the unresolved targets are: measured, they
        # are EU-domar 33 %, äldre författningar 22 %, EU-rättsakter 18 % and a
        # long tail, and any short phrase for that reads as a ranking the page
        # does not compute
        lede="Varje hänvisning är upplöst till ett dokument och, där källan har "
             "sidor, till sidan den står på (%s av dem). Flödet visar de %s "
             "hänvisningar som går mellan två dokument i databasen. Ytterligare "
             "%s pekar på ett dokument som databasen inte har."
             % ("{:,}".format(paged).replace(",", " "),
                "{:,}".format(sum(c.value for c in flows)).replace(",", " "),
                "{:,}".format(unresolved).replace(",", " ")),
        cells=flows)

    yield Measure(
        30, "D", "Mest hänvisade dokument", "toplist", unit="hänvisningar",
        lede="Inkommande länkar från hela databasen.",
        rows=[Row(t or u.replace(BASE, ""), c, u) for u, t, c in
              _q(con, "SELECT l.to_root, d.title, count(*) c FROM links l "
                      "LEFT JOIN documents d ON d.uri = l.to_root "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    yield Measure(
        31, "D", "Mest hänvisade enskilda paragraf", "toplist", unit="hänvisningar",
        lede="Ner på paragrafnivå — vilken enskild regel databasen faktiskt talar om.",
        rows=[Row(citation(u, desc), c, u) for u, desc, _src, c in
              _q(con, "SELECT l.to_uri, d.descriptive, d.source, count(*) c "
                      "FROM links l LEFT JOIN documents d ON d.uri = l.to_root "
                      "WHERE l.to_uri LIKE '%#%' "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    yield Measure(
        32, "D", "Dokument med flest utgående hänvisningar", "toplist",
        unit="hänvisningar",
        lede="Vilka dokument som citerar mest — utredningar, i praktiken.",
        rows=[Row(t or u.replace(BASE, ""), c, u) for u, t, c in
              _q(con, "SELECT l.from_uri, d.title, count(*) c FROM links l "
                      "JOIN documents d ON d.uri = l.from_uri "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    yield Measure(
        33, "D", "Vem citerar vem", "matrix", unit="hänvisningar",
        lede="Hänvisningsmatrisen mellan källor. Förarbetenas hänvisningar till "
             "SFS dominerar allt annat.",
        cells=[Cell(a, b, c) for a, b, c in
               _q(con, "SELECT d1.source, d2.source, count(*) FROM links l "
                       "JOIN documents d1 ON d1.uri = l.from_uri "
                       "JOIN documents d2 ON d2.uri = l.to_root "
                       "GROUP BY 1,2 HAVING count(*) > 500")])

    # 34 -- the shape of how attention is distributed, not a defect count. An
    # act nothing refers to is not orphaned: it is a self-contained island,
    # usually an administrative förordning that simply has no occasion to be
    # cited. The distribution says that far better than a single number did --
    # the zero bucket is the tall one, and the corpus still runs out to a
    # handful of acts with six-figure inbound counts.
    refs = dict(_q(con, "SELECT n, count(*) FROM (SELECT d.uri, "
                        "(SELECT count(*) FROM links l WHERE l.to_root = d.uri) n "
                        "FROM documents d WHERE d.source='sfs' "
                        "AND " + in_force("d.expired") + ") GROUP BY n"))
    bins = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 11), (11, 26),
            (26, 101), (101, 10**9)]
    labels = ["0", "1", "2", "3", "4", "5", "6–10", "11–25", "26–100", ">100"]
    counts = [sum(c for n, c in refs.items() if lo <= n < hi) for lo, hi in bins]
    yield Measure(
        34, "D", "Hur många hänvisar till en lag?", "histogram",
        unit="författningar",
        lede="Gällande författningar efter antal inkommande hänvisningar från "
             "hela databasen — lag, dom, förarbete och föreskrift tillsammans. "
             "%d av %d har inga alls; de är öar snarare än övergivna, oftast "
             "förvaltningsförordningar som ingenting har anledning att citera."
             % (counts[0], sum(counts)),
        xlabel="inkommande hänvisningar", ylabel="författningar",
        points=_series(zip(labels, counts, strict=True)))

    yield Measure(
        35, "D", "Den mest självrefererande texten", "toplist", unit="hänvisningar",
        lede="Hänvisningar från ett dokument till sig självt.",
        rows=[Row(t or u.replace(BASE, ""), c, u) for u, t, c in
              _q(con, "SELECT l.from_uri, d.title, count(*) c FROM links l "
                      "LEFT JOIN documents d ON d.uri = l.from_uri "
                      "WHERE l.from_uri = l.to_root "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    yield Measure(
        36, "D", "Mest omtalade begrepp", "toplist", unit="hänvisningar",
        lede="Begreppen rankade på antalet inkommande länkar: vilka begrepp "
             "resten av databasen hänvisar till.",
        rows=[Row(t, c, u) for u, t, c in
              _q(con, "SELECT d.uri, d.title, count(*) c FROM links l "
                      "JOIN documents d ON d.uri = l.to_root "
                      "WHERE d.source='begrepp' GROUP BY 1 ORDER BY c DESC LIMIT 12")])


# ==========================================================================
# E. preparatory works (37-43)
# ==========================================================================

def _group_e(con, s):
    kinds = dict(_q(con, "SELECT kind, count(*) FROM documents "
                         "WHERE source='forarbete' GROUP BY 1"))
    yield Measure(
        37, "E", "Förarbeten per typ", "bars", unit="dokument",
        lede="%s förarbeten totalt." % "{:,}".format(sum(kinds.values())).replace(",", " "),
        xlabel="typ", ylabel="dokument",
        points=_series(sorted(kinds.items(), key=lambda t: -t[1])))

    today = datetime.date.today().isoformat()
    per_year = collections.defaultdict(collections.Counter)
    for kind, year, n in _q(con, "SELECT kind, substr(date,1,4), count(*) "
                                 "FROM documents WHERE source='forarbete' "
                                 "AND date BETWEEN '1970' AND ? GROUP BY 1,2",
                            (today,)):
        per_year[year][kind] = n
    yield Measure(
        38, "E", "Förarbeten per år", "series", unit="dokument",
        lede="Utgivningstakten, alla typer sammanslagna.",
        xlabel="år", ylabel="dokument",
        points=_series(sorted((y, sum(c.values())) for y, c in per_year.items())))

    sou = [f for f in s["forarbete"] if f["type"] == "sou"]
    yield Measure(
        39, "E", "Tjockaste utredningen", "toplist", unit="tecken",
        lede="Teckenantal per SOU.",
        rows=[Row("%s %s" % (f["identifier"], f["title"][:60]), f["chars"], f["uri"])
              for f in sorted(sou, key=lambda f: -f["chars"])[:12]])

    fb = collections.Counter()
    fblaws = collections.defaultdict(set)
    # whole history: the question is how many laws a proposition rewrote *then*,
    # and a law it rewrote in 1994 counts even if it has since been repealed
    for r in s["laws_all"]:
        for a in r["amendments"]:
            for f in a["forarbeten"]:
                if f.lower().startswith("prop"):
                    fb[f] += 1
                    fblaws[f].add(r["uri"])
    # the register names the proposition by its citation string ("Prop.
    # 2013/14:110"), which is the catalog's `label` for the same document -- the
    # same join measure 41 makes. It buys the row both a title (a beteckning
    # alone says nothing about what the proposition did) and a link to it
    props = {label: (uri, title) for label, uri, title in
             _q(con, "SELECT label, uri, title FROM documents "
                     "WHERE source='forarbete' AND label LIKE 'Prop.%'")}
    yield Measure(
        40, "E", "Propositionen som ändrade flest lagar", "toplist", unit="lagar",
        lede="En proposition kan föreslå ändringar i hundratals lagar på en gång.",
        rows=[Row(_prop_label(k, props.get(k)), len(fblaws[k]),
                  props[k][0] if k in props else None, "%d ändringar" % fb[k])
              for k in sorted(fb, key=lambda k: -len(fblaws[k]))[:12]])

    # 41 -- how long the road from bill to binding law is. The join is by the
    # register's own reference string ("Prop. 2009/10:161"), which is the
    # catalog's `label` for the same document.
    #
    # Only day-precise prop dates count. 5 106 of the 8 822 dated propositions
    # sit on 12-31 or 01-01, which is a year stamped as a date rather than a
    # date -- included, they would put a spurious ±6 months on every old bill.
    propdate = {label: date for label, date in
                _q(con, "SELECT label, date FROM documents WHERE source='forarbete' "
                        "AND uri LIKE 'https://lagen.nu/prop/%' AND date IS NOT NULL "
                        "AND date NOT LIKE '%-12-31' AND date NOT LIKE '%-01-01'")}
    # whole history: how long the road from bill to binding law has been is a
    # fact about the lawmaking, not about which of those laws are still in force
    road = bill_lag(propdate, s["laws_all"])
    lags = sorted(t[0] for t in road)
    yield Measure(
        41, "E", "Från förslag till lag", "toplist", unit="dygn",
        lede="Tiden från propositionens datum till att ändringen träder i kraft, "
             "över %d spårbara par: medianen är %d dygn."
             % (len(road), statistics.median(lags) if lags else 0),
        rows=([Row("%s → %s" % (f, a["id"]), d, r["uri"],
                   r["clean_title"][:45], group="Längst väntan")
               for d, f, a, r in sorted(road, key=lambda t: -t[0])[:6]]
              + [Row("%s → %s" % (f, a["id"]), d, r["uri"],
                     r["clean_title"][:45], group="Kortast väntan")
                 for d, f, a, r in sorted(road, key=lambda t: t[0])[:6]]))

    fk = _q(con, "SELECT count(*) FROM fk_kommentar")[0][0]
    yield Measure(
        42, "E", "Volymen författningskommentar", "toplist", unit="kommentarer",
        lede="%s stycken kommentar till enskilda paragrafer, hämtade ur "
             "propositionerna." % "{:,}".format(fk).replace(",", " "),
        rows=[Row(t or u.replace(BASE, ""), c, u) for u, t, c in
              _q(con, "SELECT f.sfs_uri, d.title, count(*) c FROM fk_kommentar f "
                      "LEFT JOIN documents d ON d.uri = f.sfs_uri "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    corr = _q(con, "SELECT relation, scope, count(*) FROM correspondence "
                   "GROUP BY 1,2 ORDER BY 3 DESC")
    yield Measure(
        43, "E", "Paragrafmotsvarigheter", "bars", unit="motsvarigheter",
        lede="De spårade ”motsvarar / överförd från”-relationerna mellan gammal "
             "och ny lagtext — hur mycket av en ny lag som egentligen är gammal.",
        xlabel="relation", ylabel="motsvarigheter",
        points=_series(("%s %s" % (r, sc or ""), c) for r, sc, c in corr))


# ==========================================================================
# F. case law (44-48)
# ==========================================================================

def _group_f(con, s):
    courts = _q(con, "SELECT substr(label,1,3), count(*) c FROM documents "
                     "WHERE source='dv' GROUP BY 1 ORDER BY c DESC LIMIT 12")
    yield Measure(
        44, "F", "Avgöranden per domstol", "bars", unit="avgöranden",
        lede="Fördelningen över domstolar, avläst på referatbeteckningen.",
        xlabel="domstol", ylabel="avgöranden",
        points=_series(courts))

    today = datetime.date.today().isoformat()
    yield Measure(
        45, "F", "Avgöranden per år", "series", unit="avgöranden",
        lede="Publiceringstakten för refererad praxis.",
        xlabel="år", ylabel="avgöranden",
        points=_series(_q(con, "SELECT substr(date,1,4) y, count(*) FROM documents "
                               "WHERE source='dv' AND date BETWEEN '1980' AND ? "
                               "GROUP BY 1 ORDER BY 1", (today,))))

    yield Measure(
        46, "F", "Mest hänvisade rättsfall", "toplist", unit="hänvisningar",
        lede="Prejudikaten som faktiskt bär praxis.",
        rows=[Row(t, c, u) for u, t, c in
              _q(con, "SELECT d.uri, d.title, count(*) c FROM links l "
                      "JOIN documents d ON d.uri = l.to_root WHERE d.source='dv' "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    yield Measure(
        47, "F", "Vilka lagrum domstolarna citerar mest", "toplist",
        unit="hänvisningar",
        lede="Bara hänvisningar som går från en dom till en författning.",
        rows=[Row(citation(u, desc), c, u) for u, desc, _src, c in
              _q(con, "SELECT l.to_uri, t.descriptive, t.source, count(*) c "
                      "FROM links l JOIN documents d ON d.uri = l.from_uri "
                      "LEFT JOIN documents t ON t.uri = l.to_root "
                      "WHERE d.source='dv' AND l.to_uri LIKE '%#%' "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    dv = [d for d in s["dv"] if d["chars"] > 0]
    # the two lengths the lede compares. `dv` is empty only in the tests, whose
    # fixture corpus holds no verdicts -- the zeros keep the builders runnable
    # there without a guard in each format argument
    chars = [d["chars"] for d in dv] or [0]
    median_dv, longest = statistics.median(chars), max(chars)
    yield Measure(
        48, "F", "Längsta och kortaste avgörandet", "toplist", unit="tecken",
        # the minimum is deliberately not in the lede. The shortest artifacts
        # measure 8 tecken ("Not 125."), but the page for that same notisfall
        # renders a body the artifact does not carry -- so the number would
        # publish a disagreement between scan and the renderer as a fact about
        # how short a Swedish decision can be
        lede="Medianavgörandet är %s tecken. Det längsta är %d gånger så långt "
             "som medianen."
             % ("{:,}".format(int(median_dv)).replace(",", " "),
                longest / median_dv if median_dv else 0),
        rows=([Row(d["label"] or d["uri"].replace(BASE, ""), d["chars"], d["uri"],
                   d["court"], group="Längst")
               for d in sorted(dv, key=lambda d: -d["chars"])[:6]]
              + [Row(d["label"] or d["uri"].replace(BASE, ""), d["chars"], d["uri"],
                     d["court"], group="Kortast")
                 for d in sorted(dv, key=lambda d: d["chars"])[:6]]))


# ==========================================================================
# G. agency regulations, consultations and the world outside (49-52)
# ==========================================================================

def _group_g(con, s):
    pub = _q(con, "SELECT publisher, count(*) c FROM documents "
                  "WHERE source='foreskrift' AND publisher IS NOT NULL "
                  "GROUP BY 1 ORDER BY c DESC LIMIT 12")
    total = _q(con, "SELECT count(*), count(DISTINCT kind) FROM documents "
                    "WHERE source='foreskrift'")[0]
    yield Measure(
        49, "G", "Föreskrifter per myndighet", "bars", unit="föreskrifter",
        lede="%d föreskrifter ur %d författningssamlingar." % total,
        xlabel="myndighet", ylabel="föreskrifter",
        points=_series(pub))

    yield Measure(
        50, "G", "Direktiven som satt djupast spår i svensk rätt", "toplist",
        unit="paragrafer",
        lede="Antal svenska paragrafer som genomför direktivets artiklar.",
        rows=[Row(_shorten(citation(d, desc), 80), c, d) for d, desc, c in
              _q(con, "SELECT g.directive, t.descriptive, "
                      "       count(DISTINCT g.sfs_uri||g.sfs_anchor) c "
                      "FROM genomforande g "
                      "LEFT JOIN documents t ON t.uri = g.directive "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    yield Measure(
        51, "G", "Europakonventionen i svensk rätt", "toplist", unit="hänvisningar",
        lede="Vilka konventionsartiklar som faktiskt används.",
        rows=[Row("Artikel " + u.split("#A")[-1].replace("P", ".") , c, u)
              for u, c in
              _q(con, "SELECT to_uri, count(*) c FROM links "
                      "WHERE to_uri LIKE '%ext/coe/005#%' "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    cases = s["remisser"]
    orgs = collections.Counter(o for answers in cases.values() for o, _ in answers)
    titles = {case: answers[0][1] for case, answers in cases.items() if answers}
    biggest = sorted(cases.items(), key=lambda t: -len(t[1]))[:6]
    yield Measure(
        52, "G", "Remissvaren", "toplist", unit="remissvar",
        lede="%d remissvar på %d remitterade ärenden."
             % (sum(len(a) for a in cases.values()), len(cases)),
        rows=([Row(titles.get(case, case)[:80], len(answers), None, case,
                   group="Ärenden med flest svar")
               for case, answers in biggest]
              + [Row(o, n, None, group="Flitigaste remissinstanser")
                 for o, n in orgs.most_common(10)]))


# ==========================================================================

_GROUPS = (_group_a, _group_b, _group_c, _group_d, _group_e, _group_f, _group_g)


LIVE_SQL = ("SELECT uri FROM documents WHERE source='sfs' AND " + in_force())


def _in_force(con, scans):
    """`scans` with ``laws`` narrowed to the statutes actually in force, and the
    unnarrowed list kept as ``laws_all``.

    Gällande rätt is the default population: a reader asking how long the
    longest law is means one that *is* a law, not the Kommunalskattelag repealed
    in 1999. Narrowing once here rather than at 53 call sites means a measure
    that genuinely needs the whole history -- churn, lifespan, "how many have
    been repealed" -- has to reach for `laws_all` by name, so counting repealed
    acts is always a visible decision in the measure that does it, never an
    oversight in the measure that forgot to filter."""
    live = {u for (u,) in _q(con, LIVE_SQL)}
    return {**scans, "laws_all": scans["laws"],
            "laws": [r for r in scans["laws"] if r["uri"] in live]}


def compute(catalog_path, progress=None):
    """Every measurement, as a `Report`."""
    scans = run_scans(progress=progress)
    if progress:
        progress("measures")
    con = catalog.connect_ro(str(catalog_path))
    try:
        scans = _in_force(con, scans)       # once, not once per group
        measures = [m for group in _GROUPS for m in group(con, scans)]
    finally:
        con.close()
    measures.sort(key=lambda m: m.id)
    return Report(generated=datetime.date.today().isoformat(), measures=measures)
