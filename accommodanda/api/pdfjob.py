"""A PDF export as a background job, so no reader waits on an open request.

WeasyPrint lays out some 27 A4 pages a second on the development machine and
about 8 on the production host, and a statute printed with its full context
runs to hundreds of pages: brottsbalken with everything is 1058. The export
used to run inside the request, so on production that page passed nginx's
60-second proxy timeout several times over -- the reader got a 504 for a
render that went on to succeed and fill the cache for nobody.

Here the render moves to a worker thread and the browser follows it:

* ``start`` returns at once with a job id. Identical requests join the same
  job -- two readers, or one impatient reader clicking twice, share the one
  render instead of paying twice for the same bytes.
* WeasyPrint reports its own steps to ``weasyprint.progress``; this module
  listens and turns them into a percentage, a page count and a time left, so
  the wait says what it is doing rather than spinning.
* The result lands in the same disk cache ``/api/v1/pdf`` reads, so a job
  that reports ``klar`` means the PDF is one instant request away.

The waiting screen (``templates/pdf_wait.html``) is a real page at a real
address, not a blank tab: it polls the job, draws the bar, and replaces
itself with the PDF when the render lands.

The job routes are the site's own plumbing -- nothing but ``pdf.js`` and
``collection.js`` calls them -- so they sit on the internal API
(``api/internal.py``), at ``/internal-api/v1/pdf/…`` and same-origin only. The
single-shot ``GET /api/v1/pdf`` they share a cache with stays public: it is one
request that answers with the file.
"""

import logging
import re
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse

from ..lib import compress, tpl
from . import facsimiles, pdf, pdfcollection

# Two renders at a time. The work is CPU-bound Python, so more workers than
# the box has cores to spare just makes every reader wait longer; two lets a
# short export past a long one instead of queueing behind it, and leaves four
# of production's six cores to serve everyone else.
WORKERS = 2

# At most six requests wait behind the two active renders. A 5,000-page
# collection can occupy a worker for a long time and can use substantial
# memory. An unbounded ThreadPoolExecutor queue would let one busy client
# commit the server to hours of later work. Identical requests still join
# one job, and cached PDFs do not use a queue slot.
MAX_LIVE_JOBS = 8

# A finished job stays readable this long, so a poll that arrives after the
# last one still learns the render succeeded. The bytes live in the disk
# cache, not here -- this is a few hundred bytes of status.
KEEP_SECONDS = 600

# What share of the render each WeasyPrint step is worth, as the fraction
# complete when the step *begins*. Measured on the 422-page GDPR export with
# every context kind (steps 1-4 parse and cascade: 17 %, step 5 layout:
# 57 %, step 6 drawing: 21 %, step 7 fonts and file: 5 %). The shares hold
# per page, so they carry over to a document of another size.
_STEP_START = {1: 0.0, 2: 0.02, 3: 0.07, 4: 0.10, 5: 0.17, 6: 0.74, 7: 0.95}

# Within the layout step, the first pass over the pages is 81 % of the time;
# the repagination passes that resolve counter(pages) and the TOC's
# target-counter() re-use most of their page layouts and are quick.
_FIRST_PASS = 0.81

# Drawing and writing, as multiples of the measured layout time -- what the
# bar advances on while those steps run, since neither reports pages.
_DRAW_OF_LAYOUT = 0.37
_WRITE_OF_LAYOUT = 0.09

PHASES = {1: "läser sidan", 2: "läser formatmallen", 3: "räknar ut stilar",
          4: "bygger dokumentet", 5: "sätter sidorna", 6: "ritar sidorna",
          7: "skriver PDF:en"}

_STEP_RE = re.compile(r"^Step (\d)")
_PAGE_RE = re.compile(r"Page (\d+)")
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)


@dataclass
class Job:
    """One export's progress. Written by the rendering thread through
    ``note``; read by any number of polling requests. Attribute writes are
    atomic under the GIL and every reader tolerates a half-updated view (a
    page number one behind), so this needs no lock of its own."""

    id: str
    key: str                     # the cache file name: identical exports join
    started: float
    filename: str = "dokument.pdf"
    est_pages: int = 0           # from the transform, before layout begins
    step: int = 0
    pages: int = 0               # highest page laid out in the current pass
    total_pages: int = 0         # exact, once repagination proves it
    repagination: int = 0
    _at: dict[int, float] = field(default_factory=dict)   # step -> start time
    _peak: float = 0.0           # the bar never walks backwards
    finished: float | None = None
    error: str | None = None

    # -- written by the rendering thread ------------------------------------

    def plan(self, est_pages: int) -> None:
        """The transform's page estimate, known before WeasyPrint starts."""
        self.est_pages = est_pages

    def settle(self, future: Future) -> None:
        """The render is over, however it ended. The failure is read off the
        future rather than caught around the call: the reader is on another
        thread, so an exception nobody carries across would leave the job
        running for ever."""
        exc = future.exception()
        self.error = None if exc is None else "%s: %s" % (type(exc).__name__, exc)
        self.finished = time.monotonic()
        if self.error:
            # a failed export must not be joinable: the causes are transient
            # (a facsimile render, NFS), so "Försök igen" has to mean a fresh
            # render rather than the same recorded failure again
            with _lock:
                if _by_key.get(self.key) is self:
                    del _by_key[self.key]

    def note(self, message: str) -> None:
        """One ``weasyprint.progress`` line."""
        step = _STEP_RE.match(message)
        if not step:
            return
        self.step = int(step.group(1))
        self._at.setdefault(self.step, time.monotonic())
        if "Repagination" in message:
            # pass one is over, so the page count is now exact rather than
            # estimated -- and the pages of the passes after it are re-used
            # layouts, which say nothing about how much work is left
            self.total_pages = self.pages
            self.repagination += 1
        elif page := _PAGE_RE.search(message):
            self.pages = max(self.pages, int(page.group(1)))

    # -- read by polling requests -------------------------------------------

    @property
    def done(self) -> bool:
        return self.finished is not None

    def _raw_fraction(self, now: float) -> float:
        """How far the render has come."""
        if self.done:
            return 1.0
        if self.step < 5:
            return _STEP_START.get(self.step, 0.0)
        layout = _STEP_START[5]
        if self.step == 5:
            span = _STEP_START[6] - layout
            if self.repagination:
                return layout + span * (_FIRST_PASS + (1 - _FIRST_PASS)
                                        * self._elapsed_share(now, 5, 0.23))
            pages = self.total_pages or self.est_pages
            return layout + span * _FIRST_PASS * (min(1.0, self.pages / pages)
                                                  if pages else 0.0)
        # drawing and writing report no pages: advance on the clock, scaled
        # by how long this document's own layout took
        share = _DRAW_OF_LAYOUT if self.step == 6 else _WRITE_OF_LAYOUT
        start = _STEP_START[self.step]
        span = (_STEP_START[7] if self.step == 6 else 1.0) - start
        return start + span * self._elapsed_share(now, self.step, share)

    def _elapsed_share(self, now: float, step: int, of_layout: float) -> float:
        """How far `step` has run, as a share of its predicted duration --
        `of_layout` times the layout time this very document measured."""
        layout_seconds = self._at.get(6, now) - self._at.get(5, now)
        predicted = max(0.4, layout_seconds * of_layout)
        return min(1.0, (now - self._at.get(step, now)) / predicted)

    def status(self) -> dict:
        """The job as the waiting screen reads it."""
        now = time.monotonic()
        elapsed = now - self.started
        # 0.99 until the bytes are actually there: a bar that sits at 100 %
        # while the reader still waits is the one thing it must never do
        self._peak = max(self._peak, min(self._raw_fraction(now), 0.99))
        fraction = 1.0 if self.done else self._peak
        left = None
        if not self.done and fraction >= 0.05 and elapsed >= 1.5:
            left = round(elapsed * (1 - fraction) / fraction)
        return {"id": self.id, "klar": self.done, "fel": self.error,
                # a job that has not said a word after three seconds is not
                # starting, it is queued behind the other renders -- the
                # reader should be told which of the two they are waiting on
                "fas": PHASES.get(self.step) or ("i kö" if elapsed > 3
                                                 else "startar"),
                "andel": round(fraction, 4),
                "sida": self.pages,
                "sidor": self.total_pages or self.est_pages,
                "exakt": bool(self.total_pages),
                "kvar": left}


# --------------------------------------------------------------------------
# the registry: one live job per cache key, plus the finished ones for a while
# --------------------------------------------------------------------------

_lock = threading.Lock()
_jobs: dict[str, Job] = {}          # id -> job
_by_key: dict[str, Job] = {}        # cache key -> the live job for it
_pool: ThreadPoolExecutor | None = None
_threads: dict[int, Job] = {}       # rendering thread -> the job it serves


class QueueFull(RuntimeError):
    """The bounded PDF render queue has no slot for a new unique job."""


class _Handler(logging.Handler):
    """Every WeasyPrint progress line, routed to whichever job the emitting
    thread is rendering. Lines from a render outside a job (none today) are
    dropped, not logged."""

    def emit(self, record):
        if job := _threads.get(threading.get_ident()):
            job.note(record.getMessage())


def _install() -> ThreadPoolExecutor:
    """The worker pool, and the progress tap, created on first use. Both are
    serving-only machinery: at import time they would start threads inside
    every build process that imports the API (rule:serve-only-in-serve)."""
    global _pool
    if _pool is None:
        progress = logging.getLogger("weasyprint.progress")
        progress.setLevel(logging.INFO)
        # ours is the only consumer: a 900-page export writes a line per page
        # per pass, which in the server's own log is noise, not a record
        progress.propagate = False
        progress.addHandler(_Handler())
        _pool = ThreadPoolExecutor(WORKERS, thread_name_prefix="pdfexport")
    return _pool


def _reap(now: float) -> None:
    """Drop jobs whose result has been readable long enough. Called under
    `_lock` from `start`.

    A key is de-registered only while the job being dropped still owns it: a
    failed job releases its key at once (`Job.settle`) so a retry renders
    again, and popping the key here would unregister the *retry*, which the
    next identical request would then start a second time."""
    for job in [j for j in _jobs.values()
                if j.finished is not None and now - j.finished > KEEP_SECONDS]:
        del _jobs[job.id]
        if _by_key.get(job.key) is job:
            del _by_key[job.key]


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def _start(entry, filename, run) -> Job:
    """Start or join one cache-keyed render callable."""
    with _lock:
        _reap(time.monotonic())
        if (live := _by_key.get(entry.name)) is not None:
            return live
        if (not entry.is_file()
                and sum(not candidate.done for candidate in _by_key.values())
                >= MAX_LIVE_JOBS):
            raise QueueFull("PDF-kön är full; försök igen senare")
        job = Job(id=uuid.uuid4().hex[:16], key=entry.name,
                  started=time.monotonic(), filename=filename)
        _jobs[job.id] = job
        _by_key[job.key] = job
        if entry.is_file():
            job.finished = job.started
            return job
        pool = _install()
    pool.submit(_run, job, run).add_done_callback(job.settle)
    return job


def start(page, *, toc: bool, kinds: frozenset[str], subresource,
          amendments: bool, columns: int) -> Job:
    """Start -- or join -- the export of `page`. Raises FileNotFoundError if
    the page is not generated. A job whose result is already cached comes
    back finished, having rendered nothing."""
    entry = pdf.cache_entry(page, toc=toc, kinds=kinds,
                            amendments=amendments, columns=columns)
    return _start(entry, pdf.filename_for(page.name),
                  lambda progress: pdf.export(
                      page, toc=toc, kinds=kinds, subresource=subresource,
                      progress=progress, amendments=amendments, columns=columns))


def start_collection(manifest: pdfcollection.CollectionManifest, *, subresource,
                     generated) -> Job:
    """Start or join a stateless collection render."""
    entry = pdfcollection.cache_entry(manifest, generated)
    return _start(entry, pdfcollection.filename(manifest),
                  lambda progress: pdfcollection.export(
                      manifest, subresource=subresource, generated=generated,
                      progress=progress))


def _run(job: Job, run) -> None:
    """The render, on a worker thread. Whatever it raises travels back on the
    future to `Job.settle`; the thread binding is what routes WeasyPrint's
    progress lines to this job."""
    _threads[threading.get_ident()] = job
    try:
        run(job)
    finally:
        del _threads[threading.get_ident()]


def result(job: Job):
    """The finished job's cache entry, or None while it has no usable PDF."""
    if not job.done or job.error:
        return None
    entry = pdf.cache_dir() / job.key
    return entry if entry.is_file() else None


# --------------------------------------------------------------------------
# the waiting screen
# --------------------------------------------------------------------------

router = APIRouter(prefix="/pdf", tags=["pdf"])


def parse_request(path: str, kontext: str, columns: int):
    """The generated file and the context kinds an export request names, or
    the HTTP error it earns. Shared by all three routes of the feature --
    `/api/v1/pdf`, its job and its waiting screen answer one contract, and
    a second copy of it would drift the moment the contract moved."""
    assert columns in (1, 2), "PDF columns must be 1 or 2"  # rule:fail-fast
    try:
        kinds = pdf.parse_kinds(kontext)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    page = pdf.generated_page(path)
    if page is None:
        raise HTTPException(404, "no generated page at %r" % path)
    return page, frozenset() if columns == 2 else kinds

TPL = tpl.environment("accommodanda.api").get_template("pdf_wait.html").module


@router.get("/vanta", response_class=HTMLResponse, include_in_schema=False)
def pdf_wait_page(
        path: str = Query(..., description="public page path"),
        toc: bool = Query(False),
        kontext: str = Query(""),
        andringar: bool = Query(True),
        kolumner: int = Query(1, ge=1, le=2),
        download: bool = Query(False)):
    """The page a reader waits on while a large export renders. It is a real
    address on purpose: opening a blank tab and writing "Skapar PDF …" into
    it left the reader on a document with no URL to reload, share or keep.

    The screen starts the job itself, so the tab can be opened inside the
    click that asked for it (a popup blocker allows nothing later) and the
    render still only begins once."""
    page, _kinds = parse_request(path, kontext, kolumner)
    effective_kontext = "" if kolumner == 2 else kontext
    return HTMLResponse(TPL.wait_page(
        path, _title(page), toc, effective_kontext, andringar, kolumner,
        download))


def _title(page) -> str:
    """What the screen calls the document -- its own ``<title>``, read from
    the generated page. Not taken from the browser: this is a real address
    anyone can open, and it would then print whatever the URL said."""
    title = _TITLE_RE.search(compress.read_text(page))
    return unescape(title.group(1)).strip() if title else ""


@router.get("/samling/vanta", response_class=HTMLResponse,
            include_in_schema=False)
def pdf_collection_wait_page():
    """A real waiting address whose fragment carries the collection recipe."""
    return HTMLResponse(pdfcollection.wait_page())


@router.post("/samling/inspektera")
def pdf_collection_inspect(request: pdfcollection.InspectRequest):
    """Labels, options and selectable headings for the collection editor."""
    try:
        return {"documents": pdfcollection.inspect(request.paths)}
    except FileNotFoundError as exc:
        raise HTTPException(404, "no generated page at %r" % exc.args[0]) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/samling/jobb")
def pdf_collection_job_start(manifest: pdfcollection.CollectionManifest):
    """Start one stateless collection render and return its background job."""
    try:
        pdfcollection.validate(manifest)
        job = start_collection(
            manifest, subresource=facsimiles.subresource,
            generated=datetime.now(ZoneInfo("Europe/Stockholm")).date())
    except FileNotFoundError as exc:
        raise HTTPException(404, "no generated page at %r" % exc.args[0]) from None
    except QueueFull as exc:
        raise HTTPException(503, str(exc), headers={"Retry-After": "30"}) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return job.status()


@router.get("/jobb/{job_id}/resultat")
def pdf_job_result(job_id: str, download: bool = Query(False)):
    """The cached PDF produced by a finished background job."""
    job = get(job_id)
    if job is None:
        raise HTTPException(404, "no such pdf job")
    if job.error:
        raise HTTPException(503, job.error)
    entry = result(job)
    if entry is None:
        raise HTTPException(409, "pdf job is not finished")
    return FileResponse(
        entry, media_type="application/pdf", filename=job.filename,
        content_disposition_type="attachment" if download else "inline")


@router.post("/jobb")
def pdf_job_start(
        path: str = Query(..., description="public page path"),
        toc: bool = Query(False),
        kontext: str = Query(""),
        andringar: bool = Query(True),
        kolumner: int = Query(1, ge=1, le=2)):
    """Start the export in the background and answer at once with its job.
    A render already running for the same page and options is joined, not
    started again; one already in the cache comes back finished.

    This is what keeps a large export off the request: laying out a statute
    with its full context runs well past nginx's proxy timeout, and a reader
    who waited on it got a 504 for work that had in fact succeeded."""
    generated, kinds = parse_request(path, kontext, kolumner)
    try:
        job = start(generated, toc=toc, kinds=kinds,
                    subresource=facsimiles.subresource,
                    amendments=andringar, columns=kolumner)
    except FileNotFoundError:
        raise HTTPException(404, "no generated page at %r" % path) from None
    except QueueFull as exc:
        raise HTTPException(503, str(exc), headers={"Retry-After": "30"}) from None
    return job.status()


@router.get("/jobb/{job_id}")
def pdf_job_status(job_id: str):
    """How far the export has come: the step it is in, the pages it has laid
    out, and how many seconds are left at the rate it is going."""
    job = get(job_id)
    if job is None:
        raise HTTPException(404, "no such pdf job")
    return job.status()
