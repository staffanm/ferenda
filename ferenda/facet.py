import logging

from rdflib import URIRef, Namespace
from rdflib.namespace import RDF, RDFS, DC, SKOS
from rdflib.namespace import DCTERMS as DCTERMS
SCHEMA = Namespace("http://schema.org/")

from ferenda import fulltextindex # to get the IndexedType classes

class Facet(object):
    @staticmethod
    def defaultselector(row, binding):
        return row[binding]
      
    @staticmethod
    def firstletter(row, binding='dcterms_title'):
        return titlesortkey(row, binding)[0]

    @staticmethod
    def year(row, binding='dcterms_issued'):
        # assume a date(time) on the form 2014-06-05, the year == the first 4 chars
        return row[binding][:4]

    @staticmethod
    def titlesortkey(row, binding='dcterms_title'):
        title = row[binding].lower()
        if title.startswith("the "):
            title = title[4:]
            # filter away starting non-word characters (but not digits)
            title = re.sub("^\W+", "", title)
            # remove whitespace
            return "".join(title.split())

    @staticmethod
    def resourcelabel(row, binding='dcterms_publisher', resourcegraph=None):
        uri = URIRef(row[binding])
        for pred in (RDFS.label, SKOS.prefLabel, SKOS.altLabel, DCTERMS.title, DCTERMS.alternative):
            if resourcegraph.value(uri, pred):
                return str(resourcegraph.value(uri, pred))
        else:
            return row[binding]

    @staticmethod
    def sortresource(row, binding='dcterms_publisher', resourcegraph=None):
        row[binding] = resourcelabel(row, binding, resourcegraph)
        return titlesortkey(row, binding)

    # define a number of default values, used if the user does not
    # explicitly specify indexingtype/selector/key
    defaults = {RDF.type: {
                    'indexingtype': fulltextindex.URI(),
                    'toplevel_only': False,
                    'use_for_toc'  : False}, # -> selector etc are irrelevant
                DCTERMS.title: {
                    'indexingtype': fulltextindex.Text(boost=4),
                    'toplevel_only': False,
                    'use_for_toc': True, 
                    'selector': firstletter,
                    'key': titlesortkey,
                },
                DCTERMS.identifier: {
                    'indexingtype': fulltextindex.Label(boost=16),
                    'toplevel_only': False,
                    'use_for_toc': True, 
                    'selector': firstletter,
                    'key': titlesortkey,
                },
                DCTERMS.abstract: {
                    'indexingtype': fulltextindex.Text(boost=2),
                    'toplevel_only': True,
                    'use_for_toc': False
                },
                DC.creator:{
                    'indexingtype': fulltextindex.Label(),
                    'toplevel_only': True,
                    'use_for_toc': True,
                    'selector': firstletter,
                    'key': titlesortkey,
                },
                DCTERMS.publisher:{
                    'indexingtype': fulltextindex.Resource(),
                    'toplevel_only': True,
                    'use_for_toc': True,
                    'selector': firstletter,
                    'key': sortresource,
                },
                DC.issued:{
                    'indexingtype': fulltextindex.Datetime(),
                    'toplevel_only': True,
                    'use_for_toc': True,
                    'selector': year,
                    'key': defaultselector,
                    'selector_descending': True,
                    'key_descending': True
                },
                DC.subject: {
                    'indexingtype': fulltextindex.Keywords(),  # eg. one or more string literals (not URIRefs),
                    'multiple_values': True,
                    'toplevel_only': True,
                    'use_for_toc': True,
                    'selector': defaultselector, # probably needs changing
                    'key': defaultselector,
                    'multiple_values': True
                },
                DCTERMS.subject: {
                    'indexingtype': fulltextindex.Resources(),  # eg. one or more URIRefs + labels
                    'multiple_values': True,
                    'toplevel_only': True,
                    'use_for_toc': True,
                    'selector': defaultselector, # probably needs changing
                    'key': defaultselector,
                    'multiple_values': True
                },
                SCHEMA.free: { # "A flag to signal that the publication is accessible for free."
                    'indexingtype': fulltextindex.Boolean(),
                    'toplevel_only': True,
                    'use_for_toc': True,
                    'selector': defaultselector,
                    'key': defaultselector,
                }
            }
    # formatting directives for label/pagetitle:
    # %(criteria)s = The human-readable criteria for sorting/dividing/faceting, eg "date of publication", "document title" or "publisher"
    # %(selected)s = The selected value, eg "2014", "A", "O'Reilly and Associates Publishing, inc."
    # %(selected_uri)s = For resource-type values, the underlying URI, eg "http://example.org/ext/publisher/oreilly"
    def __init__(self,
                 rdftype=DCTERMS.title, # any rdflib.URIRef
                 label="Sorted by %(criteria)s", # toclabel
                 pagetitle="Documents where %(criteria)s = %(selected)s",
                 indexingtype=None,   # if not given, determined by rdftype
                 selector=None,       # - "" -
                 key=None,            # - "" -
                 toplevel_only=None,  # - "" -
                 use_for_toc=None,     # - "" -
                 selector_descending = None,
                 key_descending = None,
                 multiple_values = None,
             ):
        
        def _finddefault(provided, rdftype, argumenttype, default):
            if provided is None:
                if rdftype in self.defaults and argumenttype in self.defaults[rdftype]:
                    return self.defaults[rdftype][argumenttype]
                else:
                    log = logging.getLogger(__name__)
                    log.warning("Cannot map rdftype %s with argumenttype %s, defaulting to %r" %
                                (rdftype, argumenttype, default))
                    return default                
            else:
                return provided

        self.rdftype = rdftype
        self.label = label
        self.pagetitle = pagetitle
        self.indexingtype        = _finddefault(indexingtype, rdftype, 'indexingtype', fulltextindex.Text())
        self.selector            = _finddefault(selector, rdftype, 'selector', self.defaultselector)
        self.key                 = _finddefault(key, rdftype, 'key', self.defaultselector)
        self.toplevel_only       = _finddefault(toplevel_only, rdftype, 'toplevel_only', False)
        self.use_for_toc         = _finddefault(use_for_toc, rdftype, 'use_for_toc', False)
        self.selector_descending = _finddefault(selector_descending, rdftype, 'selector_descending', False)
        self.key_descending      = _finddefault(key_descending, rdftype, 'key_descending', False)
        self.multiple_values     = _finddefault(multiple_values, rdftype, 'multiple_values', False)

    # backwards compatibility shim:
    def as_criteria(self):
        from ferenda.util import uri_leaf
        return TocCriteria(uri_leaf(str(self.rdftype)),
                           self.label,
                           self.pagetitle,
                           self.selector, # might need to wrap these functions to handle differing arg lists
                           self.key,      # - "" -
                           self.selector_descending,
                           self.key_descending,
                           self.rdftype)

    # There should be a way to construct a SPARQL SELECT query from a list of Facets that retrieve all needed data
    # The needed data should be a simple 2D table, where each Facet is represented by one OR MORE fields 
    #    (ie a dcterms:publisher should result in the binding "dcterms_publisher" and "dcterms_publisher_label")
 
    # There must be a way to get a machine-readable label/identifier for each facet. This is used:
    # - for variable binding in the sparql query
    # - for field names in the fulltext index
    # preferably "dct_title", "rdf_type", etc

    # There should be a way to determine which fields that are to be indexed in the fulltext index. This should be based 
    #    on the rdftype (determines how we find the content/value of the facet) and the indexingtype (how we store it).

    # The fulltext index stores a number of fields not directly associated with a Facet:
    # - uri / iri (has corresponding value in the SPARQL SELECT results)
    # - repo (is not represented in the SPARQL SELECT results)
    # - basefile (is not represented either)

    # General modeling:
    # if the rdftype is dcterms:publisher, dcterms:creator, dcterms:subject, the indexingtype SHOULD be fulltextindex.Resource 
    #    (ie the triple should be a URIRef, not Literal, and we store both resource IRI and label)
    # if we can only get Literals, use dc:publisher, dc:creator, dc:subject.

    # at least for some facets (dcterms:subject, dcterms:creator), multiple
    # values must be permitted. 
   

# should/must work for

# Facets that occur at all documentlevels
# - Document or sectional type (rdftype=rdf.type, indexingtype=fulltext.URI(), use_for_toc=False, toplevel_only=False)
# - Title (rdftype=dcterms.title, indexingtype=fulltextindex.Text(boost=4), toplevel_only=False)
# - Identifier (rdftype=dcterms.identifier, indexingtype=fulltextindex.Label(boost=16), toplevel_only=False, use_for_toc=False) # or True, iff a custom selector method is used (like in RFC.py)

# Facets that only occur at document top level
# - Abstract (rdftype=dcterms.abstract, indexingtype=fulltextindex.Text(boost=2))
# - Author (rdftype=dc.creator, indexingtype=fulltextindex.Label()) # ie author is modelled as a Literal
# - Publisher (rdftype=dcterms.publisher, indexingtype=fulltextindex.Resource()) # publisher is modelled as URIRef, a Literal label is picked from the document or extra/[docrepo].ttl
# - Literal publisher (rdftype=dc.publisher, indexingtype=fulltextindex.Label()) # publisher modelled as Literal
# - Publication date (rdftype=dcterms.issued, indexingtype=fulltextindex=Datetime())
