"""Emit a minimal but genuine raw-verdict PDF for the DV golden fixture.

Reproduces exactly the structure the bitmap-number recovery depends on:
 - a body at font size 19 (the size line_body_size will pick),
 - a smaller running header (size 8) that _body_lines must drop as marginalia,
 - three domskäl paragraphs, each preceded by a tiny left-margin *image* (the
   paragraph-number bitmap HD prints instead of selectable text).

No personal data, ~2 KB, deterministic -- a frozen golden fixture that still
drives pdftohtml/pdf_images/parse_pdf_record end to end. Run once; commit the
PDF it writes; this generator lives in the test tree as its provenance.
"""
import sys
import zlib

# a 8x8 black PNG-free raw image (a tiny grayscale XObject); the pixels don't
# matter -- pdftohtml reports only its box, which is what _paragraph_numbers reads
IMG_W_PX, IMG_H_PX = 8, 8
IMG_DATA = bytes([0]) * (IMG_W_PX * IMG_H_PX)   # 8-bit gray, all black


def obj(n, body):
    return ("%d 0 obj\n" % n).encode() + body + b"\nendobj\n"


def build():
    # page: A4-ish 595x842 pt. Body lines at size 19; a margin number image sits
    # at each numbered paragraph's baseline, at x=40 (well inside left<260).
    page_w, page_h = 595, 842
    body_size = 19
    header_size = 8
    # (y_baseline, text) for the text layer; y from bottom
    lines = [
        (810, header_size, "Hogsta domstolens beslut  Mal nr A 1-24  Sida 1"),
        (770, body_size, "DOMSKAL"),
        (730, body_size, "Fragan i malet ar om resning ska beviljas."),
        (690, body_size, "Klaganden har anfort i huvudsak foljande."),
        (650, body_size, "Hogsta domstolen gor foljande bedomning."),
    ]
    # margin number images at the three body-paragraph baselines (730/690/650)
    img_ys = [730, 690, 650]

    content = []
    for y, size, text in lines:
        esc = text.replace("(", r"\(").replace(")", r"\)")
        content.append("BT /F1 %d Tf 60 %d Td (%s) Tj ET" % (size, y, esc))
    for y in img_ys:
        # place the 8x8 image scaled to ~10pt in the left margin at x=40
        content.append("q 10 0 0 10 40 %d cm /Im0 Do Q" % (y + 2))
    stream = ("\n".join(content)).encode()

    objs = []
    objs.append((1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    objs.append((2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"))
    objs.append((3, ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
                     "/Resources << /Font << /F1 5 0 R >> "
                     "/XObject << /Im0 6 0 R >> >> /Contents 4 0 R >>"
                     % (page_w, page_h)).encode()))
    objs.append((4, ("<< /Length %d >>\nstream\n" % len(stream)).encode()
                 + stream + b"\nendstream"))
    objs.append((5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    objs.append((6, ("<< /Type /XObject /Subtype /Image /Width %d /Height %d "
                     "/ColorSpace /DeviceGray /BitsPerComponent 8 /Length %d >>\n"
                     "stream\n" % (IMG_W_PX, IMG_H_PX, len(IMG_DATA))).encode()
                 + IMG_DATA + b"\nendstream"))

    out = b"%PDF-1.4\n"
    offsets = {}
    for n, body in objs:
        offsets[n] = len(out)
        out += obj(n, body)
    xref_pos = len(out)
    out += ("xref\n0 %d\n" % (len(objs) + 1)).encode()
    out += b"0000000000 65535 f \n"
    for n, _ in objs:
        out += ("%010d 00000 n \n" % offsets[n]).encode()
    out += ("trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref_pos)).encode()
    return out


if __name__ == "__main__":
    open(sys.argv[1], "wb").write(build())
    print("wrote", sys.argv[1])
