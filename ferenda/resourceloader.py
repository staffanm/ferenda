# -*- coding: utf-8 -*-
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
from builtins import *

from contextlib import contextmanager
import inspect
import logging
import os
import pkg_resources
import shutil

from ferenda import util
from ferenda.errors import ResourceNotFound


class ResourceLoader(object):

    # should perhaps have a corresponding make_modulepath for use with
    # pkg_resources.resource_stream et al
    @staticmethod
    def make_loadpath(instance, suffix="res"):
        """Given an object instance, returns a list of path locations
        corresponding to the physical location of the implementation
        of that instance, with a specified suffix.

        ie. if provided an ``Foo`` instance, whose class is defined in
        project/subclass/foo.py, and ``Foo`` derives from ``Bar``,
        whose class is defined in project/bar.py, the returned
        make_loadpath will return ``['project/subclass/res',
        'project/res']``

        """
        res = []
        for cls in inspect.getmro(instance.__class__):
            # Under py2, object is now
            # future.types.newobject.newobject, which means a "if cls
            # == object" won't work. So instead we make a stringly
            # comparison.
            if cls.__name__ == "object":
                continue
            path = os.path.relpath(inspect.getfile(cls))
            candidate = os.path.dirname(path) + os.sep + suffix
            # uniquify loadpath
            if candidate not in res and os.path.exists(candidate):
               res.append(candidate)
        return res

    def __init__(self, *loadpath, **kwargs):
        """
        Encapsulates resource access through a flexible load-path system.

        :param loadpath: A list of directories to search for by the
                         instance methods (in priority order) .
        :param kwargs: Any other named parameters to initialize the
                       object with. The only named parameter defined
                       is ``use_pkg_resources`` (default: True) for
                       specifying whether to use the
                       `<https://pythonhosted.org/setuptools/pkg_resources.html#resourcemanager-api>
                       ResourceManager API`_ in addition to regular
                       file operations. If set, the ResourceManager
                       API is queried only after all directories in
                       loadpath are searched.

        """
        self.loadpath = loadpath
        self.use_pkg_resources = kwargs.get("use_pkg_resources", True)
        self.modulename = "ferenda"
        self.resourceprefix = "res"
        self.log = logging.getLogger(__name__)


    def _check_module_path(self):
        # If ferenda is imported, and the working directory of the
        # process is then changed (with os.cwd()), this can cause
        # problems with the pkg_resources API, since that module might
        # use relative paths for the location of resources, and
        # changing the working directory causes these relative paths
        # to be invalid wrt the new working directory. This seem to be
        # a problem with py2 only, since on py3 the absolute path for
        # each loaded module is stored in sys.modules which
        # pkg_resources uses.
        # 
        # This method tries to detect the problem and correct it if
        # possible.
        if self.use_pkg_resources:
            module_path = pkg_resources.get_provider("ferenda").module_path
            if not os.path.exists(module_path) and not os.path.isabs(module_path):
                # There appears to be no simple way of determining the
                # "true" path of where ferenda is installed. But if
                # os.environ["FERENDA_HOME"] is defined we can rely on
                # that. Then we directly muck with sys.modules to
                # record the absolute path to the module. This might
                # not be legal... NB: This should only happen in
                # development mode, not with an installed ferenda
                # package, and only on py2.
                if "FERENDA_HOME" in os.environ:
                    truepath = (os.environ["FERENDA_HOME"] + os.sep +
                                module_path + os.sep + "__init__.py")
                    sys.modules["ferenda"].__file__ = truepath
                else:
                    raise ResourceNotFound("pkg_resources internal variable module_path is a relative path (%s). No such path exists relative to %s" % (module_path, os.getcwd()))

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
        
        Might raise :py:exc:`~ferenda.errors.ResourceNotFound`.
        """
        mode = "rb" if binary else "r"
        filename = self.filename(resourcename)
        self.log.debug("Loading %s" % filename)
        with open(filename, mode=mode) as fp:
            return fp.read()

    # this works like old-style open, eg.
    # fp = loader.open(...)
    # fp.read()
    # fp.close()
    def openfp(self, resourcename, binary=False):
        """Opens the specified resource and returns a open file object. 
        Caller must call .close() on this object when done.

        Might raise :py:exc:`~ferenda.errors.ResourceNotFound`.
        """
        mode = "rb" if binary else "r"
        filename = self.filename(resourcename)
        self.log.debug("Opening fp %s" % filename)
        return open(filename, mode=mode)

    # this is used with 'with', eg.
    # with loader.open(...) as fp:
    #     fp.read()
    @contextmanager
    def open(self, resourcename, binary=False):
        """Opens the specified resource as a context manager, ie call with
        ``with``:

            >>> loader = ResourceLoader()
            >>> with resource.open("robots.txt") as fp:
            ...     fp.read()

        Might raise :py:exc:`~ferenda.errors.ResourceNotFound`.

        """
        mode = "rb" if binary else "r"
        fp = None
        try:
            filename = self.filename(resourcename)
            self.log.debug("Opening %s" % filename)
            fp = open(filename, mode=mode)
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
        
        Might raise :py:exc:`~ferenda.errors.ResourceNotFound`.
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
        if self.use_pkg_resources:
            self._check_module_path()
            if pkg_resources.resource_exists(self.modulename,
                                             self.resourceprefix + os.sep + resourcename):
                abspath = pkg_resources.resource_filename(self.modulename, self.resourceprefix + os.sep + resourcename)
                return os.path.relpath(abspath)
            else:
                raise ResourceNotFound(resourcename)
        else:
                raise ResourceNotFound(resourcename) # should contain a list of places we searched?
                
    def extractdir(self, resourcedir, target, suffixes=None):
        """Extract all file resources contained in the specified
        resource directory to the target directory.
        
        Searches all loadpaths and optionally the Resources API for
        any file contained within. This means the target dir may end
        up with eg. one file from a high-priority path and other files
        from the system dirs/resources. This in turns makes it easy to
        just override a single file in a larger set of resource files.

        Even if the resourcedir might contain resources in
        subdirectories (eg "source/sub/dir/resource.xml"), the
        extraction will be to the top-level target directory (eg
        "target/resource.xml").

        """
        if not suffixes:
            suffixes = []
        extracted = set()
        for path in self.loadpath:
            if resourcedir and resourcedir != ".":
                path = path+os.sep+resourcedir
            if not os.path.exists(path):
                continue
            # for f in os.listdir(path):
            for f in util.list_dirs(path, suffixes):
                f = f[len(path)+1:]
                basef = os.path.basename(f)
                src = os.sep.join([path, f])
                dest = os.sep.join([target, basef])
                if dest not in extracted and os.path.isfile(src):
                    util.ensure_dir(dest)
                    shutil.copy2(src, dest)
                    extracted.add(dest)

        if self.use_pkg_resources:
            self._check_module_path()
            path = self.resourceprefix
            if resourcedir:
                path = path + os.sep + resourcedir
            for f in pkg_resources.resource_listdir(self.modulename, path):
                src = path + os.sep + f
                dest = target
                dest += os.sep + f
                if (dest not in extracted and not
                    pkg_resources.resource_isdir(self.modulename,
                                                 self.resourceprefix + os.sep + f)):
                    util.ensure_dir(dest)
                    with open(dest, "wb") as fp:
                        readfp = pkg_resources.resource_stream(self.modulename,
                                                               src)
                        fp.write(readfp.read())
                        readfp.close()
                    extracted.add(dest)
