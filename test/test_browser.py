"""The headful-Chrome transport's display handling and its completed-document
check.

The display: a real DISPLAY is used as is; a headless host gets a private Xvfb
virtual framebuffer so Chrome still runs *headful* (what the F5/Shape WAF
requires). Chrome itself isn't launched here -- only the display lifecycle,
which is the headless-server-specific part.

The check: `verify_document` is pure over what the browser read, so the three
ways a protected navigation ends short are exercised without a browser."""

import inspect
import os
import shutil

import pytest

from accommodanda.lib.browser import (
    DetachedChrome,
    IncompleteNavigation,
    WafRejected,
    verify_document,
)


@pytest.fixture
def fake_chrome(monkeypatch):
    # DetachedChrome.__init__ requires google-chrome on PATH; stand it in (and
    # keep every other binary lookup, notably Xvfb, resolving for real)
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which",
                        lambda name: "/usr/bin/google-chrome" if name == "google-chrome"
                        else real_which(name))


def test_existing_display_is_used_as_is(fake_chrome, monkeypatch):
    monkeypatch.setenv("DISPLAY", ":77")
    chrome = DetachedChrome("/tmp/prof", settle=1)
    chrome._ensure_display()
    assert chrome.xvfb is None            # no virtual display started
    assert os.environ["DISPLAY"] == ":77"


@pytest.mark.skipif(not shutil.which("Xvfb"), reason="Xvfb not installed")
def test_headless_host_starts_and_tears_down_xvfb(fake_chrome, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    chrome = DetachedChrome("/tmp/prof", settle=1)
    chrome._ensure_display()
    xvfb = chrome.xvfb
    try:
        assert xvfb is not None and xvfb.poll() is None
        assert os.environ["DISPLAY"].startswith(":")
    finally:
        chrome._teardown_display()                 # the failure-safe teardown half
    assert xvfb.poll() is not None                 # Xvfb stopped
    assert "DISPLAY" not in os.environ             # restored (was unset)


@pytest.mark.skipif(not shutil.which("Xvfb"), reason="Xvfb not installed")
def test_enter_failure_tears_down_the_display(fake_chrome, monkeypatch):
    # Chrome launch fails *after* _ensure_display started Xvfb; __exit__ never
    # runs (the `with` never bound), so __enter__ must tear the display back down
    # itself rather than leak the Xvfb process and a mutated DISPLAY.
    monkeypatch.delenv("DISPLAY", raising=False)
    chrome = DetachedChrome("/tmp/prof", settle=1)

    def boom(self):
        raise RuntimeError("chrome launch blew up")

    monkeypatch.setattr(DetachedChrome, "_launch_chrome", boom)
    with pytest.raises(RuntimeError, match="chrome launch blew up"):
        chrome.__enter__()
    assert chrome.xvfb is None                     # Xvfb torn down, handle cleared
    assert "DISPLAY" not in os.environ             # DISPLAY restored (was unset)


# --------------------------------------------------------------------------
# what a completed navigation has to be
# --------------------------------------------------------------------------

URL = "https://www4.skatteverket.se/rattsligvagledning/474872.html"


def test_a_finished_document_passes():
    assert verify_document(URL, "<html><body>Dnr: 8-1</body></html>",
                           "Dnr: 8-1", "Dnr:") is None


def test_a_waf_rejection_is_its_own_error():
    """A caller acts on this one: an F5/Shape front that has started rejecting
    keeps rejecting whatever profile asks, so a harvester should stop rather
    than retry. Rejection is checked first, because a rejection page carries no
    marker either and reading it as an incomplete navigation would send the
    caller into a longer-settle retry against a closed front."""
    with pytest.raises(WafRejected):
        verify_document(URL, "<html><body>The requested URL was rejected.</body>"
                        "</html>", "The requested URL was rejected. Please "
                        "consult with your administrator.", "Dnr:")


@pytest.mark.parametrize("html,body", [
    # the challenge script is still running
    ("<html><script>var bobcmn = 1;</script><body></body></html>", ""),
    # the page loaded, but not the document the caller named
    ("<html><body>Sidan kunde inte hittas</body></html>",
     "Sidan kunde inte hittas"),
])
def test_an_unfinished_navigation_says_to_wait_longer(html, body):
    with pytest.raises(IncompleteNavigation):
        verify_document(URL, html, body, "Dnr:")


def test_the_check_is_not_stripped_under_python_o():
    """These are remote conditions, so they raise rather than assert: under
    `python -O` an assert would strip, and the caller would store the WAF's
    rejection page as the document."""
    source = inspect.getsource(verify_document)
    assert "assert" not in source
