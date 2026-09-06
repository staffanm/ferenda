"""Record a screencast of the site for the manual (`/om/*` pages).

    uv run tools/screencast/record.py tools/screencast/casts/sok.json
    uv run tools/screencast/record.py tools/screencast/casts/*.json --base https://lagen.nu

The default `--base` is the public site. A local `lagen serve` works too: a
page loaded at its own path carries no `<base>`, so links, hover previews and
API calls stay on that host.

Each cast is a JSON file: a name, a viewport and a list of steps. The steps
drive a Chromium page through Playwright. The output
is `<name>.webm` plus `<name>.png` (the first frame, the poster the page shows
before play) in the content repo's `site/media/`, where `![alt](<name>.webm)`
in a site markdown file picks it up. A `shot` step writes a still the same
way, so the screenshots in the manual come from the same runs as the films.

Chromium supplies lossless PNG frames through its screencast protocol. FFmpeg
encodes them directly to VP9, preserving their timing. This avoids the JPEG
and low-bitrate VP8 intermediate in Playwright's built-in video recorder.
Install ffmpeg (`apt install ffmpeg`) or pass its path with `--ffmpeg`.
`--crf` controls final compression: 30 by default; lower values preserve more
detail. `--browser` can name an installed Chrome instead of Playwright's browser.
PDF scenes need Chrome's full viewer; use `--browser /usr/bin/google-chrome`
when Playwright is configured with a headless shell that cannot display PDFs.
`--warmup` rehearses the script once before recording, to warm PDF caches.
`--frames-only DIR` saves the frames and poster in a new directory for inspection
and a separate encode. A cast's optional `preload` list caches genuine PNG
responses in the temporary directory's `ferenda-screencast-cache` folder.

Playwright gives no visible mouse pointer, no text on screen and no pauses,
so the recorder adds all three: an init script draws a pointer that follows
the page's own mousemove events and a caption strip along the bottom, and
every step pauses after itself so the viewer can see what happened.

Steps (one verb per object, the rest are options):

    {"goto": "/1915:218"}                       navigate, wait for the page to settle
                                                ("until": "load" for a page that never idles)
    {"caption": "Text på skärmen"}              show a caption; "" removes it
    {"move": "selector"}                        glide the pointer to an element
    {"click": "selector"}                       move there and click
    {"type": "avtalslagen 36 §"}                type, one key at a time
    {"key": "Enter"}                            press a key (Playwright's names)
    {"select": "selector", "label": "…"}        choose an option in a <select>
    {"hover": "selector"}                       move there and stay, for a preview
    {"scroll": "selector"} / {"scroll": 600}    scroll smoothly to an element or by px
    {"pdf_scroll": 10, "duration": 9000}        scroll the native PDF viewer through page 10
    {"highlight": "selector"}                   frame an element for a moment
    {"pause": 1500}                             stand still
    {"shot": "name"}                            write site/media/<name>.png

Every step accepts "wait" (ms to pause afterwards) and the ones that name an
element accept "nth" to pick among several matches. "ready" waits for a visible
selector after the action, before the pause, for asynchronous search and previews.
"duration" sets the time for a pointer move or scroll, in milliseconds.
"say" sets the caption when an action starts, including typing and navigation.
"ready_image" waits for an image to load and decode before the reading pause.
"same_tab" keeps a link that opens a new tab in the captured tab instead.
"""

import argparse
import base64
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

from ferenda.lib import layout

MEDIA = layout.WIKI_ROOT / "site" / "media"

# the pause after a step, by verb, when the step does not set its own
WAIT = {"goto": 900, "caption": 900, "move": 300, "click": 900, "type": 500,
        "key": 900, "select": 1200, "hover": 1800, "scroll": 700,
        "highlight": 200, "pause": 0, "shot": 0, "pdf_scroll": 1500}

OVERLAY = r"""
(() => {
  const css = `
    #cast-cursor { position: fixed; left: 0; top: 0; width: 24px; height: 28px;
      pointer-events: none; z-index: 2147483647; opacity: 0;
      transition: transform .1s; transform-origin: 5px 3px; }
    #cast-cursor.down { transform: scale(.78); }
    #cast-caption { position: fixed; left: 50%; bottom: 5%;
      transform: translateX(-50%); max-width: 72%; padding: .55em 1.1em;
      background: rgba(20, 22, 26, .88); color: #fff; border-radius: 8px;
      font: 500 23px/1.35 Inter, system-ui, sans-serif; text-align: center;
      pointer-events: none; z-index: 2147483646; opacity: 0;
      transition: opacity .3s; }
    #cast-caption.on { opacity: 1; }
    .cast-mark { outline: 3px solid #c8452b !important; outline-offset: 3px;
      border-radius: 4px; }`;
  function install() {
    if (document.getElementById('cast-cursor')) return;
    const st = document.createElement('style');
    st.textContent = css;
    document.head.appendChild(st);
    const c = document.createElement('div');
    c.id = 'cast-cursor';
    c.innerHTML = '<svg viewBox="0 0 24 28" width="24" height="28">'
      + '<path d="M5 3 L5 22 L10 17 L13.5 25 L16.5 23.7 L13 16 L20 16 Z" '
      + 'fill="#fff" stroke="#111" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    document.body.appendChild(c);
    const cap = document.createElement('div');
    cap.id = 'cast-caption';
    document.body.appendChild(cap);
    document.addEventListener('mousemove', e => {
      c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px';
      c.style.opacity = 1; }, true);
    document.addEventListener('mousedown', () => c.classList.add('down'), true);
    document.addEventListener('mouseup', () => c.classList.remove('down'), true);
    window.__cast = { caption(t) {
      sessionStorage.setItem('cast-caption', t);
      cap.textContent = t; cap.classList.toggle('on', !!t);
    } };
    window.__cast.caption(sessionStorage.getItem('cast-caption') || '');
  }
  // Long legal documents can paint before DOMContentLoaded. Install as soon
  // as the body exists so a navigation's caption appears with its first paint.
  if (document.body) install();
  else {
    const observer = new MutationObserver(() => {
      if (document.body) { observer.disconnect(); install(); }
    });
    observer.observe(document, {childList: true, subtree: true});
  }
})();
"""

# a smooth scroll the page runs itself, resolved when it has arrived: the
# browser's own `behavior: smooth` has no completion signal
SCROLL = """([top, ms]) => new Promise(done => {
  const el = document.scrollingElement, from = el.scrollTop, t0 = performance.now();
  const step = now => {
    const k = Math.min(1, (now - t0) / ms), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
    el.scrollTop = from + (top - from) * e;
    if (k < 1) requestAnimationFrame(step); else done();
  };
  requestAnimationFrame(step);
})"""


class Recorder:
    def __init__(self, page, base, out):
        self.page, self.base, self.out = page, base, out
        self.caption = ""
        self.pos = (page.viewport_size["width"] // 2, page.viewport_size["height"] // 2)
        self.poster = None

    def run(self, step):
        verb = next(k for k in step if k in WAIT)
        if "say" in step:
            self.do_caption(step["say"], step)
        getattr(self, "do_" + verb)(step[verb], step)
        if "ready" in step:
            self.page.locator(step["ready"]).first.wait_for(
                state="visible", timeout=step.get("ready_timeout", 30000))
        if "ready_image" in step:
            self.page.wait_for_function("""selector => {
                const img = document.querySelector(selector);
                return img && img.complete && img.naturalWidth > 0;
            }""", arg=step["ready_image"], timeout=step.get("ready_timeout", 120000))
            self.page.locator(step["ready_image"]).evaluate("img => img.decode()")
        self.page.wait_for_timeout(step.get("wait", WAIT[verb]))

    # -- the verbs -------------------------------------------------------

    def do_goto(self, url, step):
        # a bare path is on the site; a full URL (github, paraGRAF) is itself
        response = self.page.goto(url if url.startswith("http") else self.base + url,
                                  wait_until=step.get("until", "networkidle"))
        assert response is None or response.ok, "%s: HTTP %s" % (self.page.url, response.status)
        self.page.wait_for_timeout(400)
        if self.poster is None:
            self.poster = self.page.screenshot()
        self.page.mouse.move(*self.pos)
        self.page.evaluate("t => window.__cast.caption(t)", self.caption)

    def do_caption(self, text, step):
        self.caption = text
        self.page.evaluate("t => window.__cast.caption(t)", text)

    def do_move(self, target, step):
        self._glide(self._point(target, step), step.get("duration", 650))

    def do_click(self, target, step):
        # Collection export normally opens a new tab. Keep the generated PDF
        # in the captured tab so the film can show its progress and result.
        if step.get("same_tab"):
            self.page.evaluate("""() => {
                window.open = url => { window.location.href = url; return window; };
            }""")
        self.do_move(target, step)
        self._locator(target, step).click(delay=120)
        if "url" in step:
            self.page.wait_for_url(step["url"], timeout=step.get("ready_timeout", 120000))

    def do_type(self, text, step):
        self.page.keyboard.type(text, delay=step.get("delay", 75))

    def do_key(self, key, step):
        self.page.keyboard.press(key)

    def do_select(self, target, step):
        self.do_move(target, step)
        self._locator(target, step).select_option(label=step["label"])

    def do_hover(self, target, step):
        self.do_move(target, step)

    def do_scroll(self, target, step):
        if isinstance(target, str):
            top = self._locator(target, step).evaluate(
                "el => el.getBoundingClientRect().top + window.scrollY") - step.get("offset", 120)
        else:
            top = self.page.evaluate("() => document.scrollingElement.scrollTop") + target
        self.page.evaluate(SCROLL, [top, step.get("duration", 900)])

    def do_pdf_scroll(self, last_page, step):
        # Chrome renders PDFs in an extension frame, not in the page's
        # scrollingElement. Wheel events reach the real PDF plugin.
        deadline = time.monotonic() + 30
        viewer_frame = None
        while viewer_frame is None:
            viewer_frame = self.page.frame(
                url="chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/index.html")
            assert time.monotonic() < deadline, "Chrome's PDF viewer did not open"
            self.page.wait_for_timeout(100)
        viewer_frame.wait_for_function("""() => {
            const v = document.querySelector('pdf-viewer');
            return v && v.loadState_ === 'success' && v.documentDimensions;
        }""")
        viewer = viewer_frame.locator("pdf-viewer")
        top = viewer.evaluate("""(v, n) => {
            const p = v.documentDimensions.pageDimensions[n - 1];
            return p.y * v.viewport_.getZoom();
        }""", last_page)
        self.page.wait_for_timeout(step.get("cover_wait", 1000))
        self._glide((1000, 500), 350)
        start = viewer.evaluate("v => v.viewport_.position.y")
        sent, started = 0, time.monotonic()
        while True:
            k = min(1, (time.monotonic() - started) / (step.get("duration", 9000) / 1000))
            distance = (top - start) * k
            self.page.mouse.wheel(0, distance - sent)
            sent = distance
            if k == 1:
                break
            self.page.wait_for_timeout(50)
        viewer_frame.wait_for_function(
            "n => document.querySelector('pdf-viewer').viewport_.getMostVisiblePage() === n - 1",
            arg=last_page)
        print("PDF viewer reached page %d" % last_page, flush=True)

    def do_highlight(self, target, step):
        loc = self._locator(target, step)
        loc.evaluate("el => el.classList.add('cast-mark')")
        self.page.wait_for_timeout(step.get("hold", 1400))
        loc.evaluate("el => el.classList.remove('cast-mark')")

    def do_pause(self, ms, step):
        self.page.wait_for_timeout(ms)

    def do_shot(self, name, step):
        # the overlay is ours, not the site's: a still shows the page alone
        self.page.evaluate("() => { const c = document.getElementById('cast-cursor');"
                           " c.style.opacity = 0; window.__cast.caption(''); }")
        self.page.wait_for_timeout(400)     # the caption fades out
        loc = self._locator(step["selector"], step) if "selector" in step else self.page
        loc.screenshot(path=str(self.out / (name + ".png")))
        self.page.evaluate("t => window.__cast.caption(t)", self.caption)
        self.page.mouse.move(*self.pos)

    # -- helpers ---------------------------------------------------------

    def _locator(self, selector, step):
        return self.page.locator(selector).nth(step.get("nth", 0))

    def _point(self, target, step):
        if isinstance(target, list):
            return tuple(target)
        loc = self._locator(target, step)
        loc.scroll_into_view_if_needed()
        box = loc.bounding_box()
        assert box, "%r matched nothing visible" % target
        return (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    def _glide(self, to, ms):
        x0, y0 = self.pos
        x1, y1 = to
        started = time.monotonic()
        while True:
            # Browser round-trips and PNG capture take time too. Pace against
            # elapsed time so a 650ms move does not become twice that long.
            k = min(1, (time.monotonic() - started) / max(ms / 1000, 0.001))
            e = 0.5 - math.cos(k * math.pi) / 2
            self.page.mouse.move(x0 + (x1 - x0) * e, y0 + (y1 - y0) * e)
            if k == 1:
                break
            self.page.wait_for_timeout(16)
        self.pos = to


class Capture:
    """Lossless browser frames with their real presentation times.

    Playwright's video passes through JPEG and a 1 Mbit/s VP8 encoder before
    our VP9 encode. Text damaged there cannot be recovered by lowering the
    final CRF. Chromium's PNG screencast avoids both lossy intermediate steps.
    It emits only changed frames; concat durations preserve the quiet reading
    pauses without writing thousands of identical PNG files.
    """

    def __init__(self, context, page, directory):
        self.session = context.new_cdp_session(page)
        self.directory = directory
        self.frames = []
        self.session.on("Page.screencastFrame", self.frame)

    def frame(self, event):
        path = self.directory / ("frame-%06d.png" % len(self.frames))
        path.write_bytes(base64.b64decode(event["data"]))
        self.frames.append((path.name, event["metadata"]["timestamp"]))
        self.session.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    def start(self):
        self.session.send("Page.startScreencast", {"format": "png", "everyNthFrame": 1})

    def finish(self):
        self.session.send("Page.stopScreencast")
        stopped = time.time()
        assert self.frames, "Chromium produced no screencast frames"
        lines = ["ffconcat version 1.0"]
        for i, (name, timestamp) in enumerate(self.frames):
            end = self.frames[i + 1][1] if i + 1 < len(self.frames) else stopped
            lines += ["file '%s'" % name, "duration %.6f" % max(0.001, end - timestamp)]
        # concat needs the last frame repeated to honour its duration.
        lines.append("file '%s'" % self.frames[-1][0])
        manifest = self.directory / "frames.ffconcat"
        manifest.write_text("\n".join(lines) + "\n")
        return manifest


def encode(src, dest, ffmpeg="ffmpeg", crf=30):
    """Encode lossless frames once, at constant VP9 quality."""
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(src), "-vf", "fps=25", "-c:v", "libvpx-vp9",
                    "-b:v", "0", "-crf", str(crf), "-row-mt", "1",
                    "-deadline", "good", "-cpu-used", "5", "-tune-content", "screen",
                    "-pix_fmt", "yuv420p", "-an", str(dest)], check=True)


def record(cast, base, out, *, ffmpeg="ffmpeg", crf=30, browser_path=None,
           warmup=False, frames_only=None):
    assert shutil.which(ffmpeg), "ffmpeg is not installed; install it or pass --ffmpeg"
    name = cast["name"]
    viewport = {"width": cast.get("width", 1600), "height": cast.get("height", 1000)}
    if frames_only:
        frames_only.mkdir(parents=True, exist_ok=False)
    with sync_playwright() as p, (nullcontext(str(frames_only)) if frames_only
                                 else tempfile.TemporaryDirectory()) as tmp:
        browser = p.chromium.launch(executable_path=browser_path)
        context = browser.new_context(viewport=viewport, color_scheme="light")
        # Keep genuine source images in this recording session's cache. A
        # site-wide rate limit can otherwise reject even an already cached
        # facsimile when the film reaches its Original button.
        for path in cast.get("preload", []):
            url = base + path
            cache = Path(tempfile.gettempdir()) / "ferenda-screencast-cache"
            cache.mkdir(exist_ok=True)
            cached = cache / (hashlib.sha256(url.encode()).hexdigest() + ".png")
            if not cached.exists():
                with urlopen(url, timeout=120) as response:
                    assert response.headers.get_content_type() == "image/png", url
                    cached.write_bytes(response.read())
            png = cached.read_bytes()
            assert png.startswith(b"\x89PNG\r\n\x1a\n"), cached
            context.route(url, lambda route, request, body=png: route.fulfill(
                status=200, content_type="image/png", body=body))
        context.add_init_script(OVERLAY)
        page = context.new_page()
        if warmup:
            rehearsal = Recorder(page, base, out)
            for n, step in enumerate(cast["steps"], 1):
                print("Warmup %d/%d: %s" % (n, len(cast["steps"]),
                      json.dumps(step, ensure_ascii=False)), flush=True)
                rehearsal.run(step)
            # A clean collection and caption for take two; HTTP/PDF caches
            # remain warm. This browser context exists only for this film.
            page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
            page.close()
            page = context.new_page()
        rec = Recorder(page, base, out)
        capture = Capture(context, page, Path(tmp))
        # Start on the first settled page, not on an empty browser window.
        assert "goto" in cast["steps"][0], "a cast must start with a goto step"
        rec.run(cast["steps"][0])
        capture.start()
        started = time.monotonic()
        for n, step in enumerate(cast["steps"][1:], 2):
            print("%s %d/%d (%.1fs): %s" %
                  (name, n, len(cast["steps"]), time.monotonic() - started,
                   json.dumps(step, ensure_ascii=False)), flush=True)
            rec.run(step)
        page.wait_for_timeout(1200)
        frames = capture.finish()
        (Path(tmp) / (name + ".png")).write_bytes(rec.poster)
        context.close()
        browser.close()
        if frames_only:
            print("Captured %.1fs in %s" % (time.monotonic() - started, frames), flush=True)
            return
        print("Encoding %s (%.1fs, CRF %d)…" %
              (name, time.monotonic() - started, crf), flush=True)
        encoded = Path(tmp) / (name + ".webm")
        encode(frames, encoded, ffmpeg, crf)
        # Keep an existing published movie intact until encoding succeeds.
        shutil.copyfile(encoded, out / (name + ".webm"))
    (out / (name + ".png")).write_bytes(rec.poster)
    print("%s: %s.webm (%d steps)" % (name, out / name, len(cast["steps"])))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("casts", nargs="+", type=Path)
    ap.add_argument("--base", default="https://lagen.nu")
    ap.add_argument("--out", type=Path, default=MEDIA)
    ap.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg executable or path")
    ap.add_argument("--browser", help="Chromium/Chrome executable (default: Playwright's)")
    ap.add_argument("--warmup", action="store_true", help="Rehearse once before recording to warm caches")
    ap.add_argument("--frames-only", type=Path, help="Save lossless frames to a new directory; skip encoding")
    ap.add_argument("--crf", type=int, choices=range(64), default=30,
                    metavar="0–63", help="VP9 quality; lower is sharper (default: 30)")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for path in args.casts:
        record(json.loads(path.read_text(encoding="utf-8")), args.base, args.out,
               ffmpeg=args.ffmpeg, crf=args.crf, browser_path=args.browser,
               warmup=args.warmup, frames_only=args.frames_only)


if __name__ == "__main__":
    main()
