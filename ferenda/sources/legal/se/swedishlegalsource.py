# -*- coding: utf-8 -*-
from __future__ import unicode_literals
# Intermediate base class containing some small functionality useful
# for handling data sources of swedish law.

from datetime import datetime, date
import difflib
import os
import re

from rdflib import URIRef, RDFS, Graph
from six import text_type as str

from ferenda import DocumentRepository, DocumentStore
from ferenda.elements import Paragraph, Section


class Stycke(Paragraph):
    pass


class Sektion(Section):
    pass


class SwedishLegalStore(DocumentStore):

    """Customized DocumentStore."""

    def basefile_to_pathfrag(self, basefile):
        # "2012/13:152" => "2012-13/152"
        # "2012:152"    => "2012/152"
        return basefile.replace("/", "-").replace(":", "/")

    def pathfrag_to_basefile(self, pathfrag):
        # "2012-13/152" => "2012/13:152"
        # "2012/152"    => "2012:152"
        return pathfrag.replace("/", ":").replace("-", "/")

    def intermediate_path(self, basefile, attachment=None):
        return self.path(basefile, "intermediate", ".xml", attachment=attachment)


class SwedishLegalSource(DocumentRepository):
    documentstore_class = SwedishLegalStore
    namespaces = ['rdf', 'rdfs', 'xsd', 'dct', 'skos', 'foaf',
                  'xhv', 'owl', 'prov', 'bibo',
                  ('rpubl', 'http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#'),
                  ('rinfoex', 'http://lagen.nu/terms#')]

    swedish_ordinal_list = ('första', 'andra', 'tredje', 'fjärde',
                            'femte', 'sjätte', 'sjunde', 'åttonde',
                            'nionde', 'tionde', 'elfte', 'tolfte')
    swedish_ordinal_dict = dict(list(zip(
        swedish_ordinal_list, list(range(1, len(swedish_ordinal_list) + 1)))))

    def get_default_options(self):
        resource_path = os.path.normpath(
            os.path.dirname(__file__) + "../../../../res/etc/authrec.n3")
        opts = super(SwedishLegalSource, self).get_default_options()
        opts['authrec'] = resource_path
        return opts

    def _swedish_ordinal(self, s):
        sl = s.lower()
        if sl in self.swedish_ordinal_dict:
            return self.swedish_ordinal_dict[sl]
        return None

    def _load_resources(self, resource_path):
        # returns a mapping [resource label] => [resource uri]
        # resource_path is given relative to cwd
        graph = Graph()
        graph.load(resource_path, format='n3')
        d = {}
        for uri, label in graph.subject_objects(RDFS.label):
            d[str(label)] = str(uri)
        return d

    def lookup_resource(self, resource_label, cutoff=0.8, warn=True):
        """Given a text label refering to some kind of organization,
        person or other entity, eg. 'Justitiedepartementet Gransk',
        return a URI for that entity. The text label does not need to
        match exactly byte-for-byte, a fuzziness matching function
        returns any reasonably similar (adjusted by the cutoff
        parameter) entity."""
        keys = []
        if not hasattr(self, 'org_resources'):
            self.org_resources = self._load_resources(self.config.authrec)

        for (key, value) in list(self.org_resources.items()):
            if resource_label.lower().startswith(key.lower()):
                return URIRef(value)
            else:
                keys.append(key)

        fuzz = difflib.get_close_matches(resource_label, keys, 1, cutoff)
        if fuzz:
            if warn:
                self.log.warning("Assuming that '%s' should be '%s'?" %
                                 (resource_label, fuzz[0]))
            return self.lookup_resource(fuzz[0])
        else:
            self.log.warning("No good match for '%s'" % (resource_label))
            raise KeyError(resource_label)

    def lookup_label(self, resource):
        if not hasattr(self, 'org_resources'):
            self.org_resources = self._load_resources(self.config.authrec)
        for (key, value) in list(self.org_resources.items()):
            if resource == value:
                return key

        raise KeyError(resource)

    def sameas_uri(self, uri):
        # "http://localhost:8000/res/dir/2012:35" => "http://rinfo.lagrummet.se/publ/dir/2012:35",
        # "http://localhost:8000/res/dv/hfd/2012:35" => "http://rinfo.lagrummet.se/publ/rattsfall/hdf/2012:35",
        assert uri.startswith(self.config.url)
        # FIXME: This hardcodes the res/ part of our local URIs
        # needlessly -- make configurable
        maps = (("res/dv/", "publ/rattsfall/"),
                ("res/", "publ/"))
        for fr, to in maps:
            if self.config.url + fr in uri:
                return uri.replace(self.config.url + fr,
                                   "http://rinfo.lagrummet.se/" + to)

    def parse_iso_date(self, datestr):
        # only handles YYYY-MM-DD now. Look into dateutil or isodate
        # for more complete support of all ISO 8601 variants
        return datetime.strptime(datestr, "%Y-%m-%d")

    def parse_swedish_date(self, datestr):
        # assume strings on the form "3 februari 2010"
        months = {"januari": 1,
                  "februari": 2,
                  "mars": 3,
                  "april": 4,
                  "maj": 5,
                  "juni": 6,
                  "juli": 7,
                  "augusti": 8,
                  "september": 9,
                  "oktober": 10,
                  "november": 11,
                  "december": 12,
                  "år": 12}
        # strings on the form "vid utgången av december 1999"
        if datestr.startswith("vid utgången av"):
            import calendar
            (x, y, z, month, year) = datestr.split()
            month = months[month]
            year = int(year)
            day = calendar.monthrange(year, month)[1]
        else:
            # assume strings on the form "3 februari 2010"
            (day, month, year) = datestr.split()
            day = int(day)
            month = months[month]
            year = int(year)
        return date(year, month, day)

    def infer_triples(self, d, basefile):
        try:
            identifier = d.getvalue(self.ns['dct'].identifier)
            # if the identifier is incomplete, eg "2010/11:68" instead
            # of "Prop. 2010/11:68", the following triggers a
            # ValueError, which is handled the same as if no
            # identifier is available at all.
            (doctype, arsutgava, lopnummer) = re.split("[ :]", identifier)
        except (KeyError, ValueError):
            # Create one from basefile. First guess prefix
            if self.rdf_type == self.ns['rpubl'].Direktiv:
                prefix = "Dir. "
            elif self.rdf_type == self.ns['rpubl'].Utredningsbetankande:
                if d.getvalue(self.ns['rpubl'].utrSerie) == "http://rinfo.lagrummet.se/serie/utr/ds":
                    prefix = "Ds "
                else:
                    prefix = "SOU "
            elif self.rdf_type == self.ns['rpubl'].Proposition:
                prefix = "Prop. "
            elif self.rdf_type == self.ns['rpubl'].Forordningsmotiv:
                prefix = "Fm "
            else:
                raise ValueError("Cannot create dct:identifer for rdf_type %r" % self.rdf_type)
            identifier = "%s%s" % (prefix, basefile)

            self.log.warning(
                "%s: No dct:identifier, assuming %s" % (basefile, identifier))
            d.value(self.ns['dct'].identifier, identifier)

        self.log.debug("Identifier %s" % identifier)
        (doctype, arsutgava, lopnummer) = re.split("[ :]", identifier)
        d.value(self.ns['rpubl'].arsutgava, arsutgava)
        d.value(self.ns['rpubl'].lopnummer, lopnummer)

    def toc_query(self):
        return """PREFIX dct:<http://purl.org/dc/terms/>
                  PREFIX rpubl:<http://rinfo.lagrummet.se/ns/2008/11/rinfo/publ#>
                  SELECT DISTINCT ?uri ?title ?identifier ?arsutgava ?lopnummer ?departement
                  FROM <%s>
                  WHERE {?uri dct:title ?title;
                              dct:identifier ?identifier;
                              rpubl:arsutgava ?arsutgava;
                              rpubl:lopnummer ?lopnummer;
                              rpubl:departement ?departement;
                  }""" % self.context()

    def toc_criteria(self):
        return (
            {'predicate': self.ns['rpubl']['arsutgava'],
             'binding': 'arsutgava',
             'label': 'Efter årtal',
             'sorter': cmp,
             'pages': []},
            {'predicate': self.ns['dct']['title'],
             'binding': 'title',
             'label': 'Efter rubrik',
             'selector': lambda x: x[0].lower(),
             'sorter': cmp,
             'pages': []},
            {'predicate': self.ns['rpubl']['departement'],
             'binding': 'departement',
             'label': 'Efter departement',
             'selector': self.lookup_label,
             'sorter': cmp,
             'pages': []},
        )

    def toc_item(self, binding, row):
        return {'uri': row['uri'],
                'label': row['identifier'] + ": " + row['title']}
