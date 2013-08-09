import os

from rdflib import URIRef, Graph
from docutils.core import publish_file
from docutils import writers, nodes
import pkg_resources

from ferenda import DocumentRepository
from ferenda import DocumentStore
from ferenda import util
from ferenda.decorators import managedparsing

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
            return super(StaticStore, self).list_basefiles_for(action,basedir)

class Writer(writers.Writer):
    supported = ('xhtml')
    config_section = 'xhtml writer'
    config_section_dependencies = ('writers',)

    output = None
    """Final translated form of `document`."""

    def translate(self):
        self.visitor = visitor = XHTMLTranslator(self.document)
        self.document.walkabout(visitor)
        self.output = ''.join(visitor.output)


class XHTMLTranslator(nodes.GenericNodeVisitor):
    def __init__(self, document):
        nodes.NodeVisitor.__init__(self, document)
        self.output = []

    def default_visit(self, node):
        from pudb import set_trace; set_trace()
        self.output.append(node.starttag('"'))

    def default_departure(self, node):
        return None
    
    def visit_title(self,node):
        super(XHTMLTranslator, self).visit_title()

    
    
    

class Static(DocumentRepository):
    alias = "static"
    downloaded_suffix = ".rst"
    documentstore_class = StaticStore
    # urls become on the form "http://lcoalhost:8000/static/about"
    
    def download(self):
        pass

    @managedparsing
    def parse(self, doc):
        with self.store.open_downloaded(doc.basefile) as source:
            with self.store.open_parsed(doc.basefile, "w") as destination:
                publish_file(source=source, destination=destination, writer=Writer())
        
    def tabs(self):
        if os.path.exists(self.store.generated_path("about")):
            return [("About", self.generated_url("about"))]
                
    def footer(self):
        # FIXME: ordering?
        res = []
        for basefile in self.store.list_basefiles_for("_postgenerate"):
            uri = self.generated_url(basefile)
            g = Graph()
            g.parse(self.store.distilled_path(basefile))
            title = g.value(URIRef(uri), self.ns['dct'].title).toPython()
            if not title:
                title = basefile
            res.append((title,uri))
        return res
