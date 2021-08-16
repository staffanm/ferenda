from rdflib import Graph, Literal, Namespace, URIRef, BNode, RDF, RDFS
from collections import defaultdict, OrderedDict
from lxml.etree import Element


XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
XSI_SCHEMALOC = "{http://www.w3.org/2001/XMLSchema-instance}schemaLocation"

META = "{http://www.w3.org/1999/xhtml}meta"
TITLE = "{http://www.w3.org/1999/xhtml}title"
LINK = "{http://www.w3.org/1999/xhtml}link"
HEAD = "{http://www.w3.org/1999/xhtml}head"

def to_resource(obj):
    return obj.n3() if isinstance(obj, BNode) else str(obj)

class dump(object):
    def __new__(cls, graph, doc_uri, doc_lang):
        self = object.__new__(cls)
        self.graph = graph
        self.doc_uri = doc_uri
        self.doc_lang = doc_lang

        self.done_subjects = set()
        self.missing_subjects = set()
        
        headcontent = Element(HEAD, {'about': doc_uri})
        headcontent.extend(list(self.render_head()))
        return headcontent

    def render_head(self):
        for res in self.render_subject(URIRef(self.doc_uri)):
            yield res
        while self.missing_subjects:
            subj = next(iter(sorted(self.missing_subjects)))
            self.missing_subjects -= set((subj,))
            for res in self.render_subject(subj):
                yield res
    
    def render_subject(self, uri):
        if uri in self.done_subjects: return

        self.done_subjects.add(uri)

        for (pred, obj) in sorted(self.graph.predicate_objects(uri)):
            yield self.render_tripple(uri, pred, obj)
            for res in self.render_subject(obj):
                yield res

        # We could just recurse here, but to make the flow of the
        # generated RDFa more readable, we postpone these to until all
        # forward-recursed tripples have been exhausted.
        self.missing_subjects.update(set(subj for subj, pred in self.graph.subject_predicates(uri))
                                     - self.done_subjects)

    def render_tripple(self, subj, pred, obj):
        if self.graph.qname(pred) == "dcterms:title" and str(subj) == self.doc_uri:
            childattrs = OrderedDict([('property', 'dcterms:title')])
            if obj.language != self.doc_lang:
                childattrs[XML_LANG] = obj.language or ""
            e = Element(TITLE, childattrs)
            e.text = str(obj)
            return e
        elif isinstance(obj, (URIRef, BNode)):
            if str(obj) == self.doc_uri:
                childattrs = OrderedDict([('rev', self.graph.qname(pred)),
                                          ('resource' if isinstance(subj, BNode) else "href",
                                           to_resource(subj))])
            else:
                childattrs = OrderedDict([('rel', self.graph.qname(pred)),
                                          ('resource' if isinstance(obj, BNode) else "href",
                                           to_resource(obj))])
                if str(subj) != self.doc_uri:
                    childattrs['about'] = to_resource(subj)
                    childattrs.move_to_end("about", False)
            return Element(LINK, childattrs)                    
        else:  # this must be a literal, ie something to be
               # rendered as <meta property="..."
               # content="..."/>
            childattrs = OrderedDict([('property', self.graph.qname(pred)),
                                      ('content', str(obj))])
            if to_resource(subj) != self.doc_uri:
                childattrs['about'] = to_resource(subj)
                childattrs.move_to_end("about", False)
            if obj.datatype:
                childattrs['datatype'] = self.graph.qname(obj.datatype)
            elif obj.language:
                childattrs[XML_LANG] = obj.language
            elif self.doc_lang:
                childattrs[XML_LANG] = ""
            return Element(META, childattrs)

