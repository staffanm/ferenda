"""Unpack a CELLAR bulk "legislation" data dump into the per-CELEX layout the
incremental harvester produces, so the whole corpus can be imported from the
official dumps instead of fetched document-by-document.

A dump is a set of big zips for one release date:

  LEG_MTD_<date>.zip       {work-uuid}/tree_non_inferred.rdf      metadata
  LEG_EN_FMX_<date>.zip    {work-uuid}/fmx4/*.fmx.xml             Formex, English
  LEG_SV_FMX_<date>.zip    {work-uuid}/fmx4/*.fmx.xml             Formex, Swedish
  LEG_EN_HTML_<date>.zip   {work-uuid}/{html,xhtml}/*             HTML, English
  LEG_SV_HTML_<date>.zip   ...
  LEG_EN_PDF_<date>.zip    {work-uuid}/{pdf,pdfa1a,pdfa2a,...}/*.pdf  PDF, English
  LEG_SV_PDF_<date>.zip    ...

A work's manifestations are split across these per-format dumps, and not every
work is in every format (an older act is often HTML- or PDF-only -- e.g. directive
2000/53/EC has no Formex). So, per work and language, we keep the single best
available -- fmx4 > html > pdf, the same preference the live SPARQL downloader
applies -- and write the files a download leaves. Everything is keyed by the
opaque cellar work UUID; the CELEX (our basefile) lives only in the metadata rdf
(`resource_legal_id_celex`), which we read to map each UUID to its CELEX:

  {root}/{year}/{celex}/notice.ttl          the metadata, as turtle
  {root}/{year}/{celex}/{lang}.fmx4[.zip]   Formex (bare act, or annex bundle), or
  {root}/{year}/{celex}/{lang}.{html,xhtml} the HTML manifestation, or
  {root}/{year}/{celex}/{lang}.pdf          the PDF (last resort)

so `lagen eurlex parse` then treats them exactly like downloaded documents.
"""

import io
import os
import re
import sys
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..lib import compress
from ..lib.util import status
from .download import (
    SECTORS,
    content_filename,
    doc_dir,
    parse_notice,
    write_watermark,
)
from .model import doctype

LANG = {"EN": "eng", "SV": "swe"}    # dump language code -> our 3-letter code

# the dump's INCL.ELEMENT graphics (model certificates, figures) are TIFFs,
# overwhelmingly *uncompressed* -- recompressing them losslessly to WebP shrinks
# them to a few percent of their size. WebP can't hold these modes, so convert.
_WEBP_MODES = {"1", "L", "LA", "P", "RGB", "RGBA"}
_WEBP_METHOD = 4                     # 4 matches 6's size at lower cost (measured)
_INCL_ELEMENT = re.compile(rb"<INCL\.ELEMENT\b[^>]*>")
# encode the graphics in batches: the uncompressed TIFFs are huge (a single
# certificate-heavy work holds ~10 GB of them), so we never hold more than a
# batch of raw images in memory at once -- ~2x the pool keeps it well fed
_ENCODE_BATCH = 16
RE_CELEX = re.compile(rb"resource_legal_id_celex[^>]*>([^<]+)<")
RE_WORKDATE = re.compile(rb"work_date_document[^>]*>([^<]+)<")
_SECTOR_BY_DIGIT = {s.digit: name for name, s in SECTORS.items()}


def _find_zip(bundle, pattern):
    matches = sorted(bundle.glob(pattern))
    return matches[0] if matches else None


def _uuid_index(zf):
    """work-uuid -> [member names] for one language dump (one namelist pass)."""
    index = defaultdict(list)
    for name in zf.namelist():
        if not name.endswith("/"):
            index[name.split("/", 1)[0]].append(name)
    return index


def _celex_of(rdf_bytes):
    match = RE_CELEX.search(rdf_bytes)
    return match.group(1).decode().strip() if match else None


def _workdate_of(rdf_bytes):
    match = RE_WORKDATE.search(rdf_bytes)
    return match.group(1).decode().strip() if match else None


def _wanted(celex):
    """Whether a dump work belongs in the corpus. The "legislation" dumps also
    carry sector-3 acts we don't publish: of the legal acts (sector 3) we keep
    only regulations (R) and directives (L), dropping decisions (D) and the
    minor act types. Other sectors (treaties, case law) pass through unchanged."""
    return celex[0] != "3" or doctype(celex) in ("regulation", "directive")


def _recompress(raw):
    """A TIFF graphic re-encoded as lossless WebP. Returns (bytes, extension);
    keeps the original bytes (`.tif`) for the rare source image PIL cannot
    decode, so a corrupt graphic neither aborts the import nor is lost."""
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except (UnidentifiedImageError, OSError):
        return raw, ".tif"
    if im.mode not in _WEBP_MODES:
        im = im.convert("RGB")
    out = io.BytesIO()
    im.save(out, "WEBP", lossless=True, method=_WEBP_METHOD)
    return out.getvalue(), ".webp"


def _rewrite_filerefs(xml, renamed):
    """Point each INCL.ELEMENT FILEREF at the recompressed graphic's new name
    (and its TYPE at WEBP). `renamed` maps old basename -> new basename."""
    if not renamed:
        return xml

    def repl(m):
        el = m.group(0)
        ref = re.search(rb'FILEREF="([^"]+)"', el)
        new = ref and renamed.get(ref.group(1).rsplit(b"/", 1)[-1].decode())
        if new:
            el = el.replace(ref.group(0), b'FILEREF="%s"' % new.encode())
            el = el.replace(b'TYPE="TIFF"', b'TYPE="WEBP"')
        return el

    return _INCL_ELEMENT.sub(repl, xml)


def _bundle_fmx(zf, members, encode=map):
    """A work's Formex content as one blob. A lone act with no annexes and no
    graphics is returned bare (the raw `.xml`); anything with several parts or
    `.tif` graphics is packed into a zip -- the `.xml` content plus those
    graphics (model certificates, figures), dropping the .doc(.fmx).xml wrapper
    (a manifest, not content). Graphics are recompressed to WebP and FILEREFs
    repointed to match. None if the work has no Formex content. The caller names
    the blob by sniffing it (bare -> `.fmx4`, zip -> `.fmx4.zip`).

    `encode` maps _recompress over the graphics -- pass a thread pool's `.map`
    to parallelise (WebP encoding frees the GIL); the zip itself is read
    serially since ZipFile is not thread-safe."""
    xml_parts = sorted(n for n in members if n.endswith(".xml")
                       and not n.endswith((".doc.xml", ".doc.fmx.xml")))
    if not xml_parts:
        return None
    tif_parts = sorted(n for n in members if n.endswith(".tif"))
    if len(xml_parts) == 1 and not tif_parts:
        return zf.read(xml_parts[0])
    images = {}                      # new basename -> bytes
    renamed = {}                     # old basename -> new basename
    for i in range(0, len(tif_parts), _ENCODE_BATCH):
        batch = tif_parts[i:i + _ENCODE_BATCH]
        for name, (data, ext) in zip(batch,
                                     encode(_recompress, [zf.read(n) for n in batch]),
                                     strict=True):
            base = name.rsplit("/", 1)[-1]
            newbase = base[:-4] + ext
            images[newbase] = data
            if newbase != base:
                renamed[base] = newbase
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for name in xml_parts:
            out.writestr(name.rsplit("/", 1)[-1],
                         _rewrite_filerefs(zf.read(name), renamed))
        for newbase, data in images.items():
            out.writestr(newbase, data)
    return buf.getvalue()


def _pick_html(members):
    """The main HTML manifestation member (prefer xhtml; the main file has the
    shortest name -- numbered sub-parts and note .jpg/.xml sit beside it)."""
    cands = [n for n in members if n.endswith((".html", ".xhtml"))]
    pool = [n for n in cands if "/xhtml/" in n] or cands
    return min(pool, key=len) if pool else None


def _pick_pdf(members):
    """The main PDF member. A work carries a single PDF rendition, but in a
    type-named subdir (pdf, pdfa1a, pdfa1b, pdfa2a, ...) that varies per document
    -- we take any `.pdf` regardless. The main file is the shortest name; a
    multi-part act splits into OJ page-range files of equal-length names, so a
    tie breaks on the name itself -- the lowest page range, i.e. the opening
    part, first."""
    cands = [n for n in members if n.endswith(".pdf")]
    return min(cands, key=lambda n: (len(n), n)) if cands else None


def _select_content(lang, uuid, fmx, html, pdf, pool):
    """The best manifestation for one work in one language as (filename, bytes),
    or None if the work has no content there. Mirrors the live downloader's
    preference -- fmx4 > html > pdf -- and degrades the same way: an fmx4 entry
    that yields no usable Formex (a wrapper-only work) falls through to html,
    then pdf. Each of `fmx`/`html`/`pdf` is a (zips-by-lang, index-by-lang) pair;
    a format whose dump was not supplied is simply absent from the chain."""
    zips, idx = fmx
    if lang in zips and uuid in idx[lang]:
        blob = _bundle_fmx(zips[lang], idx[lang][uuid], encode=pool.map)
        if blob:
            return content_filename(lang, "fmx4", blob), blob
    zips, idx = html
    if lang in zips and uuid in idx[lang]:
        member = _pick_html(idx[lang][uuid])
        if member:
            ext = ".xhtml" if member.endswith(".xhtml") else ".html"
            return lang + ext, zips[lang].read(member)
    zips, idx = pdf
    if lang in zips and uuid in idx[lang]:
        member = _pick_pdf(idx[lang][uuid])
        if member:
            return lang + ".pdf", zips[lang].read(member)
    return None


def unpack_bulk(source, root, languages=("swe", "eng"), limit=None, log=print):
    """Import a bulk dump (a directory, or any file inside it) into root.
    Returns the number of works written."""
    bundle = Path(source)
    bundle = bundle if bundle.is_dir() else bundle.parent
    root = Path(root)

    mtd_zip = _find_zip(bundle, "LEG_MTD_*.zip")
    if mtd_zip is None:
        raise SystemExit("no LEG_MTD_*.zip found in %s" % bundle)
    mtd = zipfile.ZipFile(mtd_zip)
    fmx = {our: zipfile.ZipFile(z) for code, our in LANG.items()
           if (z := _find_zip(bundle, "LEG_%s_FMX_*.zip" % code))}
    html = {our: zipfile.ZipFile(z) for code, our in LANG.items()
            if (z := _find_zip(bundle, "LEG_%s_HTML_*.zip" % code))}
    pdf = {our: zipfile.ZipFile(z) for code, our in LANG.items()
           if (z := _find_zip(bundle, "LEG_%s_PDF_*.zip" % code))}
    log("dump: metadata + fmx%s + html%s + pdf%s; indexing ..."
        % (sorted(fmx), sorted(html), sorted(pdf)))
    fmx_idx = {l: _uuid_index(z) for l, z in fmx.items()}
    html_idx = {l: _uuid_index(z) for l, z in html.items()}
    pdf_idx = {l: _uuid_index(z) for l, z in pdf.items()}

    works = [n for n in mtd.namelist() if n.endswith("tree_non_inferred.rdf")]
    total = len(works)
    log("indexed; %d works to unpack" % total)

    written = skipped = filtered = empty = 0
    marks = {}                       # sector digit -> latest work date in the dump
    # recompressing the TIFF graphics to WebP is the bottleneck; fan it out
    # across cores (threads suffice -- libwebp releases the GIL during encode)
    with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 4))) as pool:
        for i, name in enumerate(works, 1):
            uuid = name.split("/", 1)[0]
            rdf = mtd.read(name)
            celex = _celex_of(rdf)
            if not celex or len(celex) < 5:
                skipped += 1
            elif not _wanted(celex):
                # excluded acts must not advance the sector watermark either, so
                # filter before the mark update below
                filtered += 1
            else:
                contents = []
                for lang in languages:
                    chosen = _select_content(lang, uuid, (fmx, fmx_idx),
                                             (html, html_idx), (pdf, pdf_idx), pool)
                    if chosen:
                        contents.append((lang, chosen))
                if not contents:
                    # a metadata-only work: no Swedish/English manifestation
                    # exists (a pre-accession act never translated). Don't create
                    # an entry -- a notice with no document is dead weight the
                    # parser can only skip, and (keyed on by is_downloaded) it
                    # would mask the work from a later run that does find one.
                    empty += 1
                else:
                    wdate = _workdate_of(rdf)
                    if wdate and wdate > marks.get(celex[0], ""):
                        marks[celex[0]] = wdate
                    dest = doc_dir(root, celex)
                    compress.write_download(dest / "notice.ttl", parse_notice(rdf).ttl())
                    for lang, (fname, data) in contents:
                        compress.write_download(dest / fname, data)
                        # one manifestation per language (the best tier): clear any
                        # other-format/zip-ness content a prior run left for this lang
                        for old in compress.glob(dest, lang + ".*"):
                            if old.name != fname:
                                compress.unlink(old)
                    written += 1
            status(i, total, "%d unpacked, %d skipped, %d filtered, %d empty  %s"
                   % (written, skipped, filtered, empty, celex or "-"),
                   actual=written)
            if limit and written >= limit:
                break
    if total:
        sys.stderr.write("\n")
    # the dump is the baseline: set each sector's watermark to its latest work
    # date, so a later `download` is incremental from here rather than re-walking
    # the whole sector (and its PDF-only long tail) from the first year.
    if not limit:
        for digit, date in sorted(marks.items()):
            sector = _SECTOR_BY_DIGIT.get(digit)
            if sector:
                write_watermark(root, sector, date)
                log("watermark[%s] = %s (dump baseline)" % (sector, date))
    log("unpacked %d works into %s (%d skipped: no CELEX, %d filtered: sector-3 "
        "non-regulation/directive, %d empty: no swe/eng manifestation)"
        % (written, root, skipped, filtered, empty))
    return written
