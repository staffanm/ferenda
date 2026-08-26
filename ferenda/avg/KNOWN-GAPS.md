# Kända luckor i avg-korpuset

Samma bokföring som `remisser/KNOWN-GAPS.md`: avgöranden som pipelinen inte får
igenom, och vad som krävs för att de ska komma in. Avstämt mot hela
`lagen all all`-körningen 2026-07-31, där `avg parse` gick igenom 8 601
avgöranden och föll på två.

**Ingen av de två är en bestående lucka.** Båda är utredda och åtgärdade, och
det här är dokumentationen av vad de var — så att nästa gång något liknande dyker
upp finns diagnosen kvar.

## 1. `kkv/468/2004` — HTML som deklarerar `us-ascii` (åtgärdat)

    ERROR parse kkv/468/2004: ValueError: kkv html body declares b'us-ascii',
    not the windows-1252 the diarium publishes

Konkurrensverkets diarium publicerar sina pre-2006-dokument som FrontPage-HTML i
windows-1252, och `kkv_html_text` vägrar allt annat hellre än att gissa en
kodning och tyst producera mojibake. Det här dokumentet deklarerar `us-ascii`
och är därmed det enda i korpuset som faller på regeln.

Undersökningen: dokumentet är 26 242 byte och innehåller **noll** byte över
0x7F. Deklarationen är alltså sann, och eftersom ASCII är en äkta delmängd av
cp1252 avkodas filen likadant oavsett vilken av dem man väljer — det fanns aldrig
något att förvanska.

Åtgärd (2026-07-31): `us-ascii` accepteras, men först efter att bytena
kontrollerats med `data.isascii()`. Ett dokument som deklarerar `us-ascii` men
bär höga byte ljuger om sig självt och avvisas fortfarande — vilket är hela
poängen med kontrollen.

## 2. `kkv/117/2017` — OCR som föll under last (övergående)

    ERROR parse kkv/117/2017: CalledProcessError: Command '['ocrmypdf', '--quiet',
    '--force-ocr', '-l', 'swe', '.../17-0117-artilleriet-interiors-ab.pdf', ...]'
    returned non-zero exit status 15

Det här är **inte** ett trasigt dokument. Samma fil, samma kommando, kört ensamt
efteråt: exit 0, 5,8 MB in, 852 kB ut, "Output file is a PDF/A-2B (as expected)".

ocrmypdf:s exit 15 är `child_process_error` — ett underprogram (ghostscript eller
tesseract) dog. Felet uppstod i en `lagen all all`-körning med 32 parallella
parse-arbetare som var och en startar just de programmen, alla flertrådade och
minneshungriga. Det är en resurskollision, inte ett dataproblem, och den kan
träffa vilket OCR-krävande dokument som helst.

Ingen kodändring gjord. Om den här klassen av fel återkommer i mer än enstaka
dokument per körning är åtgärden att strypa parallelliteten för OCR-steget, inte
att undanta dokumentet.

## Inte luckor

* **Tomma `.doc`/`.docx` under `downloaded/dv`** — avsiktliga, se projektets
  anteckningar. Hör inte till avg men förväxlas lätt.
* **IMY-poster utan diarienummer** — 13 dokument i `avg download` skriver ut
  inget diarienummer och kan inte filas (`Läs IMY:s yttrande`,
  `Inspektionsbekräftelse`, Klarnas dataskyddsinformation m.fl.). Det är bilagor
  och sidhänvisningar, inte beslut; hämtningen vägrar att hitta på en identitet
  åt dem, vilket är rätt. De ska inte in i korpuset.

## KKV's footnotes are not collected

`avg` recovers the notes its letterhead templates set below the running text
(`lib.pdftext.letterhead_footnotes`), which is where IMY grounds a vägledning it
has named in prose -- see `edpb/KNOWN-GAPS.md` for the measurement. That is
wired for **imy** only.

JO's and JK's templates set no notes and ARN's decisions arrive as one unbroken
run of prose, so neither has any to collect. **KKV does**, but its three
document formats (PDF, the pre-2006 FrontPage HTML, Word) go through one
dispatcher, `kkv_read_document`, which would have to grow a third return value
threaded through `kkv_body` and `parse_kkv`. Left open rather than done
half-way.
