"""Record a screencast of the site for the manual (`/om/*` pages).

    uv run tools/screencast/record.py tools/screencast/casts/sok.json
    uv run tools/screencast/record.py tools/screencast/casts/*.json --base https://lagen.nu

The default `--base` is the public site. A local `lagen serve` works too: a
page loaded at its own path carries no `<base>`, so links, hover previews and
API calls stay on that host.

Each cast is a JSON file: a name, a viewport and a list of steps. The steps
drive a Chromium page through Playwright, which records the video. The output
is `<name>.webm` plus `<name>.png` (the first frame, the poster the page shows
before play) in the content repo's `site/media/`, where `![alt](<name>.webm)`
in a site markdown file picks it up. A `shot` step writes a still the same
way, so the screenshots in the manual come from the same runs as the films.

Playwright records VP8 at about 1 Mbit/s and has no quality setting, so the
recording is re-encoded to VP9 with the system ffmpeg (`apt install ffmpeg`;
the one Playwright ships cannot encode). Every current browser plays VP9 in
WebM; Safari on iOS from 17.4.

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
    {"highlight": "selector"}                   frame an element for a moment
    {"pause": 1500}                             stand still
    {"shot": "name"}                            write site/media/<name>.png

Every step accepts "wait" (ms to pause afterwards) and the ones that name an
element accept "nth" to pick among several matches.
"""

import argparse
import json
import math
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from ferenda.lib import layout

MEDIA = layout.WIKI_ROOT / "site" / "media"

# the pause after a step, by verb, when the step does not set its own
WAIT = {"goto": 900, "caption": 900, "move": 300, "click": 900, "type": 500,
        "key": 900, "select": 1200, "hover": 1800, "scroll": 700,
        "highlight": 200, "pause": 0, "shot": 0}

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
    window.__cast = { caption(t) { cap.textContent = t; cap.classList.toggle('on', !!t); } };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
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
        getattr(self, "do_" + verb)(step[verb], step)
        self.page.wait_for_timeout(step.get("wait", WAIT[verb]))

    # -- the verbs -------------------------------------------------------

    def do_goto(self, url, step):
        # a bare path is on the site; a full URL (github, paraGRAF) is itself
        self.page.goto(url if url.startswith("http") else self.base + url,
                       wait_until=step.get("until", "networkidle"))
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
        self.do_move(target, step)
        self.page.mouse.down()
        self.page.wait_for_timeout(120)
        self.page.mouse.up()

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
        steps = max(8, int(ms / 16))
        for i in range(1, steps + 1):
            k = i / steps
            e = 0.5 - math.cos(k * math.pi) / 2
            self.page.mouse.move(x0 + (x1 - x0) * e, y0 + (y1 - y0) * e)
            time.sleep(ms / steps / 1000)
        self.pos = to


# constant quality, tuned for a screen recording: text stays readable at 46,
# and the file lands at about 70% of Playwright's own VP8 output
VP9 = ("ffmpeg", "-v", "error", "-y", "-i", "{src}", "-c:v", "libvpx-vp9",
       "-b:v", "0", "-crf", "46", "-row-mt", "1", "-deadline", "good",
       "-cpu-used", "2", "-tune-content", "screen", "-an", "{dest}")


def encode(src, dest):
    """Re-encode a VP8 recording to VP9 at `dest`."""
    assert shutil.which("ffmpeg"), "ffmpeg is not installed (apt install ffmpeg)"
    subprocess.run([a.format(src=src, dest=dest) for a in VP9], check=True)


def record(cast, base, out):
    name = cast["name"]
    viewport = {"width": cast.get("width", 1600), "height": cast.get("height", 1000)}
    with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
        browser = p.chromium.launch()
        context = browser.new_context(viewport=viewport, record_video_dir=tmp,
                                      record_video_size=viewport)
        context.add_init_script(OVERLAY)
        page = context.new_page()
        rec = Recorder(page, base, out)
        for step in cast["steps"]:
            rec.run(step)
        page.wait_for_timeout(1200)
        video = page.video
        context.close()
        browser.close()
        encode(video.path(), out / (name + ".webm"))
    (out / (name + ".png")).write_bytes(rec.poster)
    print("%s: %s.webm (%d steps)" % (name, out / name, len(cast["steps"])))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("casts", nargs="+", type=Path)
    ap.add_argument("--base", default="https://ferenda.lagen.nu")
    ap.add_argument("--out", type=Path, default=MEDIA)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for path in args.casts:
        record(json.loads(path.read_text(encoding="utf-8")), args.base, args.out)


if __name__ == "__main__":
    main()
