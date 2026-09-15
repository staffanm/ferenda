"""Public citation extraction from text supplied in a POST body."""

import logging
import re
import sqlite3
import traceback
from typing import Annotated, Literal, Self

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..lib import citationextract
from .db import get_con

MAX_CHARACTERS = 250_000
MAX_BODY_BYTES = 2_000_000
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
            response.headers["Cache-Control"] = "no-store"
            return response

        return handle


router = APIRouter(prefix="/api/v1/citations", tags=["citations"], route_class=ExtractionRoute)


class TextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, description="unique client block id")
    text: str = Field(max_length=MAX_CHARACTERS, description="unaltered extracted text")


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(None, max_length=MAX_CHARACTERS,
                            description="plain text; use either text or blocks")
    blocks: list[TextBlock] | None = Field(
        None, min_length=1, max_length=5000,
        description="blocks in reading order, up to 250000 characters in total")

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

    Your text is processed only in temporary memory on lagen.nu. It is
    discarded after processing, never saved, and never forwarded elsewhere.
    Results are returned only to you. Responses and validation errors use `no-store`.
    Limits: 250000 Unicode characters, 5000 blocks, 2000000 JSON body bytes.
    """
    blocks = ([block.model_dump() for block in body.blocks] if body.blocks is not None
              else [{"id": "text", "text": body.text}])
    return ExtractionResponse(occurrences=citationextract.extract(blocks, con))
