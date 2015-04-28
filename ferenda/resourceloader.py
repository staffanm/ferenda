class ResourceLoader(object):
    def __init__(self, *loadpath, **kwargs):
        self.loadpath = loadpath
        self.use_pkg_resources = kwargs.get("use_pkg_resources", True)
        self.modulename = "ferenda"

    def exists(self, resourcename):
        try:
            self.extract(resourcename)
            return True
        except ResourceNotFound:
            return False
            
    def load(self, resourcename, binary=False):
        mode = "rb" if binary else "r"
        with open(self.extract(resourcename), mode=mode) as fp:
            return fp.read()
    
    def open(self, resourcename, binary=False):
        # should preferably work both as classic open() and as a context manager
        pass
        
    def extract(self, resourcename):
        for path in self.loadpath:
            candidate = self.loadpath + os.sep + resourcename
            if os.path.exists(candidate):
                return candidate
        if self.use_pkg_resources and pkg_resources.resource_exists(self.modulename, resourcename):
            return pkg_resources.resource_extract(self.modulename, resourcename)
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
    
    
