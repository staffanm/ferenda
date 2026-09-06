"""Camoufox transport for sites that reject ordinary HTTP clients.

Some public sites gate their documents behind a JavaScript bot challenge:
F5/Shape on skatteverket.se and tillvaxtanalys.se, Cloudflare on icj-cij.org
and ohchr.org. Camoufox is a Firefox build whose fingerprint spoofing sits in
the browser's own C++ rather than in injected JavaScript, so it clears those
challenges *headless* -- which plain Playwright Chromium never did (the F5
front hard-rejected it, and instrumented headful Chrome too, which is why this
module used to relaunch a detached Chrome per navigation and attach over CDP
afterwards).

Because the browser is driven normally again, a navigation ends when the page
says it has: `html` waits for the caller's marker instead of sleeping a fixed
settle. Callers still own URL selection and source semantics.
"""

import time
from pathlib import Path

from .errors import UpstreamChanged

# a page is finished when its text carries the caller's marker -- or when the
# WAF says no, which is terminal and must not be waited out
_READY = """m => { const t = document.body ? document.body.innerText : "";
    return t.includes(m)
        || t.toLowerCase().includes("requested url was rejected"); }"""

# How long a challenged host is given to mint its cookie before the navigation
# is tried again. Measured: icj-cij.org's Cloudflare clearance appears 7.7 s
# into the first navigation, and tillvaxtanalys.se's F5 front wants two tries.
CHALLENGE_WAIT = 8.0
PDF_ATTEMPTS = 4


def _camoufox_api():
    """The Camoufox launcher and Playwright's timeout error, on first use.

    Deferred: importing either loads the greenlet C extension (and camoufox's
    numpy and lxml besides), and build.py pulls this module -- through
    foreskrift.harvest -- into *every* worker process. Parse workers must not
    carry a coroutine-switching C extension they never use
    (rule:no-infunction-imports sanctioned exception, mirrored in pyproject
    per-file-ignores)."""
    from camoufox.sync_api import Camoufox
    from playwright.sync_api import TimeoutError
    return Camoufox, TimeoutError


class WafRejected(RuntimeError):
    """The site's bot defence refused this navigation.

    Its own class so a caller can *tell it apart* from a navigation that merely
    ran out of time, because the two want opposite responses: measured against
    skatteverket.se, an F5/Shape front that has started rejecting keeps
    rejecting for a while whatever the browser asks, so retrying is useless
    while a longer wait is the fix for the other. What a harvester does with
    that is its own policy (`rs.download.until_blocked` stops the run; the
    föreskrift harvests do not, their agencies being tens of documents rather
    than thousands). A raise, not an assert -- this is a remote condition, and
    under ``python -O`` an assert would return the WAF's rejection page as the
    document (rule:errors-drive-retry-use-raise)."""


class IncompleteNavigation(RuntimeError):
    """The navigation returned, but the page is not the finished document --
    the JavaScript challenge is still running, or the content the caller named
    never appeared. Distinct from :class:`WafRejected`: this one usually means
    the timeout was too short, and retrying with a longer one is the fix."""


def verify_document(url, html, body, marker):
    """Raise unless the loaded page is the finished document the caller asked
    for. Pure over what the browser read, so the three ways a protected
    navigation ends short are testable without a browser.

    The order matters: a WAF rejection also carries no marker, and calling that
    an incomplete navigation would send a caller into a longer-timeout retry
    against a front that has stopped answering."""
    if "requested url was rejected" in body.lower():
        raise WafRejected("%s was rejected by its WAF" % url)
    if "bobcmn" in html:
        raise IncompleteNavigation("%s is still a JavaScript challenge" % url)
    if marker not in body:
        raise IncompleteNavigation(
            "%s completed without expected marker %r" % (url, marker))


class CamoufoxBrowser:
    """One Camoufox session, reused for every navigation in a run.

    `timeout` is how long a page has to become the document the caller named.
    `pace` is a *minimum* interval between navigations, and only Skatteverket
    needs one: its front rejects everything for a while once some 30 documents
    have been asked for inside a couple of minutes (measured: navigation 31 at
    2-second spacing). That is a rate rule rather than a bot verdict, so no
    browser gets around it -- see `rs.download.SKV_PAGE_PACE`."""

    def __init__(self, profile, timeout=60.0, pace=0.0):
        assert timeout > 0, "navigation timeout must be positive"
        assert pace >= 0, "navigation pace cannot be negative"
        self.profile = Path(profile)
        self.timeout = timeout
        self.pace = pace
        self.session = None
        self.page = None
        self._timeout_error: type[Exception] | None = None
        self._last_navigation = 0.0

    def __enter__(self):
        camoufox, self._timeout_error = _camoufox_api()
        self.profile.mkdir(parents=True, exist_ok=True)
        # persistent_context: the profile keeps each host's solved-challenge
        # cookie, so a re-run starts past the wall rather than at it. locale
        # sv-SE because every Swedish site here serves on it.
        self.session = camoufox(headless=True, humanize=True, locale="sv-SE",
                                persistent_context=True,
                                user_data_dir=str(self.profile))
        context = self.session.__enter__()
        self.page = context.new_page()
        return self

    def __exit__(self, exc_type, exc, traceback):
        session = self.session
        assert session is not None, "the Camoufox session is not open"
        self.session = self.page = None
        session.__exit__(exc_type, exc, traceback)

    def _open_page(self):
        page = self.page
        assert page is not None, "the Camoufox session is not open"
        return page

    def _goto(self, url, timeout):
        page = self._open_page()
        wait = self.pace - (time.monotonic() - self._last_navigation)
        if wait > 0:
            time.sleep(wait)
        self._last_navigation = time.monotonic()
        return page.goto(url, timeout=1000 * timeout)

    def html(self, url, marker, timeout=None):
        """Navigate, then return the completed HTML document carrying `marker`.

        `timeout` overrides the session's own for this one navigation. A site is
        rarely uniform: Skatteverkets register page renders 2,600 rows and takes
        half a minute, while each document page it links is done in one second."""
        timeout = self.timeout if timeout is None else timeout
        page = self._open_page()
        expired = self._timeout_error
        assert expired is not None, "the Camoufox session is not open"
        self._goto(url, timeout)
        try:
            page.wait_for_function(_READY, arg=marker, timeout=1000 * timeout)
        except expired:
            # not the error to report: `verify_document` reads the page that is
            # actually there and names which of the three ways it ended short,
            # and a caller acts on that distinction (rule:no-catch-log-continue
            # -- this handler hands the decision to the check below, it does not
            # swallow a failure)
            pass
        html = page.content()
        verify_document(url, html, page.locator("body").inner_text(), marker)
        return html

    def pdf(self, url, timeout=None):
        """Navigate to `url` and return the exact PDF bytes the browser read.

        A challenged host answers the first navigation with its challenge page
        instead of the file. That page mints the host's cookie while it runs, so
        the next navigation gets the PDF -- one retry against icj-cij.org
        (Cloudflare), two against tillvaxtanalys.se (F5), none once the profile
        already holds the cookie."""
        timeout = self.timeout if timeout is None else timeout
        for _attempt in range(PDF_ATTEMPTS):
            response = self._goto(url, timeout)
            assert response is not None, "%s did not answer the navigation" % url
            content_type = response.headers.get("content-type", "")
            if content_type.startswith("application/pdf"):
                data = response.body()
                # a raise, not an assert: what the far end served is a remote
                # condition, and under ``python -O`` an assert would strip and
                # this would store an error page as the document
                # (rule:errors-drive-retry-use-raise)
                if not data.startswith(b"%PDF-"):
                    raise UpstreamChanged(
                        "%s served %d bytes under application/pdf that do not "
                        "start a PDF" % (url, len(data)))
                return data
            if "requested url was rejected" in response.text().lower():
                raise WafRejected("%s was rejected by its WAF" % url)
            time.sleep(CHALLENGE_WAIT)
        raise IncompleteNavigation(
            "%s served %r rather than a PDF in %d navigations"
            % (url, content_type, PDF_ATTEMPTS))
