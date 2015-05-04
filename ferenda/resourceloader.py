import inspect
import os
import logging
import pkg_resources
from contextlib import contextmanager

from ferenda.errors import ResourceNotFound


class ResourceLoader(object):

    # should perhaps have a corresponding make_modulepath for use with
    # pkg_resources.resource_stream et al
    @staticmethod
    def make_loadpath(instance, suffix="res"):
        res = []
        for cls in inspect.getmro(instance.__class__):
            if cls == object:
                continue
            path = os.path.relpath(inspect.getfile(cls))
            candidate = os.path.dirname(path) + os.sep + suffix
            if candidate not in res and os.path.exists(candidate):
               res.append(candidate)

        # uniquify loadpath
        return res

    def __init__(self, *loadpath, **kwargs):
        self.loadpath = loadpath
        self.use_pkg_resources = kwargs.get("use_pkg_resources", True)
        self.modulename = "ferenda"
        self.resourceprefix = "res"
        self.log = logging.getLogger(__name__)

    def exists(self, resourcename):
        try:
            self.filename(resourcename)
            return True
        except ResourceNotFound:
            return False

    def load(self, resourcename, binary=False):
        mode = "rb" if binary else "r"
        with open(self.filename(resourcename), mode=mode) as fp:
            return fp.read()

    # this works like old-style open, eg.
    # fp = loader.open(...)
    # fp.read()
    # fp.close()
    def openfp(self, resourcename, binary=False):
        mode = "rb" if binary else "r"
        return open(self.filename(resourcename), mode=mode)

    # this is used with 'with', eg.
    # with loader.open(...) as fp:
    #     fp.read()
    @contextmanager
    def open(self, resourcename, binary=False):
        mode = "rb" if binary else "r"
        fp = None
        try:
            fp = open(self.filename(resourcename), mode=mode)
            yield fp
        except ResourceNotFound:
            raise
        finally:
            if fp:
                fp.close()

    def filename(self, resourcename):
        if os.path.isabs(resourcename):  # don't examine the loadpath
            if os.path.exists(resourcename):
                return resourcename
            else:
                raise ResourceNotFound(resourcename)
        for path in self.loadpath:
            candidate = path + os.sep + resourcename
            if os.path.exists(candidate):
                return candidate
        if (self.use_pkg_resources and
            pkg_resources.resource_exists(self.modulename,
                                          self.resourceprefix + os.sep + resourcename)):
            return pkg_resources.resource_filename(self.modulename, self.resourceprefix + os.sep + resourcename)
        raise ResourceNotFound(resourcename) # should contain a list of places we searched?
                
    
if __name__ == "__main__":
    rl = ResourceLoader()  # default loadpath: cwd + pkg_resources('ferenda')
    rl = ResourceLoader(os.getcwd, os.path.dirname(__file__), use_pkg_resources=False)
    rq = rl.load("sparql/construct.rq") 
    # rq is now a string containing the contents of construct.rq
    fp = rl.open("sparql/construct.rq")
    # fp is now an open filehandle from which the contents of construct.rq can be read
    fname = rl.extract("sparql/construct.rq")
    # fname is now the path to a filename (might be in a temp dir, or could be the original place) containing construct.rq
    
    # possibly also rl.cleanup() that removes any temp files created by extract
    
    
