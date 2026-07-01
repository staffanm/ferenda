"""POI-backed extraction of legacy court-decision Word files (.doc/.docx).

The old DV feed stores each decision as a Word document. The binary
Word 97-2003 format (.doc) and the OOXML format (.docx) are read through
Apache POI (HWPF / XWPF) over jpype into a *single* flat paragraph stream
-- (text, bold, in_table) -- recovering the label/value table structure
and the bold run markers that antiword's DocBook conversion flattened.

The whole referat sits inside one Word table, so table membership is not
the body discriminator; the 'REFERAT' marker and bold metadata labels are.
Downstream parsing lives in dv_legacy.py.
"""

import glob
from dataclasses import dataclass
from pathlib import Path

import jpype
import jpype.imports

_JARS = sorted(glob.glob(str(Path(__file__).parent.parent / "vendor" / "poi" / "*.jar")))

# Word's field-result placeholder, emitted for empty form fields (Avdelning,
# Domsnummer …). It carries no value, so we strip it to empty.
_FORMTEXT = "FORMTEXT"

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = b"PK\x03\x04"


@dataclass
class Para:
    text: str
    bold: bool
    in_table: bool


def _ensure_jvm():
    if not jpype.isJVMStarted():
        assert _JARS, "no POI jars found under vendor/poi/"
        # We ship only log4j-api (no -core); point it at the built-in
        # SimpleLogger so it stops printing "could not find a logging
        # provider" to stdout, and silence that logger.
        jpype.startJVM(
            "-Dlog4j2.loggerContextFactory="
            "org.apache.logging.log4j.simple.SimpleLoggerContextFactory",
            "-Dorg.apache.logging.log4j.simplelog.StatusLogger.level=OFF",
            classpath=_JARS,
        )


def _clean(text):
    # \x07 is the HWPF cell/row terminator bell; drop it, the form-field
    # placeholder, and CRs, then collapse remaining whitespace.
    text = text.replace("\x07", "").replace("\r", "").replace(_FORMTEXT, "")
    return " ".join(text.split())


def _read_hwpf(path):
    # POI/jpype classes resolve only after startJVM, so unlike every other
    # import in this package these must stay in-function (see _ensure_jvm).
    from java.io import FileInputStream  # ty: ignore[unresolved-import]
    from org.apache.poi.hwpf import HWPFDocument  # ty: ignore[unresolved-import]

    doc = HWPFDocument(FileInputStream(str(path)))
    try:
        rng = doc.getRange()
        out = []
        for i in range(rng.numParagraphs()):
            p = rng.getParagraph(i)
            bold = any(p.getCharacterRun(j).isBold()
                       for j in range(p.numCharacterRuns()))
            out.append(Para(_clean(str(p.text())), bold, bool(p.isInTable())))
        return out
    finally:
        doc.close()   # also closes the FileInputStream; skipping it on a
        # malformed doc would leak a JVM-side file handle per failure


def _read_xwpf(path):
    # POI/jpype classes resolve only after startJVM (see _read_hwpf).
    from java.io import FileInputStream  # ty: ignore[unresolved-import]
    from org.apache.poi.xwpf.usermodel import (
        XWPFDocument,  # ty: ignore[unresolved-import]
    )

    doc = XWPFDocument(FileInputStream(str(path)))
    try:
        out = []

        def emit(p, in_table):
            bold = any(r.isBold() for r in p.getRuns())
            out.append(Para(_clean(str(p.getText())), bold, in_table))

        for el in doc.getBodyElements():
            kind = str(el.getClass().getSimpleName())
            if kind == "XWPFParagraph":
                emit(el, False)
            elif kind == "XWPFTable":
                for row in el.getRows():
                    for cell in row.getTableCells():
                        for p in cell.getParagraphs():
                            emit(p, True)
        return out
    finally:
        doc.close()


def read(path):
    """A legacy Word file -> ordered list[Para]. Dispatches on file magic."""
    _ensure_jvm()
    path = Path(path)
    magic = path.read_bytes()[:8]
    if magic.startswith(_ZIP_MAGIC):
        return _read_xwpf(path)
    if magic.startswith(_OLE2_MAGIC):
        return _read_hwpf(path)
    raise ValueError("%s: neither OLE2 (.doc) nor ZIP (.docx): %r" % (path, magic))
