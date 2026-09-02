"""The avg source's registration: vägledande myndighetsavgöranden from JO, JK,
ARN, IMY and KKV.

Five organs, five agency sites, one harvest that fans them out. Decisions
arrive only through the bulk harvest -- there is no per-document download
stage, so parse runs over whatever is on disk (the föreskrift rule).
"""

import functools
from pathlib import Path

from ..lib import compress, layout, util
from ..lib import stage as protocol
from ..lib.datasets import JO_ARSBERATTELSE
from ..lib.errors import SkipDocument
from ..lib.pdftext import pdf_intermediate
from ..lib.stage import (
    CASENUMBER_CODE,
    CITATION_DATA,
    Source,
    parse_stage,
    patch_input,
    scoped_harvest,
)
from ..lib.util import document_extension
from . import arsberattelse, download, model, parse, render

HERE = Path(__file__).parent

AVG_CODE = (HERE / "parse.py", HERE / "model.py", HERE / "download.py",
            HERE.parent / "lib" / "pdftext.py",
            HERE.parent / "lib" / "lagrum.py",
            HERE.parent / "lib" / "emdref.py", *CITATION_DATA, *CASENUMBER_CODE,
            HERE.parent / "lib" / "artifact.py")


def avg_arsberattelse(args=()):
    """Refresh the JO ämbetsberättelse snapshot (`lagen avg arsberattelse`):
    sweep the JO artifacts' officialReport pages and rewrite
    avg/data/arsberattelse.json, which the citation engine reads to resolve
    "JO 2003/04 s. 450" onto the decision's dnr URI. Reads artifacts already
    on disk -- no network, no per-document chain."""
    if protocol.RUN.dry_run:
        print("avg arsberattelse: would sweep JO artifacts -> %s"
              % JO_ARSBERATTELSE)
        return
    pages, unparseable = arsberattelse.harvest()
    ambiguous = sum(1 for v in pages.values() if len(v) > 1)
    print("avg arsberattelse: %d pages (%d ambiguous, left unlinked) -> %s"
          % (len(pages), ambiguous, JO_ARSBERATTELSE))
    for report in unparseable:
        print("  !! unparseable officialReport: %r" % report)


def avg_list():
    return sorted(bf for org in model.ORGS
                  for bf in compress.list_basefiles(layout.AVG_DOWNLOADED, org))


def avg_record(basefile):
    return util.record_path(layout.AVG_DOWNLOADED, basefile.split("/", 1)[0],
                            basefile)


def avg_inputs(basefile):
    """The record JSON plus the decision body file (JO/ARN: the PDF; JK: the
    landing page) -- re-downloading/re-importing either re-stales the parse."""
    paths = [avg_record(basefile)]
    if basefile.startswith("jo/"):
        pdf = download.jo_pdf_path(layout.AVG_DOWNLOADED, basefile)
        if pdf.exists():
            paths.append(pdf)
        # the frozen corpus's ämbetsberättelse map: a rewritten map
        # re-stales every JO parse that could graft from it
        report = download.jo_officialreport_path(layout.AVG_DOWNLOADED)
        if report.exists():
            paths.append(report)
    elif basefile.startswith("arn/"):
        paths.append(download.arn_pdf_path(layout.AVG_DOWNLOADED, basefile))
    elif basefile.startswith("imy/"):
        # an IMY decision is assembled from the documents its record names --
        # shared assets, so several decisions can depend on the same PDF
        paths.extend(download.imy_pdf_path(layout.AVG_DOWNLOADED, part["fil"])
                     for part in compress.read_json(
                         avg_record(basefile))["delar"])
    elif basefile.startswith("kkv/"):
        # a KKV case publishes at most one decision document (a few publish
        # none) and, for the long ones, a separate sammanfattning beside it
        record = compress.read_json(avg_record(basefile))
        paths.extend(download.kkv_body_path(layout.AVG_DOWNLOADED,
                                                record[key]["fil"])
                     for key in ("dokument", "sammanfattning_dokument")
                     if key in record)
    else:
        paths.append(download.jk_html_path(layout.AVG_DOWNLOADED, basefile))
    return paths + patch_input("avg", basefile)


def avg_harvest(scopes):
    """Bulk harvest of the JO/JK/ARN/IMY/KKV decisions (scopes = organ codes;
    empty = all five). `--force` re-walks the whole corpus (JO) / refetches
    landings (JK) / refetches every document (ARN, IMY, KKV); `--only
    jo/2340-2025` fetches a single decision (needs its organ scope)."""
    # five organs, five agency sites: fan them out (lib.harvest.fan_out)
    return scoped_harvest("avg", download, layout.AVG_DOWNLOADED, scopes,
                          noun="organ",
                          example="lagen avg download jo --only jo/2340-2025",
                          label="jo + jk + arn + imy + kkv",
                          jobs=protocol.RUN.jobs)


# No per-document download stage (the foreskrift rule): decisions arrive only
# through the bulk `avg_harvest` sweep, so parse runs over whatever is on
# disk; relate/index/dump/generate act on the artifacts by source name.
def avg_intermediate(basefile):
    """The intermediate an avg decision's parse actually reads, dispatched on
    the org exactly as the parser does: pdftohtml XML for the PDF-bodied organs,
    a JK decision's landing-page HTML, and for KKV whichever of the two its
    document happens to be (the diarium published a third of the corpus as
    windows-1252 HTML).

    An IMY decision assembled from several documents has no single intermediate
    -- parse threads one patch through every part, so a patch authored against
    one part would be attempted against the next and fail the document. Those
    are refused here rather than offered a patch that cannot hold."""
    org = basefile.split("/", 1)[0]
    record = compress.read_json(avg_record(basefile))
    if org == "jk":
        return compress.read_text(
            download.jk_html_path(layout.AVG_DOWNLOADED, basefile))
    if org == "jo":
        dnrs = download.jo_dnrs(record.get("diary_number"))
        if not dnrs:
            raise SkipDocument("%s: jo record carries no diarienummer" % basefile)
        return pdf_intermediate(
            download.jo_pdf_path(layout.AVG_DOWNLOADED, "jo/" + dnrs[0]))
    if org == "imy":
        parts = [d for d in record["delar"] if d["sprak"] == "sv"]
        if len(parts) != 1:
            raise SkipDocument(
                "%s: assembled from %d documents, which one patch cannot span"
                % (basefile, len(parts)))
        return pdf_intermediate(
            download.imy_pdf_path(layout.AVG_DOWNLOADED, parts[0]["fil"]))
    if org == "kkv":
        if "dokument" not in record:
            raise SkipDocument("%s: the diarium published no document for it"
                               % basefile)
        path = download.kkv_body_path(layout.AVG_DOWNLOADED,
                                      record["dokument"]["fil"])
        data = compress.read_bytes(path)
        if document_extension(data) == ".pdf":
            return pdf_intermediate(path)
        if document_extension(data) in (".doc", ".docx"):
            # read through POI, which has no editable text intermediate the way
            # pdftohtml XML and HTML do (the two Word cases in the corpus)
            raise SkipDocument("%s: a Word document has no patchable "
                               "intermediate" % basefile)
        return parse.kkv_html_text(data)
    return pdf_intermediate(download.arn_pdf_path(
        layout.AVG_DOWNLOADED, "arn/" + record["diarienummer"]))


SOURCES: tuple[Source, ...] = (Source("avg", avg_list, {
    "parse": parse_stage("avg", parse.parse, layout.AVG_DOWNLOADED,
                          inputs=avg_inputs, code=AVG_CODE),
},
    render=render.render,
    intermediate=(avg_intermediate,
                  "pdftohtml XML (jk, and kkv's pre-2006 documents: HTML)"),
    artifacts=functools.partial(layout.artifacts, "avg"),
    actions={"arsberattelse": avg_arsberattelse},
    harvest=avg_harvest,
    origin="https://www.jo.se/",
    scopes=frozenset(model.ORGS),
    notes="download flag: --only org/dnr (fetch one; needs its organ scope)\n"
          "scopes are the organs: jo (Riksdagens ombudsmän), jk "
          "(Justitiekanslern), arn (Allmänna reklamationsnämnden), imy "
          "(Integritetsskyddsmyndigheten), kkv (Konkurrensverket); empty = all\n"
          "the arn scope downloads the live vägledande-beslut listing (2017-); it "
          "overwrites any frozen import of the same dnr (live wins)\n"
          "the imy scope reads each decision's diarienummer out of its PDF (the "
          "tillsyn pages never state it), so --only needs the decision already "
          "harvested\n"
          "the kkv scope joins two sources on the diarienummer: the diarium's "
          "published decisions in closed cases, narrowed to Konkurrensverkets "
          "own supervisory ärendetyp groups (1,830 since 1998 -- the status "
          "filter alone is 10k, a third of it remissyttranden), plus the "
          "curated ärendelista's account of the 329 cases (413 dnr) it "
          "covers, 346 of which the narrowed set does not carry -- 2,176 in all"),)
