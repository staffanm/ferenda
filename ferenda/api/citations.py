"""Public citation extraction and metadata endpoints."""

import functools
import hashlib
import json
import logging
import re
import sqlite3
import traceback
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..lib import citationextract, datasets, lagrum
from .db import get_con

MAX_CHARACTERS = 250_000
MAX_BODY_BYTES = 2_000_000
REPO_ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger(__name__)


class ExtractionRoute(APIRoute):
    """Bound streamed bodies and omit submitted text from validation errors."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            received = 0

            async def receive():
                nonlocal received
                message = await request.receive()
                received += len(message.get("body", b""))
                if received > MAX_BODY_BYTES:
                    raise HTTPException(413, "request body exceeds 2000000 bytes")
                return message

            try:
                if request.method in ("POST", "PUT", "PATCH"):
                    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                        raise HTTPException(415, "send application/json containing text or blocks")
                response = await original(Request(request.scope, receive))
            except RequestValidationError as exc:
                # FastAPI normally echoes invalid field values as `input`.
                # This endpoint receives private text, not public search terms.
                response = JSONResponse(status_code=422, content={"detail": [
                    {key: value for key, value in error.items() if key in ("loc", "msg", "type")}
                    for error in exc.errors()]})
            except HTTPException as exc:
                response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            except Exception as exc:  # noqa: BLE001 — recover privacy-safe error reporting: exception messages can contain submitted text (rule:no-catch-log-continue)
                frames = traceback.extract_tb(exc.__traceback__)
                log.error("citation extraction failed: %s at %s", type(exc).__name__,
                          [(frame.filename, frame.lineno, frame.name) for frame in frames])
                response = JSONResponse(status_code=500, content={"detail": "citation extraction failed"})
            if request.method == "POST":
                response.headers["Cache-Control"] = "no-store"
            return response

        return handle


router = APIRouter(prefix="/api/v1/citations", tags=["citations"], route_class=ExtractionRoute)


class TextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, description="unique client block id")
    text: str = Field(max_length=MAX_CHARACTERS, description="unaltered extracted text")


CitationKind = Literal[citationextract.KINDS]


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(None, max_length=MAX_CHARACTERS,
                            description="plain text; use either text or blocks")
    blocks: list[TextBlock] | None = Field(
        None, min_length=1, max_length=5000,
        description="blocks in reading order, up to 250000 characters in total")
    kinds: list[CitationKind] | None = Field(
        None, description="return only targets of these kinds; default: all kinds")
    whole_documents: list[CitationKind] | None = Field(
        None, description="kinds whose document may be cited without a provision, article, "
                          "paragraph or page; default: all kinds. For example ['case', 'eu-case'] "
                          "leaves out a bare 'GDPR' or 'brottsbalken' but keeps 'artikel 17 GDPR'")
    case_names: Literal["bare", "with_identifier"] = Field(
        "bare", description="'with_identifier': a popular case name ('Strukturen') alone is not "
                            "a citation; the case is still found by its reference (NJA 2024 s. 445)")

    @model_validator(mode="after")
    def check_document(self) -> Self:
        if (self.text is None) == (self.blocks is None):
            raise ValueError("provide exactly one of text or blocks")
        if self.blocks:
            if len({block.id for block in self.blocks}) != len(self.blocks):
                raise ValueError("block ids must be unique")
            if sum(len(block.text) for block in self.blocks) > MAX_CHARACTERS:
                raise ValueError("combined text exceeds 250000 characters")
        values = [value for block in self.blocks or [] for value in (block.id, block.text)]
        if self.text is not None:
            values.append(self.text)
        if any(re.search(r"[\ud800-\udfff]", value) for value in values):
            raise ValueError("text and block ids must not contain unpaired Unicode surrogates")
        return self


class CitationLocation(BaseModel):
    block_id: str
    start: int = Field(ge=0, description="inclusive UTF-16 offset in this block")
    end: int = Field(ge=0, description="exclusive UTF-16 offset in this block")


class CitationTarget(BaseModel):
    uri: str = Field(description="interpreted target; existence is not established")
    source: str


class CitationOccurrence(BaseModel):
    text: str = Field(description="original citation text; a newline joins text from adjacent blocks")
    locations: list[CitationLocation]
    targets: list[CitationTarget] = Field(
        description="zero or more interpretations; an empty list means an unresolved candidate")


class ExtractionResponse(BaseModel):
    offset_unit: Literal["utf-16"] = "utf-16"
    occurrences: list[CitationOccurrence]


@router.post("/extract", response_model=ExtractionResponse,
             summary="Find legal citations in text or ordered document blocks",
             responses={413: {"description": "Request body exceeds 2000000 bytes"},
                        415: {"description": "Only application/json is accepted"}})
def extract_endpoint(body: ExtractionRequest, con: Annotated[sqlite3.Connection, Depends(get_con)]):
    """Find Swedish, EU and international citation occurrences without full-text search.

    Send plain `text`, or `blocks` with unique ids in reading order. Plain text
    uses block id `text`. Original PDF/DOCX files and OCR are not accepted here;
    clients extract their text and retain the mapping from block ids to pages,
    paragraphs or footnotes. A citation crossing blocks has several locations.

    Offsets count UTF-16 code units, so JavaScript `text.slice(start, end)`
    selects the original text. Blocks join with one newline during parsing.
    Context is shared within this request and reset between requests.

    Every occurrence is returned, including repeats. Targets are interpretations,
    not existence verdicts. Send each distinct target URI to `/api/v1/resolve`
    to check it. For an empty target list, the occurrence text can be submitted
    to `/resolve`, but an empty response does not establish invalidity.

    Three optional fields select what comes back. `kinds` limits the targets to
    some kinds. `whole_documents` lists the kinds that may be cited without a
    provision, article, paragraph or page, so `["case", "eu-case"]` leaves out a
    bare "GDPR" but keeps "artikel 17 GDPR". `case_names: "with_identifier"`
    leaves out a popular case name that stands alone. Context still comes from
    the whole text, and an occurrence left with no targets is not returned.

    Your text is processed only in temporary memory on lagen.nu. It is
    discarded after processing, never saved, and never forwarded elsewhere.
    Results are returned only to you. Responses and validation errors use `no-store`.
    Limits: 250000 Unicode characters, 5000 blocks, 2000000 JSON body bytes.
    """
    blocks = ([block.model_dump() for block in body.blocks] if body.blocks is not None
              else [{"id": "text", "text": body.text}])
    return ExtractionResponse(occurrences=citationextract.extract(
        blocks, con, kinds=body.kinds, whole_documents=body.whole_documents, case_names=body.case_names))


DATASET_REGISTRY: dict[tuple[str, str], dict[str, Any]] = {
    ("sfs", "namedlaws"): {
        "source": "sfs",
        "name": "namedlaws",
        "description": "Swedish named laws, nicknames, and abbreviations",
        "path": datasets.NAMEDLAWS,
    },
    ("eurlex", "namedacts"): {
        "source": "eurlex",
        "name": "namedacts",
        "description": "EU named acts, regulations, and directives",
        "path": datasets.NAMEDACTS,
    },
    ("eurlex", "treaties"): {
        "source": "eurlex",
        "name": "treaties",
        "description": "EU founding treaties and the Charter of Fundamental Rights",
        "path": datasets.EU_TREATIES,
    },
    ("eurlex", "casenames"): {
        "source": "eurlex",
        "name": "casenames",
        "description": "Court of Justice of the European Union (CJEU) case nicknames",
        "path": datasets.NAMEDEUCASES,
    },
    ("hudoc", "casenames"): {
        "source": "hudoc",
        "name": "casenames",
        "description": "European Court of Human Rights (ECHR) party names and application numbers (supports ?trim=true)",
        "path": datasets.EMD_CASES,
    },
    ("hudoc", "respondents_sv"): {
        "source": "hudoc",
        "name": "respondents_sv",
        "description": "Swedish translations of ECHR respondent state names",
        "path": datasets.EMD_RESPONDENTS,
    },
    ("dv", "namedcases"): {
        "source": "dv",
        "name": "namedcases",
        "description": "Swedish Supreme Court (HD) precedential case nicknames",
        "path": datasets.NAMEDCASES,
    },
    ("dv", "casenumbers"): {
        "source": "dv",
        "name": "casenumbers",
        "description": "Swedish court case numbers mapped to published referat URIs",
        "path": datasets.CASENUMBERS,
        "fallback": REPO_ROOT / "test" / "files" / "dv" / "casenumbers.json",
    },
    ("avg", "arsberattelse"): {
        "source": "avg",
        "name": "arsberattelse",
        "description": "Justitieombudsmannen (JO) annual report page citations",
        "path": datasets.JO_ARSBERATTELSE,
    },
    ("foreskrift", "series"): {
        "source": "foreskrift",
        "name": "series",
        "description": "Swedish agency regulatory code series designations",
        "path": datasets.FS_SERIES,
    },
    ("coe", "names"): {
        "source": "coe",
        "name": "names",
        "description": "Council of Europe treaty names",
        "path": datasets.COE_NAMES,
    },
    ("icrc", "names"): {
        "source": "icrc",
        "name": "names",
        "description": "International Committee of the Red Cross treaty names",
        "path": datasets.ICRC_NAMES,
    },
    ("untc", "treaties"): {
        "source": "untc",
        "name": "treaties",
        "description": "United Nations Treaty Collection treaty names and numbers",
        "path": datasets.UNTC_TREATIES,
    },
    ("lib", "treaty_names"): {
        "source": "lib",
        "name": "treaty_names",
        "description": "Common English treaty names cited by international courts",
        "path": datasets.TREATY_NAMES,
    },
    ("lib", "grammar"): {
        "source": "lib",
        "name": "grammar",
        "description": "Lark EBNF citation grammar rules, terminals, and triggers",
        "path": None,
    },
}


def trim_echr_casenames(data: dict) -> dict:
    """Trim anonymous cases (single-letter applicant like 'x' or 'a') and non-judgment records.

    Reduces the uncompressed payload from ~6.6 MB to ~1.5 MB (~230 KB in Brotli).
    """
    cases = {}
    for key, entries in data.get("cases", {}).items():
        applicant = key.split("|")[0].strip()
        if len(applicant) <= 1:
            continue
        judgments = [e for e in entries if e[0] == "j"]
        if judgments:
            cases[key] = judgments

    appnos = {}
    for no, entries in data.get("appnos", {}).items():
        judgments = [e for e in entries if e[0] == "j"]
        if judgments:
            appnos[no] = judgments

    return {
        "_comment": data.get("_comment", "ECHR cases trimmed to judgments and named applicants."),
        "cases": cases,
        "appnos": appnos,
    }


def get_grammar_payload() -> bytes:
    """Return JSON bytes of the Lark EBNF grammar rules, terminals, and triggers."""
    grammar_obj = {
        "type_order": lagrum.TYPE_ORDER,
        "depends": lagrum.DEPENDS,
        "roots": lagrum.ROOTS,
        "rules": lagrum.RULES,
        "terminals": lagrum.TERMINALS,
        "eu_terminals": lagrum.EU_TERMINALS,
        "eu_extra_rules": lagrum.EU_EXTRA_RULES,
        "eu_namnakt_rules": lagrum.EU_NAMNAKT_RULES,
        "trigger_src": lagrum.TRIGGER_SRC,
        "eu_trigger_src_eng": lagrum.EU_TRIGGER_SRC_ENG,
        "eu_rules_eng": lagrum.EU_RULES_ENG,
    }
    return json.dumps(grammar_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


@functools.lru_cache(maxsize=32)
def get_dataset_payload(source: str, name: str, trim: bool = False) -> tuple[bytes, str]:
    """Return (raw_json_bytes, etag) for a registered dataset."""
    key = (source, name)
    if key not in DATASET_REGISTRY:
        raise KeyError(f"unknown dataset: {source}/{name}")

    if key == ("lib", "grammar"):
        raw = get_grammar_payload()
        etag = f'"{hashlib.sha256(raw).hexdigest()[:16]}"'
        return raw, etag

    spec = DATASET_REGISTRY[key]
    path = spec["path"]
    if path is not None and not path.exists() and "fallback" in spec and spec["fallback"].exists():
        path = spec["fallback"]
    if path is None or not path.exists():
        raise FileNotFoundError(f"dataset file missing: {path}")

    raw = path.read_bytes()
    if (source, name) == ("hudoc", "casenames") and trim:
        data = json.loads(raw.decode("utf-8"))
        trimmed = trim_echr_casenames(data)
        raw = json.dumps(trimmed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    etag = f'"{hashlib.sha256(raw).hexdigest()[:16]}"'
    return raw, etag


class DatasetMeta(BaseModel):
    id: str
    source: str
    name: str
    description: str
    bytes: int
    etag: str
    url: str


class MetadataCatalogResponse(BaseModel):
    datasets: list[DatasetMeta]


@router.get("/metadata", response_model=MetadataCatalogResponse, summary="List available citation parser datasets")
def list_metadata_endpoint():
    """List all available parser datasets with identifiers, sizes, and ETags."""
    items = []
    for (source, name), spec in DATASET_REGISTRY.items():
        path = spec.get("path")
        if path is not None:
            if not path.exists() and "fallback" in spec and spec["fallback"].exists():
                path = spec["fallback"]
            if not path.exists():
                continue
        try:
            raw, etag = get_dataset_payload(source, name, trim=False)
        except (KeyError, FileNotFoundError):
            continue
        items.append(DatasetMeta(
            id=f"{source}/{name}",
            source=source,
            name=name,
            description=spec["description"],
            bytes=len(raw),
            etag=etag,
            url=f"/api/v1/citations/metadata/{source}/{name}",
        ))
    return MetadataCatalogResponse(datasets=items)


@router.get("/metadata/{source}/{name}", summary="Get an individual citation parser dataset")
def get_dataset_endpoint(source: str, name: str, request: Request, trim: bool = False):
    """Serve an individual dataset by source and name, with ETag and Brotli/gzip compression support."""
    key = (source, name)
    if key not in DATASET_REGISTRY:
        raise HTTPException(404, f"unknown dataset: {source}/{name}")
    try:
        raw_bytes, etag = get_dataset_payload(source, name, trim=trim)
    except FileNotFoundError:
        raise HTTPException(404, f"dataset not found: {source}/{name}") from None

    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={
            "ETag": etag,
            "Cache-Control": "public, max-age=3600, stale-while-revalidate=86400",
            "Vary": "Accept-Encoding",
        })

    return Response(
        content=raw_bytes,
        media_type="application/json; charset=utf-8",
        headers={
            "ETag": etag,
            "Cache-Control": "public, max-age=3600, stale-while-revalidate=86400",
            "Vary": "Accept-Encoding",
        },
    )

