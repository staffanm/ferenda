"""Compute the 54 corpus measurements into a `Report`.

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

Each measure still stamps its `title`/`lede`/`note` into the artifact, which
keeps the dated snapshots self-describing -- but the *page* takes its prose
from stats.html (1:1 with what renders), except the ledes whose sentences
embed measured values: those the template pulls from the artifact
(``computed_lede``), so the words can never outrun the figures beside them.
The `note` field -- a per-figure caveat about population -- is deliberately
empty for now; the ledes carry what the reader needs, and notes are re-added
case by case where a figure genuinely needs one.
"""

import collections
import datetime
import statistics
from concurrent.futures import ProcessPoolExecutor

from ..lib import catalog, layout
from ..lib.render import human_fragment
from . import scan
from .model import Cell, Measure, Point, Report, Row

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


def _paragraf_label(t):
    """The citing form for a paragraf extreme: ``9 kap. 62 § Förordning om EU:s
    gemensamma jordbrukspolitik``.

    No dash between pinpoint and title -- that *is* the Swedish citation form,
    and an em dash made it read as two separate things. The pinpoint is
    chapter-qualified (scan.py builds it from the anchor): a bare "62 §" of a
    chaptered statute names nothing."""
    return "%s %s" % (t[2], t[3]["clean_title"])


def _paragraf_uri(t):
    """The paragraf's own url, not its statute's -- the row promises a specific
    paragraf, so the link has to land on it. The anchor is the node id the
    renderer already emits as the element's `id` (scan.py carries it)."""
    return "%s#%s" % (t[3]["uri"], t[1]) if t[1] else t[3]["uri"]


def _pinpoint(uri, descriptive, source):
    """A cited lagrum as a lawyer would write it: ``6 § räntelagen``,
    ``8 kap. 7 § regeringsformen``, ``artikel 6 Europakonventionen (EKMR)``.

    The raw ``1975:635#P6`` a link carries is a machine address -- readable only
    if you already know which act 1975:635 is, which is the opposite of what a
    "most-cited paragraf" list is for. `human_fragment` turns the anchor into a
    pinpoint (it is the same rendering the site puts on inbound links), and the
    catalog's `descriptive` column is the act's compact citing name.

    EU anchors are the bare article number, which `human_fragment` cannot type
    on its own -- an eurlex fragment is always an article, so say so. Anything
    still unnamed falls back to the raw path rather than inventing a name."""
    root, _, frag = uri.partition("#")
    name = descriptive or root.replace(BASE, "")
    where = human_fragment(frag)
    if not where and frag:
        where = "artikel %s" % frag if source == "eurlex" else frag
    return "%s %s" % (where, name) if where else name


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
# A. the statute book: size and shape (1-10)
# ==========================================================================

def _group_a(con, s):
    laws = s["laws"]                    # gällande rätt (see `_in_force`)
    by_chars = sorted(laws, key=lambda r: -r["chars"])

    yield Measure(
        1, "A", "De längsta lagarna", "toplist", unit="tecken",
        lede="Kroppstext i tecken, per konsoliderad författning.",
        rows=[Row(r["clean_title"], r["chars"], r["uri"]) for r in by_chars[:12]])

    yield Measure(
        2, "A", "De kortaste lagarna", "toplist", unit="tecken",
        lede="Samma mått, andra änden.",
        rows=[Row(r["clean_title"], r["chars"], r["uri"])
              for r in sorted((x for x in laws if x["chars"] > 0),
                              key=lambda r: r["chars"])[:12]])

    yield Measure(
        3, "A", "Flest paragrafer", "toplist", unit="paragrafer",
        lede="Antal § per författning; kapitelantalet står som detalj.",
        rows=[Row(r["clean_title"], r["paragrafer"], r["uri"],
                  "%d kap." % r["kapitel"] if r["kapitel"] else None)
              for r in sorted(laws, key=lambda r: -r["paragrafer"])[:12]])

    # 4 -- the paragraf extremes, and the same measure for EU articles
    plens = [(c, a, o, r) for r in laws for c, a, o in r["paragraf_lengths"]]
    lengths = [c for c, *_ in plens]
    yield Measure(
        4, "A", "Längsta och kortaste paragrafen", "toplist", unit="tecken",
        # the count is the real paragraf count, not len(plens): a paragraf whose
        # body is only a renumbering stub or an editorial note carries no length
        # row (scan.py), so measuring the corpus by the rows here would publish
        # a number several thousand short of the paragrafer that exist
        lede="Gällande rätt har %s paragrafer, varav %s går att mäta. "
             "Medianparagrafen är %d tecken; medelvärdet %d — fördelningen är "
             "kraftigt högersvansad."
             % ("{:,}".format(sum(r["paragrafer"] for r in laws)).replace(",", " "),
                "{:,}".format(len(plens)).replace(",", " "),
                statistics.median(lengths), statistics.mean(lengths)),
        rows=([Row(_paragraf_label(t), t[0], _paragraf_uri(t), group="Längst")
               for t in sorted(plens, key=lambda t: -t[0])[:6]]
              + [Row(_paragraf_label(t), t[0], _paragraf_uri(t), group="Kortast")
                 for t in sorted((x for x in plens if x[0] > 0),
                                 key=lambda t: t[0])[:6]]))

    eu_lengths = [c for a in s["eurlex"] for c, _ in a["lengths"]]
    single = [a for a in s["eurlex"] if not a["multi_instrument"]]
    eu_single = [(c, n, a) for a in single for c, n in a["lengths"]]
    yield Measure(
        5, "A", "Längsta och kortaste EU-artikeln", "toplist", unit="tecken",
        lede="%d artiklar i CELEX sektor 1 (fördrag) och 3 (lagstiftning). "
             "Median %d tecken."
             % (len(eu_lengths), statistics.median(eu_lengths) if eu_lengths else 0),
        rows=([Row("Art. %s — %s" % (t[1], t[2]["title"][:70] or t[2]["celex"]),
                   t[0], BASE + "ext/celex/" + t[2]["celex"], group="Längst")
               for t in sorted(eu_single, key=lambda t: -t[0])[:6]]
              + [Row("Art. %s — %s" % (t[1], t[2]["title"][:70] or t[2]["celex"]),
                     t[0], BASE + "ext/celex/" + t[2]["celex"], group="Kortast")
                 for t in sorted((x for x in eu_single if x[0] > 0),
                                 key=lambda t: t[0])[:6]]))

    # 6 -- the shape of the distribution, which is the whole point
    bins = [(0, 500), (500, 1000), (1000, 2500), (2500, 5000), (5000, 10000),
            (10000, 25000), (25000, 50000), (50000, 100000), (100000, 10**9)]
    labels = ["<500", "500–1k", "1k–2,5k", "2,5k–5k", "5k–10k", "10k–25k",
              "25k–50k", "50k–100k", ">100k"]
    counts = [sum(1 for r in laws if lo <= r["chars"] < hi) for lo, hi in bins]
    yield Measure(
        6, "A", "Fördelningen av lagars längd", "histogram", unit="författningar",
        lede="Formen är poängen: några få jättar och en mycket lång svans av "
             "tvåparagrafsförordningar.",
        xlabel="tecken", ylabel="författningar",
        points=_series(zip(labels, counts, strict=True)))

    titled = sorted((r for r in laws if r["clean_title"]),
                    key=lambda r: len(r["clean_title"]))
    yield Measure(
        7, "A", "Längsta och kortaste rubriken", "toplist", unit="tecken",
        lede="Beteckningen räknas inte med — ”Ellag (1997:857)” är en rubrik på "
             "fem tecken, inte sexton.",
        rows=([Row(r["clean_title"], len(r["clean_title"]), r["uri"],
                   group="Kortast")
               for r in titled[:6]]
              + [Row(r["clean_title"][:90] + "…", len(r["clean_title"]), r["uri"],
                     group="Längst")
                 for r in titled[-4:][::-1]]))

    named = [r for r in laws if r["alternate"]]
    yield Measure(
        8, "A", "Lagar med eget namn", "scalar", unit="författningar",
        value=len(named),
        display="%d av %d (%.1f %%)" % (len(named), len(laws),
                                        _pct(len(named), len(laws))),
        lede="Författningar som bär en etablerad förkortning (BrB, RB, ABL, IL) — "
             "kända nog att ha ett smeknamn.",
        rows=[Row(r["alternate"], r["chars"], r["uri"], r["clean_title"][:60])
              for r in sorted(named, key=lambda r: -r["chars"])[:10]])

    dep = collections.Counter(r["department"] for r in laws if r["department"])
    yield Measure(
        9, "A", "Författningar per departement", "bars", unit="författningar",
        lede="Vem som äger regelmassan.",
        xlabel="departement", ylabel="författningar",
        points=_series((d.replace("_", " ").capitalize(), n)
                       for d, n in dep.most_common(12)))

    total_chars = sum(r["chars"] for r in laws)
    words = total_chars // 6
    hours = words / 200 / 60
    yield Measure(
        10, "A", "Hela svensk författningssamling i siffror", "scalar",
        unit="tecken",
        value=total_chars,
        display="%s tecken · %s ord · %d dygn högläsning" % (
            "{:,}".format(total_chars).replace(",", " "),
            "{:,}".format(words).replace(",", " "), hours / 24),
        lede="All gällande författningstext tillsammans. Vid 200 ord i minuten "
             "tar det %d dygn att läsa upp den utan paus." % (hours / 24))


# ==========================================================================
# B. change and churn (11-21)
# ==========================================================================

def _group_b(con, s):
    laws = s["laws"]                    # gällande rätt (see `_in_force`)

    yield Measure(
        11, "B", "De mest ändrade lagarna", "toplist", unit="ändringar",
        lede="Antal ändringsförfattningar i registret.",
        rows=[Row(r["clean_title"], len(r["amendments"]), r["uri"])
              for r in sorted(laws, key=lambda r: -len(r["amendments"]))[:12]])

    # 12 -- the chain depth, read out of the download tree's change-act titles
    chains = [(bet, rubrik, depth)
              for rows in s["registers"] for bet, rubrik, depth in rows if depth >= 2]
    seen, deepest = set(), []
    for bet, rubrik, depth in sorted(chains, key=lambda t: -t[2]):
        if bet in seen:
            continue
        seen.add(bet)
        deepest.append((bet, rubrik, depth))
    yield Measure(
        12, "B", "”Lag om ändring i lagen om ändring i lagen om…”", "toplist",
        unit="led",
        lede="En ändringsförfattning kan ändra en ändringsförfattning som ändrar "
             "en annan. De längsta kedjorna i registret, räknat i led.",
        # the chains themselves, longest first -- the distribution behind them
        # said how many were 2 and 3 links deep, which is the uninteresting part:
        # the point of this measure is that the deep ones exist at all
        rows=[Row(r[:150], d, None, b) for b, r, d in deepest[:10]])

    touch = collections.defaultdict(set)
    for r in laws:
        for a in r["amendments"]:
            if a["id"]:
                touch[a["id"]].add(r["clean_title"])
    yield Measure(
        13, "B", "Ändringsförfattningen som rör flest lagar samtidigt", "toplist",
        unit="lagar",
        lede="En enda författning kan skriva om hela regelmassan på en gång.",
        rows=[Row(k, len(v), None, sorted(v)[0][:60] + " m.fl.")
              for k, v in sorted(touch.items(), key=lambda t: -len(t[1]))[:10]])

    never = [r for r in laws if len(r["amendments"]) <= 1]
    yield Measure(
        14, "B", "Lagar som aldrig har ändrats", "scalar", unit="författningar",
        value=len(never),
        display="%d av %d" % (len(never), len(laws)),
        lede="Aldrig en enda ändring sedan de utfärdades.",
        rows=[Row(r["clean_title"][:80], len(r["amendments"]), r["uri"], r["ikraft"])
              for r in sorted((x for x in never if x["ikraft"]),
                              key=lambda r: r["ikraft"])[:10]])

    byyear = collections.Counter()
    for r in laws:
        for a in r["amendments"]:
            if a["ikraft"]:
                byyear[a["ikraft"][:4]] += 1
    today = datetime.date.today().year
    yield Measure(
        15, "B", "Ändringar per år", "series", unit="ändringar",
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
        16, "B", "Snabbast ändrade lag", "toplist", unit="dygn",
        lede="Tiden från att lagen trädde i kraft till att den första ändringen "
             "gjorde det.",
        rows=[Row(r["clean_title"][:80], d, r["uri"], "%s → %s" % (r["ikraft"], first))
              for d, r, first in sorted(gaps, key=lambda t: t[0])[:10]])

    # 17 -- how old the text in force actually is
    ages = []
    for r in laws:
        dates = [a["ikraft"] for a in r["amendments"] if a["ikraft"]]
        if dates and r["paragrafer"]:
            ages.append((max(dates), r))
    yield Measure(
        17, "B", "Lagar med äldst kvarvarande text", "toplist", unit="år",
        lede="Gällande författningar vars senaste ändring ligger längst tillbaka "
             "— regelmassans orörda botten.",
        rows=[Row(r["clean_title"][:80], today - int(d[:4]), r["uri"], d)
              for d, r in sorted(ages, key=lambda t: t[0])[:10]])

    # 18 -- the same question asked per paragraf: how old is the text that is
    # actually in force? A law is not "from 1962" because its SFS number is; it
    # is a mosaic of paragrafer of very different ages, and the register says
    # which amendment last touched each one (`ersatter`/`inforsI`).
    weighted = [(age, int(r["ikraft"][:4]), r) for r in laws
                if (age := text_age(r)) is not None]
    yield Measure(
        18, "B", "Lagtextens medelålder", "toplist", unit="år",
        lede="Per paragraf: vilket år fick den sin nuvarande lydelse? Snittet "
             "över en lags paragrafer är textens verkliga ålder — %d "
             "författningar går att mäta så, och deras medeltext är från %d."
             % (len(weighted),
                statistics.mean([m for m, _, _ in weighted]) if weighted else 0),
        rows=([Row(r["clean_title"][:70], round(mean, 1), r["uri"],
                   "grundförfattning %d" % born, group="Äldst kvarvarande text")
               for mean, born, r in sorted(weighted, key=lambda t: t[0])[:6]]
              + [Row(r["clean_title"][:70], round(mean, 1), r["uri"],
                     "grundförfattning %d — %d års förnyelse" % (born, mean - born),
                     group="Mest renoverade")
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
    yield Measure(
        19, "B", "Den mest omskrivna enskilda paragrafen", "toplist",
        unit="omskrivningar",
        lede="Hur många gånger en och samma paragraf har fått ny lydelse.",
        rows=[Row(u.replace(BASE, ""), n, u) for u, n in changed.most_common(12)])

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
        20, "B", "Vad ändringar faktiskt gör", "bars", unit="ändringar",
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
        21, "B", "Tidsmaskinens djup", "toplist", unit="versioner",
        lede="Hur många historiska lydelser lagen.nu kan visa. Totalt %d "
             "versioner mot %d gällande lagar." % (len(s["versions"]), len(laws)),
        rows=[Row(by_uri[u]["clean_title"] if u in by_uri else u.replace(BASE, ""),
                  n, u)
              for u, n in vc.most_common(12)])


# ==========================================================================
# C. time, lifespan, mortality (22-30)
# ==========================================================================

def _group_c(con, s):
    today = datetime.date.today().isoformat()
    yield Measure(
        22, "C", "Äldsta lagar som fortfarande gäller", "toplist", unit="år",
        lede="Regelmassans äldsta lager, fortfarande i kraft.",
        rows=[Row(t, int(today[:4]) - int(d[:4]), u, d) for u, t, d in
              _q(con, "SELECT uri, title, date FROM documents WHERE source='sfs' "
                      "AND " + in_force() + " AND date > '1500' "
                      "ORDER BY date LIMIT 12")])

    yield Measure(
        23, "C", "Längst levande upphävda lagar", "toplist", unit="år",
        lede="Från utfärdande till upphävande.",
        rows=[Row(t, y, u, "%s → %s" % (d, e)) for u, t, d, e, y in
              _q(con, "SELECT uri, title, date, expired, "
                      "CAST((julianday(expired)-julianday(date))/365.25 AS INT) y "
                      "FROM documents WHERE source='sfs' AND " + repealed() + " "
                      "AND date > '1500' ORDER BY y DESC LIMIT 12")])

    yield Measure(
        24, "C", "Kortast levande lagar", "toplist", unit="dygn",
        lede="Författningar som knappt hann träda i kraft.",
        rows=[Row(t[:90], d, u, "%s → %s" % (a, e)) for u, t, a, e, d in
              _q(con, "SELECT uri, title, date, expired, "
                      "CAST(julianday(expired)-julianday(date) AS INT) d "
                      "FROM documents WHERE source='sfs' AND " + repealed() + " "
                      "AND d >= 0 ORDER BY d ASC LIMIT 12")])

    yield Measure(
        25, "C", "Upphävanden per år", "series", unit="upphävanden",
        lede="Hur mycket regelmassa som avvecklas varje år.",
        xlabel="år", ylabel="upphävda författningar",
        points=_series(_q(con, "SELECT substr(expired,1,4) y, count(*) "
                               "FROM documents WHERE source='sfs' "
                               "AND " + repealed() + " AND y BETWEEN '1900' AND ? "
                               "GROUP BY 1 ORDER BY 1", (today[:4],))))

    # 25 -- the survival curve: the single most informative series here
    issued = dict(_q(con, "SELECT substr(date,1,4), count(*) FROM documents "
                          "WHERE source='sfs' AND date BETWEEN '1900' AND ? "
                          "GROUP BY 1", (today,)))
    alive = dict(_q(con, "SELECT substr(date,1,4), count(*) FROM documents "
                         "WHERE source='sfs' AND " + in_force() + " "
                         "AND date BETWEEN '1900' AND ? GROUP BY 1", (today,)))
    yield Measure(
        26, "C", "Överlevnadskurva", "series", unit="procent",
        lede="Av alla författningar utfärdade år X — hur stor andel gäller "
             "fortfarande idag? Kurvan är svensk lagstiftnings halveringstid.",
        xlabel="utfärdandeår", ylabel="% kvar i kraft",
        points=_series(sorted((y, _pct(alive.get(y, 0), n))
                              for y, n in issued.items() if n >= 5)))

    yield Measure(
        27, "C", "Vilken dag träder svensk lag i kraft?", "bars", unit="ändringar",
        lede="Ikraftträdandedatumen samlade på månad. Svensk lagstiftning har "
             "två hjärtslag om året — 1 januari och 1 juli — och nästan "
             "ingenting däremellan.",
        xlabel="månad", ylabel="ikraftträdda ändringar",
        # the whole history: this describes when Sweden *has* brought changes
        # into force, and an amendment to an act repealed since was no less a
        # real ikraftträdande. Narrowing to gällande rätt would draw the curve
        # only from the acts that happened to survive (rule: `_in_force`)
        points=_series(_ikraft_months(s["laws_all"])))

    # 27 -- the notice period. Only grundförfattningar: the amendment register
    # dates the ikraftträdande but not the utfärdande (11 of 50 948 entries carry
    # one, and the download tree has none either), so the same curve drawn over
    # changes would describe registration practice rather than lawmaking.
    # whole history, for the same reason as 27: how much notice lawmaking has
    # given is a fact about the past, not about what survives today
    notice = [(d, r) for r in s["laws_all"] if (d := notice_days(r)) is not None]
    days = sorted(d for d, _ in notice)
    bins = [(0, 1), (1, 15), (15, 31), (31, 61), (61, 92), (92, 183),
            (183, 366), (366, 10**6)]
    labels = ["samma dag", "1–14 dygn", "15–30", "31–60", "61–91", "3–6 mån",
              "6–12 mån", ">1 år"]
    yield Measure(
        28, "C", "Hur långt varsel får en ny lag?", "histogram",
        unit="författningar",
        lede="Tiden från att författningen utfärdas till att den träder i kraft. "
             "Medianen är %d dygn; en fjärdedel får %d dygn eller mindre."
             % (statistics.median(days) if days else 0,
                days[len(days) // 4] if days else 0),
        xlabel="varsel", ylabel="författningar",
        points=_series(zip(labels, [sum(1 for d in days if lo <= d < hi)
                                    for lo, hi in bins], strict=True)))

    future = collections.Counter()
    for r in s["laws"]:
        for a in r["amendments"]:
            if a["ikraft"] and a["ikraft"] > today:
                future[a["ikraft"][:4]] += 1
    yield Measure(
        29, "C", "Framtiden som redan är skriven", "bars", unit="ändringar",
        lede="Ändringar som är beslutade men ännu inte i kraft — lagstiftning "
             "som redan finns men inte gäller.",
        xlabel="ikraftträdandeår", ylabel="beslutade ändringar",
        points=_series(sorted(future.items())))

    dec = collections.Counter()
    for (date,) in _q(con, "SELECT date FROM documents WHERE source='sfs' "
                           "AND " + in_force() + " AND date > '1800'"):
        dec[date[:3] + "0"] += 1
    yield Measure(
        30, "C", "Nya författningar per decennium", "bars", unit="författningar",
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


MONTHS = ("januari", "februari", "mars", "april", "maj", "juni", "juli",
          "augusti", "september", "oktober", "november", "december")


def _ikraft_months(laws):
    counts = collections.Counter()
    for r in laws:
        for a in r["amendments"]:
            if a["ikraft"]:
                counts[int(a["ikraft"][5:7])] += 1
    return [(MONTHS[i - 1], counts.get(i, 0)) for i in range(1, 13)]


# ==========================================================================
# D. the citation graph (31-38)
# ==========================================================================

def _group_d(con, s):
    docs = _q(con, "SELECT count(*) FROM documents")[0][0]
    links = _q(con, "SELECT count(*) FROM links")[0][0]
    paged = _q(con, "SELECT count(*) FROM links WHERE from_page IS NOT NULL")[0][0]
    yield Measure(
        31, "D", "Korpuset i siffror", "scalar", unit="hänvisningar",
        value=links,
        display="%s hänvisningar mellan %s dokument"
                % ("{:,}".format(links).replace(",", " "),
                   "{:,}".format(docs).replace(",", " ")),
        lede="Varje hänvisning är upplöst till ett dokument och, där källan har "
             "sidor, till sidan den står på (%s av dem)."
             % "{:,}".format(paged).replace(",", " "))

    yield Measure(
        32, "D", "Mest hänvisade dokument", "toplist", unit="hänvisningar",
        lede="Inkommande länkar från hela korpuset.",
        rows=[Row(t or u.replace(BASE, ""), c, u) for u, t, c in
              _q(con, "SELECT l.to_root, d.title, count(*) c FROM links l "
                      "LEFT JOIN documents d ON d.uri = l.to_root "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    yield Measure(
        33, "D", "Mest hänvisade enskilda paragraf", "toplist", unit="hänvisningar",
        lede="Ner på paragrafnivå — vilken enskild regel korpuset faktiskt talar om.",
        rows=[Row(_pinpoint(u, desc, src), c, u) for u, desc, src, c in
              _q(con, "SELECT l.to_uri, d.descriptive, d.source, count(*) c "
                      "FROM links l LEFT JOIN documents d ON d.uri = l.to_root "
                      "WHERE l.to_uri LIKE '%#%' "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    yield Measure(
        34, "D", "Dokument med flest utgående hänvisningar", "toplist",
        unit="hänvisningar",
        lede="Vilka dokument som citerar mest — utredningar, i praktiken.",
        rows=[Row(t or u.replace(BASE, ""), c, u) for u, t, c in
              _q(con, "SELECT l.from_uri, d.title, count(*) c FROM links l "
                      "JOIN documents d ON d.uri = l.from_uri "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    yield Measure(
        35, "D", "Vem citerar vem", "matrix", unit="hänvisningar",
        lede="Hänvisningsmatrisen mellan källor. Förarbetenas hänvisningar till "
             "SFS dominerar allt annat.",
        cells=[Cell(a, b, c) for a, b, c in
               _q(con, "SELECT d1.source, d2.source, count(*) FROM links l "
                       "JOIN documents d1 ON d1.uri = l.from_uri "
                       "JOIN documents d2 ON d2.uri = l.to_root "
                       "GROUP BY 1,2 HAVING count(*) > 500")])

    # 36 -- the shape of how attention is distributed, not a defect count. An
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
        36, "D", "Hur många hänvisar till en lag?", "histogram",
        unit="författningar",
        lede="Gällande författningar efter antal inkommande hänvisningar från "
             "hela korpuset — lag, dom, förarbete och föreskrift tillsammans. "
             "%d av %d har inga alls; de är öar snarare än övergivna, oftast "
             "förvaltningsförordningar som ingenting har anledning att citera."
             % (counts[0], sum(counts)),
        xlabel="inkommande hänvisningar", ylabel="författningar",
        points=_series(zip(labels, counts, strict=True)))

    yield Measure(
        37, "D", "Den mest självrefererande texten", "toplist", unit="hänvisningar",
        lede="Hänvisningar från ett dokument till sig självt.",
        rows=[Row(t or u.replace(BASE, ""), c, u) for u, t, c in
              _q(con, "SELECT l.from_uri, d.title, count(*) c FROM links l "
                      "LEFT JOIN documents d ON d.uri = l.from_uri "
                      "WHERE l.from_uri = l.to_root "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    yield Measure(
        38, "D", "Mest omtalade begrepp", "toplist", unit="hänvisningar",
        lede="Begreppen rankade på inkommande länkar — vad korpuset bråkar om.",
        rows=[Row(t, c, u) for u, t, c in
              _q(con, "SELECT d.uri, d.title, count(*) c FROM links l "
                      "JOIN documents d ON d.uri = l.to_root "
                      "WHERE d.source='begrepp' GROUP BY 1 ORDER BY c DESC LIMIT 12")])


# ==========================================================================
# E. preparatory works (39-45)
# ==========================================================================

def _group_e(con, s):
    kinds = dict(_q(con, "SELECT kind, count(*) FROM documents "
                         "WHERE source='forarbete' GROUP BY 1"))
    yield Measure(
        39, "E", "Förarbeten per typ", "bars", unit="dokument",
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
        40, "E", "Förarbeten per år", "series", unit="dokument",
        lede="Utgivningstakten, alla typer sammanslagna.",
        xlabel="år", ylabel="dokument",
        points=_series(sorted((y, sum(c.values())) for y, c in per_year.items())))

    sou = [f for f in s["forarbete"] if f["type"] == "sou"]
    yield Measure(
        41, "E", "Tjockaste utredningen", "toplist", unit="tecken",
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
    yield Measure(
        42, "E", "Propositionen som ändrade flest lagar", "toplist", unit="lagar",
        lede="En proposition kan skriva om hundratals lagar på en gång.",
        rows=[Row(k, len(fblaws[k]), None, "%d ändringar" % fb[k])
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
        43, "E", "Från förslag till lag", "toplist", unit="dygn",
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
        44, "E", "Volymen författningskommentar", "toplist", unit="kommentarer",
        lede="%s stycken kommentar till enskilda paragrafer, hämtade ur "
             "propositionerna." % "{:,}".format(fk).replace(",", " "),
        rows=[Row(t or u.replace(BASE, ""), c, u) for u, t, c in
              _q(con, "SELECT f.sfs_uri, d.title, count(*) c FROM fk_kommentar f "
                      "LEFT JOIN documents d ON d.uri = f.sfs_uri "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    corr = _q(con, "SELECT relation, scope, count(*) FROM correspondence "
                   "GROUP BY 1,2 ORDER BY 3 DESC")
    yield Measure(
        45, "E", "Paragrafmotsvarigheter", "bars", unit="motsvarigheter",
        lede="De spårade ”motsvarar / överförd från”-relationerna mellan gammal "
             "och ny lagtext — hur mycket av en ny lag som egentligen är gammal.",
        xlabel="relation", ylabel="motsvarigheter",
        points=_series(("%s %s" % (r, sc or ""), c) for r, sc, c in corr))


# ==========================================================================
# F. case law (46-50)
# ==========================================================================

def _group_f(con, s):
    courts = _q(con, "SELECT substr(label,1,3), count(*) c FROM documents "
                     "WHERE source='dv' GROUP BY 1 ORDER BY c DESC LIMIT 12")
    yield Measure(
        46, "F", "Avgöranden per domstol", "bars", unit="avgöranden",
        lede="Fördelningen över domstolar, avläst på referatbeteckningen.",
        xlabel="domstol", ylabel="avgöranden",
        points=_series(courts))

    today = datetime.date.today().isoformat()
    yield Measure(
        47, "F", "Avgöranden per år", "series", unit="avgöranden",
        lede="Publiceringstakten för refererad praxis.",
        xlabel="år", ylabel="avgöranden",
        points=_series(_q(con, "SELECT substr(date,1,4) y, count(*) FROM documents "
                               "WHERE source='dv' AND date BETWEEN '1980' AND ? "
                               "GROUP BY 1 ORDER BY 1", (today,))))

    yield Measure(
        48, "F", "Mest hänvisade rättsfall", "toplist", unit="hänvisningar",
        lede="Prejudikaten som faktiskt bär praxis.",
        rows=[Row(t, c, u) for u, t, c in
              _q(con, "SELECT d.uri, d.title, count(*) c FROM links l "
                      "JOIN documents d ON d.uri = l.to_root WHERE d.source='dv' "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    yield Measure(
        49, "F", "Vilka lagrum domstolarna citerar mest", "toplist",
        unit="hänvisningar",
        lede="Bara hänvisningar som går från en dom till en författning.",
        rows=[Row(_pinpoint(u, desc, src), c, u) for u, desc, src, c in
              _q(con, "SELECT l.to_uri, t.descriptive, t.source, count(*) c "
                      "FROM links l JOIN documents d ON d.uri = l.from_uri "
                      "LEFT JOIN documents t ON t.uri = l.to_root "
                      "WHERE d.source='dv' AND l.to_uri LIKE '%#%' "
                      "GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    dv = [d for d in s["dv"] if d["chars"] > 0]
    yield Measure(
        50, "F", "Längsta och kortaste avgörandet", "toplist", unit="tecken",
        lede="Median %d tecken. Somliga domar är böcker, somliga referat är tre "
             "meningar." % (statistics.median([d["chars"] for d in dv]) if dv else 0),
        rows=([Row(d["label"] or d["uri"].replace(BASE, ""), d["chars"], d["uri"],
                   d["court"], group="Längst")
               for d in sorted(dv, key=lambda d: -d["chars"])[:6]]
              + [Row(d["label"] or d["uri"].replace(BASE, ""), d["chars"], d["uri"],
                     d["court"], group="Kortast")
                 for d in sorted(dv, key=lambda d: d["chars"])[:6]]))


# ==========================================================================
# G. agency regulations, consultations and the world outside (51-54)
# ==========================================================================

def _group_g(con, s):
    pub = _q(con, "SELECT publisher, count(*) c FROM documents "
                  "WHERE source='foreskrift' AND publisher IS NOT NULL "
                  "GROUP BY 1 ORDER BY c DESC LIMIT 12")
    total = _q(con, "SELECT count(*), count(DISTINCT kind) FROM documents "
                    "WHERE source='foreskrift'")[0]
    yield Measure(
        51, "G", "Föreskrifter per myndighet", "bars", unit="föreskrifter",
        lede="%d föreskrifter ur %d författningssamlingar." % total,
        xlabel="myndighet", ylabel="föreskrifter",
        points=_series(pub))

    yield Measure(
        52, "G", "Direktiven som satt djupast spår i svensk rätt", "toplist",
        unit="paragrafer",
        lede="Antal svenska paragrafer som genomför direktivets artiklar.",
        rows=[Row(d.replace(BASE + "ext/celex/", ""), c, d) for d, c in
              _q(con, "SELECT directive, count(DISTINCT sfs_uri||sfs_anchor) c "
                      "FROM genomforande GROUP BY 1 ORDER BY c DESC LIMIT 12")])

    yield Measure(
        53, "G", "Europakonventionen i svensk rätt", "toplist", unit="hänvisningar",
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
        54, "G", "Remissvaren", "toplist", unit="remissvar",
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
    in 1999. Narrowing once here rather than at 54 call sites means a measure
    that genuinely needs the whole history -- churn, lifespan, "how many have
    been repealed" -- has to reach for `laws_all` by name, so counting repealed
    acts is always a visible decision in the measure that does it, never an
    oversight in the measure that forgot to filter."""
    live = {u for (u,) in _q(con, LIVE_SQL)}
    return {**scans, "laws_all": scans["laws"],
            "laws": [r for r in scans["laws"] if r["uri"] in live]}


def compute(catalog_path, jobs=None, progress=None):
    """Every measurement, as a `Report`."""
    scans = run_scans(jobs=jobs, progress=progress)
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
