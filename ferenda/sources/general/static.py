import os

from rdflib import URIRef, Graph, Literal
import pkg_resources

try:
    from docutils.core import publish_doctree
    from docutils import writers, nodes
except ImportError:
    # FIXME: allow this for now as no tests are run against the code,
    # and we'd like to avoid the expensive docutils dependency for
    # testing. But sooner or later we'll need to take that hit.
    pass



from ferenda import DocumentRepository
from ferenda import DocumentStore
from ferenda import util
from ferenda.decorators import managedparsing
from ferenda import elements
from ferenda.elements import html


class StaticStore(DocumentStore):
    """Customized DocumentStore that looks for all "downloaded" resources
    from the specified ``staticdir``. If ``staticdir`` isn't provided
    or doesn't exist, falls back to a collection of package resources
    (under ferenda/res/static-content). Parsed, generated etc files
    are handled like normal, ie stored under
    ``[datadir]/static/{parsed,distilled,generated,...}/``
    """
    def __init__(self, datadir, downloaded_suffix=".rst", storage_policy="file", staticdir="static"):
        super(StaticStore,self).__init__(datadir, downloaded_suffix, storage_policy)
        if os.path.exists(staticdir):
            self.staticdir = staticdir
        else:
            # find out the path of our resourcedir
            p = pkg_resources.resource_filename('ferenda',
                                                'res/static-content/README')
            self.staticdir = os.path.dirname(p)
    
    def downloaded_path(self, basefile, version=None, attachment=None):
        segments = [self.staticdir,
                    self.basefile_to_pathfrag(basefile)+self.downloaded_suffix]
        return "/".join(segments).replace("/",os.sep)

    def list_basefiles_for(self,action,basedir=None):
        if action == "parse":
            for x in util.list_dirs(self.staticdir, self.downloaded_suffix):
                pathfrag  = x[len(self.staticdir)+1:-len(self.downloaded_suffix)]
                yield self.pathfrag_to_basefile(pathfrag)
        else:
            for x in super(StaticStore, self).list_basefiles_for(action,basedir):
                yield x

class Static(DocumentRepository):
    alias = "static"
    downloaded_suffix = ".rst"
    documentstore_class = StaticStore
    # urls become on the form "http://lcoalhost:8000/static/about"
    
    def download(self):
        pass

    @managedparsing
    def parse(self, doc):
        source = util.readfile(self.store.downloaded_path(doc.basefile))
        doctree = publish_doctree(source=source)
        stack = []
        root = self._transform(doctree,stack)
        if isinstance(root[0], elements.Title):
            doc.meta.add((URIRef(doc.uri), self.ns['dct'].title, Literal(str(root[0]),doc.lang)))
            root.pop(0)
        doc.body = root

    # converts a tree of docutils.nodes into ferenda.elements
    def _transform(self, node, stack):
        cls = {'document':elements.Body,
               'title':elements.Title,
               'paragraph':elements.Paragraph,
               '#text': str
               }.get(node.tagname, elements.CompoundElement)
        if hasattr(node,'attributes'):
            attrs = dict((k,v) for (k,v) in node.attributes.items() if v)
            el = cls(**attrs)
        else:
            el = cls(node) # !

        if len(stack) > 0:
            top = stack[-1]
            top.append(el)
            
        if hasattr(node,'attributes'):
            stack.append(el)
            for childnode in node:
                self._transform(childnode, stack)
            return stack.pop()

    def toc(self, otherrepos=[]):
        pass

    def news(self, otherrepos=[]):
        pass
    
    def frontpage_content(self, primary=False):
        pass

    def tabs(self):
        if os.path.exists(self.store.parsed_path("about")):
            return [("About", self.canonical_uri("about"))]
        else:
            return[]
                
    def footer(self):
        # FIXME: ordering?
        res = []
        for basefile in self.store.list_basefiles_for("generate"):
            uri = self.canonical_uri(basefile)
            g = Graph()
            g.parse(self.store.distilled_path(basefile))
            title = g.value(URIRef(uri), self.ns['dct'].title).toPython()
            if not title:
                title = basefile
            res.append((title,uri))
        return res
