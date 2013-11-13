# flake8: noqa
from .citationparser import CitationParser
from .uriformatter import URIFormatter
from .describer import Describer
from .layeredconfig import LayeredConfig
from .pdfreader import PDFReader
from .textreader import TextReader
from .wordreader import WordReader
from .triplestore import TripleStore
from .fulltextindex import FulltextIndex
from .devel import Devel
from .fsmparser import FSMParser
from .tocpageset import TocPageset
from .tocpage import TocPage
from .toccriteria import TocCriteria
from .newscriteria import NewsCriteria
from .transformer import Transformer
from .document import Document
from .documententry import DocumentEntry
from .documentstore import DocumentStore
from .documentrepository import DocumentRepository
from .pdfdocumentrepository import PDFDocumentRepository
from .compositerepository import CompositeRepository
__version__ = "0.1.6.1" #gets pulled into setup.py and docs/conf.py
