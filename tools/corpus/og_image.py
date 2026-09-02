"""Render lib/assets/og-image.png, the 1200x630 link-preview card every page's
og:image names (lib/templates/page.html). One site-wide image: the mark and the
wordmark in the site's own fonts and light-theme colours, laid out as HTML and
screenshotted with playwright's headless Chromium, so the card is the same
drawing the masthead makes. Run it again after changing the
mark, the fonts or the palette:

    uv run tools/corpus/og_image.py
"""

import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).resolve().parents[2] / "ferenda" / "lib" / "assets"
WIDTH, HEIGHT = 1200, 630


def _font(name):
    return "url(data:font/woff2;base64,%s) format('woff2')" % base64.b64encode(
        (ASSETS / "fonts" / name).read_bytes()).decode("ascii")


def html():
    mark = (ASSETS / "favicon.svg").read_text(encoding="utf-8")
    return """<!doctype html><meta charset="utf-8"><style>
@font-face { font-family: 'Source Serif 4'; font-weight: 200 900; src: %s; }
@font-face { font-family: 'Inter'; font-weight: 100 900; src: %s; }
html, body { margin: 0; background: #e9ecf0; }
body { width: %dpx; height: %dpx; display: flex; align-items: center;
       justify-content: center; gap: 72px; color: #171b21; }
svg { width: 150px; height: 330px; }
.word { font-family: 'Source Serif 4', serif; font-size: 168px; font-weight: 500;
        letter-spacing: -0.02em; line-height: 1; }
.word em { font-style: normal; color: #8f3524; }
.tag { font-family: 'Inter', sans-serif; font-size: 40px; color: #3b434d;
       margin-top: 26px; letter-spacing: 0.005em; }
</style><body>%s<div><div class="word">lagen<em>.nu</em></div>
<div class="tag">Sveriges lagar, med kontext</div></div>""" % (
        _font("source-serif-4-latin.woff2"), _font("inter-latin.woff2"),
        WIDTH, HEIGHT, mark)


def main():
    out = ASSETS / "og-image.png"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT},
                                color_scheme="light")
        page.set_content(html())
        page.wait_for_function("document.fonts.ready.then(() => true)")
        page.screenshot(path=str(out), full_page=False)
        browser.close()
    print("wrote %s (%d bytes)" % (out, out.stat().st_size))


if __name__ == "__main__":
    main()
