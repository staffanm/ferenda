"""Screenshot one URL, for embedding in the audit report.

    .venv/bin/python shot.py --out shots --name brb-k3p5 '/1962:700#K3P5'
    .venv/bin/python shot.py --out shots --name rail --clip 1200,0,400,1000 '/2009:400'
    .venv/bin/python shot.py --out shots --name toc --selector '.toc' '/dom/nja/2023s560'

A URL with a #fragment lands scrolled to that anchor. --scroll scrolls a further
N pixels, for reaching a rail state that only exists mid-document.

Keep images under 2000x2000: a --full page at --scale 2 will exceed the read
limit and cannot be viewed. Use --clip or --selector for detail shots instead.
"""

import argparse
import pathlib

from playwright.sync_api import sync_playwright


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--out", default="shots")
    ap.add_argument("--name")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=1100)
    ap.add_argument("--scale", type=int, default=1, help="device scale factor")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--mobile", action="store_true")
    ap.add_argument("--scroll", type=int, default=0, help="scroll this many px after load")
    ap.add_argument("--selector", help="clip to this element")
    ap.add_argument("--clip", help="clip region as x,y,width,height")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = args.name or (args.url.strip("/").replace("/", "_").replace(":", "-") or "shot")
    path = out / f"{name}.png"
    viewport = {"width": 390, "height": 844} if args.mobile else \
               {"width": args.width, "height": args.height}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=viewport, device_scale_factor=args.scale,
                                is_mobile=args.mobile, has_touch=args.mobile)
        msgs = []
        page.on("console", lambda m: msgs.append(f"{m.type}: {m.text}"))
        page.on("requestfailed", lambda r: msgs.append(f"requestfailed: {r.url}"))

        resp = page.goto(args.base + args.url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1000)
        if args.scroll:
            page.evaluate(f"window.scrollTo(0, {args.scroll})")
            page.wait_for_timeout(800)

        if args.selector:
            page.query_selector(args.selector).screenshot(path=str(path))
        elif args.clip:
            x, y, w, h = (int(v) for v in args.clip.split(","))
            page.screenshot(path=str(path), clip={"x": x, "y": y, "width": w, "height": h})
        else:
            page.screenshot(path=str(path), full_page=args.full)

        print(f"{resp.status if resp else '?'} {path}")
        for m in msgs[:15]:
            print("  ", m)
        browser.close()


if __name__ == "__main__":
    main()
