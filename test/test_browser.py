"""The Camoufox transport's pacing and its completed-document check.

Both are exercised without a browser. `verify_document` is pure over what the
browser read, so the three ways a protected navigation ends short are covered
here; the pace is the one piece of navigation policy this module owns, and
Skatteverkets rate rule depends on it holding."""

import inspect
import time

import pytest

from ferenda.lib import browser as browser_module
from ferenda.lib.browser import (
    PDF_ATTEMPTS,
    CamoufoxBrowser,
    IncompleteNavigation,
    WafRejected,
    verify_document,
)


# --------------------------------------------------------------------------
# pacing: the minimum interval between navigations
# --------------------------------------------------------------------------

class FakePage:
    """Records when each navigation was asked for."""

    def __init__(self):
        self.times = []

    def goto(self, url, timeout=None):
        self.times.append(time.monotonic())
        return url


def test_navigations_are_spaced_by_the_pace():
    """Skatteverkets front rejects everything for minutes once some 30
    navigations land inside two, so the pace is what keeps a 2,614-document
    backfill under its limit."""
    browser = CamoufoxBrowser("/tmp/prof", pace=0.2)
    browser.page = FakePage()
    for _ in range(3):
        browser._goto("https://example.se/", timeout=5)
    gaps = [b - a for a, b in zip(browser.page.times, browser.page.times[1:])]
    assert all(gap >= 0.2 for gap in gaps), gaps


def test_an_unpaced_session_does_not_wait():
    browser = CamoufoxBrowser("/tmp/prof")
    browser.page = FakePage()
    started = time.monotonic()
    for _ in range(3):
        browser._goto("https://example.se/", timeout=5)
    assert time.monotonic() - started < 0.2


def test_a_navigation_without_a_session_fails_loudly():
    browser = CamoufoxBrowser("/tmp/prof")
    with pytest.raises(AssertionError, match="not open"):
        browser._goto("https://example.se/", timeout=5)


# --------------------------------------------------------------------------
# the PDF fetch: a challenged host answers once with its challenge page
# --------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, content_type, body=b"", text=""):
        self.headers = {"content-type": content_type}
        self._body = body
        self._text = text

    def body(self):
        return self._body

    def text(self):
        return self._text


class ScriptedPage:
    """Answers each navigation with the next scripted response."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.navigations = 0

    def goto(self, _url, timeout=None):
        self.navigations += 1
        return self.responses.pop(0)


def _browser(responses, monkeypatch):
    monkeypatch.setattr(browser_module, "CHALLENGE_WAIT", 0.0)
    browser = CamoufoxBrowser("/tmp/prof")
    browser.page = ScriptedPage(responses)
    return browser


def test_a_pdf_is_returned_from_the_navigation_itself(monkeypatch):
    browser = _browser([FakeResponse("application/pdf", b"%PDF-1.7 body")],
                       monkeypatch)
    assert browser.pdf("https://example.se/x.pdf") == b"%PDF-1.7 body"


def test_a_challenge_page_is_retried_until_the_pdf_arrives(monkeypatch):
    """icj-cij.org answers the first navigation with Cloudflare's interstitial
    and mints its cookie while that page runs; the next navigation gets the
    file."""
    browser = _browser([FakeResponse("text/html", text="Just a moment..."),
                        FakeResponse("application/pdf", b"%PDF-1.4 body")],
                       monkeypatch)
    assert browser.pdf("https://example.se/x.pdf") == b"%PDF-1.4 body"
    assert browser.page.navigations == 2


def test_a_rejected_pdf_navigation_stops_at_once(monkeypatch):
    """A WAF rejection is terminal, so it must not spend the retries: the front
    has closed and the caller counts it (`rs.download.until_blocked`)."""
    browser = _browser([FakeResponse(
        "text/html", text="The requested URL was rejected.")], monkeypatch)
    with pytest.raises(WafRejected):
        browser.pdf("https://example.se/x.pdf")
    assert browser.page.navigations == 1


def test_a_host_that_never_serves_the_pdf_gives_up(monkeypatch):
    browser = _browser([FakeResponse("text/html")] * PDF_ATTEMPTS, monkeypatch)
    with pytest.raises(IncompleteNavigation):
        browser.pdf("https://example.se/x.pdf")
    assert browser.page.navigations == PDF_ATTEMPTS


def test_a_body_that_is_not_a_pdf_raises_rather_than_asserts(monkeypatch):
    """The far end served an error page under `application/pdf`. That is a
    remote condition, so it raises: under `python -O` an assert would strip and
    the error page would be stored as the document
    (rule:errors-drive-retry-use-raise)."""
    browser = _browser([FakeResponse("application/pdf", b"<html>oops</html>")],
                       monkeypatch)
    with pytest.raises(ValueError, match="do not start a PDF"):
        browser.pdf("https://example.se/x.pdf")
    source = inspect.getsource(CamoufoxBrowser.pdf)
    assert "assert data" not in source


# --------------------------------------------------------------------------
# what a completed navigation has to be
# --------------------------------------------------------------------------

URL = "https://www4.skatteverket.se/rattsligvagledning/474872.html"


def test_a_finished_document_passes():
    assert verify_document(URL, "<html><body>Dnr: 8-1</body></html>",
                           "Dnr: 8-1", "Dnr:") is None


def test_a_waf_rejection_is_its_own_error():
    """A caller acts on this one: an F5/Shape front that has started rejecting
    keeps rejecting whatever the browser asks, so a harvester should stop rather
    than retry. Rejection is checked first, because a rejection page carries no
    marker either and reading it as an incomplete navigation would send the
    caller into a longer-timeout retry against a closed front."""
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
