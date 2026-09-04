"""Parse archived SFS consolidations into per-version artifacts.

A statute's downloaded file is replaced whenever rkrattsbaser folds a new
amendment in; the superseded consolidation is preserved under
``archive/downloaded/{y}/{n}/.versions/`` (see sfs.download). Two decades of
that archive exist in three raw forms: the latin-1 SFST pages of the original
rättsdatabaser, the utf-8 pages of its successor, and the new beta-API JSON.
This module gives every archived consolidation the same treatment the current
one gets from the parse stage -- extract → assemble → normal form. The versions
stage (`sfs.source`: one dispatch key per archived consolidation, then a
per-source hook that assembles each statute's index) writes

  archive/artifact/{y}/{n}/.versions/{vy}/{vn}.json   one artifact per version
  artifact/{y}/{n}.versions.json                      the per-statute index

which the renderer (historical "lydelse" pages, the version panel), the API
(/document/versions, /document/diff) and the diff view consume. A version's
id is the SFS number of the last amendment folded in ("2003:466"), the same
identity the downloader uses. Legacy counter-keyed archive files ("11.html",
archived before the old downloader learned to read the cutoff) carry the real
cutoff in their header, so their id is recovered at parse time; a recovered id
that duplicates an explicitly-keyed version is the same consolidation fetched
twice and is skipped.
"""

import html
import re

from ..lib import compress, util
from . import parse_sfs, parse_sfs_source
from . import register as register_mod
from .extract import sniff_encoding
from .nf import BASE, to_normalform

# the consolidation cutoff in an SFST header: "t.o.m. SFS 2003:466" (the year
# is occasionally dropped in the source; a yearless cutoff is unusable here
# since there is no register to resolve it against).
#
# Two of the source's own typing slips are read rather than refused, because
# refusing them leaves the act with no cutoff at all and its whole version
# history unkeyed: the abbreviation written without its last dot ("t.o.m SFS
# 2018:1409", 1962:627) and the SFS number written with a dot for its colon
# ("t.o.m. SFS 2026.1467", 2007:1175). Measured over every header in the
# corpus: 49 gain a cutoff, none read differently.
RE_CUTOFF = re.compile(r"t\.o\.m\.?\s*SFS\s*(\d+)\s*[:.]\s?(\d+)")

# a header line in the latin-1 archival <pre> block: "  Rubrik: value"
RE_HEADER_LINE = re.compile(r"^\s*([^:]+):(.*)$")


def konsolidering_uri(basefile, version):
    return "%s/konsolidering/%s" % (
        register_mod.amendment_uri(basefile, BASE),
        ":".join(register_mod.sfs_slug(version)) if ":" in version else version)


def archival_header(path):
    """Header key→value pairs from a latin-1 SFST archival page. The header
    is plain text above the <hr> inside the <pre>: "key:<b> value</b>" lines,
    keys sometimes wrapped over two lines ("Departement/\\nmyndighet:")."""
    text = compress.read_bytes(path).decode("latin-1")
    start = text.index("<pre>")
    end = text.index("<hr>", start)
    block = html.unescape(re.sub(r"<[^>]+>", "", text[start:end]))
    header = {}
    key = None
    for line in block.split("\n"):
        if line.rstrip().endswith("/") and ":" not in line:
            # a wrapped key ("Departement/" + "myndighet:"): the colon line
            # below carries the operative key part and the value
            key = None
            continue
        m = RE_HEADER_LINE.match(line)
        if m:
            key = m.group(1).strip().split("/")[-1]
            header[key] = m.group(2).strip()
        elif key and line.strip():
            header[key] += " " + line.strip()
    return header


def header_cutoff(header):
    """The consolidation cutoff SFS number named in an SFST header, or None
    when the header carries none (an un-amended act) or only a yearless one."""
    m = RE_CUTOFF.search(header.get("Ändring införd", ""))
    return "%s:%s" % (m.group(1), m.group(2)) if m else None


def version_metadata(basefile, version, header):
    """Document metadata for an HTML-archived consolidation, which has no
    register to drive register.build_metadata: the konsolidering identity plus
    the descriptive header fields the page front-matter shows."""
    props = {"dcterms:identifier": "SFS %s i lydelse enligt SFS %s"
                                   % (basefile, version)}
    if header.get("Rubrik"):
        props["dcterms:title"] = util.normalize_space(header["Rubrik"])
    for src, dst in (("Utfärdad", "rpubl:utfardandedatum"),
                     ("Ikraft", "rpubl:ikrafttradandedatum"),
                     ("Upphävd", "rpubl:upphavandedatum")):
        if header.get(src):
            props[dst] = header[src][:10]
    return {"uri": konsolidering_uri(basefile, version),
            "properties": props, "secondary": {}}


def parse_version(basefile, version, path, refparser=None):
    """Parse one archived consolidation into its version artifact. Returns
    ``(recovered_version, artifact)`` -- `recovered_version` is the cutoff the
    file itself names, which for legacy counter-keyed archives ("11") replaces
    the meaningless counter."""
    is_html = path.suffix != ".json"
    if is_html:
        header = (archival_header(path)
                  if sniff_encoding(compress.read_bytes(path)) == "latin-1"
                  else register_mod.parse_sfst_header(path))
        art = to_normalform(parse_sfs(path, basefile, historical=True), basefile,
                            refparser=refparser)
    else:
        source = compress.read_json(path)
        header = register_mod.sfst_header_from_source(source)
        art = to_normalform(parse_sfs_source(source, basefile, historical=True),
                            basefile, refparser=refparser,
                            register=register_mod.register_from_source(source),
                            sfst_header=header)
    recovered = header_cutoff(header) or version
    if is_html:
        # no register to drive build_metadata on the HTML path -- construct
        # the konsolidering identity from the header instead
        art["metadata"] = version_metadata(basefile, recovered, header)
    art["uri"] = konsolidering_uri(basefile, recovered)
    art["version"] = recovered
    return recovered, art
