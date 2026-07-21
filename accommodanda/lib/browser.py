"""Headful Chrome transport for sites that reject instrumented navigation.

Some JavaScript bot defences classify a browser while their challenge is
running and reject it merely because a DevTools client is attached.  This
transport keeps Playwright/CDP completely disconnected during navigation,
then attaches briefly to read the completed DOM or exact browser-cached
resource bytes.  Callers still own URL selection and source semantics.
"""

import base64
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


def _sync_playwright():
    # deferred: importing playwright loads the greenlet C extension, and
    # build.py pulls this module (through foreskrift.harvest) into *every*
    # worker process -- parse workers must not carry a coroutine-switching C
    # extension they never use (rule:no-infunction-imports sanctioned
    # exception, mirrored in pyproject per-file-ignores)
    from playwright.sync_api import sync_playwright
    return sync_playwright()


class DetachedChrome:
    """One real Chrome session whose navigations happen without Playwright."""

    def __init__(self, profile, settle=20.0):
        assert settle > 0, "detached navigation settle time must be positive"
        chrome = shutil.which("google-chrome")
        # a runtime environment check, not an internal invariant: raise so it
        # survives `python -O` (an assert would strip to Popen([None, ...]) and a
        # cryptic TypeError far from the cause) -- rule:errors-drive-retry-use-raise
        if chrome is None:
            raise RuntimeError("google-chrome is not installed")
        self.chrome: str = chrome
        self.profile = Path(profile)
        self.settle = settle
        self.process: subprocess.Popen[bytes] | None = None
        self.endpoint: str | None = None
        self.command: list[str] | None = None
        self.xvfb: subprocess.Popen[bytes] | None = None
        self._prev_display: str | None = None

    def _ensure_display(self):
        """Guarantee a real X display for headful Chrome. A desktop's ``DISPLAY``
        is used as-is; on a headless host (runlevel 3, no X) start a private Xvfb
        virtual framebuffer and point ``DISPLAY`` at it. Chrome runs *headful*
        against Xvfb exactly as against a monitor -- which is the whole point: the
        F5/Shape WAF rejects ``--headless``, not the absence of a screen. Torn
        down in ``__exit__``; a genuinely headless host without Xvfb is a
        fail-fast (rule:fail-fast), not a silent fall back to headless."""
        if os.environ.get("DISPLAY"):
            return
        xvfb = shutil.which("Xvfb")
        # runtime environment check -> raise, not assert: it must fail the same
        # way under `python -O` (rule:errors-drive-retry-use-raise)
        if xvfb is None:
            raise RuntimeError(
                "headless host has no DISPLAY and no Xvfb -- `apt install xvfb` "
                "so headful Chrome has a virtual framebuffer to draw on")
        # -displayfd lets Xvfb pick a free display number and report it back, so
        # concurrent runs never fight over a fixed :99
        read_fd, write_fd = os.pipe()
        self.xvfb = subprocess.Popen(
            [xvfb, "-displayfd", str(write_fd), "-screen", "0", "1440x900x24",
             "-nolisten", "tcp"],
            pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(write_fd)
        number = b""
        while not number.endswith(b"\n"):
            chunk = os.read(read_fd, 64)      # Xvfb writes the number once ready
            # EOF means Xvfb died before reporting its display. This is the loop's
            # only exit on that path, so it must raise, not assert -- under
            # `python -O` an assert strips and the loop busy-spins on b"" forever
            # (rule:errors-drive-retry-use-raise).
            if not chunk:
                os.close(read_fd)
                raise OSError("Xvfb exited before reporting a display number")
            number += chunk
        os.close(read_fd)
        self._prev_display = os.environ.get("DISPLAY")
        os.environ["DISPLAY"] = ":" + number.strip().decode()

    def __enter__(self):
        self._ensure_display()
        # _ensure_display already spawned Xvfb and mutated os.environ["DISPLAY"];
        # if launching Chrome now fails, __exit__ will NOT run (the `with` never
        # bound), so tear the half-built session down here or the Xvfb process
        # leaks and DISPLAY stays pointed at a dead framebuffer for whatever runs
        # next in this process (rule:fail-fast -- no half-initialized session).
        try:
            self._launch_chrome()
        except BaseException:
            self._teardown()
            raise
        return self

    def _launch_chrome(self):
        self.profile.mkdir(parents=True, exist_ok=True)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.endpoint = "http://127.0.0.1:%d" % port
        command = [
            self.chrome,
            "--user-data-dir=%s" % self.profile,
            "--remote-debugging-port=%d" % port,
            "--no-first-run",
            "--no-default-browser-check",
            "--lang=sv-SE",
            "--window-size=1440,900",
        ]
        if os.environ.get("FERENDA_CHROME_NO_SANDBOX"):
            # in a container Chrome's sandbox can't initialise as a non-root uid
            # (no host userns/SUID sandbox); the container is the isolation
            # boundary. Env-gated so dev keeps its real desktop sandbox.
            # --disable-dev-shm-usage: Docker's default 64 MB /dev/shm is too small
            # and crashes Chrome on big pages.
            command += ["--no-sandbox", "--disable-dev-shm-usage"]
        process: subprocess.Popen[bytes] = subprocess.Popen(
            [*command, "about:blank"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.command = command
        self.process = process
        for _attempt in range(50):
            assert process.poll() is None, \
                "Google Chrome exited with %s" % process.returncode
            try:
                with urlopen(self.endpoint + "/json/version", timeout=1) as response:
                    json.load(response)
            except URLError:
                time.sleep(0.2)
                continue
            break
        else:
            raise RuntimeError("Google Chrome debugging endpoint did not start")

    def __exit__(self, _exc_type, _exc, _traceback):
        self._teardown()

    def _teardown(self):
        """Stop Chrome (if it started) and tear the private Xvfb display back
        down, restoring the prior ``DISPLAY``. Idempotent and safe to call from a
        failed ``__enter__`` as well as ``__exit__`` -- neither half need have
        been reached, so each is guarded by its own handle."""
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        self._teardown_display()

    def _teardown_display(self):
        """Tear down the private Xvfb framebuffer (if we started one) and restore
        the ``DISPLAY`` that was in effect before ``_ensure_display``."""
        if self.xvfb is None:                         # a real desktop DISPLAY was used
            return
        if self.xvfb.poll() is None:
            self.xvfb.terminate()
            self.xvfb.wait(timeout=10)
        if self._prev_display is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = self._prev_display
        self.xvfb = None

    def _navigate(self, url):
        command = self.command
        process = self.process
        assert command is not None and process is not None and process.poll() is None, \
            "Google Chrome session is not running"
        subprocess.run(
            [*command, url],
            check=True,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(self.settle)

    @staticmethod
    def _page(browser, url):
        pages = [page for context in browser.contexts for page in context.pages
                 if page.url == url]
        assert pages, "Google Chrome has no completed page for %s" % url
        return pages[-1]

    def html(self, url, marker):
        """Navigate detached, then return a verified completed HTML document."""
        self._navigate(url)
        playwright = _sync_playwright().start()
        try:
            endpoint = self.endpoint
            assert endpoint is not None, "Google Chrome debugging endpoint is absent"
            browser = playwright.chromium.connect_over_cdp(endpoint)
            page = self._page(browser, url)
            html = page.content()
            body = page.locator("body").inner_text()
            assert "bobcmn" not in html, "%s is still a JavaScript challenge" % url
            assert "requested url was rejected" not in body.lower(), \
                "%s was rejected by its WAF" % url
            assert marker in body, "%s completed without expected marker %r" % (url, marker)
            page.close()
            return html
        finally:
            playwright.stop()

    def pdf(self, url):
        """Navigate detached, then read the exact cached PDF through Chrome."""
        self._navigate(url)
        playwright = _sync_playwright().start()
        try:
            endpoint = self.endpoint
            assert endpoint is not None, "Google Chrome debugging endpoint is absent"
            browser = playwright.chromium.connect_over_cdp(endpoint)
            page = self._page(browser, url)
            session = page.context.new_cdp_session(page)
            frame_id = session.send("Page.getFrameTree")["frameTree"]["frame"]["id"]
            resource = session.send("Network.loadNetworkResource", {
                "frameId": frame_id,
                "url": url,
                "options": {"disableCache": False, "includeCredentials": True},
            })["resource"]
            assert resource["success"] and resource["httpStatusCode"] == 200, \
                "%s did not load successfully through Chrome" % url
            headers = {key.lower(): value for key, value in resource["headers"].items()}
            assert headers.get("content-type") == "application/pdf", \
                "%s served %r instead of PDF" % (url, headers.get("content-type"))
            data = bytearray()
            while True:
                chunk = session.send("IO.read", {"handle": resource["stream"]})
                data.extend(base64.b64decode(chunk["data"])
                            if chunk.get("base64Encoded") else chunk["data"].encode())
                if chunk["eof"]:
                    break
            session.send("IO.close", {"handle": resource["stream"]})
            page.close()
            assert data.startswith(b"%PDF-"), "%s cached body is not a PDF" % url
            return bytes(data)
        finally:
            playwright.stop()
