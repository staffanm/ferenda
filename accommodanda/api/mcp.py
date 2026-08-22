"""The public MCP (Model Context Protocol) server over the corpus -- the same
read-only view the REST API exposes (api/app.py), reshaped as MCP *tools* so any
MCP-capable AI host (Claude, ChatGPT, …) can ground answers about Swedish (and
EU) law in the live corpus and its citation graph, and cite the exact §/article.

It is mounted into the one `lagen serve` FastAPI process at **/mcp** over the
Streamable HTTP transport, so it ships wherever the site ships -- no second
service, no port, no auth (it is public, read-only data, like the REST API and
the site). `mount(app)` adds the endpoint; `lifespan(app)` runs the transport's
session manager and must be wired into the FastAPI app that mounts it.

Every tool reads the same three rebuildable backends as the REST service: the
SQLite **catalog** (metadata + the citation graph), **OpenSearch** (full-text;
only `search` needs it, and a down cluster is a visible tool error), and the
**artifact JSON** on disk (a document's full parsed body). The tools answer
through `api/reads.py` -- the same functions the REST endpoints call -- so a
corpus fact reaches MCP and REST through one code path.
"""

import contextlib
import itertools
import json
import logging
import time
from collections.abc import Mapping
from typing import Annotated, Literal, TypedDict

from mcp.server import MCPServer
from mcp.server.caching import CacheableMethod, CacheHint
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import ConfigDict, Field

from .. import config
from ..lib import layout, mdtext, pins, text
from ..lib.search import SearchIndex
from . import analytics, db, reads

# the ceiling on a single document body, shared by `get_document`'s clamp and
# `fetch`, which deliberately reads at it -- one number so raising it can't
# leave the other reader on the old bound.
MAX_CHARS = 200_000

log = logging.getLogger(__name__)

# Shown to the AI host so it knows when to reach for these tools, what the ids
# look like, and the order to call them in. Read once by the host at connect.
INSTRUCTIONS = """\
lagen.nu -- den svenska rättskällesamlingen: lagar och förordningar (sfs),
domstolsavgöranden (dv), Europadomstolens praxis (hudoc), förarbeten
(forarbete), myndighetsföreskrifter (foreskrift), EU-rätt (eurlex),
Europarådets konventioner (coe), JO-, JK- och ARN-avgöranden (avg),
myndigheters ställningstaganden (rs), folkrättsliga källor (icj, icc, icrc,
untc) och redaktionell kommentar (kommentar, begrepp) -- med
hänvisningsgrafen mellan dem.

Använd verktygen när användaren frågar vad lagen säger, vad som gäller
rättsligt, hur en bestämmelse ska tolkas, eller efterfrågar lagrum, rättsfall,
förarbeten, myndighetsbeslut eller källhänvisningar. Använd dem även när
lagen.nu inte nämns uttryckligen, och även när en fråga om svensk rätt,
EU-rätt eller Europakonventionen ställs på engelska. Föredra verktygen framför
allmän webbsökning när uppgiften är att identifiera, hämta eller hänvisa till
en rättskälla: lagar ändras, och samlingen bär den gällande lydelsen.

Dokument identifieras med sin publika lagen.nu-URI, t.ex.
`https://lagen.nu/1962:700` (brottsbalken). Ett `#`-fragment pekar ut en
enskild bestämmelse: `#K3P1` är 3 kap. 1 §, `#P6` är 6 §, en EU-artikel är
`#32`. Fragmentet är det som gör en hänvisning exakt.

Normalt arbetssätt för att belägga en rättsfråga:
 1. Gör om varje lag eller rättsfall till en URI: `resolve_citation` när
    användaren har namngett den ("utlänningslagen", "avtalslagen 36 §",
    "GDPR art 32"), annars `search` för att hitta den utifrån ämne. Gissa
    aldrig en URI.
 2. `get_document(uri, pinpoint=...)` för bestämmelsens gällande lydelse.
 3. `get_incoming_citations(uri + '#' + pinpoint)` för den praxis och de
    beslut som tillämpar bestämmelsen; `get_outgoing_citations` för vad
    dokumentet självt stöder sig på. Att gå längs grafen är själva poängen --
    det är vad en vanlig webbsökning inte kan.
 4. Hänvisa till fragmentet (t.ex. `#K5P8`), aldrig bara till lagen.

Använd exakta URI:er, id:n och fragment som verktygen har returnerat; ändra
eller konstruera dem inte själv. Utelämna valfria filter när de inte behövs --
ett felaktigt `source`- eller `kind`-filter döljer relevanta källor.

Allt är läsning av offentliga data; inget verktyg ändrar något.\
"""


@contextlib.contextmanager
def _root_logging_preserved():
    """Undo any reconfiguration of the *root* logger done inside the block.

    MCPServer's constructor calls logging.basicConfig() -- a library claiming the
    root logger, which belongs to whoever owns the process. Since `mcp` is built
    at module scope (the @mcp.tool decorators below need it), merely importing
    this module would otherwise install the SDK's handler at INFO on every
    process that reaches api/app.py -- including the `lagen` CLI, where it made
    opensearch-py narrate every bulk round-trip into the build output. Snapshot
    and restore, so importing us configures nothing: the app decides (uvicorn's
    own config when serving; app.py's basicConfig under __main__).
    """
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)


# `tools/list` and `server/discover` are the two cacheable methods a host calls
# on every connect. Ours answer from a tool table fixed at import and public
# read-only data, so a client may hold them for an hour and share them across
# authorization contexts -- the corpus grows nightly, but the *tool surface* and
# the instructions only change when this file is deployed. (SEP-2549; the hints
# ride along as ttlMs/cacheScope at 2026-07-28 and are ignored by older clients.)
CACHE_HINTS: Mapping[CacheableMethod, CacheHint] = {
    "tools/list": CacheHint(ttl_ms=3_600_000, scope="public"),
    "server/discover": CacheHint(ttl_ms=3_600_000, scope="public")}

with _root_logging_preserved():
    mcp = MCPServer("lagen.nu", instructions=INSTRUCTIONS,
                    website_url="https://lagen.nu/", cache_hints=CACHE_HINTS)

# one search client for the process; constructing it opens no connection, so
# importing/mounting never needs a running OpenSearch -- only a `search` call
# does. Tests swap this out.
_index = SearchIndex()


@contextlib.contextmanager
def _con():
    """`db.connection()` for a tool call: the same read-only catalog handle the
    REST endpoints take, with the unbuilt catalog reported as a plain
    RuntimeError (the SDK turns that into the tool's error result -- there is no
    HTTP status to raise here)."""
    if not db.catalog_ready():
        raise RuntimeError(db.NOT_BUILT)
    with db.connection() as con:
        yield con


# the corpus sources -- a closed set, so a strict enum: the schema teaches the
# host the vocabulary and it can't pass a value that matches nothing. `kind`, by
# contrast, is source-specific and open-ended (an FS code per agency, an eurlex
# doctype, …), so it stays a guided free string -- a strict enum there would
# reject valid kinds the host sees in results.
Source = Literal["sfs", "dv", "hudoc", "forarbete", "foreskrift", "eurlex",
                 "coe", "avg", "rs", "guidance", "kommentar", "begrepp",
                 "icc", "icj", "icrc", "untc"]
SourceArg = Annotated[Source | None, Field(
    description="Begränsar till en del av källsamlingen. Utelämna när flera "
    "slags rättskällor kan vara relevanta -- ett felaktigt filter döljer "
    "relevanta källor. Värden: sfs (svenska lagar och förordningar), dv "
    "(svenska domstolsavgöranden), forarbete (propositioner, SOU, Ds, "
    "kommittédirektiv m.m.), foreskrift (myndigheters författningssamlingar), "
    "eurlex (EU-rättsakter och EU-domstolens avgöranden), hudoc "
    "(Europadomstolen), coe (Europarådets konventioner och protokoll), avg "
    "(JO, JK, ARN, IMY, KKV), rs (myndigheters rättsliga ställningstaganden), "
    "edpb (Europeiska dataskyddsstyrelsen), icj (Internationella domstolen), "
    "icc (Internationella brottmålsdomstolen), icrc (humanitärrättsliga "
    "traktater), untc (FN:s traktatsamling), kommentar (juridiska "
    "kommentarer), begrepp (begreppsbeskrivningar).")]
KindArg = Annotated[str | None, Field(
    description="Begränsar till en dokumenttyp inom källan. Typerna är "
    "källspecifika: lag/forordning (sfs), case (dv), "
    "prop/bet/rskr/sou/dir/so/lr/ds/pm/skr (forarbete), "
    "regulation/directive/judgment/opinion/decision (eurlex), "
    "judgment/decision (hudoc), treaty/protocol (coe), en "
    "författningssamlingskod som fffs eller nfs (foreskrift), "
    "jo/kkv/jk/arn/imy (avg), en myndighetskod som skv, fk eller migr (rs), "
    "riktlinjer/rekommendationer/wp (edpb), dom/beslut (icj), "
    "kommentar, begrepp. Utelämna om dokumenttypen inte är känd -- den står "
    "som `kind` på varje träff, vilket är det säkra sättet att få den rätt.")]

QueryArg = Annotated[str, Field(
    description="Den juridiska hänvisningen, det rättsliga begreppet eller den "
    "sakfråga som ska sökas: en exakt hänvisning ('dataskyddslagen 1 kap. 7 §', "
    "'GDPR Article 85') eller en kort beskrivning i naturligt språk "
    "('arbetsgivares rätt att läsa anställdas e-post', 'preskription av "
    "konsumentfordran'). Håll frågan kort och saklig -- skicka inte hela "
    "konversationen eller instruktioner som inte hör till sökningen.")]
SearchLimitArg = Annotated[int, Field(
    description="Högsta antal träffar, 1-50. Lågt för en avgränsad fråga, "
    "högre när rättsläget behöver undersökas bredare.")]
CitationArg = Annotated[str, Field(
    description="Hänvisningen som ska slås upp, skriven som i juridisk text "
    "eller som användaren formulerade den: '1 kap. 7 § dataskyddslagen', "
    "'YGL 1:4', 'GDPR art. 85', 'artikel 10 Europakonventionen', "
    "'NJA 2020 s. 3', 'C-199/24'. Ange en hänvisning, inte en allmän "
    "rättsfråga -- för den, använd `search`.")]
DocUriArg = Annotated[str, Field(
    description="Dokumentets fullständiga lagen.nu-URI, t.ex. "
    "'https://lagen.nu/1962:700' eller 'https://lagen.nu/ext/celex/62024CJ0199'. "
    "Hämta den från `search`, `resolve_citation` eller ett tidigare "
    "verktygsresultat; ange inte en sökfråga här.")]
PinpointArg = Annotated[str | None, Field(
    description="Pekar ut en enskild bestämmelse: 'K3P1' för 3 kap. 1 §, 'P6' "
    "för 6 §, ett artikel-id för en EU-rättsakt, eller ett fragment/ankare som "
    "`search`, `resolve_citation` eller hänvisningsverktygen har returnerat. "
    "Utelämna för hela dokumentet.")]
MaxCharsArg = Annotated[int, Field(
    description="Högsta antal tecken av dokumenttexten, tak 200000. Lägre när "
    "en översikt räcker. För mycket långa dokument: begär hellre en exakt "
    "`pinpoint` än en större text.")]
FormatArg = Annotated[Literal["md", "json"], Field(
    description="Textens format. 'md' (förvalt) ger läsbar markdown: rubriker, "
    "paragrafbeteckningar, listor och tabeller, med varje hänvisning som en "
    "[text](uri)-länk. 'json' ger den råa artefakt-JSON:en -- trädet av typade "
    "noder med texten som inline-runs -- för strukturell bearbetning.")]
FetchIdArg = Annotated[str, Field(
    description="Det exakta `id` som `search` returnerade, t.ex. "
    "'https://lagen.nu/1962:700' eller 'https://lagen.nu/1962:700#K3P1'. "
    "Ändra det inte och konstruera inte ett eget fragment.")]
CitedUriArg = Annotated[str, Field(
    description="URI:n vars hänvisningar ska hämtas. En fragment-URI "
    "('https://lagen.nu/2018:218#K1P7') ställer den skarpare frågan om hur en "
    "viss paragraf har tillämpats; en URI utan fragment svarar för hela "
    "dokumentet.")]
ScopeArg = Annotated[Literal["tree", "exact"], Field(
    description="'tree' (förvalt) tar med hänvisningar till URI:n och alla "
    "bestämmelser under den -- för en stor lag tiotusentals rader. 'exact' tar "
    "bara med dem som namnger URI:n själv.")]
CitationSortArg = Annotated[Literal["rail", "citations"], Field(
    description="Ordningen på raderna. 'rail' (förvalt) är den lagen.nu självt "
    "använder: rättspraxis först för en författning, sedan "
    "myndighetsavgöranden, sedan resten av grafen. 'citations' lägger de mest "
    "citerade källorna först -- svaret på 'vilka är de viktigaste rättsfallen "
    "om den här bestämmelsen'. Kombinera 'citations' med source='dv' när "
    "frågan gäller praxis.")]
PageLimitArg = Annotated[int, Field(
    description="Högsta antal rader på sidan, 1-1000. Bläddra vidare med "
    "`offset`.")]
ListLimitArg = Annotated[int, Field(
    description="Högsta antal dokument på sidan, 1-500.")]
OffsetArg = Annotated[int, Field(
    description="Hur många rader från början av den stabila ordningen som ska "
    "hoppas över. 0 för första sidan; öka med föregående `limit` för nästa.")]
IncludeExpiredArg = Annotated[bool, Field(
    description="Ta med upphävda dokument -- upphävda författningar, "
    "EU-rättsakter som inte längre gäller, återkallade ställningstaganden. "
    "Standard är att de utelämnas, så listan visar gällande rätt. Sätt true "
    "bara när frågan uttryckligen gäller äldre eller upphävd rätt.")]


# every tool is a pure read of public data: readOnlyHint lets a host auto-run them
# without a per-call approval prompt (so the multi-step grounding flow isn't
# interrupted); openWorldHint marks results as drawn from a large external corpus,
# not a fixed enumerable set.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)


# --------------------------------------------------------------------------
# the search/fetch result shapes
#
# `search` and `fetch` are the two tools OpenAI's hosts expect a knowledge
# server to expose, with a fixed result shape: search returns `{results: [{id,
# title, url}]}` and fetch returns `{id, title, text, url, metadata}`, both as
# `structuredContent` (which the SDK only emits for a tool whose return type it
# can build a schema from -- a bare `-> dict` yields none). Declaring these
# shapes is the whole of the adaptation: their required fields are a *subset* of
# what the corpus already answers with, so the contract is met by naming the
# fields rather than by narrowing any tool. Everything the contract doesn't
# mention -- fragments, inbound_count, source/kind, the citation-graph tools --
# stays exactly as it is for every other host.
#
# The hit allows extra keys so lib/search.py can grow fields without them being
# silently dropped from structuredContent. The envelope can't: the SDK builds
# the top-level model itself and drops that config, so every key `search`
# returns is declared here.
# --------------------------------------------------------------------------

class SearchHit(TypedDict):
    """One search result. `id` is what `fetch` takes -- the *most precise*
    target for this hit, so a paragraph-deep match ids the fragment
    (`https://lagen.nu/1962:700#K3P1`), not the whole statute."""

    # pydantic's documented way to configure a TypedDict-derived model; PEP 589
    # allows only annotated declarations in the body, hence the ignore. Verified
    # to reach the wire as `additionalProperties: true` on this hit's schema.
    __pydantic_config__ = ConfigDict(extra="allow")  # ty: ignore[invalid-typed-dict-statement]

    id: str
    title: str
    url: str


class SearchResults(TypedDict):
    """`results` is the contract; `query`/`total` are ours, and are declared so
    they survive into structuredContent."""

    results: list[SearchHit]
    query: str
    total: int


class Document(TypedDict):
    """`get_document`'s reply.

    Declared, rather than left as a bare `-> dict`, because the SDK builds a
    tool's `outputSchema` from its return annotation and emits
    `structuredContent` only when it has one. A bare dict yields neither, so a
    client that reads results structurally -- the read the spec points at -- had
    nothing to read for this tool and had to fall back to parsing the text
    block."""

    uri: str
    source: str | None
    kind: str | None
    label: str | None
    title: str | None
    source_url: str | None
    inbound_count: int
    pinpoint: str | None
    format: Literal["md", "json"]
    truncated: bool
    text: str


class DocumentList(TypedDict):
    """`list_documents`' page, declared for the reason in `Document`."""

    total: int
    limit: int
    offset: int
    documents: list[dict]


class IncomingCitations(TypedDict):
    """`get_incoming_citations`' page, declared for the reason in `Document`.
    `total` counts the rows `source` left, while `by_source` covers the whole
    scope before that filter -- so the reply still says what the other corpora
    hold."""

    uri: str
    scope: str
    source: str | None
    sort: str
    total: int
    by_source: dict[str, int]
    limit: int
    offset: int
    citations: list[dict]


class ResolvedCitations(TypedDict):
    """`resolve_citation`'s hits, wrapped.

    Wrapped, and not returned as a bare list, because the SDK renders a list
    return as one content block *per element*. A client reading `content[0]` --
    which the spec permits, since `content` is the human-readable rendering --
    then gets element 0 of N with no error raised anywhere. That is
    size-dependent truncation: this tool usually resolves exactly one citation,
    so such a client works perfectly until the day two hits come back, while
    `get_outgoing_citations` silently hands it 1 of 55 from the start. A single
    block cannot be half-read.

    `results` rather than `citations`: these rows are the same shape `search`
    returns under that name, so a client can reuse the same handler."""

    results: list[dict]


class OutgoingCitations(TypedDict):
    """`get_outgoing_citations`' rows, wrapped for the reason in
    `ResolvedCitations`. `citations` matches `get_incoming_citations`, which
    already answers with a dict under that key, so the pair reads alike."""

    citations: list[dict]


class SourceList(TypedDict):
    """`list_sources`' rows, wrapped for the reason in `ResolvedCitations` --
    this one emitted 16 content blocks, one per source."""

    sources: list[dict]


class FetchedDocument(TypedDict):
    """A document (or one provision of it) in the fetch contract's shape.
    `metadata` is the free-form slot, and is where the corpus facts the contract
    has no field for -- source, kind, publisher page, citation count -- ride."""

    id: str
    # `str | None`, not `str`: the catalog's title column is nullable, so a
    # document can genuinely have none. Declaring it required-and-present made
    # the schema promise something the data does not
    title: str | None
    text: str
    url: str
    metadata: dict[str, str | int | bool | None]


def _hit_id(hit):
    """A search hit's `fetch` id: the resolved provision's URI when the query
    named one, else the document URI. Both are already-valid ids, since a
    fragment URI is just the document URI plus its `#`-pinpoint.

    The `pin` is the only fragment that is the *answer*. A full-text hit's
    `fragments` are places inside a document where the words stand, which is not
    the same claim -- iding the first of them made "dataförordningen" fetch
    article 47 of the EU Data Act, the one article that quotes the act's title.

    Indexes `pin` rather than `.get`-ing it: both producers always set the key
    (`search.parse_hit`, `pins.resolved_results` -- None for a document-level
    match), so a missing one means a hit shape changed under us. Raising then
    beats defaulting, which would silently collapse every id to the document URI
    and have hosts fetch whole statutes instead of the provision.
    """
    return hit["pin"]["uri"] if hit["pin"] else hit["uri"]


def _page_url(uri, pinpoint=None):
    """The absolute public URL of a document's page, `#pinpoint` appended.

    Absolute, unlike the root-relative path `layout.page_url` mints for the
    site's own pages: an MCP result is read by a client on another origin, and a
    relative path there is not merely unhelpful but wrong -- ChatGPT resolved
    `/1915:218` against `https://chatgpt.com` and rendered its citations pointing
    at that host. Every `url` the tools below emit goes through here or
    `_absolute_page_urls`."""
    return (config.PUBLIC_BASE_URL + layout.page_url(uri)
            + ("#" + pinpoint.lstrip("#") if pinpoint else ""))


def _absolute_page_urls(hits):
    """`hits` with each row's relative `url` replaced by its absolute form.

    The two producers of this row shape (`search.parse_hit`,
    `pins.resolved_results`) set `url` for the site's own pages, so the MCP layer
    rewrites rather than adds. Indexed, not `.get`-ed: a row without the key
    means the shape changed under us, and silently emitting no link is worse than
    failing here (rule:fail-fast)."""
    return [{**hit, "url": config.PUBLIC_BASE_URL + hit["url"]} for hit in hits]


# --------------------------------------------------------------------------
# tools
# --------------------------------------------------------------------------

@mcp.tool(title="Sök i den svenska rättskällesamlingen", annotations=READ_ONLY)
def search(query: QueryArg, source: SourceArg = None, kind: KindArg = None,
           limit: SearchLimitArg = 10) -> SearchResults:
    """Standardverktyget för juridisk informationssökning. Använd det när
    användaren ställer en rättslig fråga eller efterfrågar rättskällor -- "vad
    gäller?", "vad säger lagen?", "får man ...?", "vilken bestämmelse gäller?",
    "finns det rättsfall om detta?" -- även när lagen.nu inte nämns.

    Fulltextsökning i hela samlingen, rangordnad på både relevans och hur ofta
    ett dokument har citerats. Sökningen når enskilda paragrafer och artiklar:
    varje träff bär dokumentets eget utdrag och därtill de `fragments` med
    markerad text som visar var i dokumentet orden står.

    Verktyget känner också igen juridiska hänvisningar och vedertagna namn: är
    frågan "avtalslagen 36 §", "BrB 12:1", "GDPR artikel 32", "NJA 2015 s. 899"
    eller ett etablerat rättsfallsnamn, löses den exakta källan ut och läggs
    först i resultatet -- vilket ren fulltext inte kan, eftersom namnet oftast
    inte står i texten.

    Hämta sedan texten med `get_document` (eller `fetch`, när ett exakt `id`
    från ett sökresultat redan finns). Använd inte `list_documents` för
    fulltextsökning -- det är ett index, inte en sökning.

    Varje träff: id (skickas till `fetch`; pekar ut den utlösta bestämmelsen när
    frågan var en hänvisning, annars dokumentet), uri (dokumentets identitet),
    url (dess publika webbadress), identifier, title, source, kind,
    inbound_count (hur ofta dokumentet citeras), pin (den utlösta bestämmelsen,
    annars null) och fragments (de matchande styckena i dokumentet).
    """
    limit = max(1, min(limit, 50))
    # a down cluster raises reads.SearchUnavailable, which the SDK returns as
    # the tool's error result -- a visible failure, never a silently smaller
    # answer (the old degrade-to-citation-only read as "nothing else exists")
    res = reads.search(_index, query, source=source, kind=kind, limit=limit)
    return SearchResults(query=query, total=res["total"],
                         results=[{**r, "id": _hit_id(r)}
                                  for r in _absolute_page_urls(res["results"])])


@mcp.tool(title="Slå upp en juridisk hänvisning", annotations=READ_ONLY)
def resolve_citation(citation: CitationArg) -> ResolvedCitations:
    """Gör om en känd juridisk hänvisning -- en lagförkortning, ett vedertaget
    lagnamn, ett rättsfallsnamn -- till exakt URI, och när hänvisningen pekar ut
    en paragraf eller artikel även till det exakta fragmentet. Det pålitliga
    sättet att gå från "det användaren skrev" till en citerbar länk, utan att
    gissa en URI.

    Klarar t.ex. "avtalslagen 36 §", "BrB 3:1", "dataskyddslagen 1 kap. 7 §",
    "GDPR artikel 85", "artikel 10 Europakonventionen", "NJA 2015 s. 899",
    "C-199/24" och etablerade rättsfallsnamn som "Instagrambilden".

    Använd det när källan redan är namngiven. Gäller frågan ett bredare ämne och
    rätt bestämmelse ännu inte är känd, använd `search` i stället.

    Svaret är en `results`-lista (oftast en post, tom när inget löses ut) med
    samma radform som `search` ger: id, uri, url, identifier, title, source,
    kind, inbound_count, pin och fragments. Avsåg hänvisningen en paragraf eller
    artikel är `pin.uri` den utpekade delen, och `id` -- det `fetch` tar -- är
    samma utpekade URI.
    """
    with _con() as con:
        return ResolvedCitations(results=[
            {**r, "id": _hit_id(r)}
            for r in _absolute_page_urls(pins.resolved_results(con, citation))])


@mcp.tool(title="Hämta ett dokuments text och metadata", annotations=READ_ONLY)
def get_document(uri: DocUriArg, pinpoint: PinpointArg = None,
                 max_chars: MaxCharsArg = 20000,
                 format: FormatArg = "md") -> Document:
    """Hämtar metadata och fullständig text för ett känt dokument, eller för
    en bestämd del av det.

    Det normala hämtningssteget efter `search` eller `resolve_citation`, när
    URI:n är känd. Ange `pinpoint` när bara en viss paragraf eller artikel
    behövs -- det är billigare och precisare, och håller irrelevant text borta.
    Pinpoints kommer från `search`-fragment, `resolve_citation` eller
    `anchor`-fältet på hänvisningsverktygens träffar.

    Texten är markdown (förvalt): rubriker, paragrafbeteckningar, listor och
    tabeller, med varje hänvisning i texten som en [text](uri)-länk vars URI
    kan skickas vidare till verktygen. Ange `format='json'` för dokumentets
    råa artefakt-JSON i stället, när svaret ska bearbetas strukturellt.

    Lång markdown kapas vid `max_chars` (högst 200 000 tecken). Är svaret märkt
    `truncated: true` har bara början hämtats -- hänvisa då inte till text som
    inte returnerats, utan begär en mer exakt `pinpoint`. JSON kapas aldrig:
    ryms inte trädet inom `max_chars` blir det ett fel som säger vad som
    behövs i stället.

    Svaret: uri, source, kind, label, title, source_url (utgivarens egen sida),
    inbound_count (hur ofta dokumentet citeras), den efterfrågade `pinpoint`,
    `format` och `text`.
    """
    max_chars = max(1, min(max_chars, MAX_CHARS))
    with _con() as con:
        data = reads.document(con, uri)
    if data is None:
        raise ValueError("no document %r in the catalog" % uri)
    art = data.pop("artifact")
    if pinpoint:
        node = text.fragment_node(art, pinpoint.lstrip("#"))
        if node is None:
            raise ValueError("no section %r in %s -- check the pinpoint against a "
                             "search fragment or a citation anchor"
                             % (pinpoint, uri))
        body = (json.dumps(node, ensure_ascii=False) if format == "json"
                else mdtext.node_markdown(node))
    else:
        body = (json.dumps(art, ensure_ascii=False) if format == "json"
                else mdtext.document_markdown(
                    art, title=data["title"] or data["label"]))
    # a JSON body is never truncated: a markdown prefix is readable, a JSON
    # prefix is unparseable -- worse than no answer for the structural
    # processing the format exists for (rule:fail-fast)
    if format == "json" and len(body) > max_chars:
        raise ValueError(
            "the artifact JSON is %d chars, over max_chars=%d -- raise "
            "max_chars (ceiling %d), request a pinpoint, or use format='md'"
            % (len(body), max_chars, MAX_CHARS))
    # named rather than splatted from `data`: the keys are the tool's declared
    # output schema, so spelling them here is what makes a change to
    # `reads.document` a type error instead of a silently altered contract
    return Document(
        uri=data["uri"], source=data["source"], kind=data["kind"],
        label=data["label"], title=data["title"],
        source_url=data["source_url"], inbound_count=data["inbound_count"],
        pinpoint=pinpoint, format=format, truncated=len(body) > max_chars,
        text=body[:max_chars])


@mcp.tool(title="Hämta ett dokument via sökresultatets id", annotations=READ_ONLY)
def fetch(id: FetchIdArg) -> FetchedDocument:
    """Hämtar texten bakom ett `id` som `search` har returnerat.

    Bär `id` ett fragment efter `#` -- `https://lagen.nu/1962:700#K3P1`, som är
    hur `search` id-märker en paragrafdjup träff -- hämtas just den
    bestämmelsen; utan fragment hämtas hela dokumentet.

    Motsvarar `get_document`, men tar ett sammansatt sökresultat-id i stället
    för URI och pinpoint var för sig. Använd `get_document` när du redan har
    båda separat, eller när textens längd behöver styras.

    Ändra inte identifieraren och konstruera inte ett eget fragment när ett
    exakt `id` redan finns i sökresultatet.

    Svaret: id, title, url (absolut publik adress), text (markdown, som
    `get_document` ger) och `metadata` med
    source, kind, label, utgivarens sida, inbound_count, den lästa `pinpoint`
    (null för ett helt dokument) och `truncated`. Texten kapas vid 200 000
    tecken; är `metadata.truncated` true har du ett prefix, inte hela
    bestämmelsen -- hämta då ett `#`-utpekat id i stället.
    """
    # the contract asks for the *complete* content, so take get_document's
    # ceiling rather than its (deliberately modest) interactive default
    uri, _, pinpoint = id.partition("#")
    doc = get_document(uri, pinpoint or None, max_chars=MAX_CHARS)
    return FetchedDocument(
        id=id, title=doc["title"], text=doc["text"],
        url=_page_url(doc["uri"], pinpoint),
        metadata={"source": doc["source"], "kind": doc["kind"],
                  "label": doc["label"], "source_url": doc["source_url"],
                  "pinpoint": doc["pinpoint"], "truncated": doc["truncated"],
                  "inbound_count": doc["inbound_count"]})


@mcp.tool(title="Lista dokument i samlingen", annotations=READ_ONLY)
def list_documents(source: SourceArg = None, kind: KindArg = None,
                   limit: ListLimitArg = 50,
                   offset: OffsetArg = 0,
                   include_expired: IncludeExpiredArg = False) -> DocumentList:
    """Bläddrar i eller inventerar vad samlingen innehåller: dokument med
    grundläggande metadata, filtrerbart på källa och dokumenttyp, sidindelat.

    Detta är ett **index**, inte en fulltextsökning. Ställer användaren en
    sakfråga, söker ett rättsligt begrepp eller vill hitta dokument som
    innehåller viss text -- använd `search`. Använd det här när frågan är vilka
    dokument från en viss myndighet, författningssamling eller kategori som
    finns. Hämta sedan ett enskilt dokument med `get_document`.

    Ordningen är stabil (efter URI), och `total` är antalet träffar före
    sidindelning, så hela mängden kan bläddras igenom med `limit`/`offset`.

    Upphävda dokument utelämnas, så listan visar gällande rätt. De finns kvar
    och går att hämta med `get_document` och att nå via hänvisningsgrafen;
    `include_expired=true` tar med dem i listan.

    Varje post: uri, source, kind, label, title, source_url (utgivarens sida
    där den är känd) och updated (när dokumentet senast bearbetades, ISO 8601).
    """
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    with _con() as con:
        return reads.documents(con, source=source, kind=kind,
                               limit=limit, offset=offset,
                               include_expired=include_expired)


@mcp.tool(title="Vilka källor hänvisar hit (inkommande hänvisningar)",
          annotations=READ_ONLY)
def get_incoming_citations(uri: CitedUriArg, limit: PageLimitArg = 50,
                           offset: OffsetArg = 0,
                           source: SourceArg = None,
                           scope: ScopeArg = "tree",
                           sort: CitationSortArg = "rail") -> IncomingCitations:
    """Vilka rättsfall, myndighetsbeslut, förarbeten och andra källor som
    hänvisar till, tillämpar eller diskuterar ett dokument eller en bestämmelse
    -- hänvisningsgrafen inåt, och lagen.nu:s signaturfunktion som data.
    Besvarar "vilka domar tillämpar den här paragrafen", "vad hänvisar till det
    här avgörandet".

    Ange en fragment-URI (`...#K1P7`) när frågan gäller hur en viss paragraf
    har tillämpats -- det är den skarpare frågan. En URI utan fragment svarar
    för lagen **och varje bestämmelse i den** (`scope="tree"`, förvalt), vilket
    för en stor lag är tiotusentals rader: läs `total` och `by_source` för att
    se formen, och begränsa sedan med en exakt bestämmelse eller med `source`
    i stället för att bläddra igenom alltihop. `scope="exact"` är den smala
    frågan: bara hänvisningar som namnger `uri` själv.

    Ordningen är den lagen.nu självt använder: för en författning kommer
    rättspraxis först, sedan myndighetsavgöranden, sedan resten av grafen --
    så de första raderna är de en jurist läser först.

    **Vilka av dem väger tyngst?** Varje rad bär `inbound_count` -- hur många
    dokument som citerar det *citerande* dokumentet. Det är källans egen
    auktoritetssignal, så svaret går att rangordna utan ett anrop per rad.
    `sort="citations"` ordnar hela scopet efter den, störst först.

    **Sätt source="dv" till när frågan gäller praxis.** En lag eller en
    proposition citeras en tiopotens oftare än något avgörande, så
    `sort="citations"` utan källfilter svarar med författningar och förarbeten
    och når aldrig fram till ett rättsfall: på avtalslagen 36 § är de tre
    översta SFS 1994:1512 (955), Prop. 2007/08:95 (946) och Prop. 2004/05:85
    (681), medan det mest citerade *avgörandet* om paragrafen -- NJA 1987
    s. 394, Den kollektiva hemförsäkringen -- har 32. Med source="dv" ligger
    det avgörandet överst. Svarar du på "vilka är de viktigaste rättsfallen"
    utan filtret rapporterar du propositioner som rättsfall.

    Läs siffran som en ledtråd, inte som ett facit: den mäter hur ofta något
    citeras, vilket samvarierar med auktoritet men inte är samma sak. Den
    gynnar ett gammalt avgörande framför ett färskt, och den kan bara räkna det
    som finns i den här samlingen. Ett färskt prejudikat kan väga tungt med
    låg siffra.

    `sort="citations"` är den enda ordning där `offset` *inte* är stabil mellan
    ombyggnader -- siffran räknas om vid varje bygge, så en rad kan flytta sig
    mellan sidor när samlingen växer. Ta första sidan, eller bläddra klart i
    ett svep.

    Svaret: uri; scope, source och sort (filtren och ordningen, återgivna);
    total (rader att bläddra, alltså *efter* `source`); by_source (antal per
    källa över hela scopet, alltså *före* `source` -- så svaret ändå säger vad
    de andra samlingarna bär); limit; offset; och citations -- var och en med
    uri (det citerande dokumentet), target (den citerade bestämmelsen), anchor
    och page (var i citeraren den står), label, title, source, kind, date och
    inbound_count.
    """
    limit, offset = max(1, min(limit, 1000)), max(0, offset)
    with _con() as con:
        return reads.inbound_citations(con, uri, scope=scope, source=source,
                                       sort=sort, limit=limit, offset=offset)


@mcp.tool(title="Vad dokumentet hänvisar till (utgående hänvisningar)",
          annotations=READ_ONLY)
def get_outgoing_citations(uri: DocUriArg) -> OutgoingCitations:
    """Varje hänvisning ett dokument gör -- hänvisningsgrafen utåt. Användbart
    för att se vilka lagrum, rättsfall, förarbeten och andra källor som åberopas
    i en dom, ett myndighetsbeslut, ett förarbete eller en kommentar. Ange
    dokumentets grund-URI, utan fragment.

    Varje post i `citations`: uri (den citerade källan, med `#`-fragment när
    hänvisningen är paragrafdjup), anchor (var i det citerande dokumentet den
    står), predicate (relationen, t.ex. dcterms:references), text (hur
    hänvisningen lyder i källan), label, title och source för målet, samt
    `hosted` -- false när målet ännu inte finns i samlingen, och då saknas
    label och title.
    """
    with _con() as con:
        return OutgoingCitations(citations=reads.outbound(con, uri))


@mcp.tool(title="Lista källorna och deras storlek", annotations=READ_ONLY)
def list_sources() -> SourceList:
    """Vilka källor samlingen har och hur många dokument var och en bär --
    orientering, och sättet att välja ett riktigt `source`-värde till `search`,
    `list_documents` eller `get_incoming_citations`.

    Använd det inte som första steg i en vanlig rättsfråga; börja då med
    `search`. Varje post i `sources`: source och documents.
    """
    with _con() as con:
        return SourceList(sources=reads.sources(con))


# --------------------------------------------------------------------------
# mounting into the FastAPI app (see api/app.py)
# --------------------------------------------------------------------------

# built once at import: creates the Streamable HTTP ASGI app and, lazily, the
# session manager `lifespan` runs. Serving at "/" internally so a mount at
# "/mcp/" lands the endpoint on exactly /mcp/ (see mount()).
#
# One endpoint serves both protocol eras off these settings. A 2026-07-28 client
# sends a self-contained POST -- no initialize handshake, no Mcp-Session-Id, its
# protocol version and capabilities riding in `params._meta` -- and the SDK
# routes it to the single-exchange handler. A 2025-era client still handshakes;
# `stateless_http` gives it a fresh transport per request rather than a session
# pinned to this process, so either way no request needs sticky routing.
#
# DNS-rebinding protection guards localhost-bound servers from hostile web
# pages; this server is public, unauthenticated and read-only, served behind
# nginx which already routes by vhost. Left on (the SDK default), it would
# 421 every request whose Host isn't localhost -- i.e. all production traffic.
_http_app = mcp.streamable_http_app(
    streamable_http_path="/", json_response=True, stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False))


@contextlib.asynccontextmanager
async def lifespan(app):
    """Run the Streamable HTTP session manager for the lifetime of the host app.
    Wire this as the FastAPI app's `lifespan` (it is a no-op for the in-process
    TestClient path used during `generate`, which never calls /mcp). Still
    required at 2026-07-28: the manager owns the task group every request --
    session or no session -- is dispatched from."""
    async with mcp.session_manager.run():
        yield


def _message(body):
    """The request body as a JSON-RPC message object, or None if it is not one
    (an empty GET body, a malformed POST). Parsed once per request and handed to
    both readers below."""
    try:
        msg = json.loads(body)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def _describe(msg, size):
    """One grep-friendly token run for a JSON-RPC request: the method, its
    envelope id, and for tools/call the tool name + its arguments (truncated --
    get_document can take a 200k max_chars but the *arguments* stay small; the cap
    only guards against a hostile oversized payload flooding the log).

    The `id` earns its place by making a client's own giving-up legible: a caller
    that stops waiting may say so with `notifications/cancelled`, whose params
    name the request being abandoned. Without both ids in the log, that notice
    could not be paired with the call it cancelled -- and a client-side timeout is
    otherwise invisible from in here (it is reported to the model, not to us, and
    the model tends to relay it as a server fault). So the cancellation also
    prints its `requestId` and `reason`."""
    if msg is None:
        return "<unparseable body, %d bytes>" % size
    method = msg.get("method", "<no method>")
    ident = "id=%s" % json.dumps(msg.get("id"))
    params = msg.get("params") or {}
    if method == "notifications/cancelled":
        return "%s %s cancels=%s reason=%s" % (
            method, ident, json.dumps(params.get("requestId")),
            json.dumps(params.get("reason"))[:200])
    if method != "tools/call":
        return "%s %s" % (method, ident)
    args = json.dumps(params.get("arguments", {}), ensure_ascii=False)
    return "%s %s %s %s" % (method, ident, params.get("name"), args[:500])


def _called(msg):
    """`(method, tool)` for a JSON-RPC request -- what analytics counts -- or
    None if the body carried no method to count. `tool` is None for every method
    but tools/call."""
    if msg is None or "method" not in msg:
        return None
    if msg["method"] != "tools/call":
        return msg["method"], None
    return msg["method"], msg.get("params", {}).get("name")


# How much of a response to hold on to while deciding whether it was an error.
# A JSON-RPC failure is a sentence ("no document ... in the catalog"), while the
# bodies that run past this are successful reads -- get_document alone returns up
# to MAX_CHARS. So a capture that overflows is a success by construction, and the
# cap is what keeps a 200k-character document from being copied to count it.
CAPTURE_MAX = 64 * 1024

# How large a request body this endpoint reads before refusing it. The
# middleware buffers the whole body to describe and replay it, so without a cap
# one caller decides how much memory the process spends. nginx already refuses
# more than this in production (`client_max_body_size`), but the app must not
# depend on nginx standing in front of it -- a direct uvicorn or another
# deployment gets the same limit. An MCP call is a few kilobytes of JSON.
BODY_MAX = 4 * 1024**2


def _failed(status, body, truncated):
    """Whether an MCP response carries an error.

    The status alone cannot say: the transport answers 200 and puts the failure
    *inside* the JSON-RPC envelope -- either a top-level `error` (bad method, bad
    params) or, for a tool that raised, a result flagged `isError`.

    Two responses carry no envelope to read, and neither is a failure: one
    `truncated` at CAPTURE_MAX (only a successful read grows that big -- an error
    is a sentence), and the empty body of the 202 that acknowledges a
    notification. Anything else unreadable is counted as failed rather than
    waved through: this runs *after* the response has gone out, where raising
    would leave the caller mid-stream, so the honest move is to record an
    envelope we cannot vouch for as the anomaly it is."""
    if status is None or status >= 400:
        return True
    if truncated or not body:
        return False
    try:
        msg = json.loads(body)
    except ValueError:
        return True
    if not isinstance(msg, dict):
        return True                    # not an envelope we can vouch for
    # `result` is whatever the peer sent -- `null` is legal JSON-RPC, and any
    # other non-object is malformed. Test the type rather than reaching into it:
    # this runs *past* the response, where an AttributeError does not become a
    # 500 but a second response.
    return "error" in msg or (isinstance(msg.get("result"), dict)
                              and msg["result"].get("isError") is True)


def _header(scope, name):
    """One request header from an ASGI scope, latin-1 decoded as the spec stores
    it, or "-" when absent. Truncated: a user-agent is caller-controlled."""
    want = name.encode("latin-1")
    for key, value in scope.get("headers", ()):
        if key == want:
            return value.decode("latin-1", "replace")[:200]
    return "-"


def _client_ip(scope):
    """The caller's address, not nginx's.

    `scope["client"]` is the socket peer, which behind the compose-network proxy
    is always nginx (172.19.x.x) -- so every MCP line logged the same address and
    could not tell an OpenAI host from a Claude one. The vhost sets
    X-Forwarded-For; its FIRST entry is the original client, the rest are proxies.
    It is caller-forgeable, so it identifies traffic, it does not authorise
    anything."""
    forwarded = _header(scope, "x-forwarded-for")
    if forwarded != "-":
        return forwarded.split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "-"


_request_ids = itertools.count(1)

# nginx's `proxy_read_timeout` for the vhost that fronts this app. It is not set
# in docker/nginx/ferenda.lagen.nu.conf, so nginx's own default applies -- keep
# this in step if that changes.
#
# Load-bearing for the log's honesty: the timeout fires while nginx reads the
# response, after which it answers the client 504 and drops the upstream
# connection. The app does not find out. It goes on to emit `http.response.start`
# with 200, so a `done status=200` line can describe a request the caller
# received a 504 for -- measured on 2026-08-17, when a resolve_citation logged as
# arriving at 12:28:20 and nginx logged "upstream timed out while reading
# response header" at 12:29:20, exactly 60 s later. Past this mark the status we
# report is what we *sent*, not what the caller *got*.
PROXY_READ_TIMEOUT = 60.0


class _LoggedMCP:
    """ASGI wrapper logging every MCP request twice -- once on arrival, once on
    completion -- and reporting tool calls to Matomo (api/analytics.py) when a
    tracker is configured.

    Two lines, not one, because a single arrival line cannot distinguish the three
    outcomes that matter when a client reports a failure we cannot see: the call
    answered, the call answered with a JSON-RPC error, or the app never produced a
    response at all. Both lines carry `mcp[<n>]`, so concurrent calls can be
    paired up (`grep 'mcp\\[7\\]'`).

    Every request is logged, whatever its method. Only POST carries a JSON-RPC
    body, but streamable HTTP also uses GET (the SSE stream) and DELETE (session
    teardown), and those used to pass through invisibly -- a blind spot exactly
    where a client-side transport failure would show.

    The status on the done line is what this app *sent*. Past
    PROXY_READ_TIMEOUT that is not what the caller *received* -- nginx has
    already answered it 504 and dropped the connection -- so the line says so
    outright rather than showing a bare `status=200` for a request that failed
    from the caller's side.

    The uvicorn/nginx access logs see only `POST /mcp/ 200`, so tool-level
    visibility has to come from here. A POST body is buffered to be parsed
    (single JSON-RPC messages, stateless_http -- small by construction) and
    replayed to the wrapped app. The response is watched up to CAPTURE_MAX
    because whether a tool call failed is only readable there (see `_failed`).
    Nothing is withheld from the caller: each message is passed on as it arrives,
    and the tracking hit is sent after the response has gone out."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = next(_request_ids)
        method = scope["method"]
        common = "mcp[%d] %s %s ip=%s ua=%s" % (
            # `path` is the full request path even under a Mount (`root_path`
            # carries the prefix separately, so joining them doubles it)
            rid, method, scope.get("path", "-"), _client_ip(scope),
            _header(scope, "user-agent"))

        called, described, replay = None, "", None
        if method == "POST":
            messages, size = [], 0
            while True:
                message = await receive()
                messages.append(message)
                size += len(message.get("body", b""))
                if size > BODY_MAX:
                    # the arrival/completion pair, kept: a request that answers
                    # here still logs two lines under the same mcp[<n>], which is
                    # what reading the log by request id depends on
                    log.info("%s start body over %d bytes", common, BODY_MAX)
                    log.info("%s done status=413 OVERSIZED-BODY", common)
                    # `close`: the caller may still be sending, and a reset it
                    # meets before reading the response turns a clean 413 into an
                    # opaque transport error at the one caller we want to inform
                    await send({"type": "http.response.start", "status": 413,
                                "headers": [(b"content-type",
                                             b"application/json; charset=utf-8"),
                                            (b"connection", b"close")]})
                    await send({"type": "http.response.body",
                                "body": b'{"error": "request body too large"}'})
                    return
                if (message["type"] != "http.request"
                        or not message.get("more_body")):
                    break
            body = b"".join(m.get("body", b"") for m in messages
                            if m["type"] == "http.request")
            msg = _message(body)
            described = _describe(msg, len(body))
            called = _called(msg)
            replay = iter(messages)

        client_gone = False

        async def receive_replayed():
            """The buffered request messages, then the real channel -- watching
            for `http.disconnect`, which is definite evidence the caller stopped
            waiting (an nginx timeout drops the upstream connection).

            Definite when it arrives, but not something to rely on: nothing polls
            this channel after the body is read, so a disconnect usually goes
            unobserved. The duration against PROXY_READ_TIMEOUT is the dependable
            signal; this one confirms it when we happen to see it."""
            nonlocal client_gone
            message = next(replay, None) if replay is not None else None
            if message is None:
                message = await receive()
            if message["type"] == "http.disconnect":
                client_gone = True
            return message

        log.info("%s start %s", common, described)

        status, captured = None, bytearray()

        async def watched(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                # slice to the room left, not just skip once full: with
                # json_response the whole envelope arrives as ONE message, so a
                # test on the buffer alone always sees an empty buffer and copies
                # the entire body -- the cap would bound nothing. And extend, not
                # `+=`: an augmented assignment would rebind the name and so make
                # it local to this closure.
                captured.extend(message.get("body", b"")[:CAPTURE_MAX - len(captured)])
            await send(message)

        started = time.monotonic()
        try:
            await self.app(scope, receive_replayed, watched)
        except Exception:
            # the call did not complete, whatever the caller ends up receiving
            # (api/errors.py's 500, or a body cut short if the transport had
            # already started one). Logged with its traceback here -- this is the
            # one record that a request arrived and died inside us, which is the
            # question a client-side error report cannot answer. Then let it
            # through untouched: this wrapper observes, it does not handle.
            log.exception("%s raised after %.0f ms (status so far %s)",
                          common, (time.monotonic() - started) * 1000, status)
            if analytics.ENABLED and called:
                analytics.track_mcp(scope, *called, failed=True)
            raise
        # `_failed` reads a JSON-RPC envelope, which only a POST answers. A GET is
        # the SSE stream, whose `event:`/`data:` frames are not JSON -- running the
        # envelope test on those would label every stream JSONRPC-ERROR.
        if method == "POST":
            failed = _failed(status, bytes(captured), len(captured) >= CAPTURE_MAX)
            flag = " JSONRPC-ERROR" if failed else ""
        else:
            failed = status is None or status >= 400
            flag = " HTTP-ERROR" if failed else ""
        elapsed = time.monotonic() - started
        # `status` is what we sent, which past the proxy's read timeout is not
        # what the caller got -- see PROXY_READ_TIMEOUT. Say so on the line rather
        # than leaving a bare `status=200` to be read as a delivered 200.
        if elapsed > PROXY_READ_TIMEOUT:
            flag += (" PAST-PROXY-TIMEOUT(%.0fs) status-is-what-we-sent-not-what"
                     "-the-caller-got" % PROXY_READ_TIMEOUT)
        if client_gone:
            flag += " CLIENT-GONE"
        log.info("%s done status=%s %.0f ms bytes=%d%s %s",
                 common, status, elapsed * 1000, len(captured), flag, described)
        if analytics.ENABLED and called:
            analytics.track_mcp(scope, *called, failed=failed)


class BarePathToMount:
    """Make `/mcp` reach the mount instead of being redirected to `/mcp/`.

    This has to sit *ahead of the router*. `Mount("/mcp")` compiles to a pattern
    that requires something after the prefix, so the bare path never matches it;
    Starlette's `redirect_slashes` then notices that adding a slash would match
    and answers 307. Rewriting the path inside the mounted app is therefore too
    late -- nothing gets there.

    A redirect is correct HTTP and still the wrong answer here. The URL we
    publish has no trailing slash, so *every* session paid a 307 on its first
    POST, and a client whose transport will not replay a POST body across a
    redirect fails at the handshake -- which reaches the model as a bare
    "server error" and leaves nothing in our logs, since the request never got
    past the router. Two sources of that failure disappear at once: the extra
    round trip, and the redirect itself.

    Exact match only: `/mcp/…` sub-paths are the transport's own business."""

    def __init__(self, app, path="/mcp"):
        self.app = app
        self.path = path

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == self.path:
            scope = {**scope, "path": self.path + "/",
                     "raw_path": (self.path + "/").encode()}
        await self.app(scope, receive, send)


def mount(app):
    """Expose the MCP server on `app` at /mcp and /mcp/, neither redirecting.
    Call before the static site catch-all is mounted (serve() mounts "/" last),
    so the MCP routes win."""
    app.mount("/mcp/", _LoggedMCP(_http_app))
    app.add_middleware(BarePathToMount)
