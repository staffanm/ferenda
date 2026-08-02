"""Walk a list of URLs, screenshot each, and report what a reader would see.

    .venv/bin/python browse.py --out shots / /sfs/ /dom/ /folkratt/

Prints one JSON object per URL: status, title, headings, nav labels, rail
headings, dl.meta pairs, console errors and failed requests. Screenshots land
in --out, named after the path.
"""

import argparse
import json
import pathlib

from playwright.sync_api import sync_playwright

PROBE = """() => ({
    title: document.title,
    h1: [...document.querySelectorAll('h1')].map(e => e.textContent.trim()),
    nav: [...document.querySelectorAll('nav a, .masthead a')]
            .map(e => e.textContent.trim()).slice(0, 40),
    railHeads: [...document.querySelectorAll('.rail-h, .rail summary, .rail h2, .rail h3')]
            .map(e => e.textContent.replace(/\\s+/g, ' ').trim()),
    railSections: [...document.querySelectorAll('.rail-sec')]
            .map(e => e.className + ' | ' + e.textContent.replace(/\\s+/g, ' ').trim().slice(0, 60)),
    meta: [...document.querySelectorAll('dl.meta dt')].map((dt, i) => {
        const dd = document.querySelectorAll('dl.meta dd')[i];
        return dt.textContent.trim() + ': ' + (dd ? dd.textContent.trim().slice(0, 70) : '');
    }).slice(0, 25),
    headings: [...document.querySelectorAll('h2, h3')]
            .map(e => e.textContent.replace(/\\s+/g, ' ').trim()).slice(0, 40),
    anchors: document.querySelectorAll('a').length,
    emptyLinks: [...document.querySelectorAll('a')].filter(a => !a.textContent.trim()).length,
    textLen: document.body.innerText.length,
})"""


def slug(url):
    return url.strip("/").replace("/", "_").replace(":", "-").replace("#", "-at-") or "front"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--out", default="shots")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=1400)
    ap.add_argument("--full", action="store_true", help="full-page screenshots")
    ap.add_argument("--mobile", action="store_true", help="390x844 touch viewport")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    viewport = {"width": 390, "height": 844} if args.mobile else \
               {"width": args.width, "height": args.height}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for url in args.urls:
            page = browser.new_page(viewport=viewport, is_mobile=args.mobile,
                                    has_touch=args.mobile)
            console, failed = [], []
            page.on("console", lambda m: m.type in ("error", "warning")
                    and console.append(f"{m.type}: {m.text}"))
            page.on("requestfailed", lambda r: failed.append(f"{r.url} {r.failure}"))

            resp = page.goto(args.base + url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(800)
            shot = out / f"{slug(url)}.png"
            page.screenshot(path=str(shot), full_page=args.full)

            print(json.dumps({"url": url, "status": resp.status if resp else None,
                              "shot": str(shot), "console": console[:15],
                              "requestFailed": failed[:10], **page.evaluate(PROBE)},
                             ensure_ascii=False, indent=1))
            page.close()
        browser.close()


if __name__ == "__main__":
    main()
