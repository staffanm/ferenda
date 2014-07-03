# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import logging
from datetime import datetime

from six import text_type as str
from rdflib import URIRef, Namespace
from rdflib.namespace import RDF, RDFS, DC, SKOS, FOAF, DCTERMS
SCHEMA = Namespace("http://schema.org/")

from ferenda import fulltextindex # to get the IndexedType classes
from ferenda import util

class Facet(object):
    @classmethod
    def defaultselector(cls, row, binding, resource_graph=None):
        return row[binding]

    @classmethod
    def year(cls, row, binding='dcterms_issued', resource_graph=None):
        datestring = row[binding]
        # assume a date(time) like '2014-06-05T12:00:00', '2014-06-05'
        # or even '2014-06'
        formatstring = {19: "%Y-%m-%dT%h:%m:%s",
                        10: "%Y-%m-%d",
                        7: "%Y-%m"}[len(datestring)]
        d = datetime.strptime(datestring, formatstring)
        return str(d.year)

    @classmethod
    def booleanvalue(cls, row, binding='schema_free', resource_graph=None):
        # only 'true' is True, everything else is False
        return row[binding] == 'true'
        
    @classmethod
    def titlesortkey(cls, row, binding='dcterms_title', resource_graph=None):
        # ingnore provided binding -- this key func sorts by dcterms:title, period.
        title = row['dcterms_title']
        return util.title_sortkey(title)

    @classmethod
    def firstletter(cls, row, binding='dcterms_title', resource_graph=None):
        return cls.titlesortkey(row, binding)[0]

    @classmethod
    def resourcelabel(cls, row, binding='dcterms_publisher', resource_graph=None):
        uri = URIRef(row[binding])
        for pred in (RDFS.label, SKOS.prefLabel, SKOS.altLabel, DCTERMS.title, DCTERMS.alternative, FOAF.name):
            if resource_graph.value(uri, pred):
                return str(resource_graph.value(uri, pred))
        else:
            return row[binding]

    @classmethod
    def sortresource(cls, row, binding='dcterms_publisher', resource_graph=None):
        row[binding] = cls.resourcelabel(row, binding, resource_graph)
        return cls.titlesortkey(row, binding)

    @classmethod
    def qname(cls, row, binding='rdf_type', resource_graph=None):
        u = URIRef(row[binding])
        return resource_graph.qname(u)

    # define a number of default values, used if the user does not
    # explicitly specify indexingtype/selector/key
    defaults = None
    # formatting directives for label/pagetitle:
    # %(criteria)s = The human-readable criteria for sorting/dividing/faceting, eg "date of publication", "document title" or "publisher"
    # %(selected)s = The selected value, eg "2014", "A", "O'Reilly and Associates Publishing, inc."
    # %(selected_uri)s = For resource-type values, the underlying URI, eg "http://example.org/ext/publisher/oreilly"
    def __init__(self,
                 rdftype=DCTERMS.title, # any rdflib.URIRef -- should be called 'rdfpredicate'??
                 label=None, # toclabel
                 pagetitle=None, 
                 indexingtype=None,   # if not given, determined by rdftype
                 selector=None,       # - "" -
                 key=None,            # - "" -
                 toplevel_only=None,  # - "" -
                 use_for_toc=None,     # - "" -
                 selector_descending = None,
                 key_descending = None,
                 multiple_values = None,
                 dimension_type = None, # could be determined by indexingtype
                 dimension_label = None
             ):
            
        def _finddefault(provided, rdftype, argumenttype, default):
            if provided is None:
                if rdftype in self.defaults and argumenttype in self.defaults[rdftype]:
                    return self.defaults[rdftype][argumenttype]
                else:
                    # since self.defaults doesn't contain meaningless
                    # defaults (like selector for rdf:type) it's not a
                    # good UI to warn about this. Might need to add
                    # more data to self.defaults in order to re-enable
                    # this.

                    # log = logging.getLogger(__name__)
                    # log.warning("Cannot map rdftype %s with argumenttype %s, defaulting to %r" %
                    #             (rdftype, argumenttype, default))
                    return default                
            else:
                return provided

        self.rdftype = rdftype
        self.label = _finddefault(label, rdftype, 'label', "Sorted by %(term)s")
        self.pagetitle = _finddefault(pagetitle, rdftype, 'pagetitle', "Documents where %(term)s = %(selected)s")
        self.indexingtype        = _finddefault(indexingtype, rdftype, 'indexingtype', fulltextindex.Text())
        self.selector            = _finddefault(selector, rdftype, 'selector', self.defaultselector)
        self.key                 = _finddefault(key, rdftype, 'key', self.defaultselector)
        self.toplevel_only       = _finddefault(toplevel_only, rdftype, 'toplevel_only', False)
        self.use_for_toc         = _finddefault(use_for_toc, rdftype, 'use_for_toc', False)
        self.selector_descending = _finddefault(selector_descending, rdftype, 'selector_descending', False)
        self.key_descending      = _finddefault(key_descending, rdftype, 'key_descending', False)
        self.multiple_values     = _finddefault(multiple_values, rdftype, 'multiple_values', False)
        self.dimension_type      = _finddefault(dimension_type, rdftype, 'dimension_type', None)
        # dimension_label should only be provided if an unusual
        # selector for a rdftype is used (eg is_april_fools() for
        # dcterms:issued), therefore no rdftype-dependent default.
        self.dimension_label     = dimension_label

    def __repr__(self):
        dictrepr = "".join((" %s=%r" % (k, v) for k, v in sorted(self.__dict__.items()) if not callable(v)))
        return ("<%s%s>" % (self.__class__.__name__, dictrepr))
        
    def __eq__(self, other):
        # compare only those properties that affects the SET of
        # selected data using this facet
        return (self.rdftype == other.rdftype and
                self.dimension_type == other.dimension_type and
                self.dimension_label == other.dimension_label and 
                self.selector == other.selector)

        
Facet.defaults = {RDF.type: {
                      'indexingtype': fulltextindex.URI(),
                      'toplevel_only': False,
                      'use_for_toc': False,
                      'selector': Facet.qname,
                      'dimension_type': "term"},
                  DCTERMS.title: {
                      'indexingtype': fulltextindex.Text(boost=4),
                      'toplevel_only': False,
                      'use_for_toc': True, 
                      'selector': Facet.firstletter,
                      'key': Facet.titlesortkey,
                      'dimension_type': "value",
                      'pagetitle': 'Documents starting with "%(selected)s"'
                  },
                  DCTERMS.identifier: {
                      'indexingtype': fulltextindex.Label(boost=16),
                      'toplevel_only': False,
                      'use_for_toc': True, 
                      'selector': Facet.firstletter,
                      'key': Facet.titlesortkey,
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
                      'selector': Facet.defaultselector,
                      'key': Facet.titlesortkey,
                      'dimension_type': "value"
                  },
                  DCTERMS.publisher:{
                      'indexingtype': fulltextindex.Resource(),
                      'toplevel_only': True,
                      'use_for_toc': True,
                      'selector': Facet.defaultselector,
                      'key': Facet.sortresource,
                      'dimension_type': 'ref',
                  },
                  DCTERMS.references:{ # NB: this is a single URI reference w/o label
                      'indexingtype': fulltextindex.URI(),
                      'use_for_toc': False,
                  },
                  DCTERMS.issued:{
                      'label': "Sorted by publication year",
                      'pagetitle': "Documents published in %(selected)s",
                      'indexingtype': fulltextindex.Datetime(),
                      'toplevel_only': True,
                      'use_for_toc': True,
                      'selector': Facet.year,
                      'key': Facet.defaultselector,
                      'selector_descending': False,
                      'key_descending': False,
                      'dimension_type': "year"
                  },
                  DC.subject: {
                      'indexingtype': fulltextindex.Keyword(),  # eg. one or more string literals (not URIRefs),
                      'multiple_values': True,
                      'toplevel_only': True,
                      'use_for_toc': True,
                      'selector': Facet.defaultselector, # probably needs changing
                      'key': Facet.defaultselector,
                      'multiple_values': True,
                      'dimension_type': 'value',
                },
                DCTERMS.subject: {
                    'indexingtype': fulltextindex.Resource(),  # eg. one or more URIRefs + labels
                    'multiple_values': True,
                    'toplevel_only': True,
                    'use_for_toc': True,
                    'selector': Facet.defaultselector, # probably needs changing
                    'key': Facet.defaultselector,
                    'multiple_values': True,
                    'dimension_type': 'value',
                },
                SCHEMA.free: { # "A flag to signal that the publication is accessible for free."
                    'indexingtype': fulltextindex.Boolean(),
                    'toplevel_only': True,
                    'use_for_toc': True,
                    'selector': Facet.booleanvalue,
                    'key': Facet.defaultselector,
                    'dimension_type': 'value'
                }
            }

