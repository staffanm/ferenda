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
        """Given an object instance, returns a list of path locations corresponding 
        to the physical location of the implementation of that instance, with
        a specified suffix. 
        
        ie. if provided an ``Foo`` instance, whose class is defined in project/subclass/foo.py, 
        and ``Foo`` derives from ``Bar``, whose class is defined in project/bar.py, the returned
        make_loadpath will return ``['project/subclass/res', 'project/res']`` 
        """
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
        """
        :param loadpath: A list of directories to search for by the instance methods (in priority order). 
        :param kwargs: Any other named parameters to initialize the object with. The 
                       only named parameter defined is ``use_pkg_resources`` (default: 
                       True) for specifying whether to use the 
                       `<https://pythonhosted.org/setuptools/pkg_resources.html#resourcemanager-api> ResourceManager API`_
                       in addition to regular file operations. If set, the ResourceManager
                       API is queried only after all directories in loadpath are searched.
                       
        """
        self.loadpath = loadpath
        self.use_pkg_resources = kwargs.get("use_pkg_resources", True)
        self.modulename = "ferenda"
        self.resourceprefix = "res"
        self.log = logging.getLogger(__name__)

    def exists(self, resourcename):
        """Returns True iff the named resource can be found anywhere in any
        place where this loader searches, False otherwise"""
        try:
            self.filename(resourcename)
            return True
        except ResourceNotFound:
            return False

    def load(self, resourcename, binary=False):
        """Returns the contents of the resource, either as a string or a bytes
        object, depending on whether ``binary`` is False or True.
        
        Might raise ResourceNotFound.
        """
        mode = "rb" if binary else "r"
        with open(self.filename(resourcename), mode=mode) as fp:
            return fp.read()

    # this works like old-style open, eg.
    # fp = loader.open(...)
    # fp.read()
    # fp.close()
    def openfp(self, resourcename, binary=False):
        """Opens the specified resource and returns a open file object. 
        Caller must call .close() on this object when done.

        Might raise ResourceNotFound.
        """
        mode = "rb" if binary else "r"
        return open(self.filename(resourcename), mode=mode)

    # this is used with 'with', eg.
    # with loader.open(...) as fp:
    #     fp.read()
    @contextmanager
    def open(self, resourcename, binary=False):
        """Opens the specified resource as a context manager, ie call with ``with``:
        >>> loader = ResourceLoader()
        >>> with resource.open("robots.txt") as fp:
        ...     fp.read()

        Might raise ResourceNotFound.
        """
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
        """Return a filename pointing to the physical location of the resource.
        If the resource is only found using the ResourceManager API, extract '
        the resource to a temporary file and return its path.
        
        Might raise ResourceNotFound.
        """
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
                
    def extractdir(self, resourcedir, target):
        """Extract all file resources directly contained in the specified resource directory. 
        
        Searches all loadpaths and optionally the Resources API for any file contained within.
        This means the target dir may end up with eg. one file from a high-priority path and 
        other files from the system dirs/resources. This in turns makes it easy to just override
        a single file in a larger set of resource files.
        """
        extracted = set()
        for path in self.loadpath:
            for f in os.listdir(self.loadpath+os.sep+resourcedir):
                src = self.loadpath+os.sep+resourcedir + os.sep + f
                dest = target + os.sep + resourcedir + os.sep + f
                if dest not in extracted and os.isfile(src):
                    shutil.copy2(src, dest)
                    extracted.add(dest)
        if self.use_pkg_resources:
            for f in pkg_resources.resource_listdir(self.modulename, self.resourceprefix + os.sep + resourcedir):
                src = self.resourceprefix + os.sep + resourcedir + os.sep + f
                dest = target + os.sep + resourcedir + os.sep + f
                if dest not in extracted and os.isfile(src):
                    # FIXME: use proper API
                    pkg_resources.resource_extract(self.module, src, dest)
                    extracted.add(dest)
