# flake8: noqa
from .citationparser import CitationParser
from .uriformatter import URIFormatter
from .describer import Describer
from .pdfreader import PDFReader
from .pdfanalyze import PDFAnalyzer
from .textreader import TextReader
from .wordreader import WordReader
from .triplestore import TripleStore
from .fulltextindex import FulltextIndex
from .documententry import DocumentEntry
from .devel import Devel
from .fsmparser import FSMParser
from .tocpageset import TocPageset
from .tocpage import TocPage
from .facet import Facet
from .feedset import Feedset
from .feed import Feed
from .transformer import Transformer
from .document import Document
from .documentstore import DocumentStore
from .documentrepository import DocumentRepository
from .pdfdocumentrepository import PDFDocumentRepository
from .compositerepository import CompositeRepository, CompositeStore
from .resources import Resources
from .wsgiapp import WSGIApp
__version__ = "0.2.1.dev10" #gets pulled into setup.py and docs/conf.py
# dev1: changes constructor signature for DocumentRepository (using a config obj as first positional parameter)
# dev2: enables multiprocessing for manager task queue handling (./ferenda.py rfc parse --all --processes=4)
# dev3: incorporation of changes in ferenda.sources.legal.se during lagen.nu-tng experimentation
# dev4: Element.as_xhtml() now creates dct:isPartOf triples for sectioned documents
# dev5: bundled a git snapshot (4e339f0) of swc.mw (for MediaWiki markup) as ferenda.thirdparty.mw 
# dev6: more incorporation of changes in ferenda.sources.legal.se during lagen.nu-tng experimentation
# dev7: changes in toc handling
# dev8: LayeredConfig.set (used by manager._instantiate_class)
# dev9: buildclient, buildqueue, --buildserver and --buildqueue support
# dev10: LayeredConfig separate package, no longer bundled.
# dev11: Feed functionality uses facets, New PDFAnalyzer, testutil.download_test supports FERENDA_SET_TESTFILES...
